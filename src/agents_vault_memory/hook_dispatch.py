#!/usr/bin/env python3
"""Dispatch an agent hook event to the correct vault-local sync script."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

try:
    from agents_vault_memory.registry import matching_vault, normalize_path
except ImportError:
    from registry import matching_vault, normalize_path


def read_stdin_json() -> dict[str, Any]:
    if sys.stdin.closed:
        return {}
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def candidate_cwds(args: argparse.Namespace, payload: dict[str, Any]) -> list[pathlib.Path]:
    values: list[str] = []
    if args.cwd:
        values.append(args.cwd)
    values.append(os.getcwd())
    for env_name in (
        "CLAUDE_PROJECT_DIR",
        "GEMINI_PROJECT_DIR",
        "GEMINI_CWD",
        "PWD",
    ):
        value = os.environ.get(env_name)
        if value:
            values.append(value)
    for key in ("cwd", "directory", "worktree"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    paths: list[pathlib.Path] = []
    seen: set[str] = set()
    for value in values:
        try:
            path = normalize_path(pathlib.Path(value))
        except OSError:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def resolve_vault(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any] | None:
    for cwd in candidate_cwds(args, payload):
        match = matching_vault(cwd)
        if match is not None:
            return match
    return None


def run_sync(vault: dict[str, Any], agent: str) -> int:
    sync_script = pathlib.Path(str(vault.get("sync_script") or ""))
    vault_root = pathlib.Path(str(vault.get("vault_root") or ""))
    if not sync_script.exists():
        return 0
    cmd = [
        sys.executable,
        str(sync_script),
        "--vault-root",
        str(vault_root),
        "--source",
        agent,
        "--since-days",
        "7",
        "--include-active",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dispatch a hook event to the matching vault.")
    parser.add_argument("--agent", required=True, choices=["codex", "claude", "gemini", "opencode"])
    parser.add_argument("--cwd", default=None, help="Explicit workspace path override.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = read_stdin_json()
    vault = resolve_vault(args, payload)
    if vault is None:
        return 0
    return 0 if run_sync(vault, args.agent) in {0, 1} else 1


if __name__ == "__main__":
    raise SystemExit(main())
