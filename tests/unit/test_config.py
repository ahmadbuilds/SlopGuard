"""Tests for SlopGuard's pure configuration parsing and file discovery."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from slopguard.config import (
    MAX_CONFIG_BYTES,
    AllowlistEntry,
    ConfigError,
    RegistrySettings,
    SlopGuardConfig,
    load_config,
    parse_config,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "slopguard.toml"


def test_example_configuration_matches_dataclass_defaults() -> None:
    config = parse_config(EXAMPLE.read_text(encoding="utf-8"), source="example")

    assert config == SlopGuardConfig()


def test_same_settings_under_pyproject_tool_table_parse_identically() -> None:
    standalone = EXAMPLE.read_text(encoding="utf-8")
    pyproject_config = standalone.replace("version = 1", "tool.slopguard.version = 1", 1)
    for table in (
        "policy",
        "registry",
        "scoring.severity",
        "scoring.weights",
        "scoring.signals",
        "similarity",
    ):
        pyproject_config = pyproject_config.replace(f"[{table}]", f"[tool.slopguard.{table}]")
    pyproject_config = pyproject_config.replace("[[allowlist]]", "[[tool.slopguard.allowlist]]")

    assert parse_config(standalone, source="standalone") == parse_config(
        "[project]\nname='demo'\n" + pyproject_config,
        source="pyproject",
        kind="pyproject",
    )


def test_standalone_config_rejects_pyproject_tables_instead_of_ignoring_them() -> None:
    with pytest.raises(ConfigError, match="config.project"):
        parse_config(
            "[project]\nname='demo'\n[policy]\nmode='block'\n",
            source="slopguard.toml",
        )


def test_pyproject_without_slopguard_table_ignores_other_tables() -> None:
    assert (
        parse_config(
            "[dependency-groups]\ndev = []\n",
            source="pyproject.toml",
            kind="pyproject",
        )
        == SlopGuardConfig()
    )


@pytest.mark.parametrize(
    ("document", "key"),
    [
        ("rogue = 1", "config.rogue"),
        ("[policy]\nrogue = 1", "policy.rogue"),
        ("[[allowlist]]\nname='demo'\nreason='test'\nrogue=1", "allowlist[1].rogue"),
        ("[policy]\nmode = 1", "policy.mode"),
        ("[registry]\nunknown = 1", "registry.unknown"),
        ("[registry]\ntimeout_seconds = 11", "registry.timeout_seconds"),
        ("[registry]\nmax_retries = 3", "registry.max_retries"),
        ("[registry]\nbackoff_seconds = 1.1", "registry.backoff_seconds"),
        ("[registry]\nmax_backoff_seconds = true", "registry.max_backoff_seconds"),
        ("[policy]\nwarn_threshold = true", "policy.warn_threshold"),
        ("[similarity]\ntop_n = true", "similarity"),
        ("[similarity]\nunknown_setting = 1", "similarity.unknown_setting"),
        (
            "[similarity]\nmax_distance_by_length = [[4, 1], [8, 3]]",
            "scoring.signals.distance_factors",
        ),
        ("[policy]\nwarn_threshold = 101", "policy.warn_threshold"),
        (
            "[policy]\nwarn_threshold = 81\nblock_threshold = 80",
            "policy.warn_threshold",
        ),
        (
            "[scoring.severity]\nlow=20\nmedium=60\nhigh=50\ncritical=80",
            "scoring.severity.high",
        ),
        (
            "[[allowlist]]\nname='Foo_Bar'\nreason='one'\n"
            "[[allowlist]]\nname='foo-bar'\nreason='two'",
            "allowlist[2].name",
        ),
        ("[[allowlist]]\nname='demo'", "allowlist[1].reason"),
        ("[[allowlist]]\nname='demo'\nreason='  '", "allowlist[1].reason"),
        ("version = 9", "version"),
        ("[policy\nmode='warn'", "config.document"),
        ("x" * (MAX_CONFIG_BYTES + 1), "config.document"),
    ],
    ids=[
        "top-level-unknown",
        "nested-unknown",
        "allowlist-unknown",
        "wrong-type",
        "registry-unknown-key",
        "registry-timeout-ceiling",
        "registry-retry-ceiling",
        "registry-backoff-ceiling",
        "registry-backoff-type",
        "bool-as-number",
        "similarity-bool-as-int",
        "similarity-unknown",
        "missing-distance-factor",
        "out-of-range",
        "warn-above-block",
        "severity-order",
        "duplicate-name",
        "missing-reason",
        "empty-reason",
        "version",
        "malformed-toml",
        "oversized-document",
    ],
)
def test_invalid_configuration_reports_source_and_dotted_key(document: str, key: str) -> None:
    with pytest.raises(ConfigError) as error:
        parse_config(document, source="inline.toml")

    assert "inline.toml" in str(error.value)
    assert key in str(error.value)
    assert "(got " in str(error.value)


def test_malformed_megabyte_document_error_does_not_embed_input() -> None:
    prefix = 'value = "'
    document = prefix + "x" * (MAX_CONFIG_BYTES - len(prefix))

    with pytest.raises(ConfigError) as error:
        parse_config(document, source="large.toml")

    message = str(error.value)
    assert len(message) < 300
    assert "xxxx" not in message


def test_unencodable_text_reports_its_location_without_echoing_input() -> None:
    with pytest.raises(ConfigError) as error:
        parse_config("[policy]\nmode='\ud800'", source="surrogate.toml")

    assert "line 2, column 7" in str(error.value)
    assert "\ud800" not in str(error.value)


def test_discovery_prefers_explicit_path(tmp_path: Path) -> None:
    (tmp_path / "slopguard.toml").write_text("[policy]\nwarn_threshold=41\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopguard.policy]\nwarn_threshold=42\n", encoding="utf-8"
    )
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[policy]\nwarn_threshold=43\n", encoding="utf-8")

    assert load_config(explicit, root=tmp_path).policy.warn_threshold == 43


def test_discovery_prefers_slopguard_toml_over_pyproject(tmp_path: Path) -> None:
    (tmp_path / "slopguard.toml").write_text("[policy]\nwarn_threshold=41\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopguard.policy]\nwarn_threshold=42\n", encoding="utf-8"
    )

    assert load_config(root=tmp_path).policy.warn_threshold == 41


def test_discovery_uses_pyproject_table_when_standalone_is_absent(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopguard.policy]\nwarn_threshold=42\n", encoding="utf-8"
    )

    assert load_config(root=tmp_path).policy.warn_threshold == 42


def test_discovery_defaults_when_no_configuration_is_found(tmp_path: Path) -> None:
    assert load_config(root=tmp_path) == SlopGuardConfig()


def test_pyproject_without_slopguard_table_uses_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    assert load_config(root=tmp_path) == SlopGuardConfig()


def test_missing_explicit_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.path"):
        load_config(tmp_path / "missing.toml", root=tmp_path)


def test_registry_settings_default_values() -> None:
    assert SlopGuardConfig().registry == RegistrySettings()


def test_allowlist_names_are_canonical_and_expiry_is_explicit() -> None:
    config = SlopGuardConfig(
        allowlist=(
            AllowlistEntry(name="Foo_Bar", reason="approved", expires=date(2026, 5, 1)),
            AllowlistEntry(name="forever", reason="internal dependency"),
        )
    )
    now = datetime(2026, 5, 2, tzinfo=UTC)

    assert config.allowlist[0].name == "foo-bar"
    assert config.allowlist_entry_for("FOO.BAR", now=now) is None
    assert config.allowlist_entry_for("forever", now=now) == config.allowlist[1]
    assert config.expired_allowlist_entries(now) == (config.allowlist[0],)
