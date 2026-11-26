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

Configuration can live in a standalone `slopguard.toml` file or under `[tool.slopguard]` in
`pyproject.toml`. Copy [`examples/slopguard.toml`](examples/slopguard.toml) for documented defaults.
The library defaults to `mode = "warn"`; the sample workflow will use `mode = "block"`. Scoring is
a configurable heuristic that helps prioritize review; it is not proof that a package is malware.
An allow-list exception passes a result while retaining its score and evidence. Unknown registry
results follow `on_unknown`, which defaults to `warn`.

The configuration loader prefers an explicit path, then `slopguard.toml`, then the
`[tool.slopguard]` table in `pyproject.toml`, then built-in defaults. PR scans read policy from the
base branch tip so a change cannot loosen its own policy; dependency differences are still
calculated from the merge base.

## GitHub Action

Add the published action to a pull-request workflow. The workflow runs for every pull request so
the `SlopGuard` required check always reports, including when no dependency files changed; scans
with no relevant changes pass quickly. Checkout the default pull-request merge ref with full
history so the base, head and merge-base commits are local:

```yaml
name: Dependency security

on:
  pull_request:

permissions:
  contents: read

jobs:
  slopguard:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: ahmadbuilds/SlopGuard@v1
        with:
          config: slopguard.toml
          fail-on-block: "true"
```

`config` is optional and names a repository-relative TOML file. `report-path` defaults to a JSON
file under the runner temporary directory. `python-version` defaults to 3.11. Set `fail-on-block`
to `"false"` to keep the step successful while retaining a `block` decision in the report.

The action exposes `decision`, `blocked`, `warned`, and `report-path` outputs. It also writes a
Markdown summary to `$GITHUB_STEP_SUMMARY` and outputs to `$GITHUB_OUTPUT` when those files are
available. Exit codes are 0 for pass/warn, 1 for a blocking decision when `fail-on-block` is true,
2 for unsupported events or invalid configuration, and 3 for internal errors. `--no-fail-on-block`
provides the same behavior as setting `fail-on-block` to false for direct CLI use.

The workflow requires only `contents: read`; it does not need write permissions or repository
secrets. Network access is limited to anonymous HTTPS requests to `pypi.org`. GitHub is contacted
only when SlopGuard must use its bounded shallow-fetch fallback; on private repositories, that
fallback requires read credentials. With the full-history checkout shown above, no fallback fetch
is needed. Fork pull requests work with the read-only token. Automated PR comments are planned for
SG-07. Reruns are deterministic for the same base and head commits except for live PyPI state, which
can change between runs. After adding the workflow, mark the `SlopGuard` job as a required status
check in branch protection or the repository's ruleset.

## More information

- [Architecture and data flow](ARCHITECTURE.md)
- [Security model and vulnerability reporting](SECURITY.md)
- [Repository rules for contributors and coding agents](AGENTS.md)
