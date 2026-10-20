---
name: factory-setup
description: Set up a project repository for the BSRI software factory, once per project. Use when the owner asks to install, set up or onboard the factory in a repo, or when another factory skill finds no factory.yaml at the repository root. Interviews the owner, runs the setup command, settles conflicts with the project's own rules, drafts review rules, checks skills on both hosts, and commits only after the owner approves.
---

# Factory setup

Sets a project up for the factory: `factory.yaml`, `REVIEW.md`, the `factory/` folders and a short factory section in `CLAUDE.md`/`AGENTS.md`. It runs once per project, on its own branch, and nothing is committed until the owner has reviewed the result and approved it in chat.

The project's own rules win over factory defaults. Every conflict is shown to the owner and settled in `factory.yaml`; the owner approving `factory.yaml` is the explicit request such rules ask for.

Uses skills: grilling

## Rules for the whole skill

- Ask the owner **one question per message**, in the grilling skill's style, and offer your proposed answer with each question (and where in the repo it came from), so the owner can simply confirm.
- Never commit before the owner approves. Never push to or merge into the base branch: the owner merges the setup.
- Change no existing project file except through `factory setup` (it appends the factory section and a `.gitignore` entry). You edit only files setup creates: `factory.yaml`, `REVIEW.md` and `factory/baseline/expected-failures.md`.
- Never stash, reset or discard anyone's changes. Never enter a credential or install anything.

Two names below depend on the host you run in:

| Placeholder | Claude Code | Codex (CLI or the ChatGPT app) |
|---|---|---|
| `<host>` (for `factory skills check`) | `claude` | `codex` |
| `<vendor>` (for `factory role …`) | `claude` | `openai` |

## 1. Check the starting point

1. Run `factory --version`. If the command is not found, stop: the owner installs the CLI first (`pip install -e <factory checkout>/tools`, as the factory's install guide says).
2. Work from the repository root (`git rev-parse --show-toplevel`). If `factory.yaml` is already there, the project is set up: run `factory config check --project .`, report the result, and stop. Changes after setup are edits to `factory.yaml` agreed with the owner, never a second setup.
3. `git status --porcelain --untracked-files=no` must print nothing. If it lists files, show them to the owner and ask what to do; they commit or set aside their own work.

## 2. Read the project's rules

Read `AGENTS.md` and `CLAUDE.md` at the root (and the files they point to), `CONTRIBUTING.md`, the README's build and test sections, and the CI workflow files. Note:

- how the project builds and runs its tests: candidates for the quick and full verify commands;
- rules about branches, builds, commits, pushes and merges;
- rules a change must never break (for example "never change XML serialization"): candidates for `always_flag` in `REVIEW.md` and for project hard stops;
- the base branch: when there is a remote, `git symbolic-ref --short refs/remotes/origin/HEAD` prints `origin/<base>`; otherwise ask.

## 3. Interview the owner

One question per message, in this order. Explain each setting in a sentence when the owner may not know it.

1. **Quick verify command.** Builds and tests what one task touched; run at every task; must fit a Fix inside one session; exits non-zero on failure and prints totals.
2. **Full verify command.** The whole build and test suite; run by the tester, at milestone demos and at the merge gate. Its exit code is the verdict. Ask whether the full suite passes on the base branch today. If it does not, each known failure goes in `factory/baseline/expected-failures.md` (with the base commit where it fails and why), and the command must exit 0 when only those fail; agree with the owner how (often a small wrapper script the owner adds).
3. **Approvers.** Who may approve a checkpoint in chat, written exactly as each name will be used; default: the owner.
4. **Branching.** `per-item` (one branch `factory/<ID>-<slug>` per work item; the default) or `single` (all work on one branch the owner names, one item at a time; for projects whose rules forbid extra branches).
5. **Build policy.** `allow` (the factory runs the full build when the plan says so) or `ask` (it asks the owner before each full run; for projects whose rules restrict builds).
6. **The remaining defaults, in one confirmation.** The project's name, the base branch, and the mode: `review` (stop for the owner at intent, spec, plan, each milestone demo and the merge; the default) or `auto` (write a note and carry on; hard stops still halt).

## 4. Choose the branch

Setup's files go on a branch of their own, so the owner reviews them before they reach the base branch.

- If the project's rules allow branches, cut `factory/setup` from the base branch: `git switch -c factory/setup <base>`, and tell the owner.
- If the rules forbid or restrict extra branches, ask the owner first which branch setup goes on (often the work branch they named), and switch to it.

## 5. Run setup

Run, from the repository root, quoting each value for your shell:

`factory setup --project "<name>" --verify-quick "<quick command>" --verify-full "<full command>" --approver "<name>" --base <base> --branching <per-item|single> --builds <allow|ask> --mode <review|auto>`

Repeat `--approver` once per approver. With `single` branching add `--work-branch <branch>`.

- Exit 0 prints JSON: `created`, `updated` and `conflicts`. Exit 2 means setup refused and wrote nothing; the reason is on stderr. Fix the cause and run it again.
- Open `factory.yaml` and check that `verify.quick`, `verify.full` and `approvers` hold exactly what the owner said. If your shell changed a value (quotes, `$`, `%`), correct it in the file.
- To change an answer later, edit `factory.yaml`; never run setup a second time.

## 6. Settle every conflict

`conflicts` lists the lines of `AGENTS.md`/`CLAUDE.md` that look like rules a factory default would break, each as `<file>:<line>: <text>`. Take them one at a time: quote the rule, say which default it meets, propose a settlement, and ask. Typical settlements:

| The project's rule says | Settle it with |
|---|---|
| no extra branches | `branching: single` and `work_branch: <the branch the owner names>` |
| do not build (unless instructed) | `builds: ask` |
| commit straight to main | the owner chooses: work on a separate branch the owner merges (the default), or `branching: single` with `work_branch` set to the base branch (the factory commits there, and the owner pushes) |
| never push | add a project hard stop, such as `- no-push  # AGENTS.md:12: never push; the owner pushes` |
| (a line that only looks like a conflict) | "not a conflict", with the reason |

Record every settlement in `factory.yaml`: change the keys it affects, and add one comment line per conflict to a block at the top of the file:

```text
# Settled at setup with <approver>, <date>:
#   AGENTS.md:12 "never push" -> hard stop no-push; the owner pushes
```

## 7. Draft the review rules and the project's hard stops

Present each draft to the owner (one at a time) and apply their changes.

- **`always_flag`** in the front matter of `REVIEW.md` (setup writes `always_flag: []`): one entry per project rule a reviewer must flag every time a change breaks it, as a short sentence citing its source, for example `"Never change XML serialization (AGENTS.md:27)"`. Only rules about the product and its changes belong here, not rules about how agents work.
- **`ignore`** in `REVIEW.md` and **`merge.generated`** in `factory.yaml`: glob patterns of generated files, if the repo has any.
- **`hard_stops`** in `factory.yaml`: keep the ten defaults unless the owner removes one on purpose; add a project id, with a comment saying what it covers, for each project rule that must halt the factory before the action (for example `- xml-serialization  # AGENTS.md:27: never change XML serialization`).

## 8. Check

1. `factory config check --project .` must print `config ok`. Otherwise fix each problem it lists (with the owner, when the fix is a decision) and run it again.
2. Run both, whichever host you are in: `factory skills check --host claude --project .` and `factory skills check --host codex --project .`. Report to the owner what each says: skills present, skills missing (with the fallback for each, and whether that fallback is installed), and interactive skills bound to unattended roles. Missing skills do not stop setup; the owner installs them (the factory's install guide lists them). Never install skills yourself.

## 9. Owner review and commit

1. Show the owner the result: `git status`, the full text of `factory.yaml` and `REVIEW.md`, the diff of each updated file (`git diff`), the settled conflicts and both readiness results.
2. Ask: "Do you approve this setup for commit?" Wait for an explicit yes. If they ask for changes, make them and show the result again. A conditional yes ("yes, but …") is a request for changes.
3. Get your provenance: `factory role resolve interviewer --host <vendor> --project .` prints `resolved.model` and `resolved.effort`.
4. Write the commit message with your file-writing tool (never through the shell, which can change quotes, `$`, `%` and line breaks) to `<home>/.factory/staging/<project>/setup-commit.txt`, where `<home>` is your home folder written out in full:

   ```text
   Set up the software factory

   Approved by <approver> in chat:

   > <the owner's message, word for word, one "> " line per line they wrote>

   Factory-Role: interviewer
   Factory-Model: <model> (<effort>)
   ```

   Add your host's own co-author line at the end if it uses one.
5. Stage only what setup made and what you edited: `git add -- factory.yaml REVIEW.md factory` plus each of `CLAUDE.md`, `AGENTS.md` and `.gitignore` that setup listed as created or updated. Check `git status` shows nothing else staged, then `git commit -F <the message file>` and delete the message file.
6. Push the setup branch only if the owner asks (never the base branch). With `per-item` branching, tell the owner to merge it into the base branch before the first work item is triaged: work-item branches are cut from the base branch and need `factory.yaml` there.
