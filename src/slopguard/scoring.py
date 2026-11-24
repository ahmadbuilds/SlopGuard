"""Pure, explainable scoring and policy decisions for dependency results."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal

from slopguard.config import AllowlistEntry, ScoringSignals, SlopGuardConfig
from slopguard.contracts import DependencyResult, ScoreContribution, ScoreSignal, Severity

RISK_SCORE_RANGE = (0.0, 100.0)  # Fixed bounds in the versioned result contract.
UNKNOWN_REGISTRY_STATUSES = frozenset({"transient_error", "malformed_response"})


def score_result(
    result: DependencyResult,
    config: SlopGuardConfig,
    *,
    now: datetime,
) -> DependencyResult:
    """Return a deep copy with score contributions and a policy decision."""

    _validate_now(now)
    lookalike_contribution, strongest_lookalike = _lookalike_contribution(result, config)
    contributions = [
        _not_found_contribution(result, config),
        lookalike_contribution,
        _new_package_contribution(result, config, now),
        _few_releases_contribution(result, config),
        _missing_source_contribution(result, config),
    ]
    contributions.sort(key=lambda contribution: contribution["signal"])

    raw_score = sum(contribution["points"] for contribution in contributions)
    risk_score = round(min(max(raw_score, RISK_SCORE_RANGE[0]), RISK_SCORE_RANGE[1]), 2)
    severity = _severity_for_score(risk_score, config)
    decision = _policy_decision(result, risk_score, config)
    allowlist_entry = config.allowlist_entry_for(result["normalized_name"], now=now)
    allowlisted = allowlist_entry is not None
    if allowlisted:
        decision = "pass"
    new_package_points = next(
        contribution["points"]
        for contribution in contributions
        if contribution["signal"] == "new_package"
    )

    scored = deepcopy(result)
    scored["risk_score"] = risk_score
    scored["severity"] = severity
    scored["remediation"] = _remediation(
        result,
        decision,
        allowlist_entry,
        strongest_lookalike,
        recently_released=new_package_points > 0,
    )
    scored["score_contributions"] = contributions
    scored["decision"] = decision
    scored["allowlisted"] = allowlisted
    return scored


def _validate_now(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be a timezone-aware UTC datetime.")


def _zero(signal: ScoreSignal, summary: str) -> ScoreContribution:
    return {"signal": signal, "points": 0.0, "summary": summary}


def _not_found_contribution(
    result: DependencyResult,
    config: SlopGuardConfig,
) -> ScoreContribution:
    status = result.get("registry_status")
    if status == "not_found":
        return {
            "signal": "not_found",
            "points": float(config.scoring.weights.not_found),
            "summary": f"PyPI returned not found for {result['requested_name']!r}.",
        }
    if status in UNKNOWN_REGISTRY_STATUSES or status is None:
        return _zero(
            "not_found",
            "Registry status evidence was unavailable; not-found points were not applied.",
        )
    return _zero(
        "not_found",
        "PyPI confirms this package exists; not-found points were not applied.",
    )


def _rank_factor(rank: int, config: SlopGuardConfig) -> float:
    signals = config.scoring.signals
    top_n = config.similarity.top_n
    if rank <= signals.full_strength_rank or top_n <= signals.full_strength_rank:
        return signals.full_rank_factor
    rank_span = top_n - signals.full_strength_rank
    effective_rank = min(rank, top_n)
    progress = (effective_rank - signals.full_strength_rank) / rank_span
    return signals.full_rank_factor - progress * (
        signals.full_rank_factor - signals.min_rank_factor
    )


def _candidate_strength(
    candidate: Mapping[str, object],
    config: SlopGuardConfig,
) -> tuple[float, int, int, str, float] | None:
    distance = candidate.get("edit_distance")
    rank = candidate.get("popularity_rank")
    name = candidate.get("name")
    match_kind = candidate.get("match_kind")
    if (
        isinstance(distance, bool)
        or not isinstance(distance, int)
        or distance < 0
        or isinstance(rank, bool)
        or not isinstance(rank, int)
        or rank < 1
        or not isinstance(name, str)
        or not name
        or not isinstance(match_kind, str)
        or match_kind not in {"edit", "separator"}
    ):
        return None

    signals: ScoringSignals = config.scoring.signals
    if match_kind == "separator":
        distance_factor = signals.separator_factor
    else:
        factors_by_distance = dict(signals.distance_factors)
        if distance not in factors_by_distance:
            raise ValueError(f"No scoring factor is configured for edit distance {distance}.")
        distance_factor = factors_by_distance[distance]
    rank_factor = _rank_factor(rank, config)
    strength = distance_factor * rank_factor
    return strength, distance, rank, name, distance_factor


def _lookalike_contribution(
    result: DependencyResult,
    config: SlopGuardConfig,
) -> tuple[ScoreContribution, str | None]:
    candidates = result.get("similarity_candidates", [])
    if not candidates:
        return _zero("lookalike", "No look-alike candidates were found."), None
    scored_candidates = [
        scored
        for candidate in candidates
        if isinstance(candidate, Mapping)
        if (scored := _candidate_strength(candidate, config)) is not None
    ]
    if not scored_candidates:
        return (
            _zero(
                "lookalike",
                "Look-alike distance or popularity evidence was unavailable.",
            ),
            None,
        )
    strongest = min(scored_candidates, key=lambda item: (-item[0], item[1], item[2], item[3]))
    strength, distance, rank, name, _ = strongest
    points = config.scoring.weights.lookalike * strength
    return (
        {
            "signal": "lookalike",
            "points": float(points),
            "summary": f"{name!r} is the strongest look-alike: distance {distance}, popularity rank {rank}.",
        },
        name,
    )


def _metadata_for_existing_result(
    result: DependencyResult,
) -> Mapping[str, object] | None:
    if result.get("registry_status") != "exists":
        return None
    metadata = result.get("registry_metadata")
    return metadata if isinstance(metadata, Mapping) else None


def _parse_release_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp.astimezone(UTC)


def _new_package_contribution(
    result: DependencyResult,
    config: SlopGuardConfig,
    now: datetime,
) -> ScoreContribution:
    metadata = _metadata_for_existing_result(result)
    first_release = metadata.get("first_release_at") if metadata is not None else None
    released_at = _parse_release_timestamp(first_release)
    if released_at is None:
        return _zero(
            "new_package",
            "First-release evidence was unavailable; new-package points were not applied.",
        )
    age = now - released_at
    if timedelta(0) <= age <= timedelta(days=config.scoring.signals.new_package_days):
        return {
            "signal": "new_package",
            "points": float(config.scoring.weights.new_package),
            "summary": (
                f"The first recorded release was {age.days} days before the scoring time, "
                f"within the {config.scoring.signals.new_package_days}-day window."
            ),
        }
    return _zero(
        "new_package",
        f"The first recorded release is outside the {config.scoring.signals.new_package_days}-day window.",
    )


def _few_releases_contribution(
    result: DependencyResult,
    config: SlopGuardConfig,
) -> ScoreContribution:
    metadata = _metadata_for_existing_result(result)
    count = metadata.get("release_count") if metadata is not None else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return _zero(
            "few_releases",
            "Release-count evidence was unavailable; few-release points were not applied.",
        )
    maximum = config.scoring.signals.few_releases_max
    if count <= maximum:
        return {
            "signal": "few_releases",
            "points": float(config.scoring.weights.few_releases),
            "summary": f"The package has {count} release versions (at most {maximum}).",
        }
    return _zero(
        "few_releases",
        f"The package has {count} release versions, above the {maximum}-release signal limit.",
    )


def _missing_source_contribution(
    result: DependencyResult,
    config: SlopGuardConfig,
) -> ScoreContribution:
    metadata = _metadata_for_existing_result(result)
    if metadata is None:
        return _zero(
            "missing_source",
            "Registry source-link evidence was unavailable; missing-source points were not applied.",
        )
    project_urls = metadata.get("project_urls")
    source_hints = metadata.get("source_hints")
    if not isinstance(project_urls, Mapping) or not isinstance(source_hints, list):
        return _zero(
            "missing_source",
            "Registry source-link evidence was unavailable; missing-source points were not applied.",
        )
    if not project_urls and not source_hints:
        return {
            "signal": "missing_source",
            "points": float(config.scoring.weights.missing_source),
            "summary": "The existing package has no project URLs or source links in its metadata.",
        }
    return _zero(
        "missing_source",
        "Project URLs or source links were present; missing-source points were not applied.",
    )


def _severity_for_score(score: float, config: SlopGuardConfig) -> Severity:
    bounds = config.scoring.severity
    if score >= bounds.critical:
        return "critical"
    if score >= bounds.high:
        return "high"
    if score >= bounds.medium:
        return "medium"
    if score >= bounds.low:
        return "low"
    return "info"


def _is_import_only(result: DependencyResult) -> bool:
    locations = result.get("source_locations")
    return bool(locations) and all(
        isinstance(location, Mapping) and location.get("source_type") == "import"
        for location in locations
    )


def _policy_decision(
    result: DependencyResult,
    score: float,
    config: SlopGuardConfig,
) -> Literal["pass", "warn", "block"]:
    policy = config.policy
    if score >= policy.block_threshold and policy.mode == "block":
        decision: Literal["pass", "warn", "block"] = "block"
    elif score >= policy.warn_threshold:
        decision = "warn"
    else:
        decision = "pass"

    status = result.get("registry_status")
    unknown = status is None or status in UNKNOWN_REGISTRY_STATUSES
    if unknown:
        if decision == "block":
            decision = "warn"
        elif policy.on_unknown == "warn" and decision == "pass":
            decision = "warn"
    if _is_import_only(result) and decision == "block":
        decision = "warn"
    return decision


def _remediation(
    result: DependencyResult,
    decision: Literal["pass", "warn", "block"],
    allowlist_entry: AllowlistEntry | None,
    strongest_lookalike: str | None,
    *,
    recently_released: bool,
) -> str:
    requested_name = result["requested_name"]
    if allowlist_entry is not None:
        exception = (
            f"This dependency is allowed by the configured exception: {allowlist_entry.reason}"
        )
        if strongest_lookalike is not None:
            return f"{exception} It resembles {strongest_lookalike!r}."
        return exception
    suggestion = strongest_lookalike
    if decision == "block":
        if result.get("registry_status") == "not_found" and isinstance(suggestion, str):
            return (
                f"Do not add {requested_name!r} until its name is verified. "
                f"PyPI did not find it; check whether {suggestion!r} was intended."
            )
        if isinstance(suggestion, str):
            evidence = (
                "was first released recently and resembles" if recently_released else "resembles"
            )
            return (
                f"{requested_name!r} {evidence} {suggestion!r}; confirm {suggestion!r} was not "
                "intended before merging."
            )
        return "Review this dependency before merging; its configured risk score reached the block threshold."
    if decision == "warn":
        if result.get("registry_status") in UNKNOWN_REGISTRY_STATUSES:
            message = "Review the dependency because registry evidence was unavailable and its risk warrants a warning."
            if isinstance(suggestion, str):
                return f"{message} It resembles {suggestion!r}."
            return message
        if result.get("registry_status") == "not_found" and isinstance(suggestion, str):
            return (
                f"Verify {requested_name!r} with its publisher. PyPI did not find it; "
                f"{suggestion!r} is a possible intended name."
            )
        if result.get("registry_status") == "not_found":
            return (
                f"Verify {requested_name!r} with its publisher because PyPI did not find that name."
            )
        if isinstance(suggestion, str):
            return (
                f"Review {requested_name!r}; it resembles {suggestion!r}. "
                "Confirm the dependency is intended."
            )
        return "Review the dependency evidence and confirm that the package is intended."
    if result.get("registry_status") == "not_found":
        if isinstance(suggestion, str):
            return (
                f"Confirm {requested_name!r} is intended; PyPI did not find it and "
                f"{suggestion!r} is a possible intended name."
            )
        return f"Confirm that {requested_name!r} is the intended package name before adding it."
    if isinstance(suggestion, str):
        return f"{requested_name!r} resembles {suggestion!r}; confirm the dependency is intended."
    return "No configured risk threshold was reached; review the evidence if this dependency was unexpected."
