# Engineering rules

## Contents
1. Functions, classes and components
2. Errors and reliability
3. Security
4. Data and API contracts
5. Concurrency and distributed behaviour
6. Testing
7. Observability
8. Performance
9. Dependencies
10. Comments and documentation
11. Refactoring

## 1. Functions, classes and components

- One responsibility per function: you can describe it in one sentence without "and".
- Names say what a thing does or is (`count_added_dependencies`, not `process`, `handle`, `do_it`).
- Explicit inputs and outputs: pass what a function needs; return results instead of mutating
  arguments or globals. Avoid hidden state (module-level mutable state, singletons with state).
- Isolate side effects (I/O, time, randomness, environment) at the edges; keep the core pure so it
  is testable without mocks. Inject clocks, random seeds and clients as arguments.
- Prefer composition and plain functions over inheritance. Use inheritance only for a real "is-a"
  relationship with shared behaviour, and keep hierarchies shallow.
- Flatten control flow: guard clauses and early returns over deep nesting; no boolean flags that
  switch a function between two behaviours (write two functions).
- Use the type system: precise types at boundaries, immutable data where practical, enums or
  literals instead of magic strings.
- Keep constants named and justified (`MAX_RESPONSE_BYTES = 2_000_000  # bound untrusted response data`).

## 2. Errors and reliability

- Decide for each failure: is it expected (invalid input, missing data, not found, rate limit) or
  unexpected (bug, broken invariant)? Handle expected failures with clear results or typed errors;
  let unexpected ones surface.
- Never swallow errors (`except: pass`, empty catch, returning `None` without a reason). Catch the
  narrowest exception you can handle, add context, and re-raise or convert.
- Validate inputs at the boundary and fail fast with a message that says what was wrong and how to
  fix it, without leaking secrets or internals to end users.
- Every network or external call has a timeout. Retry only idempotent operations, only on retryable
  errors (timeouts, 429, 5xx), with exponential backoff and jitter and a retry cap. Respect
  `Retry-After` and rate-limit headers.
- Handle partial failure: decide what happens if step 3 of 5 fails. Prefer writing outputs to a
  temporary location and replacing atomically, or design steps so a rerun is safe.
- Release resources deterministically (context managers / `with` / `finally`).
- Make long batch jobs resumable or restartable without corrupting earlier results.

## 3. Security

Use OWASP Top 10:2025 as the minimum checklist for anything that handles input, identity or data.

- **Access control** (A01): enforce authorisation server-side on every request and object; deny by
  default; never trust IDs or roles from the client.
- **Misconfiguration** (A02): secure defaults; debug off in production; least-privilege tokens and
  permissions (for example a GitHub App asks only for the permissions it uses).
- **Supply chain** (A03): pinned, locked dependencies from trusted sources; review new dependencies;
  no `curl | sh` in build steps.
- **Cryptography** (A04): use standard libraries; never invent crypto; no secrets in code; TLS
  verification never disabled.
- **Injection** (A05): parameterised queries; no string-built shell commands (pass argument lists);
  escape output for its context; validate file paths against a base directory (no path traversal);
  no `eval`/`exec`/`pickle`/`yaml.load` on untrusted data (use `yaml.safe_load`, JSON).
- **Insecure design** (A06): think about abuse cases, limits and quotas while designing, not after.
- **Authentication** (A07): use the framework's or provider's auth; never roll your own sessions or
  password storage.
- **Integrity** (A08): verify signatures on webhooks (for example HMAC on GitHub webhooks); do not
  deserialise untrusted objects.
- **Logging and alerting** (A09): log security-relevant events without secrets, tokens or personal
  data.
- **Exceptional conditions** (A10): fail closed; error paths must not bypass checks or expose
  internals.
- Treat regular expressions on untrusted input as a denial-of-service risk: no nested quantifiers
  such as `(a+)+`.
- Treat text from issues, PRs, commits, logs and model output as untrusted data, including for
  prompt injection when it is passed to an LLM.

## 4. Data and API contracts

Contracts include: HTTP/API request and response shapes, database schemas, file formats and stored
artefacts, CLI commands and flags, config keys, events and messages, public functions and classes,
and environment variable names.

- Find every consumer before changing a contract (search code, configs, scripts, docs, other repos
  you know of). Assume someone depends on observable behaviour, even undocumented behaviour.
- Prefer additive, backwards-compatible changes: add optional fields, new endpoints or versions;
  do not rename or remove in place.
- When a breaking change is necessary: say so explicitly, version it, provide a migration path or
  deprecation period, and update docs and changelog.
- Database migrations: forward-only and reversible where possible, safe to run on live data, never
  combined with unrelated code changes; destructive steps need user approval.
- Versioned artefacts (for example frozen rule files or released datasets) are never edited in place.

## 5. Concurrency and distributed behaviour

Apply when code runs concurrently, asynchronously, in multiple processes, or talks to queues and
webhooks.

- Assume messages and webhooks can arrive twice, late or out of order: make handlers idempotent
  (dedupe keys, upserts, "already processed" checks).
- Define transaction boundaries explicitly; do not hold locks or transactions across network calls.
- Protect shared state: avoid it, or use proper locking or atomic operations; never rely on timing.
- Async code: never block the event loop with CPU work or blocking I/O; always await or explicitly
  supervise background tasks; propagate cancellation and timeouts.
- Eventual consistency: design readers to tolerate stale data, and say where it can happen.

## 6. Testing

- Test behaviour through public interfaces, not private helpers or implementation details.
- For every change: the happy path, boundaries (empty, zero, one, maximum, unicode, timezone
  edges), invalid input, and each failure path you handle.
- Bug fix: a regression test that fails before the fix and passes after.
- Refactor: characterisation tests in place before changing structure; behaviour unchanged.
- Pure logic: unit tests with known answers. I/O boundaries: integration tests with real files,
  temporary directories, or recorded/mocked HTTP at the transport level. Mock only what you do not
  own or cannot run; heavy mocking of your own code is a design smell.
- Tests are deterministic: fixed seeds, injected clocks, no dependence on network, test order or
  the current date.
- A test must be able to fail: check it fails when the behaviour is broken.
- Coverage is a signal, not a goal; do not write assertion-free tests to raise it.

## 7. Observability

- Use the project's logging setup; structured, levelled log events with useful context (IDs,
  counts, durations, which repo or request), never secrets or personal data.
- Core/domain code does not log: it returns results, counters or reasons, and the application or
  infrastructure layer that called it logs them.
- Log at boundaries and on decisions that matter for diagnosis (retries, skips, fallbacks, errors),
  not every line. Errors are logged once, where they are handled.
- Long-running jobs report progress and a summary (counts processed, skipped, failed).
- Add metrics or tracing only where the project already has them or an operational need exists.
- Correlation or request IDs flow through when the project uses them.

## 8. Performance

Do not optimise prematurely; do avoid obvious waste:
- N+1 queries or one API call per item when a batch or paginated call exists.
- Repeated identical network requests or file reads inside loops (cache within the operation).
- Loading whole large datasets into memory when streaming works; unbounded lists, caches or queues.
- Quadratic loops over large inputs where a dict or set lookup works.
- Blocking calls inside async code.
- Measure before and after when performance is the goal of the change.

## 9. Dependencies

Before adding one:
1. Check whether the project already has something that does it.
2. Check whether the standard library or the existing framework does it.
3. If still needed: prefer well-maintained, widely used, permissively licensed packages with a
   reasonable dependency tree; check recent releases and known vulnerabilities.
4. Add it through the project's tool so the lock file updates (for example `uv add`), in the right
   group (runtime vs dev vs optional), with a reason in the PR description.
5. Never add a dependency to save a few lines.

## 10. Comments and documentation

- Comments explain why: intent, constraints, trade-offs, links to specs or issues, non-obvious
  invariants. Do not restate what the code says.
- Docstrings on public modules, classes and functions: purpose, inputs, outputs, errors raised.
- Delete commented-out code; git remembers it.
- `TODO` only with an owner or issue reference and a reason.
- Update user-facing docs (README, runbooks, specs) in the same change as the behaviour.

## 11. Refactoring

- Refactor in separate steps from behaviour changes; each step keeps tests green.
- Refactor only what your task touches, unless the user asked for a refactor.
- Use small, mechanical moves (extract function, rename, move module, inline) and run tests after
  each.
- Keep public interfaces stable, or deprecate them explicitly.
- Stop and ask if the refactor grows beyond the area of the task or changes architecture.
