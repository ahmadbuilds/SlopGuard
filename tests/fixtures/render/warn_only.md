# SlopGuard: WARN

Found 1 dependency: 0 pass, 1 warn, and 0 block.

Policy: mode block; warn at 40; block at 80; unknown results pass; config source slopguard\.toml.

Counts by decision: pass 0, warn 1, block 0.
Counts by severity: info 0, low 0, medium 0, high 1, critical 0.

## Unverified package metadata

All dependency metadata was verified.

## Warn finding: known\-package
- Requested name: known\-package; normalized name: known\-package.
- Source locations: requirements\.txt:4 (requirements).
- Registry state: exists.
- Closest look-alike: requests (distance 1, rank 8).
- Score 63.5 (warn at 40, block at 80).
- lookalike: +12.5 - Resembles requests\.
- not\_found: +51 - PyPI did not find it\.
- Registry metadata: first release 2026\-11\-26T00:00:00Z, release count 1, source link yes; maintainers: package\-maintainer; project URL labels: Source.
- Remediation: Review this dependency\.

SlopGuard 0\.2\.0. [Workflow run](https://github.invalid/example/project/actions/runs/123). [Documentation](https://docs.invalid/slopguard).