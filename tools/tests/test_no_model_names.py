"""No model name outside ``config/roles.yaml`` (design section 7.2).

Skills, prompts, agents, templates, the swfactory code and the teammate guides refer to roles only; the role
table is the one file that binds a role to a model. The scan covers every file under the scanned folders,
including folders that do not exist yet, so the test fails the moment a model id is written into any of them.
Model ids are caught as written in the table and in their display form ("Sonnet 5.5").
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from swfactory.config import factory_home, load_role_table

SCANNED = ("skills", "prompts", "agents", "templates", "tools/swfactory", "docs/guide")
GENERIC_PATTERNS = (r"\bclaude-(opus|sonnet|haiku|fable)-\d", r"\bgpt-\d", r"\b(opus|sonnet|haiku|fable)[ -]?\d")


def models_in(node: Any) -> set[str]:
    """Every ``model:`` value anywhere in a parsed YAML document."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "model" and isinstance(value, str):
                found.add(value)
            else:
                found |= models_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= models_in(item)
    return found


def model_patterns(roles_yaml: Path) -> list[re.Pattern[str]]:
    ids = models_in(yaml.safe_load(roles_yaml.read_text(encoding="utf-8")))
    sources = [re.escape(model) for model in sorted(ids)] + list(GENERIC_PATTERNS)
    return [re.compile(source, re.IGNORECASE) for source in sources]


def find_model_names(root: Path, patterns: list[re.Pattern[str]]) -> list[str]:
    """``<path>:<number>: <line>`` for each line naming a model in a scanned folder under ``root``.

    ``__pycache__`` is skipped (it is compiled from the scanned sources). Files are read as bytes and decoded
    leniently, so a binary file cannot stop the scan; model ids are plain ASCII either way.
    """
    offenders: list[str] = []
    for folder in SCANNED:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if any(pattern.search(line) for pattern in patterns):
                    offenders.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
    return offenders


def test_no_model_names_outside_config():
    root = factory_home()
    offenders = find_model_names(root, model_patterns(root / "config" / "roles.yaml"))
    assert offenders == []


def test_patterns_cover_every_model_in_the_role_table():
    roles_yaml = factory_home() / "config" / "roles.yaml"
    table = load_role_table(roles_yaml)
    in_table = {
        binding.model
        for role in table.roles.values()
        for bindings in (role.bindings, role.fallback)
        for binding in bindings.values()
    }
    assert in_table
    assert in_table <= models_in(yaml.safe_load(roles_yaml.read_text(encoding="utf-8")))


def test_scan_finds_model_names_in_any_scanned_folder(tmp_path):
    """The scan reaches folders that do not exist in the factory yet, is case-insensitive, and leaves
    ``config/roles.yaml`` alone."""
    real = factory_home() / "config" / "roles.yaml"
    a_model = sorted(models_in(yaml.safe_load(real.read_text(encoding="utf-8"))))[0]
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "roles.yaml").write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    files = {
        "skills/factory-run/SKILL.md": f"Run it with {a_model.upper()}.\n",
        "agents/x.md": "---\nname: x\n---\nuses GPT-7 today\n",
        "tools/swfactory/m.py": "ALIAS = 'claude-haiku-9'\n",
        "templates/project/ok.md": "claude-([a-z]+)- is a pattern, not a model\n",
        "tools/swfactory/__pycache__/m.cpython-314.pyc": "claude-haiku-9",
        "docs/guide/using.md": "The tester runs on Sonnet 5.5 by default.\n",
        "docs/guide/ok.md": "Models come from config/roles.yaml; a sonnet is a poem.\n",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    offenders = find_model_names(tmp_path, model_patterns(tmp_path / "config" / "roles.yaml"))
    assert offenders == [
        f"skills/factory-run/SKILL.md:1: Run it with {a_model.upper()}.",
        "agents/x.md:4: uses GPT-7 today",
        "tools/swfactory/m.py:1: ALIAS = 'claude-haiku-9'",
        "docs/guide/using.md:1: The tester runs on Sonnet 5.5 by default.",
    ]
