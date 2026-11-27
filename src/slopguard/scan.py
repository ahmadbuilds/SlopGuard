"""Injected orchestration for one pull-request dependency scan."""

from __future__ import annotations

import tomllib
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import TypedDict

from . import __version__
from .config import ConfigError, GitSettings, SlopGuardConfig, parse_config
from .contracts import Decision, DependencyResult, ScanReport, ScanWarning
from .extractor import (
    ExtractionWarning,
    extract_dependencies,
    local_modules_from_paths,
    local_test_modules_by_directory,
)
from .git import GitRepository, GitWarning
from .registry import RegistryClient, RegistryLookupResult
from .registry_result import apply_registry_lookup
from .scoring import score_result
from .similarity import PopularPackageIndex, apply_similarity, find_lookalikes

DEFAULT_CONFIG_FILES = ("slopguard.toml", "pyproject.toml")
_DECISION_ORDER: dict[str, int] = {"pass": 0, "warn": 1, "block": 2}
_SEVERITIES = ("info", "low", "medium", "high", "critical")


class _EffectiveConfig(TypedDict):
    config: SlopGuardConfig
    source: str
    project_name: str | None


def run_scan(
    repo: GitRepository,
    *,
    repository: str,
    base: str,
    head: str,
    client: RegistryClient,
    now: datetime,
    config_path: str | None = None,
) -> ScanReport:
    """Run one deterministic scan using the pull request base-tip policy."""

    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be a timezone-aware UTC datetime.")
    repo.ensure_commits(base, head)
    base_paths = set(repo.list_files(base))
    config_error: ConfigError | None = None
    try:
        base_effective = _effective_config(repo, base, base_paths, config_path)
        fetch_settings = base_effective["config"].git
    except ConfigError as error:
        config_error = error
        fetch_settings = GitSettings()
    merge_base = repo.merge_base(base, head, fetch_depth=fetch_settings.fetch_depth)
    if config_error is not None:
        raise config_error
    head_paths = repo.list_files(head)
    warnings: list[ScanWarning] = []

    try:
        head_effective = _effective_config(repo, head, set(head_paths), config_path)
        config_changed = base_effective["config"] != head_effective["config"]
    except (ConfigError, ValueError):
        config_changed = True
        warnings.append(
            _warning(
                config_path or "slopguard.toml",
                None,
                "Head configuration could not be parsed; the base-tip policy remains in force.",
                "config_error",
            )
        )

    changed_files, git_warnings = repo.changed_files(merge_base, head)
    warnings.extend(_from_git_warning(item) for item in git_warnings)
    local_modules = local_modules_from_paths(
        head_paths,
        project_name=base_effective["project_name"],
    )
    extracted = extract_dependencies(
        changed_files,
        local_modules=local_modules,
        local_modules_by_test_directory=local_test_modules_by_directory(head_paths),
    )
    warnings.extend(_from_extraction_warning(item) for item in extracted.warnings)

    candidates = tuple(sorted(extracted.candidates, key=lambda item: item["normalized_name"]))
    lookup_client = client.with_settings(base_effective["config"].registry)
    lookup_results = _lookup_candidates(candidates, lookup_client, base_effective["config"])
    index = PopularPackageIndex.load()
    results: list[DependencyResult] = []
    for candidate in candidates:
        try:
            result = score_candidate(
                candidate,
                lookup_results[candidate["normalized_name"]],
                config=base_effective["config"],
                index=index,
                now=now,
            )
        except ValueError as error:
            locations = candidate.get("source_locations", [])
            location = (
                locations[0]
                if isinstance(locations, list) and locations and isinstance(locations[0], dict)
                else {"file": "", "line": None}
            )
            warnings.append(
                _warning(
                    str(location.get("file", "")),
                    location.get("line") if isinstance(location.get("line"), int) else None,
                    f"Dependency candidate {candidate['requested_name']!r} could not be evaluated: {error}",
                    "candidate_error",
                )
            )
            continue
        results.append(result)

    for entry in base_effective["config"].expired_allowlist_entries(now):
        warnings.append(
            _warning(
                "",
                None,
                f"Allow-list exception for {entry.name!r} expired on {entry.expires.isoformat()}.",
                "expired_allowlist",
            )
        )
    warnings.sort(key=lambda item: (item["file"], item["line"] or 0, item["kind"], item["message"]))
    results.sort(key=lambda item: item["normalized_name"])
    return _make_report(
        results=results,
        warnings=warnings,
        repository=repository,
        base=base,
        merge_base=merge_base,
        head=head,
        config_source=base_effective["source"],
        config_changed=config_changed,
        lookup_used=min(len(candidates), base_effective["config"].registry.max_lookups),
        lookup_budget=base_effective["config"].registry.max_lookups,
        now=now,
    )


def score_candidate(
    candidate: DependencyResult,
    lookup: RegistryLookupResult,
    *,
    config: SlopGuardConfig,
    index: PopularPackageIndex,
    now: datetime,
) -> DependencyResult:
    """Apply recorded lookup, similarity and risk scoring to one candidate."""

    enriched = apply_registry_lookup(candidate, lookup)
    matches = find_lookalikes(enriched["normalized_name"], index, config.similarity)
    compared = apply_similarity(enriched, matches)
    scored = score_result(compared, config, now=now)
    if "decision" not in scored:
        raise RuntimeError("Scoring returned a result without a decision.")
    return scored


def _effective_config(
    repo: GitRepository,
    commit: str,
    paths: set[str],
    config_path: str | None,
) -> _EffectiveConfig:
    selected: str | None = None
    if config_path is not None:
        selected = PurePosixPath(config_path.replace("\\", "/")).as_posix()
        if selected not in paths:
            raise ConfigError(selected, "config.path", "configuration file is missing", None)
    else:
        selected = next(
            (candidate for candidate in DEFAULT_CONFIG_FILES if candidate in paths), None
        )

    project_name: str | None = None
    if selected is None:
        return {"config": SlopGuardConfig(), "source": "defaults", "project_name": None}
    content, warning = repo.read_blob(commit, selected)
    if content is None:
        detail = warning.message if warning is not None else "configuration file could not be read"
        raise ConfigError(selected, "config.path", detail, None)
    kind = "pyproject" if PurePosixPath(selected).name == "pyproject.toml" else "slopguard"
    config = parse_config(content, source=selected, kind=kind)
    pyproject_content = content if kind == "pyproject" else None
    if pyproject_content is None and "pyproject.toml" in paths:
        pyproject_content, _warning = repo.read_blob(commit, "pyproject.toml")
    if pyproject_content is not None:
        try:
            document = tomllib.loads(pyproject_content)
        except (tomllib.TOMLDecodeError, RecursionError):
            document = {}
        project = document.get("project")
        if isinstance(project, dict) and isinstance(project.get("name"), str):
            project_name = project["name"]
    return {"config": config, "source": selected, "project_name": project_name}


def _lookup_candidates(
    candidates: tuple[DependencyResult, ...],
    client: RegistryClient,
    config: SlopGuardConfig,
) -> dict[str, RegistryLookupResult]:
    budget = config.registry.max_lookups
    allowed = candidates[:budget]
    results: dict[str, RegistryLookupResult] = {}
    with ThreadPoolExecutor(max_workers=config.registry.max_concurrency) as executor:
        pending: dict[Future[RegistryLookupResult], str] = {
            executor.submit(client.lookup, item["normalized_name"]): item["normalized_name"]
            for item in allowed
        }
        for future in as_completed(pending):
            name = pending[future]
            try:
                results[name] = future.result()
            except Exception as error:
                results[name] = _transient_result(
                    name, f"Registry client failed: {type(error).__name__}."
                )
    for candidate in candidates[budget:]:
        name = candidate["normalized_name"]
        results[name] = _transient_result(name, "Per-scan PyPI lookup budget exhausted.")
    return results


def _transient_result(name: str, error: str) -> RegistryLookupResult:
    return RegistryLookupResult(
        normalized_name=name,
        status="transient_error",
        metadata=None,
        latency_ms=0,
        attempts=1,
        http_status=None,
        retry_after=None,
        rate_limit_remaining=None,
        error=error,
    )


def _from_git_warning(warning: GitWarning) -> ScanWarning:
    return _warning(warning.file, warning.line, warning.message, warning.kind)


def _from_extraction_warning(warning: ExtractionWarning) -> ScanWarning:
    return _warning(warning.file, warning.line, warning.message, "extraction")


def _warning(file: str, line: int | None, message: str, kind: str) -> ScanWarning:
    return {"file": file, "line": line, "message": message, "kind": kind}


def _make_report(
    *,
    results: list[DependencyResult],
    warnings: list[ScanWarning],
    repository: str,
    base: str,
    merge_base: str,
    head: str,
    config_source: str,
    config_changed: bool,
    lookup_used: int,
    lookup_budget: int,
    now: datetime,
) -> ScanReport:
    decision_counts: dict[Decision, int] = {"pass": 0, "warn": 0, "block": 0}
    severity_counts = {severity: 0 for severity in _SEVERITIES}
    decisions: list[Decision] = []
    for result in results:
        decision = result["decision"]
        decisions.append(decision)
        decision_counts[decision] += 1
        severity_counts[result["severity"]] += 1
    decision: Decision = (
        max(decisions, key=lambda value: _DECISION_ORDER[value]) if decisions else "pass"
    )
    if decision == "pass" and any(warning["kind"] == "candidate_error" for warning in warnings):
        decision = "warn"
    return {
        "contract_version": "1.0.0",
        "tool_version": __version__,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "repository": repository,
        "base_sha": base,
        "merge_base_sha": merge_base,
        "head_sha": head,
        "config_source": config_source,
        "config_changed": config_changed,
        "decision": decision,
        "counts": {"by_decision": decision_counts, "by_severity": severity_counts},
        "results": results,
        "warnings": warnings,
        "lookups": {"used": lookup_used, "budget": lookup_budget},
    }
