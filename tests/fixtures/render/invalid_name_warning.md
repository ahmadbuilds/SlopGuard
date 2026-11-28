# SlopGuard: WARN

Found 1 dependency: 0 pass, 1 warn, and 0 block.

Policy: mode block; warn at 40; block at 80; unknown results pass; config source slopguard\.toml.

Counts by decision: pass 0, warn 1, block 0.
Counts by severity: info 0, low 0, medium 0, high 1, critical 0.

## Unverified package metadata

- \_invalid: not a valid distribution name; policy decision warn.
Unknown metadata is unverified, not evidence that the package does not exist.

## Warn finding: \_invalid
- Requested name: \_invalid; normalized name: \-invalid.
- Registry state: not a valid distribution name.
- Closest look-alike: requests (distance 1, rank 8).
- Score 63.5 (warn at 40, block at 80).
- lookalike: +12.5 - Resembles requests\.
- not\_found: +51 - PyPI did not find it\.
- Remediation: Review this dependency\.

SlopGuard 0\.2\.0. [Workflow run](https://github.invalid/example/project/actions/runs/123). [Documentation](https://docs.invalid/slopguard).