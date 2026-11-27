# Evaluation data

This directory holds manually produced evaluation inputs and recordings. No real records are
checked in yet; create them from the documented sources and review their provenance before adding
them.

| File | Produced by |
| --- | --- |
| `malicious.jsonl` | `uv run python -m evals.build_osv --osv-dir PATH` against a local OSSF malicious-packages checkout. |
| `synthetic.jsonl` | `uv run python -m evals.build_synthetic` using the packaged popularity snapshot. |
| `legitimate.jsonl` | `uv run python -m evals.build_legitimate`, optionally with `--curated PATH`. |
| `hallucinated.jsonl` | `uv run python -m evals.build_hallucinated --outputs PATH` from team-supplied model-output JSONL. |
| `labels.jsonl` | `uv run python -m evals.merge_labels FILE...` after reviewing builder output and resolving overlaps. |
| `registry.jsonl.gz` | `uv run python -m evals.record` against every name in `labels.jsonl`. |
| `repos.json` | Team-curated input listing HTTPS repository URLs, default branches, `per_repo`, and `cutoff_date`. |
| `real_prs.jsonl` | `uv run python -m evals.collect_prs` from the configured repository input. |
| `real_prs_registry.jsonl.gz` | Written alongside `real_prs.jsonl` by `evals.collect_prs`. |

The JSONL label and real-PR files are reviewable text. The compressed recordings are snapshots of
live PyPI responses and carry a UTC recording time and tool version. Keep all generated files
outside the Python package; only `src/slopguard/` is discovered for wheels.
