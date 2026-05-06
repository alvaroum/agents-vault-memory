#!/usr/bin/env python3
"""Sync session-based AI memory cards for the current vault.

This watcher reads native session histories from supported local agents and
creates compact session memory cards inside the Obsidian vault:

- Codex
- Claude Code
- Gemini CLI
- Opencode

It keeps the vault memory lightweight by storing summaries and provenance,
while leaving full transcripts in the agents' native local stores.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import time
from typing import Any

try:
    from agents_vault_memory.session_memory_parsers import (
        codex_session_matches_workspace,
        encode_claude_project_dir,
        infer_file_modifications_from_tool_metadata,
        parse_claude_session_jsonl,
        parse_codex_session_jsonl,
        parse_gemini_session_json,
    )
except ImportError:
    from session_memory_parsers import (
        codex_session_matches_workspace,
        encode_claude_project_dir,
        infer_file_modifications_from_tool_metadata,
        parse_claude_session_jsonl,
        parse_codex_session_jsonl,
        parse_gemini_session_json,
    )


SESSION_ORDER = [
    "type",
    "date",
    "platform",
    "session_id",
    "source_path",
    "workspace",
    "started_at",
    "last_updated_at",
    "status",
    "summary",
    "files_touched",
    "deliverables",
    "open_loops",
    "durable_context_updates",
    "courses_touched",
    "contains_sensitive_data",
    "tags",
]

DAILY_ORDER = [
    "type",
    "date",
    "source_sessions",
    "platforms",
    "session_count",
    "what_mattered",
    "key_decisions",
    "completed_actions",
    "open_loops",
    "courses_touched",
    "people_touched",
    "key_files",
    "contains_sensitive_data",
    "created",
    "last_modified",
    "tags",
]

ACTIVE_ORDER = [
    "type",
    "last_rebuilt",
    "active_projects",
    "open_loops",
    "stable_preferences",
    "operating_constraints",
    "priority_this_week",
    "recent_sessions",
    "tags",
]


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(microsecond=0)


def now_local_str() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M")


def iso_from_ts(ts: float | int | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).astimezone().replace(
        microsecond=0
    ).isoformat()


def iso_from_ms(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return iso_from_ts(float(ms) / 1000.0)


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone()


def read_text(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def normalize_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.expanduser(str(path))).resolve()


def rel_to_vault(path_text: str, vault_root: pathlib.Path) -> str | None:
    if not path_text:
        return None
    try:
        path = normalize_path(pathlib.Path(path_text))
    except OSError:
        return None
    try:
        rel = path.relative_to(vault_root)
    except ValueError:
        return None
    return str(rel)


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return ""
    if re.match(r"^[A-Za-z0-9_./:[\]@+\-]+$", text):
        return text
    return '"' + text.replace('"', '\\"') + '"'


def render_frontmatter(data: dict[str, Any], order: list[str]) -> str:
    lines = ["---"]
    rendered = set()
    for key in order:
        rendered.add(key)
        value = data.get(key)
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                escaped = str(item).replace('"', '\\"')
                lines.append(f'  - "{escaped}"')
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    for key in sorted(k for k in data.keys() if k not in rendered):
        value = data[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                escaped = str(item).replace('"', '\\"')
                lines.append(f'  - "{escaped}"')
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def list_to_md(items: list[str]) -> str:
    if not items:
        return "- "
    return "\n".join(f"- {item}" for item in items)


def uniq(items: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = re.sub(r"\s+", " ", str(item)).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if limit is not None and len(out) >= limit:
            break
    return out


def truncate(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def looks_like_local_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    blocked_prefixes = (
        "<command-name>",
        "<local-command",
        "<cwd>",
        "<environment_context>",
    )
    return stripped.startswith(blocked_prefixes)


def is_injected_context(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    patterns = (
        "# AGENTS.md instructions for ",
        "<INSTRUCTIONS>",
        "<environment_context>",
        "<cwd>",
    )
    return any(stripped.startswith(pattern) for pattern in patterns)


def extract_text_from_jsonish(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if looks_like_local_command(stripped):
        return ""
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        if isinstance(data, dict):
            if isinstance(data.get("text"), str):
                return data["text"].strip()
            if isinstance(data.get("content"), str):
                return data["content"].strip()
        if isinstance(data, list):
            parts: list[str] = []
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "\n".join(parts).strip()
        return stripped
    return stripped


def extract_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        cleaned = re.sub(r"^[-*]\s*", "", raw.strip())
        cleaned = re.sub(r"^\d+[.)]\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) < 16:
            continue
        lines.append(cleaned)
    return lines


def sanitize_memory_lines(items: list[str], limit: int) -> list[str]:
    blocked = re.compile(
        r"^(i('|’)?ll|next i('|’)?ll|i can|let me|checking|working on|progress|status|i('|’)?m|i('|’)?ve|i have)\b",
        re.IGNORECASE,
    )
    filtered: list[str] = []
    for item in items:
        cleaned = truncate(item, 220)
        if blocked.search(cleaned):
            continue
        if cleaned.endswith(":"):
            continue
        filtered.append(cleaned)
    return uniq(filtered, limit)


def summarize_actions(actions: list[dict[str, str]]) -> list[str]:
    out: list[str] = []
    verb_map = {
        "added": "Added",
        "modified": "Updated",
        "deleted": "Removed",
        "renamed": "Renamed",
    }
    for action in actions:
        path = action.get("path", "").strip()
        if not path:
            continue
        verb = verb_map.get(action.get("action", "modified").lower(), "Updated")
        out.append(f"{verb}: {path}")
    return uniq(out, 20)


def infer_project_labels(paths: list[str]) -> list[str]:
    labels: list[str] = []
    for path in paths:
        if path.startswith("10. Courses/"):
            parts = path.split("/")
            if len(parts) >= 3:
                labels.append(parts[2])
        elif path.startswith("11. Programs/"):
            parts = path.split("/")
            if len(parts) >= 3:
                labels.append(parts[2])
        elif path.startswith("40. Memory/"):
            labels.append("AI memory system")
        elif path.startswith("30. Teaching resources/"):
            labels.append("Teaching resources")
        elif path.startswith("90. Miscellaneous/AI/"):
            labels.append("AI workflows")
    return uniq(labels, 12)


def session_short_id(platform: str, session_id: str, source_path: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", session_id)
    if cleaned:
        return f"{platform}-{cleaned[-12:].lower()}"
    digest = hashlib.sha1(f"{platform}:{source_path}".encode("utf-8")).hexdigest()[:12]
    return f"{platform}-{digest}"


def vault_link(rel_path: str) -> str:
    return f"[[{rel_path}]]"


def load_state(path: pathlib.Path, vault_root: pathlib.Path) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 2)
    data["workspace_root"] = str(vault_root)
    data.setdefault("processed_sources", {})
    data.setdefault("session_index", {})
    return data


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def collect_codex_candidates(
    workspace_root: pathlib.Path, cutoff: dt.datetime
) -> list[dict[str, Any]]:
    codex_home = pathlib.Path.home() / ".codex" / "sessions"
    if not codex_home.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in codex_home.rglob("rollout-*.jsonl"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        updated = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).astimezone()
        if updated < cutoff:
            continue
        if not codex_session_matches_workspace(path, workspace_root):
            continue
        out.append(
            {
                "platform": "codex",
                "source_key": f"codex:{path}",
                "source_path": str(path),
                "signature": f"{stat.st_mtime_ns}:{stat.st_size}",
                "updated_at": updated.isoformat(),
                "idle_seconds": max(0, int((now_local() - updated).total_seconds())),
            }
        )
    return out


def collect_gemini_candidates(workspace_root: pathlib.Path, cutoff: dt.datetime) -> list[dict[str, Any]]:
    gemini_tmp = pathlib.Path.home() / ".gemini" / "tmp"
    if not gemini_tmp.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in gemini_tmp.rglob("session-*.json"):
        marker = path.parent.parent / ".project_root"
        if not marker.exists():
            continue
        try:
            marker_root = normalize_path(pathlib.Path(read_text(marker).strip()))
            stat = path.stat()
        except (OSError, RuntimeError):
            continue
        if marker_root != workspace_root:
            continue
        updated = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).astimezone()
        if updated < cutoff:
            continue
        out.append(
            {
                "platform": "gemini",
                "source_key": f"gemini:{path}",
                "source_path": str(path),
                "signature": f"{stat.st_mtime_ns}:{stat.st_size}",
                "updated_at": updated.isoformat(),
                "idle_seconds": max(0, int((now_local() - updated).total_seconds())),
            }
        )
    return out


def collect_claude_candidates(workspace_root: pathlib.Path, cutoff: dt.datetime) -> list[dict[str, Any]]:
    claude_root = pathlib.Path.home() / ".claude" / "projects"
    if not claude_root.exists():
        return []
    encoded_dir = claude_root / encode_claude_project_dir(workspace_root)
    if not encoded_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in encoded_dir.rglob("*.jsonl"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        updated = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).astimezone()
        if updated < cutoff:
            continue
        out.append(
            {
                "platform": "claude",
                "source_key": f"claude:{path}",
                "source_path": str(path),
                "signature": f"{stat.st_mtime_ns}:{stat.st_size}",
                "updated_at": updated.isoformat(),
                "idle_seconds": max(0, int((now_local() - updated).total_seconds())),
            }
        )
    return out


def collect_opencode_candidates(workspace_root: pathlib.Path, cutoff: dt.datetime) -> list[dict[str, Any]]:
    db_path = pathlib.Path.home() / ".local/share/opencode/opencode.db"
    if not db_path.exists():
        return []
    out: list[dict[str, Any]] = []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        rows = cur.execute(
            """
            select s.id, s.title, s.directory, s.time_created, s.time_updated, p.worktree
            from session s
            join project p on p.id = s.project_id
            where s.time_archived is null
              and (s.directory = ? or p.worktree = ?)
            order by s.time_updated desc
            """,
            (str(workspace_root), str(workspace_root)),
        ).fetchall()
    finally:
        con.close()

    for session_id, title, directory, created_ms, updated_ms, worktree in rows:
        updated = parse_iso(iso_from_ms(updated_ms))
        if updated is None or updated < cutoff:
            continue
        out.append(
            {
                "platform": "opencode",
                "source_key": f"opencode:{session_id}",
                "source_path": f"{db_path}#session:{session_id}",
                "signature": str(updated_ms),
                "session_id": str(session_id),
                "title": str(title or ""),
                "directory": str(directory or worktree or workspace_root),
                "updated_at": updated.isoformat(),
                "idle_seconds": max(0, int((now_local() - updated).total_seconds())),
            }
        )
    return out


def parse_file_based_session(candidate: dict[str, Any]) -> dict[str, Any]:
    path = pathlib.Path(candidate["source_path"])
    platform = candidate["platform"]
    if platform == "codex":
        raw, messages, parse_status, prompt_context, tool_events, file_mods, session_id = parse_codex_session_jsonl(
            path, False
        )
    elif platform == "gemini":
        raw, messages, parse_status, prompt_context, tool_events, file_mods, session_id = parse_gemini_session_json(
            path, False
        )
    elif platform == "claude":
        raw, messages, parse_status, prompt_context, tool_events, file_mods, session_id = parse_claude_session_jsonl(
            path, False
        )
    else:
        raise ValueError(f"Unsupported file-based platform: {platform}")
    return {
        "platform": platform,
        "session_id": session_id or path.stem,
        "source_path": str(path),
        "raw_text": raw,
        "messages": messages,
        "tool_events": tool_events,
        "file_modifications": file_mods,
        "prompt_context": prompt_context,
        "parse_status": parse_status,
    }


def stringify_tool_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def parse_opencode_session(candidate: dict[str, Any]) -> dict[str, Any]:
    db_path = pathlib.Path.home() / ".local/share/opencode/opencode.db"
    session_id = candidate["session_id"]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        session_row = cur.execute(
            """
            select s.id, s.title, s.directory, s.time_created, s.time_updated, p.worktree
            from session s
            join project p on p.id = s.project_id
            where s.id = ?
            """,
            (session_id,),
        ).fetchone()
        message_rows = cur.execute(
            """
            select id, data, time_created
            from message
            where session_id = ?
            order by time_created asc, id asc
            """,
            (session_id,),
        ).fetchall()
        part_rows = cur.execute(
            """
            select id, message_id, data, time_created
            from part
            where session_id = ?
            order by time_created asc, id asc
            """,
            (session_id,),
        ).fetchall()
    finally:
        con.close()

    parts_by_message: dict[str, list[dict[str, Any]]] = {}
    raw_parts: list[dict[str, Any]] = []
    for row in part_rows:
        data = json.loads(row["data"])
        raw_parts.append(data)
        parts_by_message.setdefault(str(row["message_id"]), []).append(data)

    messages: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    file_modifications: list[dict[str, Any]] = []
    seen_mods: set[tuple[str, str]] = set()
    tool_index = 0

    for index, row in enumerate(message_rows, start=1):
        msg_data = json.loads(row["data"])
        role = str(msg_data.get("role") or "unknown").lower()
        parts = parts_by_message.get(str(row["id"]), [])
        text_chunks: list[str] = []
        for part in parts:
            part_type = str(part.get("type") or "")
            if part_type == "text":
                text_chunks.append(str(part.get("text") or ""))
            elif part_type == "tool":
                tool_index += 1
                state = part.get("state") or {}
                tool_name = str(part.get("tool") or "")
                output_text = stringify_tool_output(state.get("output"))
                status = str(state.get("status") or "")
                tool_events.append(
                    {
                        "index": tool_index,
                        "timestamp": iso_from_ms(((state.get("time") or {}).get("start")) or msg_data.get("time", {}).get("created")),
                        "tool_name": tool_name,
                        "call_id": str(part.get("callID") or ""),
                        "input_verbatim": json.dumps(state.get("input"), ensure_ascii=False),
                        "output_verbatim": output_text,
                        "status": status,
                        "exit_code": 0 if status == "completed" else None,
                    }
                )
                mods = infer_file_modifications_from_tool_metadata(
                    tool_name,
                    state.get("input"),
                    output_text,
                    status="success" if status == "completed" else status,
                )
                for mod in mods:
                    rel = rel_to_vault(str(mod.get("path") or ""), normalize_path(pathlib.Path(candidate["directory"])))
                    if rel:
                        mod_path = rel
                    else:
                        mod_path = str(mod.get("path") or "")
                    mod_key = (mod_path, str(mod.get("action") or "modified"))
                    if mod_key in seen_mods:
                        continue
                    seen_mods.add(mod_key)
                    file_modifications.append(
                        {
                            "path": mod_path,
                            "action": str(mod.get("action") or "modified"),
                            "status_code": str(mod.get("status_code") or ""),
                            "summary": str(mod.get("summary") or ""),
                        }
                    )
        messages.append(
            {
                "index": index,
                "timestamp": iso_from_ms(((msg_data.get("time") or {}).get("created"))),
                "role": role,
                "content_verbatim": "\n".join(chunk for chunk in text_chunks if chunk).strip(),
            }
        )

    raw_text = json.dumps(
        {
            "session": dict(session_row) if session_row is not None else {},
            "messages": [dict(row) for row in message_rows],
            "parts": raw_parts,
        },
        ensure_ascii=False,
        indent=2,
    )
    return {
        "platform": "opencode",
        "session_id": session_id,
        "source_path": candidate["source_path"],
        "raw_text": raw_text,
        "messages": messages,
        "tool_events": tool_events,
        "file_modifications": file_modifications,
        "prompt_context": {},
        "parse_status": "parsed_opencode_sqlite",
        "title": str(candidate.get("title") or ""),
    }


def clean_message_text(text: str) -> str:
    cleaned = extract_text_from_jsonish(text)
    if is_injected_context(cleaned):
        return ""
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def first_meaningful_message(messages: list[dict[str, Any]], role: str) -> str:
    for msg in messages:
        if str(msg.get("role") or "").lower() != role:
            continue
        text = clean_message_text(str(msg.get("content_verbatim") or ""))
        if len(text) >= 8:
            return text
    return ""


def last_meaningful_message(messages: list[dict[str, Any]], role: str) -> str:
    for msg in reversed(messages):
        if str(msg.get("role") or "").lower() != role:
            continue
        text = clean_message_text(str(msg.get("content_verbatim") or ""))
        if len(text) >= 8:
            return text
    return ""


def collect_texts(messages: list[dict[str, Any]], role: str) -> list[str]:
    out: list[str] = []
    for msg in messages:
        if str(msg.get("role") or "").lower() != role:
            continue
        text = clean_message_text(str(msg.get("content_verbatim") or ""))
        if len(text) >= 8:
            out.append(text)
    return out


def extract_decisions(assistant_texts: list[str]) -> list[str]:
    decision_kw = re.compile(
        r"\b(recommend|should|will use|default|replace|keep|use|switch|primary|best approach|target architecture)\b",
        re.IGNORECASE,
    )
    lines: list[str] = []
    for text in assistant_texts[-4:]:
        for line in extract_lines(text):
            if decision_kw.search(line):
                lines.append(line)
    return sanitize_memory_lines(lines, 8)


def extract_open_loops(texts: list[str]) -> list[str]:
    open_kw = re.compile(
        r"\b(open loop|pending|follow[- ]?up|todo|continue|remaining|next step|still need)\b",
        re.IGNORECASE,
    )
    lines: list[str] = []
    for text in texts[-4:]:
        for line in extract_lines(text):
            if open_kw.search(line):
                lines.append(line)
    return sanitize_memory_lines(lines, 8)


def extract_durable_updates(user_texts: list[str]) -> list[str]:
    update_kw = re.compile(
        r"\b(prefer|always|never|must|do not|don't|keep|accessible|any of the different agents|include in the agents|i want)\b",
        re.IGNORECASE,
    )
    lines: list[str] = []
    for text in user_texts:
        for line in extract_lines(text):
            if update_kw.search(line):
                lines.append(line)
    return sanitize_memory_lines(lines, 8)


def normalize_file_actions(file_modifications: list[dict[str, Any]], vault_root: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    paths: list[str] = []
    actions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for mod in file_modifications:
        raw_path = str(mod.get("path") or "").strip().strip('"')
        if not raw_path:
            continue
        rel = rel_to_vault(raw_path, vault_root)
        path_text = rel if rel else raw_path
        if path_text.startswith(".obsidian/"):
            continue
        if path_text.endswith("/"):
            continue
        action = str(mod.get("action") or "modified").lower()
        key = (path_text, action)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path_text)
        actions.append({"path": path_text, "action": action})
    return uniq(paths, 30), actions


def infer_summary(
    original_request: str,
    final_response: str,
    files_touched: list[str],
    deliverables: list[str],
    platform: str,
) -> str:
    if final_response:
        return truncate(final_response, 320)
    if deliverables:
        return truncate(f"{platform.title()} session changed {len(files_touched)} vault file(s).", 320)
    if original_request:
        return truncate(f"{platform.title()} session handled request: {original_request}", 320)
    return f"{platform.title()} session synced."


def normalize_session(
    parsed: dict[str, Any],
    candidate: dict[str, Any],
    vault_root: pathlib.Path,
) -> dict[str, Any]:
    messages = parsed["messages"]
    user_texts = collect_texts(messages, "user")
    assistant_texts = collect_texts(messages, "assistant")
    original_request = first_meaningful_message(messages, "user")
    final_response = last_meaningful_message(messages, "assistant")
    files_touched, action_records = normalize_file_actions(parsed["file_modifications"], vault_root)
    deliverables = summarize_actions(action_records)
    decision_lines = extract_decisions(assistant_texts)
    open_loops = extract_open_loops(user_texts + assistant_texts)
    durable_updates = extract_durable_updates(user_texts)
    timestamps = [parse_iso(str(msg.get("timestamp") or "")) for msg in messages]
    timestamps = [ts for ts in timestamps if ts is not None]
    started = min(timestamps) if timestamps else parse_iso(candidate.get("updated_at"))
    updated = max(timestamps) if timestamps else parse_iso(candidate.get("updated_at"))
    date_str = (started or updated or now_local()).date().isoformat()
    summary = infer_summary(original_request, final_response, files_touched, deliverables, parsed["platform"])
    session_id = str(parsed.get("session_id") or "")
    note_relpath = f"40. Memory/sessions/{date_str}/{session_short_id(parsed['platform'], session_id, parsed['source_path'])}.md"
    courses_touched = [label for label in infer_project_labels(files_touched) if label not in {"AI memory system", "Teaching resources", "AI workflows"}]
    return {
        "type": "ai_session_memory",
        "date": date_str,
        "platform": parsed["platform"],
        "session_id": session_id,
        "source_path": parsed["source_path"],
        "workspace": str(vault_root),
        "started_at": (started or now_local()).isoformat(),
        "last_updated_at": (updated or now_local()).isoformat(),
        "status": "synced",
        "summary": summary,
        "original_request": truncate(original_request, 1600),
        "final_response": truncate(final_response, 2400),
        "decision_lines": decision_lines,
        "files_touched": files_touched,
        "deliverables": deliverables,
        "open_loops": open_loops,
        "durable_context_updates": durable_updates,
        "courses_touched": uniq(courses_touched, 12),
        "contains_sensitive_data": False,
        "tags": ["ai", "memory", parsed["platform"]],
        "note_relpath": note_relpath,
    }


def write_session_note(vault_root: pathlib.Path, session: dict[str, Any]) -> pathlib.Path:
    note_path = vault_root / session["note_relpath"]
    frontmatter = {
        key: session[key]
        for key in (
            "type",
            "date",
            "platform",
            "session_id",
            "source_path",
            "workspace",
            "started_at",
            "last_updated_at",
            "status",
            "summary",
            "files_touched",
            "deliverables",
            "open_loops",
            "durable_context_updates",
            "courses_touched",
            "contains_sensitive_data",
            "tags",
        )
    }
    body = (
        f"# Session Memory - {session['platform'].title()} {session['session_id']}\n\n"
        "## Request\n\n"
        f"{session['original_request'] or 'No user request text was recovered.'}\n\n"
        "## Outcome\n\n"
        f"{session['summary']}\n\n"
        "## Decisions\n\n"
        f"{list_to_md(session['decision_lines'])}\n\n"
        "## Files Touched\n\n"
        f"{list_to_md(session['files_touched'])}\n\n"
        "## Deliverables\n\n"
        f"{list_to_md(session['deliverables'])}\n\n"
        "## Open Loops\n\n"
        f"{list_to_md(session['open_loops'])}\n\n"
        "## Durable Context Updates\n\n"
        f"{list_to_md(session['durable_context_updates'])}\n\n"
        "## Final Response Excerpt\n\n"
        f"{session['final_response'] or 'No final assistant message was recovered.'}\n"
    )
    text = render_frontmatter(frontmatter, SESSION_ORDER) + "\n\n" + body
    write_text(note_path, text)
    return note_path


def aggregate_day_sessions(state: dict[str, Any], date_str: str) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for item in state.get("session_index", {}).values():
        if not isinstance(item, dict):
            continue
        if item.get("date") != date_str:
            continue
        sessions.append(item)
    sessions.sort(key=lambda item: item.get("last_updated_at", ""))
    return sessions


def write_daily_note(vault_root: pathlib.Path, state: dict[str, Any], date_str: str) -> pathlib.Path | None:
    sessions = aggregate_day_sessions(state, date_str)
    if not sessions:
        return None
    source_sessions = [vault_link(session["note_relpath"]) for session in sessions]
    what_mattered = uniq([session["summary"] for session in sessions], 20)
    key_decisions = uniq(
        [line for session in sessions for line in session.get("decision_lines", [])],
        20,
    )
    completed = uniq(
        [item for session in sessions for item in session.get("deliverables", [])],
        30,
    )
    open_loops = uniq(
        [item for session in sessions for item in session.get("open_loops", [])],
        20,
    )
    key_files = uniq(
        [item for session in sessions for item in session.get("files_touched", [])],
        30,
    )
    courses = uniq(
        [item for session in sessions for item in session.get("courses_touched", [])],
        20,
    )
    platforms = uniq([session["platform"] for session in sessions], 8)
    daily_path = vault_root / "40. Memory/daily" / f"{date_str}.md"
    created = now_local_str()
    if daily_path.exists():
        text = read_text(daily_path)
        match = re.search(r"(?m)^created:\s*(.+)$", text)
        if match:
            created = match.group(1).strip().strip('"')
    frontmatter = {
        "type": "ai_memory_daily",
        "date": date_str,
        "source_sessions": source_sessions,
        "platforms": platforms,
        "session_count": len(sessions),
        "what_mattered": what_mattered,
        "key_decisions": key_decisions,
        "completed_actions": completed,
        "open_loops": open_loops,
        "courses_touched": courses,
        "people_touched": [],
        "key_files": key_files,
        "contains_sensitive_data": False,
        "created": created,
        "last_modified": now_local_str(),
        "tags": ["ai", "memory", "daily"],
    }
    body = (
        f"# Daily AI Memory Summary - {date_str}\n\n"
        "## What Mattered Today\n\n"
        f"{list_to_md(what_mattered)}\n\n"
        "## Decisions\n\n"
        f"{list_to_md(key_decisions)}\n\n"
        "## Completed Actions\n\n"
        f"{list_to_md(completed)}\n\n"
        "## Open Loops for Tomorrow\n\n"
        f"{list_to_md(open_loops)}\n\n"
        "## Source Sessions\n\n"
        f"{list_to_md(source_sessions)}\n"
    )
    write_text(daily_path, render_frontmatter(frontmatter, DAILY_ORDER) + "\n\n" + body)
    return daily_path


def rebuild_active_context(vault_root: pathlib.Path, state: dict[str, Any]) -> pathlib.Path:
    sessions = [
        item
        for item in state.get("session_index", {}).values()
        if isinstance(item, dict)
    ]
    sessions.sort(key=lambda item: item.get("last_updated_at", ""), reverse=True)
    cutoff = now_local() - dt.timedelta(days=30)
    recent = [
        item
        for item in sessions
        if (parse_iso(str(item.get("last_updated_at") or "")) or now_local()) >= cutoff
    ]
    if not recent:
        recent = sessions[:30]
    recent_projects = uniq(
        [label for session in recent for label in infer_project_labels(session.get("files_touched", []))],
        10,
    )
    recent_loops = uniq(
        [loop for session in recent for loop in session.get("open_loops", [])],
        10,
    )
    preferences = uniq(
        [item for session in recent[:40] for item in session.get("durable_context_updates", [])],
        10,
    )
    priorities = uniq([session.get("summary", "") for session in recent[:8]], 6)
    recent_session_links = uniq(
        [vault_link(session["note_relpath"]) for session in recent[:8]],
        8,
    )
    key_files = uniq(
        [path for session in recent[:10] for path in session.get("files_touched", [])],
        10,
    )
    frontmatter = {
        "type": "ai_memory_active_context",
        "last_rebuilt": now_local().isoformat(),
        "active_projects": recent_projects,
        "open_loops": recent_loops,
        "stable_preferences": preferences,
        "operating_constraints": [
            "Use session memory cards in 40. Memory/sessions as the primary AI memory layer.",
            "Native agent histories remain outside the vault and act as the evidence layer.",
        ],
        "priority_this_week": priorities,
        "recent_sessions": recent_session_links,
        "tags": ["ai", "memory", "context"],
    }
    body = (
        "# Active Context\n\n"
        "Use this note as the first memory checkpoint before reading daily notes or session cards.\n\n"
        "## Ongoing Work\n\n"
        f"{list_to_md(recent_projects)}\n\n"
        "## Open Loops\n\n"
        f"{list_to_md(recent_loops)}\n\n"
        "## Stable Preferences\n\n"
        f"{list_to_md(preferences)}\n\n"
        "## Constraints\n\n"
        f"{list_to_md(frontmatter['operating_constraints'])}\n\n"
        "## This Week Focus\n\n"
        f"{list_to_md(priorities)}\n\n"
        "## Recent Sessions\n\n"
        f"{list_to_md(recent_session_links)}\n\n"
        "## Key Files\n\n"
        f"{list_to_md(key_files)}\n"
    )
    active_path = vault_root / "40. Memory/Active Context.md"
    write_text(active_path, render_frontmatter(frontmatter, ACTIVE_ORDER) + "\n\n" + body)
    return active_path


def select_candidates(
    workspace_root: pathlib.Path,
    source: str,
    since_days: int,
) -> list[dict[str, Any]]:
    cutoff = now_local() - dt.timedelta(days=since_days)
    candidates: list[dict[str, Any]] = []
    if source in {"all", "codex"}:
        candidates.extend(collect_codex_candidates(workspace_root, cutoff))
    if source in {"all", "gemini"}:
        candidates.extend(collect_gemini_candidates(workspace_root, cutoff))
    if source in {"all", "claude"}:
        candidates.extend(collect_claude_candidates(workspace_root, cutoff))
    if source in {"all", "opencode"}:
        candidates.extend(collect_opencode_candidates(workspace_root, cutoff))
    candidates.sort(key=lambda item: item.get("updated_at", ""))
    return candidates


def process_candidate(
    candidate: dict[str, Any],
    vault_root: pathlib.Path,
    state: dict[str, Any],
) -> tuple[bool, dict[str, str] | str]:
    platform = candidate["platform"]
    source_key = candidate["source_key"]
    try:
        if platform == "opencode":
            parsed = parse_opencode_session(candidate)
        else:
            parsed = parse_file_based_session(candidate)
        session = normalize_session(parsed, candidate, vault_root)
        note_path = write_session_note(vault_root, session)
    except Exception as exc:  # noqa: BLE001
        return False, f"{source_key}: {exc}"

    state["processed_sources"][source_key] = {
        "signature": candidate["signature"],
        "platform": platform,
        "source_path": candidate["source_path"],
        "note_relpath": session["note_relpath"],
        "last_processed_at": now_local().isoformat(),
        "last_source_updated_at": candidate["updated_at"],
    }
    state["session_index"][f"{platform}:{session['session_id']}"] = session
    return True, {"note_path": str(note_path), "date": str(session["date"])}


def cleanup_state(state: dict[str, Any], vault_root: pathlib.Path) -> None:
    session_index = state.get("session_index", {})
    keep: dict[str, Any] = {}
    for key, item in session_index.items():
        if not isinstance(item, dict):
            continue
        note_relpath = item.get("note_relpath")
        if not isinstance(note_relpath, str):
            continue
        if (vault_root / note_relpath).exists():
            keep[key] = item
    state["session_index"] = keep


def sync_once(args: argparse.Namespace) -> int:
    vault_root = normalize_path(pathlib.Path(args.vault_root))
    state_path = vault_root / "40. Memory/state/session_memory_state.json"
    state = load_state(state_path, vault_root)
    candidates = select_candidates(vault_root, args.source, args.since_days)
    processed_paths: list[str] = []
    changed_dates: set[str] = set()
    errors: list[str] = []

    for candidate in candidates:
        if not args.include_active and int(candidate.get("idle_seconds", 0)) < args.idle_seconds:
            continue
        prior = state["processed_sources"].get(candidate["source_key"], {})
        if not args.force and prior.get("signature") == candidate["signature"]:
            continue
        ok, payload = process_candidate(candidate, vault_root, state)
        if ok:
            if isinstance(payload, dict):
                processed_paths.append(payload["note_path"])
                changed_dates.add(payload["date"])
        else:
            errors.append(str(payload))

    cleanup_state(state, vault_root)

    daily_paths: list[str] = []
    for date_str in sorted(changed_dates):
        daily_path = write_daily_note(vault_root, state, date_str)
        if daily_path is not None:
            daily_paths.append(str(daily_path))
    active_path = rebuild_active_context(vault_root, state)
    save_state(state_path, state)

    print(json.dumps(
        {
            "processed_sessions": len(processed_paths),
            "session_notes": processed_paths,
            "daily_notes": daily_paths,
            "active_context": str(active_path),
            "errors": errors,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 1 if errors and not processed_paths else 0


def watch_loop(args: argparse.Namespace) -> int:
    while True:
        code = sync_once(args)
        if code not in {0, 1}:
            return code
        time.sleep(args.poll_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync session-based AI memory into the vault.")
    parser.add_argument("--vault-root", default=".", help="Vault root path.")
    parser.add_argument(
        "--source",
        default="all",
        choices=["all", "codex", "claude", "gemini", "opencode"],
        help="Source platform selection.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help="Only scan sessions updated within this many days.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=180,
        help="Only sync sessions that have been idle for at least this long.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Watcher poll interval in seconds.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously instead of a single sync pass.",
    )
    parser.add_argument(
        "--include-active",
        action="store_true",
        help="Also process currently active sessions without idle gating.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess candidates even if the saved signature has not changed.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.watch:
        return watch_loop(args)
    return sync_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
