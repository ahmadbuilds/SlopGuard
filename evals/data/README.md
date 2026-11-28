# Evaluation data

This directory contains the checked-in evaluation labels, source records, and PyPI response snapshots used by [the evaluation guide](../README.md). The evaluator replays stored responses so CI does not need live registry access.

## Sources and attribution

| Source | Use | License or attribution |
| --- | --- | --- |
| [OpenSSF malicious-packages](https://github.com/ossf/malicious-packages) | Public package reports used to build the malicious-name labels. | The source repository is licensed under [Apache-2.0](https://github.com/ossf/malicious-packages/blob/main/LICENSE). Preserve its attribution when reusing those records. |
| [Top PyPI Packages by Hugovk](https://hugovk.dev/top-pypi-packages/) | Download-rank data used to build the checked-in popularity snapshots and legitimate-name samples. | The source JSON does not state a license; the generated snapshot records this as “not stated by source” and retains its source URL and timestamps. |
| Recorded model suggestions | Candidate names used to evaluate package-name hallucinations. | Generated evaluation inputs; the repository records the source data and review status with each label. |
| Public Git repository histories | Historical pull-request samples selected from the configured repositories. | Repository source remains at its original URL and under its own license. The collector stores extracted candidate metadata rather than a copy of the repository. |
| PyPI JSON API | Registry metadata snapshots for labels and historical samples. | Public package metadata, recorded with the retrieval time and tool version. |

## Files

| File | Contents |
| --- | --- |
| `malicious.jsonl` | Labeled package names built from a local OpenSSF malicious-packages checkout. |
| `synthetic.jsonl` | Generated look-alike names based on the popularity snapshot. |
| `legitimate.jsonl` | Legitimate package names sampled from the separate popularity ranking data. |
| `hallucinated_outputs.jsonl` | Recorded model suggestions used by the hallucination label builder. |
| `hallucinated.jsonl` | Labeled suggestions that were reviewed as package-name hallucinations. |
| `labels.jsonl` | Merged evaluation labels with duplicate canonical names rejected. |
| `registry.jsonl.gz` | Recorded PyPI responses for the labeled package names. The first line records the count, retrieval time, and tool version. |
| `popular_15000.json` | Popularity ranking input used for evaluation samples, separate from the runtime snapshot. |
| `repos.json` | Public repository URLs and collection parameters for historical pull-request samples. |
| `real_prs.jsonl` | Dependency candidates extracted from selected historical commits. |
| `real_prs_registry.jsonl.gz` | Recorded PyPI responses for historical candidates, including the retrieval time in its header. |
| `classifier_conclusion.md` | Human-authored conclusion included in the classifier report. |

The sources and response recordings were captured on 2026-11-27 for the committed results. The popularity JSON records its own source update and retrieval timestamps; those may differ from the registry recording date.

## Regenerate data

Data collection is manual because it reads external sources or makes live PyPI requests. The commands below refresh the labeled examples and their registry recording; review the generated files and provenance before committing them.

```sh
set -euo pipefail
uv sync --locked --group eval
OSV_DIR="$(mktemp -d)"
git clone --filter=blob:none https://github.com/ossf/malicious-packages.git "$OSV_DIR"
uv run python scripts/update_popular_packages.py --limit 15000 --output evals/data/popular_15000.json
uv run python -m evals.build_osv --osv-dir "$OSV_DIR"
uv run python -m evals.build_synthetic
uv run python -m evals.build_legitimate --popular-file evals/data/popular_15000.json
uv run python -m evals.build_hallucinated --outputs evals/data/hallucinated_outputs.jsonl
uv run python -m evals.merge_labels evals/data/malicious.jsonl evals/data/synthetic.jsonl evals/data/legitimate.jsonl evals/data/hallucinated.jsonl --out evals/data/labels.jsonl
uv run python -m evals.record --labels evals/data/labels.jsonl --registry evals/data/registry.jsonl.gz
uv run python -m evals.verify_labels --labels evals/data/labels.jsonl --registry evals/data/registry.jsonl.gz --osv-dir "$OSV_DIR" --popular-file evals/data/popular_15000.json --outputs evals/data/hallucinated_outputs.jsonl --out evals/data/labels.jsonl
```

The block above does not refresh the historical pull-request candidates or their registry recording. To collect those files from the repositories listed in [`repos.json`](repos.json), run:

```sh
uv run python -m evals.collect_prs --repos evals/data/repos.json
```

The collector clones the configured public repositories, selects eligible commits, and records PyPI responses. Review the generated candidates, recording headers, and provenance before committing. See [the evaluation guide](../README.md) for the offline replay commands.
