# Command reference

The factory has two layers:

- **Skills** are what you use. You talk to Claude Code or Codex in your project, and a skill does the work step by step.
- **The `sw-factory` command** is what the skills use. It does the exact bookkeeping (ids, Status lines, approval records, review verdicts, the merge gate), so the results don't depend on a model's memory. You will run a few of its commands yourself; the rest are for the skills.

For a walkthrough, read [Using the factory](using-the-factory.md). For installing, read the [install guide](install.md).

## Skills

In Claude Code, type `/` and pick `bsri-sw-factory:<skill>`. In Codex, type `$` and pick `bsri-sw-factory:<skill>`. You can also just say what you want; the agent picks the skill.

| Skill | What it does | When you use it |
|---|---|---|
| `setup-project` | Sets a project up once: interviews you one question at a time, writes `sw-factory.yaml` and `REVIEW.md`, settles conflicts with the project's own rules, checks both hosts, and commits on a setup branch once you approve | Once per project: *"Set up this repository for the software factory."* |
| `intake` | Sizes new work, proposes a track (or takes the one you name), opens the work item, and writes and confirms a Fix ticket | Each new bug, change or idea: *"Triage this for the factory: …"*, or *"Triage this as a Feature: …"* |
| `orchestrate` | Moves a work item through its track's stages, stops at checkpoints, records your approvals, writes the retro, and hands each task and the merge to the skills below | To start or resume a work item: *"Continue FEA-7."* |
| `task-loop` | Runs one task: failing test, fix, quick verify, review panel, fix rounds, tester, close | Called by `orchestrate`; you don't need to call it |
| `review-panel` | Runs the review lenses at the same time, at least one from the other vendor, and combines their verdicts. Three lenses get a reference skill's text inlined in their prompt as guidance: correctness `tdd`, maintainability `codebase-design`, spec `domain-modeling` (when the project has a `CONTEXT.md` or ADRs) | Called by the task loop |
| `merge-gate` | Brings the branch up to date, spot-checks an acceptance criterion, runs the gate, and gives you the merge | Called by `orchestrate` at the end |

## The `sw-factory` command

Every command takes `--help` for its full options. Commands that work on a project find it from the current folder (the folder holding `sw-factory.yaml`, or one above it); most accept `--project PATH` to point elsewhere.

### Commands you are likely to run yourself

| Command | What it does |
|---|---|
| `sw-factory --version` | Prints the installed version. |
| `sw-factory work status <ID>` | Shows where a work item stands: its stage, state, next step and last update. |
| `sw-factory skills check --host claude` | Checks that the third-party skills the factory's roles need are installed for Claude Code. Use `--host codex` for Codex. Exit 0 ready, 1 not ready. |
| `sw-factory config check --project .` | Checks the factory's role table and skills registry, and this project's `sw-factory.yaml` and `REVIEW.md`. Exit 0 ok, 1 problems. |

### Setting up

| Command | What it does |
|---|---|
| `sw-factory setup --project NAME --verify-quick CMD --verify-full CMD --approver NAME` | Creates `sw-factory.yaml`, `REVIEW.md`, the `sw-factory/` folders and a factory section in `CLAUDE.md`/`AGENTS.md`, and prints the project rules that conflict with factory defaults. Options: `--base`, `--branching per-item\|single`, `--work-branch`, `--builds allow\|ask`, `--mode review\|auto`. It never commits, and it refuses (exit 2) if the project is already set up. The setup-project skill runs it for you. |

### Work items

| Command | What it does |
|---|---|
| `sw-factory work new --track fix --slug km-label --title "Range ring label shows m for km"` | Opens a work item: takes the next free id for the track (`FIX-`, `FEA-`, `EPIC-`), creates and switches to its branch, copies the track's files into `sw-factory/work/<ID>-<slug>/`, and commits. `--track` is `fix`, `feature` or `epic`. |
| `sw-factory work status <ID>` | Prints the work item's Status line as JSON. |
| `sw-factory work set-status <ID> --stage S --state S --next TEXT --note TEXT` | Rewrites the Status line and adds a note at the top of the log in `progress.md`. Doesn't commit. |

### Approvals

| Command | What it does |
|---|---|
| `sw-factory approve <ID> <artifact> --rev R --quote-file FILE --approver NAME --commit SHA` | Records an owner's approval from their exact words (in a file, so the shell can't change them; `--quote` takes the text directly). The approver must be listed in `sw-factory.yaml`. Writes `approvals/<artifact>-rev<R>.md`; never overwrites one. Exit 0 recorded, 1 refused. Options: `--host`, `--session`. |

### Roles and reviews

| Command | What it does |
|---|---|
| `sw-factory role resolve <role> --host claude --author claude` | Shows which vendor, model and effort a role uses, and how it will run: in the main session, as a subagent, or as a headless command. `--host` and `--author` name a **vendor** here: `claude` or `openai`. `--fallback` shows the fallback binding. |
| `sw-factory role run <role> --host claude --author claude --prompt FILE --out FILE` | Runs a headless reviewer through `codex exec` or `claude -p` and writes its review, with a provenance line, to `--out`. Exit 0 written, 1 failed (try again with `--fallback`), 3 the role runs in the session or as a subagent instead. `--schema FILE` asks Codex for JSON. |
| `sw-factory prompt render TEMPLATE --set NAME=VALUE --out FILE` | Fills in a prompt template. `--from-review-md REVIEW.md --lens security` adds that lens's checklist and the nit cap. Refuses missing or unused values. The correctness, maintainability and spec prompts also need `--reference-skill NAME --host claude` (or `--host codex`, the app): it inlines the installed skill NAME that `config/skills.yaml` registers for the lens (its `SKILL.md` and listed files, or its fallback's `SKILL.md`), searching only your user's skill folders, and prints `reference: NAME sha256:…`; when the skill is not installed, or with `--reference-skill none`, the prompt says none was available and it prints `reference: none`. The spec prompt also takes `--set domain_files=…`. |
| `sw-factory skills path codebase-design --host claude` | Prints the absolute path of an installed skill's `SKILL.md`, found where that app loads skills from (exit 0), or `none` when it is not installed (exit 1). `--host` is the app, `claude` or `codex`; `--project PATH` searches the project's own skill folders too, `--home PATH` another home folder. Handy to see which copy of a skill a host would use; the review panel inlines reference skills with `prompt render --reference-skill`. |
| `sw-factory panel combine WORK_DIR --task T1 --round 1` | Reads the round's lens reviews and writes the panel summary `reviews/<task>-r<round>-panel.md`. Exit 0 PASS, 1 CHANGES, 2 INCOMPLETE (a lens is missing or unreadable). `--commit` names the reviewed commit (default HEAD). |

### Merging

| Command | What it does |
|---|---|
| `sw-factory gate <ID> --spot-check "AC2: 25 km now reads 25.0 km"` | Runs the six merge-gate checks (base merged in, commit is the reviewed one with no uncommitted changes, size under the cap, id unused on the base, full verify passes, spot check recorded) and writes `merge-gate.md`. Exit 0 PASS, 1 FAIL, 2 could not run. Options: `--reviewed-commit`, `--no-verify`, `--builds-approved` (needed when the project says `builds: ask`). It never merges; you do. |

## Two meanings of `--host`

`sw-factory skills check --host`, `sw-factory skills path --host` and `sw-factory prompt render --host` name the **app** you run: `claude` (Claude Code) or `codex`. For `sw-factory role resolve` and `sw-factory role run`, `--host` and `--author` name the **vendor**: `claude` or `openai`. In a Codex session, that means `skills check --host codex` but `role resolve --host openai`.

## Environment variables

| Variable | What it does |
|---|---|
| `FACTORY_HOME` | Where the factory's files are (`config/`, `prompts/`, `templates/`). Needed only if `sw-factory` can't find them from its install. |
| `FACTORY_SKILL_PATHS` | Extra folders to search for installed skills, separated like `PATH`. |
| `FACTORY_CODEX_ARGS` | Extra arguments added to every `codex exec` run, for example `-c windows.sandbox="unelevated"` when Codex's elevated Windows sandbox fails. Anything that would change a reviewer's sandbox, approvals, output file or working folder is refused. |
