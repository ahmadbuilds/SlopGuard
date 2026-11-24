@AGENTS.md

## Claude Code specifics

- Project skills in `.claude/skills/` and the subagent in `.claude/agents/` are GENERATED from
  `.agents/`. Edit the `.agents/` copy, then run `uv run python scripts/sync_agent_skills.py`.
- After implementing any change and passing checks, delegate review to the `code-quality-reviewer`
  subagent with the task description and the `git diff`. Do not approve your own change.
- Never read or print `.env`.
