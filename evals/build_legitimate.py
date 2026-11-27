"""Build rank-stratified legitimate labels from an evaluation popularity snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from slopguard.similarity import PopularPackageIndex, SimilaritySettings, find_lookalikes

from .constants import (
    DEFAULT_LEGITIMATE_LABELS_PATH,
    DEFAULT_LEGITIMATE_LOOKALIKE_COUNT,
    DEFAULT_LEGITIMATE_RANK_MAX,
    DEFAULT_LEGITIMATE_RANK_MIN,
    DEFAULT_LEGITIMATE_SAMPLE_COUNT,
    DEFAULT_RANDOM_SEED,
    DEFAULT_STRATA_COUNT,
)
from .io import read_jsonl_objects, write_jsonl
from .schema import Label, validate_labels


def build_legitimate_labels(
    index: PopularPackageIndex,
    *,
    count: int = DEFAULT_LEGITIMATE_SAMPLE_COUNT,
    lookalike_count: int = DEFAULT_LEGITIMATE_LOOKALIKE_COUNT,
    strata: int = DEFAULT_STRATA_COUNT,
    rank_min: int = DEFAULT_LEGITIMATE_RANK_MIN,
    rank_max: int = DEFAULT_LEGITIMATE_RANK_MAX,
    seed: int = DEFAULT_RANDOM_SEED,
    curated_path: Path | None = None,
    popular_sha256: str = "in-memory fixture",
    comparison_index: PopularPackageIndex | None = None,
    added_by: str = "slopguard-evals",
) -> tuple[Label, ...]:
    """Sample snapshot names evenly across popularity ranks and append curated examples."""

    if count < 0 or lookalike_count < 0 or strata <= 0 or rank_min <= 0 or rank_max < rank_min:
        raise ValueError("count/strata/rank range is invalid")
    if not popular_sha256.strip():
        raise ValueError("popular_sha256 must be non-empty")
    randomizer = random.Random(seed)
    packages = tuple(package for package in index.packages if rank_min <= package.rank <= rank_max)
    if count and not packages:
        raise ValueError(
            f"popular file contains no packages in ranks {rank_min} through {rank_max}"
        )
    package_count = len(packages)
    active_strata = min(strata, package_count)
    sampled: list[Label] = []
    sample_count = min(count, package_count)
    if active_strata:
        base_quota, remainder = divmod(sample_count, active_strata)
        bucket_bounds = [
            (
                stratum * package_count // active_strata,
                (stratum + 1) * package_count // active_strata,
            )
            for stratum in range(active_strata)
        ]
        desired_quotas = [base_quota + (stratum < remainder) for stratum in range(active_strata)]
        quotas = [
            min(quota, end - start)
            for quota, (start, end) in zip(desired_quotas, bucket_bounds, strict=True)
        ]
        leftover = sample_count - sum(quotas)
        for stratum, (start, end) in enumerate(bucket_bounds):
            additional = min(leftover, end - start - quotas[stratum])
            quotas[stratum] += additional
            leftover -= additional
        for stratum in range(active_strata):
            start, end = bucket_bounds[stratum]
            bucket = packages[start:end]
            quota = quotas[stratum]
            for package in randomizer.sample(bucket, min(quota, len(bucket))):
                sampled.append(
                    Label(
                        name=package.name,
                        label="legitimate",
                        expected="pass",
                        category=f"rank-stratum-{stratum + 1}",
                        rationale="Known package name selected from the evaluation popularity file.",
                        source=(
                            f"popular_file_sha256={popular_sha256}; rank_range={rank_min}-{rank_max}; "
                            f"rank={package.rank}; strata={active_strata}; "
                            f"sample_count={sample_count}; seed={seed}"
                        ),
                        added_by=added_by,
                        reviewed_by=None,
                    )
                )
    if lookalike_count:
        comparison = (
            comparison_index if comparison_index is not None else PopularPackageIndex.load()
        )
        already_sampled = {row.name for row in sampled}
        matches_by_name = {
            package.name: find_lookalikes(package.name, comparison, SimilaritySettings())
            for package in packages
            if package.name not in already_sampled
        }
        lookalike_candidates = [
            package for package in packages if matches_by_name.get(package.name)
        ]
        if len(lookalike_candidates) < lookalike_count:
            raise ValueError(
                f"requested {lookalike_count} lookalike negatives but only "
                f"{len(lookalike_candidates)} candidates exist in ranks {rank_min}-{rank_max}"
            )
        for package in randomizer.sample(lookalike_candidates, lookalike_count):
            match = matches_by_name[package.name][0]
            sampled.append(
                Label(
                    name=package.name,
                    label="legitimate",
                    expected="pass",
                    category="lookalike-legitimate",
                    rationale=(
                        f"Known package {package.name!r} resembles popular package "
                        f"{match.name!r} (rank {match.popularity_rank})."
                    ),
                    source=(
                        f"popular_file_sha256={popular_sha256}; rank_range={rank_min}-{rank_max}; "
                        f"rank={package.rank}; seed={seed}; count={lookalike_count}"
                    ),
                    added_by=added_by,
                    reviewed_by=None,
                )
            )
    if curated_path is not None:
        curated_rows = read_jsonl_objects(curated_path)
        for row_number, raw in enumerate(curated_rows, start=1):
            name = raw.get("name")
            rationale = raw.get("rationale")
            source = raw.get("source")
            category = raw.get("category", "curated-lookalike")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (name, rationale, source, category)
            ):
                raise ValueError(
                    f"{curated_path}:row {row_number}: name, rationale, source, and category are required"
                )
            sampled.append(
                Label(
                    name=name,
                    label="legitimate",
                    expected="pass",
                    category=category,
                    rationale=rationale,
                    source=(
                        f"{source}; popular_file_sha256={popular_sha256}; "
                        f"rank_range={rank_min}-{rank_max}; curated file; sample_count={sample_count}; "
                        f"curated_count={len(curated_rows)}; seed={seed}"
                    ),
                    added_by=added_by,
                    reviewed_by=None,
                )
            )
    return tuple(sorted(validate_labels(sampled), key=lambda row: (row.name.casefold(), row.name)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--count", type=int, default=DEFAULT_LEGITIMATE_SAMPLE_COUNT)
    parser.add_argument("--lookalike-count", type=int, default=DEFAULT_LEGITIMATE_LOOKALIKE_COUNT)
    parser.add_argument("--strata", type=int, default=DEFAULT_STRATA_COUNT)
    parser.add_argument("--popular-file", type=Path, required=True)
    parser.add_argument("--rank-min", type=int, default=DEFAULT_LEGITIMATE_RANK_MIN)
    parser.add_argument("--rank-max", type=int, default=DEFAULT_LEGITIMATE_RANK_MAX)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--curated", type=Path)
    parser.add_argument("--added-by", default="slopguard-evals")
    parser.add_argument("--out", type=Path, default=DEFAULT_LEGITIMATE_LABELS_PATH)
    args = parser.parse_args(argv)
    try:
        raw_snapshot = args.popular_file.read_bytes()
        snapshot = json.loads(raw_snapshot)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        parser.error(f"could not read popular file: {error}")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("packages"), list):
        parser.error("popular file must be a generated snapshot with a packages array")
    try:
        index = PopularPackageIndex.from_packages(snapshot["packages"])
    except ValueError as error:
        parser.error(f"invalid popular file: {error}")
    sha256 = hashlib.sha256(raw_snapshot).hexdigest()
    labels = build_legitimate_labels(
        index,
        count=args.count,
        lookalike_count=args.lookalike_count,
        strata=args.strata,
        rank_min=args.rank_min,
        rank_max=args.rank_max,
        seed=args.seed,
        curated_path=args.curated,
        popular_sha256=sha256,
        added_by=args.added_by,
    )
    write_jsonl(args.out, (row.to_dict() for row in labels))
    print(f"Wrote {len(labels)} legitimate labels to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
