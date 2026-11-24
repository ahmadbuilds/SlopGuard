"""Fixtures for supported syntax, source context, and diff-only behavior."""

import json
from pathlib import Path

import pytest
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


def test_invalid_dynamic_type_warns_instead_of_crashing() -> None:
    result = extract_dependencies(
        [ChangedFile("pyproject.toml", "", '[project]\ndynamic = 1\ndependencies = ["httpx"]\n')]
    )

    assert {candidate["normalized_name"] for candidate in result.candidates} == {"httpx"}
    assert any("project.dynamic value is invalid" in warning.message for warning in result.warnings)


def test_dotted_optional_dependency_key_is_extracted() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "pyproject.toml",
                "",
                '[project]\nname = "example"\noptional-dependencies.dev = ["risky-package>=1"]\n',
            )
        ]
    )

    assert {candidate["normalized_name"] for candidate in result.candidates} == {"risky-package"}
    assert result.candidates[0]["source_locations"] == [
        {"file": "pyproject.toml", "line": 3, "source_type": "pyproject"}
    ]


def test_crlf_pyproject_extracts_added_dependencies() -> None:
    base = _read(FIXTURES / "pyproject/base.toml").replace("\n", "\r\n")
    head = _read(FIXTURES / "pyproject/head.toml").replace("\n", "\r\n")

    result = extract_dependencies([ChangedFile("pyproject.toml", base, head)])

    assert {candidate["normalized_name"] for candidate in result.candidates} == {"httpx"}
    assert result.warnings == ()


def test_oversized_diff_returns_warning_without_emitting_candidates() -> None:
    base = "repeated-package==1\n" * 1_001
    head = f"{base}new-package==1\n"

    result = extract_dependencies([ChangedFile("requirements.txt", base, head)])

    assert result.candidates == ()
    assert len(result.warnings) == 1
    assert "safe diff size limit" in result.warnings[0].message


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


def test_new_manifest_dependency_is_not_suppressed_by_unchanged_import() -> None:
    result = extract_dependencies(
        [
            ChangedFile("requirements.txt", "", "requests==2.32.0\n"),
            ChangedFile("src/app.py", "import requests\n", "import requests\n# changed\n"),
        ]
    )

    assert [candidate["normalized_name"] for candidate in result.candidates] == ["requests"]
    assert result.candidates[0]["source_locations"] == [
        {"file": "requirements.txt", "line": 1, "source_type": "requirements"}
    ]


def test_import_text_inside_multiline_string_is_not_emitted() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "src/app.py",
                "",
                'DOCUMENTATION = """\nimport fake_package\n"""\n',
            )
        ]
    )

    assert result.candidates == ()
    assert result.warnings == ()


def test_inline_optional_dependencies_are_extracted() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "pyproject.toml",
                "",
                '[project]\nname = "example"\noptional-dependencies = {dev = ["pytest>=8"]}\n',
            )
        ]
    )

    assert [candidate["normalized_name"] for candidate in result.candidates] == ["pytest"]
    assert result.candidates[0]["source_locations"] == [
        {"file": "pyproject.toml", "line": 3, "source_type": "pyproject"}
    ]
    assert result.warnings == ()


def test_import_of_changed_local_module_is_not_a_pypi_candidate() -> None:
    result = extract_dependencies(
        [
            ChangedFile("src/helpers.py", "", "VALUE = 1\n"),
            ChangedFile("src/app.py", "", "import helpers\n"),
        ]
    )

    assert result.candidates == ()
    assert result.warnings == ()


@pytest.mark.parametrize("local_path", ["src/unrelated/requests.py", "tests/requests.py"])
def test_nested_local_module_does_not_hide_top_level_external_import(local_path: str) -> None:
    result = extract_dependencies(
        [
            ChangedFile(local_path, "", "VALUE = 1\n"),
            ChangedFile("src/app.py", "", "import requests\n"),
        ]
    )

    assert [candidate["normalized_name"] for candidate in result.candidates] == ["requests"]
    assert result.candidates[0]["source_locations"] == [
        {"file": "src/app.py", "line": 1, "source_type": "import"}
    ]


def test_aggregate_input_limit_stops_consuming_a_large_iterable() -> None:
    repeated_content = "x" * 1_000_000
    yielded = 0

    def changed_files():
        nonlocal yielded
        for index in range(100):
            yielded += 1
            yield ChangedFile(f"requirements-{index}.txt", "", repeated_content)

    result = extract_dependencies(changed_files())

    assert yielded == 21
    assert result.candidates == ()
    assert len(result.warnings) == 1
    assert "aggregate diff size limit" in result.warnings[0].message


def test_changed_file_count_limit_stops_consuming_a_large_iterable() -> None:
    yielded = 0

    def changed_files():
        nonlocal yielded
        for index in range(2_000):
            yielded += 1
            yield ChangedFile(f"file-{index}.txt", "", "")

    result = extract_dependencies(changed_files())

    assert yielded == 1_001
    assert result.candidates == ()
    assert len(result.warnings) == 1
    assert "changed-file count limit" in result.warnings[0].message


def test_extraction_example_matches_shared_contract() -> None:
    example = json.loads(
        (ROOT / "examples" / "extracted-dependency.json").read_text(encoding="utf-8")
    )

    VALIDATOR.validate(example)
