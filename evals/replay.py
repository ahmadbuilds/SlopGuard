"""Replay immutable registry recordings without network access."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

from packaging.utils import canonicalize_name

from slopguard.config import RegistrySettings
from slopguard.registry import RegistryLookupResult

from .constants import MAX_ARCHIVE_BYTES, MAX_INPUT_LINE_BYTES, MAX_REGISTRY_RECORD_BYTES


class ReplayClient:
    """A registry client backed only by a validated compressed recording."""

    def __init__(self, path: Path) -> None:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError(f"registry recording exceeds {MAX_ARCHIVE_BYTES} compressed bytes")
        self.path = path
        self.header, self._results = _load_recording(path)

    def lookup(self, name: str) -> RegistryLookupResult:
        """Return a recorded result or fail clearly when the name was not recorded."""

        canonical = canonicalize_name(name, validate=True)
        try:
            return self._results[canonical]
        except KeyError as error:
            raise KeyError(f"No registry recording exists for {canonical!r}.") from error

    def with_settings(self, settings: RegistrySettings) -> ReplayClient:
        """Keep the same immutable recording when scan-specific settings are applied."""

        if not isinstance(settings, RegistrySettings):
            raise TypeError("settings must be RegistrySettings")
        return self


def _load_recording(path: Path) -> tuple[dict[str, object], dict[str, RegistryLookupResult]]:
    header: dict[str, object] | None = None
    results: dict[str, RegistryLookupResult] = {}
    decompressed_size = 0
    try:
        with gzip.open(path, "rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                decompressed_size += len(raw_line)
                if decompressed_size > MAX_REGISTRY_RECORD_BYTES:
                    raise ValueError("registry recording exceeds the safe decompressed size")
                if len(raw_line) > MAX_INPUT_LINE_BYTES:
                    raise ValueError(f"registry recording line {line_number} is too large")
                try:
                    row = json.loads(raw_line)
                except (json.JSONDecodeError, RecursionError) as error:
                    raise ValueError(
                        f"registry recording line {line_number} is invalid JSON"
                    ) from error
                if not isinstance(row, dict):
                    raise ValueError(f"registry recording line {line_number} must be an object")
                if header is None:
                    _validate_header(row)
                    header = row
                    continue
                lookup = RegistryLookupResult.from_dict(row)
                name = canonicalize_name(lookup.normalized_name, validate=True)
                if name in results:
                    raise ValueError(f"duplicate registry recording for {name!r}")
                results[name] = lookup
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read registry recording {path}") from error
    if header is None:
        raise ValueError("registry recording is empty")
    if header["count"] != len(results):
        raise ValueError("registry recording count does not match its rows")
    return header, results


def _validate_header(header: dict[str, object]) -> None:
    if set(header) != {"recorded_at", "tool_version", "count"}:
        raise ValueError("registry recording header has missing or unknown fields")
    recorded_at = header.get("recorded_at")
    tool_version = header.get("tool_version")
    count = header.get("count")
    if not isinstance(recorded_at, str) or not isinstance(tool_version, str):
        raise ValueError("registry recording header timestamps and version must be strings")
    try:
        timestamp = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("registry recording recorded_at is not ISO 8601") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("registry recording recorded_at must be UTC")
    if not tool_version.strip():
        raise ValueError("registry recording tool_version must be non-empty")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("registry recording count must be a non-negative integer")
