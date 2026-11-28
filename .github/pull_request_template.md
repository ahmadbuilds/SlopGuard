## Summary

<!-- What changed and why? -->

## Related issue

<!-- Link a related GitHub issue and use Closes #… when the pull request resolves it. -->

## Checks run

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run pytest`
- [ ] `uv run python scripts/sync_agent_skills.py --check` (when agent files change)
- [ ] `make eval` or `make classifier` (when evaluation behavior or artifacts change)

## Contract change?

- [ ] No
- [ ] Yes — describe compatibility and fixture updates:

## Security impact

<!-- Describe input handling, network, permissions, and policy impact. -->

## New dependency

- [ ] No
- [ ] Yes — dependency, purpose, and why existing dependencies are insufficient:
