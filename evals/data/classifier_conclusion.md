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
