"""Offline source verification tests using temporary data only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.build_synthetic import build_synthetic_labels
from evals.record import write_recording
from evals.schema import Label
from evals.verify_labels import main as verify_main
from evals.verify_labels import verify_labels
from slopguard.registry import RegistryLookupResult
from slopguard.similarity import PopularPackageIndex

ROOT = Path(__file__).resolve().parents[2]
OSV_FIXTURE = ROOT / "tests/fixtures/evals/osv"


def _lookup(name: str, status: str = "not_found") -> RegistryLookupResult:
    return RegistryLookupResult(
        normalized_name=name,
        status=status,  # type: ignore[arg-type]
        metadata=None,
        latency_ms=1.0,
        attempts=1,
        http_status=404 if status == "not_found" else 200,
        retry_after=None,
        rate_limit_remaining=None,
        error=None,
    )


def _row(
    name: str, label: str, source: str, *, category: str | None = None, models: tuple[str, ...] = ()
) -> Label:
    return Label(
        name=name,
        label=label,  # type: ignore[arg-type]
        expected="flag" if label != "legitimate" else "pass",
        category=category or label,
        rationale="fixture label rationale",
        source=source,
        added_by="fixture",
        reviewed_by=None,
        models=models,
    )


def _write_osv(root: Path, *, ecosystem: str = "PyPI", name: str = "fixture-malicious") -> None:
    advisory = root / "osv" / "malicious" / "pypi" / "MAL-1.json"
    advisory.parent.mkdir(parents=True, exist_ok=True)
    advisory.write_text(
        json.dumps(
            {
                "id": "MAL-1",
                "affected": [{"package": {"ecosystem": ecosystem, "name": name}}],
            }
        ),
        encoding="utf-8",
    )


def _inputs(
    tmp_path: Path, labels: tuple[Label, ...], *, osv_name: str = "fixture-malicious"
) -> tuple[Path, Path, Path, Path, Path]:
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(
        "".join(json.dumps(row.to_dict(), sort_keys=True) + "\n" for row in labels),
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.jsonl.gz"
    names = {row.name: ("exists" if row.label == "legitimate" else "not_found") for row in labels}
    write_recording(
        (_lookup(name, status) for name, status in names.items()),
        registry_path,
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    osv_dir = tmp_path / "osv"
    _write_osv(osv_dir, name=osv_name)
    popular_file = tmp_path / "popular.json"
    popular_file.write_text(
        json.dumps({"packages": [{"name": "fixture-legitimate", "rank": 1}]}),
        encoding="utf-8",
    )
    popular_sha256 = hashlib.sha256(popular_file.read_bytes()).hexdigest()
    verified_sources = [
        replace(
            row,
            source=row.source.replace(
                "popular_file_sha256=fixture", f"popular_file_sha256={popular_sha256}"
            ),
        )
        for row in labels
    ]
    labels_path.write_text(
        "".join(json.dumps(row.to_dict(), sort_keys=True) + "\n" for row in verified_sources),
        encoding="utf-8",
    )
    outputs_path = tmp_path / "outputs.jsonl"
    outputs_path.write_text(
        json.dumps(
            {
                "model": "fixture-model",
                "date": "2026-01-01",
                "prompt": "fixture prompt",
                "packages": ["fixture-hallucination"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return labels_path, registry_path, osv_dir, popular_file, outputs_path


def _valid_rows() -> tuple[Label, ...]:
    synthetic = build_synthetic_labels(PopularPackageIndex.load(), top_k=10, seed=11608)[0]
    return (
        _row("fixture-malicious", "malicious", "OSV MAL-1; sample_count=1; seed=2"),
        synthetic,
        _row(
            "fixture-legitimate",
            "legitimate",
            "popular_file_sha256=fixture; rank_range=1-1; rank=1; seed=2; count=1",
            category="lookalike-legitimate",
        ),
        _row(
            "fixture-hallucination",
            "hallucinated",
            "LLM model=fixture-model; date=2026-01-01; prompt=fixture prompt; models_count=1",
            models=("fixture-model",),
        ),
    )


def test_verifier_passes_each_class_and_writes_atomically(tmp_path: Path) -> None:
    labels = _valid_rows()
    inputs = _inputs(tmp_path, labels)
    out = tmp_path / "nested" / "verified.jsonl"

    updated, summary, failures = verify_labels(*inputs[:2], inputs[2], inputs[3], inputs[4], out)

    assert failures == ()
    assert {row.reviewed_by for row in updated} == {"automated:evals.verify_labels"}
    assert {row["checked"] for row in summary.values()} == {1}
    assert {row["passed"] for row in summary.values()} == {1}
    assert out.exists()
    assert list(out.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    ("bad_label", "osv_name", "reason"),
    [
        ("malicious", "fixture-other", "was not found"),
        ("synthetic", "fixture-malicious", "did not reproduce"),
        ("legitimate", "fixture-legitimate", "appears in the OSV"),
        ("hallucinated", "fixture-malicious", "model/date/prompt"),
    ],
)
def test_failed_checks_exclude_but_preserve_each_label(
    tmp_path: Path, bad_label: str, osv_name: str, reason: str
) -> None:
    rows = list(_valid_rows())
    row_label = {
        "malicious": "malicious",
        "synthetic": "typosquat_synthetic",
        "legitimate": "legitimate",
        "hallucinated": "hallucinated",
    }[bad_label]
    target = next(index for index, row in enumerate(rows) if row.label == row_label)
    if bad_label == "synthetic":
        rows[target] = _row(
            "fixture-not-generated-typo", "typosquat_synthetic", "top_k=10; seed=11608"
        )
    elif bad_label == "hallucinated":
        rows[target] = _row(
            "fixture-hallucination",
            "hallucinated",
            "LLM model=fixture-model; date=2026-01-01; prompt=wrong prompt",
            models=("fixture-model",),
        )
    inputs = _inputs(tmp_path, tuple(rows), osv_name=osv_name)
    if bad_label == "malicious":
        rows[target] = _row("fixture-malicious", "malicious", "OSV MAL-404; seed=1")
        labels_path = inputs[0]
        labels_path.write_text(
            "".join(json.dumps(row.to_dict(), sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    out = tmp_path / "verified.jsonl"

    updated, summary, failures = verify_labels(*inputs[:2], inputs[2], inputs[3], inputs[4], out)

    failed = next(row for row in updated if row.name == rows[target].name)
    assert len(updated) == len(rows)
    assert failed.label == "borderline" and failed.reviewed_by == "automated:evals.verify_labels"
    assert row_label in failed.category
    assert reason in failed.rationale
    assert summary[row_label]["failed"] >= 1
    assert failures


def test_malicious_verifier_rejects_wrong_ecosystem(tmp_path: Path) -> None:
    labels = (_row("fixture-malicious", "malicious", "OSV MAL-1"),)
    inputs = _inputs(tmp_path, labels)
    _write_osv(inputs[2], ecosystem="npm")
    _updated, summary, failures = verify_labels(
        *inputs[:2], inputs[2], inputs[3], inputs[4], tmp_path / "out.jsonl"
    )
    assert summary["malicious"]["failed"] == 1
    assert "no affected PyPI package" in failures[0]


def test_missing_osv_directory_cli_exits_one(tmp_path: Path, capsys) -> None:
    labels_path, registry_path, _osv_dir, popular, outputs = _inputs(tmp_path, _valid_rows())
    missing = tmp_path / "missing-osv"

    assert (
        verify_main(
            [
                "--labels",
                str(labels_path),
                "--registry",
                str(registry_path),
                "--osv-dir",
                str(missing),
                "--popular-file",
                str(popular),
                "--outputs",
                str(outputs),
                "--out",
                str(tmp_path / "out.jsonl"),
            ]
        )
        == 1
    )
    assert "missing OSV advisory directory" in capsys.readouterr().err
