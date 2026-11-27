"""Label definition and strict JSONL loading for evaluation datasets."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from packaging.utils import InvalidName, canonicalize_name

from .constants import (
    MAX_INPUT_FILE_BYTES,
    MAX_INPUT_LINE_BYTES,
    MAX_LABEL_COUNT,
    MAX_LABEL_FIELD_CHARACTERS,
)

LabelKind = Literal[
    "malicious",
    "hallucinated",
    "typosquat_synthetic",
    "legitimate",
    "borderline",
]
Expected = Literal["flag", "pass", "exclude"]
LABEL_KINDS = frozenset(
    {"malicious", "hallucinated", "typosquat_synthetic", "legitimate", "borderline"}
)
EXPECTED_VALUES = frozenset({"flag", "pass", "exclude"})
LABEL_FIELDS = frozenset(
    {"name", "label", "expected", "category", "rationale", "source", "added_by", "reviewed_by"}
)
OPTIONAL_LABEL_FIELDS = frozenset({"models"})


@dataclass(frozen=True, slots=True)
class Label:
    """One human-auditable package label used in offline evaluation."""

    name: str
    label: LabelKind
    expected: Expected
    category: str
    rationale: str
    source: str
    added_by: str
    reviewed_by: str | None
    models: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Serialize this label with stable field names."""

        row: dict[str, object] = {
            "name": self.name,
            "label": self.label,
            "expected": self.expected,
            "category": self.category,
            "rationale": self.rationale,
            "source": self.source,
            "added_by": self.added_by,
            "reviewed_by": self.reviewed_by,
        }
        if self.models:
            row["models"] = sorted(set(self.models))
        return row

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Label:
        """Validate and construct one label row."""

        keys = set(value)
        if keys != LABEL_FIELDS and keys != LABEL_FIELDS | OPTIONAL_LABEL_FIELDS:
            raise ValueError("label row has missing or unknown fields")
        name = value["name"]
        label = value["label"]
        expected = value["expected"]
        category = value["category"]
        rationale = value["rationale"]
        source = value["source"]
        added_by = value["added_by"]
        reviewed_by = value["reviewed_by"]
        models_value = value.get("models", [])
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty package name")
        try:
            canonicalize_name(name, validate=True)
        except InvalidName as error:
            raise ValueError("name is not a valid package name") from error
        if not isinstance(label, str) or label not in LABEL_KINDS:
            raise ValueError(f"label must be one of {', '.join(sorted(LABEL_KINDS))}")
        if not isinstance(expected, str) or expected not in EXPECTED_VALUES:
            raise ValueError(f"expected must be one of {', '.join(sorted(EXPECTED_VALUES))}")
        if label == "borderline" and expected != "exclude":
            raise ValueError("borderline labels must have expected='exclude'")
        for field_name, field_value in (
            ("category", category),
            ("rationale", rationale),
            ("source", source),
            ("added_by", added_by),
        ):
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            if len(field_value) > MAX_LABEL_FIELD_CHARACTERS:
                raise ValueError(f"{field_name} exceeds {MAX_LABEL_FIELD_CHARACTERS} characters")
        if len(name) > MAX_LABEL_FIELD_CHARACTERS:
            raise ValueError(f"name exceeds {MAX_LABEL_FIELD_CHARACTERS} characters")
        if reviewed_by is not None and (
            not isinstance(reviewed_by, str) or not reviewed_by.strip()
        ):
            raise ValueError("reviewed_by must be a non-empty string or null")
        if isinstance(reviewed_by, str) and len(reviewed_by) > MAX_LABEL_FIELD_CHARACTERS:
            raise ValueError(f"reviewed_by exceeds {MAX_LABEL_FIELD_CHARACTERS} characters")
        if not isinstance(models_value, list) or any(
            not isinstance(model, str)
            or not model.strip()
            or len(model) > MAX_LABEL_FIELD_CHARACTERS
            for model in models_value
        ):
            raise ValueError("models must be a list of non-empty strings")
        return cls(
            name=name,
            label=label,  # type: ignore[arg-type]
            expected=expected,  # type: ignore[arg-type]
            category=category,
            rationale=rationale,
            source=source,
            added_by=added_by,
            reviewed_by=reviewed_by,
            models=tuple(sorted(set(models_value))),
        )


def validate_labels(rows: Iterable[Label]) -> tuple[Label, ...]:
    """Reject duplicate canonical package names and return the stable row tuple."""

    labels = tuple(rows)
    if len(labels) > MAX_LABEL_COUNT:
        raise ValueError(f"label dataset exceeds {MAX_LABEL_COUNT} rows")
    seen: set[str] = set()
    for row in labels:
        Label.from_dict(row.to_dict())
        canonical = canonicalize_name(row.name, validate=True)
        if canonical in seen:
            raise ValueError(f"duplicate canonical package name: {canonical}")
        seen.add(canonical)
    return labels


def load_labels(path: Path) -> tuple[Label, ...]:
    """Read a bounded JSONL label file and validate all rows and canonical names."""

    if path.stat().st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"label file exceeds {MAX_INPUT_FILE_BYTES} bytes")
    rows: list[Label] = []
    with path.open("rb") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if len(raw_line) > MAX_INPUT_LINE_BYTES:
                raise ValueError(f"{path}:{line_number}: row exceeds the safe line limit")
            if not raw_line.strip():
                continue
            try:
                decoded = json.loads(raw_line)
                if not isinstance(decoded, dict):
                    raise ValueError("row must be a JSON object")
                rows.append(Label.from_dict(decoded))
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
    return validate_labels(rows)
