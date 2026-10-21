"""The plugin manifests (design section 6.1).

The factory ships as one plugin, ``bsri-sw-factory``, with a Claude Code manifest and a Codex manifest that
share one ``skills/`` folder. A marketplace file lists the plugin for each host. The manifests must agree with
each other and with the CLI's version, and every path they name must exist.

The Claude Code manifest names no ``agents`` path: Claude Code loads the plugin's ``agents/`` folder by itself.
``claude plugin validate`` (Claude Code 2.1.294) rejects ``"agents": "./agents/"`` (an agents path must end
in ``.md``), the plugin then fails to load, and a list of the agent files loaded no agent at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import swfactory
from swfactory.config import factory_home

NAME = "bsri-sw-factory"
MARKETPLACE = "bsri-factory"
SHARED_FIELDS = ("name", "version", "description", "author", "repository", "license", "skills")


def load(relative: str) -> dict:
    path = factory_home() / relative
    assert path.is_file(), f"{relative} is missing"
    return json.loads(path.read_text(encoding="utf-8"))


def claude_manifest() -> dict:
    return load(".claude-plugin/plugin.json")


def codex_manifest() -> dict:
    return load(".codex-plugin/plugin.json")


def claude_marketplace() -> dict:
    return load(".claude-plugin/marketplace.json")


def codex_marketplace() -> dict:
    return load(".agents/plugins/marketplace.json")


def resolve(relative: str) -> Path:
    """A manifest path (``./skills/``) resolved against the plugin root, the factory checkout."""
    assert relative.startswith("./"), f"{relative!r}: manifest paths are relative and start with ./"
    return factory_home() / relative


def test_manifests_agree():
    claude, codex = claude_manifest(), codex_manifest()
    assert claude["name"] == codex["name"] == NAME
    assert claude["version"] == codex["version"] == swfactory.__version__
    assert {field: claude.get(field) for field in SHARED_FIELDS} == {field: codex.get(field) for field in SHARED_FIELDS}


def test_manifest_values():
    claude = claude_manifest()
    assert claude["author"] == {"name": "Battlespace Research"}
    assert claude["repository"] == "https://github.com/Battlespace-Research/software-factory"
    assert claude["license"] == "Proprietary"
    assert claude["skills"] == "./skills/"
    assert isinstance(claude["description"], str) and len(claude["description"]) > 40
    assert "agents" not in claude, "Claude Code loads ./agents/ by default; see the module docstring"
    assert "agents" not in codex_manifest(), "subagent definitions are Claude Code only"


def test_manifest_paths_exist():
    claude, codex = claude_manifest(), codex_manifest()
    for relative in (claude["skills"], codex["skills"]):
        assert resolve(relative).is_dir(), relative
    assert sorted(resolve(claude["skills"]).glob("*/SKILL.md")), "the skills folder holds no skill"
    agents = factory_home() / "agents"
    assert agents.is_dir() and sorted(agents.glob("*.md")), "the default agents folder holds no subagent"


def test_marketplace_lists_plugin():
    market = claude_marketplace()
    assert market["name"] == MARKETPLACE
    assert market["owner"] == {"name": "Battlespace Research"}
    assert [plugin["name"] for plugin in market["plugins"]] == [NAME]
    entry = market["plugins"][0]
    assert entry["source"] == "./" and (resolve(entry["source"]) / ".claude-plugin" / "plugin.json").is_file()
    assert entry["description"] == claude_manifest()["description"]


def test_codex_marketplace_lists_plugin():
    market = codex_marketplace()
    assert market["name"] == MARKETPLACE
    assert [plugin["name"] for plugin in market["plugins"]] == [NAME]
    source = market["plugins"][0]["source"]
    assert source == {"source": "local", "path": "./"}
    assert (resolve(source["path"]) / ".codex-plugin" / "plugin.json").is_file()


@pytest.mark.parametrize(
    "relative", [".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json", ".agents/plugins/marketplace.json"]
)
def test_manifests_are_plain_utf8_json(relative):
    path = factory_home() / relative
    assert path.is_file(), f"{relative} is missing"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{relative} starts with a byte order mark"
    json.loads(raw.decode("utf-8"))
