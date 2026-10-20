---
name: factory-merge-gate
description: The software factory's last check before the owner merges a work item, or a Program milestone, into the base branch. Brings the work branch up to date with the base (re-reviewing when that changed it), commits everything, spot-checks one acceptance criterion, runs factory gate, and hands the owner a summary with the merge command; the owner merges. Use when factory-run reaches the merge, or the owner asks whether a work branch is ready to merge.
---

# Factory merge gate

Checks that what the owner is about to merge is what was reviewed and tested, that it is up to date with the base branch and small enough to review, that the full verify passes, and that one acceptance criterion holds when checked by hand. `factory gate` runs six checks and writes `<work>/merge-gate.md`. You prepare the branch for it, give it the spot-check, and report. **The owner merges; you never merge into or push to the base branch** (the `main-branch` hard stop), in auto mode too.

Uses skills: verification-before-completion

## Names used below

Names such as `<ID>`, `<work>`, `<branch>` and `<vendor>` mean what the table at the top of the factory-run skill (`../factory-run/SKILL.md`) says. Also:

| Name | Meaning |
|---|---|
| `<base>` | `base_branch` in `factory.yaml` |
| `<base ref>` | `origin/<base>` when that ref exists, otherwise the local `<base>`: the ref the gate compares with |
| `<last task>` | the last task in plan order that this merge carries (a Fix's `T1`; for a Program milestone, its last task) |

Run every command from the repository root. Commits follow factory-run section 8, as the implementer: `Factory-Work: <ID>`, `Factory-Role: implementer`, and the model and effort from `factory role resolve implementer --host <vendor> --project .`; a review round's commits name its task instead (`Factory-Work: <ID>/<task>`).

## 1. Check the work is ready

1. You are on `<branch>`. Read `factory work status <ID>` and note its `stage` and `next`. If `next` contains `then the merge gate`, a re-review (section 2, step 4) was interrupted: finish it first with the factory-task-loop skill, which resumes it and sends a PASS back here, then start this section again.
2. Every task this merge carries is ticked in `plan.md` (a Fix: the criteria in `ticket.md`), and each has a passing panel summary (`<work>/reviews/<task>-r<N>-panel.md`, first line `VERDICT: PASS`) and, except in a Fix, a tester report `<work>/reviews/<task>-tester-<try>.md` that starts `RESULT: PASS`. For a Program milestone, its demo checkpoint is passed (approved, or noted in auto mode). If anything is missing, go back to factory-run.

## 2. Bring the branch up to date with the base

This comes before the final review round, because merging the base after the review has passed changes the commit the review covered.

1. If `git remote` lists `origin`, run `git fetch origin`. The gate reads `<base ref>` without fetching, so it sees what you fetched now.
2. `git merge-base --is-ancestor <base ref> HEAD` exits 0 when the branch already contains the base: go to section 3.
3. Otherwise merge it in: `git merge --no-commit <base ref>` (a merge, never a rebase). Resolve any conflict keeping the intent of both sides; a resolution that needs a decision outside the work's scope goes to the owner. Run `verify.quick` from `factory.yaml` and paste its totals, then commit the merge with the factory trailers: `git commit -m "<ID>: merge <base ref>" --trailer "Factory-Work: <ID>" --trailer "Factory-Role: implementer" --trailer "Factory-Model: <model> (<effort>)"`.
4. The merge changed files outside `factory/work/`, so the last passing review no longer covers HEAD (the gate's `reviewed-commit` check would fail). Run one more review round of `<last task>`, with `<N>` one above that task's highest round, so the reviewers see the whole item's change on top of the updated base:
   1. **Record the re-review's start.** `<base sha>` is the full SHA that `git rev-parse <base ref>` prints. Run `factory work set-status <ID> --stage <the stage from section 1> --state "<last task> re-review" --next "<last task>: review round <N> (factory-review-panel), then the merge gate (factory-merge-gate section 3)" --note "<last task> started at <base sha> (merge-gate re-review)"` and commit it. That log entry tells the factory-task-loop skill, and any session that resumes the round, that it starts at `<base sha>` and belongs to the merge gate.
   2. **Run the round:** sections 3 and 4 of the factory-task-loop skill for `<last task>`, round `<N>`, with `<start>` = `<base sha>`. On CHANGES, answer and fix as its section 5 says and run the next round; the round limit and the `third-failed-round` hard stop apply. On PASS, go on to section 3 here: the tester does not run again, because the gate runs the full verify itself.

## 3. Commit everything

`git status --porcelain --untracked-files=no` must print nothing: the full verify runs on the working tree, and the gate fails on any uncommitted tracked change. Commit what is left, such as `progress.md` after `factory work set-status`, or a `merge-gate.md` from an earlier run of the gate.

## 4. Ask before the full build

If `builds` in `factory.yaml` is `ask`, ask the owner: "May I run the full verify command `<verify.full>` for the merge gate?" On an unconditional yes, add `--builds-approved` to the gate (section 6) and put the owner's answer in the next log note. On any other answer, ask the owner how to go on: pass `--no-verify` only when the owner tells you to in this chat (for example because they run the full build themselves), and the summary then says the full verify was skipped, and why. Never pass `--no-verify` on your own.

## 5. Spot-check one acceptance criterion

1. Pick one acceptance criterion this merge delivers: from `ticket.md` for a Fix, otherwise from the acceptance checks in `spec.md`. Prefer one a user would notice and that the tests cover least directly.
2. Check it yourself on the code at HEAD: run the product, the command or the screen it describes, and see the result. A test run alone is not a spot check.
3. Leave the tracked tree as it was: `git status --porcelain --untracked-files=no` still prints nothing afterwards.
4. Write the result as one line, `<criterion id or name>: <what you ran and what you saw>`, free of double quotes, `$`, backticks and `%`. If the criterion does not hold, do not run the gate: it is a defect, which goes back through the task loop as a new task in `plan.md` (an amendment).

## 6. Run the gate

`factory gate <ID> --spot-check "<criterion: evidence>"`, adding `--builds-approved` when section 4 says so. Write `--spot-check="<criterion: evidence>"` if the text could start with a dash. It runs the full verify command, which can take long: run it in the background if your host limits how long a command may run.

It prints the path of `<work>/merge-gate.md` and `GATE: PASS` or `GATE: FAIL`, and exits 0 (PASS), 1 (FAIL) or 2 (it could not run: the reason is on stderr; fix that and run it again). An exit 1 that says no passing panel summary was found means the review has not passed: go back to the task loop. Read `merge-gate.md`: one line per check (`base-merged`, `reviewed-commit`, `size-cap`, `id-unused-on-base`, `verify-full`, `spot-check`), `ok` or `FAIL`, with its detail.

## 7. Record the result

1. On PASS: `factory work set-status <ID> --stage <the stage from section 1> --state "merge pending" --next "<next>" --note "Merge gate PASS (merge-gate.md)"`. `<next>` is `owner merges <branch> into <base>`, followed by what comes after the merge: after the item's last merge, `then apply the approved lessons and queue the follow-ups (factory-run, Deploy and close, steps 6 and 7)`; after a Program milestone, `then bring <branch> up to date with <base> and start M<n+1>`.
2. On FAIL: the same command with `--state "gate failed"`, the next step `fix <the failed checks>, then run the merge gate again` and the note `Merge gate FAIL: <the failed checks>`.
3. Commit `merge-gate.md` with `progress.md`, whatever the result, and push `<branch>` under the push rule (factory-run section 8).

## 8. PASS: hand the merge to the owner

This is the **merge** checkpoint. Give the owner, with evidence rather than a summary of it (the verification-before-completion skill):

- **What changed:** `git diff --stat <base ref>...HEAD -- . ":(exclude)factory/work"`, and one line per task saying what it delivered.
- **Panel results:** for each task, its rounds and verdicts (`reviews/*-panel.md`), and every finding that was declined, with its reason (`reviews/disposition-*.md`).
- **Verify output:** the `verify-full` lines of `merge-gate.md`, pasted, the first line of each tester report, and the spot check.
- **Risks:** declined blocker or major findings, choices settled by the decision ladder (their `decisions.md` rows, applied in commits with a `Factory-Decision` trailer), roles that ran on a fallback binding, and anything skipped (such as `--no-verify`).
- **Follow-ups:** the `open` entries of `<work>/follow-ups.md`.
- **The merge command**, for the owner to run (leave out the `pull` and `push` lines when there is no remote; if the project merges through pull requests, open one from `<branch>` to `<base>` instead):

  ```text
  git switch <base>
  git pull --ff-only
  git merge --no-ff <branch>
  git push origin <base>
  ```

Then wait. When the owner says it is merged, check it: `git fetch origin` (if there is a remote), then `git merge-base --is-ancestor <branch> <base ref>` exits 0. Hand back to factory-run.

## 9. FAIL: report and propose

Show the owner each failed check, with its detail from `merge-gate.md`, and what you propose. Never merge, and never weaken a check to make it pass.

- **`base-merged`:** the base moved again; go back to section 2.
- **`reviewed-commit`:** commit the uncommitted changes it names (section 3); if product files changed after the review, run another review round (section 2, step 4).
- **`size-cap`:** the change is bigger than `merge.size_cap` lines (the work folder and `merge.generated` files do not count). Propose a split: list the changed lines per file with `git diff --numstat <base ref>...HEAD -- . ":(exclude)factory/work"`, then group the files by task or module into parts that each stay under the cap and build, pass the verify and make sense on their own; say which tasks go in each part and in what order the parts merge. For a Feature, that usually means stepping up to a Program with one milestone per part (the factory-triage skill, "Stepping up"). Splitting changes the plan and the scope, so the owner decides.
- **`id-unused-on-base`:** another work item on the base has this id. Its detail names the next free id; renaming the item is the owner's decision.
- **`verify-full`:** "ask the owner before the full build" means section 4 was skipped. Otherwise it is a defect: find the cause and fix it through the task loop as a new task in `plan.md` (an amendment), then run the gate again.
- **`spot-check`:** the text was empty; do section 5.
