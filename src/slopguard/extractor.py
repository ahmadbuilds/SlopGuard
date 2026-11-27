"""Extract normalized dependencies introduced by changed Python project files.

This module is deliberately offline: it parses text and never installs or runs a
dependency. Pass complete base/head contents for each changed file; only lines
added or replaced on the head side are considered.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from . import CONTRACT_VERSION
from .contracts import DependencyResult, SourceLocation
from .import_names import distribution_for_import, namespace_roots, stdlib_modules

SourceType = Literal["requirements", "pyproject", "import"]
MAX_DIFF_INPUT_LINES = 2_000
MAX_DIFF_INPUT_CHARACTERS = 1_000_000
MAX_CHANGED_FILES = 1_000
MAX_TOTAL_DIFF_INPUT_CHARACTERS = 20_000_000
_SOURCE_ROOTS = frozenset({"lib", "src"})
_TEST_ROOTS = frozenset({"test", "tests"})


@dataclass(frozen=True)
class ChangedFile:
    """Base and head text for one changed file in a pull request."""

    path: str
    base_content: str
    content: str


def is_supported_path(value: str) -> bool:
    """Return whether a repository path can produce dependency findings or warnings."""

    path = PurePosixPath(value.replace("\\", "/"))
    filename = path.name.lower()
    if filename in {"pyproject.toml", "setup.py", "setup.cfg", "pipfile"}:
        return True
    if path.suffix.lower() == ".py":
        return True
    return path.suffix.lower() in {".txt", ".in"} and (
        "requirement" in filename or "constraint" in filename or "requirements" in path.parts
    )


@dataclass(frozen=True)
class ExtractionWarning:
    """A visible diagnostic for syntax that was not safely interpreted."""

    file: str
    line: int | None
    message: str


@dataclass(frozen=True)
class ExtractionResult:
    """Schema-shaped candidates plus diagnostics for unsupported additions."""

    candidates: tuple[DependencyResult, ...]
    warnings: tuple[ExtractionWarning, ...]


def extract_dependencies(
    changed_files: Iterable[ChangedFile],
    *,
    local_modules: Iterable[str] | None = None,
    local_modules_by_test_directory: Mapping[str, Iterable[str]] | None = None,
) -> ExtractionResult:
    """Return dependencies added in requirements, pyproject, and Python imports.

    Candidate rows use SG-01's result shape. Registry state is unknown and the
    score is a neutral placeholder until the later analysis stages run.
    """

    materialized_files: list[ChangedFile] = []
    total_characters = 0
    for changed_file in changed_files:
        if len(materialized_files) >= MAX_CHANGED_FILES:
            return ExtractionResult(
                (),
                (
                    ExtractionWarning(
                        changed_file.path,
                        None,
                        "Pull request exceeds the safe changed-file count limit; dependency extraction was skipped.",
                    ),
                ),
            )
        total_characters += len(changed_file.base_content) + len(changed_file.content)
        if total_characters > MAX_TOTAL_DIFF_INPUT_CHARACTERS:
            return ExtractionResult(
                (),
                (
                    ExtractionWarning(
                        changed_file.path,
                        None,
                        "Pull request exceeds the safe aggregate diff size limit; dependency extraction was skipped.",
                    ),
                ),
            )
        materialized_files.append(changed_file)

    changed_files = tuple(materialized_files)
    local_module_names = _local_python_module_names(changed_files)
    if local_modules is not None:
        local_module_names.update(name.split(".", maxsplit=1)[0] for name in local_modules)
    candidates: dict[str, DependencyResult] = {}
    baseline: dict[str, set[str]] = {}
    warnings: list[ExtractionWarning] = []

    for changed_file in changed_files:
        if not is_supported_path(changed_file.path):
            continue
        is_python = PurePosixPath(changed_file.path).suffix.lower() == ".py"
        if _diff_input_exceeds_limits(changed_file, ignore_line_limit=is_python):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    None,
                    "Changed file exceeds the safe diff size limit; dependency extraction was skipped.",
                )
            )
            continue

        if changed_file.base_content and not is_python:
            baseline_candidates: dict[str, DependencyResult] = {}
            base_lines = {
                line_number: line
                for line_number, line in enumerate(changed_file.base_content.splitlines(), start=1)
            }
            _extract_file(
                ChangedFile(changed_file.path, "", changed_file.base_content),
                base_lines,
                baseline_candidates,
                [],
                local_module_names,
            )
            for name, candidate in baseline_candidates.items():
                baseline.setdefault(name, set()).update(
                    location["source_type"] for location in candidate["source_locations"]
                )

        if is_python:
            if PurePosixPath(changed_file.path).name.lower() == "setup.py":
                added_lines = _added_lines(changed_file.base_content, changed_file.content)
                if added_lines:
                    _extract_file(
                        changed_file, added_lines, candidates, warnings, local_module_names
                    )
                continue
            modules_for_file = local_module_names
            if local_modules_by_test_directory is not None:
                test_modules = local_modules_by_test_directory.get(
                    PurePosixPath(changed_file.path.replace("\\", "/")).parent.as_posix(), ()
                )
                if test_modules:
                    modules_for_file = local_module_names | set(test_modules)
            _extract_imports(changed_file, candidates, warnings, modules_for_file)
            continue

        added_lines = _added_lines(changed_file.base_content, changed_file.content)
        if not added_lines:
            continue
        _extract_file(changed_file, added_lines, candidates, warnings, local_module_names)

    candidates = {
        name: candidate
        for name, candidate in candidates.items()
        if not _candidate_already_present(name, candidate, baseline)
    }

    return ExtractionResult(tuple(candidates.values()), tuple(warnings))


def _diff_input_exceeds_limits(
    changed_file: ChangedFile, *, ignore_line_limit: bool = False
) -> bool:
    total_characters = len(changed_file.base_content) + len(changed_file.content)
    if total_characters > MAX_DIFF_INPUT_CHARACTERS:
        return True
    if ignore_line_limit:
        return False
    total_lines = len(changed_file.base_content.splitlines()) + len(
        changed_file.content.splitlines()
    )
    return total_lines > MAX_DIFF_INPUT_LINES


def _candidate_already_present(
    name: str,
    candidate: DependencyResult,
    baseline: dict[str, set[str]],
) -> bool:
    baseline_sources = baseline.get(name, set())
    if not baseline_sources:
        return False
    has_manifest_source = any(
        location["source_type"] in {"requirements", "pyproject"}
        for location in candidate["source_locations"]
    )
    relevant_sources = {"requirements", "pyproject"} if has_manifest_source else {"import"}
    return bool(baseline_sources & relevant_sources)


def _local_python_module_names(changed_files: tuple[ChangedFile, ...]) -> set[str]:
    return local_modules_from_paths(
        changed_file.path for changed_file in changed_files if changed_file.content
    )


def local_modules_from_paths(paths: Iterable[str], project_name: str | None = None) -> set[str]:
    """Return repository-wide local imports from root, ``src/`` and ``lib/`` paths.

    A file stem is top-level only when it is directly under the repository root, ``src/`` or
    ``lib/``. Nested namespace-package files contribute their first import component, not their
    leaf stem. Test modules are handled separately because their import paths are scoped to tests.
    """

    normalized_paths = tuple(PurePosixPath(value.replace("\\", "/")) for value in paths)
    names: set[str] = set()
    for original_path in normalized_paths:
        path = original_path
        if path.suffix.lower() != ".py":
            continue
        parts = list(path.parts)
        source_root = bool(parts and parts[0] in _SOURCE_ROOTS)
        if source_root:
            parts = parts[1:]
        if not parts:
            continue
        top = parts[0] if len(parts) > 1 else path.stem
        if top != "__init__":
            names.add(top)
        stem = path.stem
        direct_import_module = len(parts) == 1
        if stem != "__init__" and direct_import_module:
            names.add(stem)
    if project_name:
        import_name = project_name.lower().replace("-", "_")
        if import_name:
            names.add(import_name)
    return names


def local_test_modules_by_directory(paths: Iterable[str]) -> dict[str, set[str]]:
    """Return test modules scoped to directories that contain Python test files."""

    normalized_paths = tuple(PurePosixPath(value.replace("\\", "/")) for value in paths)
    test_directories = {
        path.parent
        for path in normalized_paths
        if path.suffix.lower() == ".py"
        and path.parts
        and path.parts[0] in _TEST_ROOTS
        and (
            path.name.lower() == "conftest.py"
            or path.name.lower().startswith("test_")
            or path.name.lower().endswith("_test.py")
        )
    }
    modules_by_directory: dict[str, set[str]] = {}
    for path in normalized_paths:
        if path.suffix.lower() != ".py" or path.name == "__init__.py":
            continue
        if path.parent in test_directories:
            modules_by_directory.setdefault(path.parent.as_posix(), set()).add(path.stem)
    return modules_by_directory


def _extract_file(
    changed_file: ChangedFile,
    lines: dict[int, str],
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
    local_modules: set[str],
) -> None:
    path = PurePosixPath(changed_file.path.replace("\\", "/"))
    filename = path.name.lower()

    if filename == "pyproject.toml":
        _extract_pyproject(changed_file, lines, candidates, warnings)
    elif path.suffix.lower() in {".txt", ".in"} and (
        "requirement" in filename or "constraint" in filename or "requirements" in path.parts
    ):
        _extract_requirements(changed_file, lines, candidates, warnings)
    elif filename in {"setup.py", "setup.cfg", "pipfile"}:
        line_number = min(lines) if lines else 1
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                line_number,
                f"Dependency changes in {path.name} are not analysed by SlopGuard; review them manually.",
            )
        )
    elif path.suffix.lower() == ".py":
        _extract_imports(changed_file, candidates, warnings, local_modules)


def _added_lines(base_content: str, content: str) -> dict[int, str]:
    """Map 1-based head line numbers to inserted or replacement lines."""

    base_lines = base_content.splitlines()
    head_lines = content.splitlines()
    matcher = SequenceMatcher(a=base_lines, b=head_lines, autojunk=False)
    added: dict[int, str] = {}
    for tag, _base_start, _base_end, head_start, head_end in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            for index in range(head_start, head_end):
                added[index + 1] = head_lines[index]
    return added


def _extract_requirements(
    changed_file: ChangedFile,
    added_lines: dict[int, str],
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
) -> None:
    physical_lines = changed_file.content.splitlines()
    logical_lines: list[tuple[int, str, bool]] = []
    index = 0
    while index < len(physical_lines):
        start = index
        chunks: list[str] = []
        added = False
        while index < len(physical_lines):
            raw = physical_lines[index]
            added = added or index + 1 in added_lines
            stripped = raw.rstrip()
            continued = stripped.endswith("\\")
            chunks.append(stripped[:-1] if continued else raw)
            index += 1
            if not continued:
                break
        logical_lines.append((start + 1, " ".join(chunks), added))

    for line_number, raw_line, is_added in logical_lines:
        if not is_added:
            continue
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith(("-r", "--requirement", "-c", "--constraint")):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "Requirement include directives are not followed; scan the referenced file separately.",
                )
            )
            continue
        if line.startswith(("-e", "--editable")):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "Editable and VCS requirement syntax is unsupported and was skipped.",
                )
            )
            continue

        if re.match(
            r"^(?:-i|--index-url|--extra-index-url|-f|--find-links|--trusted-host)(?:\s|=|$)",
            line,
        ):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "Package index option changes where packages are installed from; review it manually.",
                )
            )
            continue
        if re.match(
            r"^(?:--pre|--require-hashes|--no-binary|--only-binary|--prefer-binary)(?:\s|=|$)", line
        ):
            continue

        line = re.sub(r"\s+--hash(?:=|\s+)[^\s]+", "", line).strip()

        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line:
            continue
        if line.startswith(("http://", "https://", "git+", "file:")):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "URL-only requirement syntax is unsupported and was skipped.",
                )
            )
            continue

        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "Malformed or unsupported requirement syntax was skipped.",
                )
            )
            continue

        if requirement.url is not None:
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "Direct URL requirements are unsupported and were skipped.",
                )
            )
            continue

        _add_candidate(
            candidates,
            requirement.name,
            changed_file.path,
            line_number,
            "requirements",
        )


def _extract_pyproject(
    changed_file: ChangedFile,
    added_lines: dict[int, str],
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
) -> None:
    try:
        document = tomllib.loads(changed_file.content)
    except tomllib.TOMLDecodeError as error:
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                getattr(error, "lineno", None),
                f"Invalid TOML; dependencies were not extracted: {error}",
            )
        )
        return

    project = document.get("project", {})
    dynamic = project.get("dynamic", []) if isinstance(project, dict) else []
    dynamic_line = _pyproject_key_line(changed_file.content, "project", "dynamic")
    if not isinstance(dynamic, list) or not all(isinstance(value, str) for value in dynamic):
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                dynamic_line,
                "The project.dynamic value is invalid; dynamic dependencies cannot be resolved.",
            )
        )
    elif any(value in dynamic for value in ("dependencies", "optional-dependencies")):
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                dynamic_line,
                "Dynamic PEP 621 dependencies cannot be resolved from this file and were skipped.",
            )
        )

    for dependency, line_number in _pep621_dependency_entries(
        changed_file.content, warnings, changed_file.path
    ):
        if line_number not in added_lines:
            continue
        _add_requirement(
            dependency,
            changed_file.path,
            line_number,
            "pyproject",
            candidates,
            warnings,
        )

    table: str | None = None
    for line_number, line in enumerate(changed_file.content.splitlines(), start=1):
        header = re.fullmatch(r"\s*\[([^\[\]]+)\]\s*(?:#.*)?", line)
        if header:
            table = header.group(1).strip()
        if (
            line_number in added_lines
            and table is not None
            and table.startswith("tool.poetry.")
            and table.endswith(".dependencies")
        ):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    "Poetry dependency tables are not supported yet; use PEP 621 project.dependencies.",
                )
            )


def _pep621_dependency_entries(
    content: str,
    warnings: list[ExtractionWarning],
    path: str,
) -> list[tuple[str, int]]:
    """Read string items and source lines from PEP 621 dependency arrays."""

    raw_lines = content.splitlines(keepends=True)
    lines = [line.removesuffix("\n").removesuffix("\r") for line in raw_lines]
    line_offsets: list[int] = []
    offset = 0
    for line in raw_lines:
        line_offsets.append(offset)
        offset += len(line)

    entries: list[tuple[str, int]] = []
    table: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        header = re.fullmatch(r"\s*\[([^\[\]]+)\]\s*(?:#.*)?", line)
        if header:
            table = header.group(1).strip()
            index += 1
            continue

        assignment = re.match(
            r"\s*(?:([A-Za-z0-9_.-]+)|\"([^\"]+)\"|'([^']+)')\s*=\s*(.*)",
            line,
        )
        if not assignment or table not in {
            "project",
            "project.optional-dependencies",
            "dependency-groups",
            "build-system",
        }:
            index += 1
            continue

        key = next(value for value in assignment.groups()[:3] if value is not None)
        is_dependency_array = (
            (table == "project" and key == "dependencies")
            or (table == "project" and key.startswith("optional-dependencies."))
            or table == "project.optional-dependencies"
            or (table == "dependency-groups" and bool(key))
            or (table == "build-system" and key == "requires")
        )
        is_inline_optional_dependencies = table == "project" and key == "optional-dependencies"
        value_text = assignment.group(4)
        if is_inline_optional_dependencies and value_text.lstrip().startswith("{"):
            try:
                optional_dependencies = tomllib.loads(f"values = {value_text}")["values"]
            except tomllib.TOMLDecodeError:
                optional_dependencies = None
            if not isinstance(optional_dependencies, dict) or any(
                not isinstance(group, str)
                or not isinstance(dependencies, list)
                or not all(isinstance(dependency, str) for dependency in dependencies)
                for group, dependencies in optional_dependencies.items()
            ):
                warnings.append(
                    ExtractionWarning(
                        path,
                        index + 1,
                        "Inline optional dependencies must map group names to static string arrays; this declaration was skipped.",
                    )
                )
            else:
                entries.extend(
                    (dependency, index + 1)
                    for dependencies in optional_dependencies.values()
                    for dependency in dependencies
                )
            index += 1
            continue
        if not is_dependency_array:
            index += 1
            continue

        bracket_offset = value_text.find("[")
        if bracket_offset < 0:
            if value_text.strip() and not value_text.lstrip().startswith("#"):
                warnings.append(
                    ExtractionWarning(
                        path,
                        index + 1,
                        "PEP 621 dependency values must be static arrays; this declaration was skipped.",
                    )
                )
            index += 1
            continue

        start_offset = line_offsets[index] + assignment.start(4) + bracket_offset
        array_text, end_offset = _capture_toml_array(content, start_offset)
        if array_text is None:
            warnings.append(
                ExtractionWarning(path, index + 1, "Unterminated dependency array was skipped.")
            )
            index += 1
            continue

        try:
            parsed_array = tomllib.loads(f"values = {array_text}")["values"]
        except tomllib.TOMLDecodeError:
            warnings.append(
                ExtractionWarning(path, index + 1, "Malformed dependency array was skipped.")
            )
            index = content.count("\n", 0, end_offset) + 1
            continue

        source_line = content.count("\n", 0, start_offset) + 1
        array_items = _toml_array_items(array_text, source_line)
        is_dependency_group = table == "dependency-groups"
        valid_items = isinstance(parsed_array, list) and array_items is not None
        extracted_items: list[tuple[str, int]] = []
        if valid_items:
            for value, item_line in array_items:
                if isinstance(value, str):
                    extracted_items.append((value, item_line))
                elif (
                    is_dependency_group
                    and isinstance(value, dict)
                    and set(value) == {"include-group"}
                    and isinstance(value["include-group"], str)
                ):
                    continue
                else:
                    valid_items = False
                    break
        if not valid_items:
            warnings.append(
                ExtractionWarning(
                    path,
                    index + 1,
                    "Dependency array contains unsupported values and was skipped.",
                )
            )
            index = content.count("\n", 0, end_offset) + 1
            continue
        entries.extend(extracted_items)
        index = content.count("\n", 0, end_offset) + 1

    return entries


def _pyproject_key_line(content: str, table_name: str, key_name: str) -> int | None:
    table: str | None = None
    for line_number, line in enumerate(content.splitlines(), start=1):
        header = re.fullmatch(r"\s*\[([^\[\]]+)\]\s*(?:#.*)?", line)
        if header:
            table = header.group(1).strip()
        if table == table_name and re.match(rf"\s*{re.escape(key_name)}\s*=", line):
            return line_number
    return None


def _capture_toml_array(content: str, start: int) -> tuple[str | None, int]:
    depth = 0
    index = start
    while index < len(content):
        char = content[index]
        if char == "#":
            newline = content.find("\n", index)
            index = len(content) if newline < 0 else newline + 1
            continue
        if char in "\"'":
            index = _toml_string_end(content, index)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return content[start : index + 1], index + 1
        index += 1
    return None, len(content)


def _toml_string_end(content: str, start: int) -> int:
    quote = content[start]
    delimiter = quote * 3 if content.startswith(quote * 3, start) else quote
    index = start + len(delimiter)
    while index < len(content):
        if quote == '"' and content[index] == "\\":
            index += 2
            continue
        if content.startswith(delimiter, index):
            return index + len(delimiter)
        index += 1
    return len(content)


def _toml_array_items(array_text: str, first_line: int) -> list[tuple[object, int]] | None:
    """Parse top-level TOML array items while retaining each item's line."""

    items: list[tuple[object, int]] = []
    start = 1
    index = 1
    square_depth = 0
    brace_depth = 0
    while index < len(array_text) - 1:
        char = array_text[index]
        if char == "#":
            newline = array_text.find("\n", index)
            index = len(array_text) - 1 if newline < 0 else newline
            continue
        if char in "\"'":
            index = _toml_string_end(array_text, index)
            continue
        if char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == "," and square_depth == 0 and brace_depth == 0:
            raw_item = array_text[start:index].strip()
            if raw_item:
                try:
                    value = tomllib.loads(f"item = {raw_item}")["item"]
                except tomllib.TOMLDecodeError:
                    return None
                line_number = first_line + array_text.count("\n", 0, start)
                items.append((value, line_number))
            start = index + 1
        index += 1
    raw_item = array_text[start:-1].strip()
    if raw_item:
        try:
            value = tomllib.loads(f"item = {raw_item}")["item"]
        except tomllib.TOMLDecodeError:
            return None
        line_number = first_line + array_text.count("\n", 0, start)
        items.append((value, line_number))
    return items


def _add_requirement(
    raw_requirement: str,
    path: str,
    line_number: int,
    source_type: SourceType,
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
) -> None:
    try:
        requirement = Requirement(raw_requirement)
    except InvalidRequirement:
        warnings.append(
            ExtractionWarning(
                path, line_number, "Malformed or unsupported requirement syntax was skipped."
            )
        )
        return
    if requirement.url is not None:
        warnings.append(
            ExtractionWarning(
                path, line_number, "Direct URL requirements are unsupported and were skipped."
            )
        )
        return
    _add_candidate(candidates, requirement.name, path, line_number, source_type)


def _extract_imports(
    changed_file: ChangedFile,
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
    local_modules: set[str],
) -> None:
    try:
        head_module = ast.parse(changed_file.content)
    except SyntaxError as error:
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                error.lineno,
                "Python file has syntax errors; import additions could not be parsed.",
            )
        )
        return
    try:
        base_module = ast.parse(changed_file.base_content)
    except SyntaxError as error:
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                error.lineno,
                "Base Python file has syntax errors; existing imports were treated as empty.",
            )
        )
        base_module = ast.Module(body=[], type_ignores=[])

    def imports_by_distribution(module: ast.Module) -> dict[str, tuple[str, int]]:
        found: dict[str, tuple[str, int]] = {}
        nodes = sorted(
            (node for node in ast.walk(module) if isinstance(node, ast.Import | ast.ImportFrom)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in nodes:
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif node.level or not node.module or node.module == "__future__":
                continue
            else:
                names = [node.module]
            for name in names:
                root_name = name.split(".", maxsplit=1)[0]
                if root_name in stdlib_modules() or root_name in local_modules:
                    continue
                distribution = distribution_for_import(name)
                resolved_name = str(canonicalize_name(distribution or root_name))
                found.setdefault(resolved_name, (name, node.lineno))
        return found

    base_imports = imports_by_distribution(base_module)
    head_imports = imports_by_distribution(head_module)
    for resolved_name, (full_name, line_number) in head_imports.items():
        if resolved_name in base_imports:
            continue
        root_name = full_name.split(".", maxsplit=1)[0]
        distribution = distribution_for_import(full_name)
        if distribution is None and root_name in namespace_roots():
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    line_number,
                    f"Namespace import {full_name} could not be mapped to a PyPI distribution.",
                )
            )
            continue
        _add_candidate(
            candidates,
            full_name if distribution else root_name,
            changed_file.path,
            line_number,
            "import",
            normalized_name=distribution or root_name,
        )


def _add_candidate(
    candidates: dict[str, DependencyResult],
    requested_name: str,
    path: str,
    line_number: int,
    source_type: SourceType,
    *,
    normalized_name: str | None = None,
) -> None:
    canonical_name = str(canonicalize_name(normalized_name or requested_name))
    if not canonical_name:
        return

    candidate = candidates.get(canonical_name)
    if candidate is None:
        candidate = {
            "contract_version": CONTRACT_VERSION,
            "requested_name": requested_name,
            "normalized_name": canonical_name,
            "registry_exists": None,
            "metadata_evidence": [],
            "similarity_candidates": [],
            "risk_score": 0.0,
            "severity": "info",
            "remediation": "Await registry lookup and risk analysis before making policy decisions.",
            "source_locations": [],
        }
        candidates[canonical_name] = candidate

    location: SourceLocation = {
        "file": path,
        "line": line_number,
        "source_type": source_type,
    }
    if location not in candidate["source_locations"]:
        candidate["source_locations"].append(location)
