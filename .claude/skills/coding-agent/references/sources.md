# Sources and trade-offs

These rules translate established practice into instructions for an AI coding agent. Where sources
disagree, the chosen rule and the reason are given.

| Source | What we take | What we deliberately do not take |
|---|---|---|
| Google Engineering Practices, code review guide (google.github.io/eng-practices) | Design first; functionality incl. edge cases and concurrency; complexity and over-engineering; tests in the same change; comments explain why; docs updated; look beyond the diff; approve once the change definitely improves code health even if imperfect; "Nit:" for optional points | Nothing omitted; it is the reviewer baseline |
| *Software Engineering at Google* (Winters, Manshreck, Wright) | Hyrum's Law: every observable behaviour will be depended on, so treat contracts conservatively; tests as the safety net for change ("if you liked it, you should have put a test on it"); small, frequent changes | Monorepo-specific tooling assumptions |
| *A Philosophy of Software Design* (Ousterhout) | Deep modules (simple interface, substantial behaviour); avoid shallow pass-through layers and wrappers; complexity is the main enemy | |
| *Clean Code* (Martin) | Meaningful names; functions that do one thing; no commented-out code; guard clauses | Very small function-size targets and splitting purely to shorten functions; these create shallow, fragmented code (Ousterhout's critique). We use size only as a signal to look closer |
| SOLID | Single responsibility read as "one reason to change"; dependency inversion at I/O boundaries | Interfaces for every class; we require a second implementation, a test seam or a boundary first |
| Martin Fowler (refactoring catalogue, YAGNI, Beck's design rules) | Refactor in small behaviour-preserving steps with tests; YAGNI against speculative generality; "passes tests, reveals intention, no duplication, fewest elements" | |
| The Twelve-Factor App (12factor.net) | Config in the environment, not code; dependencies explicitly declared and isolated; logs as event streams; dev/prod parity | Process-model details irrelevant to libraries and CLIs |
| OWASP Top 10:2025 (top10.owasp.org/2025) | A01 Broken Access Control, A02 Security Misconfiguration, A03 Software Supply Chain Failures, A04 Cryptographic Failures, A05 Injection, A06 Insecure Design, A07 Authentication Failures, A08 Software or Data Integrity Failures, A09 Security Logging and Alerting Failures, A10 Mishandling of Exceptional Conditions | |
| Google SRE book; AWS Well-Architected (Reliability pillar) | Timeouts on every remote call; retries only when safe, with exponential backoff, jitter and caps; idempotency; graceful degradation; design for partial failure | Full SLO machinery for a prototype |
| Microsoft REST API Guidelines and API design practice | Backwards-compatible evolution, explicit versioning, never repurpose a field | Azure-specific conventions |
| Testing practice (Beck, Fowler's test pyramid, Google testing blog) | Test behaviour through public interfaces; many fast unit tests, fewer integration tests; deterministic tests; prefer real objects or fakes over heavy mocks | Coverage targets as goals |
| Anthropic Agent Skills best practices (platform.claude.com) | Concise SKILL.md under 500 lines; third-person "what + when" descriptions; references one level deep with a table of contents; checklists and validate-fix-repeat loops; one term per concept | |

## Key tensions and how the skills resolve them

- **Consistency vs quality:** match existing conventions, but never copy bugs or unsafe patterns;
  improve only what the change touches.
- **Modularity vs fragmentation:** split along responsibilities that change for different reasons
  or need different tests; never split by size alone.
- **Abstraction vs simplicity:** abstraction needs a second real use, a boundary, a test seam or a
  project rule.
- **Thoroughness vs scope:** smallest coherent change; unrelated issues become follow-ups.
- **Review strictness vs progress:** block on correctness, security, data loss and contract breaks;
  approve changes that clearly improve code health even if imperfect.
