"""Merge and validate builder JSONL files into the evaluation label set."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .constants import DEFAULT_LABELS_PATH
from .io import write_jsonl
from .schema import Label, load_labels, validate_labels


def merge_label_files(paths: tuple[Path, ...]) -> tuple[Label, ...]:
    """Load rows from each builder output, reject duplicate canonical names, and sort."""

    rows = [row for path in paths for row in load_labels(path)]
    return tuple(sorted(validate_labels(rows), key=lambda row: (row.name.casefold(), row.name)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=DEFAULT_LABELS_PATH)
    args = parser.parse_args(argv)
    labels = merge_label_files(tuple(args.inputs))
    write_jsonl(args.out, (label.to_dict() for label in labels))
    balance = Counter(label.label for label in labels)
    print(json.dumps({name: balance[name] for name in sorted(balance)}, sort_keys=True))
    print(f"Wrote {len(labels)} labels to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
