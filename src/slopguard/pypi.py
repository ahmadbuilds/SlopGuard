"""Pure parsing helpers for PyPI's project JSON response."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name

from .contracts import RegistryMetadata


class PyPIMetadataError(ValueError):
    """Raised when a successful HTTP response is not valid project JSON."""


def parse_project_response(
    payload: bytes | str, *, expected_name: str | None = None
) -> RegistryMetadata:
    """Extract scorer metadata from a PyPI project JSON response without I/O."""

    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise PyPIMetadataError("PyPI returned invalid JSON.") from error

    if not isinstance(document, dict) or not isinstance(document.get("info"), dict):
        raise PyPIMetadataError("PyPI JSON response must contain an info object.")

    info = document["info"]
    project_name = info.get("name")
    if not isinstance(project_name, str) or not project_name.strip():
        raise PyPIMetadataError("PyPI JSON info object must contain a project name.")
    if expected_name is not None and canonicalize_name(project_name) != canonicalize_name(
        expected_name
    ):
        raise PyPIMetadataError("PyPI response project name does not match the requested name.")

    releases = document.get("releases")
    if "releases" in document and not isinstance(releases, dict):
        raise PyPIMetadataError("PyPI JSON releases field must be an object.")

    release_count: int | None = None
    timestamps: list[datetime] = []
    if isinstance(releases, dict):
        release_count = 0
        for files in releases.values():
            if not isinstance(files, list):
                raise PyPIMetadataError("PyPI JSON release entries must be arrays.")
            if files:
                release_count += 1
            for file_entry in files:
                if not isinstance(file_entry, dict):
                    raise PyPIMetadataError("PyPI JSON release file entries must be objects.")
                timestamp = _parse_upload_time(
                    file_entry.get("upload_time_iso_8601") or file_entry.get("upload_time")
                )
                if timestamp is not None:
                    timestamps.append(timestamp)

    project_urls = _project_urls(info.get("project_urls"))
    maintainers = _maintainer_hints(info, document.get("ownership"))
    source_hints = _source_hints(project_urls)

    return {
        "first_release_at": _format_timestamp(min(timestamps)) if timestamps else None,
        "release_count": release_count,
        "latest_release_at": _format_timestamp(max(timestamps)) if timestamps else None,
        "project_urls": project_urls,
        "maintainer_hints": maintainers,
        "source_hints": source_hints,
    }


def _parse_upload_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _project_urls(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    urls: dict[str, str] = {}
    for label, url in value.items():
        if isinstance(label, str) and isinstance(url, str) and _is_web_url(url):
            urls[label.strip()] = url.strip()
    return urls


def _is_web_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _maintainer_hints(info: dict[str, Any], ownership: Any) -> list[str]:
    hints: list[str] = []
    maintainer = info.get("maintainer")
    if isinstance(maintainer, str) and maintainer.strip():
        hints.append(maintainer.strip())

    if isinstance(ownership, dict):
        roles = ownership.get("roles")
        if isinstance(roles, list):
            for entry in roles:
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role")
                username = entry.get("user")
                if isinstance(role, str) and isinstance(username, str) and username.strip():
                    if role.lower() in {"owner", "maintainer"}:
                        hints.append(f"{role.title()}: {username.strip()}")
    return list(dict.fromkeys(hints))


def _source_hints(project_urls: dict[str, str]) -> list[str]:
    source_labels = {"source", "repository", "code", "source code"}
    hints: list[str] = []
    for label, url in project_urls.items():
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if label.strip().lower() in source_labels or host in {
            "github.com",
            "gitlab.com",
            "bitbucket.org",
            "codeberg.org",
        }:
            hints.append(url)
    return list(dict.fromkeys(hints))
