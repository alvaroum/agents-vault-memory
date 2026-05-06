#!/usr/bin/env python3
"""Install agent hook integrations for Agents Vault Memory."""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import shutil
import sys
from typing import Any


HOOK_NAME = "agents-vault-memory-session-sync"
OPENCODE_PLUGIN_NAME = "agents-vault-memory.js"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dispatch_argv() -> list[str]:
    resolved = shutil.which("agents-vault-memory-hook-dispatch")
    if resolved:
        return [resolved]
    return [sys.executable, "-m", "agents_vault_memory.hook_dispatch"]


def hook_command(agent: str) -> str:
    return shlex.join(dispatch_argv() + ["--agent", agent])


def upsert_hook_rule(entries: list[Any], command: str, matcher: str | None = None) -> list[Any]:
    hook_entry = {
        "name": HOOK_NAME,
        "type": "command",
        "command": command,
        "timeout": 30000,
    }
    normalized: list[dict[str, Any]] = [item for item in entries if isinstance(item, dict)]
    for item in normalized:
        hooks = item.get("hooks")
        if not isinstance(hooks, list):
            continue
        for existing in hooks:
            if not isinstance(existing, dict):
                continue
            if existing.get("name") == HOOK_NAME:
                existing.update(hook_entry)
                if matcher is not None:
                    item["matcher"] = matcher
                elif "matcher" in item:
                    item.pop("matcher", None)
                return normalized
    rule: dict[str, Any] = {"hooks": [hook_entry]}
    if matcher is not None:
        rule["matcher"] = matcher
    normalized.append(rule)
    return normalized


def install_claude_hook() -> pathlib.Path:
    path = pathlib.Path.home() / ".claude" / "settings.json"
    data = read_json(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    hooks["SessionEnd"] = upsert_hook_rule(
        hooks.get("SessionEnd", []) if isinstance(hooks.get("SessionEnd"), list) else [],
        hook_command("claude"),
    )
    write_json(path, data)
    return path


def install_gemini_hook() -> pathlib.Path:
    path = pathlib.Path.home() / ".gemini" / "settings.json"
    data = read_json(path)
    data["hooksConfig"] = {"enabled": True, "notifications": True}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    hooks["SessionEnd"] = upsert_hook_rule(
        hooks.get("SessionEnd", []) if isinstance(hooks.get("SessionEnd"), list) else [],
        hook_command("gemini"),
        matcher="*",
    )
    write_json(path, data)
    return path


def install_codex_hooks_json() -> pathlib.Path:
    path = pathlib.Path.home() / ".codex" / "hooks.json"
    data = read_json(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    hooks["Stop"] = upsert_hook_rule(
        hooks.get("Stop", []) if isinstance(hooks.get("Stop"), list) else [],
        hook_command("codex"),
    )
    write_json(path, data)
    return path


def enable_codex_feature() -> pathlib.Path:
    path = pathlib.Path.home() / ".codex" / "config.toml"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""
    lines = text.splitlines()
    output: list[str] = []
    in_features = False
    seen_features = False
    seen_codex_hooks = False
    inserted = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_features and not seen_codex_hooks:
                output.append('codex_hooks = true')
                seen_codex_hooks = True
                inserted = True
            in_features = stripped == "[features]"
            if in_features:
                seen_features = True
            output.append(line)
            continue
        if in_features and stripped.startswith("codex_hooks"):
            output.append("codex_hooks = true")
            seen_codex_hooks = True
            inserted = True
        else:
            output.append(line)
    if seen_features and not seen_codex_hooks:
        output.append("codex_hooks = true")
        inserted = True
    if not seen_features:
        if output and output[-1].strip():
            output.append("")
        output.append("[features]")
        output.append("codex_hooks = true")
        inserted = True
    if not inserted and not output:
        output = ["[features]", "codex_hooks = true"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return path


def install_opencode_plugin() -> pathlib.Path:
    path = pathlib.Path.home() / ".config" / "opencode" / "plugins" / OPENCODE_PLUGIN_NAME
    argv = dispatch_argv()
    command = argv[0]
    args = argv[1:] + ["--agent", "opencode"]
    content = f"""import {{ spawn }} from "node:child_process";

const COMMAND = {json.dumps(command)};
const BASE_ARGS = {json.dumps(args)};

export const AgentsVaultMemoryPlugin = async ({{ directory, worktree }}) => {{
  return {{
    event: async ({{ event }}) => {{
      if (event.type !== "session.idle") {{
        return;
      }}
      const cwd = event.directory || directory || worktree || process.cwd();
      const child = spawn(COMMAND, [...BASE_ARGS, "--cwd", cwd], {{
        detached: true,
        stdio: "ignore",
      }});
      child.unref();
    }},
  }};
}};
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install close or stop hook integrations for supported agents.")
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=["claude", "gemini", "codex", "opencode"],
        default=["claude", "gemini", "codex", "opencode"],
        help="Subset of agents to configure.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    touched: list[pathlib.Path] = []
    selected = set(args.agents)
    if "claude" in selected:
        touched.append(install_claude_hook())
    if "gemini" in selected:
        touched.append(install_gemini_hook())
    if "codex" in selected:
        touched.append(install_codex_hooks_json())
        touched.append(enable_codex_feature())
    if "opencode" in selected:
        touched.append(install_opencode_plugin())
    for path in touched:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
