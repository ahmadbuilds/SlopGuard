# Security policy

SlopGuard helps reviewers identify newly added Python dependency names that are missing from
PyPI or resemble popular packages. It reads changed-file text and public PyPI JSON metadata. It
does not install, import, or execute packages under review.

SlopGuard has important limits: metadata is supplied by package publishers and is not proof of a
package's safety; similarity and other future risk signals can produce false positives and false
negatives; and registry timeouts, malformed responses, or rate limits remain unknown rather than
being treated as evidence of absence. SlopGuard is a review aid, not a security guarantee.

To report a vulnerability privately, use GitHub Security Advisories for this repository. Do not
open a public issue containing exploit details before the maintainers have had a chance to respond.
