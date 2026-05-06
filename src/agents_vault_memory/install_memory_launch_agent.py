#!/usr/bin/env python3
"""Install the memory sync launch agent for this vault."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def extra_argument_xml(args: argparse.Namespace) -> str:
    items: list[str] = []
    if args.since_days is not None:
        items.extend(["--since-days", str(args.since_days)])
    if args.idle_seconds is not None:
        items.extend(["--idle-seconds", str(args.idle_seconds)])
    if args.poll_seconds is not None:
        items.extend(["--poll-seconds", str(args.poll_seconds)])
    if not items:
        return ""
    return "\n".join(f"      <string>{item}</string>" for item in items)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the vault memory sync launch agent.")
    parser.add_argument("--vault-root", default=".", help="Vault root path.")
    parser.add_argument(
        "--label",
        default="local.obsidian-memory-sync",
        help="launchd label and plist basename.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output plist path. Defaults to ~/Library/LaunchAgents/<label>.plist",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="Load the launch agent immediately after writing the plist.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Optional watcher scan window override.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=None,
        help="Optional watcher idle threshold override.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=None,
        help="Optional watcher poll interval override.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    vault_root = pathlib.Path(args.vault_root).resolve()
    label = args.label
    template = vault_root / "40. Memory/scripts/local.obsidian-memory-sync.plist.template"
    if not template.exists():
        print(f"ERROR: template not found: {template}", file=sys.stderr)
        return 2

    output = (
        pathlib.Path(args.output).expanduser().resolve()
        if args.output
        else (pathlib.Path.home() / "Library/LaunchAgents" / f"{label}.plist").resolve()
    )
    content = template.read_text(encoding="utf-8").replace("__VAULT_ROOT__", str(vault_root))
    content = content.replace("local.obsidian-memory-sync", label)
    content = content.replace("__EXTRA_ARGUMENTS__", extra_argument_xml(args))
    write_text(output, content)

    if not args.load:
        print(output)
        return 0

    run(["launchctl", "unload", str(output)])
    code, out, err = run(["launchctl", "load", "-w", str(output)])
    if code != 0:
        print(err or out, file=sys.stderr)
        return code
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
