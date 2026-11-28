# Contributing

Thanks for helping improve SlopGuard. This guide covers the local checks and the workflow for changes.

## Set up the project

Install the locked development environment and run the repository checks:

```sh
uv sync --locked
make check
```

Evaluation changes can also run the recorded-data checks:

```sh
make eval
make classifier
```

These commands use fixtures and checked-in registry recordings. They do not install or execute packages being scanned.

## Prepare a change

- Start from the latest `main`.
- Use a branch named `feature/<name>`, `fix/<name>`, or `docs/<name>`.
- Keep each branch focused on one tracked issue and use imperative commit messages that explain why.
- Update user-facing documentation and examples whenever behavior or defaults change.
- Add or update tests for behavior changes. Tests use recorded responses and do not access live package registries.
- Run `make check` before opening a pull request. Run `make eval` or `make classifier` when changing evaluation behavior or artifacts.

## Extend the scanner

To add a dependency format, update the pure extractor and add fixtures for additions, replacements, deletions, and unsupported syntax. To add a scoring signal, keep its weight and thresholds in configuration, explain the evidence in the result, and update the architecture and evaluation documentation.

Never install, import, download, or execute a package being checked. The scanner reads declarations and public registry metadata only.

## Open a pull request

Use the repository pull-request template. Describe the user-visible behavior, security and permission effects, and checks you ran. Link a related GitHub issue when one exists. Keep workflow permissions minimal and pin third-party actions to full commit SHAs.

## Report a vulnerability

Use [private vulnerability reporting](https://github.com/ahmadbuilds/SlopGuard/security/advisories/new). Do not open a public issue for a security report.
