"""Configuration for the factory: role table, skills registry and a project's factory.yaml.

Every loader collects all the problems it can find and raises one ``ConfigError`` carrying
them, so a person fixes a file in one pass. Model names live only in ``config/roles.yaml``;
nothing in this module names one.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

STAGES = ("plan", "design", "build", "test", "deploy", "maintain")
ROLE_VENDORS = ("host", "same", "other", "claude", "openai")
RUN_AS = ("main", "subagent", "headless")
ACCESS = ("write", "read-only", "no-product-edits")
BINDING_VENDORS = ("claude", "openai")
BRANCHING = ("per-item", "single")
MODES = ("review", "auto")
BUILDS = ("allow", "ask")

DEFAULT_HARD_STOPS = [
    "main-branch",
    "delete-data",
    "credentials",
    "installers-admin",
    "purchases",
    "auth-money-schema",
    "new-dependency",
    "production-data",
    "third-failed-round",
    "business-assumption",
]


class ConfigError(Exception):
    """A configuration file is missing, unreadable or invalid. ``problems`` lists every issue found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


def factory_home() -> Path:
    """The factory checkout: env ``FACTORY_HOME`` if set, else the repo root above ``tools/``."""
    env = os.environ.get("FACTORY_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- shared helpers

def _read_yaml(path: Path) -> Any:
    """Parse a YAML file (UTF-8, with or without a BOM; LF or CRLF) or raise ``ConfigError``."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError([f"{path}: file not found"]) from None
    except (OSError, UnicodeDecodeError) as err:
        raise ConfigError([f"{path}: cannot read file: {err}"]) from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ConfigError([f"{path}: invalid YAML: {' '.join(str(err).split())}"]) from None


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_str(item) for item in value)


def _expect_choice(prefix: str, key: str, value: Any, allowed: tuple[str, ...], problems: list[str]) -> None:
    if value is None:
        problems.append(f"{prefix}: {key} is required (one of {', '.join(allowed)})")
    elif not (isinstance(value, str) and value in allowed):
        problems.append(f"{prefix}: {key} {value!r} is not one of {', '.join(allowed)}")


def _merge(base: Any, override: Any) -> Any:
    """Deep-merge mappings (override wins field by field); anything else is replaced."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _merge(base[key], value) if key in base else copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


# --------------------------------------------------------------------------- role table

@dataclass(frozen=True)
class Binding:
    """A model and reasoning effort."""

    model: str
    effort: str


@dataclass(frozen=True)
class RoleSpec:
    """One role: where it runs, what it may touch, and its binding and fallback per vendor."""

    name: str
    stage: str
    vendor: str
    run_as: str
    access: str
    bindings: dict[str, Binding]
    fallback: dict[str, Binding]


@dataclass
class RoleTable:
    roles: dict[str, RoleSpec]


_ROLE_KEYS = ("stage", "vendor", "run_as", "access", "bindings", "fallback")


def _build_bindings(prefix: str, kind: str, raw: Any, problems: list[str]) -> dict[str, Binding]:
    """Validate a ``bindings`` or ``fallback`` mapping: one binding per vendor, no more, no less."""
    result: dict[str, Binding] = {}
    if not isinstance(raw, dict):
        problems.append(f"{prefix}: {kind} must be a mapping with a binding for each of {', '.join(BINDING_VENDORS)}")
        return result
    for vendor in raw:
        if vendor not in BINDING_VENDORS:
            problems.append(f"{prefix}: {kind} has unknown vendor {vendor!r} (expected {', '.join(BINDING_VENDORS)})")
    for vendor in BINDING_VENDORS:
        if vendor not in raw:
            problems.append(f"{prefix}: {kind} is missing the {vendor} binding")
            continue
        entry = raw[vendor]
        where = f"{prefix}: {kind}.{vendor}"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be a mapping with model and effort")
            continue
        for key in entry:
            if key not in ("model", "effort"):
                problems.append(f"{where} has unknown key {key!r}")
        valid = True
        for key in ("model", "effort"):
            if not _is_str(entry.get(key)):
                problems.append(f"{where}.{key} must be a non-empty string")
                valid = False
        if valid:
            result[vendor] = Binding(entry["model"], entry["effort"])
    return result


def _build_role(prefix: str, name: str, raw: Any, problems: list[str]) -> RoleSpec | None:
    """Validate one role mapping; append every problem found; return the role only if clean."""
    before = len(problems)
    if not isinstance(raw, dict):
        problems.append(f"{prefix}: must be a mapping")
        return None
    for key in raw:
        if key not in _ROLE_KEYS:
            problems.append(f"{prefix}: unknown field {key!r}")
    _expect_choice(prefix, "stage", raw.get("stage"), STAGES, problems)
    _expect_choice(prefix, "vendor", raw.get("vendor"), ROLE_VENDORS, problems)
    _expect_choice(prefix, "run_as", raw.get("run_as"), RUN_AS, problems)
    _expect_choice(prefix, "access", raw.get("access"), ACCESS, problems)
    bindings = _build_bindings(prefix, "bindings", raw.get("bindings"), problems)
    fallback = _build_bindings(prefix, "fallback", raw.get("fallback"), problems)
    if len(problems) > before:
        return None
    return RoleSpec(
        name=name,
        stage=raw["stage"],
        vendor=raw["vendor"],
        run_as=raw["run_as"],
        access=raw["access"],
        bindings=bindings,
        fallback=fallback,
    )


def load_role_table(path: Path, overrides: dict | None = None) -> RoleTable:
    """Load ``roles.yaml``, then apply a project's per-role overrides.

    An override is merged field by field onto the default role, so it can replace a single
    model or effort. The result must be valid: the default file is checked first, then each
    overridden role is checked again after the merge. Raises ``ConfigError`` listing every problem.
    """
    path = Path(path)
    data = _read_yaml(path)
    raw_roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise ConfigError([f"{path.name}: expected a non-empty 'roles' mapping"])

    problems: list[str] = []
    roles: dict[str, RoleSpec] = {}
    for name, raw in raw_roles.items():
        if not isinstance(name, str):
            problems.append(f"{path.name}: role name {name!r} must be a string")
            continue
        spec = _build_role(f"{path.name}: role '{name}'", name, raw, problems)
        if spec is not None:
            roles[name] = spec
    if problems:
        raise ConfigError(problems)

    if overrides:
        if not isinstance(overrides, dict):
            raise ConfigError(["project override: roles must be a mapping of role name to override"])
        for name, override in overrides.items():
            if name not in raw_roles:
                problems.append(f"project override: unknown role {name!r}")
                continue
            if not isinstance(override, dict):
                problems.append(f"project override: role '{name}': must be a mapping")
                continue
            spec = _build_role(f"project override: role '{name}'", name, _merge(raw_roles[name], override), problems)
            if spec is not None:
                roles[name] = spec
        if problems:
            raise ConfigError(problems)
    return RoleTable(roles)


# --------------------------------------------------------------------------- skills registry

@dataclass(frozen=True)
class SkillReq:
    """A third-party skill a role requires or can use."""

    name: str
    required: bool
    fallback: str | None
    interactive: bool


_SKILL_KEYS = ("name", "required", "fallback", "interactive")


def _build_skill(where: str, raw: Any, problems: list[str]) -> SkillReq | None:
    before = len(problems)
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be a mapping with a name")
        return None
    for key in raw:
        if key not in _SKILL_KEYS:
            problems.append(f"{where}: unknown key {key!r}")
    if not _is_str(raw.get("name")):
        problems.append(f"{where}: name is required (a non-empty string)")
    for key in ("required", "interactive"):
        if not isinstance(raw.get(key, False), bool):
            problems.append(f"{where}: {key} must be true or false")
    fallback = raw.get("fallback")
    if fallback is not None and not _is_str(fallback):
        problems.append(f"{where}: fallback must be a skill name or null")
    if len(problems) > before:
        return None
    return SkillReq(
        name=raw["name"],
        required=raw.get("required", False),
        fallback=fallback,
        interactive=raw.get("interactive", False),
    )


def load_skills_registry(path: Path) -> dict[str, list[SkillReq]]:
    """Load ``skills.yaml``: the skills each role requires or can use.

    Keyed by role; the key ``"*"`` applies to every role. Per skill, ``required`` and
    ``interactive`` default to false and ``fallback`` to null.
    """
    path = Path(path)
    data = _read_yaml(path)
    raw_roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(raw_roles, dict) or not raw_roles:
        raise ConfigError([f"{path.name}: expected a non-empty 'roles' mapping"])

    problems: list[str] = []
    registry: dict[str, list[SkillReq]] = {}
    for role, entries in raw_roles.items():
        if not isinstance(role, str):
            problems.append(f"{path.name}: role name {role!r} must be a string")
            continue
        prefix = f"{path.name}: role '{role}'"
        if not isinstance(entries, list):
            problems.append(f"{prefix}: must be a list of skills")
            continue
        skills: list[SkillReq] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            skill = _build_skill(f"{prefix}, entry {index}", entry, problems)
            if skill is None:
                continue
            if skill.name in seen:
                problems.append(f"{prefix}: skill '{skill.name}' is listed twice")
                continue
            seen.add(skill.name)
            skills.append(skill)
        registry[role] = skills
    if problems:
        raise ConfigError(problems)
    return registry


# --------------------------------------------------------------------------- project config

@dataclass
class ProjectConfig:
    """A project's ``factory.yaml`` with every default filled in."""

    project: str
    base_branch: str
    branching: str
    work_branch: str | None
    mode: str
    approvers: list[str]
    verify_quick: str
    verify_full: str
    builds: str
    size_cap: int
    generated: list[str]
    max_rounds: int
    hard_stops: list[str]
    role_overrides: dict
    github_mirror: bool


def load_project_config(path: Path) -> ProjectConfig:
    """Load a project's ``factory.yaml``, fill defaults, and validate.

    Unknown top-level keys are ignored: the file also carries settings that later parts of
    the factory read (design section 6.2). Raises ``ConfigError`` listing every problem.
    """
    path = Path(path)
    file = path.name
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError([f"{file}: expected a mapping of settings at the top level"])

    problems: list[str] = []

    def get(source: dict, key: str, default: Any) -> Any:
        value = source.get(key)
        return default if value is None else value

    def section(name: str) -> dict:
        value = get(data, name, {})
        if not isinstance(value, dict):
            problems.append(f"{file}: {name} must be a mapping")
            return {}
        return value

    def require_str(where: str, value: Any, *, required: bool = False) -> None:
        if not _is_str(value):
            problems.append(f"{file}: {where} is required (a non-empty string)" if required
                            else f"{file}: {where} must be a non-empty string")

    version = get(data, "version", 1)
    if not _is_int(version) or version != 1:
        problems.append(f"{file}: version {version!r} is not supported (expected 1)")

    project = data.get("project")
    require_str("project", project, required=True)

    base_branch = get(data, "base_branch", "main")
    require_str("base_branch", base_branch)

    branching = get(data, "branching", "per-item")
    _expect_choice(file, "branching", branching, BRANCHING, problems)

    work_branch = data.get("work_branch")
    if work_branch is not None:
        require_str("work_branch", work_branch)
    elif branching == "single":
        problems.append(f"{file}: work_branch is required when branching is 'single'")

    mode = get(data, "mode", "review")
    _expect_choice(file, "mode", mode, MODES, problems)

    approvers = data.get("approvers")
    if not _is_str_list(approvers) or not approvers:
        problems.append(f"{file}: approvers is required (a non-empty list of names)")

    verify = section("verify")
    require_str("verify.quick", verify.get("quick"), required=True)
    require_str("verify.full", verify.get("full"), required=True)

    builds = get(data, "builds", "allow")
    _expect_choice(file, "builds", builds, BUILDS, problems)

    merge = section("merge")
    size_cap = get(merge, "size_cap", 400)
    if not _is_int(size_cap) or size_cap < 1:
        problems.append(f"{file}: merge.size_cap must be a positive integer")
    generated = get(merge, "generated", [])
    if not _is_str_list(generated):
        problems.append(f"{file}: merge.generated must be a list of glob patterns")

    review = section("review")
    max_rounds = get(review, "max_rounds", 3)
    if not _is_int(max_rounds) or max_rounds < 1:
        problems.append(f"{file}: review.max_rounds must be a positive integer")

    hard_stops = get(data, "hard_stops", list(DEFAULT_HARD_STOPS))
    if not _is_str_list(hard_stops):
        problems.append(f"{file}: hard_stops must be a list of ids")

    role_overrides = get(data, "roles", {})
    if not isinstance(role_overrides, dict):
        problems.append(f"{file}: roles must be a mapping of role name to override")

    github_mirror = get(data, "github_mirror", False)
    if not isinstance(github_mirror, bool):
        problems.append(f"{file}: github_mirror must be true or false")

    if problems:
        raise ConfigError(problems)
    return ProjectConfig(
        project=project,
        base_branch=base_branch,
        branching=branching,
        work_branch=work_branch,
        mode=mode,
        approvers=list(approvers),
        verify_quick=verify["quick"],
        verify_full=verify["full"],
        builds=builds,
        size_cap=size_cap,
        generated=list(generated),
        max_rounds=max_rounds,
        hard_stops=list(hard_stops),
        role_overrides=dict(role_overrides),
        github_mirror=github_mirror,
    )


# --------------------------------------------------------------------------- whole-config check

def _check_review_md(path: Path, table: RoleTable | None) -> list[str]:
    """Problems with the ``REVIEW.md`` at ``path`` (none when there is no such file).

    A file that does not load is reported as such. One that loads is checked against ``table`` (the role table
    with the project's overrides) for a review panel that never runs on the other vendor; that check is skipped
    when there is no ``table`` to judge by.
    """
    # Imported here: reviewmd itself imports this module.
    from swfactory.reviewmd import check_panel_vendor_mix, load_review_md

    if not path.is_file():
        return []
    try:
        review = load_review_md(path)
    except ConfigError as err:
        return err.problems
    if table is None:
        return []
    return [f"{path.name}: {problem}" for problem in check_panel_vendor_mix(review, table)]


def check_config(project: Path | None = None) -> list[str]:
    """Validate the factory's own config, and a project's ``factory.yaml`` when given.

    ``project`` is a project folder or a ``factory.yaml`` path. When a ``REVIEW.md`` sits next to the
    ``factory.yaml`` it is checked too: that it loads, and that every track has a lens on the other vendor
    (judged with the project's role overrides, so only when its ``factory.yaml`` is valid). Returns every
    problem found (empty when all is well); never raises ``ConfigError``.
    """
    home = factory_home()
    problems: list[str] = []

    overrides: dict | None = None
    factory_yaml: Path | None = None
    project_loaded = False
    if project is not None:
        project = Path(project)
        factory_yaml = project / "factory.yaml" if project.is_dir() else project
        try:
            overrides = load_project_config(factory_yaml).role_overrides
            project_loaded = True
        except ConfigError as err:
            problems.extend(err.problems)

    table: RoleTable | None = None
    try:
        table = load_role_table(home / "config" / "roles.yaml", overrides)
    except ConfigError as err:
        problems.extend(err.problems)

    registry: dict[str, list[SkillReq]] | None = None
    try:
        registry = load_skills_registry(home / "config" / "skills.yaml")
    except ConfigError as err:
        problems.extend(err.problems)

    if table is not None and registry is not None:
        for role in registry:
            if role != "*" and role not in table.roles:
                problems.append(f"skills.yaml: role '{role}' is not in roles.yaml")

    if factory_yaml is not None:
        problems.extend(_check_review_md(factory_yaml.parent / "REVIEW.md", table if project_loaded else None))
    return problems
