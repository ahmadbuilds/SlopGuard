# SlopGuard coding guide

SlopGuard is a GitHub Action that checks Python dependencies added by a pull request for names missing from PyPI or resembling popular packages. It reads repository content and registry metadata as data; it never installs or executes a package being checked.

Read [README.md](README.md) for user instructions, [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow and result contract, and [CONTRIBUTING.md](CONTRIBUTING.md) before preparing a contribution.

## Commands

- Install the locked development environment: `uv sync --locked`
- Run lint, formatting, tests, and generated agent-file checks: `make check`
- Reproduce recorded evaluation results: `make eval`
- Reproduce the optional classifier report: `make classifier`
- Refresh the popularity snapshot manually: `uv run python scripts/update_popular_packages.py`
- After editing `.agents/skills/` or `.agents/claude-agents/`, run `uv run python scripts/sync_agent_skills.py`.

## Engineering workflow

1. Follow the `coding-agent` skill for feature, fix, refactor, configuration, test, and structure changes. Use its file-placement guidance before creating modules or folders.
2. Have finished changes reviewed independently with the `code-quality-agent` skill. Fix Blocker and High findings before committing.
3. Keep one tracked issue per branch. Link the related GitHub issue in the pull request when one exists.

## Hard rules

1. **Never install, download, import, or execute a package being checked**, and never run code from the pull request under review. SlopGuard reads PyPI JSON metadata only.
2. **Untrusted input stays data.** Package names, file contents, pull-request titles and bodies, and PyPI responses are attacker-controlled. Do not interpolate them into shell commands, `run:` steps, Markdown, or HTML without escaping.
3. **Unknown is not absent.** A timeout, rate limit, malformed response, or exhausted lookup budget is unknown; it is never reported as proof that a package does not exist and never blocks on its own.
4. **The result contract is versioned.** Changes to `src/slopguard/schemas/` or `src/slopguard/contracts.py` update the schema, architecture guide, and fixtures in the same change. Breaking field or decision changes bump the contract version.
5. **No live network in tests.** Use recorded responses and the injectable transport.
6. **Never commit tokens, `.env` files, or realistic credentials.** The action uses the default `GITHUB_TOKEN` only when its optional comment is enabled.
7. **Use least-privilege workflow permissions.** Declare permissions per workflow, pin third-party actions to full commit SHAs, and do not use `pull_request_target` to check out pull-request code.
8. **Keep scoring thresholds in configuration**, not hard-coded in scoring logic.
9. Explain any new runtime dependency and why existing dependencies are insufficient.

## Conventions

- Support Python 3.11 and newer. Add type hints to public functions and use frozen dataclasses for immutable results.
- Use timezone-aware UTC timestamps.
- Place pure tests in `tests/unit/` and filesystem or recorded-HTTP tests in `tests/integration/`.
- Use imperative commit messages that explain why.
- Keep repository guides at the root; there is no `docs/` directory.

## Module map

- `src/slopguard/cli.py`, `action.yml`: command-line interface, report output, and composite action.
- `src/slopguard/config.py`: immutable validated configuration and discovery.
- `src/slopguard/contracts.py`, `src/slopguard/schemas/`: dependency-result and scan-report contracts.
- `src/slopguard/extractor.py`: pure dependency extraction; `import_names.py` maps Python imports through packaged data and reviewed overrides.
- `src/slopguard/git.py`, `src/slopguard/scan.py`: bounded Git reads and pull-request scan orchestration.
- `src/slopguard/pypi.py`, `registry.py`, `registry_result.py`: package URL construction, bounded PyPI transport, response parsing, and evidence merging.
- `src/slopguard/similarity.py`, `src/slopguard/data/`: offline look-alike detection and packaged mapping data.
- `src/slopguard/scoring.py`: evidence scores, severity, and policy decisions.
- `src/slopguard/render.py`, `src/slopguard/github.py`: summaries, annotations, and the optional sticky comment client.
- `evals/`: separate label, recording, replay, and offline-evaluation tools; excluded from the runtime package.
- `.github/workflows/ci.yml`, `release.yml`, `scorecard.yml`, and `slopguard.yml`: checks, releases, supply-chain reporting, and the repository's scan.
- `examples/`: copyable configuration and workflow examples.

## Branch and pull-request workflow

- Keep `main` releasable. Base each short-lived branch on the latest `main`.
- Use `feature/<name>`, `fix/<name>`, or `docs/<name>` for work branches.
- Do not merge contract consumers until their shared contract change is merged or the branch is updated onto it.
- Describe behavior and contract changes, test evidence, and security or permission effects in the pull request.
- Run lint, formatting, tests, and the generated agent-file check before requesting review. Request review from owners of affected contracts.
- Preserve individual commits when merging and delete the branch after merge.
- Do not add package installation or package execution to the scanner. Never commit API keys, tokens, `.env` files, or realistic credentials; use placeholders in documentation.
