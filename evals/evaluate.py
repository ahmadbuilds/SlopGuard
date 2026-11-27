"""Evaluate labeled packages from immutable PyPI recordings without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from packaging.utils import canonicalize_name

from slopguard import __version__
from slopguard.config import SlopGuardConfig, parse_config
from slopguard.contracts import DependencyResult
from slopguard.registry import RegistryLookupResult
from slopguard.scan import score_candidate
from slopguard.similarity import PopularPackageIndex

from .constants import (
    AUTOMATED_LABEL_REVIEWER,
    DEFAULT_ATTACK_TIME_DAYS,
    DEFAULT_ATTACK_TIME_RELEASES,
    DEFAULT_CONFIG_NAMES,
    DEFAULT_CONFIG_PATHS,
    DEFAULT_LABELS_PATH,
    DEFAULT_REAL_PRS_PATH,
    DEFAULT_REAL_PRS_REGISTRY_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_REPOSITORIES_PATH,
    DEFAULT_RESULTS_PATH,
    GIT_COMMAND_TIMEOUT_SECONDS,
    HASH_CHUNK_BYTES,
    MAX_REPORT_FIELD_CHARACTERS,
)
from .io import read_jsonl_objects, write_jsonl
from .metrics import evaluation_metrics
from .replay import ReplayClient
from .schema import Label, load_labels

_LIMITATIONS = (
    "This is a preliminary evaluation over a curated set of a few hundred names. Synthetic "
    "typosquats are not equivalent to real attacks, label coverage is incomplete, and registry "
    "state can change after recording. Metrics should guide review, not be read as a malware verdict."
)


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """A named immutable configuration and its reproducibility digest."""

    name: str
    value: SlopGuardConfig
    sha256: str
    config_file: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_defaults(config: SlopGuardConfig) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_evaluation_configs(paths: tuple[Path, ...] | None = None) -> tuple[EvaluationConfig, ...]:
    """Load explicit configurations or package defaults plus the example block policy."""

    if paths is None:
        defaults = SlopGuardConfig()
        block_path = DEFAULT_CONFIG_PATHS[1]
        configs = [
            EvaluationConfig(
                DEFAULT_CONFIG_NAMES[0], defaults, _sha256_defaults(defaults), "package defaults"
            )
        ]
        configs.append(_load_file_config(block_path, DEFAULT_CONFIG_NAMES[1]))
    else:
        configs = [_load_file_config(path, path.stem) for path in paths]
    names = [config.name for config in configs]
    if len(set(names)) != len(names):
        raise ValueError("evaluation config names must be unique")
    safe_names = [_safe_config_name(name) for name in names]
    if len(set(safe_names)) != len(safe_names):
        raise ValueError("evaluation config names must map to unique output directories")
    return tuple(configs)


def _load_file_config(path: Path, name: str) -> EvaluationConfig:
    raw = path.read_bytes()
    config = parse_config(raw.decode("utf-8"), source=str(path), kind="slopguard")
    return EvaluationConfig(name, config, hashlib.sha256(raw).hexdigest(), path.name)


def evaluate_labels(
    labels: tuple[Label, ...],
    registry: ReplayClient,
    *,
    config: EvaluationConfig,
    index: PopularPackageIndex,
    recorded_at: datetime,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Score each non-excluded label and compute threshold and category metrics."""

    predictions: list[dict[str, object]] = []
    excluded_rows: list[dict[str, str]] = []
    llm_counts: dict[str, dict[str, int]] = {}
    llm_totals = _new_llm_counts()
    for label in labels:
        if label.expected == "exclude":
            excluded_rows.append({"name": label.name, "reason": "label marked exclude"})
            continue
        lookup = registry.lookup(label.name)
        llm_model_counts: list[dict[str, int]] = []
        is_hallucinated = label.label == "hallucinated"
        if is_hallucinated:
            model_names = tuple(sorted(set(label.models or (_llm_model(label),))))
            llm_model_counts = [
                llm_counts.setdefault(model, _new_llm_counts()) for model in model_names
            ]
            for counts in (*llm_model_counts, llm_totals):
                counts["suggested_by_models_count"] += 1
                if lookup.status in {"exists", "not_found"}:
                    counts["checked_count"] += 1
                else:
                    counts["inconclusive_count"] += 1
                if lookup.status == "not_found":
                    counts["not_found_count"] += 1
            if lookup.status == "exists":
                excluded_rows.append(
                    {
                        "name": label.name,
                        "reason": "LLM-suggested name exists on PyPI (not a hallucination)",
                    }
                )
                continue
        if (
            label.label == "typosquat_synthetic"
            and lookup.status == "exists"
            and label.reviewed_by is None
        ):
            excluded_rows.append(
                {"name": label.name, "reason": "exists on PyPI; needs human review"}
            )
            continue
        result = score_candidate(
            _candidate_for_name(label),
            lookup,
            config=config.value,
            index=index,
            now=recorded_at,
        )
        predictions.append(_prediction(label, result, lookup, recorded_at))
        if is_hallucinated and lookup.status == "not_found":
            for counts in (*llm_model_counts, llm_totals):
                if result["decision"] in {"warn", "block"}:
                    counts["flagged_not_found_warn_or_block_count"] += 1
                if result["decision"] == "block":
                    counts["flagged_not_found_block_count"] += 1
    predictions.sort(key=lambda row: (canonicalize_name(str(row["name"])), str(row["name"])))
    metrics = evaluation_metrics(
        (
            {
                "expected": str(row["expected"]),
                "decision": str(row["decision"]),
                "category": str(row["category"]),
            }
            for row in predictions
        ),
        excluded_count=len(excluded_rows),
    )
    metrics["excluded_rows"] = sorted(
        excluded_rows, key=lambda row: (canonicalize_name(row["name"]), row["name"])
    )
    metrics["exists_synthetic_review_exclusions"] = [
        row["name"]
        for row in metrics["excluded_rows"]
        if row["reason"] == "exists on PyPI; needs human review"
    ]
    llm_by_model = {
        model: {
            **counts,
            "hallucination_rate": _rate(counts["not_found_count"], counts["checked_count"]),
        }
        for model, counts in sorted(llm_counts.items())
    }
    metrics["hallucinated_outputs"] = {
        **{key: value for key, value in llm_totals.items() if key != "suggested_by_models_count"},
        "hallucination_rate": _rate(llm_totals["not_found_count"], llm_totals["checked_count"]),
        "by_model": llm_by_model,
    }
    return tuple(predictions), metrics


def _llm_model(label: Label) -> str:
    """Return the model recorded in a hallucinated label's provenance string."""

    prefix = "LLM model="
    if label.source.startswith(prefix) and "; date=" in label.source:
        model = label.source[len(prefix) :].split("; date=", maxsplit=1)[0].strip()
        if model:
            return model
    return "unknown"


def _new_llm_counts() -> dict[str, int]:
    return {
        "suggested_by_models_count": 0,
        "checked_count": 0,
        "not_found_count": 0,
        "inconclusive_count": 0,
        "flagged_not_found_warn_or_block_count": 0,
        "flagged_not_found_block_count": 0,
    }


def _verification_coverage(labels: tuple[Label, ...]) -> dict[str, object]:
    classes: dict[str, dict[str, int]] = {}
    failures: list[dict[str, str]] = []
    for label in labels:
        is_verified = label.reviewed_by == AUTOMATED_LABEL_REVIEWER
        if label.label == "borderline" and is_verified and ":" in label.category:
            class_name = label.category.split(":", maxsplit=1)[0]
        elif label.label == "borderline" or label.expected == "exclude":
            continue
        else:
            class_name = label.label
        stats = classes.setdefault(class_name, {"verified": 0, "total": 0, "failed": 0})
        stats["total"] += 1
        marker = "verification failed:"
        if is_verified and marker in label.rationale:
            stats["failed"] += 1
            failures.append(
                {
                    "class": class_name,
                    "name": label.name,
                    "reason": label.rationale.split(marker, maxsplit=1)[1].strip(),
                }
            )
        elif is_verified:
            stats["verified"] += 1
    return {
        "verified": sum(item["verified"] for item in classes.values()),
        "total": sum(item["total"] for item in classes.values()),
        "failed": len(failures),
        "by_class": dict(sorted(classes.items())),
        "failed_rows": sorted(failures, key=lambda item: (item["class"], item["name"])),
    }


def _verification_coverage_sentence(value: dict[str, object]) -> str:
    return f"Verified {value['verified']}/{value['total']} labels; verification failures: {value['failed']}."


def evaluate_attack_time(
    labels: tuple[Label, ...],
    *,
    config: EvaluationConfig,
    index: PopularPackageIndex,
    recorded_at: datetime,
    excluded_names: frozenset[str],
    days: int = DEFAULT_ATTACK_TIME_DAYS,
    releases: int = DEFAULT_ATTACK_TIME_RELEASES,
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Score flag and pass labels with synthetic fresh-registration metadata."""

    if days <= 0 or releases <= 0:
        raise ValueError("attack-time days and releases must be positive")
    predictions: list[dict[str, object]] = []
    excluded_count = 0
    for label in labels:
        if label.expected == "exclude":
            excluded_count += 1
            continue
        if label.expected not in {"flag", "pass"}:
            continue
        if canonicalize_name(label.name, validate=True) in excluded_names:
            excluded_count += 1
            continue
        first_release = recorded_at - timedelta(days=days)
        timestamp = first_release.isoformat().replace("+00:00", "Z")
        lookup = RegistryLookupResult(
            normalized_name=canonicalize_name(label.name, validate=True),
            status="exists",
            metadata={
                "first_release_at": timestamp,
                "latest_release_at": timestamp,
                "release_count": releases,
                "project_urls": {},
                "maintainer_hints": [],
                "source_hints": [],
            },
            latency_ms=0.0,
            attempts=1,
            http_status=200,
            retry_after=None,
            rate_limit_remaining=None,
            error=None,
        )
        result = score_candidate(
            _candidate_for_name(label),
            lookup,
            config=config.value,
            index=index,
            now=recorded_at,
        )
        predictions.append(_prediction(label, result, lookup, recorded_at))
    predictions.sort(key=lambda row: (canonicalize_name(str(row["name"])), str(row["name"])))
    metrics = evaluation_metrics(
        (
            {
                "expected": str(row["expected"]),
                "decision": str(row["decision"]),
                "category": str(row["category"]),
            }
            for row in predictions
        ),
        excluded_count=excluded_count,
    )
    return tuple(predictions), metrics


def _candidate_for_name(label: Label) -> DependencyResult:
    normalized = canonicalize_name(label.name, validate=True)
    return {
        "contract_version": "1.0.0",
        "requested_name": label.name,
        "normalized_name": normalized,
        "registry_exists": None,
        "metadata_evidence": [],
        "similarity_candidates": [],
        "risk_score": 0.0,
        "severity": "info",
        "remediation": "Await registry lookup and risk analysis before making policy decisions.",
    }


def _prediction(
    label: Label,
    result: DependencyResult,
    lookup: RegistryLookupResult,
    recorded_at: datetime,
) -> dict[str, object]:
    metadata = lookup.metadata
    first_release = metadata.get("first_release_at") if metadata else None
    age_days = _age_days(first_release, recorded_at)
    similarities = result.get("similarity_candidates", [])
    similarity_values = [
        item["similarity"]
        for item in similarities
        if isinstance(item.get("similarity"), (int, float))
    ]
    edit_distances = [
        item["edit_distance"] for item in similarities if isinstance(item.get("edit_distance"), int)
    ]
    ranks = [
        item["popularity_rank"]
        for item in similarities
        if isinstance(item.get("popularity_rank"), int)
    ]
    return {
        "name": label.name,
        "label": label.label,
        "expected": label.expected,
        "category": label.category,
        "decision": result["decision"],
        "risk_score": result["risk_score"],
        "severity": result["severity"],
        "contributions": result.get("score_contributions", []),
        "features": {
            "registry_status": result.get("registry_status"),
            "age_days": age_days,
            "release_count": metadata.get("release_count") if metadata else None,
            "has_source": bool(metadata and metadata.get("source_hints")),
            "best_similarity": max(similarity_values, default=None),
            "best_edit_distance": min(edit_distances, default=None),
            "best_rank": min(ranks, default=None),
        },
    }


def _age_days(first_release: object, recorded_at: datetime) -> int | None:
    if not isinstance(first_release, str):
        return None
    try:
        released_at = datetime.fromisoformat(first_release.replace("Z", "+00:00"))
    except ValueError:
        return None
    if released_at.tzinfo is None:
        return None
    return (recorded_at - released_at).days


def evaluate_real_prs(
    path: Path,
    registry: ReplayClient,
    *,
    config: EvaluationConfig,
    index: PopularPackageIndex,
    recorded_at: datetime,
) -> dict[str, object]:
    """Replay stored candidate lists and summarize review flags on merged PR samples."""

    rows = list(read_jsonl_objects(path))
    collection_summary: dict[str, object] = {"repositories": [], "failed_repositories": []}
    if rows and rows[0].get("record_type") == "collection_summary":
        collection_summary = rows.pop(0)
    pr_rows: list[dict[str, object]] = []
    flagged: list[dict[str, object]] = []
    warn_or_block_prs = 0
    block_prs = 0
    for pr in rows:
        pr_id = pr.get("repo")
        candidates = pr.get("candidates")
        if not isinstance(pr_id, str) or not isinstance(candidates, list):
            raise ValueError(f"{path}: each PR row needs repo and candidates")
        decisions: list[str] = []
        candidate_decisions: list[dict[str, str]] = []
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, dict):
                raise ValueError(f"{path}: candidates must be objects")
            candidate = cast(DependencyResult, raw_candidate)
            lookup = registry.lookup(candidate["normalized_name"])
            result = score_candidate(
                candidate,
                lookup,
                config=config.value,
                index=index,
                now=recorded_at,
            )
            decision = result["decision"]
            decisions.append(decision)
            contributions = result.get("score_contributions", [])
            top_contribution = max(
                (item for item in contributions if isinstance(item, dict)),
                key=lambda item: float(item.get("points", 0)),
                default={},
            )
            candidate_decisions.append(
                {
                    "name": str(result["requested_name"]),
                    "decision": decision,
                    "reason": str(top_contribution.get("summary", "No scoring contribution")),
                }
            )
            if decision in {"warn", "block"}:
                locations = result.get("source_locations") or [{"file": "", "line": None}]
                for location in locations:
                    flagged.append(
                        {
                            "repo": pr_id,
                            "name": result["requested_name"],
                            "decision": decision,
                            "file": location.get("file", ""),
                            "line": location.get("line"),
                        }
                    )
        if any(decision in {"warn", "block"} for decision in decisions):
            warn_or_block_prs += 1
        if "block" in decisions:
            block_prs += 1
        pr_rows.append(
            {
                "repo": pr_id,
                "base": str(pr.get("base", "")),
                "head": str(pr.get("head", "")),
                "subject": str(pr.get("subject", "")),
                "candidates": candidate_decisions,
            }
        )
    pr_count = len(rows)
    candidate_count = sum(len(row["candidates"]) for row in pr_rows)
    flagged_candidate_count = sum(
        item["decision"] in {"warn", "block"} for row in pr_rows for item in row["candidates"]
    )
    blocked_candidate_count = sum(
        item["decision"] == "block" for row in pr_rows for item in row["candidates"]
    )
    return {
        "pr_count": pr_count,
        "candidate_count": candidate_count,
        "warn_or_block_candidate_count": flagged_candidate_count,
        "warn_or_block_candidate_false_positive_rate": _rate(
            flagged_candidate_count, candidate_count
        ),
        "block_candidate_count": blocked_candidate_count,
        "block_candidate_false_positive_rate": _rate(blocked_candidate_count, candidate_count),
        "warn_or_block_pr_count": warn_or_block_prs,
        "warn_or_block_false_positive_rate": _rate(warn_or_block_prs, pr_count),
        "block_pr_count": block_prs,
        "block_false_positive_rate": _rate(block_prs, pr_count),
        "repositories": collection_summary.get("repositories", []),
        "failed_repositories": collection_summary.get("failed_repositories", []),
        "selection_parameters": collection_summary.get("selection_parameters"),
        "pull_requests": pr_rows,
        "flagged_candidates": sorted(
            flagged,
            key=lambda row: (
                str(row["repo"]),
                str(row["file"]),
                int(row["line"]) if row["line"] is not None else 0,
                str(row["name"]),
            ),
        ),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _write_evaluation(
    *,
    output_dir: Path,
    config: EvaluationConfig,
    predictions: tuple[dict[str, object], ...],
    metrics: dict[str, object],
    labels_path: Path,
    registry_path: Path,
    recorded_at: str,
    runtime_seconds: float,
    attack_time_predictions: tuple[dict[str, object], ...],
    attack_time_metrics: dict[str, object],
    real_pr_metrics: dict[str, object] | None,
    real_prs_path: Path | None,
    real_prs_registry_path: Path | None,
) -> None:
    config_dir = output_dir / _safe_config_name(config.name)
    config_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(config_dir / "predictions.jsonl", predictions)
    write_jsonl(config_dir / "attack_time_predictions.jsonl", attack_time_predictions)
    result = {
        "tool_version": __version__,
        "sha256": {
            "labels": _sha256_file(labels_path),
            "registry": _sha256_file(registry_path),
            "config": config.sha256,
        },
        "recorded_at": recorded_at,
        "metrics": metrics,
        "attack_time": attack_time_metrics,
        "real_prs": real_pr_metrics,
    }
    _write_json(config_dir / "metrics.json", result)
    provenance: dict[str, object] = {
        "tool_version": __version__,
        "recorded_at": recorded_at,
        "config_file": config.config_file,
        "config_sha256": config.sha256,
        "labels_sha256": _sha256_file(labels_path),
        "registry_sha256": _sha256_file(registry_path),
    }
    if real_pr_metrics is not None:
        assert real_prs_path is not None and real_prs_registry_path is not None
        provenance["real_prs_sha256"] = _sha256_file(real_prs_path)
        provenance["real_prs_registry_sha256"] = _sha256_file(real_prs_registry_path)
    _write_text(
        config_dir / "report.md",
        _render_report(config, metrics, attack_time_metrics, real_pr_metrics, provenance),
    )
    _write_json(
        config_dir / "run_info.json",
        {"git_commit": _current_git_commit(), "runtime_seconds": round(runtime_seconds, 3)},
    )


def _current_git_commit() -> str | None:
    """Read this checkout's commit when available without failing evaluation."""
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
    return value if re.fullmatch(r"[0-9a-fA-F]{40,64}", value) else None


def _safe_config_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.")
    if not normalized:
        raise ValueError("config name has no safe path characters")
    return normalized


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_text(path: Path, value: str) -> None:
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _render_report(
    config: EvaluationConfig,
    metrics: dict[str, object],
    attack_time_metrics: dict[str, object],
    real_pr_metrics: dict[str, object] | None,
    provenance: dict[str, object],
) -> str:
    modes = (
        ("Flagged = warn or block", metrics["flagged_warn_or_block"]),
        ("Flagged = block", metrics["flagged_block"]),
    )
    lines = [
        f"# SlopGuard evaluation: {_markdown_text(config.name)}",
        "",
        f"Included labels: {metrics['included_count']}; excluded labels: {metrics['excluded_count']}",
        "",
        "## Run provenance",
        "",
        f"Tool version: {_markdown_text(provenance['tool_version'])}; recording date: "
        f"{_markdown_text(provenance['recorded_at'])}; config: "
        f"{_markdown_text(provenance['config_file'])} (SHA256 "
        f"{provenance['config_sha256']}).",
        f"Labels SHA256: {provenance['labels_sha256']}; registry recording SHA256: "
        f"{provenance['registry_sha256']}.",
        (
            f"Real PR file SHA256: {provenance['real_prs_sha256']}; real PR registry SHA256: "
            f"{provenance['real_prs_registry_sha256']}."
            if "real_prs_sha256" in provenance
            else "Real PR recordings were not used."
        ),
        "Git commit and runtime are in run_info.json because they vary between runs.",
        "",
        "## Verification coverage",
        "",
        _verification_coverage_sentence(cast(dict[str, object], metrics["verification_coverage"])),
        "Labels are verified automatically against their primary sources (OSV records, the seeded generators, the popularity snapshot and recorded model outputs); they were not individually reviewed by a person.",
        "",
        "## Metrics",
        "",
        "| Threshold | TP | FP | TN | FN | Precision | Recall | FPR | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    coverage = cast(dict[str, object], metrics["verification_coverage"])
    coverage_lines = [
        "",
        "| Class | Verified | Total | Failed |",
        "| --- | ---: | ---: | ---: |",
    ]
    by_class = cast(dict[str, dict[str, int]], coverage["by_class"])
    for class_name, counts in sorted(by_class.items()):
        coverage_lines.append(
            f"| {_markdown_text(class_name)} | "
            f"{counts['verified']} | {counts['total']} | {counts['failed']} |"
        )
    failed_rows = cast(list[dict[str, str]], coverage["failed_rows"])
    if failed_rows:
        coverage_lines.extend(("", "Verification failures:"))
        coverage_lines.extend(
            f"- {_markdown_text(row['class'])}, {_markdown_text(row['name'])}: "
            f"{_markdown_text(row['reason'])}"
            for row in failed_rows
        )
    coverage_index = lines.index("## Verification coverage") + 3
    lines[coverage_index:coverage_index] = coverage_lines
    for title, raw_metrics in modes:
        mode = cast(dict[str, object], raw_metrics)
        confusion = cast(dict[str, int], mode["confusion"])
        lines.append(
            "| "
            + " | ".join(
                (
                    title,
                    str(confusion["tp"]),
                    str(confusion["fp"]),
                    str(confusion["tn"]),
                    str(confusion["fn"]),
                    _format_metric(mode["precision"]),
                    _format_metric(mode["recall"]),
                    _format_metric(mode["false_positive_rate"]),
                    _format_metric(mode["f1"]),
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "Precision = TP / (TP + FP); recall = TP / (TP + FN); FPR = FP / (FP + TN); "
            "F1 = 2 × precision × recall / (precision + recall).",
            "Undefined metrics use `null` in JSON and `n/a` here when their denominator is zero.",
            "",
            "## Per-category breakdown",
            "",
            "| Category | Threshold | n | TP | FP | TN | FN | Precision | Recall | FPR | F1 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    by_category = cast(dict[str, dict[str, object]], metrics["by_category"])
    for category, category_metrics in sorted(by_category.items()):
        for threshold_name, metric_key in (
            ("warn or block", "flagged_warn_or_block"),
            ("block only", "flagged_block"),
        ):
            category_result = cast(dict[str, object], category_metrics[metric_key])
            confusion = cast(dict[str, int], category_result["confusion"])
            lines.append(
                "| "
                + " | ".join(
                    (
                        _markdown_text(category),
                        threshold_name,
                        str(category_metrics["count"]),
                        str(confusion["tp"]),
                        str(confusion["fp"]),
                        str(confusion["tn"]),
                        str(confusion["fn"]),
                        _format_metric(category_result["precision"]),
                        _format_metric(category_result["recall"]),
                        _format_metric(category_result["false_positive_rate"]),
                        _format_metric(category_result["f1"]),
                    )
                )
                + " |"
            )
    excluded_rows = cast(list[dict[str, str]], metrics.get("excluded_rows", []))
    if excluded_rows:
        lines.extend(("", "## Excluded rows", "", "| Package | Reason |", "| --- | --- |"))
        for row in excluded_rows:
            lines.append(f"| {_markdown_text(row['name'])} | {_markdown_text(row['reason'])} |")
    hallucinated = cast(dict[str, object], metrics["hallucinated_outputs"])
    lines.extend(
        (
            "",
            "## LLM-suggested package outcomes",
            "",
            f"Checked {hallucinated['checked_count']} names; "
            f"{hallucinated['not_found_count']} did not exist on PyPI "
            f"(hallucination rate {_format_metric(hallucinated['hallucination_rate'])}); "
            f"SlopGuard flagged {hallucinated['flagged_not_found_warn_or_block_count']} "
            "not-found names (warn or block).",
            f"{hallucinated['inconclusive_count']} additional lookups were inconclusive and are "
            "excluded from the rate denominator.",
            "",
            "| Model | Checked | Not found | Hallucination rate | Flagged (warn/block) | Blocked |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    llm_by_model = cast(dict[str, dict[str, object]], hallucinated["by_model"])
    for model, model_metrics in sorted(llm_by_model.items()):
        lines.append(
            f"| {_markdown_text(model)} | {model_metrics['checked_count']} | "
            f"{model_metrics['not_found_count']} | "
            f"{_format_metric(model_metrics['hallucination_rate'])} | "
            f"{model_metrics['flagged_not_found_warn_or_block_count']} | "
            f"{model_metrics['flagged_not_found_block_count']} |"
        )
    lines.append(
        "A name suggested by more than one model is counted once for each model; "
        "the overall line counts unique names."
    )
    lines.extend(
        (
            "",
            "## Attack-time scenario (as if freshly registered)",
            "",
            f"Synthetic metadata: first release {attack_time_metrics['days']} day(s) before "
            f"recording, {attack_time_metrics['releases']} release(s), and no source links.",
            "New-package signals alone can cross the warn threshold, so expected-pass controls may be warned.",
            "",
            "| Threshold | TP | FP | TN | FN | Precision | Recall | FPR |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    attack_metrics = cast(dict[str, object], attack_time_metrics["metrics"])
    for title, key in (("Warn or block", "flagged_warn_or_block"), ("Block only", "flagged_block")):
        mode = cast(dict[str, object], attack_metrics[key])
        confusion = cast(dict[str, int], mode["confusion"])
        lines.append(
            f"| {title} | {confusion['tp']} | {confusion['fp']} | {confusion['tn']} | "
            f"{confusion['fn']} | {_format_metric(mode['precision'])} | "
            f"{_format_metric(mode['recall'])} | {_format_metric(mode['false_positive_rate'])} |"
        )
    if real_pr_metrics is not None:
        selection = real_pr_metrics.get("selection_parameters")
        if isinstance(selection, dict):
            repositories = cast(list[dict[str, object]], selection.get("repositories", []))
            repository_values = ", ".join(
                f"{item.get('url', '')} ({item.get('default_branch', '')})" for item in repositories
            )
            lines.extend(
                (
                    "",
                    "## Historical pull request sample",
                    "",
                    f"Selection rule: {_markdown_text(selection.get('rule', ''))}; "
                    f"repositories/default branches: {_markdown_text(repository_values)}; "
                    f"per_repo={_markdown_text(selection.get('per_repo', ''))}; "
                    f"cutoff_date={_markdown_text(selection.get('cutoff_date', ''))}; "
                    f"MAX_COMMITS_TO_INSPECT={_markdown_text(selection.get('max_commits_to_inspect', ''))}.",
                )
            )
        else:
            lines.extend(
                ("", "## Historical pull request sample", "", "Selection parameters not recorded.")
            )
        lines.extend(
            (
                f"Candidate-level FPR (flagged new dependencies / all new dependencies): "
                f"{real_pr_metrics['warn_or_block_candidate_count']}/"
                f"{real_pr_metrics['candidate_count']} = "
                f"{_format_metric(real_pr_metrics['warn_or_block_candidate_false_positive_rate'])}; "
                f"block-only candidate rate: "
                f"{_format_metric(real_pr_metrics['block_candidate_false_positive_rate'])}.",
                f"PR-level warn/block rate: {real_pr_metrics['warn_or_block_pr_count']}/"
                f"{real_pr_metrics['pr_count']} = "
                f"{_format_metric(real_pr_metrics['warn_or_block_false_positive_rate'])}; "
                f"block-only PR rate: {_format_metric(real_pr_metrics['block_false_positive_rate'])}.",
                "",
                "### Repository inspection",
                "",
                "| Repository | Commits inspected | Inspection limit reached |",
                "| --- | ---: | --- |",
            )
        )
        for repo in cast(list[dict[str, object]], real_pr_metrics["repositories"]):
            lines.append(
                f"| {_markdown_text(repo.get('repo', ''))} | "
                f"{_markdown_text(repo.get('inspected_commits', ''))} | "
                f"{_markdown_text(repo.get('inspection_limit_reached', ''))} |"
            )
        lines.extend(
            (
                "",
                "### Pull requests and candidates",
                "",
                "| Repository | Base..head | Subject | Candidate names and decisions | Reason |",
                "| --- | --- | --- | --- | --- |",
            )
        )
        for pr in cast(list[dict[str, object]], real_pr_metrics["pull_requests"]):
            candidates = cast(list[dict[str, str]], pr["candidates"])
            candidate_text = (
                "; ".join(f"{item['name']} ({item['decision']})" for item in candidates)
                or "(no candidates)"
            )
            reason_text = (
                "; ".join(
                    f"{item['name']}: {item['reason']}"
                    for item in candidates
                    if item["decision"] in {"warn", "block"}
                )
                or "—"
            )
            lines.append(
                f"| {_markdown_text(pr['repo'])} | "
                f"{_markdown_text(pr['base'])}..{_markdown_text(pr['head'])} | "
                f"{_markdown_text(pr['subject'])} | {_markdown_text(candidate_text)} | "
                f"{_markdown_text(reason_text)} |"
            )
        failures = cast(list[dict[str, object]], real_pr_metrics["failed_repositories"])
        if failures:
            lines.extend(("", "### Failed repositories", ""))
            for failure in failures:
                lines.append(
                    f"- {_markdown_text(failure['repo'])}: {_markdown_text(failure['reason'])}"
                )
        lines.extend(
            (
                "",
                "These are proxy false-positive rates: merged history is not a human-verified "
                "ground-truth label set.",
            )
        )
    lines.extend(("", "## Limitations", "", _LIMITATIONS))
    return "\n".join(lines) + "\n"


def _format_metric(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.4f}"


def _markdown_text(value: object) -> str:
    text = str(value)[:MAX_REPORT_FIELD_CHARACTERS]
    escaped: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if character in "\r\n":
            escaped.append("\\n")
        elif category in {"Cc", "Cf"}:
            escaped.append(f"\\u{ord(character):04x}")
        elif character in "\\`*_{}[]()#+-.!|><~":
            escaped.append("\\" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def run_evaluation(
    *,
    labels_path: Path = DEFAULT_LABELS_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    config_paths: tuple[Path, ...] | None = None,
    output_dir: Path = DEFAULT_RESULTS_PATH,
    real_prs_path: Path | None = None,
    real_prs_registry_path: Path = DEFAULT_REAL_PRS_REGISTRY_PATH,
    attack_time_days: int = DEFAULT_ATTACK_TIME_DAYS,
    attack_time_releases: int = DEFAULT_ATTACK_TIME_RELEASES,
) -> None:
    """Evaluate recorded labels and optionally recorded real pull-request candidates."""

    started = time.monotonic()
    labels = load_labels(labels_path)
    registry = ReplayClient(registry_path)
    recorded_at_value = registry.header["recorded_at"]
    recorded_at = datetime.fromisoformat(str(recorded_at_value).replace("Z", "+00:00"))
    index = PopularPackageIndex.load()
    configs = load_evaluation_configs(config_paths)
    real_pr_data = None
    if real_prs_path is not None:
        real_registry = ReplayClient(real_prs_registry_path)
        real_recorded_at = datetime.fromisoformat(
            str(real_registry.header["recorded_at"]).replace("Z", "+00:00")
        )
        # PR and label recordings may be from distinct PyPI snapshots; use the PR snapshot time.
        real_pr_data = (real_prs_path, real_registry, real_recorded_at)
    for config in configs:
        predictions, metrics = evaluate_labels(
            labels,
            registry,
            config=config,
            index=index,
            recorded_at=recorded_at,
        )
        metrics["verification_coverage"] = _verification_coverage(labels)
        excluded_names = frozenset(
            canonicalize_name(str(row["name"]), validate=True)
            for row in cast(list[dict[str, str]], metrics["excluded_rows"])
        )
        attack_predictions, attack_metrics = evaluate_attack_time(
            labels,
            config=config,
            index=index,
            recorded_at=recorded_at,
            excluded_names=excluded_names,
            days=attack_time_days,
            releases=attack_time_releases,
        )
        attack_metrics = {
            "days": attack_time_days,
            "releases": attack_time_releases,
            "metrics": attack_metrics,
        }
        real_pr_metrics = None
        if real_pr_data is not None:
            real_path, real_registry, real_recorded_at = real_pr_data
            real_pr_metrics = evaluate_real_prs(
                real_path,
                real_registry,
                config=config,
                index=index,
                recorded_at=real_recorded_at,
            )
        _write_evaluation(
            output_dir=output_dir,
            config=config,
            predictions=predictions,
            metrics=metrics,
            labels_path=labels_path,
            registry_path=registry_path,
            recorded_at=str(recorded_at_value),
            runtime_seconds=time.monotonic() - started,
            attack_time_predictions=attack_predictions,
            attack_time_metrics=attack_metrics,
            real_pr_metrics=real_pr_metrics,
            real_prs_path=real_prs_path,
            real_prs_registry_path=(real_prs_registry_path if real_pr_data is not None else None),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--config", type=Path, action="append")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--real-prs", type=Path, default=None)
    parser.add_argument("--real-prs-registry", type=Path, default=DEFAULT_REAL_PRS_REGISTRY_PATH)
    parser.add_argument("--attack-time-days", type=int, default=DEFAULT_ATTACK_TIME_DAYS)
    parser.add_argument("--attack-time-releases", type=int, default=DEFAULT_ATTACK_TIME_RELEASES)
    args = parser.parse_args(argv)
    if args.attack_time_days <= 0 or args.attack_time_releases <= 0:
        parser.error("attack-time days and releases must be positive integers")
    if not args.labels.exists():
        print(
            f"error: missing {args.labels}; create labels with `uv run python -m evals.merge_labels ... --out {args.labels}`",
            file=sys.stderr,
        )
        return 1
    if not args.registry.exists():
        print(
            f"error: missing {args.registry}; create registry data with `uv run python -m evals.record --labels {args.labels} --registry {args.registry}`",
            file=sys.stderr,
        )
        return 1
    if args.real_prs is None and DEFAULT_REAL_PRS_PATH.exists():
        if not args.real_prs_registry.exists():
            print(
                f"error: missing {args.real_prs_registry}; recreate PR data with `uv run python -m evals.collect_prs --repos {DEFAULT_REPOSITORIES_PATH}`",
                file=sys.stderr,
            )
            return 1
        args.real_prs = DEFAULT_REAL_PRS_PATH
    elif args.real_prs is not None and not args.real_prs_registry.exists():
        print(
            f"error: missing {args.real_prs_registry}; create it with `uv run python -m evals.collect_prs --repos {DEFAULT_REPOSITORIES_PATH}`",
            file=sys.stderr,
        )
        return 1
    run_evaluation(
        labels_path=args.labels,
        registry_path=args.registry,
        config_paths=tuple(args.config) if args.config else None,
        output_dir=args.out_dir,
        real_prs_path=args.real_prs,
        real_prs_registry_path=args.real_prs_registry,
        attack_time_days=args.attack_time_days,
        attack_time_releases=args.attack_time_releases,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
