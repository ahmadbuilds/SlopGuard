"""Bounded deterministic JSONL input and output helpers for evaluation tools."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from .constants import MAX_INPUT_FILE_BYTES, MAX_INPUT_LINE_BYTES


def read_jsonl_objects(path: Path) -> tuple[dict[str, object], ...]:
    """Read JSONL objects with file and row size limits."""

    if path.stat().st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"{path} exceeds the safe input size limit")
    rows: list[dict[str, object]] = []
    with path.open("rb") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if len(raw_line) > MAX_INPUT_LINE_BYTES:
                raise ValueError(f"{path}:{line_number}: row exceeds the safe line limit")
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
                raise ValueError(f"{path}:{line_number}: invalid JSONL row") from error
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be a JSON object")
            rows.append(row)
    return tuple(rows)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    """Write rows in their supplied order with stable JSON key ordering."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            output.write("\n")
