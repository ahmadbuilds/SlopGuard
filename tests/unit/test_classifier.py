"""Feature encoding and offline classifier tests with a tiny synthetic dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.classifier import (
    _load_prediction_rows,
    _render_report,
    dataset_diagnostics,
    encode_features,
    join_rule_rows,
    run_classifier,
)
from evals.metrics import FLAGGED_BLOCK_ONLY, FLAGGED_WARN_OR_BLOCK, binary_metrics

pytest.importorskip("sklearn")


def _prediction(
    name: str,
    expected: str,
    *,
    status: str,
    age: int | None,
    releases: int | None,
    has_source: bool,
    distance: int | None,
    rank: int | None,
    similarity: float | None,
    decision: str,
    risk_score: float = 99,
) -> dict[str, object]:
    return {
        "name": name,
        "expected": expected,
        "decision": decision,
        "features": {
            "registry_status": status,
            "age_days": age,
            "release_count": releases,
            "has_source": has_source,
            "best_similarity": similarity,
            "best_edit_distance": distance,
            "best_rank": rank,
        },
        "risk_score": risk_score,
        "severity": "critical",
        "contributions": [{"points": 99}],
        "category": "forbidden-label",
        "label": "forbidden-label",
    }


def _datasets(tmp_path: Path) -> dict[str, Path]:
    defaults = []
    blocks = []
    attack_defaults = []
    attack_blocks = []
    for index in range(20):
        is_flag = index < 10
        name = f"fixture-package-{index:02}"
        expected = "flag" if is_flag else "pass"
        recorded_status = "not_found" if is_flag else "exists"
        distance = 1 if index % 3 == 0 else None
        rank = 35 + index if distance is not None else None
        similarity = 0.8 if distance is not None else None
        defaults.append(
            _prediction(
                name,
                expected,
                status=recorded_status,
                age=None if is_flag else 150,
                releases=None if is_flag else 5,
                has_source=not is_flag,
                distance=distance,
                rank=rank,
                similarity=similarity,
                decision="warn" if is_flag else "pass",
                risk_score=0 if is_flag else 100,
            )
        )
        blocks.append({**defaults[-1], "decision": "block" if is_flag and index < 5 else "pass"})
        attack_base = _prediction(
            name,
            expected,
            status="exists",
            age=1 + index,
            releases=1 + index % 4,
            has_source=False,
            distance=distance,
            rank=rank,
            similarity=similarity,
            decision="warn" if is_flag else "pass",
            risk_score=0 if is_flag else 100,
        )
        attack_defaults.append(attack_base)
        attack_blocks.append(
            {**attack_base, "decision": "block" if is_flag and index < 5 else "pass"}
        )
    paths = {
        "defaults": tmp_path / "defaults.jsonl",
        "defaults_attack": tmp_path / "defaults-attack.jsonl",
        "block": tmp_path / "block.jsonl",
        "block_attack": tmp_path / "block-attack.jsonl",
    }
    for key, rows in (
        ("defaults", defaults),
        ("defaults_attack", attack_defaults),
        ("block", blocks),
        ("block_attack", attack_blocks),
    ):
        paths[key].write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
        )
    return paths


def _run(paths: dict[str, Path], out_dir: Path, conclusion_path: Path | None = None) -> None:
    run_classifier(
        defaults_predictions_path=paths["defaults"],
        defaults_attack_time_path=paths["defaults_attack"],
        block_predictions_path=paths["block"],
        block_attack_time_path=paths["block_attack"],
        out_dir=out_dir,
        seed=73,
        folds=5,
        threshold=0.5,
        conclusion_path=conclusion_path,
    )


def test_report_includes_conclusion_from_source_file(tmp_path: Path) -> None:
    paths = _datasets(tmp_path)
    conclusion = tmp_path / "conclusion.md"
    conclusion.write_text("The Action keeps the rule-based score.\r\n", encoding="utf-8")

    _run(paths, tmp_path / "with", conclusion)
    _run(paths, tmp_path / "without", tmp_path / "missing.md")

    report = (tmp_path / "with" / "report.md").read_text(encoding="utf-8")
    assert "## Conclusion\n\nThe Action keeps the rule-based score.\n" in report
    assert "No conclusion has been written yet" in (tmp_path / "without" / "report.md").read_text(
        encoding="utf-8"
    )
    conclusion.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        _run(paths, tmp_path / "empty", conclusion)


def test_encode_features_uses_only_documented_features_and_indicators() -> None:
    rows = [
        _prediction(
            "missing-values",
            "flag",
            status="not_found",
            age=None,
            releases=None,
            has_source=False,
            distance=None,
            rank=None,
            similarity=None,
            decision="warn",
        ),
        _prediction(
            "known-values",
            "pass",
            status="exists",
            age=3,
            releases=4,
            has_source=True,
            distance=2,
            rank=10,
            similarity=0.75,
            decision="pass",
        ),
    ]
    matrix, names = encode_features(rows)

    assert names[:2] == ["registry_status=exists", "registry_status=not_found"]
    assert "age_days_missing" in names and "release_count_missing" in names
    assert "no_lookalike" in names
    assert not any(
        forbidden in name
        for name in names
        for forbidden in ("decision", "risk_score", "contribution", "severity", "category", "label")
    )
    missing = matrix[0]
    assert missing[names.index("age_days_missing")] == 1
    assert missing[names.index("release_count_missing")] == 1
    assert missing[names.index("no_lookalike")] == 1
    assert missing[names.index("log_best_rank")] == 0
    assert matrix[1][names.index("no_lookalike")] == 0
    altered_non_features = {**rows[0], "decision": "block", "risk_score": -1, "label": "pass"}
    assert encode_features([altered_non_features])[0] == encode_features([rows[0]])[0]


def test_defaults_and_block_join_rejects_name_and_feature_mismatches() -> None:
    row = _prediction(
        "fixture-name",
        "flag",
        status="not_found",
        age=None,
        releases=None,
        has_source=False,
        distance=None,
        rank=None,
        similarity=None,
        decision="warn",
    )
    with pytest.raises(ValueError, match="names differ"):
        join_rule_rows([row], [], scenario="fixture")
    with pytest.raises(ValueError, match="names differ"):
        join_rule_rows([row], [{**row, "name": "different-name"}], scenario="fixture")
    changed_features = {**row, "features": {**row["features"], "age_days": 7}}
    with pytest.raises(ValueError, match="feature rows differ"):
        join_rule_rows([row], [changed_features], scenario="fixture")


def test_dataset_diagnostics_find_separation_constants_and_counts() -> None:
    rows = [
        {"expected": "flag"},
        {"expected": "flag"},
        {"expected": "pass"},
        {"expected": "pass"},
    ]
    matrix = [[1.0, 7.0], [1.0, 7.0], [0.0, 7.0], [0.0, 7.0]]

    diagnostics = dataset_diagnostics(rows, ["registry_status=not_found", "constant"], matrix)

    assert diagnostics == {
        "perfect_separating_features": ["registry_status=not_found"],
        "constant_features": ["constant"],
        "class_counts": {"flag": 2, "pass": 2},
    }


def test_dataset_diagnostics_require_a_single_threshold_split() -> None:
    rows = [{"expected": "flag"}, {"expected": "pass"}, {"expected": "flag"}]
    # Distinct, interleaved values: the classes share no value, but no threshold separates them.
    matrix = [[0.1, 0.0], [0.5, 1.0], [0.9, 0.0]]

    diagnostics = dataset_diagnostics(rows, ["interleaved", "split"], matrix)

    assert diagnostics["perfect_separating_features"] == ["split"]


def test_classifier_runs_are_deterministic_and_folds_assign_each_row_once(tmp_path: Path) -> None:
    paths = _datasets(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"

    _run(paths, first)
    _run(paths, second)

    stable_files = ("comparison.json", "report.md", "oof_predictions.jsonl")
    assert all(
        (first / filename).read_bytes() == (second / filename).read_bytes()
        for filename in stable_files
    )
    assert {path.name for path in first.iterdir()} == {
        *stable_files,
        "run_info.json",
    }
    predictions = [
        json.loads(line)
        for line in (first / "oof_predictions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for scenario in ("recorded_state", "attack_time"):
        rows = [row for row in predictions if row["scenario"] == scenario]
        assert len(rows) == 20
        assert len({row["name"] for row in rows}) == 20
        assert all(1 <= row["fold"] <= 5 for row in rows)
    shift = [row for row in predictions if row["scenario"] == "recorded_to_attack_time_shift"]
    assert len(shift) == 20 and all(row["fold"] is None for row in shift)
    comparison = json.loads((first / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["parameters"]["seed"] == 73
    assert comparison["scenarios"]["recorded_state"]["classifier"]["roc_auc"] is not None
    report = (first / "report.md").read_text(encoding="utf-8")
    assert "## Interpretation" in report
    assert "registry_status=not_found" in report
    assert "Constant features:" in report
    assert "No conclusion has been written yet" in report
    assert "full-data fit; not CV coefficients" in report
    assert "## Interpretation constraints" not in report


def test_classifier_rule_metrics_reuse_shared_binary_metrics(tmp_path: Path) -> None:
    paths = _datasets(tmp_path)
    output = tmp_path / "result"
    _run(paths, output)
    result = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    scenario = result["scenarios"]["recorded_state"]
    expected_rows = [
        {"expected": "flag" if index < 10 else "pass", "decision": "warn" if index < 10 else "pass"}
        for index in range(20)
    ]
    defaults_metrics = binary_metrics(expected_rows, flagged_decisions=FLAGGED_WARN_OR_BLOCK)
    for key, expected in defaults_metrics.items():
        actual = scenario["defaults_rule_warn_or_block"][key]
        assert (
            actual == expected
            if isinstance(expected, dict) or expected is None
            else actual == round(expected, 6)
        )
    assert scenario["defaults_rule_warn_or_block"]["roc_auc"] == 0.0
    block_rows = [
        {"expected": "flag" if index < 10 else "pass", "decision": "block" if index < 5 else "pass"}
        for index in range(20)
    ]
    block_metrics = binary_metrics(block_rows, flagged_decisions=FLAGGED_BLOCK_ONLY)
    for key, expected in block_metrics.items():
        actual = scenario["block_rule_block_only"][key]
        assert (
            actual == expected
            if isinstance(expected, dict) or expected is None
            else actual == round(expected, 6)
        )


def test_random_forest_model_is_available_without_coefficients(tmp_path: Path) -> None:
    paths = _datasets(tmp_path)
    result = run_classifier(
        defaults_predictions_path=paths["defaults"],
        defaults_attack_time_path=paths["defaults_attack"],
        block_predictions_path=paths["block"],
        block_attack_time_path=paths["block_attack"],
        out_dir=tmp_path / "random-forest",
        model="random-forest",
        seed=73,
        folds=5,
    )
    assert result["parameters"]["model"] == "random-forest"
    assert result["scenarios"]["recorded_state"]["coefficients_full_data_fit"] is None


def test_report_formats_undefined_metrics_as_na(tmp_path: Path) -> None:
    paths = _datasets(tmp_path)
    _run(paths, tmp_path / "result")
    result = json.loads((tmp_path / "result" / "comparison.json").read_text(encoding="utf-8"))
    result["scenarios"]["recorded_state"]["classifier"]["precision"] = None

    report = _render_report(result, "Fixture conclusion.")

    assert "| Classifier | 10 | 0 | 10 | 0 | n/a |" in report
    assert "| Classifier | 10 | 0 | 10 | 0 | None |" not in report


def test_prediction_loader_requires_finite_rule_risk_score(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    row = _prediction(
        "fixture-name",
        "pass",
        status="exists",
        age=3,
        releases=2,
        has_source=True,
        distance=None,
        rank=None,
        similarity=None,
        decision="pass",
        risk_score=float("nan"),
    )
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="risk_score must be a finite number"):
        _load_prediction_rows(path)


def test_probabilities_are_rounded_so_float_noise_cannot_change_results() -> None:
    from evals.classifier import _positive_probabilities
    from evals.constants import CLASSIFIER_PROBABILITY_DECIMALS

    class _Estimator:
        classes_ = [0, 1]

        def __init__(self, noise: float) -> None:
            self.noise = noise

        def predict_proba(self, matrix):
            return [[1 - value - self.noise, value + self.noise] for value in (1e-9, 2e-9, 0.25)]

    clean = _positive_probabilities(_Estimator(0.0), [[0.0]] * 3)
    noisy = _positive_probabilities(_Estimator(1e-12), [[0.0]] * 3)

    assert clean == noisy
    assert clean[0] == clean[1] == 0.0
    assert all(round(value, CLASSIFIER_PROBABILITY_DECIMALS) == value for value in clean)
