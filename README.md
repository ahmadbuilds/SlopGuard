# SlopGuard

SlopGuard is a Python-only MVP for checking PyPI package names for likely typosquatting and supply-chain risk.

## Development setup

Requires Python 3.11 or newer.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m slopguard.smoke
ruff check src tests
ruff format --check src tests
pytest
```

The smoke check validates the checked-in result and extractor-candidate examples against the versioned JSON Schema in `schemas/dependency-result.schema.json`.

## Shared result contract

The contract is versioned independently from the package and is intended to be the boundary between package-name extraction, PyPI lookup, similarity scoring, and pull-request presentation. Read [the contract notes](docs/contract.md) before implementing a producer or consumer. The schema is authoritative; `examples/result.json` is a valid example.

For changed-file extraction behavior and supported syntax, see [the extraction guide](docs/extraction.md).

### Supported dependency syntax

SlopGuard extracts additions from requirements and constraints files, PEP 621 and PEP 735 dependency arrays and build requirements in `pyproject.toml`, and newly introduced absolute Python imports. Requirements continuations and hashes are handled; common import names map to their PyPI distributions. Changes in `setup.py`, `setup.cfg`, and `Pipfile`, as well as package index options and unmapped namespace imports, receive visible manual-review warnings. Import-only candidates are intended for warn-only scoring. The extractor is offline and never executes project code. See the [extraction guide](docs/extraction.md) for details and limits.

For PyPI metadata lookup states, caching, retries, and failure handling, see [the registry guide](docs/registry.md).

Current policy assumptions are conservative and need confirmation from the project team: findings default to `warn`; explicit `block` mode may block only when the configured risk threshold is met using sufficient evidence. Lookup errors or incomplete evidence must never be interpreted as proof that a package is absent or malicious.

## Configuration

Copy `slopguard.example.toml` as a starting point. It contains no credentials. Registry access is anonymous and read-only for the MVP; do not add tokens or secrets to configuration files or examples.

## Contribution workflow

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch naming, pull request expectations, and the checks required before integration.

## Status

This repository is being scaffolded under Linear issue [VOI-110](https://linear.app/voidoner/issue/VOI-110/sg-01-define-contracts-and-scaffold-the-empty-repository). The warn/block policy and contract consumers still require confirmation from Ahsan and Ahmad.
