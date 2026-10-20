# Factory core v1: the controller's rulings

These are the decisions the controller took while building core v1 (plan [`2026-10-20-factory-core-v1.md`](2026-10-20-factory-core-v1.md), spec [`2026-10-20-software-factory-design.md`](../specs/2026-10-20-software-factory-design.md)), wherever the plan, the spec, a review or the machine left a choice open. Each is copied word for word from the controller's ledger, a session workspace that is not kept, in the order it was taken, and each ends with its cost if it proves wrong ("if wrong: …"). The pre-flight rulings are numbered R1 to R5; the later ones are named by the task during which they were taken.

The ledger's other entries (dispatches, review outcomes, deferred minor findings, carries between tasks) are not repeated here: the rehearsal record [`2026-10-20-v1-rehearsal-results.md`](../acceptance/2026-10-20-v1-rehearsal-results.md) and the commit history hold what they led to.

## Pre-flight (before Task 1)

- Ruling R1: Task 1 creates a repo `.gitignore` (`.venv/`, `__pycache__/`, `*.egg-info/`, `.pytest_cache/`, `.superpowers/`) and a virtualenv at `E:\BSRI\GitHub\software-factory\.venv`; all tasks install with `.venv/Scripts/python -m pip install -e "tools[dev]"` and test with `.venv/Scripts/python -m pytest tools/tests`. — Global Python has no PyYAML/pytest and installing into it would touch the user's machine-wide setup. — If wrong: one line of docs changes; no code impact.
- Ruling R2: Task 3 creates `tools/swfactory/tracks.py` with `TRACK_PREFIX = {"program": "PRG", "feature": "FEA", "fix": "FIX"}`, `track_for_id(id_: str) -> str` and `find_project_root(start: Path) -> Path` (walks up to the directory holding `factory.yaml`); the panel CLI uses them. Task 4 imports `TRACK_PREFIX` from `tracks.py` instead of defining it. — Task 3's CLI needs a track before Task 4 exists; one definition avoids duplication. — If wrong: a module move.
- Ruling R3: `test_detached_head_error_message` uses the single-branching config (`cfg_single`), as its comment states. Per-item branching from a detached HEAD is legal (`git switch -c` works) and needs no error. — If wrong: one test fixture swap.
- Ruling R4: `run_invocation` appends extra Codex arguments from env `FACTORY_CODEX_ARGS` (split with `shlex.split(posix=False)` on Windows) before the trailing `-`, and stamps them into the provenance line, so the documented Windows fallback `-c windows.sandbox="unelevated"` needs no code change. — The owner's machine has hit Codex's elevated-sandbox refresh failure before. — If wrong: an unused env hook.
- Ruling R5: Task 13 runs unattended only Steps 1–2 plus a CLI-level rehearsal on a temp copy of the sample (setup → work new → failing test and fix → real correctness and security lens runs via `factory role run` with the Codex CLI → panel combine → gate), recorded in the results file. The host-driven passes (Steps 3–4: plugin installed into the owner's Claude Code and Codex, chat approval, owner merge) are the owner-attended v1 demo after this plan. — They change the owner's host configuration and need the owner's own approval and merge. — If wrong: the attended demo finds issues the rehearsal could not.

## Task 2

- Task 2: Ruling: `claude -p` argv includes `--effort <effort>` (the CLI supports it; the binding's effort must apply) — plan omitted it only because the flag was unverified — if wrong: one argv element.
- Task 2: Ruling: `role resolve|run` take optional `--project PATH` applying factory.yaml role overrides (design §7.2) — if wrong: an unused option.
- Task 2: Ruling: release-checker and triage keep no AGENT_FOR_ROLE entry; they belong to plan 3 (Deploy/Maintain), which maps them — if wrong: plan 3 adds two mapping lines.
- Task 2: Ruling: the vendor CLI writes to a sibling temp file (`<out>.partial`); run_invocation stamps it and renames it onto out_path only on success; every failure path (incl. BaseException) kills the process tree and deletes the temp — Review Focus 5 (spec) outranks the plan's literal `-o str(out_path)` — if wrong: one rename step.
- Task 2: Ruling: for `.cmd`/`.bat` executables, refuse (ok=False, named argument) any argv element containing `& | < > ^ % !` or a newline — the standard Windows npm shim runs through cmd.exe — if wrong: a path with those characters must be renamed by the user.
- Task 2: Ruling: access `no-product-edits` maps to Codex sandbox `workspace-write` (the tester must build and run verify); the no-edit rule is enforced by the tester prompt and a clean-working-tree check after the tester in Task 11 — if wrong: a Codex tester could edit product files undetected until the check.

## Task 3

- Task 3: Ruling: verdict.py adds `read_review_file(path, lens, nit_cap=5) -> Review` that strips BOM and a trailing `<!-- factory: … -->` stamp line, parses JSON when the text starts with `{`, else markdown; `combine` reads lens files through it — Task 2's stamp would otherwise break JSON-form files (T2 minor 3) — if wrong: one helper.
- Task 3: ⚠️ resolved — check_panel_vendor_mix is not called yet. Ruling: Task 7 wires it into `factory config check` when REVIEW.md exists (design §9.3) — if wrong: the constraint stays unchecked until setup.

## Task 4

- Task 4: ⚠️ resolved — remote refs are only as fresh as the last fetch. Ruling: the triage skill (Task 10) runs `git fetch` before `factory work new`; the merge gate's id check is the backstop — if wrong: a rare duplicate id gets a letter suffix at merge.

## Task 5

- Task 5: Ruling: `factory approve` gains `--quote-file PATH` (exclusive with `--quote`) so skills pass the owner's exact words without shell quoting — if wrong: an unused option.
- Task 5: Ruling: keep os.replace for approval files despite a theoretical same-artifact/rev race — approvals are written sequentially by one session per work item — if wrong: a simultaneous duplicate approval overwrites with an identical-purpose record.

## Task 6

- Task 6: Ruling: `reviewed-commit` also fails on uncommitted tracked changes (verify-full would otherwise pass on unmerged edits); check names/order unchanged — if wrong: the owner must commit before gating, which the skill does anyway.
- Task 6: Ruling: reviewed-commit passes when HEAD == reviewed commit, or the reviewed commit is an ancestor of HEAD and every path changed in reviewed..HEAD lies under `factory/work/` (bookkeeping only); otherwise FAIL naming the product paths changed since review — design §9.6 intends "the code merged is the code reviewed" — if wrong: bookkeeping edits outside factory/work would need re-review.
- Task 6: Ruling: size-cap always excludes `factory/work/**` (factory artifacts are not product code), in addition to cfg.generated — design §9.6's cap targets reviewable change size — if wrong: very large factory docs are uncapped, which is harmless.

## Task 9

- Task 9: Ruling: v1 supports only the four built-in lenses (correctness, security, maintainability, spec); the review-panel skill refuses a REVIEW.md track that names another lens with a clear message — project lenses need a prompt template, deferred to plan 2 — if wrong: a project can't add UX/performance lenses until then.

## Task 10

- Task 10: Ruling: `work_branch` must differ from `base_branch` — `factory config check` rejects equality and setup never offers it; "commit straight to main" projects use single branching on a separate branch the owner merges — design: pushes/merges to main are a hard stop — if wrong: such projects need one extra merge step.
- Task 10: Ruling: one push rule everywhere: push only the work item's branch (or its lessons branch), only when a remote exists, only if the project's rules and hard_stops allow, never the base branch; triage checks hard_stops before pushing — if wrong: some pushes wait for the owner.
- Task 10: Ruling: remove the "Already approved counts" rule — hard stops always stop and each needs the owner's answer (ruling 3, design §8.4) — if wrong: more owner prompts.
- Task 10: Ruling: the gate's id-unused-on-base check exempts a base folder named exactly this item's `<ID>-<slug>` (an earlier milestone of the same item); any other folder with the id still fails — per-milestone merges are in the design (§8.3) — if wrong: a same-slug collision from another item would pass (vanishingly unlikely).
- Task 10: Ruling: the `factory/<ID>-<slug>-lessons` branch is the one named exception to "push only the work branch" — lessons can't ride the reviewed branch (gate) — if wrong: lessons need a different home.
- Task 10: Ruling: approved lessons (exact text, destination file, owner's reply) are written into retro.md before the status update — state lives in files — if wrong: none.

## Task 11

- Task 11: Ruling: maintainability follow-ups are appended to `<work folder>/follow-ups.md` during the loop and turned into queued work items by factory-run's close step after the merge (not mid-task) — `factory work new` switches the checkout, which would break the running task — if wrong: follow-ups appear in the queue later than the plan text says.

## Task 12

- Task 12: Ruling: Task 12 commits only — no `git push origin main` (plan step 5 text); pushing/merging the branch is decided with the owner in finishing-a-development-branch — a push to main is a hard stop and needs the owner — if wrong: one push later.
- Task 12: Ruling: the implementer does not publish artifacts; the controller republishes the map/README visuals if figure text changed — artifact publishing belongs to the owner's session — if wrong: none.
- Task 12: Ruling: Claude manifest omits `agents` (validator rejects "./agents/"; omission loads all four agents) — plan value conflicts with the real validator — if wrong: none, agents load.
- Task 12: Ruling: keep the added Codex marketplace file `.agents/plugins/marketplace.json` (diagram-design precedent) — if wrong: one unused file.

## Final review

- Ruling: final fix wave also commits the rulings record (docs/plans/2026-10-20-factory-core-v1-rulings.md, all `Ruling:` lines from this ledger with their costs) and refreshes the map captions (Skills, role table) to say what is built — the workspace is deleted at the end and the map must stay current (F06) — if wrong: two doc files.
- Ruling: Linux check = one `pytest tools/tests` run inside WSL on a copy of the repo in the WSL filesystem, recorded in the rehearsal results; if WSL Python/pip is unavailable, record that and leave CI for plan 2 — the owner's WSL may be busy with NGC2 lanes, so one light run only, never a build — if wrong: POSIX paths stay unverified until CI.
