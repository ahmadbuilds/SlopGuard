"""Bounded access to Git objects and pull-request diffs."""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import GitSettings
from .extractor import (
    MAX_DIFF_INPUT_CHARACTERS,
    ChangedFile,
    is_supported_path,
)

GIT_COMMAND_TIMEOUT_SECONDS = 30.0
GIT_FETCH_TIMEOUT_SECONDS = 120.0
GIT_OUTPUT_LIMIT_BYTES = 12_000_000
GIT_READ_CHUNK_BYTES = 64 * 1024
INITIAL_FETCH_DEPTH = 50
REQUIRED_COMMIT_FETCH_DEPTH = 1
MAX_BLOB_SIZE_BYTES = MAX_DIFF_INPUT_CHARACTERS * 4
_RAW_DIFF_PREFIX = b":"
_REVISION_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class GitRepositoryError(RuntimeError):
    """Raised when Git cannot safely provide the requested repository data."""


@dataclass(frozen=True, slots=True)
class GitWarning:
    """A changed file or repository detail skipped during safe Git reading."""

    file: str
    line: int | None
    message: str
    kind: str


@dataclass(frozen=True, slots=True)
class _RawChange:
    old_mode: bytes
    new_mode: bytes
    status: bytes
    old_path: str
    new_path: str


class GitRepository:
    """Read repository objects through argument-list Git commands only."""

    def __init__(self, root: Path, *, settings: GitSettings | None = None) -> None:
        self.root = root.resolve()
        self.settings = settings or GitSettings()

    def merge_base(self, base: str, head: str, *, fetch_depth: int | None = None) -> str:
        """Find the common ancestor, deepening a shallow clone within configured bounds."""

        _validate_revision(base)
        _validate_revision(head)
        maximum_depth = (
            self.settings.fetch_depth
            if fetch_depth is None
            else GitSettings(fetch_depth=fetch_depth).fetch_depth
        )
        try:
            return self._text(self._run("merge-base", base, head)).strip()
        except GitRepositoryError as initial_error:
            shallow_result = self._run("rev-parse", "--is-shallow-repository")
            if self._text(shallow_result).strip() != "true":
                raise initial_error

        depth = min(INITIAL_FETCH_DEPTH, maximum_depth)
        revisions = tuple(dict.fromkeys((base, head)))
        while True:
            for revision in revisions:
                self._run(
                    "fetch",
                    "--no-tags",
                    f"--depth={depth}",
                    "origin",
                    revision,
                    timeout_seconds=GIT_FETCH_TIMEOUT_SECONDS,
                )
            try:
                return self._text(self._run("merge-base", base, head)).strip()
            except GitRepositoryError:
                if depth >= maximum_depth:
                    raise GitRepositoryError(
                        "Could not find a merge base within the configured shallow-fetch depth."
                    ) from None
                depth = min(depth * 2, maximum_depth)

    def ensure_commits(self, *shas: str) -> None:
        """Fetch requested commits missing from a shallow checkout."""

        for sha in dict.fromkeys(shas):
            _validate_revision(sha)
            object_expression = f"{sha}^{{commit}}"
            try:
                self._run("cat-file", "-e", object_expression)
                continue
            except GitRepositoryError:
                pass
            try:
                self._run(
                    "fetch",
                    "--no-tags",
                    f"--depth={REQUIRED_COMMIT_FETCH_DEPTH}",
                    "origin",
                    sha,
                    timeout_seconds=GIT_FETCH_TIMEOUT_SECONDS,
                )
                self._run("cat-file", "-e", object_expression)
            except GitRepositoryError as error:
                raise GitRepositoryError(
                    "Could not fetch a required pull-request commit from origin."
                ) from error

    def changed_files(
        self, merge_base: str, head: str
    ) -> tuple[tuple[ChangedFile, ...], tuple[GitWarning, ...]]:
        """Read supported additions and changes, using the old path for rename bases."""

        _validate_revision(merge_base)
        _validate_revision(head)
        raw = self._run("diff", "--raw", "-z", "-M", merge_base, head)
        changes, path_warnings = _parse_raw_diff(raw)
        files: list[ChangedFile] = []
        warnings = list(path_warnings)
        for change in changes:
            if change.status.startswith(b"D") or change.old_mode in {b"120000", b"160000"}:
                continue
            if change.new_mode in {b"120000", b"160000"}:
                continue
            if not is_supported_path(change.new_path):
                continue
            old_content = ""
            if not change.status.startswith(b"A"):
                old_content, warning = self.read_blob(merge_base, change.old_path)
                if warning is not None:
                    warnings.append(warning)
                if old_content is None:
                    continue
            new_content, warning = self.read_blob(head, change.new_path)
            if warning is not None:
                warnings.append(warning)
            if new_content is None:
                continue
            files.append(ChangedFile(change.new_path, old_content, new_content))
        files.sort(key=lambda item: item.path)
        return tuple(files), tuple(warnings)

    def read_blob(self, commit: str, path: str) -> tuple[str | None, GitWarning | None]:
        """Read one bounded UTF-8 blob, returning a warning instead of unsafe content."""

        _validate_revision(commit)
        object_name = f"{commit}:{path}"
        try:
            size_output = self._run("cat-file", "-s", object_name)
        except GitRepositoryError:
            return None, GitWarning(path, None, "Git blob could not be read.", "git_error")
        try:
            size = int(self._text(size_output).strip())
        except ValueError:
            return None, GitWarning(path, None, "Git returned an invalid blob size.", "git_error")
        if size < 0 or size > MAX_BLOB_SIZE_BYTES:
            return None, GitWarning(
                path, None, "Changed file exceeds the safe blob size limit.", "oversized_file"
            )
        try:
            content = self._run("cat-file", "blob", object_name)
        except GitRepositoryError:
            return None, GitWarning(path, None, "Git blob could not be read.", "git_error")
        try:
            return content.decode("utf-8-sig"), None
        except UnicodeDecodeError:
            return None, GitWarning(
                path, None, "Changed file is not valid UTF-8 and was skipped.", "binary_file"
            )

    def list_files(self, commit: str) -> tuple[str, ...]:
        """List repository paths from a commit tree in deterministic order."""

        _validate_revision(commit)
        output = self._run("ls-tree", "-r", "--name-only", "-z", commit)
        paths: list[str] = []
        for raw_path in output.split(b"\0"):
            if not raw_path:
                continue
            try:
                paths.append(raw_path.decode("utf-8"))
            except UnicodeDecodeError:
                continue
        return tuple(sorted(paths))

    def _run(self, *arguments: str, timeout_seconds: float = GIT_COMMAND_TIMEOUT_SECONDS) -> bytes:
        command = ["git", "-c", "core.fsmonitor=false", *arguments]
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except OSError as error:
            raise GitRepositoryError("Could not start Git.") from error

        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()

        def collect(stream, destination: bytearray) -> None:
            while chunk := stream.read(GIT_READ_CHUNK_BYTES):
                if len(destination) + len(chunk) > GIT_OUTPUT_LIMIT_BYTES:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                destination.extend(chunk)

        readers = (
            threading.Thread(target=collect, args=(process.stdout, stdout), daemon=True),
            threading.Thread(target=collect, args=(process.stderr, stderr), daemon=True),
        )
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            return_code = process.wait()
        for reader in readers:
            reader.join()
        if overflow.is_set():
            raise GitRepositoryError("Git output exceeded the configured safety limit.")
        if timed_out:
            raise GitRepositoryError("Git command exceeded its time limit.")
        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise GitRepositoryError(detail or "Git command failed.")
        return bytes(stdout)

    @staticmethod
    def _text(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")


def _parse_raw_diff(raw: bytes) -> tuple[tuple[_RawChange, ...], tuple[GitWarning, ...]]:
    """Parse the NUL-delimited output of `git diff --raw -z`."""

    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    result: list[_RawChange] = []
    warnings: list[GitWarning] = []
    index = 0
    while index < len(fields):
        metadata = fields[index]
        index += 1
        if not metadata.startswith(_RAW_DIFF_PREFIX):
            raise GitRepositoryError("Git returned malformed raw diff metadata.")
        parts = metadata[1:].split()
        if len(parts) != 5:
            raise GitRepositoryError("Git returned malformed raw diff metadata.")
        old_mode, new_mode, _old_oid, _new_oid, status = parts
        path_count = 2 if status.startswith((b"R", b"C")) else 1
        if index + path_count > len(fields):
            raise GitRepositoryError("Git returned incomplete raw diff paths.")
        paths = fields[index : index + path_count]
        index += path_count
        try:
            decoded = [path.decode("utf-8") for path in paths]
        except UnicodeDecodeError:
            warnings.append(
                GitWarning(
                    "<non-UTF-8 path>",
                    None,
                    "Changed path is not valid UTF-8 and was skipped.",
                    "invalid_path",
                )
            )
            continue
        old_path = decoded[0]
        new_path = decoded[-1]
        result.append(_RawChange(old_mode, new_mode, status, old_path, new_path))
    return tuple(result), tuple(warnings)


def _validate_revision(revision: str) -> None:
    if not _REVISION_PATTERN.fullmatch(revision):
        raise GitRepositoryError("Git revisions must be full hexadecimal object IDs.")
