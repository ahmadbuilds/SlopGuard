"""Pure parsing, validation and discovery for SlopGuard configuration."""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from packaging.utils import InvalidName, canonicalize_name

from .similarity import SimilaritySettings

# Bound parsing work for configuration files supplied by a repository.
MAX_CONFIG_BYTES = 1_000_000
MAX_CONFIG_VALUE_REPR = 100
# Hard safety ceilings for repository-controlled registry policy values.
MAX_REGISTRY_TIMEOUT_SECONDS = 10.0
MAX_REGISTRY_RETRIES = 2
MAX_REGISTRY_BACKOFF_SECONDS = 1.0
MAX_REGISTRY_CONCURRENCY = 32
MAX_REGISTRY_LOOKUPS = 10_000
MAX_GIT_FETCH_DEPTH = 10_000


def _short_value_repr(value: object) -> str:
    """Render a bounded value fragment for configuration diagnostics."""

    rendered = "<table>" if isinstance(value, Mapping) else repr(value)
    if len(rendered) <= MAX_CONFIG_VALUE_REPR:
        return rendered
    return rendered[: MAX_CONFIG_VALUE_REPR - 3] + "..."


class ConfigError(ValueError):
    """Raised for invalid configuration, with source and key context."""

    def __init__(self, source: str, dotted_key: str, problem: str, value: object) -> None:
        super().__init__(f"{source}: {dotted_key}: {problem} (got {_short_value_repr(value)})")
        self.source = source
        self.dotted_key = dotted_key
        self.problem = problem
        self.value = value


class _SettingError(ValueError):
    """Internal validation failure carrying a setting key and its bad value."""

    def __init__(self, key: str, problem: str, value: object) -> None:
        super().__init__(problem)
        self.key = key
        self.problem = problem
        self.value = value


@dataclass(frozen=True, slots=True)
class RegistrySettings:
    """Validated timing and retry policy for PyPI registry requests."""

    timeout_seconds: float = 3.0
    max_retries: int = 1
    backoff_seconds: float = 0.1
    max_backoff_seconds: float = 0.5
    max_concurrency: int = 8
    max_lookups: int = 500

    def __post_init__(self) -> None:
        _validate_number(
            "timeout_seconds",
            self.timeout_seconds,
            minimum=0,
            maximum=MAX_REGISTRY_TIMEOUT_SECONDS,
        )
        if self.timeout_seconds <= 0:
            raise _SettingError("timeout_seconds", "must be greater than 0", self.timeout_seconds)
        _validate_integer("max_retries", self.max_retries, minimum=0)
        if self.max_retries > MAX_REGISTRY_RETRIES:
            raise _SettingError(
                "max_retries", f"must be at most {MAX_REGISTRY_RETRIES}", self.max_retries
            )
        _validate_number(
            "backoff_seconds",
            self.backoff_seconds,
            minimum=0,
            maximum=MAX_REGISTRY_BACKOFF_SECONDS,
        )
        _validate_number(
            "max_backoff_seconds",
            self.max_backoff_seconds,
            minimum=0,
            maximum=MAX_REGISTRY_BACKOFF_SECONDS,
        )
        if self.backoff_seconds > self.max_backoff_seconds:
            raise _SettingError(
                "backoff_seconds",
                "cannot exceed max_backoff_seconds",
                self.backoff_seconds,
            )
        _validate_integer("max_concurrency", self.max_concurrency, minimum=1)
        if self.max_concurrency > MAX_REGISTRY_CONCURRENCY:
            raise _SettingError(
                "max_concurrency",
                f"must be at most {MAX_REGISTRY_CONCURRENCY}",
                self.max_concurrency,
            )
        _validate_integer("max_lookups", self.max_lookups, minimum=0)
        if self.max_lookups > MAX_REGISTRY_LOOKUPS:
            raise _SettingError(
                "max_lookups", f"must be at most {MAX_REGISTRY_LOOKUPS}", self.max_lookups
            )


@dataclass(frozen=True, slots=True)
class GitSettings:
    """Bounds for recovering merge bases in shallow Git checkouts."""

    fetch_depth: int = 1000

    def __post_init__(self) -> None:
        _validate_integer("fetch_depth", self.fetch_depth, minimum=1)
        if self.fetch_depth > MAX_GIT_FETCH_DEPTH:
            raise _SettingError(
                "fetch_depth", f"must be at most {MAX_GIT_FETCH_DEPTH}", self.fetch_depth
            )


def _validate_number(
    key: str,
    value: object,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _SettingError(key, "must be a number", value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise _SettingError(key, f"must be between {minimum:g} and {maximum:g}", value)


def _validate_integer(
    key: str,
    value: object,
    *,
    minimum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _SettingError(key, "must be an integer", value)
    if value < minimum:
        raise _SettingError(key, f"must be at least {minimum}", value)


def _validate_now(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be a timezone-aware UTC datetime.")


@dataclass(frozen=True, slots=True)
class PolicySettings:
    """Policy thresholds and treatment of incomplete registry evidence."""

    mode: Literal["warn", "block"] = "warn"
    warn_threshold: float = 40
    block_threshold: float = 80
    on_unknown: Literal["warn", "pass"] = "warn"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in {"warn", "block"}:
            raise _SettingError("mode", 'must be "warn" or "block"', self.mode)
        if not isinstance(self.on_unknown, str) or self.on_unknown not in {"warn", "pass"}:
            raise _SettingError("on_unknown", 'must be "warn" or "pass"', self.on_unknown)
        _validate_number("warn_threshold", self.warn_threshold, minimum=0, maximum=100)
        _validate_number("block_threshold", self.block_threshold, minimum=0, maximum=100)
        if self.warn_threshold > self.block_threshold:
            raise _SettingError(
                "warn_threshold",
                "must not exceed block_threshold",
                self.warn_threshold,
            )


@dataclass(frozen=True, slots=True)
class SeveritySettings:
    """Ascending score lower bounds for the four non-info severity levels."""

    low: float = 20
    medium: float = 40
    high: float = 60
    critical: float = 80

    def __post_init__(self) -> None:
        values = (
            ("low", self.low),
            ("medium", self.medium),
            ("high", self.high),
            ("critical", self.critical),
        )
        for key, value in values:
            _validate_number(key, value, minimum=0, maximum=100)
        for (previous_key, previous), (key, value) in zip(values, values[1:], strict=False):
            if value <= previous:
                raise _SettingError(key, f"must be greater than {previous_key}", value)


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """Maximum points assigned to each independent evidence signal."""

    not_found: float = 60
    lookalike: float = 35
    new_package: float = 25
    few_releases: float = 10
    missing_source: float = 10

    def __post_init__(self) -> None:
        for item in fields(self):
            _validate_number(item.name, getattr(self, item.name), minimum=0, maximum=100)


@dataclass(frozen=True, slots=True)
class ScoringSignals:
    """Evidence windows and multipliers used to scale scoring weights."""

    new_package_days: int = 30
    few_releases_max: int = 1
    distance_factors: tuple[tuple[int, float], ...] = ((1, 1.0), (2, 0.5))
    separator_factor: float = 1.0
    full_strength_rank: int = 100
    full_rank_factor: float = 1.0
    min_rank_factor: float = 0.3

    def __post_init__(self) -> None:
        _validate_integer("new_package_days", self.new_package_days, minimum=0)
        _validate_integer("few_releases_max", self.few_releases_max, minimum=0)
        _validate_integer("full_strength_rank", self.full_strength_rank, minimum=1)
        _validate_number("separator_factor", self.separator_factor, minimum=0, maximum=1)
        _validate_number("full_rank_factor", self.full_rank_factor, minimum=0, maximum=1)
        _validate_number("min_rank_factor", self.min_rank_factor, minimum=0, maximum=1)
        if self.min_rank_factor > self.full_rank_factor:
            raise _SettingError(
                "min_rank_factor", "must not exceed full_rank_factor", self.min_rank_factor
            )
        if not isinstance(self.distance_factors, tuple) or not self.distance_factors:
            raise _SettingError(
                "distance_factors", "must be a non-empty tuple of pairs", self.distance_factors
            )
        previous_distance = 0
        for pair in self.distance_factors:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise _SettingError(
                    "distance_factors", "entries must be (distance, factor) pairs", pair
                )
            distance, factor = pair
            _validate_integer("distance_factors", distance, minimum=1)
            _validate_number("distance_factors", factor, minimum=0, maximum=1)
            if distance <= previous_distance:
                raise _SettingError("distance_factors", "distances must be sorted", pair)
            previous_distance = distance


@dataclass(frozen=True, slots=True)
class ScoringSettings:
    """Groups for severity bounds, signal weights and signal parameters."""

    severity: SeveritySettings = field(default_factory=SeveritySettings)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    signals: ScoringSignals = field(default_factory=ScoringSignals)

    def __post_init__(self) -> None:
        expected_types = {
            "severity": SeveritySettings,
            "weights": ScoringWeights,
            "signals": ScoringSignals,
        }
        for key, expected_type in expected_types.items():
            if not isinstance(getattr(self, key), expected_type):
                raise _SettingError(key, "must be a validated settings table", getattr(self, key))


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    """A canonical package name permitted by an explained optional exception."""

    name: str
    reason: str
    expires: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("allow-list name must be a non-empty package name")
        try:
            normalized_name = canonicalize_name(self.name, validate=True)
        except InvalidName as error:
            raise ValueError("allow-list name must be a valid package name") from error
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("allow-list reason must be non-empty")
        if isinstance(self.expires, datetime) or (
            self.expires is not None and not isinstance(self.expires, date)
        ):
            raise ValueError("allow-list expiry must be a TOML date or null")
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "reason", self.reason.strip())


@dataclass(frozen=True, slots=True)
class SlopGuardConfig:
    """Validated, immutable settings for scoring and policy decisions."""

    version: int = 1
    policy: PolicySettings = field(default_factory=PolicySettings)
    registry: RegistrySettings = field(default_factory=RegistrySettings)
    git: GitSettings = field(default_factory=GitSettings)
    scoring: ScoringSettings = field(default_factory=ScoringSettings)
    similarity: SimilaritySettings = field(default_factory=SimilaritySettings)
    allowlist: tuple[AllowlistEntry, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version != 1:
            raise ValueError("configuration version must be 1")
        if not isinstance(self.policy, PolicySettings):
            raise ValueError("policy must be PolicySettings")
        if not isinstance(self.registry, RegistrySettings):
            raise ValueError("registry must be RegistrySettings")
        if not isinstance(self.git, GitSettings):
            raise ValueError("git must be GitSettings")
        if not isinstance(self.scoring, ScoringSettings):
            raise ValueError("scoring must be ScoringSettings")
        if not isinstance(self.similarity, SimilaritySettings):
            raise ValueError("similarity must be SimilaritySettings")
        if not isinstance(self.allowlist, tuple) or any(
            not isinstance(entry, AllowlistEntry) for entry in self.allowlist
        ):
            raise ValueError("allowlist must be a tuple of AllowlistEntry values")
        names = [entry.name for entry in self.allowlist]
        if len(names) != len(set(names)):
            raise ValueError("allowlist names must be unique after canonicalization")
        allowed_distances = {distance for _, distance in self.similarity.max_distance_by_length}
        configured_distances = {distance for distance, _ in self.scoring.signals.distance_factors}
        missing_distances = sorted(allowed_distances - configured_distances)
        if missing_distances:
            distance = missing_distances[0]
            raise ConfigError(
                "<config>",
                "scoring.signals.distance_factors",
                f"missing factor for edit distance {distance}",
                distance,
            )

    def allowlist_entry_for(self, name: str, *, now: datetime) -> AllowlistEntry | None:
        """Return the active allow-list entry for a name at the supplied UTC time."""

        _validate_now(now)
        normalized_name = canonicalize_name(name, validate=True)
        return next(
            (
                entry
                for entry in self.allowlist
                if entry.name == normalized_name
                and (entry.expires is None or entry.expires >= now.date())
            ),
            None,
        )

    def expired_allowlist_entries(self, now: datetime) -> tuple[AllowlistEntry, ...]:
        """Return entries whose expiry date is earlier than the supplied UTC date."""

        _validate_now(now)
        return tuple(
            entry
            for entry in self.allowlist
            if entry.expires is not None and entry.expires < now.date()
        )


def _reject_unknown(
    table: Mapping[str, object], allowed: set[str], *, source: str, path: str
) -> None:
    unknown = set(table) - allowed
    if unknown:
        key = sorted(unknown, key=str)[0]
        raise ConfigError(source, f"{path}.{key}", "unknown key", table[key])


def _expect_table(value: object, *, source: str, key: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigError(source, key, "must be a table", value)
    return value


def _read_dataclass_values(
    model: type,
    table: Mapping[str, object],
    *,
    source: str,
    path: str,
    defaults: object,
) -> dict[str, object]:
    names = {item.name for item in fields(model)}
    _reject_unknown(table, names, source=source, path=path)
    return {name: table.get(name, getattr(defaults, name)) for name in names}


def _construct_settings(
    model: type,
    table: Mapping[str, object],
    *,
    source: str,
    path: str,
    defaults: object,
) -> object:
    values = _read_dataclass_values(model, table, source=source, path=path, defaults=defaults)
    if model is ScoringSignals and "distance_factors" in table:
        raw_factors = table["distance_factors"]
        if isinstance(raw_factors, (str, bytes)) or not isinstance(raw_factors, Sequence):
            raise ConfigError(
                source,
                f"{path}.distance_factors",
                "must be a sequence of pairs",
                raw_factors,
            )
        try:
            values["distance_factors"] = tuple(tuple(pair) for pair in raw_factors)
        except TypeError as error:
            raise ConfigError(
                source,
                f"{path}.distance_factors",
                "must be a sequence of pairs",
                raw_factors,
            ) from error
    try:
        return model(**values)
    except _SettingError as error:
        raise ConfigError(
            source,
            f"{path}.{error.key}",
            error.problem,
            error.value,
        ) from error
    except (TypeError, ValueError) as error:
        raise ConfigError(source, path, f"invalid settings: {error}", None) from error


def _parse_allowlist(value: object, *, source: str) -> tuple[AllowlistEntry, ...]:
    if not isinstance(value, list):
        raise ConfigError(source, "allowlist", "must be an array of tables", value)
    entries: list[AllowlistEntry] = []
    names: set[str] = set()
    allowed_keys = {"name", "reason", "expires"}
    for index, raw_entry in enumerate(value, start=1):
        path = f"allowlist[{index}]"
        entry = _expect_table(raw_entry, source=source, key=path)
        _reject_unknown(entry, allowed_keys, source=source, path=path)
        name = entry.get("name")
        reason = entry.get("reason")
        expires = entry.get("expires")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(source, f"{path}.name", "must be a non-empty package name", name)
        try:
            normalized_name = canonicalize_name(name, validate=True)
        except InvalidName as error:
            raise ConfigError(
                source, f"{path}.name", "must be a valid package name", name
            ) from error
        if normalized_name in names:
            raise ConfigError(source, f"{path}.name", "duplicates an earlier canonical name", name)
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(source, f"{path}.reason", "must be non-empty", reason)
        if "expires" in entry and (
            isinstance(expires, datetime) or (expires is not None and not isinstance(expires, date))
        ):
            raise ConfigError(source, f"{path}.expires", "must be a TOML date", expires)
        names.add(normalized_name)
        entries.append(AllowlistEntry(normalized_name, reason, expires))
    return tuple(entries)


def _config_document(
    document: Mapping[str, object], *, source: str, kind: Literal["slopguard", "pyproject"]
) -> Mapping[str, object]:
    """Select the config document according to its explicitly supplied kind."""

    if kind == "slopguard":
        return document
    tool = document.get("tool", {})
    if not isinstance(tool, Mapping):
        return {}
    if "slopguard" not in tool:
        return {}
    return _expect_table(tool["slopguard"], source=source, key="tool.slopguard")


def _text_location(text: str, offset: int) -> tuple[int, int]:
    prefix = text[:offset]
    line = prefix.count("\n") + 1
    column = len(prefix.rsplit("\n", 1)[-1]) + 1
    return line, column


def _toml_error_message(error: tomllib.TOMLDecodeError) -> str:
    line = getattr(error, "lineno", None)
    column = getattr(error, "colno", None)
    problem = getattr(error, "msg", None)
    if line is not None and column is not None and problem is not None:
        return f"malformed TOML at line {line}, column {column}: {problem}"
    return f"malformed TOML: {error}"


def parse_config(
    text: str,
    *,
    source: str,
    kind: Literal["slopguard", "pyproject"] = "slopguard",
) -> SlopGuardConfig:
    """Parse an explicitly identified TOML document without filesystem access."""

    if not isinstance(text, str):
        raise ConfigError(source, "config.document", "must be text", text)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as error:
        line, column = _text_location(text, error.start)
        raise ConfigError(
            source,
            "config.document",
            f"text cannot be UTF-8 encoded at line {line}, column {column}",
            None,
        ) from error
    if size > MAX_CONFIG_BYTES:
        raise ConfigError(
            source,
            "config.document",
            f"exceeds the {MAX_CONFIG_BYTES}-byte safety limit",
            f"{size} bytes",
        )
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(source, "config.document", _toml_error_message(error), None) from error
    except RecursionError as error:
        raise ConfigError(
            source, "config.document", "malformed TOML: nesting is too deep", None
        ) from error
    if not isinstance(parsed, dict):
        raise ConfigError(source, "config.document", "must contain a TOML table", parsed)
    if kind not in {"slopguard", "pyproject"}:
        raise ValueError("kind must be 'slopguard' or 'pyproject'")
    config_table = _config_document(parsed, source=source, kind=kind)
    if not config_table:
        return SlopGuardConfig()

    allowed_keys = {"version", "policy", "registry", "git", "scoring", "similarity", "allowlist"}
    _reject_unknown(config_table, allowed_keys, source=source, path="config")
    defaults = SlopGuardConfig()
    version = config_table.get("version", defaults.version)
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigError(source, "version", "must be integer 1", version)
    if version != 1:
        raise ConfigError(source, "version", "unsupported version", version)

    policy_table = _expect_table(config_table.get("policy", {}), source=source, key="policy")
    registry_table = _expect_table(config_table.get("registry", {}), source=source, key="registry")
    git_table = _expect_table(config_table.get("git", {}), source=source, key="git")
    scoring_table = _expect_table(config_table.get("scoring", {}), source=source, key="scoring")
    severity_table = _expect_table(
        scoring_table.get("severity", {}), source=source, key="scoring.severity"
    )
    weights_table = _expect_table(
        scoring_table.get("weights", {}), source=source, key="scoring.weights"
    )
    signals_table = _expect_table(
        scoring_table.get("signals", {}), source=source, key="scoring.signals"
    )
    similarity_table = _expect_table(
        config_table.get("similarity", {}), source=source, key="similarity"
    )
    _reject_unknown(
        scoring_table, {"severity", "weights", "signals"}, source=source, path="scoring"
    )

    policy = _construct_settings(
        PolicySettings,
        policy_table,
        source=source,
        path="policy",
        defaults=defaults.policy,
    )
    registry = _construct_settings(
        RegistrySettings,
        registry_table,
        source=source,
        path="registry",
        defaults=defaults.registry,
    )
    git = _construct_settings(
        GitSettings,
        git_table,
        source=source,
        path="git",
        defaults=defaults.git,
    )
    severity = _construct_settings(
        SeveritySettings,
        severity_table,
        source=source,
        path="scoring.severity",
        defaults=defaults.scoring.severity,
    )
    weights = _construct_settings(
        ScoringWeights,
        weights_table,
        source=source,
        path="scoring.weights",
        defaults=defaults.scoring.weights,
    )
    signals = _construct_settings(
        ScoringSignals,
        signals_table,
        source=source,
        path="scoring.signals",
        defaults=defaults.scoring.signals,
    )
    scoring = ScoringSettings(severity=severity, weights=weights, signals=signals)
    similarity_keys = {item.name for item in fields(SimilaritySettings)}
    _reject_unknown(similarity_table, similarity_keys, source=source, path="similarity")
    try:
        similarity = SimilaritySettings.from_mapping(similarity_table)
    except ValueError as error:
        raise ConfigError(source, "similarity", str(error), None) from error
    allowlist = _parse_allowlist(config_table.get("allowlist", []), source=source)
    try:
        return SlopGuardConfig(
            version=version,
            policy=policy,
            registry=registry,
            git=git,
            scoring=scoring,
            similarity=similarity,
            allowlist=allowlist,
        )
    except ConfigError as error:
        if error.source == "<config>":
            raise ConfigError(source, error.dotted_key, error.problem, error.value) from error
        raise


def _read_path(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(
            str(path), "config.path", "could not read configuration file", path
        ) from error


def load_config(path: Path | None = None, *, root: Path) -> SlopGuardConfig:
    """Load the first available configuration using explicit-path precedence."""

    if path is not None:
        if not path.is_file():
            raise ConfigError(
                str(path), "config.path", "explicit configuration file does not exist", path
            )
        return parse_config(_read_path(path), source=str(path), kind="slopguard")

    standalone = root / "slopguard.toml"
    if standalone.is_file():
        return parse_config(_read_path(standalone), source=str(standalone), kind="slopguard")

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        return parse_config(_read_path(pyproject), source=str(pyproject), kind="pyproject")
    return SlopGuardConfig()
