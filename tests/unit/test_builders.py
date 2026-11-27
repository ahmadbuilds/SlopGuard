"""Determinism and fixture coverage for offline evaluation data builders."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evals.build_hallucinated import build_hallucinated_labels
from evals.build_legitimate import build_legitimate_labels
from evals.build_legitimate import main as build_legitimate_main
from evals.build_osv import build_osv_labels
from evals.build_synthetic import build_synthetic_labels
from evals.merge_labels import merge_label_files
from slopguard.similarity import PopularPackageIndex, SimilaritySettings

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/evals"


def test_osv_builder_samples_pypi_rows_from_three_fixture_files() -> None:
    root = FIXTURES / "osv"

    labels = build_osv_labels(root, count=2, seed=7)

    assert len(labels) == 2
    assert all(row.label == "malicious" and row.expected == "flag" for row in labels)
    assert all("OSV MAL-FIXTURE-" in row.source and "seed=7" in row.source for row in labels)
    assert labels == build_osv_labels(root, count=2, seed=7)


def test_synthetic_builder_is_seeded_and_drops_snapshot_names() -> None:
    index = PopularPackageIndex.from_packages(
        [
            {"name": name, "rank": rank}
            for rank, name in enumerate(
                ("a", "bravo", "charlie", "delta", "echo", "foxtrot"), start=1
            )
        ]
    )

    labels = build_synthetic_labels(index, top_k=6, seed=19)

    assert labels == build_synthetic_labels(index, top_k=6, seed=19)
    assert all(row.label == "typosquat_synthetic" for row in labels)
    assert all("seed=19" in row.source and "top_k=6" in row.source for row in labels)
    assert all(row.name not in {package.name for package in index.packages} for row in labels)
    assert all(row.rationale for row in labels)
    assert all(len(row.name) >= 4 for row in labels)


def test_synthetic_builder_uses_similarity_minimum_length() -> None:
    index = PopularPackageIndex.from_packages([{"name": "fixture-name", "rank": 1}])

    labels = build_synthetic_labels(
        index, top_k=1, seed=19, settings=SimilaritySettings(min_length=12)
    )

    assert labels == ()


def test_legitimate_builder_stratifies_and_appends_curated_rows() -> None:
    index = PopularPackageIndex.from_packages(
        [{"name": f"fixture-package-{rank}", "rank": rank} for rank in range(1, 9)]
    )

    labels = build_legitimate_labels(
        index,
        count=4,
        lookalike_count=0,
        strata=2,
        rank_min=1,
        rank_max=8,
        seed=5,
        curated_path=FIXTURES / "curated.jsonl",
        popular_sha256="fixture-sha256",
    )

    assert len(labels) == 5
    assert {row.category for row in labels if row.category.startswith("rank-")} == {
        "rank-stratum-1",
        "rank-stratum-2",
    }
    assert any(row.category == "curated-lookalike" for row in labels)
    assert all("seed=5" in row.source and "sample_count=4" in row.source for row in labels)
    assert all("popular_file_sha256=fixture-sha256" in row.source for row in labels)
    assert all("rank_range=1-8" in row.source for row in labels)


def test_legitimate_cli_uses_out_of_comparison_rank_window_and_records_hash(
    tmp_path: Path,
) -> None:
    popular_file = tmp_path / "popular.json"
    popular_file.write_text(
        json.dumps(
            {
                "packages": [
                    {"name": f"fixture-package-{rank}", "rank": rank} for rank in range(1, 9)
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "legitimate.jsonl"

    assert (
        build_legitimate_main(
            [
                "--popular-file",
                str(popular_file),
                "--rank-min",
                "3",
                "--rank-max",
                "8",
                "--count",
                "4",
                "--lookalike-count",
                "0",
                "--strata",
                "2",
                "--seed",
                "5",
                "--out",
                str(output),
            ]
        )
        == 0
    )

    labels = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(labels) == 4
    assert all(3 <= int(row["name"].rsplit("-", maxsplit=1)[1]) <= 8 for row in labels)
    assert all("rank_range=3-8" in row["source"] for row in labels)
    assert all(
        f"popular_file_sha256={hashlib.sha256(popular_file.read_bytes()).hexdigest()}"
        in row["source"]
        for row in labels
    )


def test_hallucinated_builder_keeps_model_provenance_and_seed() -> None:
    labels = build_hallucinated_labels(FIXTURES / "model_outputs.jsonl", count=1, seed=3)

    assert len(labels) == 1
    assert labels[0].label == "hallucinated"
    assert "model=fixture-model" in labels[0].source
    assert "date=2026-01-01" in labels[0].source
    assert "prompt=fixture prompt" in labels[0].source
    assert "sample_count=1" in labels[0].source and "seed=3" in labels[0].source


def test_legitimate_lookalike_stratum_is_seeded_disjoint_and_records_match() -> None:
    index = PopularPackageIndex.from_packages(
        [
            {"name": name, "rank": rank}
            for rank, name in enumerate(("fixture-one", "reqeusts", "requestx", "otherthing"), 1)
        ]
    )
    comparison = PopularPackageIndex.from_packages([{"name": "requests", "rank": 1}])

    kwargs = {
        "count": 1,
        "lookalike_count": 1,
        "strata": 1,
        "rank_min": 1,
        "rank_max": 4,
        "seed": 41,
        "popular_sha256": "fixture-digest",
        "comparison_index": comparison,
    }
    first = build_legitimate_labels(index, **kwargs)
    second = build_legitimate_labels(index, **kwargs)
    strata = [row for row in first if row.category.startswith("rank-stratum-")]
    lookalikes = [row for row in first if row.category == "lookalike-legitimate"]
    assert first == second
    assert len(strata) == len(lookalikes) == 1
    assert strata[0].name != lookalikes[0].name
    assert "requests" in lookalikes[0].rationale and "rank 1" in lookalikes[0].rationale
    assert "popular_file_sha256=fixture-digest" in lookalikes[0].source
    assert "rank_range=1-4" in lookalikes[0].source
    assert "seed=41" in lookalikes[0].source and "count=1" in lookalikes[0].source


def test_legitimate_lookalike_stratum_fails_when_short() -> None:
    index = PopularPackageIndex.from_packages([{"name": "ordinary-package", "rank": 1}])
    comparison = PopularPackageIndex.from_packages([{"name": "requests", "rank": 1}])
    with pytest.raises(ValueError, match="only 0 candidates exist"):
        build_legitimate_labels(
            index,
            count=0,
            lookalike_count=1,
            rank_min=1,
            rank_max=1,
            comparison_index=comparison,
        )


def test_hallucinated_builder_merges_shared_names_with_sorted_models(tmp_path: Path) -> None:
    model_outputs = tmp_path / "model-outputs.jsonl"
    model_outputs.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "model": "model-z",
                        "date": "2026-02-01",
                        "prompt": "first prompt",
                        "packages": ["fixture-shared-package", "fixture-only-z"],
                    }
                ),
                json.dumps(
                    {
                        "model": "model-a",
                        "date": "2026-02-02",
                        "prompt": "second prompt",
                        "packages": ["fixture-shared-package", "fixture-only-a"],
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    labels = build_hallucinated_labels(model_outputs, count=10, seed=13)

    shared = next(label for label in labels if label.name == "fixture-shared-package")
    assert shared.models == ("model-a", "model-z")
    assert "LLM model=model-z; date=2026-02-01; prompt=first prompt; models_count=2;" in (
        shared.source
    )
    assert labels == build_hallucinated_labels(model_outputs, count=10, seed=13)
    assert len({label.name for label in labels}) == len(labels)


def test_merge_builder_rejects_duplicate_canonical_names(tmp_path: Path) -> None:
    fixture_labels = ROOT / "tests/fixtures/evals/labels.jsonl"
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        '{"added_by":"fixture","category":"duplicate","expected":"pass",'
        '"label":"legitimate","name":"FIXTURE-EXISTING-EVAL-PACKAGE","rationale":"fixture",'
        '"reviewed_by":null,"source":"fixture"}\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match="duplicate canonical package name: fixture-existing-eval-package"
    ):
        merge_label_files((fixture_labels, duplicate))
