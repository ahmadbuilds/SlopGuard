# Offline classifier comparison

Model: logistic-regression; sklearn: 1.9.1; seed: 11608; folds: 5; probability threshold: 0.5.

## Input provenance

- block_attack_time: SHA256 `e56053f8ad6f5633e4a0e64d9c2b13a56c1d06e1de821661c1dd12bba5539f41`
- block_predictions: SHA256 `aba7c90f847e91141d8b368e313799c8699ce3080148726e64eea69dfa8b180c`
- defaults_attack_time: SHA256 `ad213d12ecc8a79f4ec2b676b30c19220484bd139f5ffdd363d2eeb35591a949`
- defaults_predictions: SHA256 `da3809ce0b1b22cd43abb0ed8f2160dcb862abebeac249f132f1f0e47ca253ac`

Row counts by expected class:
- attack_time: {'flag': 276, 'pass': 150}
- recorded_state: {'flag': 276, 'pass': 150}

## Recorded State

Scenario A: recorded state; classifier metrics use out-of-fold predictions.

| Scorer | TP | FP | TN | FN | Precision | Recall | FPR | F1 | ROC-AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Classifier | 276 | 0 | 150 | 0 | 1.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 |
| Defaults rule (warn/block) | 276 | 1 | 149 | 0 | 0.9964 | 1.0000 | 0.0067 | 0.9982 | 1.0000 |
| Block rule | 184 | 0 | 150 | 92 | 1.0000 | 0.6667 | 0.0000 | 0.8000 | 1.0000 |

Logistic-regression coefficients (full-data fit; not CV coefficients):

- `age_days_missing`: 0.975117
- `best_edit_distance`: -0.033323
- `best_similarity`: 0.164055
- `has_source`: -0.567707
- `log1p_age_days`: -0.916021
- `log1p_release_count`: -0.728359
- `log_best_rank`: -0.075233
- `no_lookalike`: -0.133255
- `registry_status=exists`: -0.975117
- `registry_status=not_found`: 0.975117
- `release_count_missing`: 0.975117

## Attack Time

Scenario B: attack time; classifier metrics use out-of-fold predictions.

| Scorer | TP | FP | TN | FN | Precision | Recall | FPR | F1 | ROC-AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Classifier | 182 | 7 | 143 | 94 | 0.9630 | 0.6594 | 0.0467 | 0.7828 | 0.8440 |
| Defaults rule (warn/block) | 276 | 150 | 0 | 0 | 0.6479 | 1.0000 | 1.0000 | 0.7863 | 0.7646 |
| Block rule | 90 | 2 | 148 | 186 | 0.9783 | 0.3261 | 0.0133 | 0.4891 | 0.7646 |

Logistic-regression coefficients (full-data fit; not CV coefficients):

- `age_days_missing`: 0.0
- `best_edit_distance`: -1.337467
- `best_similarity`: 3.295489
- `has_source`: 0.0
- `log1p_age_days`: -0.0
- `log1p_release_count`: -0.0
- `log_best_rank`: -3.247125
- `no_lookalike`: -1.87872
- `registry_status=exists`: 0.0
- `release_count_missing`: 0.0

## Recorded To Attack Time Shift

Scenario C: fit on recorded state, evaluate on attack time.

| Scorer | TP | FP | TN | FN | Precision | Recall | FPR | F1 | ROC-AUC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Classifier | 0 | 0 | 150 | 276 | n/a | 0.0000 | 0.0000 | n/a | 0.7718 |
| Defaults rule (warn/block) | 276 | 150 | 0 | 0 | 0.6479 | 1.0000 | 1.0000 | 0.7863 | 0.7646 |
| Block rule | 90 | 2 | 148 | 186 | 0.9783 | 0.3261 | 0.0133 | 0.4891 | 0.7646 |

Logistic-regression coefficients (full-data fit; not CV coefficients):

- `age_days_missing`: 0.975117
- `best_edit_distance`: -0.033323
- `best_similarity`: 0.164055
- `has_source`: -0.567707
- `log1p_age_days`: -0.916021
- `log1p_release_count`: -0.728359
- `log_best_rank`: -0.075233
- `no_lookalike`: -0.133255
- `registry_status=exists`: -0.975117
- `registry_status=not_found`: 0.975117
- `release_count_missing`: 0.975117

## Interpretation

- attack_time: class counts {'flag': 276, 'pass': 150}.
  No single feature perfectly separates the classes.
  Constant features: registry_status=exists, log1p_age_days, age_days_missing, log1p_release_count, release_count_missing, has_source.
- recorded_state: class counts {'flag': 276, 'pass': 150}.
  Perfectly separating single features: registry_status=exists, registry_status=not_found, log1p_age_days, age_days_missing, log1p_release_count, release_count_missing.
  No features are constant across all rows.
- recorded_to_attack_time_shift: class counts {'flag': 276, 'pass': 150}.
  No single feature perfectly separates the classes.
  Constant features: registry_status=exists, registry_status=not_found, log1p_age_days, age_days_missing, log1p_release_count, release_count_missing, has_source.

Precision = TP/(TP+FP); recall = TP/(TP+FN); FPR = FP/(FP+TN); F1 = 2×precision×recall/(precision+recall). Undefined denominators are null in JSON and n/a here.

## Conclusion

**The GitHub Action keeps the rule-based score.** The classifier remains an offline experiment.

**Scenario A (recorded state) proves nothing about the model.** Every flagged name was absent from PyPI and every legitimate name existed, so registry status alone separates the classes (see Interpretation). Both scorers do almost perfectly. That reflects how the labels were built, not skill.

**Scenario C shows the classifier learned that shortcut.** A model trained on the recorded state and tested on freshly registered packages flags none of the 276 bad names at a 0.5 threshold, because "exists on PyPI" was all it learned. A real attacker who registers the name first defeats it completely.

**Scenario B (attack time) is the honest comparison.** Every package looks newly published, so only look-alike features differ.

- The classifier, trained and scored with cross-validation, catches about two-thirds of bad names with a false-positive rate of about 5%.
- The rule score in block mode catches about one-third, with a false-positive rate of about 1%.
- The classifier ranks better overall (higher ROC-AUC). It trades more false alarms for more catches, using weights learned from the same small dataset and a fixed 0.5 probability threshold.

**We do not ship the classifier,** for four reasons:

1. The rule score explains every decision as named signals with points, and those appear in the pull request. A logistic-regression probability does not tell a maintainer what to fix.
2. Users can tune the rule thresholds and weights in `slopguard.toml` without training data. A model would need labelled data from each team.
3. The dataset is small, a few hundred names with mostly synthetic positives. The classifier's advantage could come from that sample rather than from real attacks.
4. The Action stays free of ML runtime dependencies. scikit-learn lives only in the `eval` dependency group.

**What the experiment tells us to improve.** Look-alike strength matters most once registry status is equal. A reasonable next step is to tune the rule's look-alike weight and rank scaling against attack-time data, not to replace the rule with a model.
