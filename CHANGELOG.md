# Changelog

All notable changes to SlopGuard are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The major version tag (`v1`)
always points at the latest `1.x.y` release.

## [Unreleased]

## [1.0.2] - 2026-11-28

### Changed

- The Action's display name is now "SlopGuard PyPI Dependency Check" so it can be listed on the
  GitHub Marketplace. Workflows keep using `ahmadbuilds/SlopGuard@v1`; no change is needed.
- The README shows example screenshots of the job summary, annotation and pull-request comment.

## [1.0.1] - 2026-11-28

### Changed

- Refresh the published documentation and package metadata for the stable release.

### Fixed

- Use singular labels for one dependency, finding or result in GitHub summaries and annotations.

### Security

- Updated the development dependency pytest from 8.3.3 to 9.2.0 to fix GHSA-6w46-j5rx-g56g
  (CVE-2025-71176), a local denial of service through `/tmp/pytest-of-{user}`. pytest is used
  only to test SlopGuard; the Action does not install or run it.

## [1.0.0] - 2026-11-28

First stable release of the SlopGuard GitHub Action.

### Added

- **GitHub Action** (`uses: ahmadbuilds/SlopGuard@v1`)
  - Scans the dependencies a pull request adds, comparing its merge base with its head.
  - Inputs: `config`, `report-path`, `python-version`, `fail-on-block`, `comment` and `github-token`.
  - Outputs: `decision`, `blocked`, `warned` and `report-path`.
  - Exit codes: 0 OK, 1 blocked, 2 configuration error, 3 internal error.
- **Dependency extraction**
  - `requirements*.txt` and constraints files, `pyproject.toml` (PEP 621 dependencies, dependency groups,
    optional dependencies, `build-system.requires`) and Python imports.
  - Imports are mapped to distributions through a generated import-name map. Standard-library
    modules and local modules are excluded.
- **PyPI registry checks**
  - Package existence and release metadata from the PyPI JSON API.
  - Bounded responses and safe decompression, timeouts and retries.
  - An explicit "unverified" state for transient errors, which is never reported as "does not
    exist".
- **Look-alike detection**
  - Compares names against a snapshot of the 5,000 most-downloaded PyPI packages.
  - Uses optimal string alignment distance, separator-insensitive matching and popularity rules.
- **Risk scoring**
  - Configurable weights and thresholds.
  - Explainable per-signal contributions with a remediation for each decision.
  - `warn` and `block` modes, and an allow-list whose entries have a reason and an expiry date.
- **Base-branch policy**
  - The configuration and allow-list are read from the base branch.
  - A pull request that edits `slopguard.toml` is reported, but cannot weaken its own policy.
- **Pull-request output**
  - A readable job summary: the decision in words, the policy and thresholds, unverified results
    first, and one section per finding.
  - Workflow annotations on the changed line.
  - An optional sticky pull-request comment, off by default.
  - Output limits are set in the `[output]` table.
- **JSON scan report** validated by a published schema (contract version 1.1.0).
- **Reproducible evaluation** (`make eval`)
  - 495 labelled names, each verified automatically against its source.
  - Recorded PyPI state, an attack-time scenario, and a false-positive check on 50 real merged
    pull requests.
  - CI fails when results drift.
- **Offline classifier comparison** (`make classifier`)
  - A logistic regression compared with the rule-based score.
  - scikit-learn is used only in the optional `eval` dependency group.

### Security

- Candidate packages are never installed, imported or executed. Sources are parsed with `ast`,
  `tomllib` and `packaging` only.
- Action inputs reach scripts only through environment variables, and third-party actions are
  pinned to full commit SHAs.
- The scanned repository's `uv` configuration cannot change what runs.
- Active allow-list entries are not sent to PyPI.
- Markdown and workflow-command output is escaped against injection.
- The GitHub token reaches the scan step only when the PR comment is enabled. It is never
  forwarded through redirects and is redacted from errors.
- Fork pull requests are never commented on.

## [0.1.0] - 2026-11-25

### Added

- Initial pre-release: dependency extraction, the PyPI registry client, look-alike detection and
  configurable risk scoring.

[Unreleased]: https://github.com/ahmadbuilds/SlopGuard/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/ahmadbuilds/SlopGuard/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/ahmadbuilds/SlopGuard/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/ahmadbuilds/SlopGuard/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/ahmadbuilds/SlopGuard/releases/tag/v0.1.0
