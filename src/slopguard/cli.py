"""Command-line interface for scanning pull-request dependency changes."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import traceback
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path

from .config import ConfigError
from .git import GitRepository
from .registry import PyPIClient
from .scan import run_scan

MAX_EVENT_BYTES = 1_000_000
MAX_SUMMARY_CHARACTERS = 20_000
MAX_SUMMARY_FIELD_CHARACTERS = 1_000
MAX_ERROR_CHARACTERS = 500


class ExitCode(IntEnum):
    """Process status values exposed by the SlopGuard command line."""

    OK = 0
    BLOCKED = 1
    CONFIG_ERROR = 2
    INTERNAL_ERROR = 3


class _InputError(ValueError):
    """Raised for unsupported event sources or malformed CLI input."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slopguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="scan dependency additions in a pull request")
    scan.add_argument("--base", help="base commit SHA; pair with --head")
    scan.add_argument("--head", help="head commit SHA; pair with --base")
    scan.add_argument("--repo", type=Path, default=Path.cwd(), help="Git checkout path")
    scan.add_argument("--config", help="explicit config file path inside the checkout")
    scan.add_argument("--report", type=Path, help="write the JSON report to this path")
    scan.add_argument("--summary", type=Path, help="write a Markdown summary to this path")
    scan.add_argument(
        "--github-output", type=Path, help="write GitHub Actions outputs to this path"
    )
    scan.add_argument(
        "--no-fail-on-block",
        action="store_true",
        help="return success even when the final decision is block",
    )
    scan.add_argument(
        "--verbose", action="store_true", help="include tracebacks for internal errors"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return its documented exit code."""

    args = _parser().parse_args(argv)
    try:
        return _scan(args)
    except (ConfigError, _InputError) as error:
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        print(_safe_terminal_text(str(error)), file=sys.stderr)
        return int(ExitCode.CONFIG_ERROR)
    except Exception as error:
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        else:
            print(
                f"SlopGuard scan failed ({type(error).__name__}); use --verbose for details.",
                file=sys.stderr,
            )
        return int(ExitCode.INTERNAL_ERROR)


def _scan(args: argparse.Namespace) -> int:
    base, head = _resolve_revisions(args.base, args.head)
    root = args.repo.resolve()
    config_path = _config_path(args.config, root)
    repository = GitRepository(root)
    client = PyPIClient()
    report = run_scan(
        repository,
        repository=os.environ.get("GITHUB_REPOSITORY") or root.name,
        base=base,
        head=head,
        client=client,
        now=datetime.now(UTC),
        config_path=config_path,
    )

    report_path = args.report.resolve() if args.report is not None else None
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
    summary = _summary(report, str(report_path) if report_path is not None else None)
    summary_path = args.summary or _environment_path("GITHUB_STEP_SUMMARY")
    if summary_path is not None:
        _append_file(summary_path, summary + "\n")
    else:
        print(summary)
    github_output = args.github_output or _environment_path("GITHUB_OUTPUT")
    if github_output is not None:
        _write_outputs(github_output, report, str(report_path) if report_path else "")

    if report["decision"] == "block" and not args.no_fail_on_block:
        return int(ExitCode.BLOCKED)
    return int(ExitCode.OK)


def _resolve_revisions(base: str | None, head: str | None) -> tuple[str, str]:
    if bool(base) != bool(head):
        raise _InputError("--base and --head must be provided together.")
    if base and head:
        return base, head
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name != "pull_request":
        raise _InputError(
            "SlopGuard supports pull_request events or an explicit --base/--head pair; "
            f"received {event_name or 'no GitHub event'}."
        )
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise _InputError("GITHUB_EVENT_PATH is required for pull_request events.")
    path = Path(event_path)
    try:
        if path.stat().st_size > MAX_EVENT_BYTES:
            raise _InputError("GitHub event payload exceeds the safe size limit.")
        payload = json.loads(path.read_bytes())
    except _InputError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise _InputError("GitHub event payload could not be read as valid JSON.") from error
    pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(pull_request, dict):
        raise _InputError("GitHub event payload has no pull_request object.")
    base_object = pull_request.get("base")
    head_object = pull_request.get("head")
    base_sha = base_object.get("sha") if isinstance(base_object, dict) else None
    head_sha = head_object.get("sha") if isinstance(head_object, dict) else None
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        raise _InputError("Pull-request event must contain base and head commit SHAs.")
    return base_sha, head_sha


def _config_path(value: str | None, root: Path) -> str | None:
    if value is None:
        return None
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise _InputError(
            "--config must identify a path inside the repository checkout."
        ) from error


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _summary(report: dict[str, object], report_path: str | None) -> str:
    counts = report["counts"]
    assert isinstance(counts, dict)
    by_decision = counts["by_decision"]
    assert isinstance(by_decision, dict)
    lines = [
        "# SlopGuard scan",
        "",
        f"**Decision:** {_markdown(report['decision'])}",
        "",
        "**Results:** "
        + ", ".join(f"{key}: {by_decision[key]}" for key in ("pass", "warn", "block")),
    ]
    if report["config_changed"]:
        lines.extend(("", "**Configuration changed in the pull request.**"))
    for decision, heading in (("block", "Blocked packages"), ("warn", "Warned packages")):
        selected = [item for item in report["results"] if item["decision"] == decision]
        if selected:
            lines.extend(("", f"## {heading}"))
            for item in selected:
                name = _markdown(item["requested_name"])
                remediation = _markdown(item["remediation"])
                lines.append(f"- {name}: {remediation}")
    if report["warnings"]:
        lines.extend(("", "## Warnings"))
        for warning in report["warnings"]:
            location = warning["file"] or "scan"
            if warning["line"] is not None:
                location += f":{warning['line']}"
            lines.append(f"- {_markdown(location)}: {_markdown(warning['message'])}")
    if report_path:
        lines.extend(("", f"Full report: {_markdown(report_path)}"))
    else:
        lines.extend(("", "Full JSON report was not written."))
    rendered = "\n".join(lines)
    if len(rendered) > MAX_SUMMARY_CHARACTERS:
        suffix = "\n\nSummary truncated; see the JSON report for complete details."
        rendered = rendered[: MAX_SUMMARY_CHARACTERS - len(suffix)] + suffix
    return rendered


def _markdown(value: object) -> str:
    text = str(value)[:MAX_SUMMARY_FIELD_CHARACTERS]
    escaped: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if character in "\r\n":
            escaped.append("\\n")
        elif category in {"Cc", "Cf"}:
            escaped.append(f"\\u{ord(character):04x}")
        elif character in "\\`*_{}[]()#+-.!|><~":
            escaped.append("\\" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def _write_outputs(path: Path, report: dict[str, object], report_path: str) -> None:
    blocked = [item["requested_name"] for item in report["results"] if item["decision"] == "block"]
    warned = [item["requested_name"] for item in report["results"] if item["decision"] == "warn"]
    values = {
        "decision": str(report["decision"]),
        "blocked": "\n".join(blocked),
        "warned": "\n".join(warned),
        "report-path": report_path,
    }
    contents = []
    for key, value in values.items():
        delimiter = f"slopguard_{secrets.token_hex(16)}"
        contents.extend((f"{key}<<{delimiter}", value, delimiter))
    _append_file(path, "\n".join(contents) + "\n")


def _append_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(text)


def _safe_terminal_text(value: str) -> str:
    safe = "".join(
        character
        if character in "\t " or unicodedata.category(character) not in {"Cc", "Cf"}
        else "?"
        for character in value
    )
    return safe[:MAX_ERROR_CHARACTERS]


if __name__ == "__main__":
    raise SystemExit(main())
