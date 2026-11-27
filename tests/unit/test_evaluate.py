"""Deterministic offline evaluation with a synthetic label and registry fixture."""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

import evals.evaluate as evaluate_module
from evals.evaluate import (
    evaluate_real_prs,
    load_evaluation_configs,
    run_evaluation,
)
from evals.evaluate import (
    main as evaluate_main,
)
from evals.replay import ReplayClient
from slopguard.registry import RegistryLookupResult
from slopguard.similarity import PopularPackageIndex

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "tests/fixtures/evals/labels.jsonl"
CONFIG = ROOT / "examples/slopguard.toml"
RECORDED_AT = "2026-01-01T00:00:00Z"


def _registry(path: Path) -> None:
    not_found = RegistryLookupResult(
        normalized_name="fixture-never-published-eval-name",
        status="not_found",
        metadata=None,
        latency_ms=1.0,
        attempts=1,
        http_status=404,
        retry_after=None,
        rate_limit_remaining=None,
        error=None,
    )
    existing = RegistryLookupResult(
        normalized_name="fixture-existing-eval-package",
        status="exists",
        metadata={
            "first_release_at": "2022-02-22T00:00:00Z",
            "release_count": 3,
            "latest_release_at": "2025-12-01T00:00:00Z",
            "project_urls": {"Source": "https://example.invalid/source"},
            "maintainer_hints": [],
            "source_hints": ["https://example.invalid/source"],
        },
        latency_ms=1.0,
        attempts=1,
        http_status=200,
        retry_after=None,
        rate_limit_remaining=None,
        error=None,
    )
    synthetic_exists = RegistryLookupResult(
        normalized_name="fixture-synthetic-exists-name",
        status="exists",
        metadata={
            "first_release_at": "2022-02-22T00:00:00Z",
            "release_count": 3,
            "latest_release_at": "2025-12-01T00:00:00Z",
            "project_urls": {},
            "maintainer_hints": [],
            "source_hints": [],
        },
        latency_ms=1.0,
        attempts=1,
        http_status=200,
        retry_after=None,
        rate_limit_remaining=None,
        error=None,
    )
    llm_exists = RegistryLookupResult(
        normalized_name="fixture-llm-existing-name",
        status="exists",
        metadata={
            "first_release_at": "2022-02-22T00:00:00Z",
            "release_count": 3,
            "latest_release_at": "2025-12-01T00:00:00Z",
            "project_urls": {},
            "maintainer_hints": [],
            "source_hints": [],
        },
        latency_ms=1.0,
        attempts=1,
        http_status=200,
        retry_after=None,
        rate_limit_remaining=None,
        error=None,
    )
    header = {"recorded_at": RECORDED_AT, "tool_version": "fixture", "count": 4}
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as output:
            for row in (
                header,
                not_found.to_dict(),
                existing.to_dict(),
                synthetic_exists.to_dict(),
                llm_exists.to_dict(),
            ):
                output.write(
                    (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                )


def test_evaluation_writes_deterministic_predictions_metrics_and_report(tmp_path: Path) -> None:
    registry = tmp_path / "registry.jsonl.gz"
    _registry(registry)
    output_one = tmp_path / "one"
    output_two = tmp_path / "two"

    for output in (output_one, output_two):
        run_evaluation(
            labels_path=LABELS,
            registry_path=registry,
            config_paths=(CONFIG,),
            output_dir=output,
            git_commit="fixture-commit",
        )

    first = output_one / "slopguard"
    second = output_two / "slopguard"
    stable_names = (
        "predictions.jsonl",
        "attack_time_predictions.jsonl",
        "metrics.json",
        "report.md",
    )
    assert {path.name for path in first.iterdir()} == {
        *stable_names,
        "run_info.json",
    }
    assert all((first / name).read_bytes() == (second / name).read_bytes() for name in stable_names)
    predictions = [
        json.loads(line) for line in (first / "predictions.jsonl").read_text().splitlines()
    ]
    assert len(predictions) == 2
    assert any(
        row["name"] == "fixture-never-published-eval-name" and row["expected"] == "flag"
        for row in predictions
    )
    metrics = json.loads((first / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["metrics"]["excluded_count"] == 3
    assert metrics["metrics"]["exists_synthetic_review_exclusions"] == [
        "fixture-synthetic-exists-name"
    ]
    assert metrics["recorded_at"] == RECORDED_AT
    assert metrics["sha256"]["labels"]
    assert {row["name"]: row["reason"] for row in metrics["metrics"]["excluded_rows"]}[
        "fixture-llm-existing-name"
    ] == ("LLM-suggested name exists on PyPI (not a hallucination)")
    llm_metrics = metrics["metrics"]["hallucinated_outputs"]
    assert llm_metrics["checked_count"] == 2
    assert llm_metrics["not_found_count"] == 1
    assert llm_metrics["hallucination_rate"] == 0.5
    assert llm_metrics["flagged_not_found_warn_or_block_count"] == 1
    assert llm_metrics["by_model"]["fixture-model-a"]["hallucination_rate"] == 1.0
    assert llm_metrics["by_model"]["fixture-model-a"]["flagged_not_found_warn_or_block_count"] == 1
    assert llm_metrics["by_model"]["fixture-model-a"]["suggested_by_models_count"] == 1
    assert llm_metrics["by_model"]["fixture-model-b"]["checked_count"] == 2
    assert llm_metrics["by_model"]["fixture-model-b"]["hallucination_rate"] == 0.5
    assert llm_metrics["by_model"]["fixture-model-b"]["suggested_by_models_count"] == 2
    report = (first / "report.md").read_text(encoding="utf-8")
    assert "Precision = TP" in report
    assert "exists on PyPI; needs human review" in report
    assert "LLM-suggested package outcomes" in report
    assert "Checked 2 names; 1 did not exist on PyPI" in report
    assert "SlopGuard flagged 1 not-found names" in report
    assert (
        "A name suggested by more than one model is counted once for each model; "
        "the overall line counts unique names."
    ) in report
    assert "Attack-time scenario" in report
    assert metrics["attack_time"]["days"] == 1
    assert metrics["attack_time"]["releases"] == 1
    attack_metrics = metrics["attack_time"]["metrics"]
    warn_metrics = attack_metrics["flagged_warn_or_block"]
    assert warn_metrics["confusion"] == {"tp": 1, "fp": 1, "tn": 0, "fn": 0}
    assert warn_metrics["precision"] == 0.5
    assert warn_metrics["recall"] == 1.0
    assert warn_metrics["false_positive_rate"] == 1.0
    block_metrics = attack_metrics["flagged_block"]
    assert block_metrics["confusion"] == {"tp": 0, "fp": 0, "tn": 1, "fn": 1}
    assert block_metrics["precision"] is None
    assert block_metrics["recall"] == 0.0
    assert block_metrics["false_positive_rate"] == 0.0
    attack_rows = [
        json.loads(line)
        for line in (first / "attack_time_predictions.jsonl").read_text().splitlines()
    ]
    assert len(attack_rows) == 2
    assert all(row["features"]["age_days"] == 1 for row in attack_rows)
    assert all(row["features"]["release_count"] == 1 for row in attack_rows)
    assert {row["expected"] for row in attack_rows} == {"flag", "pass"}
    assert "New-package signals alone can cross the warn threshold" in report
    assert all(
        header in report for header in ("| TP | FP | TN | FN |", "| Precision | Recall | FPR |")
    )


def test_evaluation_cli_fails_with_label_generation_command_when_inputs_are_missing(
    tmp_path: Path, capsys
) -> None:
    labels = tmp_path / "missing-labels.jsonl"
    registry = tmp_path / "missing-registry.jsonl.gz"

    assert evaluate_main(["--labels", str(labels), "--registry", str(registry)]) == 1

    assert "evals.merge_labels" in capsys.readouterr().err


def test_evaluation_cli_names_registry_record_command_when_registry_is_missing(
    tmp_path: Path, capsys
) -> None:
    labels = tmp_path / "labels.jsonl"
    labels.write_text("", encoding="utf-8")
    registry = tmp_path / "missing-registry.jsonl.gz"

    assert evaluate_main(["--labels", str(labels), "--registry", str(registry)]) == 1

    assert "evals.record" in capsys.readouterr().err


def test_evaluation_cli_uses_real_pr_files_when_present(tmp_path: Path, monkeypatch) -> None:
    labels = tmp_path / "labels.jsonl"
    registry = tmp_path / "registry.jsonl.gz"
    real_prs = tmp_path / "real_prs.jsonl"
    real_registry = tmp_path / "real_prs_registry.jsonl.gz"
    for path in (labels, registry, real_prs, real_registry):
        path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(evaluate_module, "DEFAULT_REAL_PRS_PATH", real_prs)
    monkeypatch.setattr(evaluate_module, "DEFAULT_REAL_PRS_REGISTRY_PATH", real_registry)
    captured: dict[str, object] = {}
    monkeypatch.setattr(evaluate_module, "run_evaluation", lambda **kwargs: captured.update(kwargs))

    assert (
        evaluate_main(
            [
                "--labels",
                str(labels),
                "--registry",
                str(registry),
                "--attack-time-days",
                "3",
                "--attack-time-releases",
                "2",
            ]
        )
        == 0
    )
    assert captured["real_prs_path"] == real_prs
    assert captured["real_prs_registry_path"] == real_registry
    assert captured["attack_time_days"] == 3
    assert captured["attack_time_releases"] == 2


def test_real_pr_evaluation_reports_flagged_candidate_locations(tmp_path: Path) -> None:
    registry = tmp_path / "registry.jsonl.gz"
    _registry(registry)
    prs = tmp_path / "real-prs.jsonl"
    candidate = {
        "contract_version": "1.0.0",
        "requested_name": "fixture-never-published-eval-name",
        "normalized_name": "fixture-never-published-eval-name",
        "registry_exists": None,
        "metadata_evidence": [],
        "similarity_candidates": [],
        "risk_score": 0.0,
        "severity": "info",
        "remediation": "await evaluation",
        "source_locations": [
            {"file": "requirements.txt", "line": 4, "source_type": "requirements"}
        ],
    }
    prs.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "record_type": "collection_summary",
                        "repositories": [
                            {"repo": "https://example.invalid/fixture.git", "inspected_commits": 9}
                        ],
                        "failed_repositories": [],
                    }
                ),
                json.dumps(
                    {
                        "repo": "https://example.invalid/fixture.git",
                        "base": "a" * 40,
                        "head": "b" * 40,
                        "subject": "fixture",
                        "selection_reason": "fixture row",
                        "candidates": [candidate],
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = evaluate_real_prs(
        prs,
        ReplayClient(registry),
        config=load_evaluation_configs((CONFIG,))[0],
        index=PopularPackageIndex.load(),
        recorded_at=datetime.fromisoformat(RECORDED_AT.replace("Z", "+00:00")),
    )

    assert result["pr_count"] == 1
    assert result["candidate_count"] == 1
    assert result["warn_or_block_candidate_false_positive_rate"] == 1.0
    assert result["warn_or_block_false_positive_rate"] == 1.0
    assert result["flagged_candidates"][0]["file"] == "requirements.txt"
    assert result["flagged_candidates"][0]["line"] == 4
    assert result["repositories"][0]["inspected_commits"] == 9
    pr = result["pull_requests"][0]
    assert pr["base"] == "a" * 40 and pr["head"] == "b" * 40
    assert pr["subject"] == "fixture"
    assert pr["candidates"][0]["name"] == "fixture-never-published-eval-name"
    assert pr["candidates"][0]["decision"] in {"warn", "block"}

    output_dir = tmp_path / "results"
    run_evaluation(
        labels_path=LABELS,
        registry_path=registry,
        config_paths=(CONFIG,),
        output_dir=output_dir,
        real_prs_path=prs,
        real_prs_registry_path=registry,
    )
    report = (output_dir / "slopguard" / "report.md").read_text(encoding="utf-8")
    assert "Candidate-level FPR" in report
    assert "PR-level warn/block rate" in report
    assert "a" * 40 in report and "b" * 40 in report
    assert "fixture\\-never\\-published\\-eval\\-name" in report
