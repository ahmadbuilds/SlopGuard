"""CLI event, report, summary, and GitHub output integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from slopguard.cli import ExitCode, _summary, main
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
    assert "SlopGuard scan" in summary_path.read_text(encoding="utf-8")
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


def test_cli_requires_base_and_head_together(short_tmp_path: Path, capsys) -> None:
    exit_code = main(["scan", "--repo", str(short_tmp_path), "--base", "a" * 40])

    assert exit_code == ExitCode.CONFIG_ERROR
    assert "provided together" in capsys.readouterr().err


def test_summary_marks_passed_unknown_registry_results_as_unverified() -> None:
    report = {
        "decision": "pass",
        "counts": {"by_decision": {"pass": 1, "warn": 0, "block": 0}},
        "config_changed": False,
        "results": [
            {
                "decision": "pass",
                "requested_name": "reqeusts",
                "registry_exists": None,
                "registry_status": "transient_error",
                "remediation": "Review this dependency.",
            }
        ],
        "warnings": [],
    }

    summary = _summary(report, None)

    assert "Unverified package metadata" in summary
    assert "reqeusts" in summary
    assert "transient\\_error" in summary
    assert "not evidence that the package does not exist" in summary


def test_summary_keeps_unverified_results_visible_when_other_rows_are_truncated() -> None:
    results = [
        {
            "decision": "warn",
            "requested_name": f"known-package-{index}",
            "registry_exists": True,
            "registry_status": "exists",
            "remediation": "x" * 1_000,
        }
        for index in range(300)
    ]
    results.append(
        {
            "decision": "pass",
            "requested_name": "reqeusts",
            "registry_exists": None,
            "registry_status": "transient_error",
            "remediation": "Review this dependency.",
        }
    )
    report = {
        "decision": "warn",
        "counts": {"by_decision": {"pass": 1, "warn": 300, "block": 0}},
        "config_changed": False,
        "results": results,
        "warnings": [],
    }

    summary = _summary(report, None)

    assert "**Unverified registry results:** 1" in summary
    assert "## Unverified package metadata" in summary
    assert "reqeusts" in summary
    assert "Summary truncated" in summary
