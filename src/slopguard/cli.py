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
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from .config import ConfigError, OutputSettings
from .contracts import ScanReport
from .git import GitRepository
from .github import MAX_COMMENT_RESPONSE_BYTES, GitHubClient
from .registry import PyPIClient
from .render import RenderContext, render_annotations, render_comment, render_summary
from .scan import run_scan_with_config

MAX_EVENT_BYTES = 1_000_000
MAX_ERROR_CHARACTERS = 500


class ExitCode(IntEnum):
    """Process status values exposed by the SlopGuard command line."""

    OK = 0
    BLOCKED = 1
    CONFIG_ERROR = 2
    INTERNAL_ERROR = 3


class _InputError(ValueError):
    """Raised for unsupported event sources or malformed CLI input."""


@dataclass(frozen=True, slots=True)
class _ResolvedRevisions:
    base: str
    head: str
    pull_request_number: int | None = None
    is_fork: bool = False


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
        "--annotations",
        choices=("auto", "always", "never"),
        default="auto",
        help="emit GitHub workflow annotations (auto enables them on GitHub Actions)",
    )
    scan.add_argument(
        "--comment",
        choices=("never", "auto"),
        default="never",
        help="opt in to updating a sticky pull-request comment",
    )
    scan.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable containing the GitHub token (never pass a token directly)",
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
    revisions = _resolve_revisions(args.base, args.head)
    root = args.repo.resolve()
    config_path = _config_path(args.config, root)
    repository = GitRepository(root)
    client = PyPIClient()
    report, config = run_scan_with_config(
        repository,
        repository=os.environ.get("GITHUB_REPOSITORY") or root.name,
        base=revisions.base,
        head=revisions.head,
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
    context = _render_context(root)
    if _annotations_enabled(args.annotations):
        for annotation in render_annotations(report, context=context, settings=config.output):
            print(annotation)
    summary = render_summary(report, context=context, settings=config.output)
    summary_path = args.summary or _environment_path("GITHUB_STEP_SUMMARY")
    if summary_path is not None:
        _append_file(summary_path, summary + "\n")
    else:
        print(summary)

    if args.comment == "auto":
        _sync_pull_request_comment(args, report, config.output, context, revisions)

    github_output = args.github_output or _environment_path("GITHUB_OUTPUT")
    if github_output is not None:
        _write_outputs(github_output, report, str(report_path) if report_path else "")

    if report["decision"] == "block" and not args.no_fail_on_block:
        return int(ExitCode.BLOCKED)
    return int(ExitCode.OK)


def _resolve_revisions(base: str | None, head: str | None) -> _ResolvedRevisions:
    if bool(base) != bool(head):
        raise _InputError("--base and --head must be provided together.")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name != "pull_request" and base and head:
        return _ResolvedRevisions(base, head)
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
    number = pull_request.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        number = None
    base_repo = base_object.get("repo") if isinstance(base_object, dict) else None
    head_repo = head_object.get("repo") if isinstance(head_object, dict) else None
    base_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    head_name = head_repo.get("full_name") if isinstance(head_repo, dict) else None
    is_fork = (
        not isinstance(base_name, str)
        or not base_name
        or not isinstance(head_name, str)
        or not head_name
        or head_name != base_name
    )
    return _ResolvedRevisions(base or base_sha, head or head_sha, number, is_fork)


def _sync_pull_request_comment(
    args: argparse.Namespace,
    report: ScanReport,
    settings: OutputSettings,
    context: RenderContext,
    revisions: _ResolvedRevisions,
) -> None:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        _comment_notice("Comment skipped: this is not a pull_request event.")
        return
    if revisions.is_fork:
        _comment_notice("Comment skipped: fork pull requests are not commented on.")
        return
    if revisions.pull_request_number is None:
        _comment_notice("Comment skipped: the pull-request number is unavailable.")
        return
    token = os.environ.get(args.token_env, "")
    if not token.strip():
        _comment_notice("Comment skipped: the GitHub token is unavailable.")
        return
    try:
        api_url = os.environ.get("GITHUB_API_URL", "")
        client = GitHubClient(
            api_url,
            os.environ.get("GITHUB_REPOSITORY", ""),
            token,
            timeout_seconds=settings.comment_timeout_seconds,
            max_response_bytes=MAX_COMMENT_RESPONSE_BYTES,
        )
        body = render_comment(report, context=context, settings=settings)
        client.upsert_pull_request_comment(
            revisions.pull_request_number,
            body,
            create_if_missing=report["decision"] != "pass",
            max_pages=settings.max_comment_pages,
        )
    except Exception as error:
        message = str(error).replace(token, "[redacted]") if token else str(error)
        print(
            f"SlopGuard PR comment warning: {_safe_terminal_text(message)}",
            file=sys.stderr,
        )


def _comment_notice(message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::notice::{message}")
    else:
        print(f"SlopGuard {message}", file=sys.stderr)


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


def _annotations_enabled(mode: str) -> bool:
    return mode == "always" or (mode == "auto" and os.environ.get("GITHUB_ACTIONS") == "true")


def _render_context(root: Path) -> RenderContext:
    server = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    run_url = (
        f"{server.rstrip('/')}/{repository}/actions/runs/{run_id}"
        if server and repository and run_id
        else None
    )
    try:
        project_urls = distribution("slopguard").metadata.get_all("Project-URL") or []
    except PackageNotFoundError:
        project_urls = []
    documentation_url = None
    homepage_url = None
    for project_url in project_urls:
        label, separator, url = project_url.partition(",")
        if not separator or not url.strip():
            continue
        if label.strip().casefold() == "documentation":
            documentation_url = url.strip()
            break
        if label.strip().casefold() == "homepage" and homepage_url is None:
            homepage_url = url.strip()

    path_prefix = ""
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace:
        try:
            relative_root = root.resolve().relative_to(Path(workspace).resolve())
        except ValueError:
            pass
        else:
            path_prefix = relative_root.as_posix() if relative_root.parts else ""
    return RenderContext(
        run_url=run_url,
        docs_url=documentation_url or homepage_url,
        path_prefix=path_prefix,
    )


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
