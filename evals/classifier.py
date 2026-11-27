"""Compare offline classifier metrics with SlopGuard's recorded rule decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from packaging.utils import InvalidName, canonicalize_name

from .constants import (
    CLASSIFIER_PROBABILITY_DECIMALS,
    DEFAULT_CLASSIFIER_BLOCK_ATTACK_TIME_PATH,
    DEFAULT_CLASSIFIER_BLOCK_PREDICTIONS_PATH,
    DEFAULT_CLASSIFIER_CONCLUSION_PATH,
    DEFAULT_CLASSIFIER_DEFAULTS_ATTACK_TIME_PATH,
    DEFAULT_CLASSIFIER_DEFAULTS_PREDICTIONS_PATH,
    DEFAULT_CLASSIFIER_FOLDS,
    DEFAULT_CLASSIFIER_MAX_ITER,
    DEFAULT_CLASSIFIER_MODEL,
    DEFAULT_CLASSIFIER_N_ESTIMATORS,
    DEFAULT_CLASSIFIER_RESULTS_PATH,
    DEFAULT_CLASSIFIER_SEED,
    DEFAULT_CLASSIFIER_THRESHOLD,
    GIT_COMMAND_TIMEOUT_SECONDS,
    HASH_CHUNK_BYTES,
    MAX_CLASSIFIER_CONCLUSION_BYTES,
)
from .io import read_jsonl_objects
from .metrics import FLAGGED_BLOCK_ONLY, FLAGGED_WARN_OR_BLOCK, binary_metrics, format_metric

ModelName = Literal["logistic-regression", "random-forest"]


def encode_features(rows: list[dict[str, object]]) -> tuple[list[list[float]], list[str]]:
    """Encode only the documented ``features`` object into a stable numeric matrix."""
    if not rows:
        raise ValueError("cannot encode an empty prediction dataset")
    feature_rows: list[dict[str, object]] = []
    statuses: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        features = row.get("features")
        if not isinstance(features, dict):
            raise ValueError(f"prediction row {row_number} has no features object")
        feature_rows.append(features)
        status = features.get("registry_status")
        if status is not None and (not isinstance(status, str) or not status):
            raise ValueError(f"prediction row {row_number} has invalid registry_status")
        statuses.add(status if isinstance(status, str) else "__missing__")

    feature_names = [f"registry_status={status}" for status in sorted(statuses)]
    feature_names.extend(
        (
            "log1p_age_days",
            "age_days_missing",
            "log1p_release_count",
            "release_count_missing",
            "has_source",
            "best_similarity",
            "best_edit_distance",
            "log_best_rank",
            "no_lookalike",
        )
    )
    matrix: list[list[float]] = []
    for row_number, features in enumerate(feature_rows, start=1):
        status = features.get("registry_status")
        status_value = status if isinstance(status, str) else "__missing__"
        values = [
            float(status_value == category.split("=", maxsplit=1)[1])
            for category in feature_names
            if category.startswith("registry_status=")
        ]
        age, age_missing = _log1p_feature(features.get("age_days"), "age_days", row_number)
        releases, releases_missing = _log1p_feature(
            features.get("release_count"), "release_count", row_number
        )
        has_source = features.get("has_source")
        if not isinstance(has_source, bool):
            raise ValueError(f"prediction row {row_number} has invalid has_source")
        similarity = _number_feature(
            features.get("best_similarity"), "best_similarity", row_number, nullable=True
        )
        distance_raw = features.get("best_edit_distance")
        rank_raw = features.get("best_rank")
        if (distance_raw is None) != (rank_raw is None):
            raise ValueError(f"prediction row {row_number} has incomplete look-alike features")
        no_lookalike = distance_raw is None
        distance = (
            0.0 if no_lookalike else _number_feature(distance_raw, "best_edit_distance", row_number)
        )
        rank = (
            0.0
            if no_lookalike
            else math.log(_number_feature(rank_raw, "best_rank", row_number, minimum=1))
        )
        values.extend(
            (
                age,
                age_missing,
                releases,
                releases_missing,
                float(has_source),
                similarity,
                distance,
                rank,
                float(no_lookalike),
            )
        )
        matrix.append(values)
    return matrix, feature_names


def _number_feature(
    value: object,
    field: str,
    row_number: int,
    *,
    nullable: bool = False,
    minimum: float = 0.0,
) -> float:
    if value is None and nullable:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"prediction row {row_number} has invalid {field}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < minimum:
        raise ValueError(f"prediction row {row_number} has invalid {field}")
    return numeric


def _log1p_feature(value: object, field: str, row_number: int) -> tuple[float, float]:
    if value is None:
        return 0.0, 1.0
    return math.log1p(_number_feature(value, field, row_number)), 0.0


def _load_prediction_rows(path: Path) -> list[dict[str, object]]:
    rows = list(read_jsonl_objects(path))
    by_name: dict[str, dict[str, object]] = {}
    for row_number, row in enumerate(rows, start=1):
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}:{row_number}: name must be a non-empty string")
        try:
            canonical_name = canonicalize_name(name, validate=True)
        except InvalidName as error:
            raise ValueError(f"{path}:{row_number}: invalid package name") from error
        if canonical_name in by_name:
            raise ValueError(f"{path}:{row_number}: duplicate package name {name!r}")
        expected = row.get("expected")
        if not isinstance(expected, str) or expected not in {"flag", "pass"}:
            raise ValueError(f"{path}:{row_number}: expected must be flag or pass")
        if not isinstance(row.get("decision"), str):
            raise ValueError(f"{path}:{row_number}: decision must be a string")
        if not isinstance(row.get("features"), dict):
            raise ValueError(f"{path}:{row_number}: features must be an object")
        risk_score = row.get("risk_score")
        try:
            valid_risk_score = (
                not isinstance(risk_score, bool)
                and isinstance(risk_score, (int, float))
                and math.isfinite(float(risk_score))
            )
        except (OverflowError, ValueError):
            valid_risk_score = False
        if not valid_risk_score:
            raise ValueError(f"{path}:{row_number}: risk_score must be a finite number")
        by_name[canonical_name] = row
    return [by_name[key] for key in sorted(by_name)]


def join_rule_rows(
    defaults_rows: list[dict[str, object]], block_rows: list[dict[str, object]], *, scenario: str
) -> list[tuple[dict[str, object], dict[str, object]]]:
    """Join default and block predictions by canonical name and require matching evidence."""
    defaults = {_row_name(row): row for row in defaults_rows}
    block = {_row_name(row): row for row in block_rows}
    if defaults.keys() != block.keys():
        missing_block = sorted(defaults.keys() - block.keys())
        missing_defaults = sorted(block.keys() - defaults.keys())
        raise ValueError(
            f"{scenario}: defaults/block names differ; missing from block={missing_block[:5]}, "
            f"missing from defaults={missing_defaults[:5]}"
        )
    joined: list[tuple[dict[str, object], dict[str, object]]] = []
    for name in sorted(defaults):
        default_row, block_row = defaults[name], block[name]
        if default_row["features"] != block_row["features"]:
            raise ValueError(f"{scenario}: feature rows differ for {default_row['name']!r}")
        if default_row["expected"] != block_row["expected"]:
            raise ValueError(f"{scenario}: expected labels differ for {default_row['name']!r}")
        joined.append((default_row, block_row))
    return joined


def _row_name(row: dict[str, object]) -> str:
    return str(row["name"])


def run_classifier(
    *,
    defaults_predictions_path: Path = DEFAULT_CLASSIFIER_DEFAULTS_PREDICTIONS_PATH,
    defaults_attack_time_path: Path = DEFAULT_CLASSIFIER_DEFAULTS_ATTACK_TIME_PATH,
    block_predictions_path: Path = DEFAULT_CLASSIFIER_BLOCK_PREDICTIONS_PATH,
    block_attack_time_path: Path = DEFAULT_CLASSIFIER_BLOCK_ATTACK_TIME_PATH,
    out_dir: Path = DEFAULT_CLASSIFIER_RESULTS_PATH,
    model: ModelName = DEFAULT_CLASSIFIER_MODEL,
    seed: int = DEFAULT_CLASSIFIER_SEED,
    folds: int = DEFAULT_CLASSIFIER_FOLDS,
    threshold: float = DEFAULT_CLASSIFIER_THRESHOLD,
    conclusion_path: Path | None = DEFAULT_CLASSIFIER_CONCLUSION_PATH,
) -> dict[str, object]:
    """Compare rule and classifier outcomes across recorded, attack-time, and shift scenarios."""
    started = time.monotonic()
    if folds < 2:
        raise ValueError("folds must be at least 2")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    if model not in {"logistic-regression", "random-forest"}:
        raise ValueError("model must be logistic-regression or random-forest")
    try:
        import sklearn
    except ImportError as error:
        raise RuntimeError(
            "scikit-learn is required; run `uv sync --locked --group eval`"
        ) from error

    input_paths = {
        "defaults_predictions": defaults_predictions_path,
        "defaults_attack_time": defaults_attack_time_path,
        "block_predictions": block_predictions_path,
        "block_attack_time": block_attack_time_path,
    }
    loaded = {key: _load_prediction_rows(path) for key, path in input_paths.items()}
    recorded = join_rule_rows(
        loaded["defaults_predictions"], loaded["block_predictions"], scenario="recorded state"
    )
    attack_time = join_rule_rows(
        loaded["defaults_attack_time"],
        loaded["block_attack_time"],
        scenario="attack time",
    )
    recorded_by_name = {_row_name(pair[0]): pair for pair in recorded}
    attack_by_name = {_row_name(pair[0]): pair for pair in attack_time}
    if recorded_by_name.keys() != attack_by_name.keys():
        raise ValueError("recorded-state and attack-time prediction names differ")
    for name in recorded_by_name:
        if recorded_by_name[name][0]["expected"] != attack_by_name[name][0]["expected"]:
            raise ValueError(f"expected labels differ between scenarios for {name!r}")
    scenarios: dict[str, object] = {}
    oof_rows: list[dict[str, object]] = []
    scenarios["recorded_state"] = _evaluate_cv_scenario(
        "recorded_state", recorded, model, seed, folds, threshold, oof_rows
    )
    scenarios["attack_time"] = _evaluate_cv_scenario(
        "attack_time", attack_time, model, seed, folds, threshold, oof_rows
    )
    scenarios["recorded_to_attack_time_shift"] = _evaluate_shift_scenario(
        recorded, attack_time, model, seed, threshold, oof_rows
    )

    row_counts = {
        scenario: dict(sorted(Counter(str(pair[0]["expected"]) for pair in pairs).items()))
        for scenario, pairs in (("recorded_state", recorded), ("attack_time", attack_time))
    }
    result: dict[str, object] = {
        "parameters": {
            "model": model,
            "seed": seed,
            "folds": folds,
            "threshold": threshold,
            "sklearn_version": sklearn.__version__,
        },
        "input_sha256": {key: _sha256_file(path) for key, path in sorted(input_paths.items())},
        "row_counts": row_counts,
        "scenarios": scenarios,
    }
    rounded_result = _round_floats(result)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "comparison.json").write_text(
        json.dumps(rounded_result, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    oof_rows.sort(key=lambda row: (str(row["scenario"]), canonicalize_name(str(row["name"]))))
    with (out_dir / "oof_predictions.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for row in oof_rows:
            stream.write(json.dumps(_round_floats(row), sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    (out_dir / "report.md").write_text(
        _render_report(rounded_result, _read_conclusion(conclusion_path)),
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "run_info.json").write_text(
        json.dumps(
            {
                "git_commit": _current_git_commit(),
                "runtime_seconds": round(time.monotonic() - started, 3),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return rounded_result


def _targets(rows: list[dict[str, object]]) -> list[int]:
    return [1 if row["expected"] == "flag" else 0 for row in rows]


def dataset_diagnostics(
    rows: list[dict[str, object]], feature_names: list[str], matrix: list[list[float]]
) -> dict[str, object]:
    """Measure single-feature separation, constant encoded columns, and class counts."""
    if len(rows) != len(matrix):
        raise ValueError("diagnostic rows and matrix must have the same number of rows")
    if any(len(values) != len(feature_names) for values in matrix):
        raise ValueError("diagnostic matrix width must match feature_names")
    class_counts = {"flag": 0, "pass": 0}
    values_by_class = {
        "flag": [set() for _ in feature_names],
        "pass": [set() for _ in feature_names],
    }
    for row, values in zip(rows, matrix, strict=True):
        expected = row.get("expected")
        if expected not in class_counts:
            raise ValueError("diagnostics require expected='flag' or expected='pass'")
        class_counts[str(expected)] += 1
        for index, value in enumerate(values):
            values_by_class[str(expected)][index].add(value)
    # A feature separates the classes on its own only when a single threshold splits them:
    # every value of one class lies strictly below every value of the other class.
    perfect_separators = [
        feature
        for index, feature in enumerate(feature_names)
        if class_counts["flag"]
        and class_counts["pass"]
        and (
            max(values_by_class["flag"][index]) < min(values_by_class["pass"][index])
            or max(values_by_class["pass"][index]) < min(values_by_class["flag"][index])
        )
    ]
    constant_features = [
        feature_names[index]
        for index in range(len(feature_names))
        if matrix and all(values[index] == matrix[0][index] for values in matrix[1:])
    ]
    return {
        "perfect_separating_features": perfect_separators,
        "constant_features": constant_features,
        "class_counts": class_counts,
    }


def _fit_model(model_name: ModelName, seed: int):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if model_name == "logistic-regression":
        estimator = LogisticRegression(
            class_weight="balanced", random_state=seed, max_iter=DEFAULT_CLASSIFIER_MAX_ITER
        )
    else:
        estimator = RandomForestClassifier(
            random_state=seed, n_estimators=DEFAULT_CLASSIFIER_N_ESTIMATORS
        )
    return Pipeline([("scale", StandardScaler()), ("classifier", estimator)])


def _positive_probabilities(estimator, matrix: list[list[float]]) -> list[float]:
    classes = list(estimator.classes_)
    try:
        positive_index = classes.index(1)
    except ValueError as error:
        raise ValueError("classifier training data does not contain the flag class") from error
    return [
        round(float(probability[positive_index]), CLASSIFIER_PROBABILITY_DECIMALS)
        for probability in estimator.predict_proba(matrix)
    ]


def _scenario_metrics(
    rows: list[dict[str, object]], probabilities: list[float], threshold: float
) -> dict[str, object]:
    from sklearn.metrics import roc_auc_score

    decisions = ["flag" if value >= threshold else "pass" for value in probabilities]
    metric_rows = [
        {"expected": str(row["expected"]), "decision": decision}
        for row, decision in zip(rows, decisions, strict=True)
    ]
    result = binary_metrics(metric_rows, flagged_decisions=frozenset({"flag"}))
    targets = _targets(rows)
    result["roc_auc"] = (
        float(roc_auc_score(targets, probabilities)) if len(set(targets)) == 2 else None
    )
    return result


def _rule_metrics(
    pairs: list[tuple[dict[str, object], dict[str, object]]], *, block_only: bool
) -> dict[str, object]:
    from sklearn.metrics import roc_auc_score

    rule_key = 1 if block_only else 0
    rows = [
        {"expected": str(pair[0]["expected"]), "decision": str(pair[rule_key]["decision"])}
        for pair in pairs
    ]
    threshold = FLAGGED_BLOCK_ONLY if block_only else FLAGGED_WARN_OR_BLOCK
    result = binary_metrics(rows, flagged_decisions=threshold)
    targets = _targets(rows)
    scores = [float(pair[rule_key]["risk_score"]) for pair in pairs]
    result["roc_auc"] = float(roc_auc_score(targets, scores)) if len(set(targets)) == 2 else None
    return result


def _rows_from_pairs(
    pairs: list[tuple[dict[str, object], dict[str, object]]],
) -> list[dict[str, object]]:
    return [pair[0] for pair in pairs]


def _evaluate_cv_scenario(
    scenario: str,
    pairs: list[tuple[dict[str, object], dict[str, object]]],
    model_name: ModelName,
    seed: int,
    folds: int,
    threshold: float,
    oof_rows: list[dict[str, object]],
) -> dict[str, object]:
    from sklearn.model_selection import StratifiedKFold

    rows = _rows_from_pairs(pairs)
    targets = _targets(rows)
    counts = Counter(targets)
    if not counts or min(counts.values()) < folds:
        raise ValueError(f"{scenario}: each class needs at least {folds} rows for stratified CV")
    matrix, feature_names = encode_features(rows)
    probabilities = [0.0] * len(rows)
    fold_ids = [-1] * len(rows)
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for fold_number, (train_indices, test_indices) in enumerate(splitter.split(matrix, targets), 1):
        if set(train_indices).intersection(test_indices):
            raise RuntimeError("cross-validation train and test folds overlap")
        estimator = _fit_model(model_name, seed)
        train_matrix = [matrix[index] for index in train_indices]
        train_targets = [targets[index] for index in train_indices]
        test_matrix = [matrix[index] for index in test_indices]
        fold_probabilities = _positive_probabilities(
            estimator.fit(train_matrix, train_targets), test_matrix
        )
        for index, probability in zip(test_indices, fold_probabilities, strict=True):
            probabilities[index] = probability
            fold_ids[index] = fold_number
    if any(fold_id < 1 for fold_id in fold_ids):
        raise RuntimeError("cross-validation did not assign every row to a test fold")

    metrics = _scenario_metrics(rows, probabilities, threshold)
    diagnostics = dataset_diagnostics(rows, feature_names, matrix)
    for row, fold_id, probability in zip(rows, fold_ids, probabilities, strict=True):
        oof_rows.append(_oof_row(row, scenario, fold_id, probability, threshold))
    full_model = _fit_model(model_name, seed).fit(matrix, targets)
    return {
        "classifier": metrics,
        "diagnostics": diagnostics,
        "defaults_rule_warn_or_block": _rule_metrics(pairs, block_only=False),
        "block_rule_block_only": _rule_metrics(pairs, block_only=True),
        "validation": "out-of-fold stratified cross-validation",
        "coefficients_full_data_fit": _coefficients(full_model, model_name, feature_names),
    }


def _evaluate_shift_scenario(
    recorded: list[tuple[dict[str, object], dict[str, object]]],
    attack: list[tuple[dict[str, object], dict[str, object]]],
    model_name: ModelName,
    seed: int,
    threshold: float,
    oof_rows: list[dict[str, object]],
) -> dict[str, object]:
    training_rows = _rows_from_pairs(recorded)
    evaluation_rows = _rows_from_pairs(attack)
    combined = training_rows + evaluation_rows
    combined_matrix, feature_names = encode_features(combined)
    training_matrix = combined_matrix[: len(training_rows)]
    evaluation_matrix = combined_matrix[len(training_rows) :]
    estimator = _fit_model(model_name, seed).fit(training_matrix, _targets(training_rows))
    probabilities = _positive_probabilities(estimator, evaluation_matrix)
    metrics = _scenario_metrics(evaluation_rows, probabilities, threshold)
    diagnostics = dataset_diagnostics(evaluation_rows, feature_names, evaluation_matrix)
    for row, probability in zip(evaluation_rows, probabilities, strict=True):
        oof_rows.append(
            _oof_row(row, "recorded_to_attack_time_shift", None, probability, threshold)
        )
    return {
        "classifier": metrics,
        "diagnostics": diagnostics,
        "defaults_rule_warn_or_block": _rule_metrics(attack, block_only=False),
        "block_rule_block_only": _rule_metrics(attack, block_only=True),
        "validation": "fit on all recorded-state rows; evaluated on all attack-time rows",
        "coefficients_full_training_fit": _coefficients(estimator, model_name, feature_names),
    }


def _oof_row(
    row: dict[str, object], scenario: str, fold: int | None, probability: float, threshold: float
) -> dict[str, object]:
    return {
        "name": row["name"],
        "scenario": scenario,
        "fold": fold,
        "probability": probability,
        "decision": "flag" if probability >= threshold else "pass",
        "expected": row["expected"],
    }


def _coefficients(
    estimator, model_name: ModelName, feature_names: list[str]
) -> dict[str, float] | None:
    if model_name != "logistic-regression":
        return None
    classifier = estimator.named_steps["classifier"]
    return {
        feature: float(coefficient)
        for feature, coefficient in zip(feature_names, classifier.coef_[0], strict=True)
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _current_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return (
        value
        if len(value) in {40, 64} and all(char in "0123456789abcdefABCDEF" for char in value)
        else None
    )


def _round_floats(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    return value


def _read_conclusion(path: Path | None) -> str:
    """Return the human-written conclusion kept in a source file, or a visible placeholder.

    The report is generated and checked byte for byte in CI, so the conclusion lives in its own
    file instead of being edited into ``report.md``.
    """
    if path is None:
        return "No conclusion has been written yet."
    if not path.is_file():
        return f"No conclusion has been written yet (expected at `{path.as_posix()}`)."
    if path.stat().st_size > MAX_CLASSIFIER_CONCLUSION_BYTES:
        raise ValueError(f"{path}: conclusion exceeds {MAX_CLASSIFIER_CONCLUSION_BYTES} bytes")
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError(f"{path}: conclusion file is empty")
    return text


def _render_report(result: dict[str, object], conclusion: str) -> str:
    parameters = result["parameters"]
    assert isinstance(parameters, dict)
    lines = [
        "# Offline classifier comparison",
        "",
        f"Model: {parameters['model']}; sklearn: {parameters['sklearn_version']}; "
        f"seed: {parameters['seed']}; folds: {parameters['folds']}; "
        f"probability threshold: {parameters['threshold']}.",
        "",
        "## Input provenance",
        "",
    ]
    input_hashes = result["input_sha256"]
    assert isinstance(input_hashes, dict)
    lines.extend(f"- {key}: SHA256 `{value}`" for key, value in sorted(input_hashes.items()))
    row_counts = result["row_counts"]
    assert isinstance(row_counts, dict)
    lines.extend(("", "Row counts by expected class:"))
    for scenario, counts in sorted(row_counts.items()):
        lines.append(f"- {scenario}: {counts}")
    scenarios = result["scenarios"]
    assert isinstance(scenarios, dict)
    for scenario, description in (
        (
            "recorded_state",
            "Scenario A: recorded state; classifier metrics use out-of-fold predictions.",
        ),
        ("attack_time", "Scenario B: attack time; classifier metrics use out-of-fold predictions."),
        (
            "recorded_to_attack_time_shift",
            "Scenario C: fit on recorded state, evaluate on attack time.",
        ),
    ):
        metrics = scenarios[scenario]
        assert isinstance(metrics, dict)
        lines.extend(
            (
                "",
                f"## {scenario.replace('_', ' ').title()}",
                "",
                description,
                "",
                "| Scorer | TP | FP | TN | FN | Precision | Recall | FPR | F1 | ROC-AUC |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            )
        )
        for scorer, key in (
            ("Classifier", "classifier"),
            ("Defaults rule (warn/block)", "defaults_rule_warn_or_block"),
            ("Block rule", "block_rule_block_only"),
        ):
            score = metrics[key]
            assert isinstance(score, dict)
            confusion = score["confusion"]
            assert isinstance(confusion, dict)
            auc = score.get("roc_auc")
            lines.append(
                f"| {scorer} | {confusion['tp']} | {confusion['fp']} | "
                f"{confusion['tn']} | {confusion['fn']} | "
                f"{format_metric(score['precision'])} | {format_metric(score['recall'])} | "
                f"{format_metric(score['false_positive_rate'])} | {format_metric(score['f1'])} | "
                f"{format_metric(auc)} |"
            )
        coefficient_key = (
            "coefficients_full_training_fit"
            if scenario == "recorded_to_attack_time_shift"
            else "coefficients_full_data_fit"
        )
        coefficients = metrics[coefficient_key]
        if coefficients is not None:
            lines.extend(
                ("", "Logistic-regression coefficients (full-data fit; not CV coefficients):", "")
            )
            lines.extend(
                f"- `{feature}`: {coefficient}"
                for feature, coefficient in sorted(coefficients.items())
            )
    lines.extend(("", "## Interpretation", ""))
    for scenario, metrics in sorted(scenarios.items()):
        assert isinstance(metrics, dict)
        diagnostics = metrics["diagnostics"]
        assert isinstance(diagnostics, dict)
        lines.append(f"- {scenario}: class counts {diagnostics['class_counts']}.")
        separators = diagnostics["perfect_separating_features"]
        if separators:
            lines.append(f"  Perfectly separating single features: {', '.join(separators)}.")
        else:
            lines.append("  No single feature perfectly separates the classes.")
        constants = diagnostics["constant_features"]
        if constants:
            lines.append(f"  Constant features: {', '.join(constants)}.")
        else:
            lines.append("  No features are constant across all rows.")
    lines.extend(
        (
            "",
            "Precision = TP/(TP+FP); recall = TP/(TP+FN); FPR = FP/(FP+TN); "
            "F1 = 2×precision×recall/(precision+recall). Undefined denominators are null in JSON and n/a here.",
            "",
            "## Conclusion",
            "",
            conclusion,
            "",
        )
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--defaults-predictions", type=Path, default=DEFAULT_CLASSIFIER_DEFAULTS_PREDICTIONS_PATH
    )
    parser.add_argument(
        "--defaults-attack-time-predictions",
        type=Path,
        default=DEFAULT_CLASSIFIER_DEFAULTS_ATTACK_TIME_PATH,
        dest="defaults_attack_time",
    )
    parser.add_argument(
        "--block-predictions", type=Path, default=DEFAULT_CLASSIFIER_BLOCK_PREDICTIONS_PATH
    )
    parser.add_argument(
        "--block-attack-time-predictions",
        type=Path,
        default=DEFAULT_CLASSIFIER_BLOCK_ATTACK_TIME_PATH,
        dest="block_attack_time",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_CLASSIFIER_RESULTS_PATH)
    parser.add_argument(
        "--model",
        choices=("logistic-regression", "random-forest"),
        default=DEFAULT_CLASSIFIER_MODEL,
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_CLASSIFIER_SEED)
    parser.add_argument("--folds", type=int, default=DEFAULT_CLASSIFIER_FOLDS)
    parser.add_argument("--threshold", type=float, default=DEFAULT_CLASSIFIER_THRESHOLD)
    parser.add_argument(
        "--conclusion", type=Path, default=DEFAULT_CLASSIFIER_CONCLUSION_PATH, dest="conclusion"
    )
    args = parser.parse_args(argv)
    try:
        run_classifier(
            defaults_predictions_path=args.defaults_predictions,
            defaults_attack_time_path=args.defaults_attack_time,
            block_predictions_path=args.block_predictions,
            block_attack_time_path=args.block_attack_time,
            out_dir=args.out_dir,
            model=args.model,
            seed=args.seed,
            folds=args.folds,
            threshold=args.threshold,
            conclusion_path=args.conclusion,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
