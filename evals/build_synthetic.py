"""Build seeded one-edit synthetic typosquats from the packaged popularity snapshot."""

from __future__ import annotations

import argparse
import random
import string
from pathlib import Path

from packaging.utils import canonicalize_name

from slopguard.similarity import PopularPackageIndex, SimilaritySettings

from .constants import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_SYNTHETIC_LABELS_PATH,
    DEFAULT_SYNTHETIC_TOP_K,
)
from .io import write_jsonl
from .schema import Label, validate_labels

EDIT_KINDS = ("transposition", "deletion", "insertion", "substitution", "separator change")
INSERTION_ALPHABET = string.ascii_lowercase + string.digits


def build_synthetic_labels(
    index: PopularPackageIndex,
    *,
    top_k: int = DEFAULT_SYNTHETIC_TOP_K,
    seed: int = DEFAULT_RANDOM_SEED,
    settings: SimilaritySettings | None = None,
    added_by: str = "slopguard-evals",
) -> tuple[Label, ...]:
    """Apply one seeded edit to each selected popular name and discard snapshot names."""

    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    randomizer = random.Random(seed)
    similarity_settings = settings if settings is not None else SimilaritySettings()
    snapshot_names = {package.name for package in index.packages}
    generated: dict[str, Label] = {}
    selected = index.packages[:top_k]
    for package in selected:
        if len(package.name) < similarity_settings.min_length + 1:
            continue
        edit_kind = randomizer.choice(EDIT_KINDS)
        typo, rationale, actual_kind = _apply_edit(package.name, edit_kind, randomizer)
        if len(typo) < similarity_settings.min_length:
            continue
        normalized = canonicalize_name(typo, validate=True)
        if normalized in snapshot_names or normalized in generated:
            continue
        generated[normalized] = Label(
            name=typo,
            label="typosquat_synthetic",
            expected="flag",
            category=f"synthetic-{actual_kind.replace(' ', '-')}",
            rationale=f"Seeded {rationale} of popular package {package.name!r}.",
            source=f"popular snapshot top_k={len(selected)}; seed={seed}",
            added_by=added_by,
            reviewed_by=None,
        )
    return tuple(
        sorted(validate_labels(generated.values()), key=lambda row: (row.name.casefold(), row.name))
    )


def _apply_edit(name: str, kind: str, randomizer: random.Random) -> tuple[str, str, str]:
    if kind == "transposition":
        positions = [index for index in range(len(name) - 1) if name[index] != name[index + 1]]
        if positions:
            position = randomizer.choice(positions)
            return (
                name[:position] + name[position + 1] + name[position] + name[position + 2 :],
                "adjacent transposition",
                "transposition",
            )
        return _apply_edit(name, "substitution", randomizer)
    if kind == "deletion":
        if len(name) <= 1:
            return _apply_edit(name, "substitution", randomizer)
        position = randomizer.randrange(len(name))
        return name[:position] + name[position + 1 :], "character deletion", "deletion"
    if kind == "insertion":
        position = randomizer.randrange(len(name) + 1)
        character = randomizer.choice(INSERTION_ALPHABET)
        return (
            name[:position] + character + name[position:],
            f"insertion of {character!r}",
            "insertion",
        )
    if kind == "substitution":
        position = randomizer.randrange(len(name))
        replacement = randomizer.choice(INSERTION_ALPHABET.replace(name[position], ""))
        return (
            name[:position] + replacement + name[position + 1 :],
            "character substitution",
            "substitution",
        )
    if kind == "separator change":
        separators = [index for index, character in enumerate(name) if character == "-"]
        if separators:
            position = randomizer.choice(separators)
            return name[:position] + name[position + 1 :], "separator deletion", "separator-change"
        if len(name) > 1:
            position = randomizer.randrange(1, len(name))
            return (
                name[:position] + "-" + name[position:],
                "separator insertion",
                "separator-change",
            )
        return _apply_edit(name, "insertion", randomizer)
    raise ValueError(f"unsupported synthetic edit: {kind}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--top-k", type=int, default=DEFAULT_SYNTHETIC_TOP_K)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--added-by", default="slopguard-evals")
    parser.add_argument("--out", type=Path, default=DEFAULT_SYNTHETIC_LABELS_PATH)
    args = parser.parse_args(argv)
    labels = build_synthetic_labels(
        PopularPackageIndex.load(), top_k=args.top_k, seed=args.seed, added_by=args.added_by
    )
    write_jsonl(args.out, (row.to_dict() for row in labels))
    print(f"Wrote {len(labels)} synthetic labels to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
