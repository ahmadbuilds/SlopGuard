# Review calibration examples

## Contents

1. A real Blocker, with evidence
2. A finding downgraded for lack of evidence
3. A strong change: approve
4. Non-findings: do not report these

## 1. A real Blocker, with evidence

Change: a new registry parser installs a package after receiving its project metadata.

```python
def inspect_candidate(name):
    subprocess.run(["pip", "install", name], check=True)
```

Report:

```text
**[Blocker] Registry response can trigger package execution** `src/slopguard/registry.py:71`
Problem: the lookup path installs publisher-controlled code as part of scanning.
Evidence: any newly added candidate reaches this function and its build backend can execute
arbitrary code before a result is returned.
Fix: remove installation and use the bounded PyPI JSON metadata response only.
```

## 2. A finding downgraded for lack of evidence

First draft: "[High] HTTP 503 is reported as a missing package."

Check the actual state mapping: the registry client maps non-success responses other than 404 to
`transient_error`, and `registry_exists` remains `None`. There is no supported input that turns
HTTP 503 into `not_found`.

Result: no finding. The existing test asserting `transient_error` and unknown existence is direct
evidence against the claim.

## 3. A strong change: approve

```text
## Review: package the contract schema
Scope: pyproject.toml, src/slopguard/contracts.py, schema tests
Intent: make the schema available to installed consumers | Checks: pytest, Ruff, package build

### Summary
The schema is included as package data, the public loader reads it through importlib.resources,
and tests validate both the schema and checked-in contract fixtures. Existing serialized data and
the contract version are unchanged.

### Findings
None.

VERDICT: APPROVE
```

## 4. Non-findings: do not report these

- Formatting or lint issues the configured tools already report (one line in "Checks" instead).
- Personal style preferences that match the project's existing style.
- "Could add an interface for future flexibility" with no second implementation in sight.
- Problems in untouched code (report as `pre-existing` only if relevant to the change).
- Speculative issues without a concrete failure scenario.
- Asking for tests of trivial code with no logic (simple data containers, re-exports).
