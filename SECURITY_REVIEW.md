# Security review

| Review detail | Value |
| --- | --- |
| Reviewed | 2026-11-28 |
| Review point | Main after release 1.0.0 |
| Source commit | [4439fc16d19cd473e8c12eff532e9067573a1801](https://github.com/ahmadbuilds/SlopGuard/commit/4439fc16d19cd473e8c12eff532e9067573a1801) |
| Reviewers | Abdullah Latif; Muhammad Ahmad (repository configuration, verified 2026-11-28) |

## Scope and method

The review covers dependency extraction, Git reads, registry requests, scan policy, report rendering, optional pull-request comments, action inputs, workflows, and repository Actions settings. The assessment used code review, regression and workflow tests, and the repository settings screenshots reviewed on the date above.

## Summary

SlopGuard treats changed repository content and PyPI responses as untrusted data. The scan uses the target base commit's policy, performs bounded registry requests, and does not execute candidate packages. The review findings below were fixed in [the merged security-hardening pull request](https://github.com/ahmadbuilds/SlopGuard/pull/14); regression tests cover the failure cases.

## Findings

| ID | Finding | Severity | Status | Fix |
| --- | --- | --- | --- | --- |
| SR-1 | A nested source module could mask an unrelated application import and suppress a PyPI lookup. | High | Fixed | [Pull request](https://github.com/ahmadbuilds/SlopGuard/pull/14) |
| SR-2 | A `pass` policy could make an unknown registry result appear verified in the summary. | High | Fixed | [Pull request](https://github.com/ahmadbuilds/SlopGuard/pull/14) |
| SR-3 | Long output could truncate the unverified section and hide unknown results from the visible summary. | High | Fixed | [Pull request](https://github.com/ahmadbuilds/SlopGuard/pull/14) |
| SR-4 | Sibling helper modules could be sent to PyPI and reported as external candidates. | Medium | Fixed | [Pull request](https://github.com/ahmadbuilds/SlopGuard/pull/14) |
| SR-5 | An active allow-list entry could disclose an internal package name to PyPI and use lookup capacity. | Medium | Fixed | [Pull request](https://github.com/ahmadbuilds/SlopGuard/pull/14) |

## Controls verified

| Control | Evidence |
| --- | --- |
| Candidate code is not executed | The extractor parses Python with AST, TOML with tomllib, and requirements with packaging. The action installs its own locked project; it does not install scanned project dependencies. |
| Pull-request policy integrity | scan.py reads policy from the base commit. Integration tests cover base-policy evaluation when the proposed configuration changes. |
| Bounded registry access | The registry client uses HTTPS to the fixed PyPI host, request deadlines, bounded retries and response sizes, and an injectable transport. |
| Safe result presentation | render.py bounds and escapes summary, annotation, and comment fields. Workflow tests assert minimal permissions and SHA-pinned external actions. |
| Allow-list privacy | Active allow-list entries bypass registry lookup and are recorded with their reason and expiry. A regression test asserts the client receives no allow-listed name. |

## Repository configuration

**SlopGuard repository, verified 2026-11-28.** Settings screenshots were reviewed by the maintainer.

| Setting | Verified value |
| --- | --- |
| Default workflow token | Read access to repository contents and packages. |
| Actions may create or approve pull requests | Off. |
| Fork workflow approval | Required for first-time contributors. |
| Fork-workflow secrets and write tokens | Not available for this public repository; GitHub does not send secrets or write tokens to fork workflows. |
| Allowed Actions | The Actions policy requires full-SHA references and restricts Actions to an allow-list. Third-party workflow and composite-action references are pinned to full SHAs. |
| CodeQL | Repository-level default setup analyzes Python and GitHub Actions. The Scorecard SARIF uploader is separate from CodeQL. |
| Secret scanning | Secret scanning and push protection are enabled. |
| Private vulnerability reporting | Enabled through GitHub's private reporting form. |
| Dependabot | Alerts and updates are enabled. |
| Rulesets | Active rulesets protect `main` and `v*.*.*` release tags and require status checks. |

The separate example repository is not included in this assessment; its settings must be reviewed independently before relying on its workflow results.

## Residual risks and limitations

PyPI metadata is publisher supplied and does not prove that a project is safe. The popularity snapshot can become stale. The extractor supports documented dependency formats and does not resolve transitive dependencies, inspect package artifacts, verify publisher identity, or detect every look-alike pattern. False positives and false negatives remain possible. Unknown registry results stay unverified and do not establish absence.

## Next review

Review the controls at each minor release and whenever Action permissions or the result contract change.
