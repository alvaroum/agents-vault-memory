#!/usr/bin/env python3
"""Update an existing vault install of Agents Vault Memory."""

from __future__ import annotations

import argparse

try:
    from agents_vault_memory.bootstrap_memory_system import build_parser, install_to_vault
except ImportError:
    from bootstrap_memory_system import build_parser, install_to_vault


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.description = "Update an existing vault install of the AI memory system."
    for action in parser._actions:
        if "--force" in action.option_strings:
            action.help = argparse.SUPPRESS
    args = parser.parse_args(argv)
    args.force = True
    return install_to_vault(args)


if __name__ == "__main__":
    raise SystemExit(main())
