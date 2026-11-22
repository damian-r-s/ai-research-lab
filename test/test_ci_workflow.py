"""The deploy workflow stays on the lab's own runner, read-only, and injection-safe."""

from __future__ import annotations

import re
from typing import Any

import yaml

from src.core.config import REPO_ROOT


def workflow() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy.yml").read_text())
    return data


def test_runs_only_on_the_labs_self_hosted_runner() -> None:
    job = workflow()["jobs"]["deploy"]
    assert job["runs-on"] == ["self-hosted", "linux", "ai-research-lab"]


def test_token_permissions_are_read_only() -> None:
    assert workflow()["permissions"] == {"contents": "read"}


def test_pushes_deploy_staging_and_prod_is_manual_only() -> None:
    triggers = workflow()[True]  # YAML 1.1 reads the `on` key as boolean True
    assert triggers["push"] == {"branches": ["main"]}
    assert set(triggers["workflow_dispatch"]["inputs"]["environment"]["options"]) == {
        "staging",
        "prod",
    }
    env = workflow()["jobs"]["deploy"]["steps"][1]["env"]["TARGET_ENV"]
    assert "'staging'" in env and "workflow_dispatch" in env  # pushes can only target staging


def test_no_expressions_inside_shell_scripts() -> None:
    """`${{ … }}` inside `run:` is a script-injection vector; values must go through `env:`."""
    for step in workflow()["jobs"]["deploy"]["steps"]:
        assert not re.search(r"\$\{\{", step.get("run", ""))


def test_deployments_never_overlap() -> None:
    assert workflow()["concurrency"] == {"group": "deploy", "cancel-in-progress": False}
