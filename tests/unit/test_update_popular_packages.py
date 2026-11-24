"""Tests for transforming the recorded popular-package source format."""

from datetime import UTC, datetime

from update_popular_packages import transform_popular_packages


def test_transform_canonicalizes_deduplicates_and_limits_rows() -> None:
    payload = """
    {
      "last_update": "2026-11-01T00:00:00Z",
      "rows": [
        {"project": "Requests", "download_count": 100},
        {"project": "requests", "download_count": 90},
        {"project": "My_Pkg", "download_count": 80},
        {"project": "PANDAS", "download_count": 70}
      ]
    }
    """
    retrieved_at = datetime(2026, 11, 25, 12, 30, tzinfo=UTC)

    snapshot = transform_popular_packages(
        payload,
        source_url="https://example.test/popular.json",
        retrieved_at=retrieved_at,
        limit=2,
    )

    assert snapshot == {
        "source_url": "https://example.test/popular.json",
        "source_last_update": "2026-11-01T00:00:00Z",
        "retrieved_at": "2026-11-25T12:30:00Z",
        "generator_version": "1",
        "licence": "not stated by source",
        "count": 2,
        "packages": [
            {"name": "requests", "rank": 1},
            {"name": "my-pkg", "rank": 2},
        ],
    }
