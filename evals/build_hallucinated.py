"""Build hallucinated-package labels from a supplied JSONL of model outputs."""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from packaging.utils import canonicalize_name

from .constants import (
    DEFAULT_HALLUCINATED_LABELS_PATH,
    DEFAULT_HALLUCINATED_PATH,
    DEFAULT_HALLUCINATED_SAMPLE_COUNT,
    DEFAULT_RANDOM_SEED,
)
from .io import read_jsonl_objects, write_jsonl
from .schema import Label, validate_labels


def build_hallucinated_labels(
    path: Path,
    *,
    count: int = DEFAULT_HALLUCINATED_SAMPLE_COUNT,
    seed: int = DEFAULT_RANDOM_SEED,
    added_by: str = "slopguard-evals",
) -> tuple[Label, ...]:
    """Sample model-output package names without asserting whether they exist on PyPI."""

    if count < 0:
        raise ValueError("count must be non-negative")
    input_rows = read_jsonl_objects(path)
    package_rows: dict[str, tuple[str, str, str, str, set[str]]] = {}
    for row_number, raw in enumerate(input_rows, start=1):
        model, date, prompt, packages = (
            raw.get("model"),
            raw.get("date"),
            raw.get("prompt"),
            raw.get("packages"),
        )
        if not all(isinstance(value, str) and value.strip() for value in (model, date, prompt)):
            raise ValueError(f"{path}:row {row_number}: model, date, and prompt are required")
        if not isinstance(packages, list) or any(
            not isinstance(name, str) or not name.strip() for name in packages
        ):
            raise ValueError(f"{path}:row {row_number}: packages must be non-empty strings")
        for name in packages:
            canonical = canonicalize_name(name, validate=True)
            if canonical not in package_rows:
                package_rows[canonical] = (name, model, date, prompt, {model})
            else:
                package_rows[canonical][4].add(model)
    selected_count = min(count, len(package_rows))
    selected_rows = random.Random(seed).sample(
        sorted(package_rows.values(), key=lambda row: canonicalize_name(row[0])), selected_count
    )
    candidates = [
        Label(
            name=name,
            label="hallucinated",
            expected="flag",
            category="llm-package-output",
            rationale="Produced as a package name by a recorded model response; registry existence is evaluated separately.",
            source=(
                f"LLM model={model}; date={date}; prompt={prompt}; "
                f"models_count={len(models)}; sample_count={selected_count}; seed={seed}"
            ),
            added_by=added_by,
            reviewed_by=None,
            models=tuple(sorted(models)),
        )
        for name, model, date, prompt, models in selected_rows
    ]
    return tuple(
        sorted(validate_labels(candidates), key=lambda row: (row.name.casefold(), row.name))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--outputs", type=Path, default=DEFAULT_HALLUCINATED_PATH)
    parser.add_argument("--count", type=int, default=DEFAULT_HALLUCINATED_SAMPLE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--added-by", default="slopguard-evals")
    parser.add_argument("--out", type=Path, default=DEFAULT_HALLUCINATED_LABELS_PATH)
    args = parser.parse_args(argv)
    labels = build_hallucinated_labels(
        args.outputs, count=args.count, seed=args.seed, added_by=args.added_by
    )
    write_jsonl(args.out, (row.to_dict() for row in labels))
    print(f"Wrote {len(labels)} hallucinated labels to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
