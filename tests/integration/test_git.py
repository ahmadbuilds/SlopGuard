"""Integration coverage for bounded Git object and diff reads."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from slopguard.config import GitSettings
from slopguard.git import (
    MAX_BLOB_SIZE_BYTES,
    GitRepository,
    GitRepositoryError,
    _parse_raw_diff,
)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "SlopGuard tests")
    _git(path, "config", "user.email", "tests@example.invalid")
    (path / "requirements.txt").write_text("requests\n", encoding="utf-8")
    _git(path, "add", "requirements.txt")
    _git(path, "commit", "-m", "base")
    return path, _git(path, "rev-parse", "HEAD")


def test_changed_files_reads_supported_content_and_skips_binary_and_symlink(
    short_tmp_path: Path,
) -> None:
    root, base = _repository(short_tmp_path / "repo")
    (root / "requirements.txt").write_text("requests\nreqeusts\n", encoding="utf-8")
    (root / "script.py").write_bytes(b"import requests\n\xff")
    (root / "notes.txt").write_text("ignored\n", encoding="utf-8")
    _git(root, "add", "-A")
    symlink_blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=root,
            input=b"requirements.txt",
            check=True,
            capture_output=True,
            timeout=10,
        )
        .stdout.decode("ascii")
        .strip()
    )
    _git(root, "update-index", "--add", "--cacheinfo", f"120000,{symlink_blob},link.py")
    _git(root, "commit", "-m", "head")
    head = _git(root, "rev-parse", "HEAD")

    files, warnings = GitRepository(root).changed_files(base, head)

    assert [(item.path, item.base_content, item.content) for item in files] == [
        ("requirements.txt", "requests\n", "requests\nreqeusts\n")
    ]
    assert [(item.file, item.kind) for item in warnings] == [("script.py", "binary_file")]


def test_renamed_supported_file_uses_old_path_for_base_content(short_tmp_path: Path) -> None:
    root, base = _repository(short_tmp_path / "repo")
    (root / "requirements.txt").rename(root / "constraints.txt")
    (root / "constraints.txt").write_text("requests\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "rename")
    head = _git(root, "rev-parse", "HEAD")

    files, warnings = GitRepository(root).changed_files(base, head)

    assert warnings == ()
    assert len(files) == 1
    assert files[0].path == "constraints.txt"
    assert files[0].base_content == "requests\n"
    assert files[0].content == "requests\n"


def test_raw_diff_parser_preserves_newlines_and_renames() -> None:
    raw = b":100644 100644 1111111 2222222 R100\0old-requirements.txt\0new\nrequirements.txt\0"

    parsed, warnings = _parse_raw_diff(raw)

    assert len(parsed) == 1
    assert warnings == ()
    assert parsed[0].old_path == "old-requirements.txt"
    assert parsed[0].new_path == "new\nrequirements.txt"
    assert parsed[0].status == b"R100"


def test_non_utf8_diff_path_is_skipped_with_a_warning(
    short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = GitRepository(short_tmp_path)
    invalid_path_diff = b":000000 100644 0000000 2222222 A\0bad-\xff-requirements.txt\0"
    monkeypatch.setattr(repository, "_run", lambda *arguments, **kwargs: invalid_path_diff)

    files, warnings = repository.changed_files("a" * 40, "b" * 40)

    assert files == ()
    assert len(warnings) == 1
    assert warnings[0].kind == "invalid_path"


def test_merge_base_and_list_files_use_git_tree(short_tmp_path: Path) -> None:
    root, base = _repository(short_tmp_path / "repo")
    (root / "requirements.txt").write_text("requests\nhttpx\n", encoding="utf-8")
    _git(root, "add", "requirements.txt")
    _git(root, "commit", "-m", "head")
    head = _git(root, "rev-parse", "HEAD")
    repository = GitRepository(root)

    assert repository.merge_base(base, head) == base
    assert repository.list_files(head) == ("requirements.txt",)


def test_oversized_blob_is_skipped_before_content_is_read(short_tmp_path: Path) -> None:
    root, _base = _repository(short_tmp_path / "repo")
    (root / "requirements.txt").write_bytes(b"x" * (MAX_BLOB_SIZE_BYTES + 1))
    _git(root, "add", "requirements.txt")
    _git(root, "commit", "-m", "oversized manifest")
    head = _git(root, "rev-parse", "HEAD")

    content, warning = GitRepository(root).read_blob(head, "requirements.txt")

    assert content is None
    assert warning is not None and warning.kind == "oversized_file"


def test_merge_base_deepens_a_shallow_clone_from_origin(
    short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = GitRepository(short_tmp_path)
    calls: list[tuple[str, ...]] = []
    merge_base_calls = 0
    expected = "a" * 40

    def fake_run(*arguments: str, timeout_seconds: float | None = None) -> bytes:
        nonlocal merge_base_calls
        calls.append(arguments)
        if arguments[0] == "merge-base":
            merge_base_calls += 1
            if merge_base_calls == 1:
                raise GitRepositoryError("object is missing")
            return expected.encode("ascii")
        if arguments[0] == "rev-parse":
            return b"true\n"
        return b""

    monkeypatch.setattr(repository, "_run", fake_run)

    assert repository.merge_base("b" * 40, "c" * 40) == expected
    assert [call[0] for call in calls if call[0] == "fetch"] == ["fetch", "fetch"]
    assert all("--depth=50" in call and "origin" in call for call in calls if call[0] == "fetch")


def test_merge_base_fails_clearly_at_the_configured_fetch_depth(
    short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = GitRepository(short_tmp_path, settings=GitSettings(fetch_depth=1))

    def fake_run(*arguments: str, timeout_seconds: float | None = None) -> bytes:
        if arguments[0] == "merge-base":
            raise GitRepositoryError("object is missing")
        if arguments[0] == "rev-parse":
            return b"true\n"
        return b""

    monkeypatch.setattr(repository, "_run", fake_run)
    with pytest.raises(GitRepositoryError, match="configured shallow-fetch depth"):
        repository.merge_base("b" * 40, "c" * 40)


def test_git_revisions_must_be_full_object_ids(short_tmp_path: Path) -> None:
    with pytest.raises(GitRepositoryError, match="full hexadecimal object IDs"):
        GitRepository(short_tmp_path).merge_base("--upload-pack=unsafe", "0" * 40)
