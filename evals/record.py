"""Manually record PyPI registry evidence for a validated label set."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import tempfile
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packaging.utils import canonicalize_name

from slopguard import __version__
from slopguard.config import RegistrySettings
from slopguard.registry import PyPIClient, RegistryClient, RegistryLookupResult

from .constants import (
    DEFAULT_LABELS_PATH,
    DEFAULT_RECORD_CONCURRENCY,
    DEFAULT_REGISTRY_PATH,
    MAX_RECORD_CONCURRENCY,
    TEMPORARY_FILE_SUFFIX,
)
from .schema import Label, load_labels


def record_lookups(
    labels: tuple[Label, ...],
    output_path: Path,
    *,
    concurrency: int = DEFAULT_RECORD_CONCURRENCY,
    recorded_at: datetime | None = None,
    client: PyPIClient | None = None,
) -> None:
    """Look up every unique label name and atomically write one gzip recording."""

    if not 1 <= concurrency <= MAX_RECORD_CONCURRENCY:
        raise ValueError(f"concurrency must be between one and {MAX_RECORD_CONCURRENCY}")
    timestamp = recorded_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("recorded_at must be a timezone-aware UTC datetime")
    registry = client if client is not None else PyPIClient()
    names_by_canonical = {
        canonicalize_name(label.name, validate=True): label.name for label in labels
    }
    names = tuple(names_by_canonical[name] for name in sorted(names_by_canonical))
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = tuple(executor.map(registry.lookup, names))
    for name, result in zip(names, results, strict=True):
        if canonicalize_name(result.normalized_name) != canonicalize_name(name):
            raise ValueError(f"registry client returned a result for the wrong name: {name!r}")
    write_recording(results, output_path, recorded_at=timestamp)


def write_recording(
    results: Iterable[RegistryLookupResult], output_path: Path, *, recorded_at: datetime
) -> None:
    """Atomically write the standardized gzip recording header and lookup rows."""

    if recorded_at.tzinfo is None or recorded_at.utcoffset() != timedelta(0):
        raise ValueError("recorded_at must be a timezone-aware UTC datetime")
    sorted_results = sorted(
        results,
        key=lambda result: canonicalize_name(result.normalized_name, validate=True),
    )
    canonical_names = [
        canonicalize_name(result.normalized_name, validate=True) for result in sorted_results
    ]
    if len(set(canonical_names)) != len(canonical_names):
        raise ValueError("recording results must have unique canonical names")
    header = {
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "tool_version": __version__,
        "count": len(sorted_results),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=TEMPORARY_FILE_SUFFIX,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with temporary_path.open("wb") as raw_output:
            with gzip.GzipFile(fileobj=raw_output, mode="wb", mtime=0) as compressed:
                compressed.write(_json_line(header))
                for result in sorted_results:
                    compressed.write(_json_line(result.to_dict()))
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class RecordingClient:
    """Wrap a live client and retain every response for offline replay."""

    def __init__(
        self,
        client: RegistryClient,
        *,
        _results: dict[str, RegistryLookupResult] | None = None,
        _lock: threading.Lock | None = None,
    ) -> None:
        self._client = client
        self.results = _results if _results is not None else {}
        self._lock = _lock if _lock is not None else threading.Lock()

    def lookup(self, name: str) -> RegistryLookupResult:
        """Look up a name and retain its first normalized registry response."""

        result = self._client.lookup(name)
        canonical = canonicalize_name(result.normalized_name, validate=True)
        with self._lock:
            self.results.setdefault(canonical, result)
        return result

    def with_settings(self, settings: RegistrySettings) -> RecordingClient:
        """Apply live lookup bounds while keeping the shared recording collection."""

        return RecordingClient(
            self._client.with_settings(settings), _results=self.results, _lock=self._lock
        )


def _json_line(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_RECORD_CONCURRENCY)
    args = parser.parse_args(argv)
    record_lookups(load_labels(args.labels), args.registry, concurrency=args.concurrency)
    print(f"Recorded registry results for {args.labels} to {args.registry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
