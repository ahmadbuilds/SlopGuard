"""CLI event, report, summary, and GitHub output integration tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from slopguard.cli import ExitCode, main
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
