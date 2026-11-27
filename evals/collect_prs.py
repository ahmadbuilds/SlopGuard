"""Manually collect historical pull-request candidates from configured Git repositories."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name

from slopguard.extractor import extract_dependencies, is_supported_path, local_modules_from_paths
from slopguard.git import GitRepository
from slopguard.registry import PyPIClient, RegistryLookupResult
from slopguard.scan import run_scan

from .constants import (
    DEFAULT_REAL_PRS_PATH,
    DEFAULT_REAL_PRS_REGISTRY_PATH,
    DEFAULT_REPOSITORIES_PATH,
    GIT_COMMAND_TIMEOUT_SECONDS,
    MAX_BRANCH_NAME_CHARACTERS,
    MAX_COMMIT_SUBJECT_BYTES,
    MAX_COMMITS_TO_INSPECT,
    MAX_GIT_OUTPUT_BYTES,
    MAX_GIT_STDERR_CHARACTERS,
    MAX_INPUT_FILE_BYTES,
    MAX_PER_REPOSITORY_COUNT,
    MAX_REPOSITORY_COUNT,
    MAX_REPOSITORY_URL_CHARACTERS,
    PYPROJECT_FILE_NAME,
    TEMPORARY_DIRECTORY_PREFIX,
)
from .io import write_jsonl
from .record import RecordingClient, write_recording


@dataclass(frozen=True, slots=True)
class SelectedCommit:
    head: str
    parent: str
    subject: str
    touched_path: str
    candidates: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class CommitSelection:
    commits: tuple[SelectedCommit, ...]
    inspected_commits: int
    inspection_limit_reached: bool


class RepositoryCollectionError(RuntimeError):
    """A failed repository collection with its progress count retained."""

    def __init__(self, repo: str, reason: str, inspected_commits: int) -> None:
        super().__init__(f"{repo}: {reason}")
        self.inspected_commits = inspected_commits


def load_repository_config(path: Path) -> tuple[tuple[dict[str, str], ...], int, date]:
    """Validate the repository URLs, sample count, and exclusive cutoff date."""

    if path.stat().st_size > MAX_INPUT_FILE_BYTES:
        raise ValueError("repository config exceeds the safe size limit")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"could not read repository config {path}") from error
    if not isinstance(document, dict) or set(document) != {"repos", "per_repo", "cutoff_date"}:
        raise ValueError("repository config must contain repos, per_repo, and cutoff_date")
    repos = document["repos"]
    per_repo = document["per_repo"]
    cutoff_value = document["cutoff_date"]
    if not isinstance(repos, list) or not repos or len(repos) > MAX_REPOSITORY_COUNT:
        raise ValueError(f"repos must contain between one and {MAX_REPOSITORY_COUNT} entries")
    if (
        isinstance(per_repo, bool)
        or not isinstance(per_repo, int)
        or not 1 <= per_repo <= MAX_PER_REPOSITORY_COUNT
    ):
        raise ValueError(f"per_repo must be between one and {MAX_PER_REPOSITORY_COUNT}")
    if not isinstance(cutoff_value, str):
        raise ValueError("cutoff_date must be an ISO date")
    try:
        cutoff = date.fromisoformat(cutoff_value)
    except ValueError as error:
        raise ValueError("cutoff_date must be an ISO date") from error
    validated_repos: list[dict[str, str]] = []
    for index, item in enumerate(repos, start=1):
        if not isinstance(item, dict) or set(item) != {"url", "default_branch"}:
            raise ValueError(f"repos entry {index} must contain url and default_branch")
        url = item["url"]
        branch = item["default_branch"]
        if (
            not isinstance(url, str)
            or not url.strip()
            or len(url) > MAX_REPOSITORY_URL_CHARACTERS
            or any(ord(character) < 32 for character in url)
        ):
            raise ValueError(f"repos entry {index} url must be a non-empty HTTPS URL")
        parsed_url = urlsplit(url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
        ):
            raise ValueError(f"repos entry {index} url must be HTTPS without user information")
        if (
            not isinstance(branch, str)
            or not branch.strip()
            or branch.startswith("-")
            or len(branch) > MAX_BRANCH_NAME_CHARACTERS
            or any(ord(character) < 32 for character in branch)
        ):
            raise ValueError(f"repos entry {index} default_branch is invalid")
        validated_repos.append({"url": url, "default_branch": branch})
    return tuple(validated_repos), per_repo, cutoff


def collect_pull_requests(
    config_path: Path = DEFAULT_REPOSITORIES_PATH,
    *,
    prs_path: Path = DEFAULT_REAL_PRS_PATH,
    registry_path: Path = DEFAULT_REAL_PRS_REGISTRY_PATH,
    per_repo_override: int | None = None,
) -> int:
    """Clone configured repos, scan eligible commits, and write deterministic collection rows."""

    repos, configured_per_repo, cutoff = load_repository_config(config_path)
    per_repo = configured_per_repo if per_repo_override is None else per_repo_override
    if not 1 <= per_repo <= MAX_PER_REPOSITORY_COUNT:
        raise ValueError(f"per_repo must be between one and {MAX_PER_REPOSITORY_COUNT}")
    recorded_at = datetime.now(UTC)
    recording_client = RecordingClient(PyPIClient())
    scans: list[dict[str, object]] = []
    repositories: list[dict[str, object]] = []
    failed_repositories: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix=TEMPORARY_DIRECTORY_PREFIX) as temporary:
        temp_root = Path(temporary)
        for repo_index, repo_data in enumerate(repos):
            url = repo_data["url"]
            branch = repo_data["default_branch"]
            checkout = temp_root / f"repo-{repo_index}"
            try:
                repo_rows, repo_summary = _collect_repository(
                    temp_root,
                    checkout,
                    url=url,
                    branch=branch,
                    cutoff=cutoff,
                    per_repo=per_repo,
                    recording_client=recording_client,
                    recorded_at=recorded_at,
                )
                scans.extend(repo_rows)
                repositories.append(repo_summary)
            except Exception as error:
                reason_limit = MAX_REPOSITORY_URL_CHARACTERS + MAX_GIT_STDERR_CHARACTERS + 2
                inspected = getattr(error, "inspected_commits", 0)
                if not isinstance(inspected, int) or inspected < 0:
                    inspected = 0
                detail = str(error)
                if detail.startswith(url):
                    detail = detail[len(url) :].lstrip(": ")
                reason = f"{url}: {_sanitize_git_stderr(detail)}"[:reason_limit]
                failed_repositories.append(
                    {"repo": url, "reason": reason, "inspected_commits": inspected}
                )
                repositories.append(
                    {
                        "repo": url,
                        "inspected_commits": inspected,
                        "inspection_limit_reached": False,
                        "failed": True,
                    }
                )
    scans.sort(key=lambda row: (str(row["repo"]), str(row["head"])))
    summary_row = {
        "record_type": "collection_summary",
        "repositories": sorted(repositories, key=lambda row: str(row["repo"])),
        "failed_repositories": sorted(failed_repositories, key=lambda row: str(row["repo"])),
    }
    write_jsonl(prs_path, (summary_row, *scans))
    write_recording(recording_client.results.values(), registry_path, recorded_at=recorded_at)
    return len(failed_repositories)


def _collect_repository(
    temp_root: Path,
    checkout: Path,
    *,
    url: str,
    branch: str,
    cutoff: date,
    per_repo: int,
    recording_client: RecordingClient,
    recorded_at: datetime,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    inspected_commits = 0
    try:
        _git(
            temp_root,
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "--single-branch",
            f"--branch={branch}",
            "--",
            url,
            str(checkout),
        )
        git_repo = GitRepository(checkout)
        selection = _eligible_commits(git_repo, branch, cutoff, per_repo)
        inspected_commits = selection.inspected_commits
        rows: list[dict[str, object]] = []
        for selected in selection.commits:
            scan_report = run_scan(
                git_repo,
                repository=url,
                base=selected.parent,
                head=selected.head,
                client=recording_client,
                now=recorded_at,
            )
            _remember_budget_results(recording_client, scan_report["results"])
            rows.append(
                {
                    "repo": url,
                    "base": selected.parent,
                    "head": selected.head,
                    "subject": selected.subject,
                    "selection_reason": (
                        f"recent first-parent commit before {cutoff.isoformat()} with "
                        f"{len(selected.candidates)} extracted dependency candidate(s)"
                    ),
                    "candidates": list(selected.candidates),
                }
            )
        return rows, {
            "repo": url,
            "inspected_commits": selection.inspected_commits,
            "inspection_limit_reached": selection.inspection_limit_reached,
        }
    except Exception as error:
        raise RepositoryCollectionError(url, str(error), inspected_commits) from error


def _eligible_commits(
    repository: GitRepository, branch: str, cutoff: date, per_repo: int
) -> CommitSelection:
    checkout = repository.root
    cutoff_argument = f"--before={cutoff.isoformat()}T00:00:00Z"
    revisions = (
        _git(
            checkout,
            "rev-list",
            "--first-parent",
            cutoff_argument,
            f"--max-count={MAX_COMMITS_TO_INSPECT}",
            branch,
        )
        .decode("ascii", errors="strict")
        .splitlines()
    )
    selected: list[SelectedCommit] = []
    inspected_commits = 0
    for head in revisions:
        inspected_commits += 1
        parents = _git(checkout, "rev-list", "--parents", "-n", "1", head).decode("ascii").split()
        if len(parents) < 2:
            continue
        parent = parents[1]
        paths = _git(checkout, "diff", "--name-only", "-z", parent, head).split(b"\0")
        supported: list[str] = []
        for raw_path in paths:
            if not raw_path:
                continue
            try:
                path = raw_path.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if is_supported_path(path):
                supported.append(path)
        if not supported:
            continue
        changed_files, _warnings = repository.changed_files(parent, head)
        head_paths = repository.list_files(head)
        project_name = _project_name(repository, parent)
        extraction = extract_dependencies(
            changed_files,
            local_modules=local_modules_from_paths(head_paths, project_name=project_name),
        )
        if not extraction.candidates:
            continue
        subject_format = f"--format=%<({MAX_COMMIT_SUBJECT_BYTES},trunc)%s"
        subject = _git(checkout, "show", "-s", subject_format, head)
        subject_text = subject[:MAX_COMMIT_SUBJECT_BYTES].decode("utf-8", errors="replace").strip()
        selected.append(
            SelectedCommit(
                head=head,
                parent=parent,
                subject=subject_text,
                touched_path=sorted(supported)[0],
                candidates=extraction.candidates,
            )
        )
        if len(selected) == per_repo:
            break
    return CommitSelection(
        tuple(selected),
        inspected_commits,
        len(revisions) >= MAX_COMMITS_TO_INSPECT and len(selected) < per_repo,
    )


def _project_name(repository: GitRepository, commit: str) -> str | None:
    paths = set(repository.list_files(commit))
    if PYPROJECT_FILE_NAME not in paths:
        return None
    content, _warning = repository.read_blob(commit, PYPROJECT_FILE_NAME)
    if content is None:
        return None
    try:
        document = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, RecursionError):
        return None
    project = document.get("project")
    name = project.get("name") if isinstance(project, Mapping) else None
    return name if isinstance(name, str) else None


def _remember_budget_results(client: RecordingClient, results: list[dict[str, object]]) -> None:
    """Record transient results synthesized by run_scan when its lookup budget is exhausted."""

    for result in results:
        normalized = result.get("normalized_name")
        status = result.get("registry_status")
        diagnostics = result.get("registry_diagnostics")
        if not isinstance(normalized, str) or not isinstance(status, str):
            continue
        canonical = canonicalize_name(normalized, validate=True)
        if canonical in client.results or not isinstance(diagnostics, dict):
            continue
        client.results[canonical] = RegistryLookupResult(
            normalized_name=normalized,
            status=status,
            metadata=result.get("registry_metadata"),
            latency_ms=diagnostics["latency_ms"],
            attempts=diagnostics["attempts"],
            http_status=diagnostics["http_status"],
            retry_after=diagnostics["retry_after"],
            rate_limit_remaining=diagnostics["rate_limit_remaining"],
            error=diagnostics["error"],
        )


def _git(cwd: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.CalledProcessError as error:
        detail = _sanitize_git_stderr(error.stderr)
        message = "Git command failed" + (f": {detail}" if detail else ".")
        raise RuntimeError(message) from error
    except (OSError, subprocess.SubprocessError) as error:
        detail = _sanitize_git_stderr(getattr(error, "stderr", None))
        message = "Git collection command failed" + (f": {detail}" if detail else ".")
        raise RuntimeError(message) from error
    if len(result.stdout) > MAX_GIT_OUTPUT_BYTES or len(result.stderr) > MAX_GIT_OUTPUT_BYTES:
        detail = _sanitize_git_stderr(result.stderr)
        suffix = f" Git stderr: {detail}" if detail else ""
        raise RuntimeError(f"Git collection command exceeded the output limit.{suffix}")
    return result.stdout


def _sanitize_git_stderr(stderr: bytes | str | None) -> str:
    if stderr is None:
        return ""
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])", " ", text)
    sanitized = " ".join(
        "".join(character if character.isprintable() else " " for character in text).split()
    )
    if len(sanitized) > MAX_GIT_STDERR_CHARACTERS:
        return sanitized[: MAX_GIT_STDERR_CHARACTERS - 1] + "…"
    return sanitized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--repos", type=Path, default=DEFAULT_REPOSITORIES_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_REAL_PRS_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REAL_PRS_REGISTRY_PATH)
    parser.add_argument("--per-repo", type=int, default=None)
    args = parser.parse_args(argv)
    repos, _per_repo, _cutoff = load_repository_config(args.repos)
    failed_count = collect_pull_requests(
        args.repos,
        prs_path=args.out,
        registry_path=args.registry,
        per_repo_override=args.per_repo,
    )
    print(f"Collection finished with {failed_count} failed repositories; results: {args.out}")
    return int(failed_count == len(repos))


if __name__ == "__main__":
    raise SystemExit(main())
