"""Checks for the packaged result schema and checked-in contract fixtures."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from slopguard.contracts import load_result_schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contract"
SCHEMA = load_result_schema()


def test_packaged_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_contract_fixtures_validate_against_packaged_schema() -> None:
    validator = Draft202012Validator(SCHEMA)
    fixtures = sorted(FIXTURES.glob("*.json"))

    assert fixtures, "expected at least one JSON contract fixture"
    for fixture in fixtures:
        validator.validate(json.loads(fixture.read_text(encoding="utf-8")))
