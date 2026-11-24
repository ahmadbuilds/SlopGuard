"""Integrity checks for the packaged popular-package index."""

import json
from importlib.resources import files

from packaging.utils import canonicalize_name

from slopguard.similarity import PopularPackageIndex


def test_packaged_popular_snapshot_has_valid_header_and_expected_names() -> None:
    resource = files("slopguard").joinpath("data", "popular_packages.json")
    snapshot = json.loads(resource.read_text(encoding="utf-8"))
    index = PopularPackageIndex.load()

    for field in (
        "source_url",
        "source_last_update",
        "retrieved_at",
        "generator_version",
        "licence",
        "count",
        "packages",
    ):
        assert field in snapshot
    assert snapshot["count"] == len(snapshot["packages"])
    assert snapshot["count"] >= 5000
    assert len(index.packages) == snapshot["count"]
    assert all(package.name == canonicalize_name(package.name) for package in index.packages)
    assert {"requests", "numpy", "pandas"} <= {package.name for package in index.packages}
