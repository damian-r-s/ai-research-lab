"""The root-owned image helper refuses anything that is not a lab image (it runs via sudo)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from src.core.config import REPO_ROOT

SHA = "0123456789abcdef0123456789abcdef01234567"


def load_helper() -> ModuleType:
    path = str(REPO_ROOT / "scripts" / "server" / "ai-lab-image")
    loader = importlib.machinery.SourceFileLoader("ai_lab_image", path)
    spec = importlib.util.spec_from_loader("ai_lab_image", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def archive(tmp_path: Path, tags: list[str] | None) -> bytes:
    """A minimal docker-archive: only manifest.json matters to the helper."""
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as tar:
        if tags is not None:
            data = json.dumps([{"Config": "c.json", "RepoTags": tags, "Layers": []}]).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path.read_bytes()


class Stdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[list[str]]]:
    module = load_helper()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool) -> Any:
        calls.append(cmd)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return module, calls


@pytest.mark.parametrize(
    "tags",
    [
        [f"localhost/ai-lab-api:{SHA}"],
        [f"localhost/ai-lab-frontend:{SHA}"],
    ],
)
def test_lab_images_are_imported(
    helper: tuple[ModuleType, list[list[str]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tags: list[str],
) -> None:
    module, calls = helper
    monkeypatch.setattr(module.sys, "stdin", Stdin(archive(tmp_path, tags)))
    module.do_import()
    assert len(calls) == 1 and calls[0][:5] == [
        "/usr/local/bin/k3s",
        "ctr",
        "-n",
        "k8s.io",
        "images",
    ]


@pytest.mark.parametrize(
    "tags",
    [
        ["localhost/agentic-trading-platform:local"],  # the other project's image
        [f"localhost/ai-lab-api:{SHA}", "localhost/agentic-trading-platform:local"],  # smuggled
        ["localhost/ai-lab-api:latest"],  # not an exact commit
        [f"docker.io/library/ai-lab-api:{SHA}"],  # wrong registry prefix
        [f"localhost/ai-lab-api:{SHA}x"],
        [],
    ],
)
def test_anything_else_is_refused(
    helper: tuple[ModuleType, list[list[str]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tags: list[str],
) -> None:
    module, calls = helper
    monkeypatch.setattr(module.sys, "stdin", Stdin(archive(tmp_path, tags)))
    with pytest.raises(SystemExit):
        module.do_import()
    assert calls == []  # k3s was never invoked


def test_non_archives_are_refused(
    helper: tuple[ModuleType, list[list[str]]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, calls = helper
    monkeypatch.setattr(module.sys, "stdin", Stdin(archive(tmp_path, None)))  # no manifest.json
    with pytest.raises(SystemExit, match="not a docker-archive"):
        module.do_import()
    assert calls == []


@pytest.mark.parametrize(
    ("ref", "allowed"),
    [
        (f"localhost/ai-lab-api:{SHA}", True),
        ("localhost/agentic-trading-platform:local", False),
        (f"localhost/ai-lab-api:{SHA} localhost/agentic-trading-platform:local", False),
    ],
)
def test_removal_is_limited_to_lab_images(
    helper: tuple[ModuleType, list[list[str]]], ref: str, allowed: bool
) -> None:
    module, calls = helper
    if allowed:
        module.do_rm(ref)
        assert calls == [["/usr/local/bin/k3s", "ctr", "-n", "k8s.io", "images", "rm", ref]]
    else:
        with pytest.raises(SystemExit, match="not a lab image"):
            module.do_rm(ref)
        assert calls == []
