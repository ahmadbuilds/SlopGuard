# Security policy

SlopGuard helps reviewers identify newly added Python dependency names that do not exist on PyPI or resemble popular packages. It is a review aid; it does not establish that a package is safe.

## Supported versions

| Version | Support |
| --- | --- |
| 1.x | Security reports accepted and fixes provided. |
| Earlier than 1.0 | Not supported. Upgrade to a current 1.x release before reporting a version-specific issue. |

## Report a vulnerability privately

Use [GitHub's private vulnerability reporting form](https://github.com/ahmadbuilds/SlopGuard/security/advisories/new). Include the affected release or commit, the workflow event, a concise impact description, and steps to reproduce. Remove tokens and other secrets from logs before sharing them.

We aim to acknowledge a report within seven days and aim to release a fix within 90 days. We disclose fixed issues through a GitHub Security Advisory and credit the reporter unless they prefer not to be named.

## Scope

Report vulnerabilities that could cause code execution, token exposure, a policy bypass, unsafe handling of untrusted input, or injection into a GitHub summary, annotation, or pull-request comment.

A missed look-alike or a false positive is a detection-quality issue, not a security vulnerability. Use the [false-negative form](https://github.com/ahmadbuilds/SlopGuard/issues/new?template=false_negative.yml) or [false-positive form](https://github.com/ahmadbuilds/SlopGuard/issues/new?template=false_positive.yml).

## Security model in brief

- SlopGuard parses changed manifests and Python source as data. It never installs or executes candidate packages, project code, or package archives.
- The scan reads policy from the base branch so a pull request cannot weaken its own evaluation.
- The scanner sends normalized names to the public PyPI JSON API; it does not upload repository source or pull-request text.
- Network failures and malformed responses remain unverified. They are not treated as proof that a package does not exist.
- The action uses `pull_request`, read-only contents by default, SHA-pinned third-party actions, and checkout with persisted credentials disabled.
- Sticky comments are opt-in, require `pull-requests: write`, and are skipped on fork pull requests.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete security model and trust boundaries.

## Data and network use

| Destination | When it is contacted | Data sent |
| --- | --- | --- |
| Public PyPI JSON API | For each valid, non-allow-listed candidate | Normalized package name in the request path and a versioned repository User-Agent. No source, filenames, or pull-request text. |
| GitHub API or GitHub Enterprise Server API | Only when sticky comments are enabled | The action's GitHub token and rendered comment contents. The action uses the runner's `GITHUB_API_URL`. |
| Configured Git remote | Only when the checkout lacks commit objects and the bounded fallback is needed | Git fetch requests for the missing history. Full-history checkout avoids this fallback. |

The action writes its JSON report and summary to the runner workspace or the paths selected by the workflow. Registry metadata and generated reports stay on the runner unless the workflow uploads them or comments are enabled.

## Repository protections

The checked-in workflows use SHA-pinned third-party actions and least-privilege permissions. Dependabot checks dependencies, and OpenSSF Scorecard runs periodically. No CodeQL analysis workflow is checked in; the Scorecard SARIF upload does not run CodeQL. The action's workflows do not require repository secrets for scanning. The repository's Actions fork settings are recorded in the [security review](SECURITY_REVIEW.md).

Use the private reporting form above to disclose a vulnerability; do not post it in a public issue.

## Security review

See [SECURITY_REVIEW.md](SECURITY_REVIEW.md) for the assessment, findings, verification evidence, and residual risks.
