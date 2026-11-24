# SlopGuard

SlopGuard checks dependency names added to Python projects for packages that are missing from PyPI
and names that imitate popular packages. It extracts additions from changed files and looks up
metadata from PyPI. SlopGuard reads package metadata only; it never installs or executes packages.
Look-alike checks use a packaged popularity snapshot and do not make network requests.

## Quick start for contributors

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/sync_agent_skills.py --check
```

Run `make check` to run lint, formatting, tests, and the generated agent-file check together.

Refresh the popular-package snapshot manually with `uv run python scripts/update_popular_packages.py`.
The default detector compares against the top 5,000 names; it does not detect homoglyphs or
combosquatting, and the snapshot can become stale until it is refreshed.

## Configuration

Copy [`examples/slopguard.toml`](examples/slopguard.toml) as a starting point. It contains safe
policy and scoring defaults and no credentials. The current registry client uses the public PyPI
endpoint and does not read registry URL or timeout values from this configuration.

## More information

- [Architecture and data flow](ARCHITECTURE.md)
- [Security model and vulnerability reporting](SECURITY.md)
- [Repository rules for contributors and coding agents](AGENTS.md)
