"""Pure confusion-matrix metrics for binary package-risk evaluations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

FLAGGED_WARN_OR_BLOCK = frozenset({"warn", "block"})
FLAGGED_BLOCK_ONLY = frozenset({"block"})


def format_metric(value: object) -> str:
    """Format a metric for Markdown with the shared four-decimal convention."""
    return "n/a" if value is None else f"{float(value):.4f}"


def binary_metrics(
    rows: Iterable[Mapping[str, str]],
    *,
    flagged_decisions: frozenset[str],
) -> dict[str, object]:
    """Compute binary confusion counts and metrics from expected labels and decisions."""

    counts = {key: 0 for key in ("tp", "fp", "tn", "fn")}
    for row in rows:
        expected = row["expected"]
        decision = row["decision"]
        if expected not in {"flag", "pass"}:
            raise ValueError("binary metrics require expected='flag' or expected='pass'")
        predicted_flag = decision in flagged_decisions
        if expected == "flag" and predicted_flag:
            counts["tp"] += 1
        elif expected == "pass" and predicted_flag:
            counts["fp"] += 1
        elif expected == "pass":
            counts["tn"] += 1
        else:
            counts["fn"] += 1
    true_positive = counts["tp"]
    false_positive = counts["fp"]
    true_negative = counts["tn"]
    false_negative = counts["fn"]
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    false_positive_rate = _ratio(false_positive, false_positive + true_negative)
    f1 = (
        _ratio(2 * precision * recall, precision + recall)
        if precision is not None and recall is not None
        else None
    )
    return {
        "confusion": counts,
        "precision": precision,
        "recall": recall,
        "false_positive_rate": false_positive_rate,
        "f1": f1,
    }


def evaluation_metrics(
    rows: Iterable[Mapping[str, str]], *, excluded_count: int
) -> dict[str, object]:
    """Compute both flagging thresholds and per-category views for labeled predictions."""

    values = tuple(rows)
    if excluded_count < 0:
        raise ValueError("excluded_count must be non-negative")
    categories: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in values:
        category = row.get("category")
        if not category:
            raise ValueError("each metrics row requires a category")
        categories[category].append(row)
    return {
        "included_count": len(values),
        "excluded_count": excluded_count,
        "flagged_warn_or_block": binary_metrics(values, flagged_decisions=FLAGGED_WARN_OR_BLOCK),
        "flagged_block": binary_metrics(values, flagged_decisions=FLAGGED_BLOCK_ONLY),
        "by_category": {
            category: {
                "count": len(category_rows),
                "flagged_warn_or_block": binary_metrics(
                    category_rows, flagged_decisions=FLAGGED_WARN_OR_BLOCK
                ),
                "flagged_block": binary_metrics(
                    category_rows, flagged_decisions=FLAGGED_BLOCK_ONLY
                ),
            }
            for category, category_rows in sorted(categories.items())
        },
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None
