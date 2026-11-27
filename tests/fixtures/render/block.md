# SlopGuard: BLOCK

Found 1 dependencies: 0 pass, 0 warn, and 1 block.

Policy: mode block; warn at 40; block at 80; unknown results pass; config source slopguard\.toml.

Counts by decision: pass 0, warn 0, block 1.
Counts by severity: info 0, low 0, medium 0, high 1, critical 0.

## Unverified package metadata

All dependency metadata was verified.

## Block finding: reqeusts
- Requested name: reqeusts; normalized name: reqeusts.
- Source locations: requirements\.txt:4 (requirements).
- Registry state: does not exist on PyPI.
- Closest look-alike: requests (distance 1, rank 8).
- Score 63.5 (warn at 40, block at 80).
- lookalike: +12.5 - Resembles requests\.
- not\_found: +51 - PyPI did not find it\.
- Remediation: Review this dependency\.

SlopGuard 0\.2\.0. [Workflow run](https://github.invalid/example/project/actions/runs/123). [Documentation](https://docs.invalid/slopguard).