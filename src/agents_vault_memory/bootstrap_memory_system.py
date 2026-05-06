#!/usr/bin/env python3
"""Install the session-based memory system into another Obsidian vault."""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import shutil
import subprocess
import sys


START_MARKER = "<!-- BEGIN AI MEMORY SYSTEM -->"
END_MARKER = "<!-- END AI MEMORY SYSTEM -->"
SCRIPT_FILES = [
    "session_memory_sync.py",
    "session_memory_parsers.py",
    "install_memory_launch_agent.py",
    "local.obsidian-memory-sync.plist.template",
    "bootstrap_memory_system.py",
]
MEMORY_DIRS = [
    "40. Memory/annual",
    "40. Memory/daily",
    "40. Memory/logs",
    "40. Memory/monthly",
    "40. Memory/sessions",
    "40. Memory/state",
    "40. Memory/weekly",
    "40. Memory/scripts",
]


def now_stamp() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).strftime("%Y-%m-%d %H:%M")


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug or "vault"


def default_label(vault_root: pathlib.Path) -> str:
    return f"local.obsidian-memory-sync.{slugify(vault_root.name)}"


def write_text(path: pathlib.Path, text: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return True


def copy_file(src: pathlib.Path, dst: pathlib.Path, force: bool) -> bool:
    if dst.exists() and not force:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def upsert_managed_block(path: pathlib.Path, block: str) -> None:
    replacement = f"{START_MARKER}\n{block.rstrip()}\n{END_MARKER}\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = ""
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(replacement, existing)
    elif existing.strip():
        updated = existing.rstrip() + "\n\n" + replacement
    else:
        updated = replacement
    path.write_text(updated, encoding="utf-8")


def render_readme() -> str:
    created = now_stamp()
    return f"""---
type: ai_memory_readme
created: {created}
last_modified: {created}
tags:
  - ai
  - memory
---

# AI Memory System

This folder stores AI working memory in a session-based structure:

1. Native full histories remain in the agent home directories.
2. Compact session memory cards live in `40. Memory/sessions/`.
3. Daily, weekly, monthly, and annual notes are derived from those session cards.

## Retrieval Policy

- Default load path: `Active Context` -> yesterday daily summary.
- Load relevant recent session cards only when needed.
- Load `weekly`, `monthly`, or `annual` only when needed.
- Load legacy archival logs only when detail, provenance, or exact wording is required and a session card is not enough.

## Sync Policy

1. Native session histories are the evidence layer:
   - Codex: `~/.codex/sessions/`
   - Claude Code: `~/.claude/projects/`
   - Gemini CLI: `~/.gemini/tmp/`
   - Opencode: `~/.local/share/opencode/`
2. `40. Memory/scripts/session_memory_sync.py` watches for sessions that have stopped changing and writes one compact session card per chat into `40. Memory/sessions/YYYY-MM-DD/`.
3. After syncing a session card, the script rebuilds the relevant daily note and regenerates `Active Context.md`.
4. Weekly, monthly, and annual rollups remain derived from daily notes.
5. `40. Memory/logs/` is a legacy or archival layer only, not the default memory layer.

## Sync Latency

- This is an idle-based watcher, not a true close hook.
- By default, the launch agent runs the watcher every `60s` and only processes sessions that have been idle for at least `180s`.
- In practice, a closed chat is usually captured about 1 to 4 minutes after its last write.

## Independence

- This vault install is self-contained after installation.
- It does not need to stay connected to the development repository.
- To refresh this vault to a newer release, re-run the external installer against this vault.

## Commands

One-shot sync:

```bash
python3 "40. Memory/scripts/session_memory_sync.py" --vault-root "$PWD"
```

Continuous watcher:

```bash
python3 "40. Memory/scripts/session_memory_sync.py" --vault-root "$PWD" --watch
```

Install or load the macOS launch agent:

```bash
python3 "40. Memory/scripts/install_memory_launch_agent.py" --vault-root "$PWD" --load
```

Install this same memory system into another vault:

```bash
python3 "40. Memory/scripts/bootstrap_memory_system.py" --target-vault "/path/to/Other Vault"
```
"""


def render_memory_hub() -> str:
    created = now_stamp()
    return f"""---
type: ai_memory_hub
created: {created}
last_modified: {created}
tags:
  - ai
  - memory
  - dashboard
---

# Memory Hub

> [!info] Default retrieval
> Read in order: `AGENTS.md` -> `Active Context` -> yesterday daily summary.
> Open session cards only when the daily note is not enough.

## Latest Session Cards

```dataview
TABLE date, platform, summary
FROM "40. Memory/sessions"
WHERE type = "ai_session_memory"
SORT last_updated_at DESC
LIMIT 20
```

## Latest Daily Summaries

```dataview
TABLE date, session_count, length(key_decisions) AS decisions, length(open_loops) AS open_loops
FROM "40. Memory/daily"
WHERE type = "ai_memory_daily"
SORT date DESC
LIMIT 14
```

## Latest Weekly Summaries

```dataview
TABLE week, period_start, period_end, length(key_decisions) AS decisions
FROM "40. Memory/weekly"
WHERE type = "ai_memory_weekly"
SORT week DESC
LIMIT 12
```

## Latest Monthly Summaries

```dataview
TABLE month, length(key_decisions) AS decisions, length(courses_touched) AS courses
FROM "40. Memory/monthly"
WHERE type = "ai_memory_monthly"
SORT month DESC
LIMIT 12
```

## Annual Recaps

```dataview
TABLE year, length(top_outcomes) AS outcomes, length(major_open_loops_for_next_year) AS open_loops
FROM "40. Memory/annual"
WHERE type = "ai_memory_annual_recap"
SORT year DESC
```

## Legacy Archival Logs

`40. Memory/logs/` is now a legacy or archival layer only.
Use it only when a session card or daily summary is insufficient and exact transcript evidence is required.
"""


def render_active_context() -> str:
    rebuilt = now_stamp()
    return f"""---
type: ai_active_context
last_rebuilt: {rebuilt}
active_projects: []
open_loops: []
stable_preferences: []
operating_constraints: []
priority_this_week: []
recent_sessions: []
tags:
  - ai
  - memory
  - active-context
---

# Active Context

## Active Projects

- 

## Open Loops

- 

## Stable Preferences

- 

## Operating Constraints

- 

## Priority This Week

- 

## Recent Sessions

- 
"""


def render_agents_block() -> str:
    return """## AI Memory System

- Default retrieval order: `AGENTS.md` -> `40. Memory/Active Context.md` -> the most recent available daily summary in `40. Memory/daily/`.
- Load recent session cards in `40. Memory/sessions/YYYY-MM-DD/` only when the daily note is not enough.
- Weekly, monthly, and annual notes are secondary rollups, not the default context layer.
- Native transcripts stay outside the vault in the agent stores:
  - Codex: `~/.codex/sessions/`
  - Claude Code: `~/.claude/projects/`
  - Gemini CLI: `~/.gemini/tmp/`
  - Opencode: `~/.local/share/opencode/`
- The vault-local watcher is `40. Memory/scripts/session_memory_sync.py`.
- This watcher is idle-based, not a true close hook. By default it checks every `60s` and only processes sessions after `180s` of inactivity.
- The launch agent installer is `40. Memory/scripts/install_memory_launch_agent.py`.
- The replication installer for other vaults is `40. Memory/scripts/bootstrap_memory_system.py`.
- Each vault is an independent install. The upstream repository is for future development and release management only.
"""


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the AI memory system into another vault.")
    parser.add_argument("--target-vault", required=True, help="Target Obsidian vault path.")
    parser.add_argument(
        "--label",
        default=None,
        help="launchd label to use if loading the watcher. Defaults to a label derived from the vault name.",
    )
    parser.add_argument(
        "--load-launch-agent",
        action="store_true",
        help="Install and load the launch agent in the target vault after copying files.",
    )
    parser.add_argument(
        "--run-initial-sync",
        action="store_true",
        help="Run a one-shot sync in the target vault after installation.",
    )
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=30,
        help="Days to scan when --run-initial-sync is used.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing managed files in the target vault.",
    )
    parser.add_argument(
        "--skip-agents",
        action="store_true",
        help="Do not add or update the managed AI memory block in the target AGENTS.md.",
    )
    return parser


def install_to_vault(args: argparse.Namespace) -> int:
    source_scripts = pathlib.Path(__file__).resolve().parent
    target_vault = pathlib.Path(args.target_vault).expanduser().resolve()
    label = args.label or default_label(target_vault)

    installed: list[pathlib.Path] = []
    for rel_dir in MEMORY_DIRS:
        (target_vault / rel_dir).mkdir(parents=True, exist_ok=True)
    for filename in SCRIPT_FILES:
        src = source_scripts / filename
        dst = target_vault / "40. Memory" / "scripts" / filename
        if copy_file(src, dst, args.force):
            installed.append(dst)

    note_map = {
        target_vault / "40. Memory" / "README.md": render_readme(),
        target_vault / "40. Memory" / "Memory Hub.md": render_memory_hub(),
        target_vault / "40. Memory" / "Active Context.md": render_active_context(),
    }
    for path, content in note_map.items():
        if write_text(path, content, args.force):
            installed.append(path)

    if not args.skip_agents:
        upsert_managed_block(target_vault / "AGENTS.md", render_agents_block())
        installed.append(target_vault / "AGENTS.md")

    if args.run_initial_sync:
        sync_script = target_vault / "40. Memory/scripts/session_memory_sync.py"
        code, out, err = run(
            [
                sys.executable,
                str(sync_script),
                "--vault-root",
                str(target_vault),
                "--since-days",
                str(max(args.backfill_days, 1)),
            ]
        )
        if code != 0:
            print(err or out, file=sys.stderr)
            return code

    if args.load_launch_agent:
        installer = target_vault / "40. Memory/scripts/install_memory_launch_agent.py"
        code, out, err = run(
            [
                sys.executable,
                str(installer),
                "--vault-root",
                str(target_vault),
                "--label",
                label,
                "--load",
            ]
        )
        if code != 0:
            print(err or out, file=sys.stderr)
            return code

    print(f"Installed memory system into: {target_vault}")
    print(f"Recommended launchd label: {label}")
    if installed:
        print("Files created or updated:")
        for path in installed:
            print(path)
    else:
        print("No files were overwritten. Re-run with --force to refresh managed files.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return install_to_vault(args)


if __name__ == "__main__":
    raise SystemExit(main())
