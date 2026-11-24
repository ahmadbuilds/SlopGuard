"""Typed names for the public dependency-result contract.

The JSON Schema is authoritative for serialized data. Keep these types aligned
with ``schemas/dependency-result.schema.json`` when changing the contract.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

Severity = Literal["info", "low", "medium", "high", "critical"]
EvidenceKind = Literal[
    "registry_metadata",
    "maintainer",
    "release_history",
    "similarity",
    "other",
]


class MetadataEvidence(TypedDict):
    """A sourced fact used to explain a result."""

    kind: EvidenceKind
    summary: str
    source: str
    confidence: float


class SimilarityCandidate(TypedDict):
    """A PyPI package name that resembles the requested dependency."""

    name: str
    similarity: float
    registry_exists: NotRequired[bool | None]


class SourceLocation(TypedDict):
    """Location where the dependency name was introduced."""

    file: str
    line: int
    source_type: Literal["requirements", "pyproject", "import"]


class DependencyResult(TypedDict):
    """Stable handoff shape shared by scanner stages and presentation."""

    contract_version: str
    requested_name: str
    normalized_name: str
    registry_exists: bool | None
    metadata_evidence: list[MetadataEvidence]
    similarity_candidates: list[SimilarityCandidate]
    risk_score: float
    severity: Severity
    remediation: str
    source_locations: NotRequired[list[SourceLocation]]
