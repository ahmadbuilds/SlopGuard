# Dependency result contract v1.0.0

`schemas/dependency-result.schema.json` defines one result per dependency requested by a source file. `examples/result.json` is a complete serialized example. Python producers can use `slopguard.contracts.DependencyResult`; serialized consumers must validate against the JSON Schema.

## Fields

| Field | Meaning |
| --- | --- |
| `contract_version` | Contract version, currently `1.0.0`. |
| `requested_name` | Exact name found in the input manifest or source. |
| `normalized_name` | PEP 503 canonicalized name: lowercase and each run of `.`, `_`, or `-` replaced with `-`. |
| `registry_exists` | `true` or `false` after a successful PyPI lookup; `null` if a timeout, rate limit, or other lookup failure prevents a conclusion. |
| `registry_status` | Optional explicit state: `exists`, `not_found`, `transient_error`, or `malformed_response`. This distinguishes an unknown lookup from a confirmed 404. |
| `registry_metadata` | Optional structured first/latest upload timestamps, release count, project URLs, maintainer hints, and source hints from PyPI JSON metadata. Missing fields remain `null` or empty. |
| `registry_diagnostics` | Optional request latency, attempt count, HTTP status, retry-after/rate-limit headers, and safe error detail for evaluation. |
| `metadata_evidence` | Sourced, human-readable facts. Each item includes an evidence kind, summary, source, and confidence from 0 to 1. |
| `similarity_candidates` | Candidate PyPI names with normalized similarity from 0 to 1 and optional existence state. |
| `risk_score` | Aggregated risk from 0 (lowest) to 100 (highest). |
| `severity` | `info`, `low`, `medium`, `high`, or `critical`. |
| `remediation` | Actionable explanation for a developer reviewing the finding. |
| `source_locations` | Optional source file, 1-based line, and source kind for PR annotations. Multiple locations are retained when the same normalized package appears more than once. |

## MVP scope and threat model assumptions

- The MVP inspects Python dependencies resolved through the public PyPI registry. It does not install or execute packages.
- The initial threat is dependency-name confusion, including typosquatting and suspiciously similar package names. Metadata signals are evidence, not proof of malicious intent.
- Registry requests are anonymous, read-only, and time bounded. No credentials belong in the package, configuration example, CI, or sample output.
- Network failures, rate limits, and incomplete metadata are represented as unknown evidence. They must not be converted into `registry_exists: false` or a malicious verdict.
- The default policy is `warn`. An opt-in `block` mode may block only when the result is sufficiently evidenced and the risk score reaches the configured threshold. The policy and thresholds here are provisional until confirmed by the team.

## Severity and enforcement

Score thresholds are policy configuration, not part of the JSON Schema. The example configuration uses `high >= 60` and `critical >= 85`; lower bands are `info < 20`, `low >= 20`, and `medium >= 40`. Teams may tune scoring without changing the data shape. Enforcement should consume severity, score, and evidence completeness together; an unknown registry result alone is never grounds to block.

## Compatibility

Adding an optional field is backward compatible. Renaming/removing a required field, changing its meaning or type, or changing score/severity semantics requires a contract version change and coordination with all producers and consumers before merge.
