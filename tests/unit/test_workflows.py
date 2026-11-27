"""Static safety checks for the published action and GitHub workflows."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "action.yml"
WORKFLOWS = (*sorted((ROOT / ".github/workflows").glob("*.yml")), ROOT / "examples/workflow.yml")
SHA_PIN = re.compile(r"@([0-9a-f]{40})$")
PUBLISHED_ACTION = "ahmadbuilds/SlopGuard@v1"


def _load_yaml(path: Path) -> dict[str, object]:
    parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    return parsed


def _walk(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_action_and_workflows_have_no_expressions_inside_run_blocks() -> None:
    for path in (ACTION, *WORKFLOWS):
        document = _load_yaml(path)
        for item in _walk(document):
            run = item.get("run")
            if isinstance(run, str):
                assert "${{" not in run, f"expression in run block in {path}"


def test_workflows_declare_least_privilege_permissions_and_pin_actions() -> None:
    for path in WORKFLOWS:
        workflow = _load_yaml(path)
        assert "permissions" in workflow, f"missing top-level permissions in {path}"
    for path in (ACTION, *WORKFLOWS):
        document = _load_yaml(path)
        for item in _walk(document):
            uses = item.get("uses")
            if not isinstance(uses, str) or uses.startswith("./") or uses == PUBLISHED_ACTION:
                continue
            assert SHA_PIN.search(uses), f"third-party action is not pinned in {path}: {uses}"


def test_slopguard_workflows_always_report_and_fetch_full_pr_history() -> None:
    for path in (ROOT / ".github/workflows/slopguard.yml", ROOT / "examples/workflow.yml"):
        workflow = _load_yaml(path)
        assert "paths" not in workflow["on"]["pull_request"]
        checkout = next(
            item for item in _walk(workflow) if item.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"]["fetch-depth"] == "0"
        assert checkout["with"]["persist-credentials"] == "false"
        assert "ref" not in checkout["with"]


def test_slopguard_workflows_are_fork_safe_and_read_only() -> None:
    for path in (ROOT / ".github/workflows/slopguard.yml", ROOT / "examples/workflow.yml"):
        workflow = _load_yaml(path)
        triggers = workflow["on"]

        assert set(triggers) == {"pull_request"}
        assert workflow["permissions"] == {"contents": "read"}
        assert "secrets" not in str(workflow).lower()
        assert "pull_request_target" not in str(workflow)


def test_action_ignores_uv_configuration() -> None:
    action = _load_yaml(ACTION)
    scan_step = next(item for item in _walk(action) if item.get("id") == "scan")
    assert "uv run --no-config --project" in scan_step["run"]
    assert "SLOPGUARD_COMMENT" in scan_step["env"]
    assert "GITHUB_TOKEN" in scan_step["env"]
    assert "GITHUB_API_URL" in scan_step["env"]
    assert action["inputs"]["comment"]["default"] == "false"
    assert action["inputs"]["github-token"]["default"] == "${{ github.token }}"


def test_example_keeps_comment_opt_in_and_contents_read_only() -> None:
    example = _load_yaml(ROOT / "examples/workflow.yml")
    assert example["permissions"] == {"contents": "read"}
    assert example["jobs"]["slopguard"]["steps"][-1]["with"].get("comment") is None
    source = (ROOT / "examples/workflow.yml").read_text(encoding="utf-8")
    assert "# pull-requests: write" in source
    assert '# comment: "true"' in source


def test_release_reuses_ci_workflow_and_ci_supports_workflow_call() -> None:
    ci = _load_yaml(ROOT / ".github/workflows/ci.yml")
    release = _load_yaml(ROOT / ".github/workflows/release.yml")

    assert "workflow_call" in ci["on"]
    assert ci["concurrency"]["group"] != release["concurrency"]["group"]
    assert release["jobs"]["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert "lint" not in release["jobs"]
    assert "test" not in release["jobs"]


def test_ci_replays_eval_outputs_on_python_311() -> None:
    ci = _load_yaml(ROOT / ".github/workflows/ci.yml")
    job = ci["jobs"]["eval"]
    commands = [step.get("run", "") for step in job["steps"]]
    assert "uv sync --locked --group eval" in commands
    assert "make eval" in commands
    assert "make classifier" in commands
    assert "uv run pytest tests/unit/test_classifier.py" in commands
    assert any("evals/results" in command for command in commands if command.startswith("git diff"))
    assert any(
        "evals/results/classifier" in command
        for command in commands
        if command.startswith("git diff")
    )
    assert all("run_info.json" in command for command in commands if command.startswith("git diff"))
    setup_uv = next(
        item for item in _walk(job) if item.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup_uv["with"]["python-version"] == "3.11"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "evals/data/real_prs.jsonl" in makefile
    assert "evals/data/real_prs_registry.jsonl.gz" in makefile
    assert "classifier:" in makefile


def test_wheel_package_discovery_excludes_evaluation_tools() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_discovery = project["tool"]["setuptools"]["packages"]["find"]

    assert package_discovery["where"] == ["src"]
    assert "evals" in package_discovery["exclude"]
    assert "evals.*" in package_discovery["exclude"]


def test_action_exposes_token_only_when_comment_is_enabled() -> None:
    action = (ROOT / "action.yml").read_text(encoding="utf-8")

    assert "GITHUB_TOKEN: ${{ inputs.comment == 'true' && inputs.github-token || '' }}" in action
    assert "GITHUB_TOKEN: ${{ inputs.github-token }}" not in action
