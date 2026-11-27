"""Known-answer tests for evaluation confusion counts and metric denominators."""

from __future__ import annotations

from evals.metrics import binary_metrics, evaluation_metrics


def test_metrics_match_hand_built_confusion_matrix() -> None:
    rows = (
        {"expected": "flag", "decision": "warn", "category": "a"},
        {"expected": "flag", "decision": "pass", "category": "a"},
        {"expected": "pass", "decision": "block", "category": "b"},
        {"expected": "pass", "decision": "pass", "category": "b"},
    )

    result = evaluation_metrics(rows, excluded_count=1)

    warn_block = result["flagged_warn_or_block"]
    assert warn_block == {
        "confusion": {"tp": 1, "fp": 1, "tn": 1, "fn": 1},
        "precision": 0.5,
        "recall": 0.5,
        "false_positive_rate": 0.5,
        "f1": 0.5,
    }
    assert result["excluded_count"] == 1
    assert result["by_category"]["a"]["count"] == 2


def test_zero_denominators_return_none() -> None:
    result = binary_metrics((), flagged_decisions=frozenset({"warn", "block"}))

    assert result["confusion"] == {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["false_positive_rate"] is None
    assert result["f1"] is None
