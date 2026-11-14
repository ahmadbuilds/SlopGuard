# Contributing

## Branches

- Keep `main` releasable and base each short-lived branch on the latest `main`.
- Use `feature/voi-<issue>-<short-name>` for implementation, `fix/voi-<issue>-<short-name>` for fixes, and `docs/voi-<issue>-<short-name>` for documentation.
- Do not merge feature work until the shared contract changes are merged or the feature branch is rebased on the contract update.

## Pull requests

- Link the related Linear issue and summarize behavior and contract changes.
- Include or update schema-valid examples when changing serialized output.
- Describe security or policy implications, including warn/block behavior.
- Before requesting review, run the commands in the README and ensure the smoke workflow passes.
- Request review from the owners of affected contract consumers. For VOI-110, obtain confirmation from Ahsan and Ahmad that they can build against the published contract.
- Prefer squash merges; delete the branch after merge.

## Scope and secrets

The MVP targets Python packages from PyPI. Do not add package installation or package execution to the scanner. Never commit API keys, tokens, `.env` files, or realistic credentials; use placeholders in documentation.
