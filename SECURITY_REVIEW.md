# VOI-118 security review

Reviewed 27 November 2026 against `main` at `091f487` and the changes on
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

## Detailed checklist evidence

| Item | Result | Evidence |
| --- | --- | --- |
| 1. No candidate execution | Verified. | `src/slopguard/git.py` invokes Git with argument arrays; `src/slopguard/extractor.py` parses source with `ast`, TOML with `tomllib`, and requirements with `packaging`. `tests/unit/test_workflows.py::test_action_ignores_uv_configuration` confirms the Action installs only its locked SlopGuard project; extractor and scan regression tests exercise dependency candidates as data. |
| 2. Action surface | Verified. | `action.yml` passes inputs through `env`, invokes `uv run --no-config --project "$GITHUB_ACTION_PATH" --locked --no-dev`, and pins `astral-sh/setup-uv` to a commit SHA. `tests/unit/test_workflows.py::test_action_and_workflows_have_no_expressions_inside_run_blocks`, `test_workflows_declare_least_privilege_permissions_and_pin_actions`, and `test_action_ignores_uv_configuration` cover the boundaries. |
| 3. Triggers and forks | Workflow-level controls verified; repository setting not verified. | `.github/workflows/slopguard.yml` and `examples/workflow.yml` use `pull_request`; `tests/unit/test_workflows.py::test_slopguard_workflows_are_fork_safe_and_read_only` asserts read-only `contents`, no secrets, and no `pull_request_target`. GitHub returned 404 for repository settings, and no dedicated fork PR was run. SG-07 fork-comment behavior remains a dependency check. |
| 4. Policy integrity | Verified. | `src/slopguard/scan.py::_effective_config` loads policy from the base SHA. `tests/integration/test_scan.py::test_scan_uses_base_tip_policy_and_reports_head_config_change` and `test_policy_tightened_after_fork_applies_to_open_pull_request` cover base-tip policy; `test_expired_allowlist_entries_are_reported_as_warnings`, `tests/unit/test_scoring.py::test_invalid_name_with_high_score_never_blocks`, and `test_unknown_registry_status_never_blocks_high_similarity_score` cover expiry and safe decision rules. |
| 5. Bounds on untrusted input | Verified in code and tests. | `src/slopguard/cli.py` caps event payloads at 1 MB and rejects config paths outside the checkout; `src/slopguard/extractor.py` caps files at 2,000 lines/1 MB, changed files at 1,000, and aggregate text at 20 MB; `registry.max_lookups` caps requests (500 by default). `src/slopguard/registry.py` bounds transfer/document sizes at 8/32 MB and enforces request deadlines/retry ceilings. Tests include `test_oversized_blob_is_skipped_before_content_is_read`, `test_lookup_budget_marks_excess_candidates_unknown`, response-size/deadline/retry cases in `tests/integration/test_registry.py`, and invisible-Unicode cases in `tests/unit/test_pypi.py`. `src/slopguard/git.py` reads Git blobs, skips symlinks/submodules, and bounds Git operations. |
| 6. Output safety | Summary and output-file safety verified; SG-07 annotations/comments deferred. | `src/slopguard/cli.py::_markdown` escapes untrusted Markdown; `_write_outputs` uses a random `secrets.token_hex` delimiter for `GITHUB_OUTPUT`. `tests/integration/test_scan_cli.py` covers unverified names, summary truncation and output delimiters; `tests/integration/test_scan.py::test_active_allowlist_skips_registry_lookup_and_records_exception` covers skipped lookups. Annotation workflow-command escaping and PR comment contents are not implemented in this branch; review them with SG-07 before that feature merges. |
| 7. Network and privacy | Verified after the allow-list fix. | `src/slopguard/pypi.py` constructs the fixed HTTPS PyPI URL; `src/slopguard/registry.py` rejects any non-PyPI host. `tests/integration/test_registry.py::test_invalid_names_do_not_call_the_transport` covers invalid names. Active allow-list candidates are filtered before `_lookup_candidates` in `src/slopguard/scan.py`; `tests/integration/test_scan.py::test_active_allowlist_skips_registry_lookup_and_records_exception` proves the fake client receives no name and the report records the exception and expiry. Expired entries still query PyPI. |
| 8. Fail-closed behavior | Verified in code and tests. | `src/slopguard/cli.py::ExitCode.INTERNAL_ERROR` returns exit code 3 for internal failures; `tests/integration/test_scan.py::test_result_without_scored_decision_is_an_internal_error` covers a missing scoring decision. `tests/unit/test_scoring.py::test_unknown_registry_floor_follows_on_unknown_setting` and `tests/integration/test_scan_cli.py::test_summary_marks_passed_unknown_registry_results_as_unverified` cover unknown states; the summary preserves block findings when `--no-fail-on-block` is set. |
| 9. Write-up | Updated. | `ARCHITECTURE.md` records trust boundaries, data flow, network/privacy behavior, limits, resolved review findings, and the repository-setting check. `SECURITY.md`, `README.md`, this review record, and the Linear VOI-118 comment carry the handoff. |

Findings discovered during this review are recorded under VOI-118 with severity, owner, impact and
disposition. No separate follow-up ticket was created for repository settings because the repository
administrator cannot be identified from the available account permissions.

## Findings

### Resolved findings

| Severity | Owner | Impact | Resolution |
| --- | --- | --- | --- |
| High | Abdullah Latif | A repository containing `src/unrelated/requests.py` caused `local_modules_from_paths` to treat `requests` as a top-level local module. A sibling helper such as `tests/requests.py` could create the same suppression globally. A changed application `import requests` could then skip the PyPI lookup and hide a real dependency candidate. | Repository-wide module inference recognizes root-level modules and the first component under `src/`/`lib/`; nested modules are scoped to the importing file's directory. Regression coverage confirms sibling helpers stay local and nested source or test files do not hide an application import. |
| High | Abdullah Latif | If `on_unknown = "pass"`, an unknown registry result counted as a pass but was omitted from the Markdown summary, which could read as verified. | The summary reports the count of all unknown results, lists up to ten names with status and policy decision, and states that each is unverified and is not evidence of absence. |
| High | Abdullah Latif | A large set of warning rows could push the unverified section past the 20,000-character limit. A passed unknown result could then be absent from the visible summary even though the JSON report retained it. | The unverified count and section now appear before package and warning lists. The summary caps name length and lists at ten rows, reports any omitted count, and retains the high-level marker if later output is truncated. A large-output regression test covers this case. |
| Medium | Abdullah Latif | Sibling helpers in `scripts/`, `tools/`, or `examples/` were treated as PyPI candidates, sending local names to the public registry and producing false positives. | Local modules are scoped to the importing file's directory across the repository, in both production scans and the historical PR collector. Root, `src/`, and `lib/` imports remain globally recognized; nested source modules cannot hide unrelated imports. Regression tests cover the reported sibling cases and preserve the nested-module bypass fix. |
| Medium | Abdullah Latif | Active allow-list package names were sent to PyPI before scoring, disclosing internal names and consuming the lookup budget. | Active entries now bypass the registry client and budget, pass with `allowlisted: true`, and carry local evidence naming the entry, reason, and expiry. Expired entries still receive normal lookups and warnings. |

No contract or schema changed. No runtime dependency was added.

### Recorded operational check

| Severity | Owner | Impact | Disposition |
| --- | --- | --- | --- |
| High | Repository administrator | If Actions settings send write tokens or secrets to fork pull-request workflows, PR-controlled workflow code may receive those credentials even though this workflow requests read-only contents. | The workflow code requests `contents: read` and references no secrets. A repository administrator must verify the fork settings before enabling the final demo workflow; this repository setting is not exposed by the connected GitHub review tools. |

This is an environment setting, not a code finding. GitHub's documented default for fork PR workflows is read-only token access with no secrets, but the repository policy must be checked rather than assumed.

## Verified controls

- `run_scan` uses the base-tip policy and the merge-base diff. Active allow-list entries are passed without a PyPI request and retain their score with evidence for the exception name, reason, and expiry; expired entries are looked up and visible as warnings.
- The registry client queries only the official PyPI JSON endpoint over HTTPS for candidates that are not actively allow-listed. Only a validated HTTP 404 means `not_found`; timeouts, connection failures, rate limits, malformed JSON and unexpected client errors remain unknown or malformed.
- Git reads are bounded, use no shell, skip symlinks and submodules, and report unsupported or unreadable input rather than executing it.
- The CLI bounds event input and summary size, escapes untrusted Markdown fields, sanitizes terminal diagnostics and writes outputs with collision-resistant delimiters.
- Unknown registry results are counted and shown near the top of the bounded summary, before long warning lists; the summary reports when additional unverified rows are omitted.
- Static workflow tests check that scan jobs always report, use full checkout history without persisted credentials, declare least-privilege permissions and use SHA-pinned third-party Actions.

## Verification

The regression tests were first run against the unfixed code and reproduced the original local-module
and unknown-status findings. Independent review then found the summary-truncation case; its regression
test confirms the unverified marker survives a truncated summary. After integrating current `main`
and applying Ahmad's two requested corrections, repository checks pass locally:

```text
make check
  Ruff lint: pass
  Ruff format: pass (86 files)
  pytest: 400 passed
  generated agent-file check: pass
make eval
  deterministic evaluation outputs: pass (exit 0)
```

The changed extractor, scan and CLI tests also pass independently (122 tests); the historical
collector regressions add two more cases in the 400-test suite. The static workflow tests run as
part of that suite. The new GitHub Actions run for this revision remains to be confirmed after
pushing.

## Remaining limits

SlopGuard does not resolve every dependency source or inspect transitive dependencies, artifacts,
signatures, known vulnerabilities or package behavior. Its popularity data can be stale, custom
Python import roots may be reported as dependencies, and registry state may change. Unknown results
remain explicitly unverified; selecting `on_unknown = "pass"` changes the policy decision but does
not assert registry existence. Optional secret scanning is not part of this ticket.
