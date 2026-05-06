#!/usr/bin/env python3
"""Registry of vault installs known to the hook dispatcher."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any


def config_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return pathlib.Path(base).expanduser() / "agents-vault-memory"
    return pathlib.Path.home() / ".config" / "agents-vault-memory"


def registry_path() -> pathlib.Path:
    return config_dir() / "registry.json"


def normalize_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.expanduser(str(path))).resolve()


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {"version": 1, "vaults": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "vaults": []}
    if not isinstance(data, dict):
        return {"version": 1, "vaults": []}
    data.setdefault("version", 1)
    data.setdefault("vaults", [])
    if not isinstance(data["vaults"], list):
        data["vaults"] = []
    return data


def save_registry(data: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def register_vault(vault_root: pathlib.Path) -> None:
    root = normalize_path(vault_root)
    sync_script = root / "40. Memory" / "scripts" / "session_memory_sync.py"
    entry = {
        "vault_root": str(root),
        "sync_script": str(sync_script),
    }
    data = load_registry()
    kept: list[dict[str, Any]] = []
    seen = False
    for item in data["vaults"]:
        if not isinstance(item, dict):
            continue
        if str(item.get("vault_root") or "") == str(root):
            kept.append(entry)
            seen = True
        else:
            kept.append(item)
    if not seen:
        kept.append(entry)
    kept.sort(key=lambda item: str(item.get("vault_root") or ""))
    data["vaults"] = kept
    save_registry(data)


def find_vault_from_cwd(cwd: pathlib.Path | None) -> dict[str, Any] | None:
    if cwd is None:
        return None
    current = normalize_path(cwd)
    for candidate in (current, *current.parents):
        sync_script = candidate / "40. Memory" / "scripts" / "session_memory_sync.py"
        if sync_script.exists():
            return {
                "vault_root": str(candidate),
                "sync_script": str(sync_script),
            }
    return None


def matching_vault(cwd: pathlib.Path | None) -> dict[str, Any] | None:
    direct = find_vault_from_cwd(cwd)
    if direct is not None:
        return direct
    if cwd is None:
        return None
    current = normalize_path(cwd)
    best: dict[str, Any] | None = None
    best_len = -1
    for item in load_registry().get("vaults", []):
        if not isinstance(item, dict):
            continue
        root_text = str(item.get("vault_root") or "")
        if not root_text:
            continue
        root = normalize_path(pathlib.Path(root_text))
        try:
            current.relative_to(root)
        except ValueError:
            continue
        root_len = len(str(root))
        if root_len > best_len:
            best = item
            best_len = root_len
    return best
