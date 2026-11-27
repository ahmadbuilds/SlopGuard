"""Fixtures for supported syntax, source context, and diff-only behavior."""

from __future__ import annotations

import json
import warnings
from collections.abc import Generator
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import slopguard.import_names as import_names
from slopguard.contracts import load_result_schema
from slopguard.extractor import (
    ChangedFile,
    extract_dependencies,
    is_supported_path,
    local_modules_by_directory,
    local_modules_from_paths,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SCHEMA = load_result_schema()
VALIDATOR = Draft202012Validator(SCHEMA)


@pytest.fixture
def generated_import_name_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    module_distributions = {
        "jwt": "pyjwt",
        "dotenv": "python-dotenv",
        "Crypto": "pycryptodome",
        "attr": "attrs",
        "serial": "pyserial",
        "skimage": "scikit-image",
        "magic": "python-magic",
        "docx": "python-docx",
        "pptx": "python-pptx",
        "jose": "python-jose",
        "OpenSSL": "pyopenssl",
        "usb": "pyusb",
        "git": "gitpython",
        "github": "pygithub",
        "telegram": "python-telegram-bot",
        "slugify": "python-slugify",
        "MySQLdb": "mysqlclient",
        "fitz": "pymupdf",
        "zmq": "pyzmq",
        "socks": "pysocks",
        "nacl": "pynacl",
        "dns": "dnspython",
        "kafka": "kafka-python",
        "googleapiclient": "google-api-python-client",
        "pkg_resources": "setuptools",
        "mpl_toolkits": "matplotlib",
        "win32api": "pywin32",
        "win32con": "pywin32",
        "ldap": "python-ldap",
        "google.protobuf": "protobuf",
        "google.cloud.storage": "google-cloud-storage",
        "google.generativeai": "google-generativeai",
        "azure.storage.blob": "azure-storage-blob",
        "azure.identity": "azure-identity",
    }
    documents = {
        "import_name_overrides.json": {"overrides": {}},
        "import_names.json": {
            "mappings": {
                module: {"distribution": distribution, "wheel": "fixture.whl"}
                for module, distribution in module_distributions.items()
            },
            "namespace_roots": ["google", "azure", "zope", "backports"],
        },
        "stdlib_modules.json": {"modules": []},
    }

    class ResourceFile:
        def __init__(self, contents: dict[str, object]):
            self.contents = contents

        def joinpath(self, *_parts: str) -> ResourceFile:
            return self

        def read_text(self, _encoding: str) -> str:
            return json.dumps(self.contents)

    class PackageResources:
        def joinpath(self, *_parts: str) -> ResourceFile:
            return ResourceFile(documents[_parts[-1]])  # type: ignore[arg-type]

    monkeypatch.setattr(import_names.resources, "files", lambda _package: PackageResources())
    import_names._mapping_data.cache_clear()
    import_names.stdlib_modules.cache_clear()
    yield
    import_names._mapping_data.cache_clear()
    import_names.stdlib_modules.cache_clear()


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
    assert (
        next(
            warning
            for warning in result.warnings
            if "project.dynamic value is invalid" in warning.message
        ).line
        == 2
    )


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


def test_multiline_import_addition_is_reported_as_candidate() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "src/app.py",
                "",
                "from fakepkg_xyz import (\n    first,\n    second,\n)\n",
            )
        ]
    )

    assert [candidate["normalized_name"] for candidate in result.candidates] == ["fakepkg-xyz"]
    assert result.candidates[0]["source_locations"] == [
        {"file": "src/app.py", "line": 1, "source_type": "import"}
    ]
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("from fakepkg_xyz import (\n    a,\n    b,\n)\n", "fakepkg-xyz"),
        ("import os, \\\n    fakepkg_xyz\n", "fakepkg-xyz"),
    ],
)
def test_ast_imports_capture_multiline_and_backslash_imports(content: str, expected: str) -> None:
    result = extract_dependencies([ChangedFile("app.py", "", content)])
    assert [candidate["normalized_name"] for candidate in result.candidates] == [expected]
    assert result.warnings == ()


def test_python_imports_are_compared_without_line_cap() -> None:
    base = "# base\n" * 1_001
    result = extract_dependencies([ChangedFile("app.py", base, base + "import fakepkg_xyz\n")])
    assert [candidate["normalized_name"] for candidate in result.candidates] == ["fakepkg-xyz"]
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("base", "head"),
    [
        ("from fakepkg import a\n", "from fakepkg import a\nimport fakepkg.sub\n"),
        ("import yaml\n", "import yaml\nfrom yaml import safe_load\n"),
    ],
)
def test_imports_resolving_to_same_distribution_are_not_new(base: str, head: str) -> None:
    result = extract_dependencies([ChangedFile("app.py", base, head)])
    assert result.candidates == ()
    assert result.warnings == ()


@pytest.mark.parametrize(
    "content",
    [
        "reqeusts==2.31.0 \\\n    --hash=sha256:abc\n",
        "reqeusts==2.31.0 --hash=sha256:abc\n",
    ],
)
def test_requirement_hash_options_are_removed_before_parsing(content: str) -> None:
    result = extract_dependencies([ChangedFile("requirements.txt", "", content)])
    assert [candidate["normalized_name"] for candidate in result.candidates] == ["reqeusts"]
    assert result.warnings == ()


def test_requirement_file_under_requirements_directory_is_recognized() -> None:
    result = extract_dependencies([ChangedFile("requirements/base.txt", "", "numpyy\n")])
    assert [candidate["normalized_name"] for candidate in result.candidates] == ["numpyy"]
    assert result.warnings == ()


def test_requirement_continuation_uses_first_physical_line_number() -> None:
    result = extract_dependencies(
        [ChangedFile("requirements.txt", "", "# header\nreqeusts \\\n  --hash=sha256:abc\n")]
    )
    assert result.candidates[0]["source_locations"] == [
        {"file": "requirements.txt", "line": 2, "source_type": "requirements"}
    ]


def test_extra_index_url_has_specific_warning() -> None:
    result = extract_dependencies(
        [ChangedFile("requirements.txt", "", "--extra-index-url https://x/simple\n")]
    )
    assert result.candidates == ()
    assert len(result.warnings) == 1
    assert result.warnings[0].message == (
        "Package index option changes where packages are installed from; review it manually."
    )


@pytest.mark.parametrize(
    "content",
    [
        '[dependency-groups]\ndev = ["pytset>=8"]\n',
        '[build-system]\nrequires = ["setuptools-scmm"]\n',
    ],
)
def test_pyproject_additional_dependency_arrays(content: str) -> None:
    result = extract_dependencies([ChangedFile("pyproject.toml", "", content)])
    assert len(result.candidates) == 1
    assert result.candidates[0]["source_locations"][0]["source_type"] == "pyproject"
    assert result.warnings == ()


def test_dependency_group_include_table_is_not_a_candidate() -> None:
    result = extract_dependencies(
        [
            ChangedFile(
                "pyproject.toml",
                "",
                '[dependency-groups]\ntest = ["pytest>=8"]\ndev = [{include-group = "test"}]\n',
            )
        ]
    )
    assert [candidate["normalized_name"] for candidate in result.candidates] == ["pytest"]
    assert result.candidates[0]["source_locations"] == [
        {"file": "pyproject.toml", "line": 2, "source_type": "pyproject"}
    ]
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("setup.py", "setup(install_requires=['reqeusts'])\n"),
        ("setup.cfg", "[options]\ninstall_requires =\n  reqeusts\n"),
        ("Pipfile", "[packages]\nreqeusts = '*'\n"),
    ],
)
def test_unsupported_dependency_files_warn_without_import_candidates(
    path: str, content: str
) -> None:
    result = extract_dependencies([ChangedFile(path, "", content)])
    assert result.candidates == ()
    assert len(result.warnings) == 1
    assert result.warnings[0].message == (
        f"Dependency changes in {path} are not analysed by SlopGuard; review them manually."
    )


def test_optional_local_modules_suppress_first_party_imports() -> None:
    result = extract_dependencies(
        [ChangedFile("tests/test_x.py", "", "from slopguard.registry import PyPIClient\n")],
        local_modules={"slopguard"},
    )
    assert result.candidates == ()


@pytest.mark.parametrize(
    ("path", "supported"),
    [
        ("pyproject.toml", True),
        ("requirements/base.in", True),
        ("requirements.txt", True),
        ("constraints-prod.txt", True),
        ("src/package.py", True),
        ("setup.cfg", True),
        ("Pipfile", True),
        ("notes.txt", False),
        ("README.md", False),
    ],
)
def test_supported_path_predicate(path: str, supported: bool) -> None:
    assert is_supported_path(path) is supported


def test_local_modules_from_repository_paths() -> None:
    assert local_modules_from_paths(
        [
            "src/slopguard/__init__.py",
            "lib/helpers.py",
            "tools/run.py",
            "tests/conftest_helpers.py",
            "README.md",
        ],
        project_name="My-Project",
    ) == {"slopguard", "helpers", "tools", "tests", "my_project"}


def test_nested_namespace_module_is_not_treated_as_a_top_level_module() -> None:
    assert local_modules_from_paths(["src/unrelated/requests.py"]) == {"unrelated"}


def test_direct_modules_under_supported_import_roots_are_recognized() -> None:
    assert local_modules_from_paths(
        ["requests.py", "src/yaml.py", "lib/jwt.py", "tests/conftest_helpers.py"]
    ) == {"requests", "yaml", "jwt", "tests"}


def test_modules_are_scoped_to_the_directory_that_imports_them() -> None:
    assert local_modules_by_directory(
        [
            "tests/test_a.py",
            "tests/conftest_helpers.py",
            "tests/requests.py",
            "scripts/run.py",
            "scripts/helpers.py",
        ]
    ) == {
        "tests": {"test_a", "conftest_helpers", "requests"},
        "scripts": {"run", "helpers"},
    }


def test_module_stem_from_paths_suppresses_importable_local_helper() -> None:
    local = local_modules_from_paths(["tests/conftest_helpers.py"])
    result = extract_dependencies(
        [ChangedFile("tests/test_a.py", "", "import conftest_helpers\n")],
        local_modules=local,
        local_modules_by_directory=local_modules_by_directory(
            ["tests/test_a.py", "tests/conftest_helpers.py"]
        ),
    )
    assert result.candidates == ()
    assert result.warnings == ()


def test_local_module_path_helper_suppresses_first_party_import() -> None:
    local = local_modules_from_paths(["src/slopguard/registry.py"])
    result = extract_dependencies(
        [ChangedFile("tests/test_x.py", "", "from slopguard.registry import PyPIClient\n")],
        local_modules=local,
    )
    assert result.candidates == ()


def test_python_file_inside_package_does_not_add_its_stem_as_local() -> None:
    local = local_modules_from_paths(
        ["src/slopguard/__init__.py", "src/slopguard/sub/__init__.py", "src/slopguard/sub/x.py"]
    )

    assert "slopguard" in local
    assert "x" not in local


def test_unchanged_top_level_local_test_helper_is_suppressed() -> None:
    result = extract_dependencies(
        [ChangedFile("tests/test_a.py", "", "import conftest_helpers\n")],
        local_modules={"conftest_helpers"},
    )
    assert result.candidates == ()


@pytest.mark.parametrize(
    ("module", "distribution"),
    [
        ("jwt", "pyjwt"),
        ("dotenv", "python-dotenv"),
        ("Crypto", "pycryptodome"),
        ("attr", "attrs"),
        ("serial", "pyserial"),
        ("skimage", "scikit-image"),
        ("magic", "python-magic"),
        ("docx", "python-docx"),
        ("pptx", "python-pptx"),
        ("jose", "python-jose"),
        ("OpenSSL", "pyopenssl"),
        ("usb", "pyusb"),
        ("git", "gitpython"),
        ("github", "pygithub"),
        ("telegram", "python-telegram-bot"),
        ("slugify", "python-slugify"),
        ("MySQLdb", "mysqlclient"),
        ("fitz", "pymupdf"),
        ("zmq", "pyzmq"),
        ("socks", "pysocks"),
        ("nacl", "pynacl"),
        ("dns", "dnspython"),
        ("kafka", "kafka-python"),
        ("googleapiclient", "google-api-python-client"),
        ("pkg_resources", "setuptools"),
        ("mpl_toolkits", "matplotlib"),
        ("win32api", "pywin32"),
        ("win32con", "pywin32"),
        ("ldap", "python-ldap"),
    ],
)
def test_import_names_map_to_distributions(
    module: str, distribution: str, generated_import_name_fixture: None
) -> None:
    result = extract_dependencies([ChangedFile("app.py", "", f"import {module}\n")])
    assert [candidate["normalized_name"] for candidate in result.candidates] == [distribution]


@pytest.mark.parametrize(
    ("module", "distribution"),
    [
        ("google.protobuf", "protobuf"),
        ("google.cloud.storage", "google-cloud-storage"),
        ("google.generativeai", "google-generativeai"),
        ("azure.storage.blob", "azure-storage-blob"),
        ("azure.identity", "azure-identity"),
    ],
)
def test_dotted_import_prefixes_map_to_distributions(
    module: str, distribution: str, generated_import_name_fixture: None
) -> None:
    result = extract_dependencies([ChangedFile("app.py", "", f"import {module}\n")])
    assert [candidate["normalized_name"] for candidate in result.candidates] == [distribution]


@pytest.mark.parametrize("module", ["google", "azure", "zope", "backports"])
def test_unmapped_namespace_root_warns(module: str, generated_import_name_fixture: None) -> None:
    result = extract_dependencies([ChangedFile("app.py", "", f"import {module}\n")])
    assert result.candidates == ()
    assert [warning.message for warning in result.warnings] == [
        f"Namespace import {module} could not be mapped to a PyPI distribution."
    ]


def test_removed_standard_library_modules_are_not_dependencies() -> None:
    result = extract_dependencies(
        [ChangedFile("app.py", "", "import distutils\nimport imp\nimport cgi\n")]
    )
    assert result.candidates == ()
    assert result.warnings == ()


def test_invalid_distribution_import_is_warned_and_not_a_candidate() -> None:
    result = extract_dependencies([ChangedFile("app.py", "", "import _typeshed\n")])

    assert result.candidates == ()
    assert [(warning.line, warning.message) for warning in result.warnings] == [
        (1, "'_typeshed' cannot be a PyPI distribution name; not checked")
    ]


def test_invalid_distribution_requirement_is_warned_and_not_a_candidate() -> None:
    result = extract_dependencies([ChangedFile("requirements.txt", "", "_typeshed\n")])

    assert result.candidates == ()
    assert (
        result.warnings[0].message == "'_typeshed' cannot be a PyPI distribution name; not checked"
    )


def test_python_parser_warnings_are_suppressed_for_scanned_files() -> None:
    source = r'VALUE = "\ "' + "\nimport fixture_dependency\n"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = extract_dependencies([ChangedFile("app.py", "", source)])

    assert result.candidates
    assert not any(
        issubclass(item.category, (SyntaxWarning, DeprecationWarning)) for item in caught
    )


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


@pytest.mark.parametrize("local_path", ["src/unrelated/helper.py", "tests/helper.py"])
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
        (FIXTURES / "contract" / "extracted-dependency.json").read_text(encoding="utf-8")
    )

    VALIDATOR.validate(example)
