---
name: factory-review-panel
description: Runs the software factory's review panel on one round of one task. Renders each lens prompt from REVIEW.md, starts every lens of the track at once (as subagents or as headless vendor CLI runs), combines their reviews into PASS, CHANGES or INCOMPLETE, records maintainability follow-ups and commits the round. Use when factory-task-loop or factory-merge-gate asks for a review round.
---

# Factory review panel

Reviews one task's committed change through several lenses at once: each lens is a separate reviewer with its own checklist, and at least one comes from the other vendor. Then it combines their verdicts into one. You run the reviewers and record what they say; you never write, shorten or correct a review yourself.

Uses skills: code-review

The code-review skill is the lens reviewers' craft: its practice (the diff and the spec before the implementer's account, evidence for every blocking finding) is built into the lens prompts, and `factory skills check` reports whether it is installed for the lens roles.

## Names used below

Names such as `<ID>`, `<work>`, `<vendor>`, `<factory>` and `<staging>` mean what the table at the top of the factory-run skill (`../factory-run/SKILL.md`) says. The caller gives you the rest:

| Name | Meaning |
|---|---|
| `<task>`, `<N>` | the task id (`T2`, `M1-T3`) and the review round, from 1 |
| `<start>` | the full SHA the change starts from: the task's start commit, or at the merge gate the base branch's commit |
| `<head>` | the full SHA of the commit under review |
| `<author>` | the vendor (`claude` or `openai`) that wrote the change; it decides which lenses count as "other vendor" |

Run every command from the repository root.

## 1. Check the starting point

1. `git status --porcelain --untracked-files=no` prints nothing and `git rev-parse HEAD` prints `<head>`. Otherwise go back to the caller: the panel reviews committed work only.
2. **The lenses.** The track comes from the id's prefix (`FIX-` fix, `FEA-` feature, `PRG-` program); its lenses are the list under `tracks:` for that track in the front matter of `REVIEW.md`. If the list names any lens other than `correctness`, `security`, `maintainability` and `spec`, stop and tell the owner: this version of the factory has review prompts for those four only, and project lenses come in a later version. The owner removes the lens from the track or waits. Never run part of a panel.
3. **The spec path** is `<work>/ticket.md` for a Fix and `<work>/spec.md` otherwise.
4. **Resuming:** a lens whose review file for this round, `<work>/reviews/<task>-r<N>-<lens>.md`, already exists (a session stopped in the middle of the round) is not run again; the combine step says whether that file is valid.

## 2. Render each lens prompt

For each lens:

`factory prompt render <factory>/prompts/lens-<lens>.md --from-review-md REVIEW.md --lens <lens> --set task_id=<task> --set round=<N> --set diff_command="git diff <start>..<head> -- . ':(exclude)factory/work'" --set spec_path=<spec path> --set prior_review_path=<prior> --out <staging>/<task>-r<N>-<lens>-prompt.md`

- `--lens` fills the prompt's lens name, that lens's checklist from `REVIEW.md` and the nit cap. Never pass `--set lens=…` as well.
- `<prior>` is `none` in round 1. In a later round it is that lens's review from the round before, `<work>/reviews/<task>-r<N-1>-<lens>.md`.
- The diff is a read-only `git diff` between two full SHAs. It leaves out `factory/work/` (the plan, earlier reviews, dispositions), so each reviewer reads the product change before anything the implementer wrote about it; the prompt gives the reviewer the spec and its earlier review by path.

## 3. Run every lens at once

1. **Resolve each lens:** `factory role resolve lens-<lens> --host <vendor> --author <author> --project .` prints JSON. `invocation.kind` says how the lens runs; `resolved.vendor`, `resolved.model` and `resolved.effort` say who runs it.
2. **Start all of them before waiting for any.** Spawn every subagent lens in the same turn, and start the command lenses so that they run at the same time: in Claude Code, each in the shell tool's background mode; in Codex, one shell command that starts each run as a background job and waits for all of them, with a timeout longer than the slowest run (each `factory role run` stops itself after an hour). Keep each command's exit code and error output.
   - `subagent` (only in Claude Code): spawn `invocation.agent` (`factory-lens-reviewer`, or `software-factory:factory-lens-reviewer` when the factory is installed as a plugin and the bare name is not available) with model `invocation.model_alias`, giving it the rendered prompt's text as its task. It only reads; its reply is the review. Save the reply, unchanged, as `<work>/reviews/<task>-r<N>-<lens>.md` with your file-writing tool, then append a blank line and `<!-- factory: role=lens-<lens> vendor=<resolved.vendor> model=<resolved.model> effort=<resolved.effort> sandbox=read-only at=<UTC time, such as 2026-10-20T15:42:07Z> -->`.
   - `command`: `factory role run lens-<lens> --host <vendor> --author <author> --project . --prompt <staging>/<task>-r<N>-<lens>-prompt.md --out <work>/reviews/<task>-r<N>-<lens>.md`. Exit 0: it wrote and stamped the review file. Exit 1: the run failed and left no file; run the same command once more with `--fallback`, and note that for the round's log entry. Exit 2: a usage problem it names; fix it and run again.
   - `main`: a reviewer must never be the session that wrote the change. Stop and tell the owner.
3. **While any lens runs**, change nothing in the working tree except the review files you save: the reviewers read it.
4. **Never edit a review.** Do not trim, reformat or complete a reviewer's reply to make it parse, and never write a review yourself. A reply that is not a valid review counts as invalid, and that lens runs again (section 4).

## 4. Combine

1. When every lens has finished, run `factory panel combine <work> --task <task> --round <N> --commit <head>`. It prints the summary, writes `<work>/reviews/<task>-r<N>-panel.md`, and exits with the verdict: **0 PASS**, **1 CHANGES**, **2 INCOMPLETE**. An exit 2 that printed no summary, only a reason on stderr, means it could not run: fix that and run it again.
2. **INCOMPLETE:** the summary's `## Missing` and `## Invalid` sections name the lenses without a readable review. Run only those lenses again, once, the same way as their last attempt (a command lens that needed `--fallback` uses it again; for a subagent lens, replace the invalid file with the new reply), then combine again. Still INCOMPLETE: record the round (section 6) with the state `blocked` and the next step `owner decides: lens <lens> gave no readable review in round <N>`, then stop and show the owner the summary.
3. **Warnings** (`## Warnings`: a CHANGES verdict with no blocking finding, more nits than the cap) do not change the verdict. Pass them on with it.
4. Maintainability findings never block: the panel lists them under `## Follow-ups`. Blocker and major findings of every other lens are under `## Blocking`.

## 5. Record follow-ups

Append these findings to `<work>/follow-ups.md`, creating it with the heading and table below if it is missing:

- every finding under the summary's `## Follow-ups` (all maintainability findings, whatever their severity);
- in a PASS round, every `minor` finding of the other lenses, read from their review files (the summary lists only their blockers and majors): their prompts promise that a minor becomes a follow-up, and a passing round has no disposition to answer it.

Skip a finding whose text starts "Repeats R<n>" when the same task and lens already have an entry for `R<n>`.

```markdown
# Follow-ups

Review findings left for later. After the merge, factory-run turns each `open` entry into a queued work item with factory-triage.

| Task | Round | Lens | ID | Finding | Suggested track | Status |
|---|---|---|---|---|---|---|
| T2 | 1 | maintainability | R3 | src/orders/totals.py:40: `_sum_lines` repeats `average_price`'s loop; share one helper. | fix | open |
```

- **Finding:** its location, what is wrong and the proposed fix, on one line; write a literal `|` as `\|`.
- **Suggested track:** `fix` for a change one task can make without touching a shared contract, schema or file format; `feature` otherwise. Triage settles it with the owner later.
- **Status:** `open`. The task loop changes it to `fixed in <task>` when it fixes the finding after all.

Create no work item now: `factory work new` switches the checkout under the running task.

## 6. Record the round

1. `factory work set-status <ID> --stage <stage> --state "<state>" --next "<next>" --note "<task> round <N>: panel <verdict> (reviews/<task>-r<N>-panel.md)"`, with the stage that `factory work status <ID>` shows. Add to the note each lens that ran on its fallback binding (`lens-security ran on its fallback`). The state and next step:
   - PASS: `<task> review passed`; next, the caller's step after a passing round (the task loop: the tester, or for a Fix the close; a merge-gate re-review: `the merge gate (factory-merge-gate section 3)`).
   - CHANGES: `<task> changes requested`; next `<task>: write disposition-<task>-r<N>.md, then fix`, ending with `, then the merge gate (factory-merge-gate section 3)` in a merge-gate re-review.
   - INCOMPLETE after the retry: `blocked`, as section 4 says.
2. Commit `<work>/reviews/<task>-r<N>-*.md`, `<work>/follow-ups.md` (if changed) and `progress.md` with the task's trailers (`Factory-Work: <ID>/<task>`, `Factory-Role: implementer` and the implementer's model, as the factory-task-loop skill says). This commit changes only `factory/work/`, so `<head>` stays the commit the merge gate accepts as reviewed.

## 7. Report

Tell the caller, and the owner, the verdict with its evidence: the summary's path, the blocking findings (lens, id, severity, location, finding), the follow-ups recorded, the warnings, and any lens that ran on its fallback. Then the caller acts on the verdict.
