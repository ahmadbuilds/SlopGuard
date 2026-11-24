"""Map safe PyPI lookups into the shared dependency-result contract."""

from __future__ import annotations

from copy import deepcopy
from urllib.parse import quote

from packaging.utils import canonicalize_name

from .contracts import DependencyResult, MetadataEvidence, RegistryDiagnostics
from .registry import RegistryLookupResult


def apply_registry_lookup(
    candidate: DependencyResult, lookup: RegistryLookupResult
) -> DependencyResult:
    """Return an enriched copy without making a risk or policy decision."""

    if canonicalize_name(candidate["normalized_name"]) != canonicalize_name(lookup.normalized_name):
        raise ValueError("Registry lookup name does not match the dependency candidate.")

    result = deepcopy(candidate)
    result["registry_exists"] = lookup.registry_exists
    result["registry_status"] = lookup.status
    result["registry_diagnostics"] = _diagnostics(lookup)

    if lookup.status == "exists" and lookup.metadata is not None:
        result["registry_metadata"] = deepcopy(lookup.metadata)
    else:
        result.pop("registry_metadata", None)

    api_url = f"https://pypi.org/pypi/{quote(lookup.normalized_name, safe='')}/json"
    evidence = [entry for entry in result["metadata_evidence"] if entry.get("source") != api_url]
    additions = _evidence_for_lookup(lookup.status, lookup.metadata, api_url)
    for entry in additions:
        if entry not in evidence:
            evidence.append(entry)
    result["metadata_evidence"] = evidence
    return result


def _diagnostics(lookup: RegistryLookupResult) -> RegistryDiagnostics:
    return {
        "latency_ms": lookup.latency_ms,
        "attempts": lookup.attempts,
        "http_status": lookup.http_status,
        "retry_after": lookup.retry_after,
        "rate_limit_remaining": lookup.rate_limit_remaining,
        "error": lookup.error,
    }


def _evidence_for_lookup(
    status: str, metadata: dict[str, object] | None, source: str
) -> list[MetadataEvidence]:
    if status == "not_found":
        return [
            {
                "kind": "registry_metadata",
                "summary": "PyPI returned HTTP 404 for this normalized package name.",
                "source": source,
                "confidence": 1.0,
            }
        ]
    if status != "exists" or metadata is None:
        return []

    evidence: list[MetadataEvidence] = [
        {
            "kind": "registry_metadata",
            "summary": "PyPI returned a project record for this normalized package name.",
            "source": source,
            "confidence": 1.0,
        }
    ]
    release_count = metadata.get("release_count")
    first_release = metadata.get("first_release_at")
    latest_release = metadata.get("latest_release_at")
    release_parts: list[str] = []
    if isinstance(release_count, int):
        release_parts.append(f"{release_count} published release versions")
    if isinstance(first_release, str):
        release_parts.append(f"first upload {first_release}")
    if isinstance(latest_release, str):
        release_parts.append(f"latest upload {latest_release}")
    if release_parts:
        evidence.append(
            {
                "kind": "release_history",
                "summary": "; ".join(release_parts) + ".",
                "source": source,
                "confidence": 1.0,
            }
        )

    maintainers = metadata.get("maintainer_hints")
    if isinstance(maintainers, list) and maintainers:
        evidence.append(
            {
                "kind": "maintainer",
                "summary": "PyPI maintainer hints: "
                + ", ".join(str(item) for item in maintainers)
                + ".",
                "source": source,
                "confidence": 0.8,
            }
        )

    source_hints = metadata.get("source_hints")
    if isinstance(source_hints, list) and source_hints:
        evidence.append(
            {
                "kind": "registry_metadata",
                "summary": "Project source links: "
                + ", ".join(str(item) for item in source_hints)
                + ".",
                "source": source,
                "confidence": 0.8,
            }
        )
    return evidence
