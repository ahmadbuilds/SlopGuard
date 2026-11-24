"""Extract normalized dependencies introduced by changed Python project files.

This module is deliberately offline: it parses text and never installs or runs a
dependency. Pass complete base/head contents for each changed file; only lines
added or replaced on the head side are considered.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from . import CONTRACT_VERSION
from .contracts import DependencyResult, SourceLocation

SourceType = Literal["requirements", "pyproject", "import"]

IMPORT_TO_DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}
MAX_DIFF_INPUT_LINES = 2_000
MAX_DIFF_INPUT_CHARACTERS = 1_000_000
MAX_CHANGED_FILES = 1_000
MAX_TOTAL_DIFF_INPUT_CHARACTERS = 20_000_000


@dataclass(frozen=True)
class ChangedFile:
    """Base and head text for one changed file in a pull request."""

    path: str
    base_content: str
    content: str


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


def extract_dependencies(changed_files: Iterable[ChangedFile]) -> ExtractionResult:
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
    local_modules = _local_python_module_names(changed_files)
    candidates: dict[str, DependencyResult] = {}
    baseline: dict[str, set[str]] = {}
    warnings: list[ExtractionWarning] = []

    for changed_file in changed_files:
        if _diff_input_exceeds_limits(changed_file):
            warnings.append(
                ExtractionWarning(
                    changed_file.path,
                    None,
                    "Changed file exceeds the safe diff size limit; dependency extraction was skipped.",
                )
            )
            continue

        if changed_file.base_content:
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
                local_modules,
            )
            for name, candidate in baseline_candidates.items():
                baseline.setdefault(name, set()).update(
                    location["source_type"] for location in candidate["source_locations"]
                )

        added_lines = _added_lines(changed_file.base_content, changed_file.content)
        if not added_lines:
            continue
        _extract_file(changed_file, added_lines, candidates, warnings, local_modules)

    # A line reformat can make an unchanged PEP 621 dependency look newly added
    # in a textual diff. Existing manifest dependencies suppress new manifest
    # entries; existing imports suppress import-only candidates. An old import
    # must not hide a newly declared manifest dependency.
    candidates = {
        name: candidate
        for name, candidate in candidates.items()
        if not _candidate_already_present(name, candidate, baseline)
    }

    return ExtractionResult(tuple(candidates.values()), tuple(warnings))


def _diff_input_exceeds_limits(changed_file: ChangedFile) -> bool:
    total_characters = len(changed_file.base_content) + len(changed_file.content)
    if total_characters > MAX_DIFF_INPUT_CHARACTERS:
        return True
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
    names: set[str] = set()
    for changed_file in changed_files:
        path = PurePosixPath(changed_file.path)
        if path.suffix.lower() != ".py" or not changed_file.content:
            continue
        parts = [part for part in path.with_suffix("").parts if part not in {"/", "."}]
        if parts and parts[0] in {"src", "lib"}:
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for end in range(1, len(parts) + 1):
            names.add(".".join(parts[:end]))
    return names


def _extract_file(
    changed_file: ChangedFile,
    lines: dict[int, str],
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
    local_modules: set[str],
) -> None:
    path = PurePosixPath(changed_file.path)
    filename = path.name.lower()

    if filename == "pyproject.toml":
        _extract_pyproject(changed_file, lines, candidates, warnings)
    elif path.suffix.lower() in {".txt", ".in"} and (
        "requirement" in filename or "constraint" in filename
    ):
        _extract_requirements(changed_file, lines, candidates, warnings)
    elif path.suffix.lower() == ".py":
        _extract_imports(changed_file, lines, candidates, warnings, local_modules)


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
    for line_number, raw_line in added_lines.items():
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
    if not isinstance(dynamic, list) or not all(isinstance(value, str) for value in dynamic):
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                None,
                "The project.dynamic value is invalid; dynamic dependencies cannot be resolved.",
            )
        )
    elif any(value in dynamic for value in ("dependencies", "optional-dependencies")):
        warnings.append(
            ExtractionWarning(
                changed_file.path,
                None,
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

    # Poetry's table format has different semantics from PEP 621; surface it
    # clearly rather than silently treating its values as package names.
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
        if not assignment or table not in {"project", "project.optional-dependencies"}:
            index += 1
            continue

        key = next(value for value in assignment.groups()[:3] if value is not None)
        is_dependency_array = (
            (table == "project" and key == "dependencies")
            or (table == "project" and key.startswith("optional-dependencies."))
            or table == "project.optional-dependencies"
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
        literals = _toml_string_literals(array_text, source_line)
        if (
            not isinstance(parsed_array, list)
            or not all(isinstance(value, str) for value in parsed_array)
            or [value for value, _line in literals] != parsed_array
        ):
            warnings.append(
                ExtractionWarning(
                    path,
                    index + 1,
                    "Dependency array contains unsupported non-string values and was skipped.",
                )
            )
            index = content.count("\n", 0, end_offset) + 1
            continue

        entries.extend(literals)
        index = content.count("\n", 0, end_offset) + 1

    return entries


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


def _toml_string_literals(array_text: str, first_line: int) -> list[tuple[str, int]]:
    literals: list[tuple[str, int]] = []
    index = 0
    while index < len(array_text):
        char = array_text[index]
        if char == "#":
            newline = array_text.find("\n", index)
            index = len(array_text) if newline < 0 else newline + 1
            continue
        if char not in "\"'":
            index += 1
            continue

        end = _toml_string_end(array_text, index)
        token = array_text[index:end]
        try:
            value = tomllib.loads(f"item = {token}")["item"]
        except tomllib.TOMLDecodeError:
            index = end
            continue
        if isinstance(value, str):
            line_number = first_line + array_text.count("\n", 0, index)
            literals.append((value, line_number))
        index = end
    return literals


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
    added_lines: dict[int, str],
    candidates: dict[str, DependencyResult],
    warnings: list[ExtractionWarning],
    local_modules: set[str],
) -> None:
    try:
        module = ast.parse(changed_file.content)
    except SyntaxError:
        for line_number, raw_line in added_lines.items():
            if re.match(r"^\s*(?:import|from)\b", raw_line):
                warnings.append(
                    ExtractionWarning(
                        changed_file.path,
                        line_number,
                        "Python file has syntax errors; import additions could not be parsed.",
                    )
                )
        return

    multiline_lines = _multiline_import_added_lines(
        changed_file.content, added_lines, changed_file.path, warnings
    )
    import_nodes = sorted(
        (node for node in ast.walk(module) if isinstance(node, ast.Import | ast.ImportFrom)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in import_nodes:
        line_number = node.lineno
        end_line = getattr(node, "end_lineno", line_number)
        changed_import_lines = [
            added_line for added_line in added_lines if line_number <= added_line <= end_line
        ]
        if not changed_import_lines:
            continue
        if line_number in multiline_lines or end_line > line_number:
            continue

        if isinstance(node, ast.Import):
            import_names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            import_names = [node.module]
        else:
            continue

        for full_import_name in import_names:
            import_name = full_import_name.split(".", maxsplit=1)[0]
            if import_name in sys.stdlib_module_names:
                continue
            if full_import_name in local_modules or import_name in local_modules:
                continue
            distribution = IMPORT_TO_DISTRIBUTION.get(import_name, import_name)
            _add_candidate(
                candidates,
                import_name,
                changed_file.path,
                line_number,
                "import",
                normalized_name=distribution,
            )


def _multiline_import_added_lines(
    content: str,
    added_lines: dict[int, str],
    path: str,
    warnings: list[ExtractionWarning],
) -> set[int]:
    try:
        module = ast.parse(content)
    except SyntaxError:
        return set()

    unsupported: set[int] = set()
    for node in ast.walk(module):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        end_line = getattr(node, "end_lineno", node.lineno)
        if end_line <= node.lineno:
            continue
        new_lines = [line for line in added_lines if node.lineno <= line <= end_line]
        for line_number in new_lines:
            unsupported.add(line_number)
            warnings.append(
                ExtractionWarning(
                    path,
                    line_number,
                    "Multiline import syntax is unsupported and was skipped.",
                )
            )
    return unsupported


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
