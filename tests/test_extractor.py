"""Fixtures for supported syntax, source context, and diff-only behavior."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from slopguard.extractor import ChangedFile, extract_dependencies

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SCHEMA = json.loads((ROOT / "schemas/dependency-result.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_extracts_added_candidates_and_deduplicates_with_all_sources() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "requirements.txt",
                _read(FIXTURES / "requirements/base.txt"),
                _read(FIXTURES / "requirements/head.txt"),
            ),
            ChangedFile(
                "constraints.txt",
                _read(FIXTURES / "constraints/base.txt"),
                _read(FIXTURES / "constraints/head.txt"),
            ),
            ChangedFile(
                "pyproject.toml",
                _read(FIXTURES / "pyproject/base.toml"),
                _read(FIXTURES / "pyproject/head.toml"),
            ),
            ChangedFile(
                "src/app.py",
                _read(FIXTURES / "imports/base.py.txt"),
                _read(FIXTURES / "imports/head.py.txt"),
            ),
        ]
    )

    by_name = {candidate["normalized_name"]: candidate for candidate in result.candidates}
    assert set(by_name) == {
        "fast-api",
        "shared-lib",
        "numpy",
        "httpx",
        "opencv-python",
        "python-dateutil",
    }
    assert len(by_name["httpx"]["source_locations"]) == 2
    assert by_name["httpx"]["source_locations"][0]["source_type"] == "pyproject"
    assert by_name["opencv-python"]["requested_name"] == "cv2"
    assert by_name["opencv-python"]["source_locations"] == [
        {"file": "src/app.py", "line": 3, "source_type": "import"}
    ]
    assert "requests" not in by_name
    assert "remove-me" not in by_name
    assert "common-lib" not in by_name
    assert all(not list(VALIDATOR.iter_errors(candidate)) for candidate in result.candidates)


def test_malformed_and_url_requirements_return_warnings_without_crashing() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "requirements.txt",
                "requests==2.31.0\n",
                _read(FIXTURES / "requirements/head.txt"),
            )
        ]
    )

    assert {candidate["normalized_name"] for candidate in result.candidates} == {
        "fast-api",
        "shared-lib",
    }
    assert len(result.warnings) == 3
    assert all(warning.file == "requirements.txt" for warning in result.warnings)
    assert all(warning.line is not None for warning in result.warnings)


def test_comments_and_removed_lines_do_not_create_candidates() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "requirements.txt",
                "old-package==1\n",
                "# only a comment was added\n",
            )
        ]
    )

    assert result.candidates == ()
    assert result.warnings == ()


def test_malformed_pyproject_returns_warning() -> None:
    result = extract_dependencies(
        [ChangedFile("pyproject.toml", "", "[project\ndependencies = ['broken']\n")]
    )

    assert result.candidates == ()
    assert result.warnings
    assert "Invalid TOML" in result.warnings[0].message


def test_crlf_pyproject_extracts_added_dependencies() -> None:
    base = _read(FIXTURES / "pyproject/base.toml").replace("\n", "\r\n")
    head = _read(FIXTURES / "pyproject/head.toml").replace("\n", "\r\n")

    result = extract_dependencies([ChangedFile("pyproject.toml", base, head)])

    assert {candidate["normalized_name"] for candidate in result.candidates} == {"httpx"}
    assert result.warnings == ()


def test_multiline_import_addition_is_reported_as_unsupported() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "src/app.py",
                "",
                "from package_name import (\n    first,\n    second,\n)\n",
            )
        ]
    )

    assert result.candidates == ()
    assert result.warnings
    assert all("Multiline import syntax" in warning.message for warning in result.warnings)


def test_extraction_example_matches_shared_contract() -> None:
    example = json.loads(
        (ROOT / "examples" / "extracted-dependency.json").read_text(encoding="utf-8")
    )

    VALIDATOR.validate(example)
