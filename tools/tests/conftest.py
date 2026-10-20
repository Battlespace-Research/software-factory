"""Shared pytest fixtures for the swfactory tests."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from swfactory.config import ProjectConfig, RoleTable, factory_home, load_role_table

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


# --------------------------------------------------------------------------- git repositories

def make_git_repo(tmp_path: Path, base: str = "main", with_origin: bool = True) -> Path:
    """A project repo under ``tmp_path/repo`` with one commit on ``base``.

    With ``with_origin`` it also has a bare ``origin`` (``tmp_path/origin.git``) that the base branch is
    pushed to. The repo carries its own git identity, so no test depends on (or changes) the machine's.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init", "-q", "-b", base)
    for key, value in (
        ("user.name", "Factory Test"),
        ("user.email", "factory-test@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        git("config", key, value)
    (repo / "README.md").write_text("# demo\n", encoding="utf-8", newline="\n")
    git("add", "README.md")
    git("commit", "-q", "-m", "Initial commit")
    if with_origin:
        origin = tmp_path / "origin.git"
        git("init", "-q", "--bare", "-b", base, str(origin))
        git("remote", "add", "origin", origin.as_posix())  # forward slashes: stored in .git/config as given
        git("push", "-q", "-u", "origin", base)
    return repo


def _hide_git_config(monkeypatch: pytest.MonkeyPatch, empty_config: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(scope="session")
def _empty_git_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    empty = tmp_path_factory.mktemp("gitconfig") / "config"
    empty.write_text("", encoding="utf-8")
    return empty


@pytest.fixture
def isolated_git_config(_empty_git_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the machine's git configuration (autocrlf, hooks, signing, identity) from the test's git calls."""
    _hide_git_config(monkeypatch, _empty_git_config)


@pytest.fixture(scope="session")
def _git_repo_template(tmp_path_factory: pytest.TempPathFactory, _empty_git_config: Path):
    """``template(base, with_origin)``: a repo built once per run and copied per test, because starting git
    is slow (about 0.1 s a call on Windows) and every test would otherwise spend a second building its repo."""
    built: dict[tuple[str, bool], Path] = {}

    def template(base: str, with_origin: bool) -> Path:
        if (base, with_origin) not in built:
            root = tmp_path_factory.mktemp(f"git-template-{base}-{'origin' if with_origin else 'local'}")
            with pytest.MonkeyPatch.context() as patch:
                _hide_git_config(patch, _empty_git_config)
                make_git_repo(root, base, with_origin)
            built[(base, with_origin)] = root
        return built[(base, with_origin)]

    return template


def _copy_git_repo(template: Path, tmp_path: Path) -> Path:
    """A copy of a template repo (and its origin) under ``tmp_path``, with the remote pointing at the copy."""
    repo = shutil.copytree(template / "repo", tmp_path / "repo")
    origin = template / "origin.git"
    if origin.is_dir():
        copied = shutil.copytree(origin, tmp_path / "origin.git")
        config = repo / ".git" / "config"
        text = config.read_text(encoding="utf-8")
        assert origin.as_posix() in text, "the template's remote URL is not where the copy expects it"
        config.write_text(text.replace(origin.as_posix(), copied.as_posix()), encoding="utf-8", newline="\n")
    return repo


@pytest.fixture
def git_repo(tmp_path: Path, isolated_git_config: None, _git_repo_template) -> Path:
    """A project repo on ``main`` with an ``origin`` remote."""
    return _copy_git_repo(_git_repo_template("main", True), tmp_path)


@pytest.fixture
def git_repo_no_remote_master(tmp_path: Path, isolated_git_config: None, _git_repo_template) -> Path:
    """A project repo on ``master`` with no remote at all."""
    return _copy_git_repo(_git_repo_template("master", False), tmp_path)


@pytest.fixture
def cfg() -> ProjectConfig:
    """A project's settings: one branch per work item, cut from ``main``."""
    return ProjectConfig(
        project="demo",
        base_branch="main",
        branching="per-item",
        work_branch=None,
        mode="review",
        approvers=["owner"],
        verify_quick="pytest -q",
        verify_full="pytest",
        builds="allow",
        size_cap=400,
        generated=[],
        max_rounds=3,
        hard_stops=[],
        role_overrides={},
        github_mirror=False,
    )


@pytest.fixture
def cfg_single(cfg: ProjectConfig) -> ProjectConfig:
    """The same project with ``branching: single`` on the branch ``factory-work``."""
    return dataclasses.replace(cfg, branching="single", work_branch="factory-work")
