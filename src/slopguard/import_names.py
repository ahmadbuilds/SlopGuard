"""Load the generated import-name map and its reviewed overrides."""

from __future__ import annotations

import json
import logging
import sys
from functools import lru_cache
from importlib import resources
from typing import Any

from packaging.utils import InvalidName, canonicalize_name

_LOGGER = logging.getLogger("slopguard")


@lru_cache(maxsize=1)
def _mapping_data() -> tuple[dict[str, str], frozenset[str]]:
    """Return the merged mapping and namespace roots, validating packaged JSON once."""

    overrides = _load_overrides()
    try:
        generated = _load_generated()
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        _LOGGER.warning(
            "Could not load generated import-name data (%s); using reviewed overrides only.",
            type(error).__name__,
        )
        generated_mappings: dict[str, str] = {}
        generated_roots: set[str] = set()
    else:
        generated_mappings, generated_roots = generated
    generated_mappings.update(overrides)
    return generated_mappings, frozenset(generated_roots)


def _load_overrides() -> dict[str, str]:
    document = _read_json("import_name_overrides.json")
    if not isinstance(document, dict) or set(document) != {"overrides"}:
        raise ValueError("invalid import-name overrides document")
    entries = document["overrides"]
    if not isinstance(entries, dict):
        raise ValueError("invalid import-name overrides")
    result: dict[str, str] = {}
    for module, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != {"distribution", "reason"}:
            raise ValueError("invalid import-name override entry")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ValueError("override reason must be a non-empty string")
        result[_validate_module(module)] = _validate_distribution(entry["distribution"])
    return result


def _load_generated() -> tuple[dict[str, str], set[str]]:
    document = _read_json("import_names.json")
    if not isinstance(document, dict):
        raise ValueError("invalid generated import-name document")
    mappings = document["mappings"]
    roots = document["namespace_roots"]
    if not isinstance(mappings, dict) or not isinstance(roots, list):
        raise ValueError("invalid generated import-name data")
    result: dict[str, str] = {}
    for module, entry in mappings.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("distribution"), str):
            raise ValueError("invalid generated mapping entry")
        result[_validate_module(module)] = _validate_distribution(entry["distribution"])
    valid_roots = {_validate_module(root) for root in roots}
    if any("." in root for root in valid_roots):
        raise ValueError("namespace roots must be top-level identifiers")
    return result, valid_roots


def _read_json(filename: str) -> Any:
    return json.loads(resources.files("slopguard").joinpath("data", filename).read_text("utf-8"))


def _validate_module(module: object) -> str:
    if not isinstance(module, str) or not all(part.isidentifier() for part in module.split(".")):
        raise ValueError("invalid import module name")
    return module


def _validate_distribution(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("invalid distribution name")
    try:
        normalized = canonicalize_name(name, validate=True)
    except (InvalidName, ValueError) as error:
        raise ValueError("invalid distribution name") from error
    if normalized != name:
        raise ValueError("distribution names must be canonical")
    return normalized


def distribution_for_import(module_name: str) -> str | None:
    """Return a mapped distribution using longest dotted-prefix matching."""

    mappings, _ = _mapping_data()
    parts = module_name.split(".")
    for end in range(len(parts), 0, -1):
        match = mappings.get(".".join(parts[:end]))
        if match is not None:
            return match
    return None


def namespace_roots() -> frozenset[str]:
    """Return roots with multiple namespace-package providers in the snapshot."""

    return _mapping_data()[1]


@lru_cache(maxsize=1)
def stdlib_modules() -> frozenset[str]:
    """Return the bundled stdlib union plus the running interpreter's stdlib names."""

    try:
        document = _read_json("stdlib_modules.json")
        names = document["modules"]
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name.isidentifier() for name in names
        ):
            raise ValueError("invalid standard-library module list")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        _LOGGER.warning(
            "Could not load generated standard-library data; using the current interpreter's list."
        )
        names = []
    return frozenset(names) | sys.stdlib_module_names
