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


def test_registry_metadata_and_diagnostics_match_contract() -> None:
    result = {
        **SAMPLE,
        "registry_exists": True,
        "registry_status": "exists",
        "registry_metadata": {
            "first_release_at": "2024-01-01T00:00:00Z",
            "release_count": 2,
            "latest_release_at": "2024-03-01T00:00:00Z",
            "project_urls": {"Source": "https://example.com/source"},
            "maintainer_hints": ["maintainer-name"],
            "source_hints": ["https://example.com/source"],
        },
        "registry_diagnostics": {
            "latency_ms": 25.5,
            "attempts": 1,
            "http_status": 200,
            "retry_after": None,
            "rate_limit_remaining": None,
            "error": None,
        },
    }

    VALIDATOR.validate(result)


@pytest.mark.parametrize("status", ["exists", "not_found", "transient_error", "malformed_response"])
def test_all_registry_lookup_states_are_allowed(status: str) -> None:
    result = {**SAMPLE, "registry_status": status}

    VALIDATOR.validate(result)


def test_missing_package_example_only_cites_candidate_evidence() -> None:
    assert SAMPLE["registry_exists"] is False
    assert all(evidence["kind"] != "registry_metadata" for evidence in SAMPLE["metadata_evidence"])


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
