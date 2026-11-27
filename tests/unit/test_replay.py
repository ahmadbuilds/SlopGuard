"""Offline registry recording replay tests."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.record import RecordingClient, write_recording
from evals.replay import ReplayClient
from slopguard.config import RegistrySettings
from slopguard.registry import RegistryLookupResult


def _result(name: str) -> RegistryLookupResult:
    return RegistryLookupResult(
        normalized_name=name,
        status="not_found",
        metadata=None,
        latency_ms=1.0,
        attempts=1,
        http_status=404,
        retry_after=None,
        rate_limit_remaining=None,
        error=None,
    )


def _recording(path: Path, *results: RegistryLookupResult) -> None:
    header = {
        "recorded_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "tool_version": "test",
        "count": len(results),
    }
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(header) + "\n")
        for result in results:
            output.write(json.dumps(result.to_dict()) + "\n")


def test_replay_returns_recorded_lookup_and_reuses_client(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl.gz"
    expected = _result("fixture-package")
    _recording(path, expected)
    client = ReplayClient(path)

    assert client.lookup("Fixture_Package") == expected
    assert client.with_settings(RegistrySettings()) is client


def test_replay_unknown_name_has_clear_key_error(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl.gz"
    _recording(path, _result("fixture-package"))

    with pytest.raises(KeyError, match="No registry recording exists for 'other-package'"):
        ReplayClient(path).lookup("other-package")


def test_replay_rejects_header_count_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl.gz"
    _recording(path)
    with pytest.raises(ValueError, match="count does not match"):
        raw = gzip.decompress(path.read_bytes()).replace(b'"count": 0', b'"count": 1')
        path.write_bytes(gzip.compress(raw))
        ReplayClient(path)


def test_recording_writer_produces_replayable_result(tmp_path: Path) -> None:
    path = tmp_path / "recording.jsonl.gz"
    expected = _result("fixture-package")
    recorded_at = datetime(2026, 1, 1, tzinfo=UTC)

    write_recording((expected,), path, recorded_at=recorded_at)

    assert ReplayClient(path).lookup("fixture-package") == expected


def test_recording_client_retains_results_and_settings_wrapper() -> None:
    class Client:
        def lookup(self, name: str) -> RegistryLookupResult:
            return _result(name)

        def with_settings(self, settings: RegistrySettings):
            return self

    client = RecordingClient(Client())
    wrapped = client.with_settings(RegistrySettings())
    result = wrapped.lookup("fixture-package")

    assert result.normalized_name == "fixture-package"
    assert client.results["fixture-package"] == result
