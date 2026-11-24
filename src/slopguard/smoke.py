"""Validate the checked-in sample against the dependency-result schema."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "dependency-result.schema.json"
SAMPLE_PATH = ROOT / "examples" / "result.json"


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(sample),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            print(f"{location}: {error.message}")
        return 1

    print("Dependency result schema and sample are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
