"""Offline validation tests for the manual historical-PR collector input."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import pytest

import evals.collect_prs as collect_module
from evals.collect_prs import _eligible_commits, load_repository_config
from slopguard.git import GitRepository


def test_repository_config_loads_https_sources_and_cutoff(tmp_path: Path) -> None:
    path = tmp_path / "repos.json"
    path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "url": "https://github.com/example/project.git",
                        "default_branch": "main",
                    }
                ],
                "per_repo": 12,
                "cutoff_date": "2025-01-01",
            }
        ),
        encoding="utf-8",
    )

    repos, per_repo, cutoff = load_repository_config(path)

    assert repos[0]["default_branch"] == "main"
    assert per_repo == 12
    assert cutoff.isoformat() == "2025-01-01"


@pytest.mark.parametrize("url", ["file:///tmp/repo", "https://user:token@example.invalid/repo"])
def test_repository_config_rejects_non_public_credential_free_https(
    tmp_path: Path, url: str
) -> None:
    path = tmp_path / "repos.json"
    path.write_text(
        json.dumps(
            {
                "repos": [{"url": url, "default_branch": "main"}],
                "per_repo": 1,
                "cutoff_date": "2025-01-01",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="HTTPS without user information"):
        load_repository_config(path)


def test_collector_selects_recent_first_parent_commit_with_supported_path(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix=".eval-git-", dir=Path.cwd()) as temporary:
        root = Path(temporary)
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "Eval fixture")
        _git(root, "config", "user.email", "eval@example.invalid")
        (root / "README.md").write_text("base\n", encoding="utf-8")
        _git(root, "add", "README.md")
        base = _commit(root, "base")
        (root / "requirements.txt").write_text("fixture-one\n", encoding="utf-8")
        first = _commit(root, "first dependency")
        (root / "requirements.txt").write_text("fixture-one\nfixture-two\n", encoding="utf-8")
        latest = _commit(root, "latest dependency")
        (root / "requirements.txt").write_text("# no dependency candidates\n", encoding="utf-8")
        _commit(root, "empty dependency manifest")
        (root / "README.md").write_text("documentation only\n", encoding="utf-8")
        _commit(root, "docs only")

        selected = _eligible_commits(
            GitRepository(root), "main", date.fromisoformat("2027-01-01"), 1
        )
        monkeypatch.setattr(collect_module, "MAX_COMMITS_TO_INSPECT", 2)
        limited = _eligible_commits(
            GitRepository(root), "main", date.fromisoformat("2027-01-01"), 1
        )

    assert len(selected.commits) == 1
    assert selected.commits[0].head == latest
    assert selected.commits[0].parent == first
    assert selected.commits[0].subject == "latest dependency"
    assert selected.commits[0].touched_path == "requirements.txt"
    assert selected.commits[0].candidates
    assert selected.inspected_commits == 3
    assert base != first
    assert limited.commits == ()
    assert limited.inspected_commits == 2
    assert limited.inspection_limit_reached is True


@pytest.mark.parametrize(
    ("helper_path", "script_path", "module", "expected_candidates"),
    [
        ("tools/helpers.py", "tools/run.py", "helpers", []),
        (
            "src/unrelated/requests.py",
            "src/app/app.py",
            "requests",
            [["requests"]],
        ),
    ],
)
def test_collector_uses_scanner_module_scope(
    tmp_path: Path,
    helper_path: str,
    script_path: str,
    module: str,
    expected_candidates: list[list[str]],
) -> None:
    root = tmp_path
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Eval fixture")
    _git(root, "config", "user.email", "eval@example.invalid")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    _commit(root, "base")
    helper = root / helper_path
    helper.parent.mkdir(parents=True)
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    _commit(root, "add helper module")
    script = root / script_path
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"import {module}\n", encoding="utf-8")
    _commit(root, "add script import")

    selection = _eligible_commits(GitRepository(root), "main", date.fromisoformat("2027-01-01"), 1)

    assert [
        [candidate["normalized_name"] for candidate in commit.candidates]
        for commit in selection.commits
    ] == expected_candidates


def test_collection_records_failed_repositories_and_continues(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repos": [
                    {"url": "https://github.com/example/fails.git", "default_branch": "main"},
                    {"url": "https://github.com/example/works.git", "default_branch": "main"},
                ],
                "per_repo": 1,
                "cutoff_date": "2025-01-01",
            }
        ),
        encoding="utf-8",
    )

    def collect_repo(_root, _checkout, *, url, **_kwargs):
        if "fails" in url:
            error = RuntimeError(f"{url}: Git command failed: remote unavailable")
            error.inspected_commits = 4
            raise error
        row = {"repo": url, "base": "a" * 40, "head": "b" * 40, "candidates": []}
        return [row], {
            "repo": url,
            "inspected_commits": 7,
            "inspection_limit_reached": False,
        }

    monkeypatch.setattr(collect_module, "_collect_repository", collect_repo)
    prs_path = tmp_path / "prs.jsonl"
    registry_path = tmp_path / "registry.jsonl.gz"

    failed = collect_module.collect_pull_requests(
        config, prs_path=prs_path, registry_path=registry_path
    )

    rows = [json.loads(line) for line in prs_path.read_text(encoding="utf-8").splitlines()]
    assert failed == 1
    assert rows[0]["record_type"] == "collection_summary"
    parameters = rows[0]["selection_parameters"]
    assert parameters["per_repo"] == 1
    assert parameters["cutoff_date"] == "2025-01-01"
    assert parameters["max_commits_to_inspect"] == collect_module.MAX_COMMITS_TO_INSPECT
    assert "whose diff yields at least one dependency candidate" in parameters["rule"]
    assert rows[0]["repositories"] == [
        {
            "repo": "https://github.com/example/fails.git",
            "inspected_commits": 4,
            "inspection_limit_reached": False,
            "failed": True,
        },
        {
            "repo": "https://github.com/example/works.git",
            "inspected_commits": 7,
            "inspection_limit_reached": False,
        },
    ]
    assert rows[0]["failed_repositories"] == [
        {
            "repo": "https://github.com/example/fails.git",
            "reason": "https://github.com/example/fails.git: Git command failed: remote unavailable",
            "inspected_commits": 4,
        }
    ]
    assert rows[1]["repo"] == "https://github.com/example/works.git"
    assert registry_path.exists()
    assert (
        collect_module.main(
            [
                "--repos",
                str(config),
                "--out",
                str(tmp_path / "partial-prs.jsonl"),
                "--registry",
                str(tmp_path / "partial-registry.jsonl.gz"),
            ]
        )
        == 0
    )


def test_git_error_sanitizes_and_bounds_stderr(monkeypatch, tmp_path: Path) -> None:
    def fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "git", stderr=b"fatal:\x1b[31mremote failed\n")

    monkeypatch.setattr(collect_module.subprocess, "run", fail)

    with pytest.raises(RuntimeError, match="fatal: remote failed") as error:
        collect_module._git(tmp_path, "status")

    assert "\x1b" not in str(error.value)
    assert len(collect_module._sanitize_git_stderr("x" * 5000)) == 1000


def test_collector_exits_nonzero_only_when_all_repositories_fail(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repos": [
                    {"url": "https://github.com/example/fails.git", "default_branch": "main"}
                ],
                "per_repo": 1,
                "cutoff_date": "2025-01-01",
            }
        ),
        encoding="utf-8",
    )

    def fail_repo(_root, _checkout, *, url, **_kwargs):
        raise RuntimeError(f"{url}: Git command failed: remote unavailable")

    monkeypatch.setattr(collect_module, "_collect_repository", fail_repo)
    result = collect_module.main(
        [
            "--repos",
            str(config),
            "--out",
            str(tmp_path / "prs.jsonl"),
            "--registry",
            str(tmp_path / "registry.jsonl.gz"),
        ]
    )

    assert result == 1


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _commit(root: Path, subject: str) -> str:
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=Eval fixture",
        "-c",
        "user.email=eval@example.invalid",
        "commit",
        "-m",
        subject,
    )
    return _git(root, "rev-parse", "HEAD")
