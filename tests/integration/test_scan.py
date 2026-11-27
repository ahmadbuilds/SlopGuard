"""End-to-end scan orchestration against temporary Git repositories."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from slopguard.config import ConfigError
from slopguard.contracts import load_result_schema, load_scan_report_schema
from slopguard.git import GitRepository
from slopguard.registry import RegistryLookupResult
from slopguard.scan import run_scan

NOW = datetime(2026, 5, 1, tzinfo=UTC)


class FakeClient:
    def __init__(self, not_found: set[str] | None = None) -> None:
        self.not_found = not_found or set()
        self.lookups: list[str] = []

    def lookup(self, name: str) -> RegistryLookupResult:
        self.lookups.append(name)
        missing = name in self.not_found
        return RegistryLookupResult(
            normalized_name=name,
            status="not_found" if missing else "exists",
            metadata=None
            if missing
            else {
                "first_release_at": "2010-01-01T00:00:00Z",
                "release_count": 100,
                "latest_release_at": "2026-01-01T00:00:00Z",
                "project_urls": {"Source": "https://example.invalid/source"},
                "maintainer_hints": [],
                "source_hints": ["https://example.invalid/source"],
            },
            latency_ms=1.0,
            attempts=1,
            http_status=404 if missing else 200,
            retry_after=None,
            rate_limit_remaining=None,
            error=None,
        )


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _repo(short_tmp_path: Path, *, requirements: str = "") -> tuple[Path, str]:
    root = short_tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "SlopGuard tests")
    _git(root, "config", "user.email", "tests@example.invalid")
    (root / "requirements.txt").write_text(requirements, encoding="utf-8")
    _git(root, "add", "requirements.txt")
    _git(root, "commit", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _scan(root: Path, base: str, head: str, client: FakeClient, *, repository: str | None = None):
    return run_scan(
        GitRepository(root),
        repository=repository or root.name,
        base=base,
        head=head,
        client=client,
        now=NOW,
    )


def test_scan_finds_new_dependency_and_report_validates(short_tmp_path: Path) -> None:
    root, base = _repo(short_tmp_path)
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    head = _commit(root, "add dependency")
    client = FakeClient()

    report = _scan(root, base, head, client, repository="example/project")

    assert report["decision"] == "pass"
    assert report["repository"] == "example/project"
    assert [result["normalized_name"] for result in report["results"]] == ["requests"]
    assert client.lookups == ["requests"]
    result_schema = load_result_schema()
    registry = Registry().with_resource(result_schema["$id"], Resource.from_contents(result_schema))
    validator = Draft202012Validator(load_scan_report_schema(), registry=registry)
    validator.validate(report)


def test_scan_uses_base_tip_policy_and_reports_head_config_change(short_tmp_path: Path) -> None:
    root, _ = _repo(short_tmp_path)
    (root / "slopguard.toml").write_text("[policy]\nmode = 'block'\n", encoding="utf-8")
    base = _commit(root, "enable blocking")
    (root / "requirements.txt").write_text("reqeusts\n", encoding="utf-8")
    (root / "slopguard.toml").write_text("[policy]\nmode = 'warn'\n", encoding="utf-8")
    head = _commit(root, "add look-alike and weaken head config")

    report = _scan(root, base, head, FakeClient(not_found={"reqeusts"}))

    assert report["decision"] == "block"
    assert report["config_changed"] is True
    assert report["config_source"] == "slopguard.toml"


def test_policy_tightened_after_fork_applies_to_open_pull_request(short_tmp_path: Path) -> None:
    root, fork = _repo(short_tmp_path)
    _git(root, "switch", "-c", "feature")
    (root / "requirements.txt").write_text("reqeusts\n", encoding="utf-8")
    head = _commit(root, "add a look-alike")
    _git(root, "switch", "main")
    (root / "slopguard.toml").write_text("[policy]\nmode = 'block'\n", encoding="utf-8")
    base = _commit(root, "tighten policy")

    report = _scan(root, base, head, FakeClient(not_found={"reqeusts"}))

    assert report["merge_base_sha"] == fork
    assert report["decision"] == "block"
    assert report["config_source"] == "slopguard.toml"
    assert report["config_changed"] is True


def test_scan_no_supported_changes_is_a_pass_without_lookups(short_tmp_path: Path) -> None:
    root, base = _repo(short_tmp_path)
    (root / "README.md").write_text("Documentation only.\n", encoding="utf-8")
    head = _commit(root, "docs")
    client = FakeClient()

    report = _scan(root, base, head, client)

    assert report["decision"] == "pass"
    assert report["results"] == []
    assert report["lookups"] == {"used": 0, "budget": 500}
    assert client.lookups == []


def test_invalid_merge_base_configuration_fails_closed(short_tmp_path: Path) -> None:
    root, _ = _repo(short_tmp_path)
    (root / "slopguard.toml").write_text("[policy]\nmode = 'ignore'\n", encoding="utf-8")
    base = _commit(root, "invalid config")
    (root / "README.md").write_text("No supported change.\n", encoding="utf-8")
    head = _commit(root, "head")

    with pytest.raises(ConfigError):
        _scan(root, base, head, FakeClient())


def test_renamed_requirements_file_does_not_report_existing_dependency(
    short_tmp_path: Path,
) -> None:
    root, base = _repo(short_tmp_path, requirements="requests\n")
    (root / "requirements.txt").rename(root / "constraints.txt")
    head = _commit(root, "rename manifest")
    client = FakeClient()

    report = _scan(root, base, head, client)

    assert report["results"] == []
    assert client.lookups == []


def test_base_branch_commits_after_fork_are_not_scanned(short_tmp_path: Path) -> None:
    root, fork = _repo(short_tmp_path)
    _git(root, "switch", "-c", "feature")
    (root / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    head = _commit(root, "feature dependency")
    _git(root, "switch", "main")
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    base = _commit(root, "base dependency")

    report = _scan(root, base, head, FakeClient())

    assert report["merge_base_sha"] == fork
    assert [item["normalized_name"] for item in report["results"]] == ["httpx"]


def test_lookup_budget_marks_excess_candidates_unknown(short_tmp_path: Path) -> None:
    root, _ = _repo(short_tmp_path)
    (root / "slopguard.toml").write_text(
        "[registry]\nmax_lookups = 1\nmax_concurrency = 1\n", encoding="utf-8"
    )
    base = _commit(root, "lookup budget")
    (root / "requirements.txt").write_text("httpx\nrequests\n", encoding="utf-8")
    head = _commit(root, "two dependencies")
    client = FakeClient()

    report = _scan(root, base, head, client)

    assert client.lookups == ["httpx"]
    assert report["lookups"] == {"used": 1, "budget": 1}
    assert report["results"][1]["registry_status"] == "transient_error"
    assert "budget exhausted" in report["results"][1]["registry_diagnostics"]["error"]


def test_expired_allowlist_entries_are_reported_as_warnings(short_tmp_path: Path) -> None:
    root, _ = _repo(short_tmp_path)
    (root / "slopguard.toml").write_text(
        "[[allowlist]]\nname = 'old-package'\nreason = 'temporary'\nexpires = 2026-04-30\n",
        encoding="utf-8",
    )
    base = _commit(root, "expired exception")
    (root / "README.md").write_text("Documentation only.\n", encoding="utf-8")
    head = _commit(root, "docs")

    report = _scan(root, base, head, FakeClient())

    assert any(item["kind"] == "expired_allowlist" for item in report["warnings"])


def test_scan_report_is_deterministic_for_fixed_repository_and_time(short_tmp_path: Path) -> None:
    root, base = _repo(short_tmp_path)
    (root / "requirements.txt").write_text("requests\nhttpx\n", encoding="utf-8")
    head = _commit(root, "two dependencies")

    first = _scan(root, base, head, FakeClient())
    second = _scan(root, base, head, FakeClient())

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_project_and_test_helper_imports_are_treated_as_local(short_tmp_path: Path) -> None:
    root, _ = _repo(short_tmp_path)
    (root / "pyproject.toml").write_text("[project]\nname = 'slopguard'\n", encoding="utf-8")
    (root / "slopguard.toml").write_text("[policy]\nmode = 'warn'\n", encoding="utf-8")
    base = _commit(root, "project metadata")
    (root / "tests").mkdir()
    (root / "tests" / "conftest_helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_a.py").write_text(
        "import conftest_helpers\nfrom slopguard.registry import PyPIClient\n",
        encoding="utf-8",
    )
    head = _commit(root, "test helper imports")
    client = FakeClient()

    report = _scan(root, base, head, client)

    assert report["results"] == []
    assert client.lookups == []


def test_nested_source_module_does_not_hide_external_import(short_tmp_path: Path) -> None:
    root, _ = _repo(short_tmp_path)
    nested_module = root / "src" / "unrelated" / "requests.py"
    nested_module.parent.mkdir(parents=True)
    nested_module.write_text("VALUE = 1\n", encoding="utf-8")
    base = _commit(root, "add unrelated namespace module")
    (root / "app.py").write_text("import requests\n", encoding="utf-8")
    head = _commit(root, "add external dependency import")
    client = FakeClient()

    report = _scan(root, base, head, client)

    assert [result["normalized_name"] for result in report["results"]] == ["requests"]
    assert client.lookups == ["requests"]


def test_test_helper_does_not_hide_external_import_in_application_code(
    short_tmp_path: Path,
) -> None:
    root, _ = _repo(short_tmp_path)
    tests_requests = root / "tests" / "requests.py"
    tests_requests.parent.mkdir()
    tests_requests.write_text("VALUE = 1\n", encoding="utf-8")
    (tests_requests.parent / "test_a.py").write_text("import requests\n", encoding="utf-8")
    base = _commit(root, "add test module named requests")
    (root / "app.py").write_text("import requests\n", encoding="utf-8")
    head = _commit(root, "add application dependency import")
    client = FakeClient()

    report = _scan(root, base, head, client)

    assert [result["normalized_name"] for result in report["results"]] == ["requests"]
    assert client.lookups == ["requests"]


def test_mapped_imports_with_distribution_names_scan_successfully(short_tmp_path: Path) -> None:
    root, base = _repo(short_tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_imports.py").write_text(
        "import yaml\nimport jwt\nimport google.cloud.storage\n", encoding="utf-8"
    )
    head = _commit(root, "add mapped imports")

    report = _scan(root, base, head, FakeClient())

    assert [result["normalized_name"] for result in report["results"]] == [
        "google-cloud-storage",
        "pyjwt",
        "pyyaml",
    ]


def test_depth_one_pull_request_merge_checkout_scans_after_fetching_required_commits(
    shallow_merge_checkout: tuple[Path, str, str, str],
) -> None:
    root, base, head, common = shallow_merge_checkout

    report = run_scan(
        GitRepository(root),
        repository="example/shallow-pr",
        base=base,
        head=head,
        client=FakeClient(),
        now=NOW,
    )

    assert report["merge_base_sha"] == common
    assert [item["normalized_name"] for item in report["results"]] == ["requests"]


def test_result_without_scored_decision_is_an_internal_error(
    short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import slopguard.scan as scan_module

    root, base = _repo(short_tmp_path)
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    head = _commit(root, "dependency")

    def unscored(result, config, *, now):
        return result

    monkeypatch.setattr(scan_module, "score_result", unscored)
    with pytest.raises(RuntimeError, match="without a decision"):
        _scan(root, base, head, FakeClient())
