"""Regression checks for the published dependency-result contract."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/dependency-result.schema.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((ROOT / "examples/result.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def test_example_matches_contract_schema() -> None:
    VALIDATOR.validate(SAMPLE)


def test_unknown_registry_state_is_allowed() -> None:
    result = {**SAMPLE, "registry_exists": None}

    VALIDATOR.validate(result)


@pytest.mark.parametrize(
    "change",
    [
        {"normalized_name": None},
        {"risk_score": 101},
        {"severity": "urgent"},
        {"remediation": ""},
    ],
)
def test_invalid_result_is_rejected(change: dict[str, object]) -> None:
    result = {**SAMPLE, **change}

    assert list(VALIDATOR.iter_errors(result))
