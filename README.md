# SlopGuard

[![CI](https://github.com/ahmadbuilds/SlopGuard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ahmadbuilds/SlopGuard/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ahmadbuilds/SlopGuard/badge)](https://scorecard.dev/viewer/?uri=github.com/ahmadbuilds/SlopGuard)
[![MIT license](https://img.shields.io/github/license/ahmadbuilds/SlopGuard)](LICENSE)

SlopGuard checks new Python dependency names for missing PyPI projects and look-alikes of popular packages. It reads dependency declarations and package metadata but never installs or executes a package under review.

## Contents

- [Quick start](#quick-start)
- [Usage and inputs](#usage-and-inputs)
- [Outputs](#outputs)
- [Scenarios](#scenarios)
- [Recommended permissions](#recommended-permissions)
- [How it works](#how-it-works)
- [Reading the results](#reading-the-results)
- [Configuration](#configuration)
- [Run locally](#run-locally)
- [FAQ and troubleshooting](#faq-and-troubleshooting)
- [Evaluation](#evaluation)
- [Limitations](#limitations)
- [Versioning](#versioning)
- [Contributing, security, and license](#contributing-security-and-license)

## Quick start

Add this workflow to `.github/workflows/slopguard.yml`:

```yaml
name: SlopGuard

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
```

This runs on `pull_request` so the scanner sees proposed changes without granting write access to pull-request code. `contents: read` lets checkout read the repository. `fetch-depth: 0` provides the history needed to find the merge base. `persist-credentials: false` prevents checkout from leaving its token in Git configuration. The default policy warns and does not block.

## Usage and inputs

Copy this step into your workflow's `steps:` list after checkout. The comments show each input's default from [`action.yml`](action.yml).

```yaml
- name: Scan dependency changes
  uses: ahmadbuilds/SlopGuard@v1
  with:
    # Repository-relative TOML configuration file.
    # Default: ""
    config: ""
    # JSON report path; empty uses the runner's temporary directory.
    # Default: ""
    report-path: ""
    # Python version used by the action.
    # Default: "3.11"
    python-version: "3.11"
    # Fail the action step when the policy decision is block.
    # Default: "true"
    fail-on-block: "true"
    # Create or update a sticky pull-request comment.
    # Default: "false"
    comment: "false"
    # Token used only when comments are enabled.
    # Default: ${{ github.token }}
    github-token: ${{ github.token }}
```

| Input | Default | Description |
| --- | --- | --- |
| `config` | `""` | Repository-relative TOML file. An empty value enables configuration discovery. |
| `report-path` | `""` | JSON report path. An empty value writes to the runner's temporary directory. |
| `python-version` | `3.11` | Python version used to run SlopGuard. |
| `fail-on-block` | `true` | Return a failing step when the policy decision is `block`. |
| `comment` | `false` | Create or update the sticky pull-request comment when set to `"true"`. |
| `github-token` | `${{ github.token }}` | Token for the optional comment. It is exposed to the scan only when `comment` is `"true"`; fork pull requests are skipped. |

## Outputs

| Output | Description |
| --- | --- |
| `decision` | Overall `pass`, `warn`, or `block` decision. |
| `blocked` | Blocked dependency names, one per line. |
| `warned` | Warned dependency names, one per line. |
| `report-path` | Path to the JSON report. |

The action writes a Markdown job summary to the runner-provided summary file and emits annotations as GitHub workflow commands on stdout.

## Scenarios

### Warn only

The default policy is `warn`. This complete workflow uses that default and lets the pull request continue while showing findings:

```yaml
name: SlopGuard

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
```

### Block a pull request

Copy [`examples/slopguard.block.toml`](examples/slopguard.block.toml) to `slopguard.toml` in the target repository. Its `mode = "block"` setting applies the built-in thresholds. Then use this workflow:

```yaml
name: SlopGuard

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
```

### Allow-list a dependency

Copy [`examples/slopguard.allowlist.toml`](examples/slopguard.allowlist.toml) to `slopguard.toml`, replace the example package with the exact dependency name, and keep a reason and expiry with each exception. SlopGuard does not send an active allow-listed name to PyPI. The workflow discovers the root configuration automatically:

```yaml
name: SlopGuard

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
```

### Post a sticky pull-request comment

Comments are off by default. This workflow enables them and grants only the additional permission required to write a comment. The action skips fork pull requests.

```yaml
name: SlopGuard

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

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
          comment: "true"
          github-token: ${{ github.token }}
```

### Use the decision output

Give the action step an `id`, then use its `decision` output in a later step:

```yaml
name: SlopGuard

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
      - id: scan
        uses: ahmadbuilds/SlopGuard@v1
      - name: Handle warnings
        if: steps.scan.outputs.decision == 'warn'
        run: echo "Review the SlopGuard findings in this job."
```

### Choose a configuration file

For a file under a subdirectory, set `config` to its repository-relative path:

```yaml
name: SlopGuard

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
          config: .github/slopguard.toml
```

To use `pyproject.toml`, put the configuration under `[tool.slopguard]` and omit the `config` input. For example:

```toml
[tool.slopguard.policy]
mode = "warn"
on_unknown = "warn"
```

### GitHub Enterprise Server

The action uses the runner's `GITHUB_API_URL` for optional comments, so it can address the GitHub Enterprise Server API. Keep the same least-privilege permissions and use an action reference your instance can access:

```yaml
name: SlopGuard

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  slopguard:
    runs-on: self-hosted
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: ahmadbuilds/SlopGuard@v1
        with:
          comment: "true"
```

## Recommended permissions

| Permission | When you need it | Why |
| --- | --- | --- |
| `contents: read` | Every scan using checkout | Reads the base and pull-request commits. |
| `pull-requests: write` | Only when `comment: "true"` | Creates or updates the sticky comment. |

> [!IMPORTANT]
> Keep repository settings from sending write tokens or secrets to fork workflows. The action skips its comment on fork pull requests. See the [security policy](SECURITY.md) and [GitHub's fork workflow guidance](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflows-in-forked-repositories).

## How it works

- SlopGuard compares the pull-request head with its merge base and extracts newly added dependencies from supported manifests and absolute Python imports.
- It reads the scan policy from the base branch, so a pull request cannot loosen the policy that evaluates itself.
- For names that are not actively allow-listed, it queries the public PyPI JSON API for package metadata. It never downloads a distribution.
- It compares names locally with the packaged popularity snapshot to find look-alikes.
- It combines registry and similarity evidence into an explainable score and applies the configured `warn` or `block` policy. Unknown registry results stay `unverified`.
- It writes a JSON report, a job summary, workflow annotations, and, when enabled, one sticky pull-request comment.

Read the [architecture guide](ARCHITECTURE.md) for the data flow, result contract, and security boundaries.

## Reading the results

The job summary begins with `PASS`, `WARN`, or `BLOCK` and explains which policy was used. It reports blocked and warned dependencies, unverified registry lookups, and any findings omitted to stay within GitHub's output limits. An unverified lookup is not evidence that a package does not exist.

Annotations link findings to changed files. If comments are enabled, SlopGuard creates or updates a sticky pull-request comment with the same decision and remediation details. Allow-listed dependencies appear as passed exceptions with their reason and expiry.

## Configuration

Copy [`examples/slopguard.toml`](examples/slopguard.toml) to your repository as `slopguard.toml` to see every setting and its default. The action also discovers `[tool.slopguard]` in `pyproject.toml`; an explicit `config` input takes precedence.

| Section | What it controls |
| --- | --- |
| `[policy]` | `warn` or `block` mode, decision thresholds, and treatment of unknown registry results. |
| `[registry]` | PyPI request limits, concurrency, and retry behavior. |
| `[similarity]` | Popularity snapshot window and look-alike matching. |
| `[scoring]` | Severity levels, signal weights, and score factors. |
| `[output]` | Summary, annotation, and comment limits. |
| `[git]` | Bounded history depth for shallow checkouts. |
| `[[allowlist]]` | Package exceptions with a reason and optional expiry. |

## Run locally

Run SlopGuard against your own project without installing it into that project. Set `PROJECT_DIR` to its checkout, then scan the merge base and current commit:

```sh
PROJECT_DIR="../your-project"
git -C "$PROJECT_DIR" fetch origin main
BASE_SHA="$(git -C "$PROJECT_DIR" merge-base origin/main HEAD)"
HEAD_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
uvx --from git+https://github.com/ahmadbuilds/SlopGuard@v1 slopguard scan \
  --repo "$PROJECT_DIR" --base "$BASE_SHA" --head "$HEAD_SHA" \
  --report slopguard-report.json --summary slopguard-summary.md
```

The command prints the policy decision and writes the JSON report and Markdown summary to the specified paths. See `uvx --from git+https://github.com/ahmadbuilds/SlopGuard@v1 slopguard scan --help` for the other CLI options.

## FAQ and troubleshooting

### Which exit codes can I get?

A successful `pass` or `warn` returns zero. A `block` returns one when `fail-on-block` is enabled. Exit code two indicates an unsupported event or invalid configuration. Exit code three indicates an internal error; inspect the job log and report an issue if it persists.

### Why does the scan say it cannot find a merge base?

The scanner needs the base, head, and merge-base commits locally. Use checkout with `fetch-depth: 0`. If you keep a shallow checkout, provide fetch credentials and history so the bounded fallback can retrieve the missing commits.

### What does `unverified` mean?

PyPI timed out, rate-limited the request, returned malformed data, or could not be reached. SlopGuard does not treat these states as evidence that a package is absent. Review the job summary and rerun after the registry is reachable.

### What should I do about a false positive?

Check the normalized package name and source location. If the dependency is expected, add a narrowly scoped allow-list entry with a reason and expiry. Then report the case using the [false-positive form](https://github.com/ahmadbuilds/SlopGuard/issues/new?template=false_positive.yml).

### Can I use this in a private repository?

Yes. SlopGuard sends only non-allow-listed package names to the public PyPI JSON API; it does not send repository source, filenames, or pull-request text. Optional comments use the GitHub API configured by the runner.

### Why did a fork pull request not get a comment?

SlopGuard skips comments on fork pull requests, even when a token is available. The job summary and annotations still report findings.

### Does SlopGuard scan transitive dependencies?

No. It scans dependency declarations added by the pull request and supported absolute Python imports. It does not resolve transitive dependencies.

## Evaluation

The checked-in evaluation reports compare default and blocking policies against recorded data. See the [evaluation guide](evals/README.md), [default-policy report](evals/results/defaults/report.md), and [block-policy report](evals/results/block/report.md) for the method and results. These reports describe the recorded datasets and do not predict how future packages will behave.

## Limitations

SlopGuard is a dependency-name review aid, not a malware or vulnerability scanner. It does not inspect package artifacts or runtime behavior, verify publishers or provenance, resolve transitive dependencies, or cover every dependency syntax. Popularity data can become stale, and look-alike matching can produce false positives and false negatives. A passing result is not a guarantee that a dependency is safe.

## Versioning

The examples use `@v1`, which follows the latest compatible `1.x.y` release. For stricter supply-chain control, replace `@v1` with a full commit SHA and let Dependabot propose reviewed updates.

## Contributing, security, and license

See [Contributing](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and the [security policy](SECURITY.md). SlopGuard is licensed under the [MIT License](LICENSE).
