"""Build malicious-package labels from a local OSSF malicious-packages checkout."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from packaging.utils import canonicalize_name

from .constants import (
    DEFAULT_OSV_LABELS_PATH,
    DEFAULT_OSV_SAMPLE_COUNT,
    DEFAULT_RANDOM_SEED,
    MAX_OSV_ADVISORY_BYTES,
    OSV_ADVISORY_GLOB,
    OSV_ADVISORY_RELATIVE_PATH,
)
from .io import write_jsonl
from .schema import Label, validate_labels


def build_osv_labels(
    osv_dir: Path,
    *,
    count: int = DEFAULT_OSV_SAMPLE_COUNT,
    seed: int = DEFAULT_RANDOM_SEED,
    added_by: str = "slopguard-evals",
) -> tuple[Label, ...]:
    """Sample unique PyPI names from local OSV advisories, deterministically."""

    if count < 0:
        raise ValueError("count must be non-negative")
    advisory_root = osv_dir / OSV_ADVISORY_RELATIVE_PATH
    records: dict[str, tuple[str, str]] = {}
    for path in sorted(advisory_root.rglob(OSV_ADVISORY_GLOB)):
        if path.stat().st_size > MAX_OSV_ADVISORY_BYTES:
            raise ValueError(f"OSV advisory exceeds the safe file size: {path}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError(f"could not read OSV advisory {path}") from error
        if not isinstance(document, dict):
            continue
        advisory_id = document.get("id", path.stem)
        if not isinstance(advisory_id, str) or not advisory_id.startswith("MAL-"):
            advisory_id = path.stem
        affected = document.get("affected", [])
        if not isinstance(affected, list):
            continue
        for item in affected:
            package = item.get("package") if isinstance(item, dict) else None
            if not isinstance(package, dict) or package.get("ecosystem") != "PyPI":
                continue
            name = package.get("name")
            if isinstance(name, str) and name.strip():
                records.setdefault(canonicalize_name(name, validate=True), (name, advisory_id))

    selected = random.Random(seed).sample(
        sorted(records.values(), key=lambda item: (item[0].casefold(), item[1])),
        min(count, len(records)),
    )
    labels = [
        Label(
            name=name,
            label="malicious",
            expected="flag",
            category="malicious-package",
            rationale=f"Listed as an affected PyPI package in advisory {advisory_id}.",
            source=f"OSV {advisory_id}; sample_count={len(selected)}; seed={seed}",
            added_by=added_by,
            reviewed_by=None,
        )
        for name, advisory_id in selected
    ]
    return tuple(sorted(validate_labels(labels), key=lambda row: (row.name.casefold(), row.name)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--osv-dir", type=Path, required=True, help="local malicious-packages checkout"
    )
    parser.add_argument("--count", type=int, default=DEFAULT_OSV_SAMPLE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--added-by", default="slopguard-evals")
    parser.add_argument("--out", type=Path, default=DEFAULT_OSV_LABELS_PATH)
    args = parser.parse_args(argv)
    labels = build_osv_labels(
        args.osv_dir, count=args.count, seed=args.seed, added_by=args.added_by
    )
    write_jsonl(args.out, (row.to_dict() for row in labels))
    print(f"Wrote {len(labels)} malicious labels to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
