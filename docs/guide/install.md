# Installing the software factory

This guide sets up the factory on your machine, for Claude Code, for Codex, or for both. Once it is done, go on to [Using the factory](using-the-factory.md).

> **Version 0.3.0.** The `claude` and `codex` commands below were checked against the help text of Claude Code 2.1.294 and Codex CLI 0.163.0 on Windows 11, and `claude plugin validate` passes on the plugin; the third-party install commands are the ones their publishers document. The first full install on a teammate's machine is part of the v1 acceptance run, so tell the factory's maintainers about anything that does not work as written here.

## What you install

| Part | What it is | Where it comes from |
|---|---|---|
| The `sw-factory` command | A small Python tool that does the factory's exact work: settings, work items, approvals, review files, the merge gate | A clone of this repository, installed with pip |
| The `bsri-sw-factory` plugin | The factory's skills (`setup-project`, `intake`, `orchestrate`, …) and, in Claude Code, its reviewer and tester subagents | This repository, through each host's plugin marketplace |
| Third-party skills | The craft skills the factory calls: grilling, TDD, code review, planning and others | Their own publishers; the factory never copies them |

There is nothing else to trust or turn on: version 1 has no hooks, no MCP servers and no settings changes. The rules are kept by the skills, by git and by the merge gate.

## Before you start

- **Git**, with read access to the private repository `Battlespace-Research/software-factory`. Ask the factory's owner for access. To check it, run `git ls-remote https://github.com/Battlespace-Research/software-factory.git`; it lists branches when you have access.
- **Python 3.11 or newer.** Check with `python --version`.
- **pipx**, recommended for step 1. Its [installation guide](https://pipx.pypa.io/stable/installation/) shows how to install it on each system; step 1 also says how to do without it.
- **Claude Code and the Codex CLI, both installed and signed in**, even if you only work in one of them. The review panel always asks the other vendor for a second opinion: a session in Claude Code runs some reviewers through `codex exec`, and a session in Codex runs them through `claude -p`. (A project limited to one vendor can change this in its `sw-factory.yaml`; ask the owner.)
- **Node.js**, only to install Matt Pocock's skills with `npx` (step 4).

## Step 1: install the `sw-factory` command

Clone the repository somewhere permanent:

```bash
git clone https://github.com/Battlespace-Research/software-factory.git
```

Then install the command so that **only `sw-factory`** lands on your PATH, never the Python interpreter that runs it. Your projects' own commands (`python verify.py`, the merge gate's full verify) must keep running in your projects' Python: if the factory's virtual environment came first on the PATH, they would run in it instead.

**With pipx (recommended).** [pipx](https://pipx.pypa.io/) installs a command-line tool in a virtual environment of its own and puts only the tool's commands on the PATH:

```bash
pipx install -e <clone>/tools
pipx ensurepath
```

`-e` (editable) runs the command from the clone. `pipx ensurepath` adds pipx's command folder (`~/.local/bin`, which is `%USERPROFILE%\.local\bin` on Windows) to your user PATH; open a new terminal afterwards.

**Without pipx.** Make a virtual environment in the clone, install the command into it, and put only the command on the PATH:

```bash
cd <clone>
python -m venv .venv
.venv/Scripts/python -m pip install -e tools     # Linux or macOS: .venv/bin/python -m pip install -e tools
```

- **Windows:** copy `<clone>\.venv\Scripts\sw-factory.exe` into a folder that holds nothing else (for example `%USERPROFILE%\sw-factory-bin`) and add that folder to your user PATH. The copy keeps working on its own: it starts the virtual environment's Python by its full path. Copy it again only if you re-create the virtual environment.
- **Linux or macOS:** `ln -s <clone>/.venv/bin/sw-factory ~/.local/bin/sw-factory` (with `~/.local/bin` on your PATH).

Do not add `<clone>\.venv\Scripts` (or `<clone>/.venv/bin`) itself to the PATH: it holds the virtual environment's `python` and `pip` too.

**Check it** in a new terminal:

```bash
sw-factory --version
where python        # PowerShell: where.exe python; Linux or macOS: which -a python
```

`sw-factory --version` prints `0.3.0`. The first `python` listed must be your own, the one your projects use, not one inside the factory's clone or pipx's folders. If it is the factory's, take that folder off the PATH and install the command as above.

**Keep the clone where it is.** The editable install runs the command from the clone, and the command reads the factory's role table, skills list, prompts and project templates (`config/`, `prompts/`, `templates/`) from it. Moving or deleting the clone breaks the command.

**Make `sw-factory` reachable from your agents.** Claude Code and Codex run commands in a shell of their own, so `sw-factory` must be on the PATH there too: start a new session after changing the PATH and ask the agent to run `sw-factory --version`. (The setup-project skill checks this first and stops if the command is missing.)

## Step 2: install the plugin in Claude Code

Add the factory's marketplace, then install the plugin from it:

```bash
claude plugin marketplace add Battlespace-Research/software-factory
claude plugin install bsri-sw-factory@bsri-factory
```

The marketplace is the private GitHub repository, so it is fetched with your own git credentials. Start a new Claude Code session afterwards. `claude plugin list` shows `bsri-sw-factory@bsri-factory`, and in a session the skills appear as `bsri-sw-factory:setup-project`, `bsri-sw-factory:intake` and so on.

## Step 3: install the plugin in Codex

Add the same repository as a Codex marketplace, then install the plugin:

```bash
codex plugin marketplace add Battlespace-Research/software-factory
codex plugin add bsri-sw-factory@bsri-factory
```

Codex fetches the repository with your git credentials too. `codex plugin list --marketplace bsri-factory` shows the plugin as installed. Start a new Codex session afterwards.

**Let Codex write to the factory's staging folder.** The skills keep their scratch files (rendered reviewer prompts, tester briefs, the text of your approvals before they are recorded, the setup commit message) in `~/.sw-factory/staging/`, outside the project. Codex's `workspace-write` sandbox lets a session write only inside the project and the folders listed as its *writable roots*, so without this every such write fails with `Access to the path '…\.sw-factory\staging\…' is denied.`, or Codex asks you for more permissions each time. Create the folder once, then add it to the writable roots in Codex's settings file, `~/.codex/config.toml`:

```bash
mkdir -p ~/.sw-factory       # PowerShell: New-Item -ItemType Directory -Force "$HOME\.sw-factory"
```

```toml
[sandbox_workspace_write]
writable_roots = ['C:\Users\<you>\.sw-factory']    # Linux or macOS: ["/home/<you>/.sw-factory"]
```

Write the full path of your own home folder (the single quotes keep the backslashes as they are). If `config.toml` already has a `[sandbox_workspace_write]` table, add the `writable_roots` line to it instead of a second table. Start a new Codex session afterwards. For one session only, you can instead start Codex with `codex --add-dir <your home folder>/.sw-factory`.

The table and key are the ones Codex CLI 0.163.0 recognises (`sandbox_workspace_write.writable_roots`; `--add-dir` is in `codex --help`), but this setting has not yet been tried in a Codex session for version 1. If Codex rejects it or still denies the writes, confirm the key in the Codex configuration docs for your version, and tell the factory's maintainers.

The Codex app, and Codex in the ChatGPT desktop app, read the same Codex settings folder (`~/.codex`) as the CLI, so the plugin should appear in the app's Plugins list after a restart. This has not been checked for version 1 yet.

## Step 4: install the third-party skills

The factory calls skills that other people publish. `sw-factory skills check` (step 5) tells you which ones a host is missing.

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

"Required" means `sw-factory skills check` reports the skill as missing when it is absent. The full list, per role, is in `config/skills.yaml` in the clone.

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

**If you cannot install superpowers in Codex,** `sw-factory skills check --host codex` reports `writing-plans` and `verification-before-completion` as missing, with no fallback. The factory still runs in Codex: when a step needs one of them, the orchestrate skill tells you it is missing and asks whether to go on without it. Because a work item's state is all in files, you can also do those steps from Claude Code and come back.

### Optional extras

- **last30days:** `npx skills add mvanhorn/last30days-skill -g -a claude-code -a codex`
- **diagram-design** in Claude Code: `claude plugin marketplace add cathrynlavery/diagram-design`, then `claude plugin install diagram-design@diagram-design`.
- **diagram-design** in Codex: `codex plugin marketplace add cathrynlavery/diagram-design`, then `codex plugin add diagram-design@diagram-design`.
- **Anthropic's `engineering` plugin** is not used in version 1. It comes from Anthropic's `knowledge-work-plugins` marketplace; if your claude.ai organization provides it, it is already in `claude plugin list`.

## Step 5: check that everything is ready

Run these three commands in a new terminal (from anywhere; inside a project that is set up for the factory, add `--project .` to each):

```bash
sw-factory config check
sw-factory skills check --host claude
sw-factory skills check --host codex
```

- `sw-factory config check` prints `config ok`.
- Each `sw-factory skills check` lists the required skills as `present` or `missing` and ends with `skills ok` (exit code 0) or `skills not ready` (exit code 1). A missing skill shows its fallback, if it has one, and whether that fallback is installed. Install what is missing (step 4) and run the check again.

`sw-factory skills check` looks only at the third-party skills. To check the factory's own plugin, use `claude plugin list` and `codex plugin list --marketplace bsri-factory`.

## Updating

Keep the command and the plugin on the same version: `sw-factory --version` and the version that `claude plugin list` and `codex plugin list` show.

- **The command:** run `git pull` in the clone. The editable install uses the new code at once. Only if `tools/pyproject.toml` changed, install it again: `pipx install --force -e <clone>/tools`, or, without pipx, the `pip install -e tools` line of step 1.
- **Claude Code:** `claude plugin marketplace update bsri-factory`, then `claude plugin update bsri-sw-factory@bsri-factory`, then restart Claude Code.
- **Codex:** `codex plugin marketplace upgrade bsri-factory`, then check the version with `codex plugin list --marketplace bsri-factory`. If it still shows the old version, remove and add the plugin again: `codex plugin remove bsri-sw-factory@bsri-factory`, then `codex plugin add bsri-sw-factory@bsri-factory`.

## Upgrading from 0.2.0

Version 0.3.0 renamed the largest track from Program to Epic (decision F17 in `docs/decisions.md`): the track is `epic`, its work ids are `EPIC-<n>` and its templates are in `templates/work/epic/`. The command, the plugin and the skills keep their names, and Fix and Feature work items carry on as they are. If you installed 0.2.0:

1. **Before you update, finish any open `PRG-<n>` items with 0.2.0.** 0.3.0 does not read them, and once you run `git pull` in the clone the editable install is already 0.3.0 (there is no 0.2.0 tag to go back to). An item you cannot finish first has to be opened again as an Epic after the update (`sw-factory work new --track epic …`), with its files carried over by hand.
2. **Update** the command and the plugin as [Updating](#updating) says; `sw-factory --version` then prints `0.3.0`.
3. **`REVIEW.md`**, in each project set up with 0.2.0: under `tracks:`, rename the `program:` key to `epic:` and keep its lenses. Until you do, `sw-factory config check --project .` reports `unknown track 'program'`, and every review panel fails, on every track, because the panel cannot load `REVIEW.md`.
4. **`CLAUDE.md` and `AGENTS.md`**, in each such project: in the factory's section, between `<!-- sw-factory:begin -->` and `<!-- sw-factory:end -->`, change `PRG-<n>-<slug>` to `EPIC-<n>-<slug>`. Setup cannot refresh the section: it runs once per project and stops when `sw-factory.yaml` exists.

## Upgrading from 0.1.0

Version 0.2.0 renamed the factory (decision F16 in `docs/decisions.md`): the command `factory` is now `sw-factory`, the plugin `software-factory` is now `bsri-sw-factory`, its skills and subagents lost their `factory-` prefix, and what it creates in a project is `sw-factory.yaml` and `sw-factory/`. If you installed 0.1.0:

1. **The command.** Run `git pull` in the clone, then install the command again so that `sw-factory` exists:
   - with pipx: `pipx install --force -e <clone>/tools`;
   - without pipx: in the clone, `.venv/Scripts/python -m pip install -e tools` (Linux or macOS: `.venv/bin/python -m pip install -e tools`), then on Windows replace the copied `factory.exe` in your command folder with `<clone>\.venv\Scripts\sw-factory.exe`, and on Linux or macOS replace the old link with `ln -s <clone>/.venv/bin/sw-factory ~/.local/bin/sw-factory` (and delete `~/.local/bin/factory`).

   `sw-factory --version` prints the current version, `0.3.0`. If an old `factory` command is still on the PATH, delete it.
2. **Claude Code.** Remove the old plugin, refresh the marketplace and install the new one, then restart Claude Code:

   ```bash
   claude plugin uninstall software-factory@bsri-factory
   claude plugin marketplace update bsri-factory
   claude plugin install bsri-sw-factory@bsri-factory
   ```
3. **Codex.** The same, then start a new Codex session:

   ```bash
   codex plugin remove software-factory@bsri-factory
   codex plugin marketplace upgrade bsri-factory
   codex plugin add bsri-sw-factory@bsri-factory
   ```
4. **The staging folder.** Move `~/.factory` to `~/.sw-factory` (`mv ~/.factory ~/.sw-factory`; PowerShell: `Move-Item "$HOME\.factory" "$HOME\.sw-factory"`), and change the `writable_roots` entry in `~/.codex/config.toml` to the new folder (see Step 3 above).
5. **Projects set up with 0.1.0 are not supported.** They hold `factory.yaml`, a `factory/` folder, `factory/<ID>-<slug>` branches and `<!-- factory:begin -->` sections, none of which 0.2.0 or later reads: `sw-factory` finds no project there, and setup would not notice the old `factory.yaml` or the old markers. Set the project up again on a fresh copy of it, and carry any open work over by hand.

## Troubleshooting

| Problem | What to do |
|---|---|
| A skill says `sw-factory` was not found | The folder holding the `sw-factory` command (pipx's `~/.local/bin`, or the folder you copied it to) is not on the PATH the host sees (step 1). Fix the PATH, then restart the host. |
| A project's `python` (its verify command, or the merge gate's full verify) runs in the factory's virtual environment: `merge-gate.md` shows a Python path inside the factory's clone | The factory's `.venv\Scripts` (or `.venv/bin`) folder is on the PATH ahead of your own Python. Take it off the PATH and install the command as step 1 says, so that only `sw-factory` is on the PATH; `where python` (`which -a python`) then lists your own Python first. |
| In a Codex session, writing a prompt, brief or quote file fails with `Access to the path '…\.sw-factory\staging\…' is denied.`, or Codex asks for more permissions for each such file | The staging folder is outside Codex's writable roots. Add `~/.sw-factory` to `writable_roots` in `~/.codex/config.toml` (step 3), then start a new Codex session. |
| A session spawns a bare `lens-reviewer`, `tester`, `doc-reviewer` or `researcher`, and gets "no such agent" or an agent that is not the factory's | Installed as a plugin (the normal case), the factory's subagents are named `bsri-sw-factory:<name>`, for example `bsri-sw-factory:lens-reviewer`, and the skills always spawn that name: a bare name can resolve to another plugin's agent or your own. If a session tries the bare name, tell it the plugin-qualified one. Bare names are right only when the factory's `agents/` files are installed directly, not through the plugin. `/agents` in Claude Code lists the subagents it has. |
| `claude plugin marketplace add` or `codex plugin marketplace add` cannot read the repository | You lack access to `Battlespace-Research/software-factory`, or git has no credentials for GitHub. Check with `git ls-remote` (see "Before you start"). |
| A review run fails on Windows with a Codex sandbox error | Set the environment variable `FACTORY_CODEX_ARGS` to `-c windows.sandbox="unelevated"`. The factory adds it to every `codex exec` run it starts and notes it in each review file it writes. |
| `sw-factory role run` stops with `FACTORY_CODEX_ARGS: '…' is not allowed` (or `FACTORY_CODEX_ARGS: the -c override of '…' is not allowed`) | The variable may carry `-c key=value` settings and other options, but none that would replace what the factory sets for each run: `-s`/`--sandbox`, `-a`/`--ask-for-approval`, `--full-auto`, `--approve-for-me`, `--dangerously-bypass…`, `--yolo`, `--add-dir`, `-o`/`--output-last-message`, `--output-schema`, `-C`/`--cd`. The same goes for a `-c`/`--config` setting of the keys `sandbox_mode`, `approval_policy`, `sandbox_workspace_write…` (such as `writable_roots` or `network_access`), `sandbox_permissions` or `shell_environment_policy…`, in any spelling (`-c k=v`, `-c=k=v`, `-ck=v`, `--config k=v`, `--config=k=v`; case, spaces and quotes do not matter). Other keys, such as `windows.sandbox`, are fine. Take the refused one out of the variable. |
| `sw-factory skills check` misses a skill you know is installed in an unusual folder | Set `FACTORY_SKILL_PATHS` to that folder (several folders are separated by `;` on Windows and `:` elsewhere). This only widens the check; it does not change what the host loads. |
| You want the command to use another clone of the factory | Set `FACTORY_HOME` to that clone's folder. |
