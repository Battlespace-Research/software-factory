# Software Factory — Design

Status: draft 1 · 2026-10-20 · not yet approved by the owner
Decisions: `docs/decisions.md` (F01–F12) · Map: `docs/map/factory-map.html` (published privately) · Dashboard template: `docs/dashboard/factory-dashboard.html`

Items marked **[Claude's call]** were not discussed with the owner; they are proposals the owner can overturn. Everything else was decided with the owner (decision ids in brackets).

---

## 1. Intent

BSI builds software with AI agents. On the NGC2 prototype the owner ran a disciplined, multi-model process: grilling interviews that produce numbered decisions, a reviewed spec and plan, and a per-task loop in which Claude implements, a GPT model reviews through Codex, and a Claude tester verifies. It worked, but it lives in one project's runbook, in uncommitted scripts under `C:\Users\cordc\ngc2-staging`, and in one person's agent memory.

The **software factory** turns that process into a reusable, shareable kit any BSI project can install. A teammate opens Claude Code or the Codex/ChatGPT app in a repo, and the factory takes a piece of work from idea to running software, sized from a one-session bug fix to a multi-week program. The process stays visible at every step: a map of the factory, and a dashboard of the work in flight.

Why now: the NGC2 runbook has proved the approach, the lessons are fresh, and models keep changing. The factory has to make a model swap a one-line change that can be checked, not a rewrite of prompts scattered across scripts.

## 2. Goals

1. **Reusable and shareable.** A BSI teammate installs the factory as a plugin and sets up a project without NGC2 knowledge [F01, F04].
2. **Two hosts, either can drive.** Runs in Claude Code and in Codex (CLI and the ChatGPT desktop app). Either host can run the work and call the other vendor's model for reviews [F02].
3. **Models and skills swap in one place.** Each role is bound to a vendor, model, effort and fallback in one role table. Changing a binding updates the map, the dashboard and the runs [F02, F06].
4. **Aligned with the Claude Academy AI-Native SDLC Playbook.** Six stages — Plan, Design, Build, Test, Deploy, Maintain — with committed artifacts, human gates, review against a checklist, and measured outcomes [F03]. Appendix A maps each playbook requirement to this design.
5. **Scales down.** Three tracks — Program, Feature, Fix — so a bug fix does not pay for a program's ceremony, yet still lands reviewed and verified.
6. **Deploy and Maintain are first-class.** Both the desktop installer (what BSI ships today) and Kubernetes with Helm (where NGC2 is heading) get a path. Start light, mature with use, follow best practice [F05].
7. **Visible.** The map stays current and is published as a private web page [F06]; a dashboard is regenerated throughout the work [F10].

## 3. Non-goals (for this version)

- Replacing CI/CD systems or issue trackers. The factory drives them; it does not reimplement them.
- Unattended merging. A person always merges.
- Enforcing rules with hooks. Designed for, but parked (section 14).
- Agent review on GitHub pull requests. Optional per project, off by default, no GitHub Action yet [F11].
- Organization-wide managed settings, telemetry export (OpenTelemetry) and org dashboards. Revisit when several teams use the factory.
- Automatic vendor fallback when one vendor's usage runs low. Interesting (super-board does it); later.

## 4. Anti-goals — failure even if it technically works

- The factory only works on NGC2, or only for its original author.
- A one-line fix needs a spec, a plan and four reviewers.
- Model names hard-coded in skills, prompts or scripts.
- An agent approves or merges its own work, or writes the record of a human approval.
- The real state of a piece of work exists only in a chat transcript.
- Installing or running the factory disturbs ongoing work in a project (for example the NGC2 sessions in the MACE checkout).
- The factory locks a project into one vendor.
- Lessons stay in one person's private agent memory instead of the project's shared files.

## 5. Constraints

- **Where things live.** The factory is its own private git repo under the BSRI GitHub org `Battlespace-Research`, cloned at `E:\BSRI\GitHub\software-factory` [F01]. Never write to the MACE checkout from factory work; MACE and NGC2 are read-only references.
- **Work state is files in the project's own repo**, the source of truth; GitHub issues are an optional mirror [F07].
- **Platforms.** BSI develops on Windows (PowerShell 7, Git Bash, Visual Studio); CI and Kubernetes work run on Linux. Factory scripts must run on both. Python 3 is available on both and is already used for the NGC2 dashboard.
- **Licensing of prior art.** zazencodes' factory has no license: reuse ideas, never text. super-board is MIT: code or text borrowed from it keeps its notice. Third-party skills are referenced, not copied into the factory (section 13).
- **Builds.** One build at a time per working tree; heavy test suites run alone (NGC2 lesson).
- **Credentials.** Agents never enter credentials, licences or secrets; a person does.

## 6. Shape of the solution

### 6.1 Factory repo layout

The factory ships as one plugin with two manifests that share one `skills/` folder — the same pattern BSI's `codex-plugin` repo and the diagram-design plugin already use.

```
software-factory/
  .claude-plugin/plugin.json          Claude Code manifest (+ marketplace.json)
  .codex-plugin/plugin.json           Codex manifest
  skills/                             factory skills (section 6.3), host-neutral SKILL.md files
  agents/                             Claude Code subagent definitions (lens reviewers, tester, researcher)
  prompts/                            host-neutral prompt templates (review lenses, tester brief, author briefs)
  schemas/                            JSON schemas: verdict, review findings, dashboard data, factory.yaml, roles.yaml
  config/roles.yaml                   default role table (section 7)
  config/skills.yaml                  skills registry: required/optional per role and host, with fallbacks
  templates/project/                  files created by project setup (section 6.2)
  tools/                              scripts: run-role adapters, dashboard generator, merge gate, eval runner, map generator
  evals/                              factory evals (section 12)
  docs/                               map, dashboard template, decisions, specs, user guide
```

### 6.2 What project setup creates in a project

`factory-setup` runs once per project, on a branch, and the owner reviews the result before it merges.

```
<project>/
  factory.yaml           project config: verify command, tracks, mode, hard stops, deploy paths, signals, role overrides
  REVIEW.md              review checklist [F12]
  CLAUDE.md / AGENTS.md  a short factory section added (how to resume, where work lives, the verify command)
  factory/
    work/                one folder per piece of work (section 8)
    baseline/            expected-failures list for the verify command
    lessons.md           index of lessons promoted into CLAUDE.md, REVIEW.md or skills
    dashboard/           generated; git-ignored
```

Setup drafts `REVIEW.md`, the always-flag rules and the hard-stop list from what the repo already says (for MACE: its `AGENTS.md` rules such as "never change XML serialization"). It also asks for, or discovers, the single verify command. Setup never edits existing project files beyond appending the factory section to `CLAUDE.md`/`AGENTS.md`.

### 6.3 Factory skills

Each factory skill orchestrates; the craft comes from third-party skills it calls (section 13). **[Claude's call]** on names and split:

| Skill | Does |
|---|---|
| `factory-setup` | Creates the project files in 6.2; checks skills readiness for both hosts |
| `factory-triage` | Sizes incoming work, picks the track, creates the work folder |
| `factory-run` | The orchestrator: moves a work item through its track's stages, honours mode and checkpoints, writes `progress.md` |
| `factory-task-loop` | One task: implement, verify, review panel, disposition, tester, close |
| `factory-review-panel` | Runs the lenses in parallel through the role adapters and combines findings |
| `factory-merge-gate` | Pre-merge checks (section 9.6) and the hand-off to the human |
| `factory-deploy` | Runs the project's deploy path(s): desktop installer and/or Helm (section 10) |
| `factory-maintain` | Reads signals, drafts new intent for triage, runs retros (section 11) |
| `factory-dashboard` | Regenerates the dashboard (section 15) |
| `factory-evals` | Runs the factory's own evals (section 12) |

The orchestrator (`factory-run`) keeps state only in files and holds no per-task context, so any session on either host can pick up from `progress.md` (an idea from super-board).

## 7. Roles and the role table

### 7.1 Roles

| Role | Stage | Today's default binding | Runs as |
|---|---|---|---|
| Interviewer | Plan | Fable 5.1 · xhigh | main session |
| Researcher | Plan | Sonnet 5.5 · high | read-only subagent |
| Spec and plan author | Design, Build | Fable 5.1 · xhigh | main session or subagent |
| Document reviewer A (same vendor) | Design, Build | Opus 5.5 · xhigh | subagent, no edits |
| Document reviewer B (other vendor) | Design, Build | GPT-6-Astra · xhigh | `codex exec`, read-only |
| Implementer | Build | Opus 5.5 · xhigh | main session |
| Lens reviewer: correctness | Build | GPT-6.1-Sol · xhigh (other vendor) | `codex exec`, read-only |
| Lens reviewer: security | Build | GPT-6.1-Sol · xhigh (other vendor) | `codex exec`, read-only |
| Lens reviewer: maintainability | Build | Sonnet 5.5 · high | subagent, no edits |
| Lens reviewer: spec compliance | Build | Opus 5.5 · high | subagent, no edits |
| Tester | Test | Sonnet 5.5 · high | subagent, no product edits |
| Release checker | Deploy | Sonnet 5.5 · high | headless, read-only |
| Triage and on-call | Maintain | Sonnet 5.5 · high | headless, read-only first |

### 7.2 Binding rules

- **One file.** `config/roles.yaml` in the factory holds defaults; a project may override any role in `factory.yaml` (for example a project limited to one vendor). Nothing else names a model. Skills and prompts refer to roles only.
- **Relative vendor.** A binding may say `vendor: other` or `vendor: same`, resolved against the vendor of the author of the work under review. When Codex is the host and GPT implements, the "other vendor" lens reviewers become Claude models automatically. Each role lists one binding per vendor so the swap is defined, not improvised.
- **Fallback.** Each role names a fallback binding used when the primary is unavailable; using a fallback is logged in `progress.md` and shown on the dashboard.
- **Provenance.** Every artifact and commit records which role, model, effort and skill versions produced it (section 9.7).
- **Generated views.** The role table on the map and on the dashboard is generated from `roles.yaml`; model names in map figures come from the same file (section 15.3).

### 7.3 Host adapters

`tools/run-role` runs a role's prompt against its binding and writes the result to a file:

- From Claude Code: Claude roles run as subagents (`agents/`); other-vendor roles run `codex exec -m <model> -c model_reasoning_effort=<effort> -s read-only --output-schema <schema> -o <file>`.
- From Codex: Codex roles run natively; other-vendor roles run `claude -p` headless with a read-only tool allowlist and JSON output.
- Reviewers always run read-only. The adapter stamps the sandbox mode, model and effort into the result header (an NGC2 practice).

The builder verifies the exact CLI flags of the installed versions; the design fixes the contract (prompt in, schema-validated file out), not the flags.

## 8. Work items

### 8.1 Folder and files

```
factory/work/<ID>-<slug>/
  intent.md          problem, outcome, users and systems affected, constraints, open questions, status
  decisions.md       append-only decision rows (D01…), changed only by superseding rows
  research/          numbered read-only investigation notes (Program)
  spec.md            numbered requirements, design, constraint map, acceptance checks, risks, open questions
  plan.md            milestones and tasks with stable ids (Program, Feature)
  progress.md        newest-first log; Status line at the top is the resume point
  reviews/           <artifact-or-task>-r<N>-<lens>.md, plus disposition-<…>-r<N>.md
  approvals/         one file per owner approval (section 8.4)
  demo/              milestone demo scripts and their recorded output
  retro.md           what was learned at close
```

A Fix uses `ticket.md` (intent, repro steps, acceptance criteria) instead of intent/spec/plan.

### 8.2 Ids and revisions [Claude's call, from NGC2 lessons]

- Work ids: `PRG-nn`, `FEA-nn`, `FIX-nn`, sequential per project, never reused.
- Task ids: `M1-T1` in a Program, `T1` in a Feature or Fix. A split task gets a letter suffix (`M1-T2a`). **Ids are never renumbered** — NGC2 renumbered after reviews and broke references.
- Every spec and plan starts with a Status line: revision, review rounds, approval state. The major revision goes up once per review round; a decimal marks a correction or an amendment after approval.
- A document is **frozen while a review round is open**: the author waits for every reviewer before editing (NGC2: a reviewer read a plan while it was being rewritten).

### 8.3 Tracks and triage [Claude's call on the thresholds]

| | Program | Feature | Fix |
|---|---|---|---|
| Typical size | several milestones, many sessions | up to ~5 tasks, a few sessions | one session |
| Plan | research + grilling → `intent.md` + `decisions.md` | short grill → `intent.md` | `ticket.md` with repro and criteria |
| Design | `spec.md`, two document reviewers (A and B), rounds until no blocker | mini-spec, one other-vendor review | skipped |
| Build | `plan.md` with milestones; task loop per task | plan mode → `plan.md`; task loop | failing test first, then fix; task loop with one task |
| Review lenses | all four + project lenses | correctness, security, maintainability | correctness, security |
| Test | milestone demos run by the tester | verify command + tester | verify command |
| Deploy | merge per milestone; release gate | reviewed merge | reviewed merge |
| Maintain | monitor + retro | retro note if a lesson emerged | lesson only if new |

Triage puts work in the **smallest track that fits** and records why. A Fix becomes a Feature when it needs more than one session, touches a shared contract, schema or file format, or needs more than one task. A Feature becomes a Program when it needs milestones, crosses repos, or outgrows about five tasks. Stepping up keeps the id's history: the new item links the old one.

### 8.4 Checkpoints, modes and approvals [F08]

- **Review mode (default):** the factory pauses for the owner at the intent, the spec, the plan, each milestone demo, and the merge. Between checkpoints it runs the task loop on its own.
- **Auto mode (opt-in per project or per work item):** at a checkpoint the factory writes a short note in `progress.md` and continues. Only hard stops halt it. A person still merges.
- **Approval records:** `approvals/<artifact>-rev<N>.md` quotes the owner's message, the time and the commit it approves. Until hooks exist (section 14), the agent writes the record only in response to an explicit owner message in the session and the record quotes it verbatim; reviewers check the quote exists in the transcript summary in `progress.md`. **[Claude's call]**
- **Hard stops** (halt in both modes; each project keeps its list in `factory.yaml`, starting from these defaults): any push or merge to the main branch; deleting data or files outside the work's scope; credentials, licences, secrets; installers or admin/UAC prompts; purchases; changes to authentication, money handling or database schemas; new third-party dependencies; production data; a third failed review round on one task; an assumption that is really a business decision.

## 9. Build and Test: the task loop

### 9.1 Loop

`Next task → Implement (test-first) → Verify → Review panel → Verdict → (CHANGES: disposition + fix, re-review, max 3 rounds; 3rd failure is a hard stop) → Independent tester → Close task (tick plan, progress.md, commit, push to the work branch)`.

### 9.2 Verify command

Each project declares **one** verify command in `factory.yaml`. It exits non-zero on failure and prints totals. "Done" requires its pasted output. Results are compared with `factory/baseline/expected-failures.md`, which traces each expected failure to the main branch — never with "green". Test totals come only from real runs, never hand counts (NGC2 lesson).

### 9.3 Review panel [F09]

- Lenses run **in parallel**, each with its own checklist from `REVIEW.md`: correctness, security, maintainability, spec compliance, plus any project lenses (UX, performance). Track sets which lenses run (8.3).
- At least one lens comes from the other vendor; by default correctness and security do.
- Each reviewer reads the diff and the spec **before** the implementer's summary (super-board practice), and writes a file whose first line is `VERDICT: PASS` or `VERDICT: CHANGES`, followed by findings with ids `R1…`, severity (blocker, major, minor, nit; at most five nits), location and a concrete fix.
- **Review memory:** in round N+1 each reviewer gets its round-N file and first marks each earlier finding fixed, not fixed, or no longer applies, then adds new findings.
- **Combine:** the round ends only when every lens has reported. Any blocker or major sends the task back. Maintainability findings alone never block; they become follow-up tickets (`FIX-` or `FEA-` items in triage).
- **Disposition:** the implementer answers every finding — accepted, modified or declined, with a reason — in `disposition-<task>-r<N>.md`, re-checking each finding against the code before deciding.

### 9.4 Tester

A separate role that never edits product code. It gets a brief (file path), confirms the commit hashes first, runs the verify command and any demo script exactly as written, and reports. **No one edits the working tree while the tester runs**; fixes found meanwhile are staged as patches (NGC2 lesson, hit twice).

### 9.5 Decision ladder for unattended choices [Claude's call, from super-board]

When the factory must choose without the owner (auto mode, or between checkpoints): acceptance criteria → repo precedent → smallest blast radius → stop and ask. A choice settled by the ladder is recorded as a `decisions.md` row and a commit trailer (`Factory-Decision: …`).

### 9.6 Merge gate [Claude's call, from super-board and zazencodes]

Before handing a merge to the owner:
1. Bring the work branch up to date with the base and re-run the verify command.
2. Confirm the commit being merged is the commit that was reviewed.
3. Check the size cap (default 400 changed lines, excluding generated files, set in `factory.yaml`); over the cap, propose a split instead.
4. Spot-check one acceptance criterion with evidence.
5. Summarize for the owner: what changed, review outcome, verify output, risks, follow-ups.

### 9.7 Provenance

Commit trailers: `Factory-Work: FEA-07/T3`, `Factory-Role: implementer`, `Factory-Model: <model> (<effort>)`, plus the host's own co-author trailer. Review and spec headers record model, effort, sandbox mode and the versions of the skills used.

### 9.8 Sessions, budget and parallel work (NGC2 lessons)

- `progress.md` Status line is the single resume point; every step commits.
- At about 95% of a session's token budget: finish the loop step, commit, point `progress.md` at the resume point, hand off (`handoff` skill).
- Staging (patch scripts, briefs) lives outside the project tree in a per-user folder (`~/.factory/staging/<project>/<work-id>/`) **[Claude's call]**, never in the session scratchpad, which is not durable.
- Parallel sessions use separate worktrees; one build per tree; parallelism is capped at what review keeps up with (start at two or three).
- Pushes go only to the work item's branch.

## 10. Deploy [F05]

Two paths, configured per project in `factory.yaml`; a project may have one or both. Both follow the same autonomy ladder: CI jobs start read-only, then write only through branches or merges, and never hold standing production credentials. Rollback is rehearsed every release.

### 10.1 Desktop installer path

1. Build the installer in CI (BSI already does this in GitHub Actions for MACE and Merlin).
2. Sign it; run a smoke test (install on a clean runner, launch headless where possible, run the project's smoke command).
3. Publish to a **beta** channel automatically after a merged release branch.
4. Owner promotes beta to **stable** (a checkpoint in every mode).
5. Rollback: re-publish the previous stable installer; the release notes say how.
6. Release notes come from the project changelog.

### 10.2 Kubernetes with Helm path

1. The chart lives in the project repo; CI runs `helm lint`, `helm template`, and policy checks (for NGC2's target: Restricted Pod Security, resource limits, image provenance).
2. **dev:** deploy automatically after merge; wait for rollout and readiness.
3. **staging:** owner approves; deploy; run smoke checks; rehearse `helm rollback` and roll forward again.
4. **prod:** owner approval plus the change record the environment requires; agents get no standing prod credentials.
5. Health after deploy: rollout status, readiness, restarts, error rate.

### 10.3 Light first

Version 1 delivers: the release checklist and gates for both paths, the smoke-test and rollback steps written as scripts the project fills in, and the dashboard's delivery panel. Deployment automation beyond what each project's CI already has is added project by project.

## 11. Maintain

- **Signals** are listed per project in `factory.yaml`: desktop — crash reports and user-reported issues; Kubernetes — restarts, readiness failures, error rate, latency. Each signal has a normal range.
- `factory-maintain` (run on demand or on a schedule) reads signals; anything outside its range becomes a draft `intent.md` with status "triage" for the owner, or a `FIX-` ticket. Version 1 uses simple thresholds; control bands come later.
- **Retro at close:** every work item ends with `retro.md`. Repeat mistakes become proposed additions to the project's `CLAUDE.md`/`AGENTS.md`, `REVIEW.md` or a skill — shared files, not private agent memory. The owner approves each addition; `factory/lessons.md` indexes them.

## 12. Factory evals [Claude's call, from the Academy playbook and super-board]

- `evals/` in the factory repo holds real tasks with known-good outcomes and hidden checks: start with five (small Fixes and Features from BSI repos), grow toward 20–50.
- Each eval runs a track headless in a scratch clone at the pre-change commit; the hidden check scores it; the run records pass/fail, rounds, cost and time.
- Evals run whenever `roles.yaml`, `skills.yaml`, a factory skill, a prompt or a host version changes, before that change merges into the factory's main. A drop in pass rate blocks the change until the owner accepts it.
- Every factory incident (a wrong merge, a missed bug) becomes a new eval.

## 13. Third-party skills

- The factory **references** installed skills; it does not copy them. `config/skills.yaml` lists, per role and host, the skills it requires or can use, each with a fallback.
- `factory-setup` and `factory-run` check readiness before a run and report missing skills; interactive skills (grilling) are never used inside unattended roles.

| Stage | Skills | Source |
|---|---|---|
| Plan | `grilling`, `grill-with-docs`, `research`, `domain-modeling`, `last30days` | Matt Pocock skills; last30days |
| Design | `to-spec`, `codebase-design`, `diagram-design` | Matt Pocock skills; Cathryn Lavery |
| Build | `writing-plans`, `subagent-driven-development`, `to-tickets`, `wayfinder`, `implement`, `tdd` | superpowers; Matt Pocock skills |
| Test | `tdd`, `diagnosing-bugs`, `verification-before-completion` | Matt Pocock skills; superpowers |
| Deploy | `code-review`, `security-review`, `deploy-checklist` | Matt Pocock skills; Claude Code; Anthropic engineering plugin |
| Maintain | `triage`, `incident-response` | Matt Pocock skills; Anthropic engineering plugin |
| Any | `handoff` | Matt Pocock skills |

The builder confirms each skill's license before the factory documents it as a dependency for teammates.

## 14. Rule enforcement and hooks (parked)

Version 1 enforces rules through skills, instructions, git practice and the merge gate. Hooks are designed for but not built (owner, 2026-10-20). Background for later:
- Claude Code hooks can allow, deny or ask before a tool runs. Codex 0.163 has hooks with the same event names and `permissionDecision` field, covering shell, file edits and MCP tools, but cannot "ask" yet, and requires each hook to be trusted under `/hooks`.
- Candidate rules: no edits while the tester runs; no edits to a document under review; pushes only to the work branch; no edits to the failing test during a Fix; approvals written only from the owner's typed message; hard stops ask; a session cannot end with stale evals; load `progress.md` at session start.
- GitHub branch protection is the backstop for both vendors when a project adopts it.

## 15. Visibility

### 15.1 The map [F06]

`docs/map/factory-map.html` in the factory repo, drawn with the diagram-design skill: lifecycle, tracks, task loop, review panel, roles, skills, open questions, decisions. It is republished as a private web page after every change to the factory's design.

### 15.2 The dashboard [F10]

- `tools/dashboard` (Python 3) reads `factory.yaml`, `factory/work/**`, git history, the verify baseline, CI runs (`gh`) and release records, writes `factory/dashboard/data.json`, and renders `factory/dashboard/index.html` from the template in `docs/dashboard/`.
- Data contract: `schemas/dashboard.schema.json`, starting from the template's `factory-dashboard/0.1` block.
- Panels: needs you; key numbers; work by stage; selected item (stage gates, milestones, current task loop, per-lens review results, files); quality (tests vs baseline, CI); delivery (desktop channels, Helm environments, DORA metrics); factory health (playbook indicators); roles, models and skills readiness; activity.
- Regenerated at every checkpoint, task close and on demand. When running in Claude Code it can also republish a private claude.ai page for phone viewing; the local file always works for either host. **[Claude's call]**
- The dashboard is never the source of truth; every number traces to a file, a git object, a CI run or a release record.

### 15.3 Keeping visuals current

`tools/map` regenerates the role table and the model labels in the map figures from `roles.yaml`, so a model swap updates the visuals without hand edits. Figure layout stays hand-authored.

## 16. Success measures [Claude's call]

- A teammate who has never seen NGC2 installs the plugin, runs setup, and completes a Fix end to end in one session.
- A model swap is one edit to `roles.yaml` followed by a green eval run, and the map and dashboard show the new binding.
- After two months, the playbook's indicators on the dashboard trend the right way: first-pass review rate up, review rounds per task down, owner wait time down.
- No merge reaches main without a human.

## 17. Left to the builder

- Exact CLI flags and output handling for each host adapter on the installed versions.
- Python package layout and dependencies for `tools/` (keep dependencies minimal; run on Windows and Linux).
- JSON schema details, beyond the contracts named here.
- Prompt wording for lenses, tester, authors and triage.
- Which five tasks seed the evals, picked with the owner.
- Phasing and order of work, in the implementation plan. A sensible order: core (setup, triage, Fix and Feature tracks, review panel, role table, adapters) on a pilot project; then the Program track and dashboard generator; then the two Deploy paths and Maintain; then evals; hooks when unparked.

---

## Appendix A — Alignment with the Claude Academy AI-Native SDLC Playbook

| # | Playbook requirement | In this design |
|---|---|---|
| 1 | Committed `intent.md`, accepted by the owner | 8.1, 8.4 |
| 2 | `spec.md` with numbered requirements, constraint map, open questions; logs prompt/skill versions; human go/no-go | 8.1, 9.7, 8.4 |
| 3 | Plan mode; committed `plan.md` citing requirement ids; plan and diff kept in sync | 8.3 (Feature), 8.1; plan drift fixed in the same commit |
| 4 | Short, versioned `CLAUDE.md` absorbing repeat mistakes | 6.2, 11 |
| 5 | Policy as skills, backed by hooks or CI where it must always hold | 13; hooks parked (14) — **partial** |
| 6 | Parallel work in worktrees, capped by review capacity; versioned subagents including a verifier | 9.8, 6.1 (`agents/`) |
| 7 | Pasted verification output for "done"; bugs fixed test-first; test edits blocked during fixes | 9.2, 8.3 (Fix); blocking needs hooks — **partial** |
| 8 | Hermetic evals on every configuration or model change; incidents become evals | 12 |
| 9 | Every PR reviewed by an agent against `REVIEW.md`; human code-owner approval; author never approves | review panel reads `REVIEW.md` (9.3); human merge (8.4); GitHub PR review optional (F11) — **partial by owner decision** |
| 10 | Approvals as self-explaining hooks; agents cannot write approval records | 8.4 interim rule; hooks parked — **partial** |
| 11 | Headless, sandboxed CI with scoped credentials; autonomy tiered by environment; rehearsed rollback | 10 |
| 12 | Deterministic monitoring bands; breaches become intent for triage | 11 (thresholds first, bands later) |
| 13 | One source of truth per artifact | F07, 15.2 |
| 14 | Leading and lagging indicator per play; telemetry | 15.2 health panel; OpenTelemetry deferred — **partial** |
| 15 | Plays adoptable in order; bounded fixes straight to a merge, wider work back to intent | 8.3 tracks, triage step-up |

## Appendix B — Sources

- NGC2 prototype runbook, decisions, plans and reviews (`Documentation/NGC2-Prototype/` on branch `MACE-NGC2-Prototype`), NGC2 staging tooling, and the agent memory lessons.
- Claude Academy, AI-Native SDLC Playbook: https://academy.claude.com/courses/ai-native-sdlc-playbook
- zazencodes software factory (ideas only; no license): https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code
- super-board (MIT): https://github.com/EricTechPro/super-board
- Codex hooks documentation: https://learn.chatgpt.com/docs/hooks
