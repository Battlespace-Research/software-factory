# Installing the software factory

This guide sets up the factory on your machine, for Claude Code, for Codex, or for both. Once it is done, go on to [Using the factory](using-the-factory.md).

> **Version 0.1.0, being reviewed.** The `claude` and `codex` commands below were checked against the help text of Claude Code 2.1.294 and Codex CLI 0.163.0 on Windows 11, and `claude plugin validate` passes on the plugin; the third-party install commands are the ones their publishers document. The first full install on a teammate's machine is part of the v1 acceptance run, so tell the factory's maintainers about anything that does not work as written here.

## What you install

| Part | What it is | Where it comes from |
|---|---|---|
| The `factory` command | A small Python tool that does the factory's exact work: settings, work items, approvals, review files, the merge gate | A clone of this repository, installed with pip |
| The `software-factory` plugin | The factory's skills (`factory-setup`, `factory-triage`, `factory-run`, …) and, in Claude Code, its reviewer and tester subagents | This repository, through each host's plugin marketplace |
| Third-party skills | The craft skills the factory calls: grilling, TDD, code review, planning and others | Their own publishers; the factory never copies them |

There is nothing else to trust or turn on: version 1 has no hooks, no MCP servers and no settings changes. The rules are kept by the skills, by git and by the merge gate.

## Before you start

- **Git**, with read access to the private repository `Battlespace-Research/software-factory`. Ask the factory's owner for access. To check it, run `git ls-remote https://github.com/Battlespace-Research/software-factory.git`; it lists branches when you have access.
- **Python 3.11 or newer.** Check with `python --version`.
- **Claude Code and the Codex CLI, both installed and signed in**, even if you only work in one of them. The review panel always asks the other vendor for a second opinion: a session in Claude Code runs some reviewers through `codex exec`, and a session in Codex runs them through `claude -p`. (A project limited to one vendor can change this in its `factory.yaml`; ask the owner.)
- **Node.js**, only to install Matt Pocock's skills with `npx` (step 4).

## Step 1: install the `factory` command

Clone the repository somewhere permanent and install the command from it, in a virtual environment:

```bash
git clone https://github.com/Battlespace-Research/software-factory.git
cd software-factory
python -m venv .venv
```

Activate the virtual environment:

| Shell | Command |
|---|---|
| PowerShell | `.venv\Scripts\Activate.ps1` |
| Git Bash | `source .venv/Scripts/activate` |
| Linux or macOS | `source .venv/bin/activate` |

Then install the command and check it:

```bash
python -m pip install -e tools
factory --version
```

`factory --version` prints `0.1.0`.

**Keep the clone where it is.** The `-e` (editable) install runs the command from the clone, and the command reads the factory's role table, skills list, prompts and project templates (`config/`, `prompts/`, `templates/`) from it. Moving or deleting the clone breaks the command.

**Make `factory` reachable from your agents.** Claude Code and Codex run commands in a shell of their own, so `factory` must be on the PATH there too. The reliable way is to add the virtual environment's scripts folder to your user PATH: `<clone>\.venv\Scripts` on Windows, `<clone>/.venv/bin` on Linux and macOS. Then open a new terminal, start a session, and ask the agent to run `factory --version`. (The factory-setup skill checks this first and stops if the command is missing.)

## Step 2: install the plugin in Claude Code

Add the factory's marketplace, then install the plugin from it:

```bash
claude plugin marketplace add Battlespace-Research/software-factory
claude plugin install software-factory@bsri-factory
```

The marketplace is the private GitHub repository, so it is fetched with your own git credentials. Start a new Claude Code session afterwards. `claude plugin list` shows `software-factory@bsri-factory`, and in a session the skills appear as `software-factory:factory-setup`, `software-factory:factory-triage` and so on.

## Step 3: install the plugin in Codex

Add the same repository as a Codex marketplace, then install the plugin:

```bash
codex plugin marketplace add Battlespace-Research/software-factory
codex plugin add software-factory@bsri-factory
```

Codex fetches the repository with your git credentials too. `codex plugin list --marketplace bsri-factory` shows the plugin as installed. Start a new Codex session afterwards.

The Codex app, and Codex in the ChatGPT desktop app, read the same Codex settings folder (`~/.codex`) as the CLI, so the plugin should appear in the app's Plugins list after a restart. This has not been checked for version 1 yet.

## Step 4: install the third-party skills

The factory calls skills that other people publish. `factory skills check` (step 5) tells you which ones a host is missing.

| Skills | Publisher | Used for | Needed? | License |
|---|---|---|---|---|
| `grilling`, `research`, `to-spec`, `tdd`, `diagnosing-bugs`, `code-review`, `handoff` | Matt Pocock skills (`mattpocock/skills`) | interviews, research, specs, test-first work, debugging, reviews, session hand-offs | **Required** | not yet confirmed |
| `grill-me`, `grill-with-docs`, `domain-modeling`, `codebase-design`, `to-tickets`, `wayfinder`, `implement`, `triage` | Matt Pocock skills | fallbacks and extra help for the same roles | Optional | not yet confirmed |
| `writing-plans`, `verification-before-completion` | superpowers (`obra/superpowers`) | Feature plans; evidence before anything is called done | **Required** | MIT |
| `test-driven-development`, `systematic-debugging`, `subagent-driven-development` | superpowers | fallbacks for `tdd` and `diagnosing-bugs`; optional build help | Optional | MIT |
| `last30days` | last30days (`mvanhorn/last30days-skill`) | research on recent discussion | Optional | MIT |
| `diagram-design` | Cathryn Lavery (`cathrynlavery/diagram-design`) | diagrams in specs | Optional | MIT |
| `deploy-checklist`, `incident-response` | Anthropic's `engineering` plugin | the Deploy and Maintain stages, which come in a later version | Not used in v1 | not yet confirmed |
| `security-review` | built into Claude Code | extra help for the security reviewer | Optional | — |

"Required" means `factory skills check` reports the skill as missing when it is absent. The full list, per role, is in `config/skills.yaml` in the clone.

### Matt Pocock skills (both hosts at once)

These are installed with the `skills` command-line tool, which `npx` downloads from npm:

```bash
npx skills add mattpocock/skills -g -a claude-code -a codex
```

`-g` installs them for your user rather than one project, and `-a` picks the hosts. The tool asks which skills to install: pick at least the required ones in the table above, and the optional ones you want. (This is the `skills` tool's documented usage in its version 1.7.0; `npx skills add --help` shows the options of the version you get.)

### superpowers

In **Claude Code**, install it from Anthropic's official marketplace:

```bash
claude plugin install superpowers@claude-plugins-official
```

If `claude plugin marketplace list` does not show `claude-plugins-official`, add it first with `claude plugin marketplace add anthropics/claude-plugins-official`.

In **Codex**, superpowers is in OpenAI's curated plugin marketplace. Run `codex plugin list` and find the line `superpowers@<marketplace>`; on the machine this guide was written on, the marketplace is `openai-curated-remote`:

```bash
codex plugin add superpowers@openai-curated-remote
```

In the Codex app you can instead open Plugins in the sidebar and add Superpowers from there.

**If you cannot install superpowers in Codex,** `factory skills check --host codex` reports `writing-plans` and `verification-before-completion` as missing, with no fallback. The factory still runs in Codex: when a step needs one of them, the factory-run skill tells you it is missing and asks whether to go on without it. Because a work item's state is all in files, you can also do those steps from Claude Code and come back.

### Optional extras

- **last30days:** `npx skills add mvanhorn/last30days-skill -g -a claude-code -a codex`
- **diagram-design** in Claude Code: `claude plugin marketplace add cathrynlavery/diagram-design`, then `claude plugin install diagram-design@diagram-design`.
- **diagram-design** in Codex: `codex plugin marketplace add cathrynlavery/diagram-design`, then `codex plugin add diagram-design@diagram-design`.
- **Anthropic's `engineering` plugin** is not used in version 1. It comes from Anthropic's `knowledge-work-plugins` marketplace; if your claude.ai organization provides it, it is already in `claude plugin list`.

## Step 5: check that everything is ready

Run these three commands in a new terminal (from anywhere; inside a project that is set up for the factory, add `--project .` to each):

```bash
factory config check
factory skills check --host claude
factory skills check --host codex
```

- `factory config check` prints `config ok`.
- Each `factory skills check` lists the required skills as `present` or `missing` and ends with `skills ok` (exit code 0) or `skills not ready` (exit code 1). A missing skill shows its fallback, if it has one, and whether that fallback is installed. Install what is missing (step 4) and run the check again.

`factory skills check` looks only at the third-party skills. To check the factory's own plugin, use `claude plugin list` and `codex plugin list --marketplace bsri-factory`.

## Updating

Keep the command and the plugin on the same version: `factory --version` and the version that `claude plugin list` and `codex plugin list` show.

- **The command:** run `git pull` in the clone. The editable install uses the new code at once; run `python -m pip install -e tools` again only if `tools/pyproject.toml` changed.
- **Claude Code:** `claude plugin marketplace update bsri-factory`, then `claude plugin update software-factory@bsri-factory`, then restart Claude Code.
- **Codex:** `codex plugin marketplace upgrade bsri-factory`, then check the version with `codex plugin list --marketplace bsri-factory`. If it still shows the old version, remove and add the plugin again: `codex plugin remove software-factory@bsri-factory`, then `codex plugin add software-factory@bsri-factory`.

## Troubleshooting

| Problem | What to do |
|---|---|
| A skill says `factory` was not found | The scripts folder of the virtual environment is not on the PATH the host sees (step 1). Fix the PATH, then restart the host. |
| `claude plugin marketplace add` or `codex plugin marketplace add` cannot read the repository | You lack access to `Battlespace-Research/software-factory`, or git has no credentials for GitHub. Check with `git ls-remote` (see "Before you start"). |
| A review run fails on Windows with a Codex sandbox error | Set the environment variable `FACTORY_CODEX_ARGS` to `-c windows.sandbox="unelevated"`. The factory adds it to every `codex exec` run it starts and notes it in each review file it writes. |
| `factory skills check` misses a skill you know is installed in an unusual folder | Set `FACTORY_SKILL_PATHS` to that folder (several folders are separated by `;` on Windows and `:` elsewhere). This only widens the check; it does not change what the host loads. |
| You want the command to use another clone of the factory | Set `FACTORY_HOME` to that clone's folder. |
