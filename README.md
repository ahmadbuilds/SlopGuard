# SlopGuard

[![CI](https://github.com/ahmadbuilds/SlopGuard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ahmadbuilds/SlopGuard/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ahmadbuilds/SlopGuard/badge)](https://scorecard.dev/viewer/?uri=github.com/ahmadbuilds/SlopGuard)

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
An active allow-list exception passes a result while retaining its score and evidence; its package
name is not sent to PyPI. Expired entries are looked up and reported as warnings. Unknown registry
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

The workflow requires only `contents: read` by default and does not need repository secrets.
With comments off, SlopGuard makes anonymous HTTPS requests to `pypi.org`; GitHub is contacted only
for the bounded shallow-fetch fallback. On private repositories, that fallback requires read
credentials. With the full-history checkout shown above, no fallback fetch is needed. Fork pull
requests work with the read-only token. Reruns are deterministic for the same base and head commits
except for live PyPI state, which can change between runs. After adding the workflow, mark the
`SlopGuard` job as a required status check in branch protection or the repository's ruleset.

### PR comment (optional)

Set `comment: "true"` on the action and grant `pull-requests: write` alongside `contents: read`.
When enabled, SlopGuard also calls the GitHub API configured by the runner.
SlopGuard creates one marked sticky comment for findings and updates it on later runs; a clean scan
only updates an existing comment to report no findings. Fork pull requests are always skipped, even
when a token is present. The default is off, and the token is passed through the action environment.

## Reading the results

The job summary starts with the overall `PASS`, `WARN`, or `BLOCK` decision and package counts. Its
policy line shows the mode, warning and block thresholds, unknown-result behavior, and configuration
source. When a pull request changes configuration, the summary says that the base-branch policy was
used for the scan.

Unverified package metadata appears near the top so it remains visible in a long summary. Unknown
metadata is unverified, not evidence that the package does not exist. Blocked and warned findings
show requested and normalized names, source locations, registry state, closest look-alike, score
signals, available release facts, and remediation. Passed dependencies are in a collapsed list;
scan warnings and tool, workflow-run, and documentation links follow.

GitHub workflow annotations highlight blocked results as errors, warnings as warnings, and
unverified results that passed policy as notices. `--annotations auto` (the default) emits them only
when `GITHUB_ACTIONS` is `true`; `--annotations always` and `--annotations never` force either
behavior. The annotation limit applies independently from the summary finding limit; the summary
reports how many findings it omitted.

## Security boundaries

SlopGuard analyzes dependency declarations and Python imports from the pull request diff. It does
not install or execute those dependencies, download their archives, or run their setup scripts.
Registry requests put a normalized package name in the public PyPI JSON API URL and identify the
SlopGuard version and repository in the User-Agent. Repository source, filenames, and pull-request
text are not sent. A registry error stays unverified and appears in the scan summary, including
when `on_unknown = "pass"` is explicitly configured. That policy setting does not mean the package
was verified.

This is a review aid, not a malware or vulnerability scanner. It does not resolve every dependency
format or transitive dependency, verify publisher identity or artifact provenance, or detect
homoglyph and combosquatting attacks. The optional secret scan is not implemented. See the
[security model](SECURITY.md) and the [VOI-118 review record](SECURITY_REVIEW.md) for the threat
model, checks, findings, and known limits.

For forked pull requests, the workflow requests only `contents: read` and does not reference
repository secrets. Keep the repository's Actions fork settings from granting write tokens or
secrets to fork workflows; [GitHub documents how those settings affect fork runs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflows-in-forked-repositories).

## More information

- [Architecture and data flow](ARCHITECTURE.md)
- [Offline evaluation tools, metrics, and data workflow](evals/README.md)
- [Security model and vulnerability reporting](SECURITY.md)
- [Repository rules for contributors and coding agents](AGENTS.md)
