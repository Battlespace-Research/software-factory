---
name: factory-triage
description: Size a new piece of work for the software factory and open its work item. Use when the owner brings a bug, a change or an idea to a project set up for the factory (it has a factory.yaml), asks to queue work, or when a Fix or Feature has to step up to a bigger track. Picks the Fix, Feature or Program track by the factory's thresholds, creates the branch and work folder, writes and confirms a Fix ticket, then hands over to factory-run.
---

# Factory triage

Puts incoming work in the smallest track that fits, records why, and opens its work item: an id (`FIX-<n>`, `FEA-<n>`, `PRG-<n>`), a branch and a folder under `factory/work/`. A Fix leaves triage with a confirmed ticket; every item then goes to the `factory-run` skill.

Uses skills: grilling, research

## Before you start

- The project must be set up: `factory.yaml` at the repository root. If it is not, use the `factory-setup` skill first.
- Ask the owner **one question per message**, in the grilling skill's style.
- `<vendor>` below is `claude` in Claude Code and `openai` in Codex (CLI or the ChatGPT app).

## 1. Understand the work

Ask, one question at a time, until you can say: what is wrong or wanted, for whom, what "done" looks like, and what it touches. Use the research skill to get facts from the repository rather than asking the owner for them: which files and modules are involved, whether the work touches a shared contract, a schema or a file format, and roughly how many tasks it is. A quick look is enough; save no notes in the project before the work item exists, and do not design the solution yet.

## 2. Pick the track

Put the work in the **smallest track that fits** (design section 8.3):

| Track | Fits when the work is |
|---|---|
| **Fix** | one session, one task, and touches no shared contract, schema or file format |
| **Feature** | up to about five tasks, with no milestones, in one repository |
| **Program** | anything bigger: needs milestones, more than about five tasks, or more than one repository |

A Fix that needs more than one session, more than one task, or a change to a shared contract, schema or file format is a Feature. A Feature that needs milestones, crosses repositories or outgrows about five tasks is a Program. Touching a git submodule does not by itself make work a Program.

Tell the owner the track and the reason in a sentence or two. The owner may choose another track; their choice, and why, becomes the recorded reason.

## 3. Name it

- **Slug:** a short name for the branch and folder, 1 to 40 characters from `a-z`, `0-9` and `-` (for example `km-label`).
- **Title:** one line saying what the work is.

## 4. Open the work item

1. **Clean tree.** `git status --porcelain --untracked-files=no` must print nothing, because the next command switches the checkout. If it lists files: commit work you made in this session where it belongs; for anything else, show the owner and ask. Never stash, reset or discard silently.
2. **See every id in use.** If `git remote` lists a remote, run `git fetch --all`, so ids on teammates' branches are seen.
3. **Branching** (`branching` in `factory.yaml`):
   - `per-item`: the new branch is cut from the local base branch (`base_branch`). If that is behind its remote copy (`git rev-list --count <base>..origin/<base>` prints more than 0), tell the owner and ask whether they want it updated first; only the owner moves the base branch.
   - `single`: switch to the work branch named in `factory.yaml` first. It holds one work item at a time: if an item there is not closed, ask the owner before opening another.
4. **Open it:** `factory work new --track <fix|feature|program> --slug <slug> --title "<title>"`. It allocates the id, creates (with `per-item`, switches to) the branch, copies the track's template files and commits them; it prints JSON with `id`, `branch` and `folder`.
5. **Record the reason:** `factory work set-status <ID> --stage plan --state open --next "<next step>" --note "Triage: <track>, because <reason>"`. The next step is "write ticket.md" for a Fix, "short grill, then write intent.md" for a Feature, and "research and grill, then write intent.md and decisions.md" for a Program.
6. **Commit it:** `factory role resolve interviewer --host <vendor> --project .` gives `resolved.model` and `resolved.effort`; then `git add -- <folder>/progress.md` and `git commit -m "<ID>: triage as <track>" --trailer "Factory-Work: <ID>" --trailer "Factory-Role: interviewer" --trailer "Factory-Model: <model> (<effort>)"`. Keep your host's own co-author line if it adds one.
7. **Claim the id** by pushing the item's branch, under the factory's one push rule (section 8 of `../factory-run/SKILL.md`): push only the work item's branch (or its lessons branch, after the merge), only when a remote exists, only if the project's rules and `hard_stops` allow it, and never the base branch. First read `hard_stops` in `factory.yaml` and `../factory-run/references/hard-stops.md`; if a stop covers pushing (such as a project `no-push` id), do not push, and tell the owner. Otherwise: `git push -u origin <branch>` (with `per-item` branching `factory/<ID>-<slug>`; with `single`, the work branch).

## 5. A Fix: write the ticket

1. Fill in `<folder>/ticket.md`:
   - **Problem:** what goes wrong, where, for whom; one short paragraph.
   - **Repro steps:** numbered steps that make it fail, and what you see against what you expect. Reproduce it yourself when you can, and say whether you did. The failing test is written from these steps.
   - **Acceptance criteria:** checkable statements that are true when the fix is done, including that a test which failed before the fix now passes.
   - **Out of scope:** what the fix will not touch.
2. Commit it the same way as step 4.6, with the subject `<ID>: ticket`.
3. Ask the owner to confirm the repro steps and the acceptance criteria, following "Asking" in `../factory-run/references/checkpoints.md` (artifact `ticket`, revision 1, stage `plan`; names such as `<staging>` are defined at the top of the factory-run skill). Ask in auto mode as well: triage is a conversation with the owner, and wrong criteria give a wrong fix. If they change anything, revise, commit, and ask again; a ticket changed after its approval is approved again as revision 2, then 3.
4. When the owner confirms, record it exactly as "Recording an approval" in the same file says: their words, verbatim, through `factory approve`. The next stage is `build` and the next step "T1: write the failing test from the repro steps".

If writing the ticket shows the work is bigger than a Fix, stop and step it up (below) before going on.

## 6. Hand over

Give the item to the `factory-run` skill with its id: a Fix once its ticket is confirmed, a Feature or Program straight after step 4.

If the owner wants the item queued rather than started (a Fix after its ticket is confirmed, while the owner is here), switch back to the branch you were on (`git switch <previous branch>`) and stop. With `per-item` branching the item's branch is its place in the queue.

## Stepping up

When the work outgrows its track (found here, or by `factory-run`), tell the owner why and ask; stepping up changes the work's cost, so it is the owner's call, in auto mode too. With their agreement:

1. Commit any work on the old item, then open the new item at the bigger track (sections 3 and 4). Its triage note starts "Stepped up from <old ID>: <why>".
2. Carry forward what still holds: read the old files without switching, with `git show <old branch>:factory/work/<old ID>-<old slug>/ticket.md` (or `intent.md`), and use them in the new item's intent, naming the old id. Commit.
3. Switch to the old item's branch, run `factory work set-status <old ID> --stage close --state "stepped up" --next "none: continues as <new ID>" --note "Stepped up to <new ID>: <why>"`, commit, push it under the push rule (step 4.7), and switch back to the new item's branch. The old id is never reused, and no id is renumbered.
