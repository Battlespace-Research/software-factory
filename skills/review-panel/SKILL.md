---
name: review-panel
description: "BSRI software factory: Runs the software factory's review panel on one round of one task. Renders each lens prompt from REVIEW.md, starts every lens of the track at once (as subagents or as headless vendor CLI runs), combines their reviews into PASS, CHANGES or INCOMPLETE, records maintainability follow-ups and commits the round. Use when task-loop or merge-gate asks for a review round."
---

# Review panel

Reviews one task's committed change through several lenses at once: each lens is a separate reviewer with its own checklist, and at least one comes from the other vendor. Then it combines their verdicts into one. You run the reviewers and record what they say; you never write, shorten or correct a review yourself.

Uses skills: code-review, tdd, codebase-design, domain-modeling

The code-review skill is the lens reviewers' craft: its practice (the diff and the spec before the implementer's account, evidence for every blocking finding) is built into the lens prompts, and `sw-factory skills check` reports whether it is installed for the lens roles.

Three lenses also get a **reference skill** as guidance for their lens: correctness `tdd`, maintainability `codebase-design`, and spec `domain-modeling` (only in a project that has domain files, below). Security gets none. `sw-factory prompt render --reference-skill` inlines the installed skill's text into the lens prompt, so the reviewer needs no access to the skill's folder. A reviewer reads that text, it never runs it: no procedure followed, no agents spawned, no commands run, no files written because of it, so each lens stays one reviewer writing one review in the panel's format.

## Names used below

Names such as `<ID>`, `<work>`, `<vendor>`, `<factory>` and `<staging>` mean what the table at the top of the orchestrate skill (`../orchestrate/SKILL.md`) says. The caller gives you the rest:

| Name | Meaning |
|---|---|
| `<task>`, `<N>` | the task id (`T2`, `M1-T3`) and the review round, from 1 |
| `<start>` | the full SHA the change starts from: the task's start commit, or at the merge gate the base branch's commit |
| `<head>` | the full SHA of the commit under review |
| `<author>` | the vendor (`claude` or `openai`) that wrote the change; it decides which lenses count as "other vendor" |

Run every command from the repository root.

## 1. Check the starting point

1. `git status --porcelain --untracked-files=no` prints nothing and `git rev-parse HEAD` prints `<head>`. Otherwise go back to the caller: the panel reviews committed work only.
2. **The lenses.** The track comes from the id's prefix (`FIX-` fix, `FEA-` feature, `EPIC-` epic); its lenses are the list under `tracks:` for that track in the front matter of `REVIEW.md`. If the list names any lens other than `correctness`, `security`, `maintainability` and `spec`, stop and tell the owner: this version of the factory has review prompts for those four only, and project lenses come in a later version. The owner removes the lens from the track or waits. Never run part of a panel.
3. **The spec path** is `<work>/ticket.md` for a Fix and `<work>/spec.md` otherwise.
4. **Resuming:** a lens whose review file for this round, `<work>/reviews/<task>-r<N>-<lens>.md`, already exists (a session stopped in the middle of the round) is not run again; the combine step says whether that file is valid.

## 2. Render each lens prompt

**The domain files**, once per round when the spec lens runs: `git ls-files -- ":(glob)**/CONTEXT.md" CONTEXT-MAP.md docs/adr doc/adr docs/decisions` lists the project's glossaries (`CONTEXT.md` in any folder, `CONTEXT-MAP.md`) and decision records (ADRs). `<domain files>` is that list, the paths separated by spaces, or `none` when it prints nothing. A work item's own `decisions.md` is not among them.

Then for each lens:

1. **Its reference skill:**

   | Lens | `<reference>` |
   |---|---|
   | correctness | `tdd` (required; its fallback `test-driven-development` is used when it is missing) |
   | maintainability | `codebase-design` (required) |
   | spec | `domain-modeling` when `<domain files>` is not `none`; otherwise `none` |
   | security | no reference skill: pass no `--reference-skill`, `--host` or `--home` |

2. **Render the prompt:**

   `sw-factory prompt render <factory>/prompts/lens-<lens>.md --from-review-md REVIEW.md --lens <lens> --set task_id=<task> --set round=<N> --set diff_command="git diff <start>..<head> -- . ':(exclude)sw-factory/work'" --set spec_path=<spec path> --set prior_review_path=<prior> --reference-skill <reference> --host <app> --out <staging>/<task>-r<N>-<lens>-prompt.md`

   - For spec, add `--set domain_files="<domain files>"`.
   - For security, leave out `--reference-skill <reference> --host <app>`: its prompt has no reference section, and `sw-factory prompt render` refuses a value the template does not use.
   - `<app>` is the **app** you are running in, `claude` (Claude Code) or `codex`, as for `sw-factory skills check`. It is not the vendor that `sw-factory role resolve --host` takes (`openai` in Codex): the two commands give `--host` different meanings. Only that app's user-level skill folders are searched, never the project's own, so the change under review cannot supply its reviewer's guidance.
   - After `wrote …`, it prints `reference: <name> sha256:<12 hex>` (the skill it inlined, which for correctness may be the fallback) or `reference: none`. A skill that is not installed does not stop the panel: the prompt then says no reference skill was available. When that happens for a required one (`tdd` with no fallback installed, or `codebase-design`), add to the round's note (section 6) that the lens ran without its reference skill. `sw-factory skills check` reports the skill as missing too.
   - Exit 2 names a problem and writes no prompt. For a skill that is installed but cannot be inlined (a file its registry entry lists is missing or not UTF-8, the text is too long or holds a reference tag), render that lens again with `--reference-skill none` and add the reason to the round's note. Fix any other problem and render again. Never patch a rendered prompt by hand.
   - `--lens` fills the prompt's lens name, that lens's checklist from `REVIEW.md` and the nit cap. Never pass `--set lens=…` as well.
   - `<prior>` is `none` in round 1. In a later round it is that lens's review from the round before, `<work>/reviews/<task>-r<N-1>-<lens>.md`.
   - The diff is a read-only `git diff` between two full SHAs. It leaves out `sw-factory/work/` (the plan, earlier reviews, dispositions), so each reviewer reads the product change before anything the implementer wrote about it; the prompt gives the reviewer the spec and its earlier review by path.

## 3. Run every lens at once

1. **Resolve each lens:** `sw-factory role resolve lens-<lens> --host <vendor> --author <author> --project .` prints JSON. `invocation.kind` says how the lens runs; `resolved.vendor`, `resolved.model` and `resolved.effort` say who runs it.
2. **Start all of them before waiting for any.** Spawn every subagent lens in the same turn, and start the command lenses so that they run at the same time: in Claude Code, each in the shell tool's background mode; in Codex, one shell command that starts each run as a background job and waits for all of them, with a timeout longer than the slowest run (each `sw-factory role run` stops itself after an hour). Keep each command's exit code and error output.
   - `subagent` (only in Claude Code): spawn `bsri-sw-factory:` plus the name `invocation.agent` prints, here `bsri-sw-factory:lens-reviewer` (`invocation.agent` prints the bare name, which can resolve to another plugin's or the user's own agent; spawn the bare name only when the factory's `agents/` files are installed directly, not through the plugin), with model `invocation.model_alias`, giving it the whole rendered prompt file, verbatim, as its task: with a reference skill inlined it is around 20 KB, so read it all and pass it on unshortened. It only reads; its reply is the review. Save the reply, unchanged, as `<work>/reviews/<task>-r<N>-<lens>.md` with your file-writing tool, then append a blank line and `<!-- factory: role=lens-<lens> vendor=<resolved.vendor> model=<resolved.model> effort=<resolved.effort> sandbox=read-only reference=<marker> at=<UTC time, such as 2026-10-20T15:42:07Z> -->`, where `<marker>` is what the prompt's `<!-- reference: … -->` line says (`tdd sha256:…` or `none`, as `sw-factory prompt render` printed it). For security, whose prompt has no such line, leave out `reference=<marker>`. `sw-factory role run` stamps a command lens the same way by itself.
   - `command`: `sw-factory role run lens-<lens> --host <vendor> --author <author> --project . --prompt <staging>/<task>-r<N>-<lens>-prompt.md --out <work>/reviews/<task>-r<N>-<lens>.md`. Exit 0: it wrote and stamped the review file. Exit 1: the run failed and left no file; run the same command once more with `--fallback`, and note that for the round's log entry. Exit 2: a usage problem it names; fix it and run again.
   - `main`: a reviewer must never be the session that wrote the change. Stop and tell the owner.
3. **While any lens runs**, change nothing in the working tree except the review files you save: the reviewers read it.
4. **Never edit a review.** Do not trim, reformat or complete a reviewer's reply to make it parse, and never write a review yourself. A reply that is not a valid review counts as invalid, and that lens runs again (section 4).

## 4. Combine

1. When every lens has finished, run `sw-factory panel combine <work> --task <task> --round <N> --commit <head>`. It prints the summary, writes `<work>/reviews/<task>-r<N>-panel.md`, and exits with the verdict: **0 PASS**, **1 CHANGES**, **2 INCOMPLETE**. An exit 2 that printed no summary, only a reason on stderr, means it could not run: fix that and run it again.
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

Review findings left for later. After the merge, orchestrate turns each `open` entry into a queued work item with intake.

| Task | Round | Lens | ID | Finding | Suggested track | Status |
|---|---|---|---|---|---|---|
| T2 | 1 | maintainability | R3 | src/orders/totals.py:40: `_sum_lines` repeats `average_price`'s loop; share one helper. | fix | open |
```

- **Finding:** its location, what is wrong and the proposed fix, on one line; write a literal `|` as `\|`.
- **Suggested track:** `fix` for a change one task can make without touching a shared contract, schema or file format; `feature` otherwise. Triage settles it with the owner later.
- **Status:** `open`. The task loop changes it to `fixed in <task>` when it fixes the finding after all.

Create no work item now: `sw-factory work new` switches the checkout under the running task.

## 6. Record the round

1. `sw-factory work set-status <ID> --stage <stage> --state "<state>" --next "<next>" --note "<task> round <N>: panel <verdict> (reviews/<task>-r<N>-panel.md)"`, with the stage that `sw-factory work status <ID>` shows. Add to the note each lens that ran on its fallback binding (`lens-security ran on its fallback`) and each lens that ran without its required reference skill (`lens-correctness ran without its reference skill tdd`). The state and next step:
   - PASS: `<task> review passed`; next, the caller's step after a passing round (the task loop: the tester, or for a Fix the close; a merge-gate re-review: `the merge gate (merge-gate section 3)`).
   - CHANGES: `<task> changes requested`; next `<task>: write disposition-<task>-r<N>.md, then fix`, ending with `, then the merge gate (merge-gate section 3)` in a merge-gate re-review.
   - INCOMPLETE after the retry: `blocked`, as section 4 says.
2. Commit `<work>/reviews/<task>-r<N>-*.md`, `<work>/follow-ups.md` (if changed) and `progress.md` with the task's trailers (`Factory-Work: <ID>/<task>`, `Factory-Role: implementer` and the implementer's model, as the task-loop skill says). This commit changes only `sw-factory/work/`, so `<head>` stays the commit the merge gate accepts as reviewed.

## 7. Report

Tell the caller, and the owner, the verdict with its evidence: the summary's path, the blocking findings (lens, id, severity, location, finding), the follow-ups recorded, the warnings, any lens that ran on its fallback, and any lens that ran without its required reference skill. Then the caller acts on the verdict.
