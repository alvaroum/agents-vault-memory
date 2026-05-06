# Agents Vault Memory

Session-based AI memory sync for Obsidian vaults used with local coding agents.

It reads native chat histories from:

- Codex
- Claude Code
- Gemini CLI
- Opencode

and writes compact, auditable memory back into each vault as:

- `40. Memory/sessions/YYYY-MM-DD/<platform>-<session-id>.md`
- `40. Memory/daily/YYYY-MM-DD.md`
- `40. Memory/Active Context.md`

## Intended Model

This repository is for development and distribution of the memory system.

Your vaults should remain independent installs:

- no git repo connection is required inside a vault
- no runtime dependency on the repo path is required after install
- each vault gets its own local copy of the runtime scripts
- updates are explicit: you choose when to refresh a vault from a newer package version
- optional agent hook integrations are installed separately at the user level and route events to the correct vault automatically

That means:

- the repo is the source of truth for future development
- each vault is a self-contained deployed instance

## What It Is For

Use this if you:

- work directly inside one or more Obsidian vaults with terminal agents
- switch between Codex, Claude Code, Gemini CLI, and Opencode
- want a shared vault-native memory layer instead of agent-specific chat silos
- want automatic memory updates after a chat ends or goes idle

## How It Works

1. Native transcripts stay in the agents' own local stores.
2. A vault-local watcher scans those stores and selects only sessions that belong to that vault workspace.
3. After a session has stopped changing for long enough, the watcher creates or updates one compact session card.
4. The same sync pass rebuilds the daily summary and `Active Context.md`.
5. Weekly, monthly, and annual notes remain downstream rollups from daily notes.

By default this is an idle-based watcher, not a true close hook:

- idle threshold: `180s`
- poll interval: `60s`

In practice, a closed chat is usually captured about 1 to 4 minutes after its last write.

## Supported Agent Stores

- Codex: `~/.codex/sessions/`
- Claude Code: `~/.claude/projects/`
- Gemini CLI: `~/.gemini/tmp/`
- Opencode: `~/.local/share/opencode/`

## Installation

`uv` is the recommended install method.

### Recommended: `uv tool install`

Install directly from GitHub:

```bash
uv tool install git+https://github.com/alvaroum/agents-vault-memory.git
```

If needed, ensure the tool directory is on your `PATH`:

```bash
uv tool update-shell
```

### Alternative: `pip`

```bash
python3 -m pip install git+https://github.com/alvaroum/agents-vault-memory.git
```

### Optional: `pipx`

If you use `pipx`, the equivalent install model also works:

```bash
pipx install git+https://github.com/alvaroum/agents-vault-memory.git
```

### Development Install

If you want to work on the tool itself:

```bash
git clone https://github.com/alvaroum/agents-vault-memory.git
cd agents-vault-memory
python3 -m pip install -e .
```

## Commands

After installation, these commands are available:

```bash
agents-vault-memory-bootstrap
agents-vault-memory-update
agents-vault-memory-sync
agents-vault-memory-install-launch-agent
agents-vault-memory-install-hooks
agents-vault-memory-hook-dispatch
```

## Quick Start

### 1. Install into a Vault

```bash
agents-vault-memory-bootstrap \
  --target-vault "/path/to/Your Vault"
```

This creates:

- `40. Memory/`
- vault-local runtime scripts in `40. Memory/scripts/`
- `README.md`, `Memory Hub.md`, and `Active Context.md`
- a managed AI memory block in `AGENTS.md`

### 2. Optional: Backfill Recent Sessions

```bash
agents-vault-memory-bootstrap \
  --target-vault "/path/to/Your Vault" \
  --run-initial-sync \
  --backfill-days 30
```

### 3. Enable Background Sync on macOS

Use a unique label per vault:

```bash
agents-vault-memory-install-launch-agent \
  --vault-root "/path/to/Your Vault" \
  --label "local.obsidian-memory-sync.my-vault" \
  --load
```

You can also tune the watcher:

```bash
agents-vault-memory-install-launch-agent \
  --vault-root "/path/to/Your Vault" \
  --label "local.obsidian-memory-sync.my-vault" \
  --idle-seconds 120 \
  --poll-seconds 30 \
  --since-days 14 \
  --load
```

### 4. Run a One-Shot Sync

```bash
agents-vault-memory-sync --vault-root "/path/to/Your Vault"
```

### 5. Install Global Agent Hooks

This is the close-hook layer.

```bash
agents-vault-memory-install-hooks
```

You can also install only a subset:

```bash
agents-vault-memory-install-hooks --agents claude gemini codex
```

What this configures:

- Claude Code: `SessionEnd` hook
- Gemini CLI: `SessionEnd` hook
- Codex: `Stop` hook, because Codex currently exposes `Stop` rather than `SessionEnd`
- OpenCode: global plugin listening to `session.idle`

Important:

- Claude and Gemini have true session-end lifecycle hooks.
- Codex does not currently expose a true `SessionEnd` hook in the same way, so this package uses `Stop` as the closest automatic trigger.
- OpenCode uses its plugin event system; the closest stable event for this workflow is `session.idle`.

## Updating an Existing Vault

Use the dedicated update command:

```bash
agents-vault-memory-update \
  --target-vault "/path/to/Your Vault"
```

This refreshes the managed files in the target vault from the currently installed package version.

If you also want to rerun backfill or reload the launch agent:

```bash
agents-vault-memory-update \
  --target-vault "/path/to/Your Vault" \
  --run-initial-sync \
  --backfill-days 14 \
  --load-launch-agent
```

## Updating the Installed Tool

### With `uv`

```bash
uv tool upgrade agents-vault-memory
```

### With `pip`

```bash
python3 -m pip install --upgrade git+https://github.com/alvaroum/agents-vault-memory.git
```

### With `pipx`

```bash
pipx upgrade agents-vault-memory
```

Typical flow:

1. Upgrade the installed tool.
2. Run `agents-vault-memory-update --target-vault "/path/to/Your Vault"` for each vault you want to refresh.
3. If hook behavior changed in the release, re-run `agents-vault-memory-install-hooks`.

## Multi-Vault Use

This project is designed for multiple vaults.

Each vault keeps its own:

- `40. Memory/` folder
- `Active Context.md`
- session cards
- daily summaries
- sync state
- `launchd` label

The same machine can run one watcher per vault, as long as each launch agent uses a different label.

## What Gets Stored in the Vault

Stored in the vault:

- concise session memory cards
- daily and active-context summaries
- sync state and local watcher logs

Not stored in the vault by default:

- full Codex transcripts
- full Claude Code transcripts
- full Gemini CLI transcripts
- full Opencode transcripts

Those remain in the native agent stores and act as the evidence layer.

## Current Scope

Supported well:

- per-vault memory separation
- automatic idle-based sync
- shared memory across multiple agent frontends
- macOS background automation
- explicit install and update flows without repo-vault coupling
- close or near-close hook routing for supported agents

Not yet implemented:

- a true close hook for every agent frontend
- first-class Linux service installers
- first-class Windows service installers
- Homebrew packaging

## Repository Layout

```text
agents-vault-memory/
├── README.md
├── LICENSE
├── pyproject.toml
└── src/agents_vault_memory/
    ├── __init__.py
    ├── bootstrap_memory_system.py
    ├── hook_dispatch.py
    ├── install_agent_hooks.py
    ├── install_memory_launch_agent.py
    ├── local.obsidian-memory-sync.plist.template
    ├── registry.py
    ├── session_memory_parsers.py
    ├── session_memory_sync.py
    └── update_vault_install.py
```

## License

MIT
