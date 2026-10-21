# Version 1 rehearsal results, 2026-10-20

> Names in this record are the pre-rename v1 names; see F16 and F17 in docs/decisions.md for the current names, and F19 for why references to specific BSI products and projects are generalized.

A CLI-level rehearsal of one Fix on a throwaway copy of the sample app, run unattended before the owner-attended v1 demo ([`v1-checklist.md`](v1-checklist.md)). It used the real `factory` command and real review runs through the Codex CLI, but no host session: the plugin was not installed in either host, no host setting was changed, nothing was pushed, and every place that needs the owner's approval got a clearly marked placeholder instead (section 2). The host-driven passes A and B of the checklist are still to be run, by the owner.

## Summary

- **The Fix went through end to end:** setup, triage (`FIX-1`), ticket, a failing test, the fix, the quick verify, a review panel with correctness and security run by Codex, panel combine, retro and `factory gate` with **`GATE: PASS`**. Every `factory` command exited as the skills say it should, with no manual repair and no edit to factory code.
- **Both real lens runs passed in round 1, with no findings:** correctness in 35.7 s and security in 44.0 s, started together in the background (44 s wall clock). They ran with `FACTORY_CODEX_ARGS='-c windows.sandbox="unelevated"'`, because this machine's elevated Codex sandbox is failing; the provenance stamps record that.
- **Not exercised:** the CHANGES path (disposition, round 2, an earlier review as `prior_review_path`), `--fallback`, the tester (a Fix has none), the owner's merge of `FIX-1`, a lessons branch, and everything a host session does (skills, subagents, chat approvals, the parallel start in Codex).
- **Ten findings** (section 7). The serious ones are for pass B, Codex as the host: a Codex session's sandbox cannot write to the factory's staging folder (D1), and setup gives a project with no `AGENTS.md` a factory section only Claude Code reads (D2). D3 is about the PATH advice in the install guide.
- The main flow took **5 min 30 s** of wall clock (21:57:21Z to 22:02:51Z), most of it the two lens runs and the rehearsal's own typing.

## 1. Scope and environment

| | |
|---|---|
| Factory | `E:\BSRI\GitHub\software-factory`, branch `feature/core-v1` at 9930fce (the sample and checklist commit); `factory --version` 0.1.0, run as `.venv\Scripts\factory` |
| Vendor CLIs | `codex-cli 0.163.0` (npm shim `codex.cmd`), Claude Code `2.1.294` |
| Machine | Windows 11, Git Bash, Python 3.14.8, `core.autocrlf=true` |
| Rehearsal repo | `C:\Users\cordc\AppData\Local\Temp\sf-rehearsal-20261020-1757` (outside every repository; not committed anywhere) |
| Raw log | `C:\Users\cordc\AppData\Local\Temp\sf-rehearsal-20261020-1757.log` (each command, exit code, wall time, output) |
| Staging | `C:\Users\cordc\.factory\staging\rangering-rehearsal\FIX-1\` (the rendered lens prompts and the extra review of section 6.3) |
| Project settings | name `rangering-rehearsal` (so its staging folder cannot mix with the demo's), quick `python verify.py --quick`, full `python verify.py`, approver `Rehearsal Owner`, base `main`, `per-item`, `builds: allow`, `mode: review` |
| Codex arguments | `FACTORY_CODEX_ARGS='-c windows.sandbox="unelevated"'` for the lens runs only; the elevated sandbox was not touched or repaired |

## 2. Where the owner would have acted

These are the places a host pass stops for the owner. The rehearsal did not stand in for the owner's judgement; it recorded a placeholder so the commands could run, and says so in every record.

| Step | What the owner does in the demo | What the rehearsal did |
|---|---|---|
| Setup approval (factory-setup section 9) | approves the setup in chat; the commit message quotes them | commit message `Approved by Rehearsal Owner in chat: > REHEARSAL — not an owner approval (CLI rehearsal: owner approval needed here)` |
| Setup merge | merges `factory/setup` into `main` | merged it, as the owner's stand-in, because work-item branches are cut from `main` and need `factory.yaml` there |
| Ticket approval (triage section 5) | approves the ticket in chat; `factory approve` records their words | **owner approval needed here.** `factory approve FIX-1 ticket --rev 1 --quote-file <staging>\quote-ticket-rev1.txt --approver "Rehearsal Owner" --commit 63f18f0… --host claude`, the quote file holding exactly `REHEARSAL — not an owner approval`; the approver is the one configured in the throwaway project; the progress note says `[REHEARSAL placeholder, not an owner approval]` |
| Lessons (factory-run, Deploy and close) | answers each proposed lesson | none proposed; `retro.md` says so |
| Merge checkpoint | runs the merge commands | **not done**: the branch stops at `GATE: PASS`, awaiting a merge nobody will make |

## 3. Step log

Times are wall clock from the raw log. "Key output" is quoted from the tool.

| # | Command (from the rehearsal repo) | Exit | Time | Key output | Files produced |
|---|---|---|---|---|---|
| 1 | `git -C <factory> archive HEAD:examples/sample-app \| tar -x -C <dir>`; `git init -b main`; local `user.name`/`user.email`; commit | 0 | 1.4 s | `e99ed2f Sample app: rangering`; `python verify.py --quick`: `Ran 3 tests … OK`, `verify quick: PASS` | the sample, no `__pycache__` |
| 2.0 | `factory --version` | 0 | 0.4 s | `0.1.0` | |
| 2.1 | `git switch -c factory/setup main` | 0 | 0.2 s | | |
| 2.2 | `factory setup --project rangering-rehearsal --verify-quick "python verify.py --quick" --verify-full "python verify.py" --approver "Rehearsal Owner" --base main --branching per-item --builds allow --mode review` | 0 | 0.4 s | `created`: `factory.yaml`, `REVIEW.md`, `factory/work`, `factory/baseline/expected-failures.md`, `factory/lessons.md`, `CLAUDE.md`; `updated`: `.gitignore`; `conflicts: []` | as listed; `factory.yaml` holds the three values exactly |
| 2.3 | `factory config check --project .` | 0 | 0.4 s | `config ok` | |
| 2.4 | `factory skills check --host claude --project .` | 0 | 0.7 s | nine skills `present`, `skills ok` | |
| 2.5 | `factory skills check --host codex --project .` | 1 | 1.5 s | `missing verification-before-completion (no fallback)`, `missing writing-plans (no fallback)`, `skills not ready` (superpowers is not installed in Codex; known since Task 8) | |
| 2.6 | `factory role resolve interviewer --host claude --project .` | 0 | 0.4 s | `kind: main` | |
| 2.7 | `git commit -F <staging>\setup-commit.txt` (the message written with the file tool, then deleted) | 0 | 0.2 s | `0816855 Set up the software factory` with `Factory-Role: interviewer` and `Factory-Model` trailers | |
| 2.8 | owner stand-in: `git merge --no-ff factory/setup` on `main` | 0 | 0.4 s | `0a6737d Merge factory/setup` | |
| 3.1 | `factory work new --track fix --slug km-label --title "Range ring label shows m for km values"` | 0 | 0.9 s | `"id": "FIX-1"`, branch `factory/FIX-1-km-label`, commit `60c0a50 FIX-1: open work item` | `ticket.md`, `progress.md`, `decisions.md`, `approvals/.gitkeep`, `reviews/.gitkeep` |
| 3.2 | `factory work status FIX-1` | 0 | 0.5 s | `stage: plan`, `state: open`, `next: write ticket.md (problem, repro steps, acceptance criteria)` | |
| 3.3–3.4 | `factory work set-status … --note "Triage: fix, because …"`; commit with interviewer trailers | 0 | 0.6 s | | |
| 3.5 | reproduce: `python -c "… format_range(2500, 'km') …"` | 0 | 0.2 s | `'2.5 m'` (and `'2500 m'`, `'1.3 nmi'`) | |
| 3.6–3.8 | write `ticket.md` (problem, repro steps, AC1–AC4, out of scope); commit; `set-status … "waiting for approval"`; commit | 0 | 1 s | asked commit `63f18f0` | `ticket.md` |
| 3.9 | `factory approve FIX-1 ticket --rev 1 --quote-file … --approver "Rehearsal Owner" --commit 63f18f0… --host claude` | 0 | 0.4 s | path of `approvals/ticket-rev1.md`; the record quotes `> REHEARSAL — not an owner approval` | `approvals/ticket-rev1.md` |
| 3.10 | `set-status --stage build …`; commit record, ticket and progress; delete the quote file | 0 | 0.6 s | `ac92836` | |
| 4.0 | `factory role resolve implementer --host claude --project .` | 0 | 0.4 s | `kind: main` | |
| 4.1 | `set-status … --note "T1 started at ac92836…"`; commit | 0 | 0.6 s | `<start>` = `ac92836cd552fed1cea9953eebc5d251dc688e83` | |
| 4.2 | two km tests added; `python verify.py --quick` | **1** | 0.6 s | `AssertionError: '2.5 m' != '2.5 km'`, `AssertionError: '12.3 m' != '12.3 km'`, `FAILED (failures=2)`, `verify quick: FAIL` (the expected reason) | |
| 4.3 | fix (`… m"` to `… km"` in `rangering/__init__.py`); `python verify.py --quick` | 0 | 0.4 s | `Ran 5 tests … OK`, `verify quick: PASS` | |
| 4.4–4.5 | commit `16b0f69 FIX-1 T1: label km values with km` (`Factory-Work: FIX-1/T1`, `Factory-Role: implementer`, `Factory-Model` from 4.0); `set-status … "T1 under review"`; commit | 0 | 0.8 s | `<head>` = `f213efc24d67be93cc2e0cf1e787699f3792b9ac` | |
| 5.1 | `factory role resolve lens-<lens> --host claude --author claude --project .` (both lenses) | 0 | 0.4 s each | `kind: command`, `vendor: openai`, argv `codex exec -m … -c model_reasoning_effort="xhigh" -s read-only -o <out-file> -` | |
| 5.2 | `factory prompt render E:\…\prompts\lens-<lens>.md --from-review-md REVIEW.md --lens <lens> --set task_id=T1 --set round=1 --set diff_command="git diff <start>..<head> -- . ':(exclude)factory/work'" --set spec_path=factory/work/FIX-1-km-label/ticket.md --set prior_review_path=none --out <staging>\T1-r1-<lens>-prompt.md` | 0 | 0.4 s each | `wrote …` | the two prompts, in staging |
| 5.3 | `factory role run lens-<lens> --host claude --author claude --project . --prompt <staging prompt> --out factory/work/FIX-1-km-label/reviews/T1-r1-<lens>.md`, both started together in the background | 0, 0 | **35.7 s** (correctness), **44.0 s** (security) | `wrote factory\work\FIX-1-km-label\reviews\T1-r1-<lens>.md`; both `VERDICT: PASS`, no findings | the two review files, stamped |
| 6.1 | `factory panel combine factory/work/FIX-1-km-label --task T1 --round 1 --commit f213efc…` | 0 | 1.6 s | `VERDICT: PASS`, every section `None.` | `reviews/T1-r1-panel.md` |
| 6.2–6.4 | `factory work status`; `set-status … "T1 review passed"`; commit the round | 0 | 1 s | `29087ca` | |
| 7.0 | evidence for ticking: the three labels and `python verify.py` | 0 | 0.7 s | `2.5 km \| 2500 m \| 1.3 nmi`; `Ran 5 tests … OK`, `verify full: PASS` | |
| 7.1–7.2 | tick AC1–AC4 in `ticket.md`; `set-status … "T1 done"`; commit | 0 | 0.7 s | `c6e8c8f FIX-1 T1: close` | |
| 7.3–7.5 | `factory role resolve author --host claude`; write `retro.md` (with `## Approved lessons` None); `set-status --stage close --state "merge pending" …`; commit | 0 | 1 s | `0535ae2 FIX-1: retro` | `retro.md` |
| 8.0–8.2 | merge gate sections 1–5: `factory work status`; `git merge-base --is-ancestor main HEAD` (no remote); clean tree; spot check by running the product | 0 | 0.7 s | spot check printed `2.5 km` and `1.0 km` | |
| 8.3 | `factory gate FIX-1 --spot-check "AC1: ran python -c calling format_range(2500, 'km') and format_range(999, 'km') on HEAD; it printed 2.5 km and 1.0 km"` | 0 | 1.9 s | `GATE: PASS` | `merge-gate.md` |
| 8.4–8.5 | `set-status … "merge pending" --next "owner merges …"`; commit `merge-gate.md` with `progress.md` | 0 | 2 s | `283577c FIX-1: merge gate PASS` | |
| 8.6 | `git diff --stat main...HEAD -- . ":(exclude)factory/work"` (the merge checkpoint's "what changed") | 0 | 0.2 s | `rangering/__init__.py \| 2 +-`, `tests/test_format_range.py \| 8 ++++++++` | |

Files on the work branch against the sample commit: 19 files, 291 insertions, 1 deletion; the product change is 2 files, 9 insertions, 1 deletion.

## 4. Panel results

| Round | correctness | security | Panel | Disposition |
|---|---|---|---|---|
| T1 r1 | `VERDICT: PASS`, no findings | `VERDICT: PASS`, no findings | `VERDICT: PASS` (`Commit: f213efc…`; Blocking, Follow-ups, Missing, Invalid, Warnings all `None.`) | none needed |

The provenance lines, as written:

```text
<!-- factory: role=lens-correctness vendor=openai model=gpt-6.1-sol effort=xhigh sandbox=read-only at=2026-10-20T22:00:44Z extra="-c windows.sandbox=\"unelevated\"" -->
<!-- factory: role=lens-security vendor=openai model=gpt-6.1-sol effort=xhigh sandbox=read-only at=2026-10-20T22:00:57Z extra="-c windows.sandbox=\"unelevated\"" -->
```

Codex's own session logs (`~/.codex/sessions/2026/10/20/rollout-2026-10-20T18-00-10-*.jsonl` and `…T18-00-15-*.jsonl`) show that each reviewer ran the rendered `git diff <start>..<head> -- . ':(exclude)factory/work'` under the unelevated read-only sandbox and got the whole diff, then read the changed files, the ticket and `REVIEW.md` with `git show` and `Get-Content`. Neither wrote a file: after both runs `git status` showed only the two new review files.

## 5. The gate report

`factory/work/FIX-1-km-label/merge-gate.md`, as written:

```text
GATE: PASS
- base-merged: ok — HEAD contains main
- reviewed-commit: ok — HEAD is 3 commit(s) after the reviewed commit; later changes are factory bookkeeping only (reviewed f213efc24d67, HEAD 0535ae2d6816); no uncommitted changes in tracked files
- size-cap: ok — 10 lines (cap 400)
- id-unused-on-base: ok — no other factory/work/FIX-1-* folder than FIX-1-km-label on main
- verify-full: ok — $ E:\BSRI\GitHub\software-factory\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
  $ E:\BSRI\GitHub\software-factory\.venv\Scripts\python.exe -m compileall -q rangering tests verify.py
  verify full: PASS
  (the five test lines)
  Ran 5 tests in 0.000s

  OK
- spot-check: ok — AC1: ran python -c calling format_range(2500, 'km') and format_range(999, 'km') on HEAD; it printed 2.5 km and 1.0 km
```

Note the interpreter in `verify-full`: the factory's own virtual environment, not the system Python (D3).

## 6. The checks the rehearsal was asked to make

### 6.1 Can Codex write to `~/.factory/staging`?

- **In the Claude-host flow rehearsed here, it does not need to.** `factory prompt render` (a plain Python process, outside any sandbox) wrote the prompts to `C:\Users\cordc\.factory\staging\rangering-rehearsal\FIX-1\`; `factory role run` read them and sent them to `codex exec` on stdin; Codex wrote its reply through `-o` to `reviews/<file>.md.partial` inside the project, which the factory stamped and renamed. Staging under the home folder worked; nothing had to be staged under the temp folder.
- **A Codex session cannot write there.** A separate probe, `codex exec -s workspace-write -c windows.sandbox="unelevated"` in a throwaway repo (`…\sf-rehearsal-20261020-1757-probe`), was asked to create a file in `C:\Users\cordc\.factory\staging\rangering-rehearsal\probe\` and one in its own folder. The first failed with `Access to the path 'C:\Users\cordc\.factory\staging\rangering-rehearsal\probe' is denied.`; the second worked. On a Codex host every shell command, `factory prompt render --out <staging>` included, runs in that sandbox, and the quote and commit-message files are written with Codex's own file tool. See D1. (The elevated sandbox could not be tried: it is broken on this machine.)

### 6.2 Lens run times

| Run | Vendor CLI | Time | Result |
|---|---|---|---|
| T1 r1 correctness | `codex exec`, unelevated read-only | 35.7 s | PASS, no findings |
| T1 r1 security | `codex exec`, unelevated read-only, started 4 s after correctness | 44.0 s | PASS, no findings |
| Extra: the correctness prompt as a Codex host would run it | `claude -p`, plan mode | 42.4 s | PASS, one nit (below) |

### 6.3 Extra: the Codex-host lens path at CLI level

Not in the rehearsal's step list, but pass B depends on it, so it was run once, outside the work folder: `factory role resolve lens-correctness --host openai --author openai` gives `kind: command` with argv `claude -p --model … --effort xhigh --output-format json --permission-mode plan --allowedTools Read,Grep,Glob`, and `factory role run lens-correctness --host openai --author openai --project . --prompt <the round-1 correctness prompt> --out <staging>\extra-T1-r1-correctness-claude.md` exited 0 in 42.4 s with a valid review:

```text
VERDICT: PASS
…
| R1 | nit | tests/test_format_range.py:22 | `test_rounds_to_one_decimal_place` uses 12345 m (12.345 km), where rounding and truncation both give `12.3 km`. …
<!-- factory: role=lens-correctness vendor=claude model=claude-opus-5-5 effort=xhigh sandbox=read-only at=2026-10-20T22:03:57Z -->
```

Its transcript (`~/.claude/projects/C--Users-cordc-AppData-Local-Temp-sf-rehearsal-20261020-1757/`) shows it ran `git diff`, `git show`, `cat` and `git ls-files` through Bash, although Bash is not in `--allowedTools` (D10). It changed no file.

### 6.4 Where the skills and the CLI did not match

| Skill text | What the CLI or the flow actually did | Finding |
|---|---|---|
| factory-run section 8: every commit carries `Factory-Work`, `Factory-Role`, `Factory-Model` | `factory work new` commits by itself with `Factory-Work: FIX-1` only | D7 |
| factory-run, Deploy and close, step 4: `--state "merge pending" --next "none once the owner has merged <branch>; …"` before the merge gate | the Status line says "merge pending, next: none once the owner has merged" while the gate has not run yet | D4 |
| factory-task-loop section 7: tick every acceptance criterion at the task's close | AC4 names `python verify.py` (the full verify), which nothing in a Fix's task loop runs; the rehearsal ran it by hand first | D5 |
| factory-review-panel section 4: `factory panel combine <work> --task <task> --round <N> --commit <head>` | worked as written (the controller's step list left out `--commit`; the skill's form was used) | none |
| factory-review-panel section 2: `diff_command="git diff <start>..<head> -- . ':(exclude)factory/work'"` | the quoted pathspec ran unchanged in Codex's PowerShell and in `claude -p`'s Bash | none |
| `<vendor>` (`claude`/`openai`) for `factory role …` and `<host>` (`claude`/`codex`) for `factory skills check` | as the skills' tables say; `factory role resolve --host codex` is an error, so the two vocabularies stay a trap for hand-typed commands | observation |

Everything else in the setup, triage, task-loop, review-panel and merge-gate skills ran as written: command names, options, exit codes, the JSON fields read (`resolved.model`, `resolved.effort`, `invocation.kind`), file names (`reviews/T1-r1-<lens>.md`, `T1-r1-panel.md`, `approvals/ticket-rev1.md`, `merge-gate.md`), and the gate's acceptance of bookkeeping-only commits after the review.

## 7. Findings, most serious first

Each is a proposed `FIX-` item for plan 2, or a fix before v1 closes; none was worked around by editing factory code.

| ID | Severity | Finding | Evidence | Proposed fix |
|---|---|---|---|---|
| D1 | major (pass B) | A Codex session cannot write to `<home>/.factory/staging/<project>/<ID>/`, where the skills put rendered prompts (`factory prompt render --out`), tester briefs, approval quote files and the setup commit message. Under `workspace-write` the folder is outside Codex's writable roots. Each such step would fail or need an escalation approval. | Probe in 6.1: `Access to the path … is denied.` | Stage inside a Codex writable root: for example a git-ignored `.factory-staging/` in the project (setup adds it to `.gitignore`), or the system temp folder; or document a `sandbox_workspace_write.writable_roots` entry for `~/.factory` in the install guide. Re-test on a machine whose elevated sandbox works. |
| D2 | moderate (pass B) | In a project with neither `CLAUDE.md` nor `AGENTS.md`, setup creates only `CLAUDE.md` (setup.py, by design in the plan). Codex reads `AGENTS.md`, so a Codex session never sees the factory section: where work lives, how to resume, the verify commands, "reviewers never edit". | 2.2 `created` lists `CLAUDE.md` only | Create both files when neither exists (or create `AGENTS.md` too when setup runs from a Codex host). |
| D3 | moderate | With the factory's `.venv\Scripts` ahead of the system Python on PATH, a project's `python verify.py` runs in the factory's virtual environment. The rehearsal prepended that folder; the install guide says to add it to the user PATH, which on many machines comes after the system Python but not on all. For a project with its own dependencies, verify (and so the gate) would run in the wrong interpreter. | 4.3, 7.0 and `merge-gate.md`: `E:\BSRI\GitHub\software-factory\.venv\Scripts\python.exe` | Install the command with `pipx`, or put only a `factory` shim on PATH; until then the checklist tells the owner to append, not prepend, and to check `where python`. (Task 12 deferred minor, now observed.) |
| D4 | minor | factory-run's Deploy and close step 4 sets `close · merge pending · next: none once the owner has merged …` before the merge gate runs. A session resuming there reads "waiting for the owner's merge" and has no pointer to the gate. | 7.4 in the log | Step 4's next step: "run the merge gate (factory-merge-gate)"; the gate's section 7 then writes the merge-pending line it writes today. |
| D5 | minor | For a Fix, factory-task-loop section 7 ticks every acceptance criterion at the task's close, before the merge gate has run the full verify that a criterion like AC4 names. | ticket AC4; 7.0 | Tick only criteria with evidence in hand: run `verify.full` first when a Fix criterion names it, or leave that criterion to the gate. |
| D6 | minor | `Factory-Model` trailers (and the setup commit) record the role table's binding from `factory role resolve`, not the model the session is actually running; nothing checks they agree. In this rehearsal the interviewer and author commits name the bindings while the session ran another model. | `git log` of the rehearsal repo | Say in the skills that the trailer is the binding, and tell the owner when the session's model differs; or record both. |
| D7 | minor | `factory work new`'s own commit has only `Factory-Work`, against factory-run section 8's "trailers on every commit". | `60c0a50 FIX-1: open work item` | Let `work new` take `--role`/`--model` (or amend the skill's rule to exempt it). |
| D8 | minor | On success `factory role run` prints only `wrote <path>`. Whether the reviewer could run its commands at all shows only in the vendor's own session logs; a `PASS` with no findings from a reviewer that could not read the diff would look the same as this round's. | 4: the check needed `~/.codex/sessions` | Stamp the vendor session id (or the rollout path) into the provenance line, or have the lens prompts quote the diff's `--stat` in the review. |
| D9 | observation | In the unelevated Codex sandbox every PowerShell command's output starts with `Microsoft.PowerShell_profile.ps1: Cannot dot-source this command because it was defined in a different language mode.` (the owner's profile under constrained language mode). Harmless here; noise in every reviewer command. | Codex session logs | Mention it in the install guide's troubleshooting, so nobody takes it for a failed review. |
| D10 | observation | `claude -p --permission-mode plan --allowedTools Read,Grep,Glob` still ran Bash (`git diff`, `git show`, `cat`): `--allowedTools` pre-approves tools rather than limiting them, and plan mode let read-only shell commands through. Reviews need `git diff`, so this is what the lens needs, but the read-only guarantee rests on plan mode, not on the tool list the adapter names. Also, `·` in `ticket.md` reached that reviewer as a replacement character through Bash. | 6.3 transcript | Document that plan mode is the guard (or pass `--disallowedTools` for writing tools explicitly); consider `git diff` output encoding on Windows. |

Also seen, not counted: git's `LF will be replaced by CRLF` warning on every commit (this machine has `core.autocrlf=true`; the files are stored with LF), and the date-only first log line of `progress.md` written by `work new` (Task 4 deferred minor).

## 8. Not covered by this rehearsal

- Everything that needs a host: installing the plugin, the skills being picked up and followed by an agent, the setup interview, triage questions, chat approvals and their exact words, subagents, and the Codex-host parallel start of the lens runs (background jobs in one shell command, under Codex's command time limit). These are passes A and B of the checklist.
- The CHANGES path: a disposition file, fixes, round 2 with an earlier review as `prior_review_path`, the `third-failed-round` hard stop. Round 1 passed, and a CHANGES verdict was not forced.
- `--fallback` (no run failed), the elevated Codex sandbox (broken on this machine; not repaired), pushes (no remote), the owner's merge of `FIX-1`, a lessons branch, follow-ups.
- The tester and the maintainability lens (a Fix has neither; the checklist's optional pass C covers them).

## 9. What the owner should do with this

1. Read D1 to D3 before pass B: D1 will surface as Codex permission prompts or failures when the factory writes to `~/.factory/staging`, and D2 means a Codex session will not see the factory section unless the owner points it at `CLAUDE.md`. The checklist says what to record.
2. Before pass A, check `where python` lists the system Python before the factory's `.venv` (D3).
3. Decide which findings are fixed before v1 closes and which become `FIX-` items for plan 2.

The rehearsal repo, its log and the staging folder are left in place for inspection; delete them when done (`C:\Users\cordc\AppData\Local\Temp\sf-rehearsal-20261020-1757*`, `C:\Users\cordc\.factory\staging\rangering-rehearsal`).

## 10. After the final review

The final whole-branch review asked for these findings to be fixed before `feature/core-v1` merges. Commits `4b343e2` to `93284e9`.

| ID | What changed |
|---|---|
| D1 | Documented, not moved: the install guide (step 3) and checklist step 0.4 tell the owner to add `~/.factory` to `writable_roots` under `[sandbox_workspace_write]` in `~/.codex/config.toml` (or start Codex with `--add-dir`), and Troubleshooting names the `Access to the path … is denied.` symptom. Moving the staging folder stays a plan-2 item (design section 9.8). The key is the one Codex CLI 0.163.0 knows, but it has not been tried in a Codex session yet. |
| D2 | Fixed: setup creates both `CLAUDE.md` and `AGENTS.md`, with the same section, when the project has neither. |
| D3 | Documented: the install guide recommends `pipx install -e <clone>/tools`, or putting only the `factory` entry point on the PATH, and checks with `where python` that the project's Python comes first. |
| D4 | Fixed: factory-run's Deploy and close step 4 sets `retro written`, next `run the merge gate (factory-merge-gate)`; the gate writes `merge pending` once it passes. |
| D10 | Documented in the adapter: `--allowedTools` pre-approves tools and does not restrict them; plan mode is the read-only guard; `--disallowedTools` and `--max-turns` are plan-2 items. |

Also fixed: `FACTORY_CODEX_ARGS` can no longer carry options that would replace a run's sandbox, approvals, output file or working folder (`-c key=value` stays allowed); the skills use the plugin-qualified subagent name (`software-factory:<name>`) when the bare one is not available; and the timeout test now proves the vendor CLI's process tree is killed instead of timing the run (10 of 10 runs passed).

### 10.1 Linux check (WSL)

The plan was one test run on Linux: copy the repository (without `.venv` and `.superpowers`) into the WSL file system, make a virtual environment with `python3 -m venv`, `pip install -e "tools[dev]"`, and `python -m pytest tools/tests -q`. **It could not run.** On 2026-10-20 at 22:45Z, in WSL 2 `Ubuntu-24.04` (Ubuntu 24.04.5 LTS, kernel 6.18.33.2-microsoft-standard-WSL2, Python 3.12.3), the first step after the environment check failed:

```text
$ python3 -m venv /tmp/sf-venv-probe
The virtual environment was not created successfully because ensurepip is not
available.  On Debian/Ubuntu systems, you need to install the python3-venv
package using the following command.

    apt install python3.12-venv
```

The distribution also has no pip (`/usr/bin/python3: No module named pip`) and no pytest; it has PyYAML 6.0.1 from `python3-yaml`. Installing `python3.12-venv` needs `sudo apt`, an installer run with administrator rights, which is the owner's to do, so the check stopped there: nothing was copied into WSL and no test ran. The POSIX code paths (process groups and `SIGKILL` in the adapter, shebang shims in the test fakes, permission bits in setup) remain untested on Linux.

To run it, the owner installs the package once (`sudo apt install python3.12-venv`), then, inside WSL:

```bash
rm -rf ~/sf-check && mkdir -p ~/sf-check
tar -C /mnt/e/BSRI/GitHub/software-factory --exclude=./.venv --exclude=./.superpowers --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='*.egg-info' -cf - . | tar -C ~/sf-check -xf -
cd ~/sf-check && python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e "tools[dev]" && python -m pytest tools/tests -q
```

Otherwise the check waits for CI in plan 2.

**Update, 2026-10-20 (after the owner installed `python3.12-venv` and pip in WSL):** the check ran on a fresh clone of `main` at `90be131` in the WSL file system (`~/sf-check`), with a `python3 -m venv` virtual environment and `pip install -e "tools[dev]"`:

```text
Linux 6.18.33.2-microsoft-standard-WSL2 · Python 3.12.3 · git 2.43.0
factory --version → 0.1.0
1054 passed, 12 skipped in 43.89s
```

The 12 skips are the Windows-only tests of the `.cmd` batch-shim refusal (`test_adapters.py`); the POSIX code paths — process groups and `SIGKILL` in the adapter, the shebang shims in the test fakes, and setup's permission bits — now run and pass on Linux. CI on Linux remains a plan-2 item so this stays checked on every change.
