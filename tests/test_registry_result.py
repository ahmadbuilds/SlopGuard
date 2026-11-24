"""Tests for applying registry results to SG-01 dependency candidates."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from slopguard.extractor import ChangedFile, extract_dependencies
from slopguard.pypi import parse_project_response
from slopguard.registry import HTTPResponse, PyPIClient
from slopguard.registry_result import apply_registry_lookup

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/pypi/sampleproject.json").read_bytes()
VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas/dependency-result.schema.json").read_text(encoding="utf-8"))
)


def _candidate():
    result = extract_dependencies([ChangedFile("requirements.txt", "", "sampleproject==4.0.0\n")])
    return result.candidates[0]


def _lookup(response: HTTPResponse):
    return PyPIClient(
        transport=lambda _url, _timeout, _max_bytes: response,
        clock=iter((1.0, 1.02)).__next__,
    ).lookup("sampleproject")


def test_existing_project_enriches_candidate_with_evidence_and_metadata() -> None:
    candidate = _candidate()
    original = deepcopy(candidate)
    enriched = apply_registry_lookup(_candidate(), _lookup(HTTPResponse(200, FIXTURE, {})))

    VALIDATOR.validate(enriched)
    assert candidate == original
    assert enriched["registry_exists"] is True
    assert enriched["registry_status"] == "exists"
    assert enriched["registry_metadata"]["release_count"] == 2
    assert enriched["registry_diagnostics"]["latency_ms"] == 20.0
    assert {item["kind"] for item in enriched["metadata_evidence"]} >= {
        "registry_metadata",
        "release_history",
        "maintainer",
    }
    assert enriched["source_locations"] == [
        {"file": "requirements.txt", "line": 1, "source_type": "requirements"}
    ]


def test_not_found_adds_registry_fact_without_changing_risk_score() -> None:
    candidate = _candidate()
    enriched = apply_registry_lookup(candidate, _lookup(HTTPResponse(404, b"", {})))

    VALIDATOR.validate(enriched)
    assert enriched["registry_exists"] is False
    assert enriched["registry_status"] == "not_found"
    assert enriched["risk_score"] == candidate["risk_score"]
    assert "404" in enriched["metadata_evidence"][-1]["summary"]


def test_registry_failures_remain_unknown_and_do_not_add_risk() -> None:
    candidate = _candidate()
    enriched = apply_registry_lookup(candidate, _lookup(HTTPResponse(503, b"", {})))

    VALIDATOR.validate(enriched)
    assert enriched["registry_exists"] is None
    assert enriched["registry_status"] == "transient_error"
    assert enriched["metadata_evidence"] == candidate["metadata_evidence"]
    assert enriched["risk_score"] == candidate["risk_score"]
    assert enriched["registry_diagnostics"]["error"]


def test_retrying_after_prior_evidence_removes_stale_registry_facts() -> None:
    candidate = apply_registry_lookup(_candidate(), _lookup(HTTPResponse(200, FIXTURE, {})))

    enriched = apply_registry_lookup(candidate, _lookup(HTTPResponse(503, b"", {})))

    api_url = "https://pypi.org/pypi/sampleproject/json"
    assert enriched["registry_exists"] is None
    assert all(item["source"] != api_url for item in enriched["metadata_evidence"])


def test_lookup_for_different_candidate_is_rejected() -> None:
    candidate = _candidate()
    original = deepcopy(candidate)
    lookup = PyPIClient(
        transport=lambda _url, _timeout, _max_bytes: HTTPResponse(404, b"", {})
    ).lookup("another-package")

    with pytest.raises(ValueError, match="does not match"):
        apply_registry_lookup(candidate, lookup)

    assert candidate == original


def test_fixture_sample_registry_result_matches_contract() -> None:
    sample = json.loads((ROOT / "examples/registry-result.json").read_text(encoding="utf-8"))

    VALIDATOR.validate(sample)


def test_lookup_parser_metadata_matches_fixture_sample() -> None:
    expected = json.loads((ROOT / "examples/registry-result.json").read_text(encoding="utf-8"))

    assert parse_project_response(FIXTURE) == expected["registry_metadata"]
