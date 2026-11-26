# File, module and folder placement

## Contents
1. Decision procedure (use every time)
2. Where each responsibility goes
3. When to create a new file
4. When to create a new folder
5. Naming
6. Tests, fixtures, configs, scripts, docs
7. Size and shape signals
8. Dependency direction
9. Greenfield: creating structure from nothing
10. Moving, splitting and deleting files
11. Example mapping for this repository

## 1. Decision procedure (use every time)

For each piece of logic in your change, answer in order and stop at the first answer that applies:

1. **Does a project rule say where it goes?** Check `AGENTS.md`, `CLAUDE.md`,
   `README.md`, and architecture docs if the project keeps them. A documented layout always wins.
2. **Is there an analogous feature already?** Find the closest existing feature (same kind of
   endpoint, command, job, model) and mirror its layout, naming and layering.
3. **Does an existing module already own this responsibility?** If the logic belongs to a concept an
   existing module covers and the module stays cohesive, add it there.
4. **Only then consider a new file**, using section 3.

Write the result into your placement plan (SKILL.md step 3) before editing.

## 2. Where each responsibility goes

Name each part of the change by what it does, then place it in the layer that owns that kind of work.
Use the project's names for these layers; the generic roles are:

| Responsibility | Belongs in | Never in |
|---|---|---|
| Parsing a request / CLI args / event, formatting the response | Entry point (route, handler, CLI command, job) | Domain logic |
| Business rules, calculations, decisions | Domain / core module (pure, no I/O) | Entry points, data access |
| Orchestrating a use case (load → decide → save → notify) | Application / service layer | Entry points |
| Database queries, file and object-store access | Data access / repository / storage module | Domain logic, entry points |
| Calls to external APIs and services | Adapter / client module with timeouts and retries | Business logic |
| Input validation and data shapes | Schemas / models at the boundary | Scattered `if` checks deep in logic |
| Authentication and authorisation | Middleware / dependency / policy module, enforced server-side | Individual handlers ad hoc |
| Configuration and secrets loading | One settings/config module reading env or config files | Hard-coded constants, deep `os.environ` reads |
| Logging, metrics, tracing setup | Observability module; call sites only emit events | Every module configuring its own logger |
| Emitting log events | Application and infrastructure layers. Domain functions return results, counters or reasons; the caller logs them | Domain / core modules |
| Data models of the core (entities, value objects, typed config) | Domain module; a data-modelling library the project already uses (for example pydantic here) is allowed | Infrastructure-specific types |
| Models of external payloads (API responses, webhook bodies, DB rows) | Infrastructure/adapter module, converted to core types at the boundary | Domain modules |
| Reading config files (YAML, JSON) | Settings/config loader at the edge; typed models may live in the core | Domain functions opening files |
| Cross-cutting pure helpers used by several modules | A shared module named by what it does (`dates.py`, `money.py`) | A catch-all `utils.py` |

Small projects may legitimately merge layers (for example service and data access in one module).
Follow what the project already does; split only when a module mixes concerns that change for
different reasons or that need different test strategies (pure logic vs I/O).

## 3. When to create a new file

Create a new file only if at least one is true:
- It holds a **distinct responsibility** that would make an existing module less cohesive (a second
  reason to change).
- It is an **I/O boundary** (new external service, new storage) that should be isolated and
  replaceable in tests.
- It is **used by more than one module**, or clearly will be because two callers exist in this change.
- The existing module would exceed the size/shape signals in section 7 and the new part splits along
  a real seam.
- A project rule or the analogous feature puts this kind of code in its own file.

Do **not** create a new file when:
- It would contain one small function or constant used in one place: put it next to its caller.
- It only re-exports or forwards calls.
- The split is by technical type alone (`constants.py`, `types.py`, `exceptions.py` for one feature)
  while the project groups by feature, or vice versa. Follow the project's grouping.
- You would name it `utils`, `helpers`, `misc`, `common`, `manager` or `stuff`. Find the real
  concept, or keep the code where it is used.

## 4. When to create a new folder

Create a folder (package) only when:
- there are, or this change creates, two or more closely related modules that form a subsystem with
  its own boundary, or
- the project's layout requires one for this kind of component (for example one folder per feature
  or service).

Every new package gets an `__init__.py` (or the language's equivalent) with a one-line docstring
saying what belongs there. Do not create empty folders "for later"; the exception is a convention the
project already uses (for example placeholder folders with a README listing the phase that fills them).

New **top-level** folders, new layers, or a new architectural pattern are architecture decisions:
ask first and record them in `ARCHITECTURE.md`.

## 5. Naming

- Match the project's existing naming style for files, modules, classes, functions and tests.
- Name modules after the domain concept or responsibility (`revert_detection.py`, `github_client.py`),
  not the pattern (`strategy.py`, `factory.py`) unless the pattern is the concept.
- One concept, one term: if the codebase says "change", do not introduce "patch" or "diff" for the
  same thing.
- Tests mirror the module they test, following the project's existing pattern (flat
  `tests/unit/test_pricing.py`, or mirrored subfolders `tests/unit/domain/test_pricing.py`). Before
  creating a test file, check its basename is unique across the test tree: duplicate basenames break
  pytest's default import mode. Subfolders need an `__init__.py` if the test tree already uses them.
- Avoid abbreviations the codebase does not already use.

## 6. Tests, fixtures, configs, scripts, docs

- **Tests:** in the project's test tree, split as it already is (for example `unit/` pure and fast,
  `integration/` touching files, databases or recorded HTTP). No live network in tests unless the
  project has a marked suite for it.
- **Fixtures and sample data:** `tests/fixtures/` (small, synthetic, no real personal data or
  secrets). Large or generated data never goes in git.
- **Configuration:** versioned config files in the config folder, or the settings module. Anything
  that changes a result (thresholds, windows, versions) is configuration, not a magic number.
- **Secrets:** environment variables or a secret manager, loaded once by the settings module; an
  example file (`.env.example`) documents names, never values.
- **Scripts:** thin wrappers that call the package's CLI or functions. No business logic in scripts
  or notebooks.
- **Docs:** update the doc that describes what you changed (README commands, runbooks, architecture,
  label specs). New structure or a new pattern gets an ADR.
- **Deployment files** (Dockerfiles, compose, CI, infra): in the project's deploy/CI folders; never
  mixed into source packages.

## 7. Size and shape signals

These are prompts to look closer, not hard limits. Split only along a real responsibility seam.

| Signal | Look closer when |
|---|---|
| Function length | more than ~40-50 lines, or it needs comments to separate "sections" |
| Nesting | more than 3 levels of `if`/`for`/`try` |
| Parameters | more than ~5, or boolean flags that switch behaviour |
| Module length | more than ~400-500 lines, or it holds several unrelated concepts |
| Class | many unrelated methods, or methods that do not use the class's state |
| Imports | a module imports from many layers (likely mixing concerns) |

## 8. Dependency direction

- Dependencies point inward: entry points → application → domain; infrastructure implements what the
  application needs. The core never imports frameworks, HTTP clients, ORMs or the CLI.
- No circular imports. If two modules need each other, the shared part belongs in a third module, or
  one side should receive what it needs as an argument.
- Framework types (request objects, ORM models, framework exceptions) do not leak past the boundary;
  convert them to the project's own types.

## 9. Greenfield: creating structure from nothing

### 9a. A new repository or standalone project

1. Check for an existing layout decision (architecture doc, ADR, template the team uses, the
   framework's official recommended layout). Use it if it exists.
2. Otherwise propose the **minimum** structure the first real feature needs and get agreement before
   creating it (new top-level folders are an architecture decision). Shape (adapt names to the
   language and framework):
   ```
   README.md            what it is, how to install, run and test
   AGENTS.md            rules for coding agents (layering, commands, hard rules)
   pyproject.toml       (or package.json, go.mod...) dependencies + tool config
   .gitignore  .env.example  .editorconfig
   src/<package>/       source; split into layers only when the first feature needs it
   tests/               mirrors src/
   configs/             only if there is versioned configuration
   ARCHITECTURE.md     the layout decision itself
   .github/workflows/   lint + types + tests on every PR
   ```
3. Add layers (domain/application/infrastructure or the framework's equivalent) when the first
   feature has both pure logic and I/O, not before.
4. Wire the quality thresholds on day one: formatter, linter, type checker, test runner, pre-commit or CI.
5. Record the decision and the reasons in `ARCHITECTURE.md`; update `AGENTS.md` so future changes follow it.
6. Do not generate placeholder modules, empty classes or speculative folders.

### 9b. A new subsystem inside an existing repository

No new top-level folders. Inside the existing layout, create only what the first feature needs:
- a package in each layer the feature actually uses (for example `domain/<area>/`,
  `application/<use_case>.py`, `infrastructure/<adapter>/`), each package with an `__init__.py`
  docstring stating its purpose;
- the matching test files in the existing test tiers (pure logic in unit tests, I/O in integration
  tests);
- config entries in the existing config files or a new versioned config file next to them;
- an update to `AGENTS.md`, the README or architecture docs if the subsystem adds a new kind of
  component; an ADR if it introduces a new pattern.

Do not add empty placeholder packages. Record genuinely planned modules in `AGENTS.md` and create
their package only when a concrete use case is ready.

## 10. Moving, splitting and deleting files

- Move or split in a separate step from behaviour changes, with tests passing before and after.
- Update every import, reference, config key, entry point, doc and test in the same change.
- Before deleting, prove it is unused: search for imports, string references, dynamic loading,
  CLI entry points, configs, scripts, CI files and docs.
- Keep public import paths stable, or keep a deprecated re-export and flag the change.

## 11. Example mapping for this repository

SlopGuard follows `AGENTS.md`: `src/slopguard/` contains the package modules, the dependency
extractor and metadata parsers are pure, `registry.py` owns bounded PyPI transport, schemas ship as
package data, and tests are grouped into pure unit tests and I/O-oriented integration tests.

Import-name mappings are generated data plus reviewed overrides; do not edit mappings in code.
To change them, update the override data or run the manual generator and review its output.

Example, "add a supported import-name mapping":
```
src/slopguard/data/import_name_overrides.json CHANGE  add the reviewed import mapping and reason
tests/unit/test_extractor.py         CHANGE  cover the mapping through extraction
ARCHITECTURE.md                      CHANGE  document the supported syntax if needed
```
For registry transport behavior, keep the HTTP boundary in `src/slopguard/registry.py` and place
controlled-response tests in `tests/integration/test_registry.py`. Do not put network access in
the extractor, parser, or contract modules.
