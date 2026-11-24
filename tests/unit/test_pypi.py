"""Tests for pure PyPI JSON metadata parsing."""

import json
from pathlib import Path

import pytest

from slopguard.pypi import PyPIMetadataError, parse_project_response

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pypi"


def test_parse_project_metadata_and_release_history() -> None:
    payload = (FIXTURES / "sampleproject.json").read_bytes()

    metadata = parse_project_response(payload)

    assert metadata == {
        "first_release_at": "2015-06-14T14:37:56.383366Z",
        "release_count": 2,
        "latest_release_at": "2024-11-06T22:37:10.868088Z",
        "project_urls": {
            "Homepage": "https://github.com/pypa/sampleproject",
            "Source": "https://github.com/pypa/sampleproject/",
        },
        "maintainer_hints": [
            "A. Great Maintainer",
            "Owner: theacodes",
            "Maintainer: pypa-bot",
        ],
        "source_hints": [
            "https://github.com/pypa/sampleproject",
            "https://github.com/pypa/sampleproject/",
        ],
    }


def test_missing_deprecated_releases_field_keeps_project_metadata() -> None:
    metadata = parse_project_response('{"info":{"name":"current-project"}}')

    assert metadata["release_count"] is None
    assert metadata["first_release_at"] is None
    assert metadata["project_urls"] == {}


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"{}",
        b"[]",
        b'{"info":[]}',
        b'{"info":{}}',
        b'{"info":{"name":"example"},"releases":null}',
        b'{"info":{"name":"example"},"releases":{"1.0":[null]}}',
    ],
)
def test_malformed_project_payload_raises_typed_error(payload: bytes) -> None:
    with pytest.raises(PyPIMetadataError):
        parse_project_response(payload)


def test_response_for_different_project_name_is_rejected() -> None:
    payload = '{"info":{"name":"another-project"}}'

    with pytest.raises(PyPIMetadataError, match="does not match"):
        parse_project_response(payload, expected_name="sampleproject")


def test_non_web_project_url_is_not_emitted_as_a_source_hint() -> None:
    payload = json.dumps(
        {"info": {"name": "example", "project_urls": {"Source": "javascript:alert(1)"}}}
    )

    metadata = parse_project_response(payload)

    assert metadata["project_urls"] == {}
    assert metadata["source_hints"] == []


def test_malformed_project_url_does_not_crash_response_parsing() -> None:
    payload = json.dumps({"info": {"name": "example", "project_urls": {"Source": "https://[bad"}}})

    metadata = parse_project_response(payload)

    assert metadata["project_urls"] == {}
    assert metadata["source_hints"] == []


@pytest.mark.parametrize(
    "payload",
    [
        '{"info": {"name": "x"}, "n": ' + "1" * 5000 + "}",
        "[" * 100_000 + "]" * 100_000,
    ],
    ids=["oversized-integer", "deep-nesting"],
)
def test_pathological_json_is_reported_as_malformed(payload: str) -> None:
    with pytest.raises(PyPIMetadataError):
        parse_project_response(payload)
