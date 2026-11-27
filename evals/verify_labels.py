"""Verify evaluation labels against the local primary-source recordings."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

from packaging.utils import canonicalize_name

from slopguard.similarity import PopularPackageIndex

from .build_synthetic import build_synthetic_labels
from .constants import (
    AUTOMATED_LABEL_REVIEWER,
    DEFAULT_HALLUCINATED_PATH,
    DEFAULT_LABELS_PATH,
    DEFAULT_REGISTRY_PATH,
    OSV_ADVISORY_GLOB,
    OSV_ADVISORY_RELATIVE_PATH,
)
from .io import read_jsonl_objects
from .replay import ReplayClient
from .schema import Label, load_labels

_SOURCE_FIELD = re.compile(r"(?:^|[; ])([A-Za-z_][A-Za-z_0-9]*)=([^;]*)")


def _source_fields(source: str) -> dict[str, str]:
    return {key: value for key, value in _SOURCE_FIELD.findall(source)}


def _osv_packages(osv_dir: Path) -> tuple[dict[str, object], set[str]]:
    advisory_root = osv_dir / OSV_ADVISORY_RELATIVE_PATH
    if not advisory_root.is_dir():
        raise ValueError(f"missing OSV advisory directory: {advisory_root}")
    documents: dict[str, dict[str, object]] = {}
    packages: set[str] = set()
    for path in sorted(advisory_root.rglob(OSV_ADVISORY_GLOB)):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError(f"could not read OSV advisory: {path}") from error
        if not isinstance(document, dict):
            continue
        if path.stem.startswith("MAL-"):
            documents[path.stem] = document
        affected = document.get("affected", [])
        if isinstance(affected, list):
            for item in affected:
                package = item.get("package") if isinstance(item, dict) else None
                if (
                    isinstance(package, dict)
                    and package.get("ecosystem") == "PyPI"
                    and isinstance(package.get("name"), str)
                ):
                    packages.add(canonicalize_name(package["name"], validate=True))
    return documents, packages


def _verify_malicious(
    label: Label, fields: dict[str, str], documents: dict[str, dict[str, object]]
) -> None:
    advisory_match = re.search(r"(?:^|; )OSV ([^;]+)", label.source)
    advisory_id = advisory_match.group(1).strip() if advisory_match else ""
    document = documents.get(advisory_id)
    if document is None:
        raise ValueError(f"OSV advisory {advisory_id or '(missing id)'} was not found")
    if document.get("id") != advisory_id:
        raise ValueError(f"OSV document id does not match {advisory_id}")
    for affected in document.get("affected", []):
        package = affected.get("package") if isinstance(affected, dict) else None
        if (
            isinstance(package, dict)
            and package.get("ecosystem") == "PyPI"
            and isinstance(package.get("name"), str)
            and canonicalize_name(package["name"], validate=True)
            == canonicalize_name(label.name, validate=True)
        ):
            return
    raise ValueError(f"OSV {advisory_id} has no affected PyPI package matching the label")


def _verify_synthetic(label: Label, fields: dict[str, str], registry: ReplayClient) -> None:
    if registry.lookup(label.name).status == "exists":
        raise ValueError("recording status is exists")
    try:
        top_k = int(fields["top_k"])
        seed = int(fields["seed"])
    except (KeyError, ValueError) as error:
        raise ValueError("source is missing synthetic top_k or seed") from error
    generated = build_synthetic_labels(PopularPackageIndex.load(), top_k=top_k, seed=seed)
    canonical = canonicalize_name(label.name, validate=True)
    if not any(
        canonicalize_name(row.name, validate=True) == canonical and row.category == label.category
        for row in generated
    ):
        raise ValueError("seeded synthetic generator did not reproduce this name and category")


def _verify_legitimate(
    label: Label,
    fields: dict[str, str],
    popular_index: PopularPackageIndex,
    registry: ReplayClient,
    osv_packages: set[str],
    popular_sha256: str,
) -> None:
    if fields.get("popular_file_sha256") != popular_sha256:
        raise ValueError("source popularity-file SHA256 does not match the supplied snapshot")
    try:
        expected_rank = int(fields["rank"])
        rank_min, rank_max = (int(value) for value in fields["rank_range"].split("-", 1))
    except (KeyError, ValueError) as error:
        raise ValueError("source is missing the popularity rank or rank window") from error
    actual_rank = popular_index.rank_of(canonicalize_name(label.name, validate=True))
    if actual_rank != expected_rank:
        raise ValueError(f"popularity rank is {actual_rank}, expected {expected_rank}")
    if not rank_min <= actual_rank <= rank_max:
        raise ValueError(f"popularity rank {actual_rank} is outside the recorded rank window")
    if registry.lookup(label.name).status != "exists":
        raise ValueError("recording status is not exists")
    if canonicalize_name(label.name, validate=True) in osv_packages:
        raise ValueError("package appears in the OSV PyPI malicious dataset")


def _verify_hallucinated(
    label: Label, fields: dict[str, str], outputs: tuple[dict[str, object], ...]
) -> None:
    model, date, prompt = fields.get("model"), fields.get("date"), fields.get("prompt")
    if not all((model, date, prompt)):
        raise ValueError("source is missing model, date, or prompt")
    canonical = canonicalize_name(label.name, validate=True)

    def contains(row: dict[str, object], expected_model: str) -> bool:
        packages = row.get("packages")
        return (
            row.get("model") == expected_model
            and isinstance(packages, list)
            and any(
                isinstance(name, str) and canonicalize_name(name, validate=True) == canonical
                for name in packages
            )
        )

    first_match = any(
        row.get("model") == model
        and row.get("date") == date
        and row.get("prompt") == prompt
        and contains(row, model)
        for row in outputs
    )
    if not first_match:
        raise ValueError("recorded model/date/prompt output does not contain this name")
    for suggested_model in label.models or (model,):
        if not any(contains(row, suggested_model) for row in outputs):
            raise ValueError(
                f"recorded outputs do not show this name suggested by {suggested_model}"
            )


def verify_labels(
    labels_path: Path,
    registry_path: Path,
    osv_dir: Path,
    popular_file: Path,
    outputs_path: Path,
    out_path: Path,
) -> tuple[tuple[Label, ...], dict[str, dict[str, int]], tuple[str, ...]]:
    """Verify eligible rows and atomically write retained labels with review status."""
    labels = load_labels(labels_path)
    registry = ReplayClient(registry_path)
    documents, osv_packages = _osv_packages(osv_dir)
    snapshot = json.loads(popular_file.read_text(encoding="utf-8"))
    popular_sha256 = hashlib.sha256(popular_file.read_bytes()).hexdigest()
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("packages"), list):
        raise ValueError("popular file must contain a packages array")
    popular_index = PopularPackageIndex.from_packages(snapshot["packages"])
    outputs = read_jsonl_objects(outputs_path)
    updated: list[Label] = []
    totals: Counter[str] = Counter()
    failures: list[str] = []
    for label in labels:
        if label.label == "borderline" or label.expected == "exclude":
            updated.append(label)
            continue
        class_name = label.label
        totals[f"{class_name}:checked"] += 1
        reason: str | None = None
        fields = _source_fields(label.source)
        try:
            if label.label == "malicious":
                _verify_malicious(label, fields, documents)
            elif label.label == "typosquat_synthetic":
                _verify_synthetic(label, fields, registry)
            elif label.label == "legitimate":
                _verify_legitimate(
                    label, fields, popular_index, registry, osv_packages, popular_sha256
                )
            elif label.label == "hallucinated":
                _verify_hallucinated(label, fields, outputs)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            reason = str(error)
            failures.append(f"{label.name}: {reason}")
        if reason is None:
            totals[f"{class_name}:passed"] += 1
            updated.append(replace(label, reviewed_by=AUTOMATED_LABEL_REVIEWER))
        else:
            totals[f"{class_name}:failed"] += 1
            updated.append(
                Label(
                    name=label.name,
                    label="borderline",
                    expected="exclude",
                    category=f"{class_name}:{label.category}",
                    rationale=f"{label.rationale} verification failed: {reason}",
                    source=label.source,
                    added_by=label.added_by,
                    reviewed_by=AUTOMATED_LABEL_REVIEWER,
                    models=label.models,
                )
            )
    _atomic_write_labels(out_path, updated)
    by_class = {
        class_name: {
            "checked": totals[f"{class_name}:checked"],
            "passed": totals[f"{class_name}:passed"],
            "failed": totals[f"{class_name}:failed"],
        }
        for class_name in sorted({key.split(":", 1)[0] for key in totals})
    }
    return tuple(updated), by_class, tuple(failures)


def _atomic_write_labels(path: Path, labels: list[Label]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            for label in labels:
                stream.write(
                    json.dumps(label.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
                )
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--osv-dir", type=Path, required=True)
    parser.add_argument("--popular-file", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, default=DEFAULT_HALLUCINATED_PATH)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _labels, summary, failures = verify_labels(
            args.labels, args.registry, args.osv_dir, args.popular_file, args.outputs, args.out
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for class_name, counts in summary.items():
        print(
            f"{class_name}: checked={counts['checked']} passed={counts['passed']} "
            f"failed={counts['failed']}"
        )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
