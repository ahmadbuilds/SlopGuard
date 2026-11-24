"""Download and normalize the manually refreshed popular-package snapshot."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from packaging.utils import InvalidName, canonicalize_name

DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/top-pypi-packages.min.json"
)
DEFAULT_LIMIT = 5000
GENERATOR_VERSION = "1"
REQUEST_TIMEOUT_SECONDS = 20


class SnapshotUpdateError(ValueError):
    """Raised when the source data cannot be converted into a package snapshot."""


def transform_popular_packages(
    payload: bytes | str,
    *,
    source_url: str,
    retrieved_at: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Convert recorded top-package JSON into SlopGuard's ordered snapshot format."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise SnapshotUpdateError("The snapshot limit must be a positive integer.")
    if not isinstance(source_url, str) or not source_url.strip():
        raise SnapshotUpdateError("The source URL must be a non-empty string.")
    if retrieved_at is None:
        retrieved_at = datetime.now(UTC)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise SnapshotUpdateError("The retrieval timestamp must be timezone-aware.")

    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise SnapshotUpdateError("The popular-package source is not valid JSON.") from error

    if not isinstance(document, dict):
        raise SnapshotUpdateError("The popular-package source must be a JSON object.")
    source_last_update = document.get("last_update")
    rows = document.get("rows")
    if not isinstance(source_last_update, str) or not source_last_update.strip():
        raise SnapshotUpdateError("The popular-package source must contain a last_update string.")
    if not isinstance(rows, list):
        raise SnapshotUpdateError("The popular-package source must contain a rows array.")

    unique_names: list[str] = []
    seen_names: set[str] = set()
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise SnapshotUpdateError(f"Source row {position} must be an object.")
        project = row.get("project")
        download_count = row.get("download_count")
        if not isinstance(project, str) or not project.strip():
            raise SnapshotUpdateError(f"Source row {position} must contain a project name.")
        if (
            isinstance(download_count, bool)
            or not isinstance(download_count, (int, float))
            or download_count < 0
        ):
            raise SnapshotUpdateError(
                f"Source row {position} must contain a non-negative download_count."
            )
        try:
            normalized = canonicalize_name(project, validate=True)
        except InvalidName as error:
            raise SnapshotUpdateError(
                f"Source row {position} has an invalid project name."
            ) from error
        if normalized not in seen_names:
            seen_names.add(normalized)
            unique_names.append(normalized)
            if len(unique_names) == limit:
                break

    packages = [{"name": name, "rank": rank} for rank, name in enumerate(unique_names, start=1)]
    return {
        "source_url": source_url,
        "source_last_update": source_last_update,
        "retrieved_at": retrieved_at.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "generator_version": GENERATOR_VERSION,
        "licence": "not stated by source",
        "count": len(packages),
        "packages": packages,
    }


def _download_source(source_url: str) -> bytes:
    """Fetch source JSON with a finite timeout."""

    if not source_url.startswith("https://"):
        raise SnapshotUpdateError("The source URL must use HTTPS.")
    try:
        with urlopen(source_url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read()
    except (OSError, URLError, TimeoutError) as error:
        raise SnapshotUpdateError(f"Could not fetch popular-package source: {error}") from error


def _write_snapshot(output_path: Path, snapshot: dict[str, Any]) -> None:
    """Write a JSON snapshot atomically, creating its parent directory if needed."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent, delete=False, newline="\n"
        ) as output_file:
            temporary_path = output_file.name
            json.dump(snapshot, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def main(argv: list[str] | None = None) -> int:
    """Fetch the source and write the package snapshot."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    default_output = (
        Path(__file__).resolve().parents[1] / "src" / "slopguard" / "data" / "popular_packages.json"
    )
    parser.add_argument("--output", type=Path, default=default_output)
    arguments = parser.parse_args(argv)

    try:
        snapshot = transform_popular_packages(
            _download_source(arguments.source_url),
            source_url=arguments.source_url,
            limit=arguments.limit,
        )
        _write_snapshot(arguments.output, snapshot)
    except (OSError, SnapshotUpdateError) as error:
        parser.error(str(error))
    print(f"Wrote {snapshot['count']} package names to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
