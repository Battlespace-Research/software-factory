# Factory Core v1 Implementation Plan

> Names in this record are the pre-rename v1 names; see F16 in docs/decisions.md for the current names.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable version of the software factory: a `factory` CLI (Python) plus host-neutral skills, prompts and subagents that set up a project, triage work, and run the Fix and Feature tracks through the task loop with a cross-vendor review panel and a merge gate, installable as a plugin in Claude Code and Codex.

**Architecture:** Deterministic work (config, role resolution, vendor CLI invocation, review parsing, ids, approvals, merge checks, setup scaffolding, skills readiness) lives in a small Python package with tests. Judgment work (interviews, triage, implementing, reviewing) lives in SKILL.md files and prompt templates that call the CLI. Every model name lives in `config/roles.yaml`; nothing else names one.

**Tech Stack:** Python ≥ 3.11, PyYAML (only runtime dependency), pytest (dev), git, the `claude` and `codex` CLIs, Markdown skills, JSON plugin manifests.

**Spec:** `docs/specs/2026-10-20-software-factory-design.md` (rev 2, approved F15). Decisions: `docs/decisions.md`.

**Plan series:** this is plan 1 of 4. Plan 2: dashboard generator, map generator, Program-track milestone and demo tooling, optional GitHub issues mirror. Plan 3: Deploy paths (desktop installer, Helm) and Maintain. Plan 4: factory evals (local runner, then CI). Hooks stay parked (design §14).

## Global Constraints

- Python ≥ 3.11; runtime dependency PyYAML only; tests with pytest. Runs on Windows (PowerShell 7, Git Bash) and Linux.
- Import package `swfactory` in `tools/swfactory/`; console script `factory`; install with `pip install -e tools`.
- Factory home = env `FACTORY_HOME` if set, else the repo root two levels above `tools/swfactory/`.
- **No model name anywhere except `config/roles.yaml`** (tests and docs may quote them). Skills, prompts, agents, templates and `swfactory` code refer to roles only.
- Never write to `E:\BSI\GitHub\MACE-NGC2-Prototype`. Tests use temp directories only.
- Subprocesses use argv lists, never shell strings — except the project's own verify commands, which are user-supplied command strings run with `shell=True`.
- Work ids: `PRG-<n>`, `FEA-<n>`, `FIX-<n>`, numbered per track per project, never renumbered; a collision gets a letter suffix (`FEA-12b`).
- Work folder: `factory/work/<ID>-<slug>/`; branch `factory/<ID>-<slug>` when `branching: per-item`.
- Review files: `reviews/<task>-r<round>-<lens>.md`; panel summary `reviews/<task>-r<round>-panel.md`; disposition `reviews/disposition-<task>-r<round>.md`. First line of a review or summary: `VERDICT: PASS`, `VERDICT: CHANGES` (summary also `VERDICT: INCOMPLETE`).
- Severities: `blocker`, `major`, `minor`, `nit`; at most 5 nits per review (`nit_cap`). Max review rounds 3; the third failed round is a hard stop.
- Lens sets: fix = correctness, security; feature = correctness, security, maintainability; program = correctness, security, maintainability, spec. Maintainability findings never block.
- Merge size cap default 400 changed lines, excluding generated globs and binary files.
- Default hard stops (ids): `main-branch`, `delete-data`, `credentials`, `installers-admin`, `purchases`, `auth-money-schema`, `new-dependency`, `production-data`, `third-failed-round`, `business-assumption`.
- Commit trailers: `Factory-Work: <ID>[/<task>]`, `Factory-Role: <role>`, `Factory-Model: <model> (<effort>)`.
- Approvals are recorded only from an owner's chat message, verbatim (F13).
- Do not copy text from zazencodes (no license). Anything borrowed from super-board keeps its MIT notice.

## Review Focus

1. **Windows line endings and paths.** A reviewer file saved with CRLF or a UTF-8 BOM, and paths containing spaces, must parse and run exactly like LF/space-free ones. Tests: Task 3 `test_parse_crlf_and_bom`, Task 2 `test_paths_with_spaces`.
2. **Missing or garbled reviewer output.** An absent, empty, or malformed review file (first line not a verdict, bad table, invalid JSON) must make the panel `INCOMPLETE`, never `PASS`. Tests: Task 3 `test_first_line_not_verdict_raises`, `test_combine_missing_lens_incomplete`, `test_combine_invalid_lens_incomplete`.
3. **Git edge cases in id allocation.** A repo with no remote, a base branch named `master`, no `factory/work` folder yet, or a detached HEAD must give `FIX-1` or a clear error, not a crash. Tests: Task 4 `test_next_id_empty_repo_is_1`, `test_detached_head_error_message`.
4. **Re-running setup.** Running setup on an already set-up repo, or on a repo whose `CLAUDE.md` already has a factory section, must refuse or update in place, never duplicate or overwrite. Tests: Task 7 `test_setup_refuses_second_run`, `test_section_markers_idempotent`.
5. **Vendor CLI missing or failing.** `codex` or `claude` not installed, exiting non-zero, or timing out must return a failure that suggests the fallback and must leave no file that looks like a verdict. Test: Task 2 `test_run_invocation_failure_leaves_no_output`.

---

### Task 1: Package skeleton, config loading and the default role table

**Files:**
- Create: `tools/pyproject.toml`, `tools/swfactory/__init__.py` (`__version__ = "0.1.0"`), `tools/swfactory/__main__.py`, `tools/swfactory/cli.py`, `tools/swfactory/config.py`
- Create: `config/roles.yaml`, `config/skills.yaml`
- Test: `tools/tests/conftest.py`, `tools/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `factory_home() -> Path`
  - `@dataclass(frozen=True) Binding(model: str, effort: str)`
  - `@dataclass(frozen=True) RoleSpec(name: str, stage: str, vendor: str, run_as: str, access: str, bindings: dict[str, Binding], fallback: dict[str, Binding])` — `vendor` ∈ `host|same|other|claude|openai`; `run_as` ∈ `main|subagent|headless`; `access` ∈ `write|read-only|no-product-edits`; `bindings`/`fallback` keyed `claude`, `openai`.
  - `@dataclass RoleTable(roles: dict[str, RoleSpec])`
  - `load_role_table(path: Path, overrides: dict | None = None) -> RoleTable`
  - `@dataclass(frozen=True) SkillReq(name: str, required: bool, fallback: str | None, interactive: bool)`
  - `load_skills_registry(path: Path) -> dict[str, list[SkillReq]]` (keyed by role; key `"*"` applies to all roles)
  - `@dataclass ProjectConfig(project: str, base_branch: str, branching: str, work_branch: str | None, mode: str, approvers: list[str], verify_quick: str, verify_full: str, builds: str, size_cap: int, generated: list[str], max_rounds: int, hard_stops: list[str], role_overrides: dict, github_mirror: bool)`
  - `load_project_config(path: Path) -> ProjectConfig`
  - `DEFAULT_HARD_STOPS: list[str]` (the ten ids in Global Constraints)
  - `class ConfigError(Exception)` with `.problems: list[str]`
  - CLI: `factory --version`; `factory config check [--project PATH]` (exit 0 ok, 1 with problems printed one per line). `swfactory.cli.COMMANDS: set[str]` lists every top-level subcommand (later tasks add to it).

`config/roles.yaml` defines these roles with a binding **and** a fallback for both vendors (models from design §7.1; the OpenAI side of Claude-default roles uses `gpt-6.1-sol` at the same effort, fallback `gpt-6-astra`; Claude fallbacks step down Opus/Fable → Sonnet):

| role | stage | vendor | run_as | access | claude binding | openai binding |
|---|---|---|---|---|---|---|
| interviewer | plan | host | main | write | claude-fable-5-1 xhigh | gpt-6.1-sol xhigh |
| researcher | plan | host | subagent | read-only | claude-sonnet-5-5 high | gpt-6.1-sol high |
| author | design | host | main | write | claude-fable-5-1 xhigh | gpt-6.1-sol xhigh |
| doc-reviewer-a | design | same | subagent | read-only | claude-opus-5-5 xhigh | gpt-6.1-sol xhigh |
| doc-reviewer-b | design | other | headless | read-only | claude-opus-5-5 xhigh | gpt-6-astra xhigh |
| implementer | build | host | main | write | claude-opus-5-5 xhigh | gpt-6.1-sol xhigh |
| lens-correctness | build | other | headless | read-only | claude-opus-5-5 xhigh | gpt-6.1-sol xhigh |
| lens-security | build | other | headless | read-only | claude-opus-5-5 xhigh | gpt-6.1-sol xhigh |
| lens-maintainability | build | same | subagent | read-only | claude-sonnet-5-5 high | gpt-6.1-sol high |
| lens-spec | build | same | subagent | read-only | claude-opus-5-5 high | gpt-6.1-sol high |
| tester | test | host | subagent | no-product-edits | claude-sonnet-5-5 high | gpt-6.1-sol high |
| release-checker | deploy | host | headless | read-only | claude-sonnet-5-5 high | gpt-6.1-sol high |
| triage | maintain | host | headless | read-only | claude-sonnet-5-5 high | gpt-6.1-sol high |

`config/skills.yaml` lists, per role, the third-party skills from design §13 with `required`, `fallback`, `interactive` (`grilling` and `grill-with-docs` are interactive).

`factory.yaml` keys and defaults (loader fills defaults): `version: 1`, `project` (required), `base_branch: main`, `branching: per-item` (`per-item|single`), `work_branch: null` (required when single), `mode: review` (`review|auto`), `approvers` (required, non-empty), `verify.quick` and `verify.full` (required), `builds: allow` (`allow|ask`), `merge.size_cap: 400`, `merge.generated: []`, `review.max_rounds: 3`, `hard_stops: DEFAULT_HARD_STOPS`, `roles: {}`, `github_mirror: false`.

- [ ] **Step 1: Write the failing tests** in `tools/tests/test_config.py`:

```python
def test_default_role_table_loads():
    t = load_role_table(factory_home() / "config/roles.yaml")
    assert t.roles["lens-correctness"].vendor == "other"
    assert t.roles["implementer"].bindings["claude"] == Binding("claude-opus-5-5", "xhigh")

def test_every_role_has_binding_and_fallback_for_both_vendors():
    t = load_role_table(factory_home() / "config/roles.yaml")
    for r in t.roles.values():
        assert set(r.bindings) == {"claude", "openai"} and set(r.fallback) == {"claude", "openai"}

def test_unknown_vendor_rejected(tmp_path):  # roles.yaml with vendor: banana
    with pytest.raises(ConfigError) as e: load_role_table(path)
    assert any("lens-correctness" in p and "vendor" in p for p in e.value.problems)

def test_project_override_replaces_one_binding():  # overrides={"tester": {"bindings": {"claude": {"model": "x", "effort": "low"}}}}
    assert t.roles["tester"].bindings["claude"] == Binding("x", "low")
    assert t.roles["tester"].bindings["openai"].model == "gpt-6.1-sol"

def test_project_config_defaults(tmp_path):  # minimal factory.yaml: project, approvers, verify.quick, verify.full
    c = load_project_config(p)
    assert (c.size_cap, c.max_rounds, c.mode, c.branching, c.builds) == (400, 3, "review", "per-item", "allow")
    assert c.hard_stops == DEFAULT_HARD_STOPS

def test_project_config_rejects_bad_mode_and_single_without_branch(tmp_path):
    # mode: turbo; branching: single with no work_branch -> two problems in one ConfigError
```

- [ ] **Step 2: Run** `pytest tools/tests/test_config.py -v` — Expected: FAIL (`ModuleNotFoundError: swfactory`).
- [ ] **Step 3: Implement** `tools/pyproject.toml` (setuptools, `[project.scripts] factory = "swfactory.cli:main"`, deps `pyyaml`, optional `dev = ["pytest"]`), the package files, `config.py`, `config/roles.yaml`, `config/skills.yaml`, and `cli.py` with `--version` and `config check`. Validation collects all problems before raising.
- [ ] **Step 4: Run** `pip install -e "tools[dev]"` then `pytest tools/tests -v` — Expected: PASS. Run `factory --version` — Expected: `0.1.0`.
- [ ] **Step 5: Commit** — `git add tools config && git commit -m "Add swfactory package, config loading and default role table"`

---

### Task 2: Role resolution and host adapters

**Files:**
- Create: `tools/swfactory/roles.py`, `tools/swfactory/adapters.py`, `tools/tests/fakes/fake_cli.py`
- Modify: `tools/swfactory/cli.py` (add `role` command)
- Test: `tools/tests/test_roles.py`, `tools/tests/test_adapters.py`

**Interfaces:**
- Consumes: `RoleTable`, `RoleSpec`, `Binding` (Task 1).
- Produces:
  - `Vendor = Literal["claude", "openai"]`; `other_vendor(v: Vendor) -> Vendor`
  - `@dataclass(frozen=True) ResolvedRole(role: str, vendor: Vendor, model: str, effort: str, run_as: str, access: str, fallback_used: bool)`
  - `resolve_role(table: RoleTable, role: str, host: Vendor, author: Vendor | None = None, use_fallback: bool = False) -> ResolvedRole` — `host`→host; `same`→author (host if None); `other`→opposite of author (of host if None); explicit vendor as is.
  - `@dataclass(frozen=True) Invocation(kind: Literal["main", "subagent", "command"], argv: list[str] | None, agent: str | None, model_alias: str | None, effort: str, sandbox: str)`
  - `plan_invocation(r: ResolvedRole, host: Vendor, out_path: Path, schema_path: Path | None = None) -> Invocation`
  - `@dataclass RunResult(ok: bool, exit_code: int | None, out_path: Path, stderr_tail: str, suggest_fallback: bool)`
  - `run_invocation(inv: Invocation, r: ResolvedRole, prompt_text: str, out_path: Path, timeout_s: int = 3600) -> RunResult`
  - CLI: `factory role resolve ROLE --host {claude,openai} [--author {claude,openai}] [--fallback]` prints the ResolvedRole and Invocation as JSON; `factory role run ROLE --host H --author A --prompt FILE --out FILE [--schema FILE] [--fallback]` runs command-kind invocations (exit 0/1) and, for subagent-kind, prints `spawn subagent <agent> with model <alias>` and exits 3.

Invocation rules:
- `run_as: main` → kind `main` (the session itself does it).
- Vendor equals host and `run_as` is `subagent` or `headless`, host `claude` → kind `subagent`; `agent` from `AGENT_FOR_ROLE = {"lens-*": "factory-lens-reviewer", "doc-reviewer-*": "factory-doc-reviewer", "tester": "factory-tester", "researcher": "factory-researcher"}` (glob match); `model_alias` = regex group of `^claude-([a-z]+)-` on the model.
- Vendor `openai` and not main → kind `command`, `argv = ["codex", "exec", "-m", model, "-c", f'model_reasoning_effort="{effort}"', "-s", sandbox, "-o", str(out_path)] + (["--output-schema", str(schema)] if schema else []) + ["-"]` (prompt on stdin). `sandbox = "read-only"` unless `access == "write"` (`"workspace-write"`).
- Vendor `claude` on host `openai` → kind `command`, `argv = ["claude", "-p", "--model", model, "--output-format", "json", "--permission-mode", "plan", "--allowedTools", "Read,Grep,Glob"]`, prompt on stdin; the JSON `result` field is written to `out_path`.
- Flag spellings live in module constants `CODEX_EXEC_FLAGS` and `CLAUDE_PRINT_FLAGS` so a CLI rename is a one-line fix.
- `run_invocation` writes the reviewer's text to `out_path` and appends `<!-- factory: role=… vendor=… model=… effort=… sandbox=… at=<UTC ISO> -->` as the last line. On a missing executable, non-zero exit or timeout it deletes any partial `out_path` and returns `ok=False, suggest_fallback=True`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_other_resolves_opposite_of_author(): assert resolve_role(t, "lens-correctness", host="claude", author="claude").vendor == "openai"
def test_same_without_author_uses_host(): assert resolve_role(t, "lens-spec", host="openai").vendor == "openai"
def test_fallback_binding(): r = resolve_role(t, "implementer", "claude", use_fallback=True); assert r.fallback_used and r.model == "claude-sonnet-5-5"
def test_codex_argv_for_claude_host_lens(tmp_path):
    inv = plan_invocation(resolve_role(t, "lens-security", "claude", "claude"), "claude", tmp_path/"o.md", tmp_path/"s.json")
    assert inv.kind == "command" and inv.argv[:4] == ["codex", "exec", "-m", "gpt-6.1-sol"] and "read-only" in inv.argv and inv.argv[-1] == "-"
def test_subagent_for_claude_lens_on_claude_host(tmp_path):
    inv = plan_invocation(resolve_role(t, "lens-spec", "claude", "claude"), "claude", tmp_path/"o.md")
    assert (inv.kind, inv.agent, inv.model_alias) == ("subagent", "factory-lens-reviewer", "opus")
def test_claude_argv_on_openai_host(tmp_path): # lens-correctness, host openai, author openai -> argv[0:2] == ["claude", "-p"]
def test_run_invocation_success_with_fake_cli(tmp_path, fake_cli):  # fake prints "VERDICT: PASS"
    res = run_invocation(inv, r, "prompt", out); assert res.ok and out.read_text().splitlines()[0] == "VERDICT: PASS"
    assert out.read_text().rstrip().splitlines()[-1].startswith("<!-- factory: role=lens-security")
def test_run_invocation_failure_leaves_no_output(tmp_path, fake_cli):  # fake exits 7 after writing partial output
    res = run_invocation(...); assert not res.ok and res.suggest_fallback and not out.exists()
def test_missing_executable(tmp_path):  # argv[0] = "no-such-cli-xyz" -> ok False, suggest_fallback True
def test_paths_with_spaces(tmp_path, fake_cli):  # out path in "dir with space" -> ok, file written
```

`fake_cli` fixture: puts `tools/tests/fakes/fake_cli.py` on PATH as `codex`/`claude` shims (a `.cmd` wrapper on Windows, a shebang script elsewhere); its behaviour is set by env `FAKE_CLI_MODE` (`pass`, `fail7`, `json`).

- [ ] **Step 2: Run** `pytest tools/tests/test_roles.py tools/tests/test_adapters.py -v` — Expected: FAIL (imports).
- [ ] **Step 3: Implement** `roles.py`, `adapters.py`, the fake CLI and the `role` CLI command.
- [ ] **Step 4: Run** the tests — Expected: PASS. Then check the real CLIs: `codex exec --help` and `claude --help`. Expected: every flag in `CODEX_EXEC_FLAGS` and `CLAUDE_PRINT_FLAGS` appears, and `codex exec` accepts `-` for stdin. If a flag is renamed, change only the constant and re-run.
- [ ] **Step 5: Commit** — `git commit -m "Add role resolution and Claude/Codex host adapters"`

---

### Task 3: REVIEW.md, review files and the review panel

**Files:**
- Create: `tools/swfactory/reviewmd.py`, `tools/swfactory/verdict.py`, `tools/swfactory/panel.py`, `schemas/review.schema.json`
- Modify: `tools/swfactory/cli.py` (add `panel`)
- Test: `tools/tests/test_reviewmd.py`, `tools/tests/test_verdict.py`, `tools/tests/test_panel.py`

**Interfaces:**
- Consumes: `RoleTable`, `resolve_role` (Tasks 1–2).
- Produces:
  - `@dataclass ReviewConfig(lenses: dict[str, str], tracks: dict[str, list[str]], nit_cap: int, ignore: list[str], always_flag: list[str])`
  - `load_review_md(path: Path) -> ReviewConfig` — YAML front matter (`lenses`, `tracks`, `nit_cap`, `ignore`, `always_flag`) plus one `## Lens: <name>` section per lens holding its checklist.
  - `check_panel_vendor_mix(cfg: ReviewConfig, table: RoleTable) -> list[str]` — a problem for each track whose lenses have no role `lens-<name>` with `vendor: other`.
  - `@dataclass(frozen=True) Finding(id: str, severity: str, location: str, summary: str, fix: str)`
  - `@dataclass(frozen=True) PriorStatus(id: str, status: str)` — status ∈ `fixed|not fixed|no longer applies`
  - `@dataclass Review(lens: str, verdict: str, findings: list[Finding], prior: list[PriorStatus], warnings: list[str])`
  - `class ReviewFormatError(ValueError)`
  - `parse_review_markdown(text: str, lens: str, nit_cap: int = 5) -> Review`; `parse_review_json(data: dict, lens: str, nit_cap: int = 5) -> Review`
  - `review_path(work_dir: Path, task: str, rnd: int, lens: str) -> Path`
  - `@dataclass PanelResult(verdict: str, blocking: list[tuple[str, Finding]], follow_ups: list[tuple[str, Finding]], missing: list[str], invalid: dict[str, str], commit: str)`
  - `combine(work_dir: Path, task: str, rnd: int, lenses: list[str], commit: str) -> PanelResult`
  - `write_panel_summary(work_dir: Path, task: str, rnd: int, result: PanelResult) -> Path` — first line `VERDICT: …`, second line `Commit: <sha>`, then blocking, follow-ups, missing.
  - `latest_passing_commit(work_dir: Path) -> str | None` — commit from the newest `*-panel.md` whose verdict is PASS.
  - CLI: `factory panel combine WORK_DIR --task T --round N [--commit SHA]` (lenses from the project's REVIEW.md for the work item's track; commit defaults to `HEAD`); exit 0 PASS, 1 CHANGES, 2 INCOMPLETE.

Review markdown format (also documented in the lens prompts, Task 9):

```
VERDICT: CHANGES
## Earlier findings
| ID | Status |
| R1 | fixed |
## Findings
| ID | Severity | Location | Finding | Fix |
| R2 | major | src/x.py:40 | ... | ... |
```

Combine rules: a lens with no file or an unparseable file → `INCOMPLETE` (listed in `missing`/`invalid`). Every maintainability finding goes to `follow_ups`, whatever its severity. Blocker and major findings from any other lens go to `blocking`. Verdict `CHANGES` when `blocking` is non-empty or a non-maintainability lens says `CHANGES`; otherwise `PASS`. A lens that says `CHANGES` with no blocker or major gets a warning, and is still treated as `CHANGES`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_parse_pass_minimal(): assert parse_review_markdown("VERDICT: PASS\n", "security").verdict == "PASS"
def test_parse_crlf_and_bom(): assert parse_review_markdown("\ufeffVERDICT: CHANGES\r\n## Findings\r\n| ID | Severity | Location | Finding | Fix |\r\n|---|---|---|---|---|\r\n| R1 | major | a.py:1 | x | y |\r\n", "correctness").findings[0].severity == "major"
def test_first_line_not_verdict_raises(): with pytest.raises(ReviewFormatError): parse_review_markdown("Looks good!\nVERDICT: PASS", "spec")
def test_prior_statuses(): assert review.prior == [PriorStatus("R1", "fixed")]
def test_nit_overflow_warning(): # six nit rows, nit_cap 5 -> one warning mentioning "6 nits"
def test_json_form_matches_markdown_form():  # same content via parse_review_json equals markdown parse
def test_review_schema_accepts_sample():  # load schemas/review.schema.json; sample has keys verdict, findings[], prior[]
def test_load_review_md_tracks(): assert cfg.tracks["fix"] == ["correctness", "security"] and "## Lens" not in cfg.lenses["security"]
def test_track_without_other_vendor_lens_flagged():  # tracks: {fix: [maintainability]} -> one problem naming "fix"
def test_combine_missing_lens_incomplete(tmp_path):  # only correctness file present for fix -> verdict INCOMPLETE, missing == ["security"]
def test_combine_invalid_lens_incomplete(tmp_path):  # security file "oops" -> INCOMPLETE, "security" in invalid
def test_combine_major_blocks(tmp_path):  # correctness R1 major -> CHANGES, blocking[0][0] == "correctness"
def test_maintainability_major_becomes_follow_up(tmp_path):  # maintainability R1 major only -> PASS, follow_ups has it
def test_summary_records_commit_and_latest_passing(tmp_path):  # write summary PASS commit abc -> latest_passing_commit == "abc"
```

- [ ] **Step 2: Run** `pytest tools/tests/test_reviewmd.py tools/tests/test_verdict.py tools/tests/test_panel.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the three modules, the schema (`verdict` enum PASS/CHANGES; `findings[]` with `id, severity, location, summary, fix`; `prior[]` with `id, status`) and the `panel` CLI.
- [ ] **Step 4: Run** the tests — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add REVIEW.md loader, review parsing and panel combine"`

---

### Task 4: Work items, ids, status and trailers

**Files:**
- Create: `tools/swfactory/gitutil.py`, `tools/swfactory/workitems.py`, `tools/swfactory/trailers.py`
- Create: `templates/work/fix/{ticket.md,progress.md,decisions.md}`, `templates/work/feature/{intent.md,spec.md,plan.md,progress.md,decisions.md,retro.md}`, `templates/work/program/{intent.md,spec.md,plan.md,progress.md,decisions.md,retro.md}` (each track also gets empty `reviews/` and `approvals/`; program also `research/`, `demo/`)
- Modify: `tools/swfactory/cli.py` (add `work`)
- Test: `tools/tests/test_workitems.py`; extend `conftest.py` with fixture `git_repo(tmp_path, base="main", with_origin=True)`.

**Interfaces:**
- Consumes: `ProjectConfig` (Task 1).
- Produces:
  - `class GitError(RuntimeError)`; `run_git(repo: Path, *args: str) -> str`
  - `TRACK_PREFIX = {"program": "PRG", "feature": "FEA", "fix": "FIX"}`
  - `ids_in_use(repo: Path, base: str) -> set[str]` — ids from `factory/work/*` in the base tree plus `refs/heads/factory/*` and `refs/remotes/*/factory/*`.
  - `next_id(repo: Path, track: str, base: str) -> str` — highest number for the track + 1; `FIX-1` when none.
  - `with_letter_suffix(id_: str, taken: set[str]) -> str` — `FEA-12` → `FEA-12b`, then `FEA-12c`.
  - `@dataclass WorkItem(id: str, track: str, slug: str, title: str, branch: str | None, path: Path)`
  - `create_work_item(repo: Path, cfg: ProjectConfig, track: str, slug: str, title: str) -> WorkItem` — validates slug `^[a-z0-9-]{1,40}$`; per-item: `git switch -c factory/<ID>-<slug> <base>`; single: must already be on `cfg.work_branch`; copies the track's templates replacing `{{id}}`, `{{title}}`, `{{track}}`, `{{date}}`; commits `"<ID>: open work item"` with `Factory-Work: <ID>` trailer.
  - `find_work_item(repo: Path, id_: str) -> WorkItem`
  - `@dataclass Status(stage: str, state: str, next: str, updated: str)`; `read_status(work_dir: Path) -> Status`; `write_status(work_dir: Path, stage: str, state: str, next: str, note: str) -> Status` — rewrites line 1 of `progress.md` as `Status: <stage> · <state> · next: <next> · updated <UTC ISO>` and inserts `note` as the newest entry under `## Log`.
  - `format_trailers(work: str, role: str, model: str, effort: str) -> str`
  - CLI: `factory work new --track {fix,feature,program} --slug S --title T`; `factory work status ID`; `factory work set-status ID --stage S --state S --next TEXT --note TEXT`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_next_id_empty_repo_is_1(git_repo_no_remote_master): assert next_id(repo, "fix", "master") == "FIX-1"
def test_next_id_sees_remote_branch(git_repo):  # push branch factory/FEA-4-x to origin, delete locally -> next_id(feature) == "FEA-5"
def test_next_id_sees_base_tree(git_repo):  # commit factory/work/FIX-9-y/ on main -> "FIX-10"
def test_letter_suffix(): assert with_letter_suffix("FEA-12", {"FEA-12", "FEA-12b"}) == "FEA-12c"
def test_create_fix_item_files(git_repo, cfg):
    w = create_work_item(repo, cfg, "fix", "licence-crash", "Crash when licence missing")
    assert w.branch == "factory/FIX-1-licence-crash" and (w.path / "ticket.md").exists() and (w.path / "reviews").is_dir()
    assert "Factory-Work: FIX-1" in run_git(repo, "log", "-1", "--format=%B")
def test_create_feature_item_files(git_repo, cfg):  # intent.md, spec.md, plan.md, retro.md present; no ticket.md
def test_bad_slug_rejected(git_repo, cfg):  # "Bad Slug!" -> ValueError
def test_single_branching_requires_work_branch(git_repo, cfg_single):  # not on work branch -> GitError mentioning the branch name
def test_status_roundtrip(tmp_path):  # write_status then read_status returns same stage/state/next; log has note first
def test_detached_head_error_message(git_repo, cfg):  # detached HEAD + single branching -> GitError("detached HEAD ...")
def test_format_trailers(): assert format_trailers("FEA-7/T3", "implementer", "m", "xhigh").splitlines() == ["Factory-Work: FEA-7/T3", "Factory-Role: implementer", "Factory-Model: m (xhigh)"]
```

- [ ] **Step 2: Run** `pytest tools/tests/test_workitems.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the modules, templates and `work` CLI. Template content: each file opens with the section headings the design §8.1 lists for it (`ticket.md`: Problem, Repro steps, Acceptance criteria, Out of scope; `progress.md`: Status line then `## Log`).
- [ ] **Step 4: Run** the tests — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add work items, id allocation, status and trailers"`

---

### Task 5: Approvals

**Files:**
- Create: `tools/swfactory/approvals.py`
- Modify: `tools/swfactory/cli.py` (add `approve`)
- Test: `tools/tests/test_approvals.py`

**Interfaces:**
- Consumes: `ProjectConfig`, `find_work_item` (Tasks 1, 4).
- Produces:
  - `class ApprovalError(ValueError)`
  - `@dataclass Approval(artifact: str, rev: str, approver: str, when: str, host: str, session: str, commit: str, quote: str, path: Path)`
  - `record_approval(work_dir: Path, cfg: ProjectConfig, artifact: str, rev: str, quote: str, approver: str, commit: str, host: str = "", session: str = "") -> Approval` — writes `approvals/<artifact>-rev<rev>.md`: lines `Artifact:`, `Revision:`, `Approved by:`, `When:` (UTC ISO), `Host:`, `Session:`, `Commit:`, then `Quote:` and the quote verbatim as a `>` block, every line preserved.
  - `latest_approval(work_dir: Path, artifact: str) -> Approval | None` — highest revision by numeric comparison of dotted parts.
  - CLI: `factory approve ID ARTIFACT --rev R --quote Q --approver A --commit C [--host H] [--session S]`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_record_writes_verbatim_quote(tmp_path, cfg):
    a = record_approval(wd, cfg, "spec", "2", 'Approved — "ship it"\nthanks', "Cord", "abc123")
    assert '> Approved — "ship it"' in a.path.read_text(encoding="utf-8") and "> thanks" in a.path.read_text(encoding="utf-8")
def test_empty_quote_refused(tmp_path, cfg): with pytest.raises(ApprovalError): record_approval(wd, cfg, "spec", "1", "   ", "Cord", "abc")
def test_unknown_approver_refused(tmp_path, cfg): # approver "Mallory" not in cfg.approvers -> ApprovalError
def test_existing_record_not_overwritten(tmp_path, cfg): # second call same artifact/rev -> ApprovalError, file unchanged
def test_latest_approval_picks_highest_rev(tmp_path, cfg): # revs "2", "10", "2.1" -> latest is "10"
```

- [ ] **Step 2: Run** `pytest tools/tests/test_approvals.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `approvals.py` and the CLI command.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add approval records from owner chat messages"`

---

### Task 6: Merge gate

**Files:**
- Create: `tools/swfactory/mergegate.py`
- Modify: `tools/swfactory/cli.py` (add `gate`)
- Test: `tools/tests/test_mergegate.py`

**Interfaces:**
- Consumes: `ProjectConfig`, `WorkItem`, `find_work_item`, `run_git`, `ids_in_use`, `latest_passing_commit` (Tasks 1, 3, 4).
- Produces:
  - `@dataclass(frozen=True) GateCheck(name: str, ok: bool, detail: str)`
  - `@dataclass GateReport(checks: list[GateCheck], ok: bool)`
  - `run_gate(repo: Path, cfg: ProjectConfig, item: WorkItem, reviewed_commit: str, spot_check: str, run_verify: bool = True, builds_approved: bool = False) -> GateReport`
  - `write_gate_report(work_dir: Path, report: GateReport) -> Path` — `merge-gate.md`, first line `GATE: PASS` or `GATE: FAIL`.
  - CLI: `factory gate ID --spot-check TEXT [--reviewed-commit SHA] [--no-verify] [--builds-approved]` (reviewed commit defaults to `latest_passing_commit`); exit 0/1.

Checks, in order, all reported (no early exit):
1. `base-merged` — `git merge-base --is-ancestor <base> HEAD` (uses `origin/<base>` when it exists).
2. `reviewed-commit` — `rev-parse HEAD == rev-parse reviewed_commit`.
3. `size-cap` — sum of added+deleted from `git diff --numstat <base>...HEAD`, skipping binary rows (`-`) and paths matching `cfg.generated` (fnmatch); detail `"<n> lines (cap <cap>)"`, failure detail adds `"split the work"`.
4. `id-unused-on-base` — no other `factory/work/<item.id>-*` folder on the base.
5. `verify-full` — skipped (ok, detail `skipped`) when `run_verify` is False; when `cfg.builds == "ask"` and not `builds_approved` → fail with detail `ask the owner before the full build`; else runs `cfg.verify_full` with `shell=True`, ok on exit 0, detail = last 40 output lines.
6. `spot-check` — ok when `spot_check.strip()` is non-empty.

- [ ] **Step 1: Write the failing tests** (temp repos from the `git_repo` fixture):

```python
def test_gate_passes_clean_branch(...): assert report.ok and [c.name for c in report.checks] == ["base-merged", "reviewed-commit", "size-cap", "id-unused-on-base", "verify-full", "spot-check"]
def test_base_not_merged_fails(...)            # base gets a new commit after branching -> base-merged False
def test_unreviewed_commit_fails(...)          # extra commit after review -> reviewed-commit False
def test_size_cap_counts_and_excludes_generated(...)  # 300 lines in src + 500 in gen/x.g.cs with generated ["gen/*"], cap 400 -> ok, detail "300 lines (cap 400)"
def test_size_cap_exceeded_suggests_split(...)  # 450 lines -> False, "split the work" in detail
def test_binary_files_ignored(...)
def test_builds_ask_without_approval_fails(...)  # cfg.builds = "ask" -> verify-full False, "ask the owner" in detail
def test_verify_failure_reports_tail(...)  # verify_full = python -c "print('boom'); raise SystemExit(1)" -> False, "boom" in detail
def test_empty_spot_check_fails(...)
def test_report_file_first_line(...)  # write_gate_report -> first line "GATE: FAIL" when any check fails
```

- [ ] **Step 2: Run** `pytest tools/tests/test_mergegate.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `mergegate.py` and the CLI command.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add merge gate checks"`

---

### Task 7: Project setup

**Files:**
- Create: `tools/swfactory/setup.py`
- Create: `templates/project/factory.yaml`, `templates/project/REVIEW.md`, `templates/project/claude-section.md`, `templates/project/lessons.md`, `templates/project/expected-failures.md`
- Modify: `tools/swfactory/cli.py` (add `setup`)
- Test: `tools/tests/test_setup.py`

**Interfaces:**
- Consumes: `load_project_config`, `load_review_md` (Tasks 1, 3).
- Produces:
  - `@dataclass SetupAnswers(project: str, verify_quick: str, verify_full: str, approvers: list[str], base_branch: str = "main", branching: str = "per-item", work_branch: str | None = None, builds: str = "allow", mode: str = "review")`
  - `@dataclass SetupResult(created: list[Path], updated: list[Path], conflicts: list[str])`
  - `class SetupError(RuntimeError)`
  - `setup_project(repo: Path, answers: SetupAnswers) -> SetupResult` — refuses (`SetupError("already set up")`) if `factory.yaml` exists; writes `factory.yaml` and `REVIEW.md` from templates; creates `factory/work/`, `factory/baseline/expected-failures.md`, `factory/lessons.md`; adds `factory/dashboard/` to `.gitignore` once; writes the factory section between `<!-- factory:begin -->` and `<!-- factory:end -->` into `CLAUDE.md` and `AGENTS.md` where each exists (creates `CLAUDE.md` if neither does), replacing an existing marked section in place; does **not** commit.
  - `find_conflicts(repo: Path) -> list[str]` — `"<file>:<line>: <text>"` for lines in `AGENTS.md`/`CLAUDE.md` matching (case-insensitive) `do not create (git )?branches`, `do not build`, `commit (directly )?to main`, `never push`.
  - CLI: `factory setup --project NAME --verify-quick CMD --verify-full CMD --approver NAME [--approver NAME…] [--base B] [--branching per-item|single] [--work-branch B] [--builds allow|ask] [--mode review|auto]` prints the SetupResult as JSON.

`REVIEW.md` template front matter: `lenses: [correctness, security, maintainability, spec]`, `tracks: {fix: [correctness, security], feature: [correctness, security, maintainability], program: [correctness, security, maintainability, spec]}`, `nit_cap: 5`, `ignore: []`, `always_flag: []`. Lens sections cover — correctness: logic, edge cases, error handling, tests prove the behaviour, concurrency; security: input validation, authentication and authorization, secrets, injection, unsafe deserialization, risky dependencies; maintainability (never blocks): duplication, module boundaries, naming, dead code, documentation of public surface; spec: every requirement and acceptance criterion addressed, nothing outside scope added, the plan task fulfilled. The `expected-failures.md` template states the contract: the project's full verify command compares its own results with this list and exits 0 when only listed failures occur (the factory treats the exit code as the verdict). The `claude-section.md` text says where work lives, how to resume (`factory work status`, `progress.md`), the verify command, and that reviewers and the tester never edit product code.

- [ ] **Step 1: Write the failing tests:**

```python
def test_setup_creates_files(tmp_path):
    res = setup_project(repo, answers)
    assert (repo/"factory.yaml").exists() and (repo/"REVIEW.md").exists() and (repo/"factory/baseline/expected-failures.md").exists()
    assert load_project_config(repo/"factory.yaml").verify_full == answers.verify_full
    assert load_review_md(repo/"REVIEW.md").tracks["fix"] == ["correctness", "security"]
def test_setup_refuses_second_run(tmp_path): with pytest.raises(SetupError, match="already set up"): setup_project(repo, answers)
def test_section_markers_idempotent(tmp_path):  # CLAUDE.md with old marked section + user text -> one section, user text kept
    assert text.count("<!-- factory:begin -->") == 1 and "user text" in text
def test_conflicts_detected_from_agents_md(tmp_path):  # AGENTS.md line 3 "- Do not create git branches unless explicitly asked."
    assert res.conflicts == ["AGENTS.md:3: - Do not create git branches unless explicitly asked."]
def test_gitignore_entry_once(tmp_path):  # existing .gitignore already has factory/dashboard/ -> still exactly one
def test_creates_claude_md_when_neither_exists(tmp_path)
```

- [ ] **Step 2: Run** `pytest tools/tests/test_setup.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `setup.py`, the templates and the CLI command.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add project setup scaffolding and conflict detection"`

---

### Task 8: Skills readiness

**Files:**
- Create: `tools/swfactory/readiness.py`
- Modify: `tools/swfactory/cli.py` (add `skills`), `config/skills.yaml` if Task 1 left gaps
- Test: `tools/tests/test_readiness.py`

**Interfaces:**
- Consumes: `load_skills_registry`, `load_role_table`, `SkillReq` (Task 1).
- Produces:
  - `skill_search_paths(host: Literal["claude", "codex"], home: Path, project: Path | None) -> list[Path]` — claude: `home/.claude/skills`, `home/.agents/skills`, every `skills` dir under `home/.claude/plugins/cache`, on Windows every `skills` dir under `%APPDATA%/Claude/rpm`, `project/.claude/skills`; codex: `home/.agents/skills`, `home/.codex/skills`, every `skills` dir under `home/.codex/plugins/cache`, `project/.agents/skills`, `project/.codex/skills`; both append entries from env `FACTORY_SKILL_PATHS` (`os.pathsep`-separated).
  - `find_installed_skills(paths: list[Path]) -> dict[str, Path]` — a directory containing `SKILL.md` counts; key = frontmatter `name` if present, else folder name.
  - `@dataclass ReadinessReport(host: str, present: list[str], missing: list[tuple[str, str | None]], interactive_in_unattended: list[str])` (missing = `(skill, fallback)`; a missing optional skill is not listed).
  - `check_readiness(registry: dict[str, list[SkillReq]], table: RoleTable, host: str, home: Path, project: Path | None) -> ReadinessReport` — an interactive skill registered for a role whose `run_as` is `headless` or `subagent` goes in `interactive_in_unattended`.
  - CLI: `factory skills check --host {claude,codex} [--project PATH]`; exit 0 when nothing required is missing and no interactive-in-unattended entries, else 1.

- [ ] **Step 1: Write the failing tests** (fake home in `tmp_path`, `monkeypatch` for env):

```python
def test_present_and_missing(tmp_path): # home/.agents/skills/tdd/SKILL.md only; registry requires tdd and grilling(fallback grill-me)
    assert rep.present == ["tdd"] and rep.missing == [("grilling", "grill-me")]
def test_plugin_cache_nested(tmp_path): # home/.claude/plugins/cache/x/superpowers/7.0.0/skills/writing-plans/SKILL.md -> present
def test_frontmatter_name_wins(tmp_path): # folder "wp" with "name: writing-plans" -> key "writing-plans"
def test_env_extra_path(tmp_path, monkeypatch)
def test_interactive_in_unattended_flagged(tmp_path): # registry maps grilling (interactive) to role "tester" (subagent) -> flagged
def test_optional_missing_not_reported(tmp_path)
```

- [ ] **Step 2: Run** `pytest tools/tests/test_readiness.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `readiness.py` and the CLI command.
- [ ] **Step 4: Run** — Expected: PASS. Then run `factory skills check --host claude` and `factory skills check --host codex` on this machine — Expected: a report; note any genuinely missing skills in the task's commit message.
- [ ] **Step 5: Commit** — `git commit -m "Add skills readiness check for both hosts"`

---

### Task 9: Prompts, subagents and the no-model-names rule

**Files:**
- Create: `prompts/lens-correctness.md`, `prompts/lens-security.md`, `prompts/lens-maintainability.md`, `prompts/lens-spec.md`, `prompts/document-review.md`, `prompts/tester-brief.md`
- Create: `agents/factory-lens-reviewer.md`, `agents/factory-doc-reviewer.md`, `agents/factory-tester.md`, `agents/factory-researcher.md`
- Create: `tools/swfactory/prompts.py`; modify `tools/swfactory/cli.py` (add `prompt render`)
- Test: `tools/tests/test_prompts.py`, `tools/tests/test_no_model_names.py`

**Interfaces:**
- Consumes: `load_review_md` (Task 3).
- Produces:
  - `class MissingValue(KeyError)` listing every missing name.
  - `required_placeholders(template: Path) -> set[str]` — `{{name}}` occurrences.
  - `render(template: Path, values: dict[str, str]) -> str`
  - CLI: `factory prompt render TEMPLATE --set name=value … [--from-review-md PATH --lens L] --out FILE` (`--from-review-md` fills `checklist` and `nit_cap`).

Placeholders — lens prompts: `{{lens}}`, `{{checklist}}`, `{{task_id}}`, `{{round}}`, `{{diff_command}}`, `{{spec_path}}`, `{{prior_review_path}}`, `{{nit_cap}}`. Document review: `{{document_path}}`, `{{revision}}`, `{{decisions_path}}`, `{{prior_review_path}}`. Tester brief: `{{work_id}}`, `{{task_id}}`, `{{commits}}`, `{{verify_command}}`, `{{demo_script}}`, `{{report_path}}`.

Every lens prompt states: read the diff (`{{diff_command}}`) and the spec before any implementer summary; you are read-only; if `{{prior_review_path}}` exists, first mark each earlier finding `fixed`, `not fixed` or `no longer applies`; the exact review format from Task 3 including `VERDICT:` as the first line; `CHANGES` only for blocker or major findings; at most `{{nit_cap}}` nits. The maintainability prompt adds that its findings never block. Agent files have YAML front matter `name`, `description`, `tools` (lens and doc reviewers: `Read, Grep, Glob, Bash`, read-only use only; tester: `Read, Grep, Glob, Bash`, never edits product files) and **no `model` field**.

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize("lens", ["correctness", "security", "maintainability", "spec"])
def test_each_lens_prompt_has_contract(lens):
    t = (factory_home()/f"prompts/lens-{lens}.md").read_text(encoding="utf-8")
    assert "VERDICT:" in t and "| ID | Severity | Location | Finding | Fix |" in t and "{{prior_review_path}}" in t
def test_render_missing_placeholder_raises(): with pytest.raises(MissingValue) as e: render(p, {"lens": "x"}); assert "checklist" in str(e.value)
def test_tester_brief_placeholders(): assert required_placeholders(p) == {"work_id", "task_id", "commits", "verify_command", "demo_script", "report_path"}
def test_agents_have_no_model_field():  # parse front matter of every agents/*.md -> "model" not in keys
def test_no_model_names_outside_config():
    # patterns: every model in config/roles.yaml, plus r"\bclaude-(opus|sonnet|haiku|fable)-\d", r"\bgpt-\d"
    # scanned: skills/, prompts/, agents/, templates/, tools/swfactory/ ; allowed: config/roles.yaml only
    assert offenders == []
```

- [ ] **Step 2: Run** `pytest tools/tests/test_prompts.py tools/tests/test_no_model_names.py -v` — Expected: FAIL.
- [ ] **Step 3: Write** the prompts, agents, `prompts.py` and the CLI command.
- [ ] **Step 4: Run** the whole suite `pytest tools/tests -v` — Expected: PASS (the no-model-names test also guards Tasks 1–8 code).
- [ ] **Step 5: Commit** — `git commit -m "Add review and tester prompts, subagents, and the no-model-names rule"`

---

### Task 10: Skills — setup, triage and run

**Files:**
- Create: `skills/factory-setup/SKILL.md`, `skills/factory-triage/SKILL.md`, `skills/factory-run/SKILL.md`, `skills/factory-run/references/checkpoints.md`, `skills/factory-run/references/hard-stops.md`
- Test: `tools/tests/test_skills_lint.py`

**Interfaces:**
- Consumes: CLI commands `setup`, `config check`, `skills check`, `work new|status|set-status`, `approve`, `role resolve` (Tasks 1–8); `swfactory.cli.COMMANDS`; `config/skills.yaml`.
- Produces: skills `factory-setup`, `factory-triage`, `factory-run`, referenced by Task 11 and the guide.

Each SKILL.md has front matter `name` and `description` (when to use), then the steps. Every third-party skill it uses is listed on one line `Uses skills: a, b, c`; every CLI call is written `factory <command> …`.

- **factory-setup:** works on a `factory/setup` branch unless project rules forbid branches (ask the owner first); reads `AGENTS.md`/`CLAUDE.md`; asks the owner one question at a time for the quick and full verify commands, approvers, branching and build policy; runs `factory setup`; presents every conflict and has the owner settle each in `factory.yaml`; drafts `always_flag` rules in `REVIEW.md` from the project's own rules; runs `factory config check` and `factory skills check` for both hosts; commits only after the owner approves, quoting the approval in the commit message. Uses skills: grilling.
- **factory-triage:** sizes work with the design §8.3 thresholds (Fix: one session, one task, no shared contract, schema or file format; Feature: up to about five tasks, no milestones; otherwise Program); records the reason in `progress.md` via `factory work set-status`; runs `factory work new`; for a Fix, writes `ticket.md` with repro steps and acceptance criteria and asks the owner to confirm them; for a Feature or Program, hands to `factory-run`. Uses skills: grilling, research.
- **factory-run:** the orchestrator. Reads the Status line first (`factory work status`); follows the track's stage table (design §8.3); keeps state only in files; in review mode stops at each checkpoint (intent, spec, plan, milestone demo, merge) and, on the owner's approval message, runs `factory approve` with the owner's words verbatim; for a Feature's mini-spec, runs one other-vendor document review (`factory role run doc-reviewer-b` with `prompts/document-review.md`), and for a Program's spec and plan runs reviewers A and B, freezing the document until every reviewer has reported; Program items use the same task loop, with milestone demos run by the tester from `demo/` scripts until plan 2 adds tooling; in auto mode writes a note and continues; at close writes `retro.md` and proposes each lesson as an addition to `CLAUDE.md`/`AGENTS.md`, `REVIEW.md` or a skill, applying it only after the owner approves and indexing it in `factory/lessons.md`; stops at every hard stop in `references/hard-stops.md`; applies the decision ladder (acceptance criteria → repo precedent → smallest blast radius → ask) and records ladder decisions in `decisions.md` and a `Factory-Decision:` trailer; commits every step with `format_trailers` trailers and pushes only the work branch; near 95% of the session budget, finishes the step, updates the Status line and hands off. Uses skills: to-spec, writing-plans, handoff, verification-before-completion.

- [ ] **Step 1: Write the failing lint test** `test_skills_lint.py`:

```python
@pytest.mark.parametrize("skill_md", sorted((factory_home()/"skills").glob("*/SKILL.md")))
def test_skill_front_matter(skill_md): fm = front_matter(skill_md); assert fm["name"] == skill_md.parent.name and len(fm["description"]) > 40
def test_cli_commands_exist(skill_md): assert set(re.findall(r"`factory ([a-z-]+)", text)) <= COMMANDS
def test_used_skills_registered(skill_md): assert used_skills(text) <= registered_skill_names()  # from config/skills.yaml
def test_expected_skills_present(): assert {"factory-setup", "factory-triage", "factory-run"} <= skill_names()
```

- [ ] **Step 2: Run** `pytest tools/tests/test_skills_lint.py -v` — Expected: FAIL (`factory-setup` missing).
- [ ] **Step 3: Write** the three skills and their references.
- [ ] **Step 4: Run** `pytest tools/tests -v` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add factory-setup, factory-triage and factory-run skills"`

---

### Task 11: Skills — task loop, review panel and merge gate

**Files:**
- Create: `skills/factory-task-loop/SKILL.md`, `skills/factory-review-panel/SKILL.md`, `skills/factory-merge-gate/SKILL.md`
- Modify: `tools/tests/test_skills_lint.py` (expected set adds these three)

**Interfaces:**
- Consumes: CLI `role resolve|run`, `prompt render`, `panel combine`, `work new|set-status`, `gate` (Tasks 2–6, 9); agents `factory-lens-reviewer`, `factory-tester` (Task 9).
- Produces: skills `factory-task-loop`, `factory-review-panel`, `factory-merge-gate`, called by `factory-run`.

- **factory-task-loop:** for one task: implement test-first; run the quick verify command and paste its output; call `factory-review-panel`; on `CHANGES` write `reviews/disposition-<task>-r<N>.md` (columns `# | Finding | Severity | Disposition | Reason`, disposition ∈ accepted, modified, declined), re-checking each finding against the code before deciding, fix, and run the next round; on the third failed round stop (hard stop `third-failed-round`); on `PASS` render the tester brief (`factory prompt render prompts/tester-brief.md …`), start the tester as `factory role resolve tester` says, and make **no edits to the working tree while the tester runs** (stage fixes as patches under `~/.factory/staging/<project>/<work-id>/`, never in the session scratchpad); close the task: tick its checkbox in `plan.md` (or `ticket.md` criteria), `factory work set-status`, commit with trailers, push the work branch. Uses skills: tdd, diagnosing-bugs, verification-before-completion.
- **factory-review-panel:** reads the track's lenses from `REVIEW.md`; for each lens runs `factory role resolve lens-<lens> --host <host> --author <author>`, renders the lens prompt with `--from-review-md`, and starts all lenses at the same time — subagent lenses as the `factory-lens-reviewer` subagent with the resolved model alias, command lenses with `factory role run`; a failed command lens is retried once with `--fallback`; when every lens has reported, runs `factory panel combine` and reports the verdict; each maintainability follow-up becomes a queued item via `factory work new` (fix or feature track). Uses skills: code-review.
- **factory-merge-gate:** spot-checks one acceptance criterion with evidence, runs `factory gate <ID> --spot-check "<criterion: evidence>"` (asking the owner first when `builds: ask`), and on `GATE: PASS` gives the owner a summary — what changed, panel results, verify output, risks, follow-ups — and the merge command; the owner merges. On failure it reports the failing checks and, for `size-cap`, proposes how to split.

- [ ] **Step 1: Update the lint test's expected set** to include the three skills.
- [ ] **Step 2: Run** `pytest tools/tests/test_skills_lint.py -v` — Expected: FAIL.
- [ ] **Step 3: Write** the three skills.
- [ ] **Step 4: Run** `pytest tools/tests -v` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Add task loop, review panel and merge gate skills"`

---

### Task 12: Plugin packaging, install guide and docs

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `docs/guide/install.md`, `docs/guide/using-the-factory.md`
- Modify: `README.md` (Status, Repository layout, Roadmap: v1 core built), `docs/decisions.md` only if a decision changed
- Test: `tools/tests/test_manifests.py`

**Interfaces:**
- Consumes: `swfactory.__version__`; `skills/`, `agents/` (Tasks 9–11).
- Produces: an installable plugin named `software-factory`, version `0.1.0`.

Manifest values: Claude `plugin.json` — `name: "software-factory"`, `version`, `description`, `author: {"name": "Battlespace Research"}`, `repository: "https://github.com/Battlespace-Research/software-factory"`, `license: "Proprietary"`, `skills: "./skills/"`, `agents: "./agents/"`. Claude `marketplace.json` — `name: "bsri-factory"`, `owner: {"name": "Battlespace Research"}`, `plugins: [{"name": "software-factory", "source": "./", "description": …}]`. Codex `plugin.json` — same `name`, `version`, `description`, `author`, `repository`, `license`, `skills: "./skills/"`.

`install.md` covers: Python ≥ 3.11 and `pip install -e <clone>/tools`; adding the marketplace and installing the plugin in Claude Code; installing in Codex (exact commands confirmed with `codex plugin --help`); the third-party skills to install (design §13) and `factory skills check` for each host; trusting nothing extra (no hooks in v1). `using-the-factory.md` walks through setup, triage, a Fix and a Feature, approvals in chat, and where files live.

- [ ] **Step 1: Write the failing test** `test_manifests.py`:

```python
def test_manifests_agree():
    c, x = json.load(open(".claude-plugin/plugin.json")), json.load(open(".codex-plugin/plugin.json"))
    assert c["name"] == x["name"] == "software-factory" and c["version"] == x["version"] == swfactory.__version__
def test_manifest_paths_exist(): assert (home/"skills").is_dir() and (home/"agents").is_dir()
def test_marketplace_lists_plugin(): assert m["plugins"][0]["name"] == "software-factory"
```

- [ ] **Step 2: Run** `pytest tools/tests/test_manifests.py -v` — Expected: FAIL.
- [ ] **Step 3: Write** the manifests and both guides; update the README.
- [ ] **Step 4: Run** `pytest tools/tests -v` — Expected: PASS. If `claude plugin validate` exists (`claude plugin --help`), run `claude plugin validate .` — Expected: no errors. Republish the factory map if any figure text changed.
- [ ] **Step 5: Commit and push** — `git commit -m "Package the factory as a Claude Code and Codex plugin; add guides"` then `git push origin main`.

---

### Task 13: Sample repo and end-to-end acceptance

**Files:**
- Create: `examples/sample-app/` — a tiny Python package `rangering` with `format_range(meters: float, unit: str) -> str` (`unit` ∈ `m`, `km`, `nmi`), tests for `m` only, and a planted bug: `km` values are labelled `m`; `examples/sample-app/verify.py` with `--quick` (unit tests) and full (tests + `python -m compileall`).
- Create: `docs/acceptance/v1-checklist.md`, then `docs/acceptance/<date>-v1-results.md`

**Interfaces:**
- Consumes: everything above, installed as a plugin in both hosts.
- Produces: the v1 demo record the owner reviews.

- [ ] **Step 1: Write the sample app and its passing `m` tests.** Run `python examples/sample-app/verify.py --quick` — Expected: exit 0 (the bug is not yet covered).
- [ ] **Step 2: Write `v1-checklist.md`:** copy the sample to a temp folder outside every repo, `git init`, commit; in **Claude Code**: install the plugin, run `factory-setup`, triage "range ring label shows m for km values" (expect Fix), run the task loop (failing test first, review panel with correctness and security through Codex, tester), run the merge gate, merge as the owner; then repeat on a fresh copy with **Codex as host** (correctness and security through `claude -p`). For each run record: files created, panel verdicts per round, gate report, time, and any manual intervention.
- [ ] **Step 3: Run the Claude-host pass** per the checklist. Expected: a `FIX-1` work folder with ticket, reviews (`T1-r1-correctness.md`, `T1-r1-security.md`, `T1-r1-panel.md`), `merge-gate.md` with `GATE: PASS`, and an approval record quoting the owner.
- [ ] **Step 4: Run the Codex-host pass.** Expected: the same artifacts, with the lens files' provenance lines showing Claude models.
- [ ] **Step 5: Write `<date>-v1-results.md`,** commit and push, and present it to the owner as the v1 demo checkpoint. Any defect found becomes a `FIX-` item for plan 2 or a fix before closing v1.
