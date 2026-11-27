"""CLI event, report, summary, and GitHub output integration tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from slopguard.cli import ExitCode, main
from slopguard.github import GitHubClientError
from slopguard.registry import RegistryLookupResult


class FakeClient:
    def lookup(self, name: str) -> RegistryLookupResult:
        missing = name == "reqeusts"
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

    def with_settings(self, settings):
        return self


class NotFoundClient(FakeClient):
    def lookup(self, name: str) -> RegistryLookupResult:
        return RegistryLookupResult(
            normalized_name=name,
            status="not_found",
            metadata=None,
            latency_ms=1.0,
            attempts=1,
            http_status=404,
            retry_after=None,
            rate_limit_remaining=None,
            error=None,
        )


def _repository_with_lookalike(root: Path) -> tuple[str, str]:
    root.mkdir()
    for arguments in (
        ("init", "-b", "main"),
        ("config", "user.name", "SlopGuard tests"),
        ("config", "user.email", "tests@example.invalid"),
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True)
    requirements = root / "requirements.txt"
    requirements.write_text("# initial state\n", encoding="utf-8")
    config = root / "slopguard.toml"
    config.write_text("[policy]\nmode = 'warn'\n[output]\nmax_annotations = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "requirements.txt", "slopguard.toml"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    requirements.write_text("# initial state\nreqeusts\n", encoding="utf-8")
    config.write_text("[policy]\nmode = 'block'\n[output]\nmax_annotations = 0\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "add a look-alike"], cwd=root, check=True, capture_output=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return base, head


def test_slopguard_scan_reads_pr_event_and_uses_depth_one_merge_checkout(
    shallow_merge_checkout: tuple[Path, str, str, str],
    short_tmp_path: Path,
    monkeypatch,
) -> None:
    root, base, head, common = shallow_merge_checkout
    event_path = short_tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}),
        encoding="utf-8",
    )
    report_path = short_tmp_path / "report.json"
    summary_path = short_tmp_path / "summary.md"
    output_path = short_tmp_path / "outputs.txt"
    monkeypatch.setattr("slopguard.cli.PyPIClient", FakeClient)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/shallow-pr")

    exit_code = main(
        [
            "scan",
            "--repo",
            str(root),
            "--report",
            str(report_path),
            "--summary",
            str(summary_path),
            "--github-output",
            str(output_path),
        ]
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == ExitCode.OK
    assert report["repository"] == "example/shallow-pr"
    assert report["merge_base_sha"] == common
    assert [item["normalized_name"] for item in report["results"]] == ["requests"]
    assert "SlopGuard: PASS" in summary_path.read_text(encoding="utf-8")
    output = output_path.read_text(encoding="utf-8")
    assert "decision<<slopguard_" in output
    assert "report-path<<slopguard_" in output


def test_cli_rejects_pull_request_target_without_traceback(
    short_tmp_path: Path, monkeypatch, capsys
) -> None:
    event_path = short_tmp_path / "event.json"
    event_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    exit_code = main(["scan", "--repo", str(short_tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.CONFIG_ERROR
    assert "pull_request_target" in captured.err
    assert "Traceback" not in captured.err


def test_auto_annotations_are_silent_outside_github_actions(
    short_tmp_path: Path, monkeypatch, capsys
) -> None:
    root = short_tmp_path / "auto-repo"
    base, head = _repository_with_lookalike(root)
    monkeypatch.setattr("slopguard.cli.PyPIClient", NotFoundClient)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    exit_code = main(
        [
            "scan",
            "--repo",
            str(root),
            "--base",
            base,
            "--head",
            head,
            "--no-fail-on-block",
        ]
    )

    assert exit_code == ExitCode.OK
    output = capsys.readouterr().out
    assert output.startswith("# SlopGuard: WARN")
    assert not any(line.startswith("::") for line in output.splitlines())


def test_annotations_always_writes_workflow_commands_before_summary(
    short_tmp_path: Path, monkeypatch, capsys
) -> None:
    root = short_tmp_path / "always-repo"
    base, head = _repository_with_lookalike(root)
    monkeypatch.setattr("slopguard.cli.PyPIClient", NotFoundClient)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    exit_code = main(
        [
            "scan",
            "--repo",
            str(root),
            "--base",
            base,
            "--head",
            head,
            "--annotations",
            "always",
            "--no-fail-on-block",
        ]
    )

    assert exit_code == ExitCode.OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("::warning ")
    assert lines[1] == "# SlopGuard: WARN"
    assert any("Policy: mode warn" in line for line in lines)
    assert any("base-branch policy was used" in line for line in lines)


def test_cli_requires_base_and_head_together(short_tmp_path: Path, capsys) -> None:
    exit_code = main(["scan", "--repo", str(short_tmp_path), "--base", "a" * 40])

    assert exit_code == ExitCode.CONFIG_ERROR
    assert "provided together" in capsys.readouterr().err


def _repository_with_clean_change(root: Path) -> tuple[str, str]:
    root.mkdir()
    for arguments in (
        ("init", "-b", "main"),
        ("config", "user.name", "SlopGuard tests"),
        ("config", "user.email", "tests@example.invalid"),
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True)
    readme = root / "README.md"
    readme.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    readme.write_text("head\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-am", "documentation change"], cwd=root, check=True, capture_output=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return base, head


def _event_file(
    path: Path, base: str, head: str, *, head_repository: str | None = "acme/project"
) -> None:
    path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 23,
                    "base": {"sha": base, "repo": {"full_name": "acme/project"}},
                    "head": {
                        "sha": head,
                        "repo": None if head_repository is None else {"full_name": head_repository},
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_auto_comment_updates_clean_scan_without_creating_a_comment(
    short_tmp_path: Path, monkeypatch, capsys
) -> None:
    root = short_tmp_path / "clean-repo"
    base, head = _repository_with_clean_change(root)
    event_path = short_tmp_path / "event.json"
    _event_file(event_path, base, head)
    calls: list[tuple[int, str, bool, int]] = []
    client_tokens: list[str] = []

    class RecordingGitHubClient:
        def __init__(self, _api_url, _repository, token, **_kwargs) -> None:
            client_tokens.append(token)

        def upsert_pull_request_comment(self, number, body, *, create_if_missing, max_pages):
            calls.append((number, body, create_if_missing, max_pages))

    monkeypatch.setattr("slopguard.cli.GitHubClient", RecordingGitHubClient)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/project")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.invalid")
    monkeypatch.setenv("SLOP_TEST_TOKEN", "fixture-secret-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = main(
        ["scan", "--repo", str(root), "--comment", "auto", "--token-env", "SLOP_TEST_TOKEN"]
    )

    assert exit_code == ExitCode.OK
    assert len(calls) == 1
    number, body, create_if_missing, max_pages = calls[0]
    assert number == 23
    assert body.startswith("<!-- slopguard:comment -->")
    assert create_if_missing is False
    assert max_pages == 10
    assert client_tokens == ["fixture-secret-token"]
    assert "# SlopGuard: PASS" in capsys.readouterr().out


def test_auto_comment_skips_when_explicit_revisions_are_not_a_pr_event(
    short_tmp_path: Path, monkeypatch, capsys
) -> None:
    root = short_tmp_path / "manual-scan"
    base, head = _repository_with_clean_change(root)

    def should_not_create_client(*_args, **_kwargs):
        raise AssertionError("comment client must be skipped")

    monkeypatch.setattr("slopguard.cli.GitHubClient", should_not_create_client)
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret-token")
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = main(
        ["scan", "--repo", str(root), "--base", base, "--head", head, "--comment", "auto"]
    )

    assert exit_code == ExitCode.OK
    assert "not a pull_request event" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("head_repository", "token", "notice"),
    [
        ("fork/project", "fixture-secret-token", "fork pull requests"),
        ("acme/project", "", "token is unavailable"),
        (None, "fixture-secret-token", "fork pull requests"),
    ],
)
def test_auto_comment_skips_forks_and_missing_tokens(
    short_tmp_path: Path, monkeypatch, capsys, head_repository: str | None, token: str, notice: str
) -> None:
    root = short_tmp_path / "skip-repo"
    base, head = _repository_with_clean_change(root)
    event_path = short_tmp_path / "event.json"
    _event_file(event_path, base, head, head_repository=head_repository)

    def should_not_create_client(*_args, **_kwargs):
        raise AssertionError("comment client must be skipped")

    monkeypatch.setattr("slopguard.cli.GitHubClient", should_not_create_client)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/project")
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = main(["scan", "--repo", str(root), "--comment", "auto"])

    assert exit_code == ExitCode.OK
    assert notice in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["HTTP 403", "request timed out"])
def test_comment_failure_warns_without_changing_scan_exit_code(
    short_tmp_path: Path, monkeypatch, capsys, failure: str
) -> None:
    root = short_tmp_path / "comment-failure"
    base, head = _repository_with_lookalike(root)
    event_path = short_tmp_path / "event.json"
    _event_file(event_path, base, head)

    class BrokenGitHubClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def upsert_pull_request_comment(self, *_args, **_kwargs):
            raise GitHubClientError(f"GitHub API returned {failure} for fixture-secret-token")

    monkeypatch.setattr("slopguard.cli.PyPIClient", NotFoundClient)
    monkeypatch.setattr("slopguard.cli.GitHubClient", BrokenGitHubClient)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/project")
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.invalid")
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret-token")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = main(["scan", "--repo", str(root), "--comment", "auto", "--no-fail-on-block"])

    captured = capsys.readouterr()
    assert exit_code == ExitCode.OK
    assert "PR comment warning" in captured.err
    assert "fixture-secret-token" not in captured.out + captured.err
