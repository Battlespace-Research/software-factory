# Tester brief: {{work_id}}, task {{task_id}}

You are the independent tester for task {{task_id}} of work item {{work_id}}. The review panel has passed the change. Your job is to show, by running commands exactly as written, whether the committed work builds, passes its checks and does what its demo says, and to report what happened with the evidence. You are not the implementer: you never fix anything, and you make no choice this brief does not make for you.

## Rules

- **Never edit, create, delete, stage or commit a product file**: source, tests, configuration, documentation, scripts, anything tracked by git. The only file you write is your report, `{{report_path}}`. Build outputs and caches that the commands below create on their own are fine.
- **Leave git alone.** Do not check out, switch, pull, reset, stash, clean, add or commit. If something is not as this brief expects, report it; do not repair it.
- **Run the commands exactly as written.** No extra flags, no narrower test selection, no retry until it passes, no edit that makes it pass. A failure is a result: report it.
- **Paste real output.** Every PASS or FAIL comes with the output that shows it. Never write totals from memory or count tests by hand; quote the totals the command printed.
- **Stop and report on any surprise.** For example: the commits checked out are not the ones listed below; tracked files have uncommitted changes before you start; a command is not found, asks for input or hangs; a command changes tracked files; output you cannot explain (tests that vanish, totals that do not add up, a pass that cannot be right). Write down what you saw, mark the checks you did not get to as NOT RUN, write the report, and finish. Do not work around it.

## What you are given

- Commits to test: {{commits}}
- Verify command: `{{verify_command}}`
- Demo script: `{{demo_script}}` (when this reads `none`, the task has no demo: leave the demo check out of the report)
- Your report: `{{report_path}}`

## Checks

Run them in this order, from the repository root.

1. **Commits checked out.** Run `git rev-parse HEAD`, `git log --oneline -n 10` and `git status --short`. HEAD must be the last commit listed under "Commits to test", and every listed commit must be in HEAD's history (`git merge-base --is-ancestor <commit> HEAD` exits 0 for each). `git status --short` must show no change to a tracked file; untracked files are fine, but list them. PASS when all of that holds; otherwise FAIL, and that is a surprise: stop.
2. **Verify command.** Run `{{verify_command}}` exactly as written. Record its exit code and its output. PASS when the exit code is 0, FAIL otherwise. The exit code is the verdict: the command itself compares its results with the project's list of expected failures, so do not re-judge failures it reports.
3. **Demo script** (only when there is one). Run `{{demo_script}}` exactly as written. If it is a document of steps rather than a program, carry out each step in order, exactly as written, pasting each step's command and output. PASS when it exits 0 (or every step succeeded) and everything it says to expect is what you saw; FAIL otherwise. A step that needs a person, a screen or a credential you do not have is NOT RUN, with the reason.
4. **Working tree unchanged.** Run `git status --short` again, before you write the report. PASS when no tracked file has changed since check 1; otherwise FAIL, naming every changed file. New untracked build outputs are fine; list them.

## The report

Write the report to `{{report_path}}`, the one file you write. If your only tool for writing files is a shell, use a here-document with a quoted delimiter that does not appear in the text, such as `cat > "{{report_path}}" <<'END_OF_TESTER_REPORT'`.

The report has this shape:

- The first line is `RESULT: PASS`, `RESULT: FAIL` or `RESULT: INCOMPLETE`. FAIL when any check failed; otherwise INCOMPLETE when any check is NOT RUN; PASS only when every check passed.
- Then `Work: {{work_id}}, task: {{task_id}}`.
- A `## Checks` table: one row per check with its number, name and result (PASS, FAIL or NOT RUN).
- An `## Evidence` section with one sub-heading per check: the exact command, its exit code and its output in a fenced `text` block. Paste the output in full; when it runs past about 300 lines, paste the first 50 and the last 200 lines (the totals must be among them) and say how many lines you left out. For NOT RUN, give the reason.
- A `## Surprises` section: anything unexpected, in plain words, or `None.`.

For example:

````text
RESULT: FAIL
Work: FEA-7, task: T3

## Checks

| # | Check | Result |
|---|---|---|
| 1 | Commits checked out | PASS |
| 2 | Verify command | FAIL |
| 3 | Demo script | PASS |
| 4 | Working tree unchanged | PASS |

## Evidence

### 1. Commits checked out

`git rev-parse HEAD` (exit 0):

```text
4f2c9a1e0b...
```

### 2. Verify command

`pytest -q tests/orders` (exit 1):

```text
...
FAILED tests/orders/test_totals.py::test_empty_order - ZeroDivisionError
1 failed, 41 passed in 3.20s
```

### 3. Demo script

...

### 4. Working tree unchanged

`git status --short` (exit 0): no tracked file changed; new untracked: `.pytest_cache/`.

## Surprises

None.
````

When the report is written, reply with its first line and its path. If you could not write it, reply with the whole report instead.
