# Architecture

## Overview and data flow

SlopGuard checks Python dependencies added by a pull request for names that do not exist on PyPI
and names that imitate popular packages. The pipeline extracts dependency additions from changed
files, looks up project metadata on PyPI, and passes structured evidence to later similarity,
scoring, and presentation stages.

The extractor and response parser are pure: they parse input text and JSON but never install,
import, or execute a package. The registry client reads PyPI project JSON only. Registry failures
remain unknown and do not become evidence that a project is absent or malicious. The repository
implements extraction, bounded Git reads, scan orchestration, bounded PyPI lookup, merging registry
evidence, offline look-alike detection, configuration and scoring. CLI presentation and the
published GitHub Action compose the scan as a CLI and composite action.

## Result contract

Contract version `1.0.0` is defined by
[`src/slopguard/schemas/dependency-result.schema.json`](src/slopguard/schemas/dependency-result.schema.json).
The package exposes the typed `DependencyResult` shape and `load_result_schema()` through
`slopguard.contracts`. Checked-in JSON contract examples are in `tests/fixtures/contract/`.
Serialized consumers should validate results against the JSON Schema.

The scan report is defined by
[`src/slopguard/schemas/scan-report.schema.json`](src/slopguard/schemas/scan-report.schema.json)
and `load_scan_report_schema()`. Its fixture is `tests/fixtures/contract/scan-report.json`; each
entry in `results` references the dependency-result schema.

### Fields

| Field | Meaning |
| --- | --- |
| `contract_version` | Contract version, currently `1.0.0`. |
| `requested_name` | Exact name found in the input manifest or source. |
| `normalized_name` | PEP 503 canonicalized name: lowercase and each run of `.`, `_`, or `-` replaced by `-`. |
| `registry_exists` | `true` or `false` after a successful PyPI lookup; `null` if a timeout, rate limit, or other lookup failure prevents a conclusion. |
| `registry_status` | Optional explicit state: `exists`, `not_found`, `transient_error`, or `malformed_response`. This distinguishes an unknown lookup from a confirmed 404. |
| `registry_metadata` | Optional structured first/latest upload timestamps, release count, project URLs, maintainer hints, and source hints from PyPI JSON metadata. Missing fields remain `null` or empty. |
| `registry_diagnostics` | Optional request latency, attempt count, HTTP status, retry-after/rate-limit headers, and safe error detail for evaluation. |
| `metadata_evidence` | Sourced, human-readable facts. Each item includes an evidence kind, summary, source, and confidence from 0 to 1. |
| `similarity_candidates` | Candidate PyPI names with normalized similarity from 0 to 1, optional existence state, edit distance, popularity rank, and match kind. |
| `score_contributions` | Optional additive points and explanation for each of the five scoring signals. |
| `decision` | Optional `pass`, `warn`, or `block` result from the policy stage. Missing means not evaluated. |
| `allowlisted` | Optional boolean indicating an active allow-list entry matched the package. |
| `risk_score` | Aggregated risk from 0 (lowest) to 100 (highest). |
| `severity` | `info`, `low`, `medium`, `high`, or `critical`. |
| `remediation` | Actionable explanation for a developer reviewing the finding. |
| `source_locations` | Optional source file, 1-based line, and source kind for PR annotations. Multiple locations are retained when the same normalized package appears more than once. |

### MVP scope and threat model assumptions

- The MVP inspects Python dependencies resolved through the public PyPI registry. It does not
  install or execute packages.
- The initial threat is dependency-name confusion, including typosquatting and suspiciously
  similar package names. Metadata signals are evidence, not proof of malicious intent.
- Registry requests are anonymous, read-only, and time bounded. No credentials belong in the
  package, configuration example, CI, or sample output.
- Network failures, rate limits, and incomplete metadata are represented as unknown evidence. They
  must not be converted into `registry_exists: false` or a malicious verdict.
- Registry lookup status and metadata diagnostics are supporting facts only; applying them never
  changes `risk_score` or `severity`.
- The library defaults to `warn`. `block` mode may block at the configured block threshold, subject
  to the non-configurable unknown-registry and import-only safeguards described below.

### Severity and enforcement

The score is the sum of five independently explained signals, clamped to the contract's 0–100
range and rounded to two decimal places: PyPI not-found, look-alike, recent first release, few
releases, and missing source links. Default weights are `60`, `35`, `25`, `10`, and `10` in that
order; the new-package window is 30 days, and the few-release limit is one release. Look-alike
points use default edit-distance factors 1.0 for distance 1 and 0.5 for distance 2; separator-only
matches use factor 1.0. The popularity multiplier is 1.0 through rank 100 and falls linearly to
0.3 at the configured similarity `top_n`. Missing signal evidence adds zero points with an
explanation; absence of evidence is never treated as safe credit.

Default severity lower bounds are low 20, medium 40, high 60, and critical 80; scores below low are
info. Default policy thresholds are warn 40 and block 80. The library defaults to `mode = "warn"`;
blocking requires `mode = "block"` and a score at or above the block threshold. Scores and
contributions are still computed for allow-listed names, but an active allow-list entry takes
precedence and sets `decision = "pass"`.
Expired allow-list entries are ignored for decisions and can be returned by configuration helpers
so a caller can report them as warnings.

Two policy invariants cannot be configured away: transient or malformed registry results never
block (and warn at minimum when `on_unknown = "warn"`), and import-only findings never block.
These controls prevent unavailable PyPI evidence or an import-only heuristic from causing a hard
failure. Scoring is evidence for review, not a malware verdict.

Configuration is loaded in this order: explicit path, repository `slopguard.toml`, the
`[tool.slopguard]` table in `pyproject.toml`, then built-in defaults. `parse_config` accepts TOML
text without file access. A PR scan reads policy from the target base SHA, not the proposed head
configuration, so a PR cannot loosen the policy used to evaluate itself.

### Compatibility

Adding an optional field is backward compatible. Renaming/removing a required field, changing its
meaning or type, or changing score/severity semantics requires a contract version change and
coordination with all producers and consumers before merge.

Similarity candidates may include `edit_distance` (non-negative integer), `popularity_rank`
(one-based integer), and `match_kind` (`edit` or `separator`). These optional fields explain how a
candidate was selected; they do not themselves change risk or severity.

## Dependency extraction

`slopguard.extractor.extract_dependencies` is a pure, offline extractor. Give it `ChangedFile`
values containing the file path, base text, and head text. It compares the two versions and emits
only added or replacement dependencies from the head version. Deletions and unchanged dependencies
are ignored. Callers can pass `local_modules` to exclude first-party module names;
`local_modules_from_paths` builds this set from repository paths and an optional project name.

Each normalized result conforms to the dependency-result schema. At extraction time,
`registry_exists` is `null`, evidence and similarity candidates are empty, and the score is a neutral
placeholder. Later registry and scoring stages must populate those fields before making policy
decisions. `source_locations` records the file, 1-based line, and source kind; duplicate package
names are emitted once with all their locations.

An extracted result is unscored: it has no `decision`. Consumers in SG-06 and SG-07 must treat a
result without `decision` as not evaluated, never as passed.

### Supported syntax

- Requirements and constraints files named with `requirement(s)` or `constraint(s)` and ending in
  `.txt` or `.in`, plus any `.txt` or `.in` file under a `requirements` directory. PEP 508 names,
  extras, version specifiers, markers, comments, inline comments, continued logical lines, and
  per-requirement `--hash` options are supported.
- PEP 621 `project.dependencies` and `project.optional-dependencies` arrays, PEP 735 arrays in
  `[dependency-groups]`, and `[build-system].requires` in `pyproject.toml`. Dependency-group
  `{include-group = "..."}` references are skipped because they are group references, not package
  names.
- Python `import` and absolute `from` statements found by parsing the complete base and head syntax
  trees. Imports inside strings, relative imports, `__future__`, standard-library imports, and
  known local modules are excluded. Known import names and dotted prefixes are mapped to
  distributions, including `cv2` to `opencv-python`, `PIL` to `Pillow`, `yaml` to `PyYAML`, and
  `google.protobuf` to `protobuf`. An unmapped ordinary import is emitted as an import-only finding;
  downstream scoring should treat import-only findings as warn-only. Unmapped `google`, `azure`,
  `zope`, and `backports` namespace roots produce a warning instead of a guessed package name.
- Changes in `setup.py`, `setup.cfg`, and `Pipfile` produce a manual-review warning; their
  dependency syntax is not interpreted, and `setup.py` imports are not treated as dependencies.

The import-name map is packaged data: `data/import_names.json` is generated manually from the ranked
popular-package snapshot by reading each project's latest PyPI release metadata and inspecting its
wheel ZIP directory through bounded HTTPS byte ranges. The generator prefers `py3-none-any` wheels,
then any wheel; it reads `top_level.txt` when present and otherwise derives names from wheel paths,
including extension modules and dotted package prefixes. It ignores metadata/data directories,
cache directories, invalid identifiers, and common test, documentation, example, benchmark, and
script directories. No wheel is downloaded whole, extracted, installed, imported, or executed.
For each module, the highest-ranked provider wins; its mapping is omitted only when a top-level
module's canonical distribution name equals its canonical module name. Remaining providers are
listed as conflicts. A directory is a shared namespace when at least two distributions contribute
it and the highest-ranked contributor lacks its `__init__.py`. Lower-ranked add-ons do not turn a
real package from a higher-ranked provider into a namespace. Namespace expansion runs globally only
below shared top-level roots; it skips shared paths and traverses namespace directories to the first
real package or module, up to its named depth limit. The generated header records the ignored
top-level directory names. Reviewed exceptions belong in
`data/import_name_overrides.json`; overrides win over generated entries. Run
`uv run python scripts/generate_import_names.py` when intentionally refreshing the map, review the
result and conflicts, and keep this generator out of CI and release workflows. Central directories
are capped at 8 MB and 100,000 entries; selected `top_level.txt` data is capped at 1 MB compressed
and 64 KB uncompressed. The map is heuristic: it cannot cover packages outside the popular-package
snapshot, source-only releases without wheels, or modules created during installation.
Standard-library names are a generated union across Python versions plus the current runtime list,
so imports removed in newer Python versions remain excluded.

Direct URLs, VCS/editable requirements, include directives, dynamic dependencies, Poetry dependency
tables, malformed requirements, and malformed TOML are skipped with a file/line warning when their
changed lines can be located. Global package index options receive a dedicated manual-review
warning; supported option-only flags that do not identify a package are ignored. Included
requirements files must be passed separately as changed files. A head Python syntax error produces
a warning; if only the base file cannot be parsed, its imports are treated as empty and a warning is
emitted. The extractor does not access the network, resolve markers, infer imports from unchanged
code, install packages, or execute project code.

## Pull request scan flow

`slopguard.scan.run_scan` accepts a `GitRepository`, repository identifier, base and head commits,
an injected registry client, and a fixed UTC time. It ensures both commits are available before
reading trees, then loads policy from the target base tip: an explicit configuration path wins,
followed by `slopguard.toml`, `[tool.slopguard]` in `pyproject.toml`, and built-in defaults. A
malformed base configuration stops the scan. `config_changed` compares the base-tip policy with
the effective head policy, but the head never controls the current decision. Dependency changes
are still computed from the merge base. The base-tip `project.name` and head tree paths identify
local imports.

`slopguard.git.GitRepository` invokes Git with argument lists, disables fsmonitor and terminal
prompts, and bounds each command by time and output. It reads raw NUL-delimited diffs, follows
rename paths to their base blobs, and skips deleted files, symlinks and submodules. Blobs are size
checked before reading and decoded as UTF-8; invalid or oversized files become report warnings.
Shallow clones first fetch missing base/head commits from `origin`, then can be deepened to find a
merge base up to the target base tip's `git.fetch_depth` limit. Invalid base configuration uses the
safe default fetch depth and is reported as a configuration error after Git history recovery.

Changed supported files pass through the pure extractor. Registry lookups use at most
`registry.max_concurrency` workers and `registry.max_lookups` requests per scan. Candidates beyond
the budget receive a transient unknown result, which can warn but cannot block. Similarity and
scoring use the base-tip configuration and packaged popularity snapshot. Results and warnings
are sorted deterministically. A report without a scored decision is an internal error; no findings
produce a `pass` report with zero counts.

To keep processing bounded, non-Python changed files are skipped with a warning when their combined
base and head content exceeds 2,000 lines or 1,000,000 characters. Python files use AST comparison
and are subject to the 1,000,000-character cap but not the line cap. The extractor stops and
returns one warning without partial candidates when the input exceeds 1,000 changed files or
20,000,000 combined base/head characters.

## Look-alike detection

`slopguard.similarity` compares canonical dependency names with the checked-in
[`popular_packages.json`](src/slopguard/data/popular_packages.json) snapshot. The snapshot is
derived from the top PyPI packages dataset at
`https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/top-pypi-packages.min.json`.
Refresh it manually with `uv run python scripts/update_popular_packages.py`; the script records the
source update time and retrieval time. Detection itself is offline and never fetches package data.

The matcher uses bounded optimal string alignment (Damerau-Levenshtein with adjacent transpositions
counting as one edit). It searches only candidates within the configured length band and uses the
pre-ranked popularity list to return deterministic matches ordered by edit distance, popularity
rank, and name. Names that differ only by hyphen separators are marked `separator`; other edits are
marked `edit`. Similarity is `1 - edit_distance / max(name lengths)`.

The popularity rule suppresses a dependency name if it is itself within the configured top `top_n`
snapshot entries. This prevents popular packages from flagging one another. `[similarity]` settings
in `examples/slopguard.toml` control `top_n`, `min_length`, `max_distance_by_length`, and
`max_matches`; if `top_n` exceeds the snapshot size, all available entries are used.

Detection is limited to names in the top `top_n` entries and to the configured edit distance. It
does not detect homoglyph substitutions or combosquatting. Snapshot freshness depends on manual
refreshes: `source_last_update` records the source dataset age and `retrieved_at` records when this
repository's snapshot was generated, so either can lag current PyPI popularity. The matcher reports
every match within the configured distance, including weak distance-2 matches to lower-ranked
packages; the scoring stage decides their weight.

## PyPI registry lookup

`slopguard.registry.PyPIClient` queries the public project JSON endpoint built by
`slopguard.pypi.project_json_url` for the normalized name. The
[PyPI JSON API documentation](https://docs.pypi.org/api/json/) describes this response as project
metadata and release information. SlopGuard reads JSON only; it never fetches distribution URLs,
installs a package, imports it, or executes candidate code.

### Returned metadata

The response parser extracts:

- `first_release_at` and `latest_release_at`: earliest and latest valid upload timestamps found
  across release files. These dates are an age proxy, not proof that a project was created or
  maintained on those exact dates.
- `release_count`: number of release versions with at least one uploaded file. Empty release
  entries are excluded.
- `project_urls`: valid HTTP(S) links from PyPI's `info.project_urls`.
- `maintainer_hints`: the public maintainer label and owner/maintainer names exposed by PyPI, when
  available.
- `source_hints`: project URLs labeled as source/repository/code, plus common source-host links.

Publisher-controlled text is bounded while parsing: at most 20 maintainer hints of 200 characters
each, 50 project URLs with labels up to 100 characters and URLs up to 256 characters, and 20
source hints are retained. Unicode control (`Cc`) and format/invisible (`Cf`) characters are
removed. Overlong URLs are dropped; retained text is not HTML- or Markdown-escaped by this layer.

PyPI currently documents the project `releases` object as deprecated and recommends the Index API
when release listing is needed. If the key is absent, SlopGuard reports release count and upload
dates as unknown while preserving the successful `exists` result. PyPI's JSON API documentation
also notes that metadata comes from values supplied at upload and may not match the uploaded files'
contents.

### Lookup states

| State | Meaning | `registry_exists` |
| --- | --- | --- |
| `exists` | HTTP 200 with valid project metadata and a matching project name. | `true` |
| `not_found` | PyPI returned HTTP 404. | `false` |
| `transient_error` | Timeout, transport failure, rate limit, or other non-success HTTP response. | `null` |
| `malformed_response` | Invalid JSON or response structure, unsupported content encoding, a mismatched project name, truncated gzip, or a response exceeding a size cap. | `null` |
| `invalid_name` | The requested name fails `canonicalize_name(..., validate=True)`; no request is sent. | `null` |

Unknown states do not add not-found evidence and do not alter the candidate's risk score or
severity. A confirmed 404 is recorded as a sourced fact; it is not, by itself, proof of malicious
intent.

### Request bounds and diagnostics

Each `PyPIClient` has a per-attempt total request deadline, a 3-second timeout, one retry by
default, and exponential backoff from 100 ms capped at 500 ms. These policy values are configurable
in the `[registry]` table; hard ceilings of 10 seconds, two retries, and one second of backoff
cannot be raised by config. HTTP 408, 429, 5xx responses, and transport exceptions are retried
within those bounds. A numeric or HTTP-date `Retry-After` value is also capped by the configured
maximum delay.

The request asks PyPI for gzip. Response transfer bytes are capped at 8 MB (compressed when gzip),
and decoded documents at 32 MB. Gzip is decoded incrementally with an output limit; identity
responses are also checked against the document cap. Oversized, truncated, or unsupported
responses remain unknown (`malformed_response`), never `not_found`.

Each client caches results by normalized name behind a lock. Concurrent lookups for the same name
share one in-flight request, and callers receive independent copies of the result. Transport
exceptions are reported by exception class only so exception text cannot echo untrusted details.

`registry_diagnostics` records total latency including retries and backoff, attempt count, final
HTTP status, available `Retry-After`, `RateLimit-Remaining`, and `X-RateLimit-Remaining` headers,
and a bounded error/recovery note. The implementation uses an injectable HTTP transport so tests
can cover all states without network access. The complete example is
[`registry-result.json`](tests/fixtures/contract/registry-result.json).

### Limitations

The client connects directly and ignores `HTTP_PROXY` and `HTTPS_PROXY`. On a runner that requires
a proxy, every lookup becomes `transient_error` (`unknown`); it is never treated as `not_found` and
never blocks a merge.

## Security model

Dependency manifests, changed Python source, and PyPI responses are treated as untrusted data.
SlopGuard parses them but never executes project code or retrieved packages. Registry access is
read-only and bounded by time, retries, and response size. Tests use checked-in fixtures and
controlled transports; they do not call PyPI. See [SECURITY.md](SECURITY.md) for the threat scope,
limitations, and private vulnerability-reporting instructions.
