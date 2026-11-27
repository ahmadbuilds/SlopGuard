# Reproducible evaluation

The evaluation tools are an offline/manual subsystem. They are not imported by `slopguard` and are
excluded from wheels. Tests use only synthetic fixtures under `tests/fixtures/evals/`; tests and CI
do not use the network. The real dataset and registry recordings are intentionally not checked in
until they have been generated from reviewed sources.

## Workflow

1. Prepare source data locally. `build_osv` reads a local checkout of
   [`ossf/malicious-packages`](https://github.com/ossf/malicious-packages); `build_hallucinated`
   reads team-supplied JSONL rows shaped as `{model, date, prompt, packages}`. The optional
   legitimate curated JSONL has `name`, `rationale`, and `source`, with optional `category`.
2. Build deterministic label subsets. Legitimate negatives come from ranks 5001–15000 in the
   separate evaluation popularity file, outside the packaged top-5000 comparison set. Each row
   records that file's SHA256, rank range, seed, and sample count. `--rank-min` and `--rank-max`
   can adjust the sampled window; `--lookalike-count` (default 50) also samples seeded, disjoint
   known packages from that window that resemble the packaged popular-name snapshot. The stratum
   fails clearly if too few eligible names exist; optional curated hard negatives can be added
   with `--curated`.
3. Merge labels. Duplicate PEP 503 canonical names are rejected rather than
   silently assigned conflicting classes. Borderline labels must be excluded from binary metrics.
4. Record registry evidence once with the explicitly networked `record` command. The gzip file
   starts with a recording header and then one serialized `RegistryLookupResult` per name. Then
   run `evals.verify_labels` offline to check OSV, seeded synthetic generation, popularity ranks,
   and recorded model outputs. Passed rows receive an automated reviewer; failed rows remain in
   the dataset but are excluded from binary metrics.
5. Run `evaluate` offline. It replays registry results at the recording's timestamp, so age-based
   signals use a fixed time. Default runs compare package defaults with the block example config.
   The recorded-state results are accompanied by an attack-time scenario that scores expected-flag
   and expected-pass rows as if each package had appeared one day earlier with one release and no
   source links. It reports the full confusion matrix and precision, recall, and FPR at both
   thresholds; new-package signals alone can warn expected-pass controls. Use `--attack-time-days`
   and `--attack-time-releases` to change those assumptions. An unreviewed synthetic typosquat that
   already exists on PyPI is excluded and listed for human review.
   LLM-suggested names that exist on PyPI are excluded as non-hallucinations; reports show
   conclusive checked/not-found counts, hallucination rate, and flagged not-found counts per model.
   Hallucinated label rows carry an optional `models` list (every model that suggested the name);
   shared names count once per suggesting model, while overall totals count each name once.
6. Optionally collect historical PR samples with `collect_prs`. That manual command clones only
   configured HTTPS repositories with `--filter=blob:none`, scans recent first-parent commits, and
   selects only commits whose extraction yields at least one dependency candidate. Inspection is
   bounded by `MAX_COMMITS_TO_INSPECT`; per-repository inspected counts and failures are recorded.
   A repository failure is recorded with sanitized, bounded Git stderr while collection continues;
   the collector exits non-zero only when every configured repository fails. It saves candidate
   rows and a registry recording. `evaluate --real-prs` replays these stored candidates and does not
   clone repositories.

Reports include stable provenance (tool and recording versions and input SHA256 values), label
verification coverage, and stored real-PR selection parameters. The changing checkout commit and
runtime are written to `run_info.json` only.

Example commands (the defaults are also shown by each command's `--help`):

```sh
git clone --filter=blob:none https://github.com/ossf/malicious-packages.git /tmp/malicious-packages
uv run python scripts/update_popular_packages.py --limit 15000 --output evals/data/popular_15000.json
uv run python -m evals.build_osv --osv-dir /tmp/malicious-packages --count 100 --seed 11608
uv run python -m evals.build_synthetic --top-k 200 --seed 11608
uv run python -m evals.build_legitimate --popular-file evals/data/popular_15000.json --rank-min 5001 --rank-max 15000 --count 100 --strata 5 --seed 11608
uv run python -m evals.build_hallucinated --outputs evals/data/hallucinated_outputs.jsonl --count 100 --seed 11608
uv run python -m evals.merge_labels evals/data/malicious.jsonl evals/data/synthetic.jsonl evals/data/legitimate.jsonl evals/data/hallucinated.jsonl --out evals/data/labels.jsonl
uv run python -m evals.record --labels evals/data/labels.jsonl --registry evals/data/registry.jsonl.gz --concurrency 8
uv run python -m evals.verify_labels --labels evals/data/labels.jsonl --registry evals/data/registry.jsonl.gz --osv-dir /tmp/malicious-packages --popular-file evals/data/popular_15000.json --outputs evals/data/hallucinated_outputs.jsonl --out evals/data/labels.jsonl
uv run python -m evals.evaluate --labels evals/data/labels.jsonl --registry evals/data/registry.jsonl.gz --out-dir evals/results --attack-time-days 1 --attack-time-releases 1
```

For historical PRs, first create `evals/data/repos.json` as described in
[`evals/data/README.md`](data/README.md), then run:

```json
{
  "repos": [{"url": "https://github.com/example/project.git", "default_branch": "main"}],
  "per_repo": 50,
  "cutoff_date": "2025-01-01"
}
```

```sh
uv run python -m evals.collect_prs --repos evals/data/repos.json
uv run python -m evals.evaluate --labels evals/data/labels.jsonl --registry evals/data/registry.jsonl.gz --real-prs evals/data/real_prs.jsonl --real-prs-registry evals/data/real_prs_registry.jsonl.gz --out-dir evals/results
```

`make eval` requires the label and registry recordings and prints the command that creates a
missing input. When both historical PR files exist, it includes them by default. The CLI also
accepts `--attack-time-days` and `--attack-time-releases` (both default to 1). CI compares
checked-in deterministic files in `evals/results/`; volatile `run_info.json` is excluded.

## Metrics

The positive ground-truth class is `expected = "flag"`; `expected = "pass"` is negative and
`expected = "exclude"` rows are omitted and counted. Two prediction thresholds are reported:
`warn` or `block` means flagged, and `block` alone means flagged. Each includes TP, FP, TN, FN,
precision `TP/(TP+FP)`, recall `TP/(TP+FN)`, false-positive rate `FP/(FP+TN)`, and F1
`2*precision*recall/(precision+recall)`. A metric with a zero denominator is `null` in JSON and
`n/a` in Markdown. Metrics are also grouped by the label's `category`.

For historical PRs, PR-level rates count a PR as flagged when any candidate warns or blocks (or
blocks for the stricter view). These are proxy false-positive rates: a merged change is not
human-verified ground truth, and dependency behavior or reviewer intent may have changed.
Candidate-level flagged-new-dependency rate is reported first; PR-level rate is shown second.
Reports also include every selected repository, inspected-commit count, base and head SHA, commit
subject, candidate names, and decisions. Repositories that fail collection are listed while other
repositories continue.

This is a preliminary evaluation over a curated set of a few hundred names. Seeded synthetic edits
do not model all real attacks, labels may be incomplete, popularity data can become stale, and live
PyPI state changes between recordings. Metrics support threshold review; they do not establish that
a package is malicious or safe. No candidate package is installed, imported, or executed.
