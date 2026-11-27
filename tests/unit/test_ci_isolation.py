from __future__ import annotations

import os


def test_runner_environment_is_not_inherited() -> None:
    assert not any(
        name == "CI" or name.startswith(("GITHUB_", "RUNNER_", "ACTIONS_")) for name in os.environ
    )
