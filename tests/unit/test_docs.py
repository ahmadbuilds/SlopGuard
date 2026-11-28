"""Drift checks for published documentation and project metadata."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from packaging.version import Version

from slopguard.config import SlopGuardConfig, parse_config

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
ACTION = ROOT / "action.yml"
INLINE_LINK = re.compile(r"!?\[[^\]]+\]\(([^)]+)\)")
ACTION_REFERENCE = re.compile(r"ahmadbuilds/SlopGuard@([^/\s`]+)")
ACTION_USE = re.compile(r"(?m)^\s*(?:-\s*)?uses:\s*ahmadbuilds/SlopGuard@([^\s#]+)")


def _markdown_table(markdown: str, heading: str) -> tuple[list[str], list[dict[str, str]]]:
    section_start = markdown.find(f"## {heading}")
    assert section_start >= 0, f"missing README section: {heading}"
    section_end = markdown.find("\n## ", section_start + 1)
    section = markdown[section_start:] if section_end < 0 else markdown[section_start:section_end]
    lines = section.splitlines()
    table_start = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("|")), None
    )
    assert table_start is not None, f"missing table in README section: {heading}"

    parsed_rows: list[list[str]] = []
    for line in lines[table_start:]:
        if not line.lstrip().startswith("|"):
            break
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if all(not cell.replace(":", "").replace("-", "").strip() for cell in cells):
            continue
        parsed_rows.append(cells)

    assert parsed_rows, f"empty table in README section: {heading}"
    headers = parsed_rows[0]
    assert all(len(row) == len(headers) for row in parsed_rows[1:])
    return headers, [dict(zip(headers, row, strict=True)) for row in parsed_rows[1:]]


def _action_document() -> dict[str, object]:
    parsed = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _walk(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_readme_documents_every_action_input_and_default() -> None:
    action = _action_document()
    input_specs = action["inputs"]
    assert isinstance(input_specs, dict)
    headers, rows = _markdown_table(README.read_text(encoding="utf-8"), "Usage and inputs")
    assert "Input" in headers and "Default" in headers
    assert len(rows) == len(input_specs)

    documented = {row["Input"]: "" if row["Default"] == '""' else row["Default"] for row in rows}
    expected = {name: str(spec["default"]) for name, spec in input_specs.items()}
    assert documented == expected


def test_readme_documents_every_action_output() -> None:
    action = _action_document()
    output_specs = action["outputs"]
    assert isinstance(output_specs, dict)
    headers, rows = _markdown_table(README.read_text(encoding="utf-8"), "Outputs")
    assert "Output" in headers
    assert len(rows) == len(output_specs)
    assert {row["Output"] for row in rows} == set(output_specs)


def test_example_configuration_matches_runtime_defaults() -> None:
    path = ROOT / "examples/slopguard.toml"
    example = parse_config(path.read_text(encoding="utf-8"), source=str(path))

    assert example == SlopGuardConfig()


def test_action_references_follow_the_project_major_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    major = Version(project["project"]["version"]).major
    expected = f"v{major}"
    paths = [
        README,
        *(
            path
            for path in (ROOT / "examples").rglob("*")
            if path.is_file() and path.suffix in {".md", ".toml", ".yaml", ".yml"}
        ),
    ]
    documents = [(path, path.read_text(encoding="utf-8")) for path in paths if path.is_file()]
    references = [
        (path, reference)
        for path, content in documents
        for reference in ACTION_REFERENCE.findall(content)
    ]
    uses = [
        (path, reference)
        for path, content in documents
        for reference in ACTION_USE.findall(content)
    ]

    assert uses, "expected published Action use steps in the README or examples"
    assert references, "expected published Action references in the README or examples"
    assert all(reference == expected for _, reference in references), references


def test_readme_workflows_match_the_committed_example_timeout_and_checkout() -> None:
    example = yaml.load(
        (ROOT / "examples/workflow.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    expected_job = example["jobs"]["slopguard"]
    expected_checkout = next(
        item for item in _walk(expected_job) if item.get("uses", "").startswith("actions/checkout@")
    )
    snippets = re.findall(r"(?ms)^```yaml\s*\n(.*?)^```\s*$", README.read_text(encoding="utf-8"))
    workflows = [
        yaml.load(snippet, Loader=yaml.BaseLoader) for snippet in snippets if "jobs:" in snippet
    ]

    assert workflows
    for workflow in workflows:
        assert isinstance(workflow, dict)
        for job in workflow["jobs"].values():
            assert job["timeout-minutes"] == expected_job["timeout-minutes"]
            checkout = next(
                item for item in _walk(job) if item.get("uses", "").startswith("actions/checkout@")
            )
            assert checkout["with"]["fetch-depth"] == expected_checkout["with"]["fetch-depth"]
            assert (
                checkout["with"]["persist-credentials"]
                == expected_checkout["with"]["persist-credentials"]
            )
            assert checkout["uses"] == expected_checkout["uses"]


def test_project_classifier_matches_stable_release_status() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "Development Status :: 5 - Production/Stable" in project["project"]["classifiers"]


def test_readme_relative_links_resolve_to_repository_files() -> None:
    markdown = README.read_text(encoding="utf-8")
    for match in INLINE_LINK.finditer(markdown):
        destination = match.group(1).strip().strip("<>").split(maxsplit=1)[0]
        parsed = urlsplit(destination)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = (ROOT / unquote(parsed.path)).resolve()
        assert target.is_relative_to(ROOT), f"README link escapes repository: {destination}"
        assert target.is_file(), f"README link does not resolve to a file: {destination}"


def test_repository_text_has_no_internal_tracking_or_school_language() -> None:
    forbidden = "|".join(
        (
            r"\b" + "SG" + r"-?[0-9]{2}\b",
            r"\b" + "VOI" + "-",
            r"(?-i:\b" + "Lin" + "ear" + r"\b)",
            "FAST" + "-" + "NUCES",
            "term" + r"\s+" + "project",
            "Group" + r"\s+" + "4",
            "sem" + "ester",
            r"\bcourse\w*\b",
            r"\b" + "M" + "VP" + r"\b",
            "later" + r"\s+" + "stage",
        )
    )
    product_name = "Lin" + "ear"
    lowercase_usage = "lin" + "ear" + " scan"
    uppercase_usage = "LIN" + "EAR time"
    assert re.search(forbidden, product_name, flags=re.IGNORECASE)
    assert not re.search(forbidden, lowercase_usage, flags=re.IGNORECASE)
    assert not re.search(forbidden, uppercase_usage, flags=re.IGNORECASE)

    tracked_files = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=10,
    )
    paths = {path.decode("utf-8") for path in tracked_files.stdout.split(b"\0") if path}
    paths.add(Path(__file__).resolve().relative_to(ROOT).as_posix())

    violations = []
    for relative_path in sorted(paths):
        if (
            relative_path == "uv.lock"
            or relative_path.startswith("evals/data/")
            or relative_path.startswith("src/slopguard/data/")
        ):
            continue
        content = (ROOT / relative_path).read_bytes()
        if b"\0" in content:
            continue
        text = content.decode("utf-8", errors="replace")
        if relative_path == "CHANGELOG.md":
            text = re.sub(r"!?\[[^\]]+\]\([^)]*\)", "", text)
            text = re.sub(r"(?m)^\[[^\]]+\]:.*$", "", text)
        for line_number, line in enumerate(text.splitlines(), start=1):
            if re.search(forbidden, line, flags=re.IGNORECASE):
                violations.append(f"{relative_path}:{line_number}:{line}")

    assert not violations, (
        "repository text contains internal tracking or school references:\n" + "\n".join(violations)
    )
