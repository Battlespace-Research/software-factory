---
name: factory-task-loop
description: Runs one task of a software factory work item from its first failing test to its close. Implements it test-first, runs the quick verify, takes it through the review panel and its fix rounds with a written disposition of every finding, has the independent tester check the committed result, then ticks the task, commits and pushes. Use when factory-run hands over a task (T1, M1-T2, ...) or the owner asks to continue a task in progress.
---

# Factory task loop

One task through the loop: implement test-first → verify → review panel → on CHANGES, disposition and fixes, then another round → independent tester → close. The files are the state: every step ends with a commit and a Status line that names the next step, so any session, on either host, can pick the task up where the last one stopped.

Uses skills: tdd, diagnosing-bugs, verification-before-completion

## Names used below

Names such as `<ID>`, `<work>`, `<branch>`, `<vendor>`, `<factory>` and `<staging>` mean what the table at the top of the factory-run skill (`../factory-run/SKILL.md`) says. Also:

| Name | Meaning |
|---|---|
| `<task>` | the task id: `T1` in a Fix or Feature, `M1-T2` in a Program |
| `<N>` | the review round, from 1 |
| `<start>` | the full SHA of HEAD when the task started, recorded in `progress.md` |
| `<head>` | the full SHA of the commit a review round reviews |
| `<try>` | the tester's run for this task, from 1 |

Run every command from the repository root. From `factory.yaml` you need `verify.quick`, `verify.full`, `builds` and `review.max_rounds` (3 when it is not set).

**Commits.** Section 8 of factory-run applies, with the task in the trailer: `git add -- <paths>`, then `git commit -m "<ID> <task>: <what>" --trailer "Factory-Work: <ID>/<task>" --trailer "Factory-Role: implementer" --trailer "Factory-Model: <model> (<effort>)"`, the model and effort from `factory role resolve implementer --host <vendor> --project .`. Each step runs `factory work set-status <ID> --stage build --state "<state>" --next "<the exact next action>" --note "<what was done>"` and commits `progress.md` with the step. The hard stops (factory-run section 6) and the decision ladder (section 7) hold throughout.

## 1. Start or resume

1. **Read the task:** its section of `<work>/plan.md` (the requirement ids it meets, the test that comes first, what done means), or for a Fix `<work>/ticket.md` (repro steps, acceptance criteria); and `spec.md` and `decisions.md` where they exist.
2. **Resume** when `progress.md` has a log entry `<task> started at <sha>`: that SHA is `<start>`. `factory work status <ID>` names the next step, and the files confirm it: the highest round with a panel summary `<work>/reviews/<task>-r<N>-panel.md` and its first line, that round's disposition file, and the tester reports `<work>/reviews/<task>-tester-<try>.md`. Go to that step.
3. **Start** otherwise: with a clean tree (`git status --porcelain --untracked-files=no` prints nothing; if it lists files, commit your own work or ask the owner, and never stash or discard), take `<start>` from `git rev-parse HEAD`, run `factory work set-status <ID> --stage build --state "<task> in progress" --next "<task>: write the failing test" --note "<task> started at <start>"`, and commit it.

## 2. Implement, test first

Follow the tdd skill.

1. Write the test the task says comes first (for a Fix, from the repro steps). Run it and see it fail for the reason the task expects; keep the output.
2. Write the smallest change that makes it pass, and run it again to see it pass. Repeat for each behaviour the task owns, each error path included.
3. When anything fails in a way you did not expect, use the diagnosing-bugs skill: find the cause before you change code.
4. Stay inside the task. Work it does not name, a split, or a change to what gets built is an amendment to the plan: follow "Changes after approval" in `../factory-run/references/checkpoints.md` before you build it. A split task keeps its id with a letter (`T2a`).
5. Commit at each passing step.

## 3. Verify

1. Run `verify.quick` exactly as `factory.yaml` writes it. It must exit 0. On a failure, find and fix the cause (diagnosing-bugs) and run it again; never narrow the command, skip a test or call a failure flaky to get past it.
2. Paste its output in your reply, at least the totals it printed, as the verification-before-completion skill says. Never write totals from memory.
3. Commit everything, so the tracked tree is clean. Run `factory work set-status <ID> --stage build --state "<task> under review" --next "<task>: review round <N> (factory-review-panel)" --note "<task>: verify.quick passed: <the totals it printed>"`, commit it, and take `<head>` from `git rev-parse HEAD`. Nothing is committed again until the round is recorded, so on a resume `<head>` is still HEAD.

## 4. Review round `<N>`

Hand the round to the factory-review-panel skill with `<ID>`, `<task>`, `<N>`, `<start>`, `<head>` and `<author>`: your own `<vendor>`, unless the task's latest code commit was made on the other host (its `Factory-Model` trailer names a model that `factory role resolve implementer --host <the other vendor> --project .` prints, with or without `--fallback`); then that vendor. **Change nothing in the working tree while the lenses run**: they read it. The panel skill commits the round and reports its verdict.

- **PASS:** section 6. A Fix goes straight to section 7: its track is tested by the verify command alone (factory-run section 2), and the merge gate runs the full one.
- **CHANGES** with `<N>` below `review.max_rounds`: section 5.
- **CHANGES** in round `review.max_rounds`: write and commit that round's disposition (section 5, steps 1 and 2) but fix nothing. That is the `third-failed-round` hard stop (`../factory-run/references/hard-stops.md`): show the owner every round's findings and dispositions; the owner decides.
- **Blocked** (a lens gave no readable review twice): the panel skill has stopped and asked the owner; wait for the answer.

Never start a round numbered above `review.max_rounds`. If one is needed for another reason (a tester FAIL after the last allowed round, or a base merge at the merge gate), stop and ask the owner as at the `third-failed-round` hard stop.

## 5. Disposition and fixes (CHANGES)

1. Read the panel summary `<work>/reviews/<task>-r<N>-panel.md` and each lens review of the round, `<work>/reviews/<task>-r<N>-<lens>.md`.
2. Answer every finding of every review in `<work>/reviews/disposition-<task>-r<N>.md`:

   ```markdown
   # Disposition: <ID> <task>, round <N>

   | # | Finding | Severity | Disposition | Reason |
   |---|---|---|---|---|
   | correctness R2 | Empty order divides by zero in `average_price` | major | accepted | Returns 0 for no lines (src/orders/totals.py:57); test_empty_order covers it |
   ```

   - **#** is the lens and the finding's id. **Disposition** is `accepted` (fixed as proposed), `modified` (fixed another way: say how and why) or `declined` (left as it is: say why the finding does not hold, with evidence such as `path:line`, a test or a `decisions.md` row).
   - **Re-check each finding against the code before deciding:** open its location, follow the path it describes, and reproduce it with a test where you can. Reviewers can be wrong, and so can you. Decline a blocker or a major only with evidence the next round can check.
   - A maintainability finding you fix now is `accepted`, and its entry in `<work>/follow-ups.md` gets the status `fixed in <task>`. One you leave is `declined`, with the reason "left as a follow-up".
   - A minor finding of another lens that you decline because it lies outside the task goes into `<work>/follow-ups.md`, in the format the factory-review-panel skill gives.
3. Fix the `accepted` and `modified` findings test-first (section 2): a finding about behaviour gets a test that fails before the fix.
4. Run section 3 for round `<N+1>` (it commits the disposition with the fixes), then section 4 for that round. Each lens gets its own round-`<N>` review as its earlier review, and `<start>` stays the same.

## 6. The independent tester (PASS; Feature and Program)

1. If `builds` is `ask`, ask the owner before the tester runs the full verify command, and wait for a yes; on any other answer, ask the owner how the task should be tested.
2. Run `factory work set-status <ID> --stage build --state "<task> tester running" --next "<task>: read the tester's report reviews/<task>-tester-<try>.md" --note "<task>: tester try <try> started"` and commit it. The tracked tree is now clean, and HEAD is the commit to test.
3. **Render the brief:** `factory prompt render <factory>/prompts/tester-brief.md --set work_id=<ID> --set task_id=<task> --set commits="<commits>" --set verify_command="<verify.full>" --set demo_script=<demo> --set report_path=<work>/reviews/<task>-tester-<try>.md --out <staging>/<task>-tester-brief-<try>.md`
   - `<commits>`: the full SHAs that `git rev-list --reverse --first-parent <start>..HEAD` prints, joined by single spaces: oldest first, HEAD last.
   - `<verify.full>`: the full verify command, quoted for your shell. Then open the brief and check that its "Verify command" line shows the command exactly as `factory.yaml` writes it; if your shell changed it (quotes, `$`, `%`), correct that line with your file-writing tool.
   - `<demo>`: the demo script the plan's task names, if it names one; otherwise `none`.
4. **Start the tester** as `factory role resolve tester --host <vendor> --project .` says:
   - `subagent`: spawn `factory-tester` (`invocation.agent`) with model `invocation.model_alias`, giving it the brief's text as its task.
   - `command`: `factory role run tester --host <vendor> --project . --prompt <staging>/<task>-tester-brief-<try>.md --out <staging>/<task>-tester-reply-<try>.md`, in the background if your host limits how long a command may run. If it exits 1, run it once more with `--fallback` and say so in the next log entry; if that fails too, stop and tell the owner.
5. **Edit nothing in the working tree while the tester runs:** no file edits, no `factory work set-status`, no git command that writes. A fix you think of meanwhile goes into a patch file under `<staging>` (never in the project or the session scratchpad), applied only after step 6.
6. **When the tester has finished:**
   - `git status --porcelain --untracked-files=no` must print nothing (the report is new and untracked, so it does not show), and `git rev-parse HEAD` must still be the last of `<commits>`. Anything else means the tester changed the product or git: stop, discard nothing, set the state `blocked` with the next step `owner decides: the tester changed <what>`, and tell the owner.
   - The report's first line is `RESULT: PASS`, `RESULT: FAIL` or `RESULT: INCOMPLETE`. A tester that could not write the report replies with all of it: save that reply, unchanged, at the report path with your file-writing tool. With no report at all, run the tester once more (the next `<try>`); still none: stop and tell the owner.
   - Commit the report whatever it says, with `Factory-Role: tester` and the tester's `resolved.model` and `resolved.effort` in place of the implementer's.
7. **Act on the result:**
   - **PASS:** section 7.
   - **FAIL:** the change has a defect the panel missed. Find its cause (diagnosing-bugs), fix it test-first, run section 3 and a new review round (section 4), then the tester again with the next `<try>`.
   - **INCOMPLETE:** show the owner what was not run and why, and ask how to go on.
   - A patch staged in step 5 is a code change: apply it now and run sections 3, 4 and 6 again; or, if the task does not need it, add it to `<work>/follow-ups.md` and delete the patch.

## 7. Close the task

1. **Tick it:** in `plan.md`, every `- [ ]` under the task's heading becomes `- [x]`; a task without boxes gets the line `- [x] Done` under its heading. For a Fix, tick each acceptance criterion in `ticket.md` the same way (a criterion without a box gets `- [x] ` in front of it).
2. `factory work set-status <ID> --stage build --state "<task> done" --next "<next>" --note "<task> closed: panel PASS in round <N>; tester PASS (reviews/<task>-tester-<try>.md)"` (a Fix: `no tester (Fix)` in place of the tester part). `<next>` is the next open task in plan order (`<next task>: start (factory-task-loop)`); after the last task of a Program milestone, that milestone's demo; after the item's last task, `write retro.md (factory-run, Deploy and close)`.
3. Commit `plan.md` (or `ticket.md`) and `progress.md`, and push `<branch>` under the push rule (factory-run section 8).
4. Hand back to factory-run, which re-reads the Status line and goes on.
