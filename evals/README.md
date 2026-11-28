# Reproducible evaluation

The evaluation measures how SlopGuard classifies recorded suspicious, legitimate, and historical dependency changes. It uses checked-in labels and registry responses; report generation does not call PyPI.

## Reproduce the reports

Run these commands from the repository root:

```sh
uv sync --locked --group eval
make eval
make classifier
```

`make eval` replays the recorded registry responses under the default and block configurations. `make classifier` runs the optional offline classifier comparison. Neither command installs or executes any package being evaluated.

CI runs both commands, runs the classifier tests, and checks that generated evaluation files match the committed artifacts. `run_info.json` records the source commit and runtime; the reproducibility check excludes that file.

## Committed results

The registry recordings used for these runs were captured on 2026-11-27. See the [dataset guide](data/README.md) for provenance and the recording headers.

| Scenario | Metrics or comparison | Report | Input recording |
| --- | --- | --- | --- |
| Default policy | [metrics.json](results/defaults/metrics.json) | [report.md](results/defaults/report.md) | [registry data](data/registry.jsonl.gz), 2026-11-27 |
| Blocking policy | [metrics.json](results/block/metrics.json) | [report.md](results/block/report.md) | [registry data](data/registry.jsonl.gz), 2026-11-27 |
| Historical pull requests | [real_prs metrics](results/defaults/metrics.json) | [historical sample](results/defaults/report.md#historical-pull-request-sample) | [historical registry data](data/real_prs_registry.jsonl.gz), 2026-11-27 |
| Classifier comparison | [comparison.json](results/classifier/comparison.json) | [report.md](results/classifier/report.md) | Recorded evaluation data, 2026-11-27 |

The default and blocking reports include historical pull-request analysis when the historical data files are present. Each report describes its evaluation inputs and metrics. Treat these results as evidence about the recorded datasets, not as a guarantee about future packages.

## Method

The evaluation combines labeled package-name examples with recorded PyPI responses. The offline evaluator replays those responses at their recorded time so time-based signals use a fixed reference. It compares the default and blocking policies against expected labels and reports results by category.

The historical pull-request set is selected from configured public repositories. The collector reads commit diffs, keeps commits that introduce supported dependency candidates, and records the selected metadata. Evaluation replays the saved registry responses; it does not clone repositories.

The optional classifier uses stored feature objects and cross-validation to compare a learned model with SlopGuard's rule decisions. The human-written interpretation is maintained in [classifier_conclusion.md](data/classifier_conclusion.md).

## Limits

The labels are curated or generated evaluation inputs, not a complete sample of PyPI attacks. Public registry state changes, the popularity snapshot can become stale, and dataset composition affects every metric. A high result on these datasets does not show that a package is safe or predict future attacks.

See [data/README.md](data/README.md) for data sources, attribution, licensing notes, and file formats.
