# VOI-118 security review

Reviewed 27 November 2026 against `main` at `be1dee3` and the changes on
`fix/voi-118-security-boundaries`. Scope is the integrated scanner, registry client, CLI output,
Action definition, workflows, configuration and their tests.

## Acceptance checklist

- [x] No candidate dependency code, archive, setup script or module is installed, imported or
  executed. Extractor and configuration inputs are parsed as data. The Action installs only
  SlopGuard itself from the Action's locked project; it does not install the scanned project's
  dependencies.
- [x] The self-test and sample workflows run on `pull_request`, request only `contents: read`, pin
  third-party Actions and disable persisted checkout credentials. No workflow references repository
  secrets or uses `pull_request_target`. Inputs are passed to the shell through environment
  variables and argument arrays; output text is escaped and GitHub output delimiters are random.
- [x] Registry failures remain distinct from `not_found`. They follow the configured unknown policy
  and are visible in JSON and in the Markdown summary, including when the policy decision is `pass`.
- [x] Review findings are fixed or recorded below with severity, owner, impact and disposition.
- [x] Threat model, privacy/network behavior, unsupported analysis areas and false-positive and
  false-negative limits are documented in [SECURITY.md](SECURITY.md) and summarized in
  [README.md](README.md).

For fork pull requests, GitHub's normal policy supplies a read-only `GITHUB_TOKEN` and withholds
repository secrets. The workflow files themselves do not reference secrets. Repository-level Actions
settings can change fork behavior; keep settings that send write tokens or secrets to fork workflows
disabled. This review checks the workflow configuration but does not inspect repository-level
Actions settings or run a dedicated fork PR.

## Findings

### Resolved findings

| Severity | Owner | Impact | Resolution |
| --- | --- | --- | --- |
| High | Abdullah Latif | A repository containing `src/unrelated/requests.py` caused `local_modules_from_paths` to treat `requests` as a top-level local module. A test helper such as `tests/requests.py` could create the same suppression globally. A changed application `import requests` could then skip the PyPI lookup and hide a real dependency candidate. | Repository-wide module inference now recognizes root-level modules and the first component under `src/`/`lib/`; test modules are scoped to importers in the same test directory. Regression coverage proves both nested source and test files do not hide the application lookup. |
| High | Abdullah Latif | If `on_unknown = "pass"`, an unknown registry result counted as a pass but was omitted from the Markdown summary, which could read as verified. | The summary reports the count of all unknown results, lists up to ten names with status and policy decision, and states that each is unverified and is not evidence of absence. |
| High | Abdullah Latif | A large set of warning rows could push the unverified section past the 20,000-character limit. A passed unknown result could then be absent from the visible summary even though the JSON report retained it. | The unverified count and section now appear before package and warning lists. The summary caps name length and lists at ten rows, reports any omitted count, and retains the high-level marker if later output is truncated. A large-output regression test covers this case. |

No contract or schema changed. No runtime dependency was added.

### Recorded operational check

| Severity | Owner | Impact | Disposition |
| --- | --- | --- | --- |
| High | Repository administrator | If Actions settings send write tokens or secrets to fork pull-request workflows, PR-controlled workflow code may receive those credentials even though this workflow requests read-only contents. | The workflow code requests `contents: read` and references no secrets. A repository administrator must verify the fork settings before enabling the final demo workflow; this repository setting is not exposed by the connected GitHub review tools. |

This is an environment setting, not a code finding. GitHub's documented default for fork PR workflows is read-only token access with no secrets, but the repository policy must be checked rather than assumed.

## Verified controls

- `run_scan` uses the base-tip policy and the merge-base diff. Expired allow-list entries are visible as warnings; active allow-list matches retain their score and evidence.
- The registry client queries only the official PyPI JSON endpoint over HTTPS. Only a validated HTTP 404 means `not_found`; timeouts, connection failures, rate limits, malformed JSON and unexpected client errors remain unknown or malformed.
- Git reads are bounded, use no shell, skip symlinks and submodules, and report unsupported or unreadable input rather than executing it.
- The CLI bounds event input and summary size, escapes untrusted Markdown fields, sanitizes terminal diagnostics and writes outputs with collision-resistant delimiters.
- Unknown registry results are counted and shown near the top of the bounded summary, before long warning lists; the summary reports when additional unverified rows are omitted.
- Static workflow tests check that scan jobs always report, use full checkout history without persisted credentials, declare least-privilege permissions and use SHA-pinned third-party Actions.

## Verification

The regression tests were first run against the unfixed code and reproduced the original local-module
and unknown-status findings. Independent review then found the summary-truncation case; its regression
test confirms the unverified marker survives a truncated summary. After the fixes, repository checks
pass locally:

```text
make check
  Ruff lint: pass
  Ruff format: pass (60 files)
  pytest: 336 passed
  generated agent-file check: pass
```

The changed extractor, scan and CLI tests also pass independently (112 tests). The static workflow
tests run as part of the 336-test suite. A full GitHub Actions matrix on Python 3.11–3.14 is the
final remote check for the review branch.

## Remaining limits

SlopGuard does not resolve every dependency source or inspect transitive dependencies, artifacts,
signatures, known vulnerabilities or package behavior. Its popularity data can be stale, custom
Python import roots may be reported as dependencies, and registry state may change. Unknown results
remain explicitly unverified; selecting `on_unknown = "pass"` changes the policy decision but does
not assert registry existence. Optional secret scanning is not part of this ticket.
