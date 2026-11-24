"""Validate the checked-in sample against the dependency-result schema."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "dependency-result.schema.json"
SAMPLE_PATHS = (
    ROOT / "examples" / "result.json",
    ROOT / "examples" / "extracted-dependency.json",
)


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for sample_path in SAMPLE_PATHS:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        errors = sorted(
            validator.iter_errors(sample),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
        if errors:
            for error in errors:
                location = ".".join(str(part) for part in error.absolute_path) or "$"
                print(f"{sample_path.relative_to(ROOT)}:{location}: {error.message}")
            return 1

    print(f"Dependency result schema and {len(SAMPLE_PATHS)} examples are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
