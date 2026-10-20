"""Shared pytest fixtures for the swfactory tests."""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from swfactory.config import RoleTable, factory_home, load_role_table

FAKE_CLI_SCRIPT = Path(__file__).resolve().parent / "fakes" / "fake_cli.py"


@pytest.fixture(autouse=True)
def _isolate_factory_home_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with FACTORY_HOME unset, so the developer's shell cannot leak in.

    A test that needs it calls ``monkeypatch.setenv("FACTORY_HOME", ...)`` itself.
    """
    monkeypatch.delenv("FACTORY_HOME", raising=False)


@pytest.fixture(autouse=True)
def _isolate_codex_args_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with FACTORY_CODEX_ARGS unset (the owner's shell may export it)."""
    monkeypatch.delenv("FACTORY_CODEX_ARGS", raising=False)


@pytest.fixture
def table() -> RoleTable:
    """The factory's default role table (``config/roles.yaml``)."""
    return load_role_table(factory_home() / "config/roles.yaml")


@dataclass
class FakeCli:
    """Handle on the fake ``codex`` and ``claude`` tools the ``fake_cli`` fixture installs."""

    bin_dir: Path
    log: Path
    _monkeypatch: pytest.MonkeyPatch

    def mode(self, name: str) -> None:
        """Select what the fake does (see ``MODES`` in ``fakes/fake_cli.py``)."""
        self._monkeypatch.setenv("FAKE_CLI_MODE", name)

    def calls(self) -> list[dict]:
        """Every call the fakes received: ``{"tool", "argv", "prompt"}``, oldest first."""
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]


def _write_shim(bin_dir: Path, tool: str) -> None:
    """Write ``<tool>`` (a ``.cmd`` wrapper on Windows, a shebang script elsewhere) that runs the fake."""
    python = sys.executable
    if os.name == "nt":
        shim = bin_dir / f"{tool}.cmd"
        shim.write_bytes(
            f'@echo off\r\n"{python}" "{FAKE_CLI_SCRIPT}" {tool} %*\r\nexit /b %ERRORLEVEL%\r\n'.encode("utf-8")
        )
    else:
        shim = bin_dir / tool
        shim.write_text(f'#!/bin/sh\nexec "{python}" "{FAKE_CLI_SCRIPT}" {tool} "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_cli(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> FakeCli:
    """Put fake ``codex`` and ``claude`` tools first on PATH; the test sets their behaviour with ``.mode()``."""
    bin_dir = tmp_path_factory.mktemp("fake-bin")
    for tool in ("codex", "claude"):
        _write_shim(bin_dir, tool)
    log = bin_dir / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("FAKE_CLI_MODE", "pass")
    return FakeCli(bin_dir, log, monkeypatch)
