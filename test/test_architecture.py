"""Module boundaries (ARCHITECTURE §5, criterion 2.10), checked with import-linter."""

from __future__ import annotations

import shutil
import subprocess
import tomllib

import grimp
import pytest

from src.core.config import REPO_ROOT

# Packages allowed to import LangGraph or the job library, and therefore absent from those
# contracts' source lists. Everything else under src/ must be listed (checked below).
LANGGRAPH_ALLOWED = {"src.agents", "src.services"}
JOBS_ALLOWED = {"src.runs", "src.services"}


def contracts() -> list[dict[str, object]]:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    result: list[dict[str, object]] = config["tool"]["importlinter"]["contracts"]
    return result


def top_level_packages() -> set[str]:
    return {f"src.{p.name}" for p in (REPO_ROOT / "src").iterdir() if (p / "__init__.py").is_file()}


@pytest.mark.skipif(shutil.which("lint-imports") is None, reason="import-linter not installed")
def test_import_contracts_are_kept() -> None:
    result = subprocess.run(
        ["lint-imports", "--no-cache"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("forbidden", "allowed"), [("langgraph", LANGGRAPH_ALLOWED), ("procrastinate", JOBS_ALLOWED)]
)
def test_every_package_is_covered_by_the_contracts(forbidden: str, allowed: set[str]) -> None:
    """A new package under src/ must be placed explicitly: restricted, or allowed above."""
    contract = next(c for c in contracts() if forbidden in c["forbidden_modules"])  # type: ignore[operator]
    restricted = set(contract["source_modules"])  # type: ignore[call-overload]
    assert top_level_packages() - allowed == restricted


def test_the_langgraph_rule_is_not_vacuous() -> None:
    """External packages are really part of the import graph, so a violation would show."""
    graph = grimp.build_graph("src", include_external_packages=True)
    importers = graph.find_modules_that_directly_import("langgraph")
    assert importers and all(m.startswith("src.agents.") for m in importers)
