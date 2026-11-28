@AGENTS.md

# Claude Code

Follow [AGENTS.md](AGENTS.md) for repository-wide instructions, commands, module responsibilities, and branch conventions.

## Claude Code specifics

- Project skills in `.claude/skills/` and the reviewer agent in `.claude/agents/` are generated from `.agents/`. Edit the `.agents/` source, then run `uv run python scripts/sync_agent_skills.py`.
- After implementation and checks, request an independent review from the `code-quality-reviewer` agent. Do not approve your own changes.
- Never read or print `.env`.
