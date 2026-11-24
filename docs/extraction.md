# Dependency extraction

`slopguard.extractor.extract_dependencies` is a pure, offline extractor. Give it `ChangedFile` values containing the file path, base text, and head text. It compares the two versions and emits only added or replacement lines from the head version. Deletions and unchanged dependencies are ignored.

Each normalized result conforms to the SG-01 dependency-result schema. At extraction time, `registry_exists` is `null`, evidence and similarity candidates are empty, and the score is a neutral placeholder. Later registry and scoring stages must populate those fields before making policy decisions. `source_locations` records the file, 1-based line, and source kind; duplicate package names are emitted once with all their locations.

## Supported syntax

- Requirements and constraints files named with `requirement(s)` or `constraint(s)` and ending in `.txt` or `.in`. PEP 508 names, extras, version specifiers, markers, comments, and inline comments are supported.
- PEP 621 `project.dependencies` and `project.optional-dependencies` arrays in `pyproject.toml`.
- Single-line Python `import` and `from` statements. Standard-library imports are ignored. Known import names are mapped to distributions, including `cv2` to `opencv-python`, `PIL` to `Pillow`, and `yaml` to `PyYAML`.

Direct URLs, VCS/editable requirements, include directives, multiline imports, dynamic dependencies, Poetry dependency tables, malformed requirements, and malformed TOML are skipped with a file/line warning when their changed lines can be located. Included requirements files must be passed separately as changed files. The extractor does not access the network, resolve markers, infer imports from unchanged code, install packages, or execute project code.

To keep diff processing bounded, a changed file is skipped with a warning when its combined base and head content exceeds 2,000 lines or 1,000,000 characters.
