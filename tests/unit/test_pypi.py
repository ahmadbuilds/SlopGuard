"""Tests for pure PyPI JSON metadata parsing."""

import json
from pathlib import Path

import pytest

from slopguard.pypi import (
    MAX_MAINTAINER_HINT_LENGTH,
    MAX_MAINTAINER_HINTS,
    MAX_PROJECT_URLS,
    MAX_SOURCE_HINTS,
    PyPIMetadataError,
    parse_project_response,
)

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


def test_maintainer_hints_are_length_and_count_bounded() -> None:
    payload = json.dumps(
        {
            "info": {"name": "example", "maintainer": "A" * 10_000},
            "ownership": {
                "roles": [{"role": "owner", "user": f"user-{index}"} for index in range(50)]
            },
        }
    )

    metadata = parse_project_response(payload)

    assert len(metadata["maintainer_hints"]) == MAX_MAINTAINER_HINTS
    assert metadata["maintainer_hints"][0] == "A" * MAX_MAINTAINER_HINT_LENGTH
    assert all(len(hint) <= MAX_MAINTAINER_HINT_LENGTH for hint in metadata["maintainer_hints"])


def test_project_url_count_is_bounded() -> None:
    project_urls = {
        f"Source {index}": f"https://github.com/example/project-{index}"
        for index in range(MAX_PROJECT_URLS + 10)
    }

    metadata = parse_project_response(
        json.dumps({"info": {"name": "example", "project_urls": project_urls}})
    )

    assert len(metadata["project_urls"]) == MAX_PROJECT_URLS
    assert len(metadata["source_hints"]) == MAX_SOURCE_HINTS


def test_control_characters_are_removed_from_project_url_labels() -> None:
    metadata = parse_project_response(
        json.dumps(
            {
                "info": {
                    "name": "example",
                    "project_urls": {"So\x00urce": "https://example.com/source"},
                }
            }
        )
    )

    assert metadata["project_urls"] == {"Source": "https://example.com/source"}
    assert metadata["source_hints"] == ["https://example.com/source"]


def test_invisible_unicode_is_removed_from_hints_and_project_urls() -> None:
    metadata = parse_project_response(
        json.dumps(
            {
                "info": {
                    "name": "example",
                    "maintainer": "evil\u202egnp.exe\u200b\u2066",
                    "project_urls": {
                        "So\u202eurce\u200b\u2066": "https://example.com/so\u200burce\u2066"
                    },
                },
                "ownership": {"roles": [{"role": "ow\u202ener", "user": "al\u200bice\u2066"}]},
            }
        )
    )

    assert metadata["maintainer_hints"] == ["evilgnp.exe", "Owner: alice"]
    assert metadata["project_urls"] == {"Source": "https://example.com/source"}
    assert metadata["source_hints"] == ["https://example.com/source"]


def test_overlong_project_url_is_dropped_without_truncation() -> None:
    long_url = "https://example.com/" + "x" * 480
    metadata = parse_project_response(
        json.dumps({"info": {"name": "example", "project_urls": {"Homepage": long_url}}})
    )

    assert len(long_url) == 500
    assert metadata["project_urls"] == {}


def test_html_in_maintainer_hint_is_not_escaped() -> None:
    html_hint = "<b>Alice</b>"
    metadata = parse_project_response(
        json.dumps({"info": {"name": "example", "maintainer": html_hint}})
    )

    assert metadata["maintainer_hints"] == [html_hint]


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
