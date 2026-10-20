# Version 1 acceptance checklist

Version 1 is accepted when one Fix has gone from triage to an owner's merge on a throwaway copy of the sample project, once with **Claude Code** as the host and once with **Codex** as the host, and each run left the artifacts listed under "Expected artifacts", with the review panel run by the other vendor each time.

The sample is `examples/sample-app/` in this repository: a tiny Python package, `rangering`, whose `format_range(meters, unit)` labels a range ring in `m`, `km` or `nmi`. Its tests cover `m` only, and it has one planted bug: a value shown in `km` is labelled `m` (`format_range(2500, "km")` returns `2.5 m`, not `2.5 km`). Never fix the bug in this repository; every run works on a copy.

The owner runs this checklist. The factory's maintainers ran a CLI-level rehearsal of the same Fix first (the `factory` commands and real lens runs, without a host session); its record is [`2026-10-20-v1-rehearsal-results.md`](2026-10-20-v1-rehearsal-results.md). Read its defects before you start: they say where the host passes may need help.

Write what happened in `docs/acceptance/<date>-v1-results.md`, using the record table at the end of each pass. Any defect becomes a `FIX-` work item for plan 2, or a fix before v1 closes.

## 0. Before both passes (once)

These change your machine's host configuration, so they are yours to do.

1. **The `factory` command.** `factory --version` prints `0.1.0` in a new terminal, and in a session of each host when you ask the agent to run it ([install guide](../guide/install.md), step 1). If it does not, install the command as step 1 says, with `pipx install -e <factory clone>/tools` or by putting only `factory.exe` on the PATH, never the factory's `.venv\Scripts` folder, and restart both hosts. Then check that the first `python` that `where python` lists is your own, not one inside the factory's clone or pipx's folders: otherwise the sample's `python verify.py` runs in the factory's virtual environment (rehearsal finding D3).
2. **The plugin, from this checkout.** Until `feature/core-v1` is merged into `main`, the GitHub marketplace serves no plugin, so add this checkout (with `feature/core-v1` checked out) as a local marketplace:

   ```text
   claude plugin marketplace add E:\BSRI\GitHub\software-factory
   claude plugin install software-factory@bsri-factory
   codex plugin marketplace add E:\BSRI\GitHub\software-factory
   codex plugin add software-factory@bsri-factory
   ```

   Check: `claude plugin list` shows `software-factory@bsri-factory`; `codex plugin list --marketplace bsri-factory` shows it installed. Restart both hosts. Record any command that did not work as written.
3. **Third-party skills.** `factory skills check --host claude` and `factory skills check --host codex` (install guide, steps 4 and 5). Record what each reports; missing skills do not stop the run, but note which steps had to go on without them.
4. **Codex's writable roots, for pass B.** Create the folder `~/.factory` (your home folder's `.factory`) and add it to `writable_roots` under `[sandbox_workspace_write]` in `~/.codex/config.toml`, as the install guide's step 3 shows, then restart Codex. Without it, a Codex session's writes to `~/.factory/staging` (rendered prompts, quote files, the setup commit message) fail with `Access to the path … is denied.` (rehearsal finding D1). The key is the one Codex CLI 0.163.0 recognises but was not yet tried in a session: record whether Codex accepted it.
5. **The Codex sandbox on Windows.** If `codex exec` fails with a sandbox error (this machine's elevated sandbox has failed before), set the user environment variable `FACTORY_CODEX_ARGS` to `-c windows.sandbox="unelevated"` before starting Claude Code; every lens run then records it in its provenance line. For pass B, Codex itself is the host and uses its own sandbox setting: if Codex cannot run commands, fix that first (it is outside the factory).
6. **A stopwatch.** Note the wall-clock time at each numbered step below; the record asks for it.

## Making a copy of the sample (each pass)

Use a new folder outside every repository, for example under your temp folder. In Git Bash:

```bash
F=/e/BSRI/GitHub/software-factory
D="$TEMP/sf-v1-claude-$(date +%Y%m%d-%H%M)"     # sf-v1-codex-… for pass B
mkdir -p "$D" && git -C "$F" archive HEAD:examples/sample-app | tar -x -C "$D"
cd "$D" && git init -b main && git config user.name "<your name>" && git config user.email "<your email>"
git add -A && git commit -m "Sample app: rangering"
python verify.py --quick                          # Ran 3 tests ... OK, verify quick: PASS
```

`git archive` copies only the committed sample, without `__pycache__`. The local `git config` keeps the identity in that repository only. The copy has no remote, so the factory pushes nothing.

## Pass A: Claude Code as the host

Open Claude Code in the copy's folder (`cd "$D"`, then `claude`). Type the quoted messages; answer the factory's questions as given. Everywhere the factory asks for your approval, answer in your own words, and only when you mean it.

### A1. Setup

> Set up this repository for the software factory.

Expected: the factory-setup skill runs `factory --version`, reads `README.md` (no `AGENTS.md`/`CLAUDE.md` yet), then asks one question per message, each with a proposed answer:

| Question | Answer |
|---|---|
| Quick verify command | `python verify.py --quick` |
| Full verify command (and does it pass on `main` today?) | `python verify.py`; yes, it passes |
| Approvers | your name, exactly as you will write it every time |
| Branching | `per-item` |
| Build policy | `allow` |
| Name, base branch, mode | `rangering`, `main`, `review` |

Then it cuts `factory/setup`, runs `factory setup`, reports `conflicts: []`, drafts `always_flag` and hard stops with you (the sample has no rules: keep the defaults), runs `factory config check --project .` (`config ok`) and both skills checks, shows you every file and the diffs, and asks "Do you approve this setup for commit?".

- Approve in your own words. It commits on `factory/setup` with your words in the message and the trailers `Factory-Role: interviewer` and `Factory-Model: …`.
- **You merge setup:** `git switch main && git merge --no-ff factory/setup`. (The factory never merges into the base branch.)

Check: `git log --oneline main` shows the setup merge; `factory.yaml`, `REVIEW.md`, `CLAUDE.md`, `AGENTS.md` (both with the same factory section, because the sample had neither), `factory/` exist on `main`; `.gitignore` gained `factory/dashboard/`.

### A2. Triage

> Triage this for the factory: the range-ring label shows "m" for values in km.

Expected: a few questions, one per message, with facts looked up in the code rather than asked of you. It proposes the **Fix** track (one task, no shared contract), slug `km-label` or similar, and opens `FIX-1` with `factory work new` (branch `factory/FIX-1-km-label`, folder `factory/work/FIX-1-km-label/`). It writes `ticket.md`: the problem, numbered repro steps (it should reproduce `format_range(2500, "km")` returning `2.5 m` itself and say so), acceptance criteria (including a test that fails before the fix and passes after) and out of scope. It asks you to approve the ticket (`ticket` rev 1).

- Check the criteria. They should at least say: a km value is labelled `km`; `m` and `nmi` labels are unchanged; a new test fails before the fix and passes after; `python verify.py --quick` passes.
- Approve in your own words. Expected: `factory/work/FIX-1-km-label/approvals/ticket-rev1.md` holds your words verbatim, the approver, the commit, the time and the host `claude`.

### A3. The task loop

The factory-run skill should carry on to `T1` by itself; if it stops, say "Continue FIX-1".

Expected, in order, each step ending in a commit with `Factory-Work: FIX-1/T1`, `Factory-Role: implementer`, `Factory-Model: …`:

1. A log entry `T1 started at <sha>`.
2. A failing test for the km label, run, with the failure output shown (`AssertionError: '2.5 m' != '2.5 km'` or similar).
3. The smallest fix in `rangering/__init__.py`, the test passing, and `python verify.py --quick` with its totals pasted (`Ran 4 tests` or more, `OK`).
4. **Review round 1.** Two lenses, correctness and security, each resolved with `factory role resolve lens-<lens> --host claude --author claude` to a **command** invocation (`codex exec`), rendered with `factory prompt render`, and started **at the same time** in the background. Each writes `reviews/T1-r1-<lens>.md`, first line `VERDICT: PASS` or `VERDICT: CHANGES`, last line a provenance stamp `<!-- factory: role=lens-<lens> vendor=openai model=… -->` (plus `extra="-c windows.sandbox=…"` if you set step 0.5). Then `factory panel combine` writes `reviews/T1-r1-panel.md`.
5. On CHANGES: `reviews/disposition-T1-r1.md` answers every finding (accepted, modified or declined, each with a reason), the fixes come test-first, and round 2 runs the same way. At most three rounds; a third CHANGES is the `third-failed-round` hard stop.
6. On PASS: minor findings go to `follow-ups.md`; the acceptance criteria in `ticket.md` are ticked; the state is `T1 done`. **No tester runs for a Fix** (design section 8.3): the merge gate's full verify takes its place.

Watch for: the reviewers being started one after the other instead of together; a lens run that fails (it should retry once with `--fallback` and log it); the session editing files while a lens runs.

### A4. Retro and the merge gate

Expected: `retro.md` (what happened, what was learned, what to change), with any lesson proposed to you one at a time; approve or decline each. Then the factory-merge-gate skill: the branch already contains `main` (no re-review), a clean tree, a spot check it runs by hand (for example `python -c "from rangering import format_range; print(format_range(2500, 'km'))"` printing `2.5 km`), and `factory gate FIX-1 --spot-check "…"`, which runs `python verify.py` and writes `merge-gate.md`:

```text
GATE: PASS
- base-merged: ok — HEAD contains main
- reviewed-commit: ok — …
- size-cap: ok — … lines (cap 400)
- id-unused-on-base: ok — …
- verify-full: ok — (the output of python verify.py)
- spot-check: ok — (the spot check)
```

It commits `merge-gate.md`, then gives you the **merge** checkpoint: the diff stat, the panel results with any declined findings, the verify output, the risks, the follow-ups, and the merge commands.

### A5. You merge

Run the commands it gives, without the `pull` and `push` lines (there is no remote):

```bash
git switch main
git merge --no-ff factory/FIX-1-km-label
python verify.py --quick
```

Tell the factory "Merged." Expected: it checks with `git merge-base --is-ancestor factory/FIX-1-km-label main`, then, if you approved a lesson, cuts `factory/FIX-1-km-label-lessons`, applies it, and asks you to merge that too. If `follow-ups.md` has open entries, it offers to triage each into a queued work item.

### Expected artifacts (pass A)

| Where | What |
|---|---|
| `main` | the setup merge and the FIX-1 merge; `format_range(2500, "km")` returns `2.5 km` |
| `factory/work/FIX-1-km-label/` | `ticket.md` (criteria ticked), `progress.md` (Status line and a log, newest first), `decisions.md`, `retro.md`, `merge-gate.md` with `GATE: PASS`, `approvals/ticket-rev1.md` quoting you |
| `…/reviews/` | `T1-r1-correctness.md`, `T1-r1-security.md`, `T1-r1-panel.md` (and `disposition-T1-r1.md` plus `T1-r2-*` if round 1 asked for changes); the lens files' stamps name an OpenAI model |
| `git log` | every commit the skills make carries `Factory-Work`, `Factory-Role` and `Factory-Model` trailers (the commit `factory work new` makes by itself carries only `Factory-Work`) |

### Record (pass A)

| Item | What to write down |
|---|---|
| Times | start and end of A1 to A5, and of each lens run |
| Files created | `git diff --stat <sample commit>..main` and the work folder's file list |
| Panel verdicts per round | each lens's verdict and findings (ids, severities), each panel verdict, and every disposition |
| Gate report | `merge-gate.md`, pasted |
| Approvals | each record's path and the quote it holds |
| Manual interventions | every time you had to correct, prompt or unblock the factory, with what you typed and why |
| Deviations | every step that did not happen as this checklist expects, or as the skills say |

## Pass B: Codex as the host

Make a **fresh copy** (the sample commit only, not pass A's result), and open Codex in it (`cd "$D"`, then `codex`; or the Codex desktop app on that folder). Name the skills with `$` when Codex does not pick them by itself: `$factory-setup`, `$factory-triage`, `$factory-run`.

Run A1 to A5 again with the same messages and answers. The differences to check:

| Step | Expected on a Codex host |
|---|---|
| A1 | the skills checks run for both hosts; the Codex check may report `writing-plans` and `verification-before-completion` missing (superpowers not installed in Codex). Record it. |
| A1 | setup creates both `CLAUDE.md` and `AGENTS.md` with the same factory section (the sample has neither; rehearsal finding D2, fixed). Check that a later Codex session, for example at A2, knows where the work lives and how to resume without being pointed at `CLAUDE.md`. |
| A1, A2, A3 | the setup commit message, rendered prompts and quote files go to `~/.factory/staging/rangering/…`, outside the project. With `~/.factory` in Codex's writable roots (step 0.4) these writes should need no extra permission (the rehearsal's finding D1 was without it). If Codex still asks to run those commands with more permissions, or they fail with `Access to the path … is denied.`, allow only those writes, and record each prompt or failure. |
| A2 | `approvals/ticket-rev1.md` names the host `codex` |
| A3 | the implementer is the OpenAI binding (`factory role resolve implementer --host openai`); the lenses resolve with `--host openai --author openai` to **command** invocations of `claude -p`, read-only, started together as background jobs in one shell command; each lens file's stamp names a **Claude** model (`vendor=claude model=claude-…`) |
| A3 | Codex may cap how long one shell command runs: record whether both lens runs finished, and how long each took |
| A4 | the gate runs `python verify.py` inside Codex's sandbox: record whether it ran |

Expected artifacts and the record are the same as pass A's, with the Claude-model stamps.

## Optional pass C: a Feature, for the tester

A Fix never runs the independent tester or the maintainability lens. To see them in v1, run one small Feature on pass A's copy after its merge, in Claude Code:

> Triage this for the factory: add statute miles (`mi`) as a fourth unit, with tests for `nmi` and `mi`.

Expected: the **Feature** track (`FEA-1`; each track is numbered separately), an intent checkpoint, a mini-spec reviewed by `doc-reviewer-b` (Codex) and the spec checkpoint, a plan checkpoint, the task loop with three lenses (maintainability as a Claude subagent, never blocking), and after each passing task a tester report `reviews/T<n>-tester-1.md` starting `RESULT: PASS`. Record it like pass A.

## v1 is accepted when

- [ ] Pass A ended in your merge of `FIX-1`, with every expected artifact.
- [ ] Pass B ended the same way, with the lens stamps naming Claude models.
- [ ] Neither pass wrote an approval you did not give, pushed, or merged into `main` by itself.
- [ ] Every manual intervention and deviation is recorded, and each defect is a `FIX-` item for plan 2 or fixed before v1 closes.
