"""Offline detection of dependency names resembling popular PyPI projects."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Literal

from packaging.utils import InvalidName, canonicalize_name

from .contracts import DependencyResult, SimilarityCandidate


class SnapshotError(ValueError):
    """Raised when the packaged popular-package snapshot is missing or malformed."""


@dataclass(frozen=True, slots=True)
class PopularPackage:
    """One canonical package name and its one-based popularity rank."""

    name: str
    rank: int


@dataclass(frozen=True, slots=True, init=False)
class PopularPackageIndex:
    """Validated package snapshot with indexes for bounded candidate searches."""

    packages: tuple[PopularPackage, ...]
    _by_length: Mapping[int, tuple[PopularPackage, ...]] = field(repr=False)
    _by_name: Mapping[str, PopularPackage] = field(repr=False)
    _by_separatorless_name: Mapping[str, tuple[PopularPackage, ...]] = field(repr=False)

    def __init__(self, packages: Sequence[Mapping[str, object] | PopularPackage]) -> None:
        validated = self._validate_packages(packages)
        by_length: dict[int, list[PopularPackage]] = {}
        by_name: dict[str, PopularPackage] = {}
        by_separatorless_name: dict[str, list[PopularPackage]] = {}
        for package in validated:
            by_length.setdefault(len(package.name), []).append(package)
            by_name[package.name] = package
            by_separatorless_name.setdefault(package.name.replace("-", ""), []).append(package)

        object.__setattr__(self, "packages", validated)
        object.__setattr__(
            self,
            "_by_length",
            MappingProxyType({length: tuple(items) for length, items in by_length.items()}),
        )
        object.__setattr__(self, "_by_name", MappingProxyType(by_name))
        object.__setattr__(
            self,
            "_by_separatorless_name",
            MappingProxyType({key: tuple(items) for key, items in by_separatorless_name.items()}),
        )

    @classmethod
    def from_packages(
        cls, packages: Sequence[Mapping[str, object] | PopularPackage]
    ) -> PopularPackageIndex:
        """Build an index from an in-memory ordered package list."""

        return cls(packages)

    def rank_of(self, name: str) -> int | None:
        """Return the popularity rank for a canonical package name, if indexed."""

        package = self._by_name.get(name)
        return package.rank if package is not None else None

    def packages_with_length(self, length: int) -> tuple[PopularPackage, ...]:
        """Return the immutable bucket of packages with the requested name length."""

        return self._by_length.get(length, ())

    def separator_variants(self, name: str) -> tuple[PopularPackage, ...]:
        """Return packages indexed under this separator-free name key."""

        return self._by_separatorless_name.get(name, ())

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> PopularPackageIndex:
        """Load and validate the packaged snapshot through importlib.resources."""

        try:
            resource = files("slopguard").joinpath("data", "popular_packages.json")
            snapshot = json.loads(resource.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ModuleNotFoundError) as error:
            raise SnapshotError(
                f"Could not read the packaged popular-package snapshot: {error}"
            ) from error

        if not isinstance(snapshot, dict):
            raise SnapshotError("The packaged popular-package snapshot must be a JSON object.")
        required_headers = (
            "source_url",
            "source_last_update",
            "retrieved_at",
            "generator_version",
            "licence",
            "count",
            "packages",
        )
        if any(key not in snapshot for key in required_headers):
            raise SnapshotError("The packaged popular-package snapshot is missing a header field.")
        for key in required_headers[:-2]:
            if not isinstance(snapshot[key], str) or not snapshot[key].strip():
                raise SnapshotError(f"The popular-package snapshot field {key!r} must be a string.")
        raw_packages = snapshot["packages"]
        count = snapshot["count"]
        if not isinstance(raw_packages, list):
            raise SnapshotError("The popular-package snapshot packages field must be an array.")
        if isinstance(count, bool) or not isinstance(count, int) or count != len(raw_packages):
            raise SnapshotError("The popular-package snapshot count must match its packages array.")
        return cls(raw_packages)

    @staticmethod
    def _validate_packages(
        packages: Sequence[Mapping[str, object] | PopularPackage],
    ) -> tuple[PopularPackage, ...]:
        validated: list[PopularPackage] = []
        seen_names: set[str] = set()
        for expected_rank, raw_package in enumerate(packages, start=1):
            if isinstance(raw_package, PopularPackage):
                name, rank = raw_package.name, raw_package.rank
            elif isinstance(raw_package, Mapping):
                name, rank = raw_package.get("name"), raw_package.get("rank")
            else:
                raise SnapshotError(f"Package entry {expected_rank} must be an object.")
            if not isinstance(name, str) or not name:
                raise SnapshotError(f"Package entry {expected_rank} must have a non-empty name.")
            try:
                normalized_name = canonicalize_name(name, validate=True)
            except InvalidName as error:
                raise SnapshotError(
                    f"Package entry {expected_rank} has an invalid name."
                ) from error
            if name != normalized_name:
                raise SnapshotError(f"Package entry {expected_rank} name is not canonical.")
            if name in seen_names:
                raise SnapshotError(f"Package entry {expected_rank} duplicates a package name.")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank != expected_rank:
                raise SnapshotError("Package ranks must be unique and ordered from 1 through n.")
            seen_names.add(name)
            validated.append(PopularPackage(name=name, rank=rank))
        return tuple(validated)


@dataclass(frozen=True, slots=True)
class SimilaritySettings:
    """Tunable limits for comparing dependency names with the popularity snapshot."""

    top_n: int = 5000
    min_length: int = 4
    max_distance_by_length: tuple[tuple[int, int], ...] = ((4, 1), (8, 2))
    max_matches: int = 3

    def __post_init__(self) -> None:
        for field_name in ("top_n", "min_length", "max_matches"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer.")
        thresholds = self.max_distance_by_length
        if not isinstance(thresholds, tuple) or not thresholds:
            raise ValueError("max_distance_by_length must be a non-empty tuple of pairs.")
        previous_length = 0
        previous_distance = 0
        for pair in thresholds:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("max_distance_by_length entries must be (length, distance) pairs.")
            length, distance = pair
            if any(isinstance(value, bool) or not isinstance(value, int) for value in pair):
                raise ValueError("Distance thresholds and distances must be integers.")
            if length <= previous_length or distance <= 0:
                raise ValueError("Distance thresholds and distances must be positive and sorted.")
            if distance < previous_distance:
                raise ValueError("Allowed edit distances must not decrease for longer names.")
            previous_length = length
            previous_distance = distance

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> SimilaritySettings:
        """Build settings from a TOML table and reject keys this version does not know."""

        if not isinstance(values, Mapping):
            raise ValueError("Similarity settings must be a mapping.")
        known_keys = {"top_n", "min_length", "max_distance_by_length", "max_matches"}
        unknown_keys = set(values) - known_keys
        if unknown_keys:
            unknown = ", ".join(sorted(str(key) for key in unknown_keys))
            raise ValueError(f"Unknown similarity setting(s): {unknown}.")
        normalized_values = dict(values)
        if "max_distance_by_length" in normalized_values:
            thresholds = normalized_values["max_distance_by_length"]
            if isinstance(thresholds, (str, bytes)) or not isinstance(thresholds, Sequence):
                raise ValueError("max_distance_by_length must be a sequence of pairs.")
            try:
                normalized_values["max_distance_by_length"] = tuple(
                    tuple(pair)
                    for pair in thresholds  # type: ignore[arg-type]
                )
            except TypeError as error:
                raise ValueError("max_distance_by_length must be a sequence of pairs.") from error
        try:
            return cls(**normalized_values)  # type: ignore[arg-type]
        except TypeError as error:
            raise ValueError(f"Invalid similarity settings: {error}") from error

    def _distance_limit(self, name_length: int) -> int:
        allowed_distance = 0
        for length_threshold, distance in self.max_distance_by_length:
            if name_length < length_threshold:
                break
            allowed_distance = distance
        return allowed_distance


@dataclass(frozen=True, slots=True)
class LookalikeMatch:
    """A popular package name and the bounded similarity evidence for it."""

    name: str
    similarity: float
    edit_distance: int
    popularity_rank: int
    match_kind: Literal["edit", "separator"]


def find_lookalikes(
    name: str,
    index: PopularPackageIndex,
    settings: SimilaritySettings,
) -> tuple[LookalikeMatch, ...]:
    """Find popular names within the configured bounded OSA edit distance."""

    if not isinstance(name, str):
        raise TypeError("The dependency name must be a string.")
    try:
        normalized_name = canonicalize_name(name, validate=True)
    except InvalidName as error:
        raise ValueError("The dependency name is not a valid package name.") from error

    name_length = len(normalized_name)
    if name_length < settings.min_length:
        return ()
    index_limit = min(settings.top_n, len(index.packages))
    own_rank = index.rank_of(normalized_name)
    if own_rank is not None and own_rank <= index_limit:
        return ()

    max_distance = settings._distance_limit(name_length)
    if max_distance == 0:
        return ()
    min_candidate_length = name_length - max_distance
    max_candidate_length = name_length + max_distance
    separatorless_name = normalized_name.replace("-", "")
    separator_matches = {
        package.name
        for package in index.separator_variants(separatorless_name)
        if package.rank <= index_limit and package.name != normalized_name
    }

    matches: list[LookalikeMatch] = []
    for candidate_length in range(min_candidate_length, max_candidate_length + 1):
        for package in index.packages_with_length(candidate_length):
            if package.rank > index_limit or package.name == normalized_name:
                continue
            if package.name in separator_matches:
                distance = abs(name_length - len(package.name))
                match_kind: Literal["edit", "separator"] = "separator"
            else:
                bounded_distance = _osa_distance_within_limit(
                    normalized_name, package.name, max_distance
                )
                if bounded_distance is None:
                    continue
                distance = bounded_distance
                match_kind = "edit"
            matches.append(
                LookalikeMatch(
                    name=package.name,
                    similarity=1 - distance / max(name_length, len(package.name)),
                    edit_distance=distance,
                    popularity_rank=package.rank,
                    match_kind=match_kind,
                )
            )

    matches.sort(key=lambda match: (match.edit_distance, match.popularity_rank, match.name))
    return tuple(matches[: settings.max_matches])


def _osa_distance_within_limit(left: str, right: str, limit: int) -> int | None:
    """Return optimal-string-alignment distance within limit, or None when over it."""

    left_length = len(left)
    right_length = len(right)
    if abs(left_length - right_length) > limit:
        return None

    outside_band = limit + 1
    previous_previous = [outside_band] * (right_length + 1)
    previous = [outside_band] * (right_length + 1)
    for column in range(min(right_length, limit) + 1):
        previous[column] = column

    for row in range(1, left_length + 1):
        current = [outside_band] * (right_length + 1)
        if row <= limit:
            current[0] = row
        lower_column = max(1, row - limit)
        upper_column = min(right_length, row + limit)
        row_minimum = outside_band
        for column in range(lower_column, upper_column + 1):
            substitution_cost = left[row - 1] != right[column - 1]
            distance = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + substitution_cost,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                distance = min(distance, previous_previous[column - 2] + 1)
            current[column] = distance
            row_minimum = min(row_minimum, distance)
        if row_minimum > limit:
            return None
        previous_previous, previous = previous, current

    result = previous[right_length]
    return result if result <= limit else None


def apply_similarity(
    candidate: DependencyResult,
    matches: Iterable[LookalikeMatch],
) -> DependencyResult:
    """Return a deep-copied result with similarity evidence, without scoring it."""

    try:
        requested_name = canonicalize_name(candidate["requested_name"], validate=True)
        normalized_name = canonicalize_name(candidate["normalized_name"], validate=True)
    except InvalidName as error:
        raise ValueError("Candidate dependency name is invalid.") from error
    if requested_name != normalized_name:
        raise ValueError("Candidate dependency name does not match normalized name.")

    result = deepcopy(candidate)
    similarity_candidates: list[SimilarityCandidate] = []
    for match in matches:
        similarity_candidates.append(
            {
                "name": match.name,
                "similarity": round(match.similarity, 4),
                "edit_distance": match.edit_distance,
                "popularity_rank": match.popularity_rank,
                "match_kind": match.match_kind,
                "registry_exists": True,
            }
        )
    result["similarity_candidates"] = similarity_candidates
    return result
