"""Manually generate import-name data by inspecting published wheel metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import tempfile
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from _http import HTTPFetchError, get, get_range
from packaging.utils import canonicalize_name

GENERATOR_VERSION = "1"
DEFAULT_CONCURRENCY = 8
MAX_CENTRAL_DIRECTORY_BYTES = 8_000_000
MAX_CENTRAL_DIRECTORY_ENTRIES = 100_000
MAX_TOP_LEVEL_BYTES = 64_000
MAX_WHEEL_ENTRY_COMPRESSED_BYTES = 1_000_000
MAX_WHEEL_ENTRY_DECOMPRESSED_BYTES = 1_000_000
MAX_EOCD_TAIL_BYTES = 65_557
MAX_NAMESPACE_DEPTH = 12
NAMESPACE_MIN_PROVIDERS = 2
# These names commonly describe bundled support files rather than importable public modules.
IGNORED_TOP_LEVEL = frozenset(
    {
        "tests",
        "test",
        "testing",
        "docs",
        "doc",
        "examples",
        "example",
        "benchmarks",
        "scripts",
        "src",
        "lib",
        "include",
        "node_modules",
        "bin",
        "share",
        "build",
        "dist",
    }
)
_EOCD = b"PK\x05\x06"
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_EOCD = b"PK\x06\x06"
_CENTRAL_HEADER = struct.Struct("<4s6H3I5H2I")
_LOCAL_HEADER = struct.Struct("<4s5H3I2H")
RangeReader = Callable[[int, int], bytes]


class WheelScanError(ValueError):
    """A wheel range directory or selected metadata entry was invalid or over limits."""


class PackageScanError(ValueError):
    """A package metadata request failed with a stable skip reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WheelFacts:
    """Raw importable names and directory facts read from one wheel."""

    top_level_modules: frozenset[str]
    directories: dict[str, bool]
    importable_files: dict[str, bool]
    package_directories: frozenset[str]


class RangeWheelReader:
    """A seekable view of a wheel that fetches only requested byte ranges."""

    def __init__(self, url: str, size: int):
        self.url = url
        self.size = size

    def __call__(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise WheelScanError("invalid_range")
        if length == 0:
            return b""
        try:
            response = get_range(self.url, start=offset, end=offset + length - 1, max_bytes=length)
        except HTTPFetchError as error:
            raise PackageScanError("range_error") from error
        if response.total_size != self.size:
            raise WheelScanError("size_mismatch")
        return response.data


def _zip64_values(
    extra: bytes, need_uncompressed: bool, need_compressed: bool, need_offset: bool
) -> tuple[int | None, ...]:
    position = 0
    while position + 4 <= len(extra):
        field_id, field_size = struct.unpack_from("<HH", extra, position)
        position += 4
        field = extra[position : position + field_size]
        position += field_size
        if field_id != 0x0001:
            continue
        cursor = 0
        values: list[int | None] = []
        for needed in (need_uncompressed, need_compressed, need_offset):
            if needed:
                if cursor + 8 > len(field):
                    raise WheelScanError("invalid_zip64")
                values.append(struct.unpack_from("<Q", field, cursor)[0])
                cursor += 8
            else:
                values.append(None)
        return tuple(values)
    if need_uncompressed or need_compressed or need_offset:
        raise WheelScanError("invalid_zip64")
    return (None, None, None)


def _directory_bounds(reader: RangeReader, size: int) -> tuple[int, int, int]:
    tail_size = min(size, MAX_EOCD_TAIL_BYTES)
    tail_start = size - tail_size
    tail = reader(tail_start, tail_size)
    marker = tail.rfind(_EOCD)
    if marker < 0 or marker + 22 > len(tail):
        raise WheelScanError("missing_eocd")
    (
        _,
        disk,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = struct.unpack_from("<4s4H2IH", tail, marker)
    if marker + 22 + comment_size > len(tail) or disk or directory_disk:
        raise WheelScanError("invalid_eocd")
    if total_entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        locator_pos = marker - 20
        if locator_pos < 0 or tail[locator_pos : locator_pos + 4] != _ZIP64_LOCATOR:
            raise WheelScanError("missing_zip64_locator")
        _, locator_disk, zip64_offset, disks = struct.unpack_from("<4sIQI", tail, locator_pos)
        if locator_disk or disks != 1:
            raise WheelScanError("invalid_zip64_locator")
        record = reader(zip64_offset, 56)
        if len(record) != 56 or record[:4] != _ZIP64_EOCD:
            raise WheelScanError("invalid_zip64_eocd")
        (
            _,
            record_size,
            _made,
            _needed,
            disk_num,
            dir_disk,
            _disk_n,
            total_entries,
            directory_size,
            directory_offset,
        ) = struct.unpack("<4sQ2H2I4Q", record)
        if record_size < 44 or disk_num or dir_disk:
            raise WheelScanError("invalid_zip64_eocd")
    if directory_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise WheelScanError("central_directory_too_large")
    if total_entries > MAX_CENTRAL_DIRECTORY_ENTRIES:
        raise WheelScanError("too_many_entries")
    if directory_offset + directory_size > size:
        raise WheelScanError("invalid_central_directory_range")
    return directory_offset, directory_size, total_entries


def _central_entries(reader: RangeReader, size: int) -> list[dict[str, Any]]:
    offset, directory_size, expected_count = _directory_bounds(reader, size)
    directory = reader(offset, directory_size)
    if len(directory) != directory_size:
        raise WheelScanError("truncated_central_directory")
    entries: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(directory):
        if len(entries) >= MAX_CENTRAL_DIRECTORY_ENTRIES:
            raise WheelScanError("too_many_entries")
        if cursor + _CENTRAL_HEADER.size > len(directory):
            raise WheelScanError("truncated_central_directory")
        fields = _CENTRAL_HEADER.unpack_from(directory, cursor)
        if fields[0] != b"PK\x01\x02":
            raise WheelScanError("invalid_central_directory")
        compressed_size, uncompressed_size = fields[8], fields[9]
        name_size, extra_size, comment_size = fields[10], fields[11], fields[12]
        local_offset = fields[16]
        end = cursor + _CENTRAL_HEADER.size + name_size + extra_size + comment_size
        if end > len(directory):
            raise WheelScanError("truncated_central_directory")
        name_start = cursor + _CENTRAL_HEADER.size
        name = directory[name_start : name_start + name_size].decode("utf-8", errors="replace")
        extra_start = name_start + name_size
        extra = directory[extra_start : extra_start + extra_size]
        need_uncompressed = uncompressed_size == 0xFFFFFFFF
        need_compressed = compressed_size == 0xFFFFFFFF
        need_offset = local_offset == 0xFFFFFFFF
        if need_uncompressed or need_compressed or need_offset:
            values = _zip64_values(extra, need_uncompressed, need_compressed, need_offset)
            uncompressed_size = values[0] if need_uncompressed else uncompressed_size
            compressed_size = values[1] if need_compressed else compressed_size
            local_offset = values[2] if need_offset else local_offset
        entries.append(
            {
                "name": name,
                "method": fields[4],
                "compressed_size": compressed_size,
                "uncompressed_size": uncompressed_size,
                "local_offset": local_offset,
            }
        )
        cursor = end
    if len(entries) != expected_count:
        raise WheelScanError("entry_count_mismatch")
    return entries


def _read_entry(reader: RangeReader, entry: dict[str, Any]) -> bytes:
    compressed_size = entry["compressed_size"]
    uncompressed_size = entry["uncompressed_size"]
    if (
        compressed_size > MAX_WHEEL_ENTRY_COMPRESSED_BYTES
        or uncompressed_size > MAX_TOP_LEVEL_BYTES
    ):
        raise WheelScanError("top_level_too_large")
    header = reader(entry["local_offset"], _LOCAL_HEADER.size)
    if len(header) != _LOCAL_HEADER.size:
        raise WheelScanError("invalid_local_header")
    fields = _LOCAL_HEADER.unpack(header)
    if fields[0] != b"PK\x03\x04":
        raise WheelScanError("invalid_local_header")
    data_offset = entry["local_offset"] + _LOCAL_HEADER.size + fields[9] + fields[10]
    compressed = reader(data_offset, compressed_size)
    if len(compressed) != compressed_size:
        raise WheelScanError("truncated_top_level")
    if entry["method"] == 0:
        data = compressed
    elif entry["method"] == 8:
        try:
            decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
            data = decompressor.decompress(compressed, MAX_WHEEL_ENTRY_DECOMPRESSED_BYTES + 1)
            if len(data) > MAX_WHEEL_ENTRY_DECOMPRESSED_BYTES or decompressor.unconsumed_tail:
                raise WheelScanError("top_level_too_large")
            data += decompressor.flush(MAX_WHEEL_ENTRY_DECOMPRESSED_BYTES + 1 - len(data))
        except zlib.error as error:
            raise WheelScanError("invalid_top_level_compression") from error
        if not decompressor.eof or decompressor.unused_data:
            raise WheelScanError("invalid_top_level_compression")
    else:
        raise WheelScanError("unsupported_top_level_compression")
    if len(data) > MAX_WHEEL_ENTRY_DECOMPRESSED_BYTES or len(data) != uncompressed_size:
        raise WheelScanError("top_level_too_large")
    return data


def _extension_module_stem(filename: str) -> str:
    """Return the importable name from a platform-tagged extension filename."""

    return re.sub(r"(?:\.cpython-[^.]+|\.cp\d+[^.]*|\.abi3)?\.(?:so|pyd)$", "", filename)


def _valid_wheel_parts(parts: list[str]) -> bool:
    return all(
        part.isidentifier()
        and part not in IGNORED_TOP_LEVEL
        and part != "__pycache__"
        and not part.endswith((".dist-info", ".data"))
        for part in parts
    )


def _module_path_from_entry(name: str) -> tuple[str, bool] | None:
    """Return the dotted import name and native-extension flag for one wheel member."""

    parts = name.split("/")
    leaf = parts[-1]
    if leaf == "__init__.py":
        return None
    is_extension = leaf.endswith((".so", ".pyd"))
    if leaf.endswith(".py"):
        leaf = leaf[:-3]
    elif is_extension:
        leaf = _extension_module_stem(leaf)
    else:
        return None
    module_parts = [*parts[:-1], leaf]
    if len(module_parts) > MAX_NAMESPACE_DEPTH + 1 or not _valid_wheel_parts(module_parts):
        return None
    return ".".join(module_parts), is_extension


def _wheel_facts(entries: list[dict[str, Any]], reader: RangeReader) -> WheelFacts:
    """Record package paths and module files without making any mapping decisions."""

    names = {entry["name"].rstrip("/") for entry in entries}
    directory_names: set[str] = set()
    for entry in entries:
        name = entry["name"].rstrip("/")
        parts = name.split("/")
        parent_parts = parts if entry["name"].endswith("/") else parts[:-1]
        for depth in range(1, min(len(parent_parts), MAX_NAMESPACE_DEPTH + 1) + 1):
            prefix = parent_parts[:depth]
            if _valid_wheel_parts(prefix):
                directory_names.add(".".join(prefix))

    directories = {
        directory: f"{directory.replace('.', '/')}/__init__.py" in names
        for directory in directory_names
    }
    package_directories = frozenset(
        directory for directory, has_init in directories.items() if has_init
    )
    importable_files: dict[str, bool] = {}
    for entry in entries:
        if entry["name"].endswith("/"):
            continue
        member = _module_path_from_entry(entry["name"])
        if member is not None:
            module, is_extension = member
            importable_files[module] = is_extension

    top_level_entry = next(
        (
            entry
            for entry in entries
            if re.fullmatch(r"[^/]+\.dist-info/top_level\.txt", entry["name"].rstrip("/"))
        ),
        None,
    )
    if top_level_entry is not None:
        try:
            top_text = _read_entry(reader, top_level_entry).decode("utf-8")
        except UnicodeDecodeError as error:
            raise WheelScanError("invalid_top_level_encoding") from error
        top_level_modules = frozenset(
            module
            for line in top_text.splitlines()
            if (module := line.strip()) and "." not in module and _valid_wheel_parts([module])
        )
    else:
        top_level_modules = frozenset(
            {name for name in importable_files if "." not in name}
            | {name for name in directories if "." not in name}
        )
    return WheelFacts(
        top_level_modules,
        directories,
        importable_files,
        package_directories,
    )


def inspect_wheel(reader: RangeReader, size: int) -> WheelFacts:
    """Return raw wheel facts without mapping decisions or unnecessary member reads."""

    entries = _central_entries(reader, size)
    return _wheel_facts(entries, reader)


def _wheel_for_package(name: str) -> tuple[str, str, int] | None:
    try:
        payload = get(f"https://pypi.org/pypi/{quote(name, safe='')}/json")
    except HTTPFetchError as error:
        raise PackageScanError("http_error") from error
    try:
        metadata = json.loads(payload)
    except (ValueError, TypeError, RecursionError) as error:
        raise PackageScanError("metadata_error") from error
    if not isinstance(metadata, dict):
        raise PackageScanError("metadata_error")
    urls = metadata.get("urls", [])
    if not isinstance(urls, list):
        raise PackageScanError("metadata_error")
    wheels = [
        row
        for row in urls
        if isinstance(row, dict) and str(row.get("filename", "")).endswith(".whl")
    ]
    if not wheels:
        return None
    wheels.sort(key=lambda row: str(row.get("filename", "")))
    wheel = next((row for row in wheels if "py3-none-any" in row.get("filename", "")), wheels[0])
    size = wheel.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise WheelScanError("missing_size")
    filename = wheel.get("filename")
    url = wheel.get("url")
    if not isinstance(filename, str) or not filename or not isinstance(url, str) or not url:
        raise PackageScanError("metadata_error")
    return filename, url, size


def _scan_package(
    name: str,
) -> tuple[str, str | None, WheelFacts | None, str | None]:
    try:
        selected = _wheel_for_package(name)
        if selected is None:
            return name, None, None, "sdist_only"
        wheel_name, url, size = selected
        # Read an explicit in-bounds tail range; PyPI metadata supplies the expected object size.
        tail_size = min(size, MAX_EOCD_TAIL_BYTES)
        tail_start = size - tail_size
        try:
            tail = get_range(
                url,
                start=tail_start,
                end=size - 1,
                max_bytes=tail_size,
            )
        except HTTPFetchError as error:
            raise PackageScanError("range_error") from error
        if tail.total_size != size:
            raise WheelScanError("size_mismatch")
        reader = RangeWheelReader(url, size)
        facts = inspect_wheel(reader, size)
        return name, wheel_name, facts, None
    except WheelScanError as error:
        return name, None, None, str(error)
    except PackageScanError as error:
        return name, None, None, error.reason
    except Exception as error:
        return name, None, None, f"unexpected_error:{type(error).__name__}"


def build_import_map(
    packages: list[dict[str, Any]],
    scan: Callable[[str], tuple[str, str | None, WheelFacts | None, str | None]],
) -> dict[str, Any]:
    """Aggregate raw wheel facts, then resolve globally shared namespace paths."""

    records: list[tuple[str, str, WheelFacts]] = []
    skipped: Counter[str] = Counter()
    for package in packages:
        distribution = canonicalize_name(package["name"])
        _, wheel, facts, reason = scan(distribution)
        if reason is not None:
            skipped[reason] += 1
            continue
        if facts is None:
            skipped["metadata_error"] += 1
            continue
        records.append((distribution, wheel or "", facts))

    directory_providers: dict[str, dict[str, bool]] = defaultdict(dict)
    for distribution, _wheel, facts in records:
        for directory, has_init in facts.directories.items():
            directory_providers[directory][distribution] = has_init
    # Records retain rank order: a namespace requires enough contributors and a missing init file
    # in the highest-ranked contributor; a lower-ranked add-on cannot override a real package.
    shared_namespaces = {
        directory
        for directory, contributors in directory_providers.items()
        if len(contributors) >= NAMESPACE_MIN_PROVIDERS and not next(iter(contributors.values()))
    }
    real_package_paths = {
        directory
        for directory, contributors in directory_providers.items()
        if next(iter(contributors.values()))
    }
    shared_roots = {name for name in shared_namespaces if "." not in name}

    candidate_providers: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def has_real_package_ancestor(path_parts: list[str]) -> bool:
        for depth in range(2, len(path_parts)):
            prefix = ".".join(path_parts[:depth])
            if prefix in real_package_paths:
                return True
        return False

    for distribution, wheel, facts in records:
        package_candidates: set[str] = {
            module for module in facts.top_level_modules if module not in shared_namespaces
        }

        for package_path in facts.package_directories:
            parts = package_path.split(".")
            if len(parts) < 2 or parts[0] not in shared_roots:
                continue
            if package_path in shared_namespaces:
                continue
            if has_real_package_ancestor(parts):
                continue
            package_candidates.add(package_path)

        for module, is_extension in facts.importable_files.items():
            parts = module.split(".")
            if len(parts) < 2 or parts[0] not in shared_roots:
                continue
            if module in shared_namespaces:
                continue
            if has_real_package_ancestor(parts):
                continue
            parent = ".".join(parts[:-1])
            if (
                is_extension
                and parent not in facts.package_directories
                and parent not in shared_namespaces
            ):
                continue
            package_candidates.add(module)

        for module in package_candidates:
            candidate_providers[module].append((distribution, wheel))

    mappings: dict[str, dict[str, str]] = {}
    conflicts: dict[str, list[str]] = {}
    for module, choices in sorted(candidate_providers.items()):
        provider, wheel = choices[0]
        if "." not in module and canonicalize_name(module) == provider:
            continue
        mappings[module] = {"distribution": provider, "wheel": wheel}
        others = [other for other, _ in choices[1:] if other != provider]
        if others:
            conflicts[module] = sorted(set(others))
    return {
        "packages_scanned": len(records),
        "skipped": dict(sorted(skipped.items())),
        "mappings": mappings,
        "ignored_top_level": sorted(IGNORED_TOP_LEVEL),
        "namespace_roots": sorted(shared_roots),
        "conflicts": conflicts,
    }


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
        ) as stream:
            temporary = stream.name
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "src"
        / "slopguard"
        / "data"
        / "import_names.json",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0 or args.concurrency <= 0:
        parser.error("--limit and --concurrency must be positive")
    root = Path(__file__).resolve().parents[1]
    popular = json.loads(
        (root / "src" / "slopguard" / "data" / "popular_packages.json").read_text("utf-8")
    )
    packages = popular["packages"][: args.limit]
    results: dict[str, tuple[str, str | None, WheelFacts | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_scan_package, row["name"]) for row in packages]
        for future in futures:
            result = future.result()
            results[result[0]] = result
    mapping = build_import_map(packages, lambda name: results[name])
    overrides_doc = json.loads(
        (root / "src" / "slopguard" / "data" / "import_name_overrides.json").read_text("utf-8")
    )
    document = {
        "source": {
            "popular_packages_source_url": popular["source_url"],
            "popular_packages_retrieved_at": popular["retrieved_at"],
        },
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generator_version": GENERATOR_VERSION,
        **mapping,
    }
    _atomic_json(args.output, document)
    agrees = [
        module
        for module, entry in overrides_doc["overrides"].items()
        if document["mappings"].get(module, {}).get("distribution") == entry["distribution"]
    ]
    print(
        f"Wrote {len(document['mappings'])} mappings to {args.output}; overrides agreeing with generated data: {', '.join(sorted(agrees)) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
