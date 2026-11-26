"""Short-lived fixtures for real-Git integration tests."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """Create a short checkout-local temporary path for Windows Git compatibility."""

    with tempfile.TemporaryDirectory(prefix=".sg-test-", dir=Path.cwd()) as directory:
        yield Path(directory)


@pytest.fixture
def shallow_merge_checkout(short_tmp_path: Path) -> tuple[Path, str, str, str]:
    """Build a depth-one PR merge checkout with base and head stored only at origin."""

    origin = short_tmp_path / "origin.git"
    _git(short_tmp_path, "init", "--bare", str(origin))
    work = short_tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.name", "SlopGuard tests")
    _git(work, "config", "user.email", "tests@example.invalid")
    _git(work, "remote", "add", "origin", origin.as_uri())
    (work / "requirements.txt").write_text("# base\n", encoding="utf-8")
    _git(work, "add", "requirements.txt")
    _git(work, "commit", "-m", "common ancestor")
    common = _git(work, "rev-parse", "HEAD")
    _git(work, "switch", "-c", "feature")
    (work / "requirements.txt").write_text("# base\nrequests\n", encoding="utf-8")
    _git(work, "commit", "-am", "feature dependency")
    head = _git(work, "rev-parse", "HEAD")
    _git(work, "switch", "main")
    (work / "README.md").write_text("Base branch update.\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "base branch update")
    base = _git(work, "rev-parse", "HEAD")
    _git(work, "merge", "--no-ff", "feature", "-m", "pull request merge")
    merge = _git(work, "rev-parse", "HEAD")
    checkout = short_tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-b", "main")
    _git(checkout, "remote", "add", "origin", origin.as_uri())
    try:
        _git(work, "push", "origin", "main", "feature")
        _git(
            checkout,
            "fetch",
            "--depth=1",
            "origin",
            f"+{merge}:refs/remotes/pull/1/merge",
        )
    except subprocess.CalledProcessError as error:
        if "NtCreateDirectoryObject" in str(error.stderr):
            pytest.skip("Git for Windows cannot start the local upload-pack helper in this sandbox")
        raise
    assert not _contains_commit(checkout, base)
    assert not _contains_commit(checkout, head)
    return checkout, base, head, common


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def _contains_commit(root: Path, sha: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=root,
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0
