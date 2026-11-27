"""Shared pytest configuration."""

from __future__ import annotations

import os

import pytest

_CI_ENVIRONMENT_PREFIXES = ("GITHUB_", "RUNNER_", "ACTIONS_")
_CI_ENVIRONMENT_NAMES = frozenset({"CI"})


@pytest.fixture(autouse=True)
def _isolate_from_ci_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name in _CI_ENVIRONMENT_NAMES or name.startswith(_CI_ENVIRONMENT_PREFIXES):
            monkeypatch.delenv(name, raising=False)
