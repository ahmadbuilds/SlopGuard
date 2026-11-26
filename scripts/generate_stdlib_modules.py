"""Emit or merge interpreter-specific standard-library module snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

GENERATOR_VERSION = "1"
# Keep this script compatible with Python 3.10; datetime.UTC is not available there.


def merge_stdlib_files(paths: list[Path], output: Path) -> None:
    """Write the deterministic union of emitted interpreter snapshots."""

    modules: set[str] = set()
    versions: set[str] = set()
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        names = document["modules"]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError(f"Invalid module list in {path}")
        modules.update(names)
        version = document.get("python_version")
        if isinstance(version, str):
            versions.add(version)
    document = {
        "python_versions": sorted(versions),
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(timezone.utc)  # noqa: UP017
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "modules": sorted(modules),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False, newline="\n"
        ) as stream:
            temporary_name = stream.name
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary_name, output)
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--emit", action="store_true")
    action.add_argument("--merge", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "src"
        / "slopguard"
        / "data"
        / "stdlib_modules.json",
    )
    args = parser.parse_args(argv)
    if args.emit:
        json.dump(
            {
                "python_version": ".".join(map(str, sys.version_info[:2])),
                "modules": sorted(sys.stdlib_module_names),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        merge_stdlib_files(args.merge, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
