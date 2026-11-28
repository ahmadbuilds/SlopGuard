"""Pure GitHub job-summary and workflow-annotation rendering."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from .config import OutputSettings
from .contracts import DependencyResult, PolicySummary, ScanReport

# GitHub limits a job-summary step to 1 MiB; this platform ceiling is not configurable.
MAX_JOB_SUMMARY_BYTES = 1_048_576
# GitHub limits issue comment bodies to 65,536 characters.
MAX_COMMENT_CHARACTERS = 65_536
COMMENT_MARKER = "<!-- slopguard:comment -->"


@dataclass(frozen=True, slots=True)
class RenderContext:
    """External links and checkout-relative path context prepared by the CLI."""

    run_url: str | None
    docs_url: str | None
    path_prefix: str


def _counted_items(count: int, singular: str, plural: str) -> str:
    """Format a count with the matching singular or plural noun."""

    noun = singular if count == 1 else plural
    return f"{count} {noun}"


def render_summary(report: ScanReport, *, context: RenderContext, settings: OutputSettings) -> str:
    """Render a bounded Markdown summary without reading external state."""

    counts = report["counts"]
    decisions = counts["by_decision"]
    severities = counts["by_severity"]
    policy = report["policy"]
    dependency_count = sum(decisions.values())
    lines = [
        f"# SlopGuard: {_markdown(report['decision'].upper(), settings)}",
        "",
        (
            f"Found {_counted_items(dependency_count, 'dependency', 'dependencies')}: "
            f"{decisions['pass']} pass, "
            f"{decisions['warn']} warn, and {decisions['block']} block."
        ),
        "",
        (
            f"Policy: mode {_markdown(policy['mode'], settings)}; warn at "
            f"{policy['warn_threshold']:g}; block at {policy['block_threshold']:g}; "
            f"unknown results {_markdown(policy['on_unknown'], settings)}; config source "
            f"{_markdown(report['config_source'], settings)}."
        ),
    ]
    if report["config_changed"]:
        lines.append(
            "The configuration changed in this pull request; the base-branch policy was used."
        )
    if not report["results"] and not report["warnings"]:
        footer = _footer(report["tool_version"], context, settings)
        compact = "\n".join((*lines, "", footer))
        return _truncate_summary(compact, context, settings)
    lines.extend(
        (
            "",
            "Counts by decision: "
            + ", ".join(f"{key} {decisions[key]}" for key in ("pass", "warn", "block"))
            + ".",
            "Counts by severity: "
            + ", ".join(
                f"{key} {severities[key]}" for key in ("info", "low", "medium", "high", "critical")
            )
            + ".",
            "",
            "## Unverified package metadata",
            "",
        )
    )
    unverified = [item for item in report["results"] if _is_unverified(item)]
    if not unverified:
        lines.append("All dependency metadata was verified.")
    else:
        for item in unverified[: settings.max_unverified_in_summary]:
            lines.append(
                f"- {_markdown(item['requested_name'], settings)}: "
                f"{_markdown(_registry_state(item), settings)}; "
                f"policy decision {_markdown(item.get('decision', 'not evaluated'), settings)}."
            )
        lines.append(
            "Unknown metadata is unverified, not evidence that the package does not exist."
        )
        omitted = max(0, len(unverified) - settings.max_unverified_in_summary)
        if omitted:
            lines.append(
                f"{_counted_items(omitted, 'additional unverified result', 'additional unverified results')} omitted."
            )

    findings = sorted(
        (item for item in report["results"] if item.get("decision") in {"block", "warn"}),
        key=lambda item: (
            0 if item.get("decision") == "block" else 1,
            -float(item["risk_score"]),
            item["normalized_name"],
        ),
    )
    included_findings = findings[: settings.max_findings_in_summary]
    for item in included_findings:
        decision = item["decision"]
        assert decision in {"block", "warn"}
        lines.extend(
            (
                "",
                f"## {_markdown(decision.title(), settings)} finding: "
                f"{_markdown(item['requested_name'], settings)}",
            )
        )
        lines.extend(_finding_lines(item, policy, settings))
    if len(findings) > len(included_findings):
        omitted = len(findings) - len(included_findings)
        lines.extend(
            (
                "",
                f"{_counted_items(omitted, 'additional finding', 'additional findings')} omitted.",
            )
        )

    passed = [item for item in report["results"] if item.get("decision") == "pass"]
    if passed:
        lines.extend(
            (
                "",
                "<details>",
                f"<summary>Passed {_counted_items(len(passed), 'dependency', 'dependencies')}</summary>",
                "",
            )
        )
        lines.extend(_passed_line(item, settings) for item in passed)
        lines.extend(("", "</details>"))

    if report["warnings"]:
        lines.extend(("", "## Scan warnings"))
        for warning in report["warnings"]:
            location = warning["file"] or "scan"
            if warning["line"] is not None:
                location += f":{warning['line']}"
            lines.append(
                f"- {_markdown(location, settings)}: {_markdown(warning['message'], settings)}"
            )

    lines.extend(("", _footer(report["tool_version"], context, settings)))
    rendered = "\n".join(lines)
    return _truncate_summary(rendered, context, settings)


def render_comment(report: ScanReport, *, context: RenderContext, settings: OutputSettings) -> str:
    """Render the summary as a bounded sticky-comment body with a stable marker."""

    content = f"{COMMENT_MARKER}\n\n{render_summary(report, context=context, settings=settings)}"
    if len(content) <= MAX_COMMENT_CHARACTERS:
        return content
    note = _truncation_note(context, settings)
    available = MAX_COMMENT_CHARACTERS - len(note)
    return content[:available].rstrip() + note


def _finding_lines(
    item: DependencyResult, policy: PolicySummary, settings: OutputSettings
) -> list[str]:
    requested = _markdown(item["requested_name"], settings)
    normalized = _markdown(item["normalized_name"], settings)
    lines = [f"- Requested name: {requested}; normalized name: {normalized}."]
    locations = item.get("source_locations", [])
    if locations:
        formatted = [
            f"{_markdown(location['file'], settings)}:{location['line']} "
            f"({_markdown(location['source_type'], settings)})"
            for location in locations
        ]
        lines.append("- Source locations: " + ", ".join(formatted) + ".")
    lines.append(f"- Registry state: {_markdown(_registry_state(item), settings)}.")

    candidates = item.get("similarity_candidates", [])

    def match_order(candidate: dict[str, object]) -> tuple[int, int, str]:
        distance = candidate.get("edit_distance")
        rank = candidate.get("popularity_rank")
        return (
            distance if isinstance(distance, int) else 1_000_000,
            rank if isinstance(rank, int) else 1_000_000,
            str(candidate["name"]),
        )

    closest = min(
        candidates,
        key=match_order,
        default=None,
    )
    if closest is not None:
        distance = closest.get("edit_distance")
        rank = closest.get("popularity_rank")
        if distance is not None and rank is not None:
            lines.append(
                f"- Closest look-alike: {_markdown(closest['name'], settings)} "
                f"(distance {distance}, rank {rank})."
            )
        else:
            lines.append(f"- Closest look-alike: {_markdown(closest['name'], settings)}.")

    lines.append(
        f"- Score {item['risk_score']:g} (warn at {policy['warn_threshold']:g}, "
        f"block at {policy['block_threshold']:g})."
    )
    # Zero-point signals stay in the JSON report; the summary lists only what moved the score.
    applied = [
        contribution
        for contribution in item.get("score_contributions", [])
        if contribution["points"] != 0
    ]
    for contribution in applied:
        lines.append(
            f"- {_markdown(contribution['signal'], settings)}: {contribution['points']:+g} - "
            f"{_markdown(contribution['summary'], settings)}"
        )
    if not applied:
        lines.append("- No scoring signal added points.")

    metadata = item.get("registry_metadata")
    if metadata is not None:
        source_present = bool(metadata["project_urls"] or metadata["source_hints"])
        facts = (
            f"first release {_markdown(metadata['first_release_at'] or 'unknown', settings)}, "
            f"release count {metadata['release_count'] if metadata['release_count'] is not None else 'unknown'}, "
            f"source link {'yes' if source_present else 'no'}"
        )
        if metadata["maintainer_hints"]:
            maintainers = ", ".join(
                _markdown(value, settings) for value in metadata["maintainer_hints"]
            )
            facts += f"; maintainers: {maintainers}"
        if metadata["project_urls"]:
            labels = ", ".join(_markdown(value, settings) for value in metadata["project_urls"])
            facts += f"; project URL labels: {labels}"
        lines.append(f"- Registry metadata: {facts}.")

    lines.append(f"- Remediation: {_markdown(item['remediation'], settings)}")
    allowlist = item.get("allowlist")
    if allowlist is not None:
        lines.append(
            f"- Allow-list exception: {_markdown(allowlist['reason'], settings)}; "
            f"{_expiry_text(allowlist['expires'], settings)}."
        )
    return lines


def _is_unverified(item: DependencyResult) -> bool:
    """Return whether PyPI was consulted but could not confirm the package's state.

    Active allow-list entries are deliberately not looked up, so they are reported as
    allow-listed rather than as unverified registry results.
    """

    return item["registry_exists"] is None and not item.get("allowlisted")


def _expiry_text(expires: str | None, settings: OutputSettings) -> str:
    return "no expiry" if expires is None else f"expires {_markdown(expires, settings)}"


def _passed_line(item: DependencyResult, settings: OutputSettings) -> str:
    line = f"- {_markdown(item['requested_name'], settings)}"
    allowlist = item.get("allowlist")
    if allowlist is not None:
        line += (
            f" (allow-listed: {_markdown(allowlist['reason'], settings)}; "
            f"{_expiry_text(allowlist['expires'], settings)})"
        )
    elif item.get("allowlisted"):
        line += " (allow-listed)"
    return line


def _registry_state(item: DependencyResult) -> str:
    if item.get("allowlisted"):
        return "not looked up: allow-listed"
    status = item.get("registry_status")
    if status == "invalid_name":
        return "not a valid distribution name"
    exists = item.get("registry_exists")
    if exists is True:
        return "exists"
    if exists is False:
        return "does not exist on PyPI"
    return f"could not be verified ({status or 'unknown status'})"


def render_annotations(
    report: ScanReport, *, context: RenderContext, settings: OutputSettings
) -> list[str]:
    """Render bounded GitHub workflow commands for blocking, warning, and unknown results."""

    command_order: dict[str, int] = {"error": 0, "warning": 1, "notice": 2}
    annotated: list[tuple[str, DependencyResult]] = []
    for item in report["results"]:
        decision = item.get("decision")
        if decision == "block":
            annotated.append(("error", item))
        elif decision == "warn":
            annotated.append(("warning", item))
        elif decision == "pass" and _is_unverified(item):
            annotated.append(("notice", item))
    annotated.sort(
        key=lambda pair: (
            command_order[pair[0]],
            -float(pair[1]["risk_score"]),
            pair[1]["normalized_name"],
        )
    )
    output = [
        _annotation(command, item, context, settings)
        for command, item in annotated[: settings.max_annotations]
    ]
    omitted = len(annotated) - min(len(annotated), settings.max_annotations)
    if omitted and settings.max_annotations > 0:
        omitted_label = _counted_items(omitted, "more finding", "more findings")
        output.append(f"::notice::{_escape_annotation(f'{omitted_label} in the job summary')}")
    return output


def _annotation(
    command: str,
    item: DependencyResult,
    context: RenderContext,
    settings: OutputSettings,
) -> str:
    properties = []
    locations = item.get("source_locations", [])
    if locations:
        file_path = (
            f"{context.path_prefix}/{locations[0]['file']}"
            if context.path_prefix
            else locations[0]["file"]
        )
        properties.append(
            f"file={_escape_annotation(file_path[: settings.max_field_characters], property_value=True)}"
        )
        properties.append(f"line={locations[0]['line']}")
    title_value = f"SlopGuard: {item['requested_name']}"[: settings.max_field_characters]
    title = _escape_annotation(title_value, property_value=True)
    properties.append(f"title={title}")
    message_parts = [
        _registry_state(item),
        f"score {item['risk_score']:g}; decision {item.get('decision', 'not evaluated')}",
        item["remediation"],
    ]
    message = _escape_annotation("; ".join(message_parts)[: settings.max_field_characters])
    property_text = " " + ",".join(properties) if properties else ""
    return f"::{command}{property_text}::{message}"


def _escape_annotation(value: str, *, property_value: bool = False) -> str:
    escaped: list[str] = []
    for character in value:
        if character == "%":
            escaped.append("%25")
        elif character == "\r":
            escaped.append("%0D")
        elif character == "\n":
            escaped.append("%0A")
        elif unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            continue
        elif property_value and character == ":":
            escaped.append("%3A")
        elif property_value and character == ",":
            escaped.append("%2C")
        else:
            escaped.append(character)
    return "".join(escaped)


def _markdown(value: object, settings: OutputSettings) -> str:
    """Escape a report-derived value as bounded Markdown text."""

    text = str(value)[: settings.max_field_characters]
    escaped: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if character in "\r\n":
            escaped.append("\\n")
        elif category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            escaped.append(f"\\u{ord(character):04x}")
        elif character in "\\`*_{}[]()#+-.!|><~":
            escaped.append("\\" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def _link_destination(value: str, max_characters: int) -> str:
    return quote(value[:max_characters], safe=":/?#@!$&'*+,;=%")


def _footer(tool_version: str, context: RenderContext, settings: OutputSettings) -> str:
    footer = [f"SlopGuard {_markdown(tool_version, settings)}."]
    if context.run_url:
        footer.append(
            f"[Workflow run]({_link_destination(context.run_url, settings.max_field_characters)})."
        )
    if context.docs_url:
        footer.append(
            f"[Documentation]({_link_destination(context.docs_url, settings.max_field_characters)})."
        )
    return " ".join(footer)


def _truncate_summary(rendered: str, context: RenderContext, settings: OutputSettings) -> str:
    """Apply GitHub's byte limit without cutting a UTF-8 character or losing the footer note."""

    data = rendered.encode("utf-8")
    if len(data) <= MAX_JOB_SUMMARY_BYTES:
        return rendered
    note = _truncation_note(context, settings)
    budget = MAX_JOB_SUMMARY_BYTES - len(note.encode("utf-8"))
    prefix_data = data[:budget]
    closing = ""
    if b"<details>" in prefix_data and b"</details>" not in prefix_data:
        closing = "\n</details>"
        budget -= len(closing.encode("utf-8"))
        prefix_data = data[:budget]
        if b"<details>" not in prefix_data or b"</details>" in prefix_data:
            closing = ""
    prefix = prefix_data.decode("utf-8", errors="ignore")
    return prefix + closing + note


def _truncation_note(context: RenderContext, settings: OutputSettings) -> str:
    if context.run_url:
        run_url = _link_destination(context.run_url, settings.max_field_characters)
        return (
            "\n\nSummary truncated; see the "
            f"[workflow run]({run_url}) or JSON report for full details."
        )
    return "\n\nSummary truncated; see the JSON report for full details."
