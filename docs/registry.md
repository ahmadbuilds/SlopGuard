# PyPI registry lookup

`slopguard.registry.PyPIClient` queries the public project JSON endpoint at `https://pypi.org/pypi/<normalized-name>/json`. The [PyPI JSON API documentation](https://docs.pypi.org/api/json/) describes this response as project metadata and release information. SlopGuard reads JSON only; it never fetches distribution URLs, installs a package, imports it, or executes candidate code.

## Returned metadata

The response parser extracts:

- `first_release_at` and `latest_release_at`: earliest and latest valid upload timestamps found across release files. These dates are an age proxy, not proof that a project was created or maintained on those exact dates.
- `release_count`: number of release versions with at least one uploaded file. Empty release entries are excluded.
- `project_urls`: valid HTTP(S) links from PyPI's `info.project_urls`.
- `maintainer_hints`: the public maintainer label and owner/maintainer names exposed by PyPI, when available.
- `source_hints`: project URLs labeled as source/repository/code, plus common source-host links.

PyPI currently documents the project `releases` object as deprecated and recommends the Index API when release listing is needed. If the key is absent, SlopGuard reports release count and upload dates as unknown while preserving the successful `exists` result. [PyPI's JSON API docs](https://docs.pypi.org/api/json/) also note that metadata comes from values supplied at upload and may not match the uploaded files' contents.

## Lookup states

| State | Meaning | `registry_exists` |
| --- | --- | --- |
| `exists` | HTTP 200 with valid project metadata and a matching project name. | `true` |
| `not_found` | PyPI returned HTTP 404. | `false` |
| `transient_error` | Timeout, transport failure, rate limit, or other non-success HTTP response. | `null` |
| `malformed_response` | Invalid JSON or response structure, a mismatched project name, or a response exceeding the size cap. | `null` |

Unknown states do not add not-found evidence and do not alter the candidate's risk score or severity. A confirmed 404 is recorded as a sourced fact; it is not, by itself, proof of malicious intent.

## Request bounds and diagnostics

Each `PyPIClient` has a 3-second request timeout, a 2 MB response cap, one retry by default, and exponential backoff from 100 ms capped at 500 ms. HTTP 429 and 5xx responses are retried; a numeric or HTTP-date `Retry-After` value is honored only up to the same cap. All results, including failures, are cached by normalized name for that client instance, which is the per-run cache boundary.

`registry_diagnostics` records total latency including retries and backoff, attempt count, final HTTP status, available `Retry-After`, `RateLimit-Remaining`, and `X-RateLimit-Remaining` headers, and a bounded error/recovery note. The implementation uses an injectable HTTP transport so tests can cover all states without network access.

The complete example is [registry-result.json](../examples/registry-result.json). Tests use the checked-in PyPI response fixture and controlled HTTP responses; they do not call PyPI.
