"""Tests for deterministic score, severity and policy decisions."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from jsonschema import Draft202012Validator

from slopguard.config import (
    AllowlistEntry,
    SlopGuardConfig,
)
from slopguard.contracts import DependencyResult, load_result_schema
from slopguard.scoring import score_result
from slopguard.similarity import LookalikeMatch, apply_similarity

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SCHEMA_VALIDATOR = Draft202012Validator(load_result_schema())


def make_result(
    name: str,
    *,
    status: str = "exists",
    first_release_days_ago: int | None = 365,
    release_count: int | None = 10,
    project_urls: dict[str, str] | None = None,
    source_hints: list[str] | None = None,
    matches: tuple[LookalikeMatch, ...] = (),
    source_type: str = "requirements",
) -> DependencyResult:
    first_release_at = None
    if first_release_days_ago is not None:
        first_release_at = (
            (NOW - timedelta(days=first_release_days_ago)).isoformat().replace("+00:00", "Z")
        )
    result: DependencyResult = {
        "contract_version": "1.1.0",
        "requested_name": name,
        "normalized_name": name,
        "registry_exists": True if status == "exists" else False if status == "not_found" else None,
        "registry_status": status,  # type: ignore[typeddict-item]
        "registry_metadata": {
            "first_release_at": first_release_at,
            "release_count": release_count,
            "latest_release_at": None,
            "project_urls": project_urls or {},
            "maintainer_hints": [],
            "source_hints": source_hints or [],
        },
        "metadata_evidence": [],
        "similarity_candidates": [],
        "risk_score": 0.0,
        "severity": "info",
        "remediation": "Await risk analysis.",
        "source_locations": [{"file": "requirements.txt", "line": 1, "source_type": source_type}],  # type: ignore[list-item]
    }
    return apply_similarity(result, matches)


def score(
    result: DependencyResult,
    config: SlopGuardConfig | None = None,
    *,
    now: datetime = NOW,
) -> DependencyResult:
    scored = score_result(result, config or SlopGuardConfig(), now=now)
    SCHEMA_VALIDATOR.validate(scored)
    return scored


def match(name: str, distance: int, rank: int, *, kind: str = "edit") -> LookalikeMatch:
    return LookalikeMatch(
        name=name,
        similarity=0.9,
        edit_distance=distance,
        popularity_rank=rank,
        match_kind=kind,  # type: ignore[arg-type]
    )


def blocking_config() -> SlopGuardConfig:
    defaults = SlopGuardConfig()
    return replace(defaults, policy=replace(defaults.policy, mode="block"))


def contribution(result: DependencyResult, signal: str) -> dict[str, object]:
    contributions = result["score_contributions"]
    return next(item for item in contributions if item["signal"] == signal)


def test_default_configuration_scores_not_found_typosquat_to_block() -> None:
    candidate = make_result("reqeusts", status="not_found", matches=(match("requests", 1, 8),))

    scored = score(candidate, blocking_config())

    assert scored["risk_score"] == 95
    assert scored["severity"] == "critical"
    assert scored["decision"] == "block"
    assert contribution(scored, "not_found")["points"] == 60
    assert contribution(scored, "lookalike")["points"] == 35
    assert "requests" in scored["remediation"]
    assert "malware" not in scored["remediation"].lower()


def test_nonexistent_name_without_lookalike_warns_at_high_severity() -> None:
    scored = score(make_result("madeup-name", status="not_found"))

    assert scored["risk_score"] == 60
    assert scored["severity"] == "high"
    assert scored["decision"] == "warn"


def test_popular_existing_package_with_complete_metadata_passes() -> None:
    scored = score(
        make_result(
            "requests",
            status="exists",
            project_urls={"Homepage": "https://example.test"},
            source_hints=["https://example.test/source"],
        )
    )

    assert scored["risk_score"] == 0
    assert scored["severity"] == "info"
    assert scored["decision"] == "pass"


def test_fresh_registered_lookalike_with_few_signals_blocks_at_threshold() -> None:
    scored = score(
        make_result(
            "reqeusts",
            status="exists",
            first_release_days_ago=3,
            release_count=1,
            matches=(match("requests", 1, 8),),
        ),
        blocking_config(),
    )

    assert scored["risk_score"] == 80
    assert scored["decision"] == "block"
    assert contribution(scored, "new_package")["points"] == 25
    assert contribution(scored, "few_releases")["points"] == 10
    assert contribution(scored, "missing_source")["points"] == 10
    assert "'requests'" in scored["remediation"]
    assert "first released recently" in scored["remediation"]


def test_old_existing_single_edit_candidate_does_not_block() -> None:
    scored = score(
        make_result(
            "request",
            status="exists",
            first_release_days_ago=3650,
            release_count=20,
            project_urls={"Homepage": "https://example.test"},
            source_hints=["https://example.test/source"],
            matches=(match("requests", 1, 8),),
        ),
        blocking_config(),
    )

    assert scored["risk_score"] == 35
    assert scored["decision"] in {"pass", "warn"}
    assert scored["decision"] != "block"


def test_weak_snapshot_matches_score_below_strong_candidate_and_warn_threshold() -> None:
    config = SlopGuardConfig()
    strong = score(
        make_result("reqeusts", status="not_found", matches=(match("requests", 1, 8),)),
        config,
    )
    weak_cases = [
        ("opencv", match("opencc", 1, 3837)),
        ("pydantic2", match("dydantic", 2, 3870)),
    ]

    for name, candidate_match in weak_cases:
        scored = score(make_result(name, matches=(candidate_match,)), config)
        points = contribution(scored, "lookalike")["points"]
        assert points < contribution(strong, "lookalike")["points"]
        assert points < config.policy.warn_threshold


def test_remediation_names_the_strongest_candidate_not_the_first_candidate() -> None:
    defaults = SlopGuardConfig()
    weights = replace(defaults.scoring.weights, lookalike=100)
    config = replace(defaults, scoring=replace(defaults.scoring, weights=weights))
    candidate = make_result(
        "new-package",
        matches=(
            match("low-ranked", 1, 4000),
            match("top-ranked", 2, 8),
        ),
    )

    scored = score(candidate, config)

    assert scored["decision"] == "warn"
    assert "top-ranked" in contribution(scored, "lookalike")["summary"]
    assert "top-ranked" in scored["remediation"]
    assert "low-ranked" not in scored["remediation"]


def test_malformed_candidate_without_distance_or_rank_is_unavailable() -> None:
    candidate = make_result("new-package")
    candidate["similarity_candidates"] = [
        {"name": "nearby", "similarity": 0.9, "registry_exists": True}
    ]

    scored = score(candidate)

    assert contribution(scored, "lookalike")["points"] == 0
    assert "unavailable" in contribution(scored, "lookalike")["summary"]


def test_candidate_distance_without_a_configured_factor_is_rejected() -> None:
    candidate = make_result("new-package")
    candidate["similarity_candidates"] = [
        {
            "name": "nearby",
            "similarity": 0.9,
            "registry_exists": True,
            "edit_distance": 3,
            "popularity_rank": 8,
            "match_kind": "edit",
        }
    ]

    with pytest.raises(ValueError, match="edit distance 3"):
        score(candidate)


@pytest.mark.parametrize("status", ["transient_error", "malformed_response"])
def test_unknown_registry_status_never_blocks_high_similarity_score(status: str) -> None:
    defaults = blocking_config()
    weights = replace(defaults.scoring.weights, lookalike=100)
    scoring = replace(defaults.scoring, weights=weights)
    for on_unknown in ("warn", "pass"):
        config = replace(
            defaults,
            policy=replace(defaults.policy, on_unknown=on_unknown),  # type: ignore[arg-type]
            scoring=scoring,
        )
        scored = score(
            make_result("reqeusts", status=status, matches=(match("requests", 1, 8),)),
            config,
        )
        assert scored["risk_score"] == 100
        assert scored["decision"] == "warn"


@pytest.mark.parametrize(("on_unknown", "decision"), [("warn", "warn"), ("pass", "pass")])
def test_unknown_registry_floor_follows_on_unknown_setting(on_unknown: str, decision: str) -> None:
    defaults = SlopGuardConfig()
    config = replace(
        defaults,
        policy=replace(defaults.policy, on_unknown=on_unknown),  # type: ignore[arg-type]
    )

    scored = score(make_result("unknown-project", status="transient_error"), config)

    assert scored["decision"] == decision


@pytest.mark.parametrize(("on_unknown", "decision"), [("warn", "warn"), ("pass", "pass")])
def test_invalid_name_has_no_not_found_points_and_follows_unknown_policy(
    on_unknown: str, decision: str
) -> None:
    defaults = blocking_config()
    config = replace(
        defaults,
        policy=replace(defaults.policy, on_unknown=on_unknown),  # type: ignore[arg-type]
    )

    scored = score(make_result("invalid-name", status="invalid_name"), config)

    assert scored["decision"] == decision
    assert scored["allowlisted"] is False
    assert contribution(scored, "not_found")["points"] == 0
    assert "not a valid PyPI project name" in contribution(scored, "not_found")["summary"]
    assert "Fix the dependency name" in scored["remediation"]
    assert "exists" not in scored["remediation"].lower()


def test_invalid_name_with_high_score_never_blocks() -> None:
    defaults = blocking_config()
    weights = replace(defaults.scoring.weights, lookalike=100)
    scoring = replace(defaults.scoring, weights=weights)
    config = replace(
        defaults,
        policy=replace(defaults.policy, on_unknown="pass"),
        scoring=scoring,
    )

    scored = score(
        make_result("invalid-name", status="invalid_name", matches=(match("valid-name", 1, 8),)),
        config,
    )

    assert scored["risk_score"] >= config.policy.block_threshold
    assert scored["decision"] == "warn"


def test_import_only_not_found_lookalike_is_capped_at_warn() -> None:
    scored = score(
        make_result(
            "reqeusts",
            status="not_found",
            matches=(match("requests", 1, 8),),
            source_type="import",
        ),
        blocking_config(),
    )

    assert scored["risk_score"] >= blocking_config().policy.block_threshold
    assert scored["decision"] == "warn"


def test_allowlisted_result_passes_but_keeps_its_score_and_contributions() -> None:
    config = replace(
        blocking_config(),
        allowlist=(AllowlistEntry("reqeusts", "approved exception", date(2026, 2, 1)),),
    )
    candidate = make_result("reqeusts", status="not_found", matches=(match("requests", 1, 8),))

    scored = score(candidate, config)

    assert scored["allowlisted"] is True
    assert scored["allowlist"] == {"reason": "approved exception", "expires": "2026-02-01"}
    assert scored["decision"] == "pass"
    assert scored["risk_score"] == 95
    assert contribution(scored, "not_found")["points"] == 60
    assert contribution(scored, "lookalike")["points"] == 35
    SCHEMA_VALIDATOR.validate(scored)


def test_expired_allowlist_entry_does_not_apply() -> None:
    config = replace(
        SlopGuardConfig(),
        allowlist=(
            AllowlistEntry("madeup-name", "temporary", expires=NOW.date() - timedelta(days=1)),
        ),
    )

    scored = score(make_result("madeup-name", status="not_found"), config)

    assert scored["allowlisted"] is False
    assert scored["decision"] == "warn"


@pytest.mark.parametrize(
    ("points", "severity", "decision"),
    [
        (19.99, "info", "pass"),
        (20, "low", "pass"),
        (39.99, "low", "pass"),
        (40, "medium", "warn"),
        (59.99, "medium", "warn"),
        (60, "high", "warn"),
        (79.99, "high", "warn"),
        (80, "critical", "block"),
    ],
)
def test_policy_and_severity_boundaries(points: float, severity: str, decision: str) -> None:
    defaults = blocking_config()
    weights = replace(defaults.scoring.weights, not_found=points)
    config = replace(defaults, scoring=replace(defaults.scoring, weights=weights))

    scored = score(make_result("missing-project", status="not_found"), config)

    assert scored["risk_score"] == points
    assert scored["severity"] == severity
    assert scored["decision"] == decision


def test_contributions_sum_to_unclamped_score_and_result_is_deterministic() -> None:
    defaults = blocking_config()
    weights = replace(defaults.scoring.weights, not_found=100, lookalike=100)
    config = replace(defaults, scoring=replace(defaults.scoring, weights=weights))
    candidate = make_result("reqeusts", status="not_found", matches=(match("requests", 1, 8),))
    original = candidate.copy()

    first = score(candidate, config)
    second = score(candidate, config)

    assert first == second
    assert candidate == original
    assert first["risk_score"] == 100
    assert sum(item["points"] for item in first["score_contributions"]) == 200


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        score_result(make_result("example"), SlopGuardConfig(), now=datetime(2026, 1, 15))
