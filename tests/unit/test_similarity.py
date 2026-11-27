"""Tests for pure package-name look-alike detection."""

from time import perf_counter

import pytest
from jsonschema import Draft202012Validator
from packaging.utils import canonicalize_name

from slopguard.contracts import DependencyResult, load_result_schema
from slopguard.similarity import (
    LookalikeMatch,
    PopularPackageIndex,
    SimilaritySettings,
    SnapshotError,
    apply_similarity,
    find_lookalikes,
)


def make_index(*names: str) -> PopularPackageIndex:
    return PopularPackageIndex.from_packages(
        [{"name": name, "rank": rank} for rank, name in enumerate(names, start=1)]
    )


def test_common_misspellings_and_adjacent_transposition() -> None:
    settings = SimilaritySettings()
    examples = [
        ("reqeusts", "requests", 1),
        ("numpyy", "numpy", 1),
        ("pandsa", "pandas", 1),
        ("request", "requests", 1),
    ]

    for misspelling, popular_name, distance in examples:
        matches = find_lookalikes(misspelling, make_index(popular_name), settings)
        assert len(matches) == 1
        assert matches[0].name == popular_name
        assert matches[0].edit_distance == distance
        assert matches[0].match_kind == "edit"


def test_separator_only_match_is_classified_separately() -> None:
    matches = find_lookalikes("pythondateutil", make_index("python-dateutil"), SimilaritySettings())

    assert len(matches) == 1
    assert matches[0] == LookalikeMatch(
        name="python-dateutil",
        similarity=1 - 1 / len("python-dateutil"),
        edit_distance=1,
        popularity_rank=1,
        match_kind="separator",
    )


def test_exact_popular_name_never_flags_itself() -> None:
    assert find_lookalikes("requests", make_index("requests"), SimilaritySettings()) == ()


def test_top_n_larger_than_index_uses_all_available_names() -> None:
    matches = find_lookalikes("reqeusts", make_index("requests"), SimilaritySettings(top_n=5000))

    assert [match.name for match in matches] == ["requests"]


def test_index_query_methods_return_rank_buckets_and_separator_variants() -> None:
    index = make_index("python-dateutil", "requests")

    assert index.rank_of("requests") == 2
    assert index.rank_of("unknown") is None
    assert [package.name for package in index.packages_with_length(len("requests"))] == ["requests"]
    assert index.packages_with_length(1000) == ()
    assert [package.name for package in index.separator_variants("pythondateutil")] == [
        "python-dateutil"
    ]
    assert index.separator_variants("unknown") == ()


@pytest.mark.parametrize(
    "packages",
    [
        [{"name": "Requests", "rank": 1}],
        [{"name": "requests", "rank": 2}],
    ],
)
def test_in_memory_index_rejects_noncanonical_names_and_invalid_ranks(
    packages: list[dict[str, object]],
) -> None:
    with pytest.raises(SnapshotError):
        PopularPackageIndex.from_packages(packages)


def test_popular_names_do_not_flag_each_other() -> None:
    index = make_index("requests", "niquests")

    assert find_lookalikes("requests", index, SimilaritySettings(top_n=2)) == ()
    assert find_lookalikes("niquests", index, SimilaritySettings(top_n=2)) == ()


def test_unrelated_and_short_names_have_no_matches() -> None:
    index = make_index("requests", "numpy", "pandas")

    assert find_lookalikes("volcano", index, SimilaritySettings()) == ()
    assert find_lookalikes("abc", index, SimilaritySettings()) == ()


def test_distance_two_is_enabled_only_at_configured_length() -> None:
    settings = SimilaritySettings()

    assert find_lookalikes("abXYef", make_index("abcdef"), settings) == ()
    long_match = find_lookalikes("abXYefgh", make_index("abcdefgh"), settings)
    assert len(long_match) == 1
    assert long_match[0].edit_distance == 2


def test_results_are_ordered_by_distance_then_rank_and_are_deterministic() -> None:
    index = make_index("abde", "abcf", "abcd")
    settings = SimilaritySettings()

    first = find_lookalikes("abce", index, settings)
    second = find_lookalikes("abce", index, settings)

    assert first == second
    assert [(match.name, match.edit_distance, match.popularity_rank) for match in first] == [
        ("abde", 1, 1),
        ("abcf", 1, 2),
        ("abcd", 1, 3),
    ]


def test_max_matches_caps_results() -> None:
    index = make_index("abca", "abcb", "abcc", "abcd")

    matches = find_lookalikes("abce", index, SimilaritySettings(max_matches=2))

    assert [match.name for match in matches] == ["abca", "abcb"]


@pytest.mark.parametrize(
    "settings",
    [
        {"top_n": 0},
        {"min_length": 0},
        {"max_matches": 0},
        {"max_distance_by_length": ((4, 0),)},
        {"max_distance_by_length": ((8, 2), (4, 1))},
        {"max_distance_by_length": ((4, 2), (8, 1))},
    ],
)
def test_invalid_settings_are_rejected(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SimilaritySettings(**settings)  # type: ignore[arg-type]


def test_from_mapping_rejects_unknown_keys_and_accepts_toml_pairs() -> None:
    settings = SimilaritySettings.from_mapping(
        {"top_n": 20, "max_distance_by_length": [[4, 1], [8, 2]]}
    )
    assert settings.top_n == 20
    assert settings.max_distance_by_length == ((4, 1), (8, 2))

    with pytest.raises(ValueError, match="unknown"):
        SimilaritySettings.from_mapping({"unknown_setting": 1})


def test_apply_similarity_preserves_other_result_fields_and_validates_schema() -> None:
    candidate: DependencyResult = {
        "contract_version": "1.1.0",
        "requested_name": "reqeusts",
        "normalized_name": "reqeusts",
        "registry_exists": False,
        "registry_status": "not_found",
        "registry_metadata": {
            "first_release_at": None,
            "release_count": None,
            "latest_release_at": None,
            "project_urls": {},
            "maintainer_hints": [],
            "source_hints": [],
        },
        "registry_diagnostics": {
            "latency_ms": 4.0,
            "attempts": 1,
            "http_status": 404,
            "retry_after": None,
            "rate_limit_remaining": None,
            "error": None,
        },
        "metadata_evidence": [],
        "similarity_candidates": [],
        "risk_score": 37.5,
        "severity": "medium",
        "remediation": "Verify the dependency name.",
        "source_locations": [
            {"file": "requirements.txt", "line": 2, "source_type": "requirements"}
        ],
    }
    original = candidate.copy()
    matches = find_lookalikes("reqeusts", make_index("requests"), SimilaritySettings())

    enriched = apply_similarity(candidate, matches)

    assert candidate == original
    assert enriched is not candidate
    assert enriched["registry_metadata"] is not candidate["registry_metadata"]
    assert enriched["source_locations"] is not candidate["source_locations"]
    assert enriched["risk_score"] == candidate["risk_score"]
    assert enriched["severity"] == candidate["severity"]
    for field in (
        "registry_exists",
        "registry_status",
        "registry_metadata",
        "registry_diagnostics",
    ):
        assert enriched[field] == candidate[field]  # type: ignore[literal-required]
    assert enriched["similarity_candidates"] == [
        {
            "name": "requests",
            "similarity": 0.875,
            "edit_distance": 1,
            "popularity_rank": 1,
            "match_kind": "edit",
            "registry_exists": True,
        }
    ]
    Draft202012Validator(load_result_schema()).validate(enriched)


def test_apply_similarity_requires_a_nonempty_requested_name() -> None:
    candidate: DependencyResult = {
        "contract_version": "1.1.0",
        "requested_name": "",
        "normalized_name": "pyyaml",
        "registry_exists": None,
        "metadata_evidence": [],
        "similarity_candidates": [],
        "risk_score": 0,
        "severity": "info",
        "remediation": "Review the dependency.",
    }

    with pytest.raises(ValueError, match="non-empty string"):
        apply_similarity(candidate, ())


@pytest.mark.slow
def test_packaged_index_lookup_average_is_under_fifty_milliseconds() -> None:
    from slopguard.similarity import PopularPackageIndex

    index = PopularPackageIndex.load()
    settings = SimilaritySettings()
    queries = (
        "reqeusts",
        "numpyy",
        "pandsa",
        "pythondateutil",
        "urllib4",
        "packagng",
        "pytestt",
        "request",
        "pyyaml",
        "requests",
        "fastapii",
        "django",
        "flaskk",
        "pydantc",
        "typing-extension",
        "setuptols",
        "beautifulsoup",
        "cryptograpy",
        "clickk",
        "numpy",
    )
    started = perf_counter()
    for query in queries:
        find_lookalikes(query, index, settings)
    average_seconds = (perf_counter() - started) / len(queries)

    assert average_seconds < 0.05


def test_weak_matches_are_reported_for_scoring_to_weigh() -> None:
    """These low-confidence matches should score below a distance-1 top-100 match in SG-05."""

    index = PopularPackageIndex.load()
    settings = SimilaritySettings()
    opencv_matches = find_lookalikes("opencv", index, settings)
    pydantic2_matches = find_lookalikes("pydantic2", index, settings)

    assert [(match.name, match.edit_distance, match.match_kind) for match in opencv_matches] == [
        ("opencc", 1, "edit")
    ]
    assert [(match.name, match.edit_distance) for match in pydantic2_matches] == [
        ("pydantic", 1),
        ("dydantic", 2),
    ]


def test_similarity_query_is_canonicalized() -> None:
    matches = find_lookalikes(
        "Python_DateutilX", make_index("python-dateutil"), SimilaritySettings()
    )

    assert matches[0].name == canonicalize_name("python-dateutil")
