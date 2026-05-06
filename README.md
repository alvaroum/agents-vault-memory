# Agents Vault Memory

Session-based AI memory sync for Obsidian vaults used with local coding agents.

It watches native conversation histories from:

- Codex
- Claude Code
- Gemini CLI
- Opencode

and writes compact, auditable memory back into each vault as:

- `40. Memory/sessions/YYYY-MM-DD/<platform>-<session-id>.md`
- `40. Memory/daily/YYYY-MM-DD.md`
- `40. Memory/Active Context.md`

The goal is not to keep every raw transcript inside the vault. The goal is to preserve durable context, outcomes, open loops, and provenance so any agent can resume work from the vault itself.

## What This Is For

This project is meant for people who:

- work directly inside one or more Obsidian vaults with terminal agents
- switch between Codex, Claude Code, Gemini CLI, and Opencode
- want a shared vault-native memory layer instead of agent-specific chat silos
- want automatic memory updates after a session ends or goes idle

It is especially useful when each vault represents a different domain and should keep its own isolated memory.

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

## Repository Layout

```text
agents-vault-memory/
├── README.md
├── LICENSE
├── pyproject.toml
└── src/agents_vault_memory/
    ├── __init__.py
    ├── bootstrap_memory_system.py
    ├── install_memory_launch_agent.py
    ├── local.obsidian-memory-sync.plist.template
    ├── session_memory_parsers.py
    └── session_memory_sync.py
```

## Requirements

- Python `3.10+`
- macOS if you want background automation via `launchd`
- an Obsidian vault
- local agent histories already present for one or more supported agents

## Installation

### Option 1: Run Directly From the Repo

Clone the repository and run the bootstrap script with Python:

```bash
git clone <your-repo-url>
cd agents-vault-memory
python3 src/agents_vault_memory/bootstrap_memory_system.py --target-vault "/path/to/Your Vault"
```

If you want reusable CLI commands, install it into a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### Option 2: Install the CLI Commands

After `pip install -e .`, these commands are available:

```bash
agents-vault-memory-bootstrap --target-vault "/path/to/Your Vault"
agents-vault-memory-sync --vault-root "/path/to/Your Vault"
agents-vault-memory-install-launch-agent --vault-root "/path/to/Your Vault" --load
```

## Quick Start

### 1. Bootstrap a Vault

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

### 4. Run a One-Shot Sync Manually

```bash
agents-vault-memory-sync --vault-root "/path/to/Your Vault"
```

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

## Updating an Existing Vault Install

Re-run bootstrap with `--force`:

```bash
agents-vault-memory-bootstrap \
  --target-vault "/path/to/Your Vault" \
  --force
```

That refreshes the managed scripts and notes without requiring a manual reinstall.

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

Not yet implemented:

- a true close hook for every agent frontend
- first-class Linux service installers
- first-class Windows service installers

## Notes

- Vault-local runtime copies are intentional. Each vault should remain usable by any agent even if the original repo is moved elsewhere.
- The source of truth for the installer and sync logic should live in this repository, not be edited independently in each vault.

## License

MIT
