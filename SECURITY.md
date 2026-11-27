# Security policy

SlopGuard helps reviewers identify newly added Python dependency names that are missing from PyPI
or resemble popular packages. It reads changed-file text and public PyPI JSON metadata. It never
installs or executes a dependency under review, downloads its package archive, or runs its setup
script. The GitHub Action installs SlopGuard from its own locked project so it can run the scanner;
it does not install dependencies from the scanned checkout.

## Threat model and controls

The scanner treats pull-request paths, file contents, manifest entries, project metadata and PyPI
responses as untrusted data. It bounds Git output and object sizes, parses supported dependency
syntax without running it, and uses argument arrays for Git commands. Git subprocesses have time
and output limits and do not use a shell. Registry requests use HTTPS to the fixed `pypi.org`
host, certificate verification, bounded retries and a total deadline; response and decompressed
document sizes are capped. No PyPI response can cause a package archive, import, or setup script to
run.

For a pull-request scan, the policy is loaded from the base branch tip, so a pull request cannot
weaken the policy that evaluates itself. The scan workflows trigger on `pull_request`, declare only
`contents: read`, pin third-party Actions to commit SHAs and set checkout credentials to
`persist-credentials: false`. The CLI rejects `pull_request_target`. Its Action inputs reach shell
steps through environment variables and argument arrays. Markdown output escapes package names,
paths and diagnostics; GitHub outputs use random multiline delimiters.

## Data and network use

For each candidate, SlopGuard sends the canonicalized package name in a request path to the public
PyPI JSON API. The User-Agent identifies the SlopGuard version and repository. Source files,
filenames, repository contents, pull-request titles and bodies are not uploaded by SlopGuard.
Registry metadata is parsed locally and included in the local JSON report as bounded hints. The
GitHub Action writes its report and summary to the paths configured by the workflow. With the
documented full-history checkout, SlopGuard needs no GitHub fetch; its bounded fallback fetches
missing commit objects from the checkout's configured origin. No automated PR comment or write
operation is performed.

Fork pull requests normally receive a read-only `GITHUB_TOKEN` and no repository secrets, but
repository-level Actions settings can override fork behavior. Keep settings that send write tokens
or secrets to fork workflows disabled. See [GitHub's fork workflow policy](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflows-in-forked-repositories).

## Limits

- Registry metadata is supplied by package publishers and is not proof that a package is safe.
- The scanner analyzes supported dependency declarations and Python imports in the pull-request
  diff. Unsupported dependency formats produce a warning; transitive dependency resolution,
  artifact inspection, vulnerability analysis, signatures and publisher provenance are outside its
  scope.
- The popularity detector uses a checked-in top-5,000 package snapshot that can become stale. It
  can produce false positives and false negatives and does not detect homoglyph or combosquatting
  attacks.
- Repository-wide local-module recognition covers root-level modules and `src/` and `lib/` package
  roots. Test helper modules are considered local only to importers in the same test directory, so
  a helper such as `tests/requests.py` cannot mask an application import. Projects with custom
  runtime import paths can receive a false positive until those roots are explicitly supported;
  SlopGuard errs toward reporting the candidate instead of suppressing it as local.
- Timeouts, rate limits, connection errors and malformed responses are unknown. They are never
  treated as proof that a package is absent. The configured `on_unknown` policy can choose `pass`,
  but the report still marks the registry result unverified and the summary calls it out.
- Secret scanning is not implemented. It is a separate optional feature and is not enabled by this
  Action.
- PyPI state can change between runs, so results are deterministic for fixed inputs except for the
  live registry response.

The review record, including findings and verification evidence, is in
[SECURITY_REVIEW.md](SECURITY_REVIEW.md). SlopGuard is a review aid, not a security guarantee.

## Reporting a vulnerability

To report a vulnerability privately, use GitHub Security Advisories for this repository. Do not
open a public issue containing exploit details before maintainers have had a chance to respond.
