"""Mirror shared agent files from .agents/ into .claude/ so Codex CLI and Claude Code share one source.

    .agents/skills/<name>/          -> .claude/skills/<name>/     (Codex reads .agents/skills)
    .agents/claude-agents/<name>.md -> .claude/agents/<name>.md   (Claude Code subagents)

Only items listed in each destination's manifest (.synced-from-agents) are managed. Files that exist
only under .claude/ (for example skills Claude Code records itself, or subagents written directly
there) are left alone.

    python scripts/sync_agent_skills.py          # copy
    python scripts/sync_agent_skills.py --check  # exit 1 if out of sync (CI / pre-commit)
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_SRC = ROOT / ".agents" / "skills"
SKILLS_DST = ROOT / ".claude" / "skills"
AGENTS_SRC = ROOT / ".agents" / "claude-agents"
AGENTS_DST = ROOT / ".claude" / "agents"
MANIFEST_NAME = ".synced-from-agents"


def _skills(base: Path) -> list[str]:
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "SKILL.md").is_file())


def _agents(base: Path) -> list[str]:
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_file() and p.suffix == ".md")


def _managed(dst: Path) -> set[str]:
    manifest = dst / MANIFEST_NAME
    return set(manifest.read_text("utf-8").split()) if manifest.exists() else set()


def _write_manifest(dst: Path, names: list[str]) -> None:
    (dst / MANIFEST_NAME).write_text(
        "".join(f"{n}\n" for n in names), encoding="utf-8", newline="\n"
    )


def _skill_differences() -> list[str]:
    problems: list[str] = []
    source = _skills(SKILLS_SRC)
    for name in source:
        if not (SKILLS_DST / name).exists():
            problems.append(f"missing in .claude/skills: {name}")
            continue
        stack = [filecmp.dircmp(SKILLS_SRC / name, SKILLS_DST / name)]
        while stack:
            c = stack.pop()
            if c.left_only or c.right_only or c.diff_files:
                problems.append(f"{name}: differs ({c.left_only + c.right_only + c.diff_files})")
            stack.extend(c.subdirs.values())
    for stale in sorted(_managed(SKILLS_DST) - set(source)):
        if (SKILLS_DST / stale).exists():
            problems.append(f"stale in .claude/skills: {stale}")
    return problems


def _agent_differences() -> list[str]:
    problems: list[str] = []
    source = _agents(AGENTS_SRC)
    for name in source:
        dst = AGENTS_DST / name
        if not dst.exists():
            problems.append(f"missing in .claude/agents: {name}")
        elif not filecmp.cmp(AGENTS_SRC / name, dst, shallow=False):
            problems.append(f"differs in .claude/agents: {name}")
    for stale in sorted(_managed(AGENTS_DST) - set(source)):
        if (AGENTS_DST / stale).exists():
            problems.append(f"stale in .claude/agents: {stale}")
    return problems


def sync() -> None:
    SKILLS_DST.mkdir(parents=True, exist_ok=True)
    skills = _skills(SKILLS_SRC)
    for stale in _managed(SKILLS_DST) - set(skills):
        shutil.rmtree(SKILLS_DST / stale, ignore_errors=True)
    for name in skills:
        shutil.rmtree(SKILLS_DST / name, ignore_errors=True)
        shutil.copytree(SKILLS_SRC / name, SKILLS_DST / name)
    _write_manifest(SKILLS_DST, skills)

    agents = _agents(AGENTS_SRC)
    if agents or _managed(AGENTS_DST):
        AGENTS_DST.mkdir(parents=True, exist_ok=True)
        for stale in _managed(AGENTS_DST) - set(agents):
            (AGENTS_DST / stale).unlink(missing_ok=True)
        for name in agents:
            shutil.copyfile(AGENTS_SRC / name, AGENTS_DST / name)
        _write_manifest(AGENTS_DST, agents)

    print(f"synced {len(skills)} skills -> {SKILLS_DST.relative_to(ROOT)}")
    print(f"synced {len(agents)} subagents -> {AGENTS_DST.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    if parser.parse_args().check:
        problems = _skill_differences() + _agent_differences()
        for p in problems:
            print(p)
        if problems:
            print("run: python scripts/sync_agent_skills.py")
        return 1 if problems else 0
    sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
