"""Golden and injection tests for pure GitHub summary and annotation rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from slopguard import CONTRACT_VERSION
from slopguard.config import OutputSettings
from slopguard.contracts import (
    Decision,
    DependencyResult,
    RegistryLookupStatus,
    ScanReport,
    ScanWarning,
    SourceLocation,
    load_result_schema,
    load_scan_report_schema,
)
from slopguard.render import (
    COMMENT_MARKER,
    MAX_COMMENT_CHARACTERS,
    MAX_JOB_SUMMARY_BYTES,
    RenderContext,
    render_annotations,
    render_comment,
    render_summary,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "render"
CONTEXT = RenderContext(
    run_url="https://github.invalid/example/project/actions/runs/123",
    docs_url="https://docs.invalid/slopguard",
    path_prefix="",
)


def _result(
    name: str,
    *,
    decision: str = "warn",
    registry_exists: bool | None = True,
    registry_status: str | None = None,
    allowlisted: bool = False,
    metadata: bool = False,
    sources: list[dict[str, object]] | None = None,
    remediation: str = "Review this dependency.",
) -> DependencyResult:
    result: DependencyResult = {
        "contract_version": CONTRACT_VERSION,
        "requested_name": name,
        "normalized_name": name.lower().replace("_", "-"),
        "registry_exists": registry_exists,
        "metadata_evidence": [],
        "similarity_candidates": [
            {
                "name": "requests",
                "similarity": 0.875,
                "edit_distance": 1,
                "popularity_rank": 8,
                "match_kind": "edit",
            }
        ],
        "risk_score": 63.5,
        "severity": "high",
        "remediation": remediation,
        "decision": cast("Decision", decision),
        "score_contributions": [
            {"signal": "lookalike", "points": 12.5, "summary": "Resembles requests."},
            {"signal": "not_found", "points": 51.0, "summary": "PyPI did not find it."},
        ],
        "allowlisted": allowlisted,
    }
    if registry_status is not None:
        result["registry_status"] = cast("RegistryLookupStatus", registry_status)
    if sources is not None:
        result["source_locations"] = cast("list[SourceLocation]", sources)
    if metadata:
        result["registry_metadata"] = {
            "first_release_at": "2026-11-26T00:00:00Z",
            "release_count": 1,
            "latest_release_at": "2026-11-26T00:00:00Z",
            "project_urls": {"Source": "https://code.invalid/project"},
            "maintainer_hints": ["package-maintainer"],
            "source_hints": ["https://code.invalid/project"],
        }
    return result


def _report(
    results: list[DependencyResult] | None = None,
    *,
    warnings: list[dict[str, object]] | None = None,
    config_changed: bool = False,
) -> ScanReport:
    selected = results or []
    by_decision = {decision: 0 for decision in ("pass", "warn", "block")}
    by_severity = {severity: 0 for severity in ("info", "low", "medium", "high", "critical")}
    for item in selected:
        by_decision[item["decision"]] += 1
        by_severity[item["severity"]] += 1
    final_decision = max(
        (item["decision"] for item in selected),
        key={"pass": 0, "warn": 1, "block": 2}.get,
        default="pass",
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "tool_version": "0.2.0",
        "generated_at": "2026-11-27T00:00:00Z",
        "repository": "example/project",
        "base_sha": "base",
        "merge_base_sha": "base",
        "head_sha": "head",
        "config_source": "slopguard.toml",
        "config_changed": config_changed,
        "policy": {
            "mode": "block",
            "warn_threshold": 40,
            "block_threshold": 80,
            "on_unknown": "pass",
        },
        "decision": final_decision,
        "counts": {"by_decision": by_decision, "by_severity": by_severity},
        "results": selected,
        "warnings": cast("list[ScanWarning]", warnings or []),
        "lookups": {"used": len(selected), "budget": 500},
    }


def _golden_reports() -> dict[str, ScanReport]:
    located = [{"file": "requirements.txt", "line": 4, "source_type": "requirements"}]
    return {
        "clean": _report(),
        "warn_only": _report(
            [_result("known-package", decision="warn", sources=located, metadata=True)]
        ),
        "block": _report(
            [
                _result(
                    "reqeusts",
                    decision="block",
                    registry_exists=False,
                    registry_status="not_found",
                    sources=located,
                )
            ]
        ),
        "unverified_transient": _report(
            [
                _result(
                    "unverified-package",
                    decision="pass",
                    registry_exists=None,
                    registry_status="transient_error",
                )
            ]
        ),
        "invalid_name_warning": _report(
            [
                _result(
                    "_invalid",
                    decision="warn",
                    registry_exists=None,
                    registry_status="invalid_name",
                )
            ]
        ),
        "allowlisted_expiry": _report(
            [
                {
                    **_result("trusted-package", decision="pass", allowlisted=True, metadata=True),
                    "allowlist": {"reason": "Reviewed dependency", "expires": "2027-01-31"},
                }
            ]
        ),
        "allowlisted_no_metadata": _report(
            [
                {
                    **_result(
                        "trusted-unknown", decision="pass", registry_exists=None, allowlisted=True
                    ),
                    "allowlist": {"reason": "Internal release pending", "expires": None},
                }
            ]
        ),
        "config_changed": _report([_result("new-package", decision="warn")], config_changed=True),
        "many_findings": _report(
            [
                _result(
                    f"fixture-package-{index:03}",
                    decision="block" if index % 2 == 0 else "warn",
                    registry_exists=False,
                    registry_status="not_found",
                    sources=located,
                )
                for index in range(200)
            ]
        ),
    }


def _render_golden(report: ScanReport) -> str:
    return render_summary(report, context=CONTEXT, settings=OutputSettings())


def test_summary_goldens() -> None:
    for name, report in _golden_reports().items():
        actual = _render_golden(report)
        path = GOLDEN / f"{name}.md"
        if os.environ.get("UPDATE_GOLDEN") == "1":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual, encoding="utf-8", newline="\n")
        assert path.read_text(encoding="utf-8") == actual
        if name == "many_findings":
            assert "150 additional findings omitted." in actual
        if name == "unverified_transient":
            assert "policy decision pass" in actual


def test_summary_pluralizes_dependency_count() -> None:
    reports = (
        _report(),
        _report([_result("one-package", decision="pass")]),
        _report(
            [
                _result("first-package", decision="pass"),
                _result("second-package", decision="pass"),
            ]
        ),
    )

    summaries = [
        render_summary(report, context=CONTEXT, settings=OutputSettings()) for report in reports
    ]

    assert "Found 0 dependencies:" in summaries[0]
    assert "Found 1 dependency:" in summaries[1]
    assert "Passed 1 dependency" in summaries[1]
    assert "Found 2 dependencies:" in summaries[2]
    assert "Passed 2 dependencies" in summaries[2]


def test_summary_pluralizes_other_counted_items() -> None:
    reports = [
        _result(
            f"unknown-package-{index}",
            decision="warn",
            registry_exists=None,
            registry_status="transient_error",
        )
        for index in range(2)
    ]
    settings = OutputSettings(
        max_findings_in_summary=1, max_unverified_in_summary=1, max_annotations=1
    )
    report = _report(reports)

    summary = render_summary(report, context=CONTEXT, settings=settings)
    annotations = render_annotations(report, context=CONTEXT, settings=settings)

    assert "1 additional unverified result omitted." in summary
    assert "1 additional finding omitted." in summary
    assert annotations[-1] == "::notice::1 more finding in the job summary"


def test_annotations_golden_and_count_cap() -> None:
    report = _report(
        [
            _result(
                "blocked", decision="block", registry_exists=False, registry_status="not_found"
            ),
            _result("warned", decision="warn"),
            _result(
                "unknown", decision="pass", registry_exists=None, registry_status="transient_error"
            ),
        ]
    )
    actual = (
        "\n".join(
            render_annotations(report, context=CONTEXT, settings=OutputSettings(max_annotations=2))
        )
        + "\n"
    )
    path = GOLDEN / "annotations_cap.txt"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == actual


def test_zero_annotation_cap_disables_all_annotations() -> None:
    report = _report([_result("blocked", decision="block", registry_exists=False)])

    assert (
        render_annotations(report, context=CONTEXT, settings=OutputSettings(max_annotations=0))
        == []
    )


def test_comment_starts_with_marker_and_reuses_escaped_summary_content() -> None:
    hostile = _result("<img src=x onerror=alert(1)>", decision="warn")
    report = _report([hostile])

    comment = render_comment(report, context=CONTEXT, settings=OutputSettings())

    assert comment.startswith(f"{COMMENT_MARKER}\n\n# SlopGuard:")
    assert "\\<img" in comment
    assert "\n# SlopGuard: <img" not in comment
    assert comment.removeprefix(f"{COMMENT_MARKER}\n\n") == render_summary(
        report, context=CONTEXT, settings=OutputSettings()
    )


def test_comment_truncation_keeps_note_and_run_link_within_character_limit() -> None:
    result = _result("large-finding", decision="warn", remediation="detail " * 12_000)
    settings = OutputSettings(max_field_characters=100_000)

    comment = render_comment(_report([result]), context=CONTEXT, settings=settings)

    assert len(comment) <= MAX_COMMENT_CHARACTERS
    assert comment.startswith(COMMENT_MARKER)
    assert "Summary truncated" in comment
    assert CONTEXT.run_url in comment


def test_summary_escapes_report_supplied_injection_fixtures() -> None:
    hostile = "<img src=x onerror=alert(1)>"
    result = _result(
        hostile,
        decision="warn",
        registry_exists=None,
        registry_status="transient_error",
        remediation="](javascript:alert(1))",
        sources=[{"file": "src/\n::error::pwned.py", "line": 7, "source_type": "import"}],
        metadata=True,
    )
    result["registry_metadata"]["maintainer_hints"] = [
        "@team\n::set-output name=x::y %0A\u202e\u200b"
    ]
    result["registry_metadata"]["project_urls"] = {
        "](javascript:alert(1))": "https://example.invalid"
    }
    report = _report(
        [result],
        warnings=[
            {
                "file": "warning\n::error::pwned.py",
                "line": None,
                "message": "%0A\n::set-output name=x::y",
                "kind": "injected",
            }
        ],
    )

    summary = render_summary(report, context=CONTEXT, settings=OutputSettings())

    assert "\\<img src=x onerror=alert\\(1\\)\\>" in summary
    assert "[javascript:" not in summary
    assert "@team\\n::set\\-output name=x::y %0A" in summary
    assert "\\u202e\\u200b" in summary
    assert "src/\\n::error::pwned\\.py" in summary
    assert "\\n::set\\-output name=x::y" in summary
    assert "<img src=x onerror=alert(1)>" not in summary


def test_annotations_escape_command_injection_and_have_single_lines() -> None:
    result = _result(
        "bad%0Aname",
        decision="block",
        registry_exists=False,
        registry_status="not_found",
        remediation="bad\n::warning::second-command %0A\u202e",
        sources=[{"file": "src/file,one:\n::error::x.py", "line": 9, "source_type": "import"}],
    )
    annotations = render_annotations(_report([result]), context=CONTEXT, settings=OutputSettings())

    assert len(annotations) == 1
    assert "file=src/file%2Cone%3A%0A%3A%3Aerror%3A%3Ax.py" in annotations[0]
    assert "title=SlopGuard%3A bad%250Aname" in annotations[0]
    assert "%0A::warning::second-command %250A" in annotations[0]
    assert "\u202e" not in annotations[0]
    assert "\n" not in annotations[0]
    no_location = render_annotations(
        _report([_result("source-less", decision="warn")]),
        context=CONTEXT,
        settings=OutputSettings(),
    )[0]
    assert " file=" not in no_location
    assert "title=SlopGuard%3A source-less" in no_location


def test_annotations_sort_by_decision_then_descending_risk() -> None:
    highest = _result("high-block", decision="block")
    lowest = _result("low-block", decision="block")
    warning = _result("high-warning", decision="warn")
    highest["risk_score"] = 90
    lowest["risk_score"] = 10
    warning["risk_score"] = 100

    annotations = render_annotations(
        _report([warning, lowest, highest]), context=CONTEXT, settings=OutputSettings()
    )

    assert "high-block" in annotations[0]
    assert "low-block" in annotations[1]
    assert "high-warning" in annotations[2]


def test_summary_obeys_the_job_summary_byte_ceiling() -> None:
    report = _report(
        [
            _result(
                "unknown-early",
                decision="pass",
                registry_exists=None,
                registry_status="transient_error",
            )
        ],
        warnings=[
            {"file": "large.txt", "line": index + 1, "message": "x" * 1000, "kind": "large"}
            for index in range(1500)
        ],
    )

    summary = render_summary(
        report, context=RenderContext(None, None, ""), settings=OutputSettings()
    )

    assert len(summary.encode("utf-8")) <= MAX_JOB_SUMMARY_BYTES
    assert summary.endswith("Summary truncated; see the JSON report for full details.")
    assert "unknown\\-early" in summary
    assert "unverified, not evidence" in summary


def test_truncation_keeps_passed_dependency_details_closed() -> None:
    report = _report(
        [_result(f"package-{index:04}-{'x' * 700}", decision="pass") for index in range(1500)]
    )

    summary = render_summary(
        report, context=RenderContext(None, None, ""), settings=OutputSettings()
    )

    assert len(summary.encode("utf-8")) <= MAX_JOB_SUMMARY_BYTES
    assert summary.index("<details>") < summary.index("</details>")
    assert summary.index("</details>") < summary.index("Summary truncated")


def test_render_context_and_auto_annotations_are_built_by_cli(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from slopguard.cli import _annotations_enabled, _render_context

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.invalid")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    workspace = tmp_path / "workspace"
    checkout = workspace / "nested" / "repo"
    checkout.mkdir(parents=True)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))

    context = _render_context(checkout)

    assert context.run_url is None
    assert context.path_prefix == "nested/repo"
    assert _render_context(workspace).path_prefix == ""
    assert _annotations_enabled("auto") is False

    monkeypatch.setenv("GITHUB_RUN_ID", "55")
    monkeypatch.setattr(
        "slopguard.cli.distribution",
        lambda _name: SimpleNamespace(
            metadata=SimpleNamespace(
                get_all=lambda _key: [
                    "Homepage, https://home.invalid",
                    "Documentation, https://docs.invalid",
                ]
            )
        ),
    )
    complete_context = _render_context(checkout)
    outside = workspace.parent / "outside"
    outside.mkdir()
    outside_context = _render_context(outside)
    assert complete_context.run_url == "https://github.invalid/owner/repo/actions/runs/55"
    assert complete_context.docs_url == "https://docs.invalid"
    assert outside_context.path_prefix == ""
    monkeypatch.setattr(
        "slopguard.cli.distribution",
        lambda _name: SimpleNamespace(
            metadata=SimpleNamespace(get_all=lambda _key: ["Homepage, https://home.invalid"])
        ),
    )
    assert _render_context(checkout).docs_url == "https://home.invalid"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _annotations_enabled("auto") is True
    assert _annotations_enabled("never") is False
    path_annotation = render_annotations(
        _report(
            [
                _result(
                    "path-check",
                    decision="warn",
                    sources=[
                        {"file": "requirements.txt", "line": 2, "source_type": "requirements"}
                    ],
                )
            ]
        ),
        context=complete_context,
        settings=OutputSettings(),
    )[0]
    assert "file=nested/repo/requirements.txt" in path_annotation


def test_contract_fixture_and_allowlist_result_match_contract() -> None:
    result_schema = load_result_schema()
    scan_schema = load_scan_report_schema()
    report_fixture = json.loads(
        (ROOT / "tests/fixtures/contract/scan-report.json").read_text(encoding="utf-8")
    )
    result_fixture = json.loads(
        (ROOT / "tests/fixtures/contract/result.json").read_text(encoding="utf-8")
    )
    registry = Registry().with_resource(result_schema["$id"], Resource.from_contents(result_schema))

    assert report_fixture["contract_version"] == CONTRACT_VERSION
    assert result_fixture["contract_version"] == CONTRACT_VERSION
    Draft202012Validator(scan_schema, registry=registry).validate(report_fixture)
    Draft202012Validator(result_schema).validate(
        {
            **result_fixture,
            "allowlisted": True,
            "allowlist": {"reason": "reviewed", "expires": "2027-01-31"},
        }
    )


# Unknown registry evidence must stay visible, even when the policy permits a pass.


def test_passed_unknown_registry_results_are_listed_as_unverified() -> None:
    report = _report(
        [
            _result(
                "reqeusts", decision="pass", registry_exists=None, registry_status="transient_error"
            )
        ]
    )

    summary = render_summary(report, context=CONTEXT, settings=OutputSettings())

    unverified = summary.split("## Unverified package metadata", 1)[1].split("##", 1)[0]
    assert "reqeusts" in unverified
    assert "transient\\_error" in unverified
    assert "not evidence that the package does not exist" in unverified
    assert any(line.startswith("::notice") for line in _annotations(report))


def test_allowlisted_results_are_not_reported_as_unverified() -> None:
    item = _result("internal-package", decision="pass", registry_exists=None, allowlisted=True)
    item["allowlist"] = {"reason": "Internal package", "expires": "2027-01-31"}
    report = _report([item])

    summary = render_summary(report, context=CONTEXT, settings=OutputSettings())

    unverified = summary.split("## Unverified package metadata", 1)[1].split("<details>", 1)[0]
    assert "internal" not in unverified
    assert "All dependency metadata was verified." in unverified
    assert "allow-listed: Internal package; expires 2027\\-01\\-31" in summary
    assert _annotations(report) == []


def test_unverified_results_stay_visible_when_findings_are_truncated() -> None:
    findings = [_result(f"known-package-{index}", remediation="x" * 1_000) for index in range(300)]
    unknown = _result(
        "reqeusts", decision="pass", registry_exists=None, registry_status="transient_error"
    )
    settings = OutputSettings(max_findings_in_summary=5)

    summary = render_summary(_report([*findings, unknown]), context=CONTEXT, settings=settings)

    assert summary.index("## Unverified package metadata") < summary.index("finding:")
    assert "reqeusts" in summary
    assert "295 additional findings omitted." in summary


def test_empty_passed_section_is_omitted() -> None:
    summary = render_summary(
        _report([_result("reqeusts", decision="warn")]), context=CONTEXT, settings=OutputSettings()
    )

    assert "Passed dependencies" not in summary


def _annotations(report: ScanReport) -> list[str]:
    return render_annotations(report, context=CONTEXT, settings=OutputSettings())


def test_summary_lists_only_signals_that_added_points() -> None:
    item = _result("reqeusts")
    item["score_contributions"] = [
        {"signal": "not_found", "points": 60.0, "summary": "PyPI did not find it."},
        {"signal": "new_package", "points": 0.0, "summary": "Evidence was unavailable."},
    ]
    zero = _result("quiet-package")
    zero["score_contributions"] = [
        {"signal": "lookalike", "points": 0.0, "summary": "No look-alike candidates."}
    ]

    summary = render_summary(_report([item, zero]), context=CONTEXT, settings=OutputSettings())

    assert "not\\_found: +60" in summary
    assert "new\\_package" not in summary
    assert "No scoring signal added points." in summary
