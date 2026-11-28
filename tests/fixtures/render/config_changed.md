# SlopGuard: WARN

Found 1 dependency: 0 pass, 1 warn, and 0 block.

Policy: mode block; warn at 40; block at 80; unknown results pass; config source slopguard\.toml.
The configuration changed in this pull request; the base-branch policy was used.

Counts by decision: pass 0, warn 1, block 0.
Counts by severity: info 0, low 0, medium 0, high 1, critical 0.

## Unverified package metadata

All dependency metadata was verified.

## Warn finding: new\-package
- Requested name: new\-package; normalized name: new\-package.
- Registry state: exists.
- Closest look-alike: requests (distance 1, rank 8).
- Score 63.5 (warn at 40, block at 80).
- lookalike: +12.5 - Resembles requests\.
- not\_found: +51 - PyPI did not find it\.
- Remediation: Review this dependency\.

SlopGuard 0\.2\.0. [Workflow run](https://github.invalid/example/project/actions/runs/123). [Documentation](https://docs.invalid/slopguard).