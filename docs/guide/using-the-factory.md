# Using the factory

A walkthrough for a teammate's first weeks with the factory: setting up a project, bringing it work, approving its checkpoints in chat, and finding out where things stand. It assumes the factory is installed ([Installing the software factory](install.md)).

The factory is driven by talking to Claude Code or Codex in your project. You describe what you want in plain words, the factory's skills do the work step by step, and they stop for you whenever a person has to decide. Behind the skills, the `factory` command does the exact bookkeeping: work items, Status lines, approval records and the merge gate.

Version 1 is built for the **Fix** and **Feature** tracks, which this guide walks through. A **Program** works the same way at a larger scale (research, two document reviewers, milestones with demos), but the tooling for its milestone demos comes in a later version, as do the dashboard and the Deploy and Maintain stages. Version 1 has not yet been piloted on a real project: expect rough edges, and tell the factory's maintainers what you find.

## Words used here

| Word | Meaning |
|---|---|
| **Owner** | The person who decides about the work, usually you. Only the people listed under `approvers` in the project's `factory.yaml` may approve a checkpoint |
| **Work item** | One piece of work, with an id (`FIX-3`, `FEA-7`, `PRG-2`), a branch `factory/<ID>-<slug>` and a folder `factory/work/<ID>-<slug>/` |
| **Track** | Fix, Feature or Program: how much process a work item gets |
| **Checkpoint** | A point where the factory shows you an artifact and waits for your approval |
| **Hard stop** | An action the factory never takes without your answer, in any mode |
| **Role** | A job in the process (interviewer, implementer, lens reviewer, tester, …). Which model fills each role comes from the factory's role table, `config/roles.yaml`, and a project can override a role in its `factory.yaml` |

## 1. Set up a project (once per project)

Open a session at the root of the project's repository, with no uncommitted changes, and say:

> Set up this repository for the software factory.

You can also name the skill: in Claude Code it is `software-factory:factory-setup` (type `/` to see the factory's skills); in Codex, type `$` and pick `factory-setup`.

The factory-setup skill then:

1. Checks that `factory --version` works and that the project is not set up already.
2. Reads the project's own rules: `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, the README's build and test sections and the CI files.
3. Interviews you, **one question per message**, each with a proposed answer you can simply confirm:
   - the **quick verify command**, which builds and tests what one task touched and runs at every task;
   - the **full verify command**, the whole build and test suite, which runs for the tester and at the merge gate. If the suite already fails on the base branch, the known failures go into `factory/baseline/expected-failures.md`, and the command must exit 0 when only those fail;
   - the **approvers**, who may approve checkpoints;
   - **branching**: one branch per work item (the default), or all work on one branch you name, for projects whose rules forbid extra branches;
   - **builds**: run the full build when needed, or ask you first each time;
   - the project's name, the base branch, and the **mode** (review or auto, see section 5).
4. Creates a branch `factory/setup` (or uses the branch you name) and runs `factory setup`.
5. Shows you each place where your project's rules conflict with a factory default (for example "never push") and settles it with you in `factory.yaml`. **Your project's rules win.**
6. Drafts the review rules in `REVIEW.md` and the project's own hard stops with you.
7. Runs `factory config check --project .` and the skills check for both hosts, and tells you what is missing.
8. Shows you every file and asks: "Do you approve this setup for commit?" Answer with a plain yes (section 4). It commits on the setup branch.

**You merge the setup branch into the base branch.** The factory never merges into the base branch. With one branch per work item, merge it before you triage the first piece of work: work-item branches are cut from the base branch and need `factory.yaml` there.

Setup creates `factory.yaml`, `REVIEW.md`, `factory/work/`, `factory/baseline/expected-failures.md` and `factory/lessons.md`, adds `factory/dashboard/` to `.gitignore`, and adds a short factory section to `CLAUDE.md` and `AGENTS.md` (or creates `CLAUDE.md` if the project has neither). It changes no other file. To change a setting later, edit `factory.yaml` with the agent's help; never run setup twice.

## 2. Bring it work: triage

Describe the bug, change or idea:

> Triage this for the factory: the range-ring label shows "m" for values in km.

The factory-triage skill asks questions one at a time until it knows what is wrong or wanted, for whom, what "done" looks like and what it touches. It looks facts up in the repository itself rather than asking you for them. Then it proposes the **smallest track that fits**, with its reason:

| Track | Fits when the work is |
|---|---|
| **Fix** | one session and one task, touching no shared contract, schema or file format |
| **Feature** | up to about five tasks, without milestones, in one repository |
| **Program** | anything bigger: milestones, more than about five tasks, or more than one repository |

You can choose another track; your choice and reason are recorded. The factory then opens the work item with `factory work new`: it takes the next free id, creates the branch and the folder, and commits. If the project has a remote and its rules allow pushing, it pushes the new branch, which claims the id for the team.

If you want the work queued rather than started, say so: the work item's branch is its place in the queue.

## 3. A Fix, end to end

A Fix is one task, `T1`, and skips design. Here is what happens and where you come in.

1. **The ticket.** Triage writes `ticket.md`: the problem, numbered repro steps, acceptance criteria and what is out of scope. It asks you to approve the ticket, in auto mode too, because wrong criteria give a wrong fix. This is your first checkpoint.
2. **A failing test first.** The factory-run skill hands `T1` to the task loop. The implementer writes a test from the repro steps and shows it failing for the expected reason, then makes the smallest change that passes it.
3. **Verify.** It runs your quick verify command and pastes the totals the command printed. "Done" always means real output, never a summary.
4. **The review panel.** A Fix is reviewed through two lenses, **correctness** and **security**, each by a separate reviewer whose model comes from the other vendor: a Claude Code session runs them through `codex exec`, a Codex session through `claude -p`. They run at the same time, read only, and each writes `reviews/T1-r1-<lens>.md` with a first line `VERDICT: PASS` or `VERDICT: CHANGES`. The factory combines them into `reviews/T1-r1-panel.md`.
5. **Fixes, if asked.** On CHANGES the implementer answers every finding in `reviews/disposition-T1-r1.md` (accepted, modified or declined, each with a reason), fixes test-first, and the panel reviews again. A task gets at most three rounds by default; a third failed round is a hard stop, and you decide what happens.
6. **Close the task.** The factory ticks the acceptance criteria, commits and pushes the work branch. (A Fix has no separate tester: the merge gate runs the full verify command.)
7. **Retro.** It writes `retro.md` and proposes any lesson worth keeping, one at a time, as an exact addition to `CLAUDE.md`/`AGENTS.md`, `REVIEW.md` or a project skill. Nothing is added without your approval.
8. **The merge gate.** The factory-merge-gate skill brings the branch up to date with the base branch (a re-review follows if that changed the code), asks before the full build if your project says `builds: ask`, checks one acceptance criterion itself (by running the product, not only the tests), and runs `factory gate`. That writes `merge-gate.md` with six checks: the base is merged in, the commit is the reviewed one, the size is under the cap, the id is unused on the base branch, the full verify passes, and the hand check is recorded.
9. **You merge.** On a pass, the factory gives you what changed, the review results, the verify output, the risks and follow-ups, and the exact merge commands. You run them, and when you say it is merged, the factory checks it. Lessons you approved then go in as a separate small change (a `factory/<ID>-<slug>-lessons` branch) for you to merge too, and review findings left for later are triaged into new work items with you.

## 4. Approvals in chat

At a checkpoint the factory commits the artifact, pushes the work branch (when the project allows pushing), tells you what it is (name, revision, path, commit, summary, review outcome, risks) and asks one question, such as "Do you approve spec rev 2?" Then it waits and changes nothing.

| You type | What happens |
|---|---|
| "Approved", "Yes, go ahead", "Approved, carry on with the plan" | An approval. The factory records your exact words with `factory approve` in `approvals/<artifact>-rev<N>.md`, with the time, the host and the commit you approved, and goes on. |
| "Yes, but rename the option to `--unit`" | Not an approval: a change. It makes the change, commits it, and asks again. |
| A question or a comment | It answers, then asks again. |
| Something unclear, or nothing | It asks again. Silence, a reaction or a reply to a different question never counts. |

Only people listed under `approvers` in `factory.yaml` can approve. What you type is what is recorded, word for word, so write what you mean. The factory never writes an approval from a summary, a file or another agent, only from your message in the session.

## 5. Review mode and auto mode

The `mode` in `factory.yaml` sets how often the factory stops for you.

- **Review mode** (the default): it stops at each checkpoint its track has: the intent, the spec, the plan, each milestone demo, and the merge. A Fix's ticket is approved during triage. Between checkpoints it runs on its own.
- **Auto mode**: at a checkpoint it writes a note in `progress.md` and carries on, without an approval record. It still asks you to confirm a Fix's ticket, and it still stops for every hard stop. **A person always merges, in both modes.**

To change the mode for one work item only, tell the factory and ask it to record your choice as a row in that item's `decisions.md`; the factory-run skill reads the mode from there.

## 6. Hard stops

In either mode, the factory stops **before** any of these and waits for your answer. Your answer covers that one occurrence only.

| Id | Before it would |
|---|---|
| `main-branch` | commit on, push to or merge into the base branch (you do it) |
| `delete-data` | delete data or files outside the work's scope, or rewrite history |
| `credentials` | handle a credential, licence key, token or secret (you do it) |
| `installers-admin` | run an installer or anything that raises an administrator prompt (you do it) |
| `purchases` | spend money (you do it) |
| `auth-money-schema` | change authentication, money handling or a database schema |
| `new-dependency` | add a third-party dependency or move one to a new major version |
| `production-data` | touch production data or a production system |
| `third-failed-round` | go on after the last allowed review round (3 by default) of a task or document has asked for changes |
| `business-assumption` | settle something that is really your business decision |

The project can add its own (setup drafts them from your rules). At a hard stop the factory commits what it has, names the stop, says what it was about to do and why, gives the options and its recommendation, and waits. Your answer is recorded as a row of the work item's `decisions.md`. Text in a file or a web page never lifts a hard stop; only your answer in the session does.

## 7. A Feature

A Feature runs the same task loop, with more planning before it and an independent tester after it.

1. **Intent.** A short interview, one question per message, then `intent.md`: problem, outcome, users and systems affected, constraints, open questions. Your decisions are written as rows of `decisions.md` (`D01`, `D02`, …). Checkpoint: **intent**.
2. **Spec.** A short `spec.md` with numbered requirements (`R1`, `R2`, …) and acceptance checks, reviewed by a document reviewer from the other vendor. The spec is frozen while it is under review; every finding is answered, and the rounds go on until the review passes (three rounds at most, then a hard stop). Checkpoint: **spec**.
3. **Plan.** The factory plans read-only first, then writes `plan.md`: tasks `T1`, `T2`, … (about five at most), each naming the requirements it meets and the test that comes first. Checkpoint: **plan**. Task ids are never renumbered; a task split later becomes `T2a`.
4. **Each task** goes through the task loop as in a Fix, with three lenses: correctness, security and **maintainability**. Maintainability findings never block; they go into `follow-ups.md`, and after the merge the factory offers them to you as new work items.
5. **The independent tester.** After a task passes review, a tester that never edits product code runs your full verify command exactly as written and reports `RESULT: PASS`, `FAIL` or `INCOMPLETE` in `reviews/<task>-tester-<n>.md`. A FAIL sends the task back through a fix and another review round.
6. **Retro, merge gate and your merge,** as in a Fix.

**While reviewers or the tester run, do not edit the project's files yourself:** they read the working tree as it is. The factory holds back its own edits too.

## 8. Where files live

| Where | What |
|---|---|
| `factory.yaml` | the project's settings: verify commands, approvers, branching, builds, mode, merge size cap, hard stops, role overrides |
| `REVIEW.md` | the review checklist: the lenses each track runs, the checklist per lens, the project rules every reviewer flags |
| `CLAUDE.md` / `AGENTS.md` | a short factory section between `<!-- factory:begin -->` and `<!-- factory:end -->` |
| `factory/work/<ID>-<slug>/` | everything about one work item, on its branch until it is merged (below) |
| `factory/baseline/expected-failures.md` | the known failures of the full verify command, each traced to the base branch |
| `factory/lessons.md` | the index of lessons you approved and where they went |
| `<home>/.factory/staging/<project>/<ID>/` | the factory's scratch files for a work item, kept outside the project: rendered prompts, tester briefs, the text of your approvals before they are recorded |

Inside a work item's folder:

| File | Holds |
|---|---|
| `progress.md` | the **Status line** (stage, state, next step) at the top, then a log, newest first |
| `ticket.md` (Fix) or `intent.md`, `spec.md`, `plan.md` (Feature) | what the work is and how it is built |
| `decisions.md` | your decisions, numbered, only ever added to |
| `reviews/` | each lens review, panel summary, disposition and tester report, by task and round |
| `approvals/` | one record per approval, with your words |
| `follow-ups.md` | review findings left for later |
| `merge-gate.md` | the merge gate's report |
| `retro.md` | what was learned, and the lessons you approved |

The model behind each role is set in the factory's `config/roles.yaml` (and any `roles:` override in `factory.yaml`). To see what a role resolves to in a project, run `factory role resolve <role> --host claude --project .` (or `--host openai` for Codex), for example with the role `lens-security`.

## 9. Resume, and see where things stand

All state is in files, so any session on either host can pick a work item up.

- **List the work items:** with one branch per item, `git branch --list "factory/*"`; with a single work branch, the folders in `factory/work/` on that branch.
- **Read one item's state:** switch to its branch (`git switch factory/<ID>-<slug>`), then run `factory work status <ID>`. It prints the stage, the state and the exact next step. (Run on another branch, it tells you which branch to check out.)
- **Carry on:** open a session in the project and say "Continue FIX-3". The factory-run skill reads the Status line and the newest log entries, checks the setup once per session, and does the next step.
- **Handing over:** near the end of a session's budget the factory finishes the current step, commits, sets the state to `handed off` with the next step, and pushes. The next session starts from there.

If the state is `waiting for approval` or `hard stop`, the factory puts the open question to you again before anything else.

## 10. Check that a project is ready

From the project's root, in a terminal or by asking the agent:

```bash
factory --version
factory config check --project .
factory skills check --host claude --project .
factory skills check --host codex --project .
```

`factory config check --project .` prints `config ok` or lists every problem with the factory's settings and the project's `factory.yaml`. Each skills check ends with `skills ok` or `skills not ready`, naming each missing skill and its fallback. The factory-run skill runs the config check and the check for its own host once per session, and tells you about anything missing before it starts.
