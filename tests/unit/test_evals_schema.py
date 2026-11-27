"""Validation and loading tests for evaluation label rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.schema import Label, load_labels, validate_labels

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/evals/labels.jsonl"


def _label(**changes: object) -> Label:
    values: dict[str, object] = {
        "name": "fixture-package",
        "label": "legitimate",
        "expected": "pass",
        "category": "fixture",
        "rationale": "Synthetic test label.",
        "source": "test fixture",
        "added_by": "test",
        "reviewed_by": None,
    }
    values.update(changes)
    return Label.from_dict(values)


def test_jsonl_loader_reads_placeholder_rows() -> None:
    labels = load_labels(FIXTURE)

    assert len(labels) == 5
    assert any(label.label == "typosquat_synthetic" for label in labels)
    assert labels[-1].expected == "exclude"


def test_models_field_round_trips_and_is_omitted_when_empty() -> None:
    plain = _label()
    assert plain.models == ()
    assert "models" not in plain.to_dict()

    row = _label().to_dict()
    row["models"] = ["model-z", "model-a"]
    labeled = Label.from_dict(row)

    assert labeled.models == ("model-a", "model-z")
    assert labeled.to_dict()["models"] == ["model-a", "model-z"]
    assert Label.from_dict(labeled.to_dict()) == labeled


@pytest.mark.parametrize("models", ["model-a", ["model-a", ""]])
def test_loader_rejects_invalid_models_with_file_and_row(tmp_path: Path, models: object) -> None:
    path = tmp_path / "labels.jsonl"
    row = _label().to_dict()
    row["models"] = models
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"labels.jsonl:1: models must be a list"):
        load_labels(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("label", "unknown", "label must be one of"),
        ("expected", "unknown", "expected must be one of"),
        ("rationale", "  ", "rationale must be a non-empty string"),
        ("source", "", "source must be a non-empty string"),
        ("extra", "unexpected", "missing or unknown fields"),
        ("expected", "flag", "borderline labels must"),
    ],
)
def test_label_rows_reject_invalid_values(field: str, value: object, message: str) -> None:
    base = _label().to_dict()
    if field == "expected" and value == "flag":
        base["label"] = "borderline"
    base[field] = value

    with pytest.raises(ValueError, match=message):
        Label.from_dict(base)


def test_labels_reject_duplicate_canonical_names() -> None:
    first = _label(name="fixture_package")
    second = _label(name="fixture-package")

    with pytest.raises(ValueError, match="duplicate canonical package name"):
        validate_labels((first, second))


def test_loader_reports_invalid_jsonl_row(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"name": "incomplete"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"labels.jsonl:1"):
        load_labels(path)
