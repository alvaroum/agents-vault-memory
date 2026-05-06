#!/usr/bin/env python3
"""Shared parsers for native agent session histories."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
from typing import Any


def read_text(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.expanduser(str(path))).resolve()


def is_subpath(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        normalize_path(path).relative_to(normalize_path(root))
        return True
    except ValueError:
        return False


def ts_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc).replace(
                microsecond=0
            ).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        return value
    return None


def extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(extract_content(x) for x in content)
    if isinstance(content, dict):
        if "parts" in content and isinstance(content["parts"], list):
            return "\n".join(extract_content(x) for x in content["parts"])
        if "text" in content:
            return extract_content(content["text"])
    return json.dumps(content, ensure_ascii=False)


def parse_cwd_candidates(text: str) -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    if not text:
        return candidates
    patterns = [
        re.compile(r"<cwd>(.*?)</cwd>", re.IGNORECASE | re.DOTALL),
        re.compile(r'"cwd"\s*:\s*"([^"]+)"'),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            if not raw:
                continue
            try:
                candidates.append(normalize_path(pathlib.Path(raw)))
            except OSError:
                continue
    deduped: list[pathlib.Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def extract_text_from_content_blocks(content: Any) -> str:
    if not isinstance(content, list):
        return extract_content(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type in {"text", "input_text", "output_text"} and "text" in block:
                parts.append(str(block.get("text", "")))
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        else:
            parts.append(extract_content(block))
    return "\n".join(p for p in parts if p is not None and str(p) != "").strip()


def content_blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = str(block.get("type") or "")
                if block_type in {"text", "input_text", "output_text"} and "text" in block:
                    parts.append(str(block.get("text") or ""))
                elif block_type == "thinking" and "thinking" in block:
                    parts.append(str(block.get("thinking") or ""))
                elif block_type == "tool_result" and "content" in block:
                    parts.append(extract_content(block.get("content")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(extract_content(block))
        return "\n".join(x for x in parts if x != "")
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        return json.dumps(content, ensure_ascii=False)
    return extract_content(content)


def codex_session_matches_workspace(
    session_file: pathlib.Path, workspace_root: pathlib.Path
) -> bool:
    try:
        lines = read_text(session_file).splitlines()
    except OSError:
        return False
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "response_item":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        if str(payload.get("role") or "").lower() != "user":
            continue
        text = extract_text_from_content_blocks(payload.get("content", []))
        for cwd in parse_cwd_candidates(text):
            if is_subpath(cwd, workspace_root):
                return True
    return False


def parse_file_modifications_from_tool_output(
    tool_name: str, tool_input_verbatim: str, output_text: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    out = output_text or ""

    if (
        "Updated the following files:" in out
        or "Deleted the following files:" in out
        or "Added the following files:" in out
    ):
        for line in out.splitlines():
            match = re.match(r"^\s*([MADR])\s+(.+)$", line.strip())
            if not match:
                continue
            code, path = match.group(1), match.group(2).strip()
            action = {
                "M": "modified",
                "A": "added",
                "D": "deleted",
                "R": "renamed",
            }.get(code, "modified")
            records.append(
                {
                    "path": path,
                    "action": action,
                    "status_code": code,
                    "summary": "Parsed from tool output",
                }
            )

    if tool_name == "exec_command" and "git status --short" in tool_input_verbatim:
        for line in out.splitlines():
            match = re.match(r"^\s*([ MADRCU\?]{1,2})\s+(.+)$", line)
            if not match:
                continue
            status = match.group(1).strip()
            path = match.group(2).strip()
            if not status or not path:
                continue
            action = "modified"
            if "?" in status or "A" in status:
                action = "added"
            elif "D" in status:
                action = "deleted"
            elif "R" in status:
                action = "renamed"
            records.append(
                {
                    "path": path,
                    "action": action,
                    "status_code": status,
                    "summary": "Parsed from git status output",
                }
            )

    return records


def infer_file_modifications_from_tool_metadata(
    tool_name: str, args: Any, output_text: str, status: str | None = None
) -> list[dict[str, Any]]:
    records = parse_file_modifications_from_tool_output(
        tool_name=tool_name,
        tool_input_verbatim=json.dumps(args, ensure_ascii=False)
        if not isinstance(args, str)
        else args,
        output_text=output_text,
    )

    if not isinstance(args, dict):
        return records

    lowered = tool_name.lower()
    action = None
    if any(key in lowered for key in ("delete", "remove", "unlink")):
        action = "deleted"
    elif any(key in lowered for key in ("rename", "move")):
        action = "renamed"
    elif any(key in lowered for key in ("write", "edit", "patch", "update")):
        action = "modified"
    elif any(key in lowered for key in ("create", "new_file")):
        action = "added"

    path_keys = (
        "file_path",
        "path",
        "target_path",
        "target_file",
        "destination_path",
        "dest_path",
        "new_path",
        "old_path",
    )
    raw_paths: list[str] = []
    for key in path_keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            raw_paths.append(value.strip())
    if not raw_paths:
        return records

    for path in raw_paths:
        records.append(
            {
                "path": path,
                "action": action or "modified",
                "status_code": "success" if status == "success" else "unknown",
                "summary": "Inferred from tool call metadata",
            }
        )
    return records


def parse_codex_session_jsonl(
    session_file: pathlib.Path, embed_raw_events: bool
) -> tuple[
    str,
    list[dict[str, Any]],
    str,
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    raw_text = read_text(session_file)
    events: list[dict[str, Any]] = []
    for line in [line for line in raw_text.splitlines() if line.strip()]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    messages: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    file_modifications: list[dict[str, Any]] = []
    prompt_context: dict[str, Any] = {
        "system_messages_verbatim": [],
        "developer_messages_verbatim": [],
        "environment_context_verbatim": [],
    }
    call_index_by_id: dict[str, int] = {}
    seen_file_mods: set[tuple[str, str]] = set()
    session_id: str | None = None

    msg_index = 0
    tool_index = 0
    for event in events:
        event_type = event.get("type")
        timestamp = event.get("timestamp")

        if event_type == "session_meta":
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("id"):
                session_id = str(payload["id"])

        if event_type != "response_item":
            continue

        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")

        if payload_type == "message":
            role = str(payload.get("role") or "unknown").lower()
            text = extract_text_from_content_blocks(payload.get("content", []))
            if text:
                msg_index += 1
                messages.append(
                    {
                        "index": msg_index,
                        "timestamp": timestamp,
                        "role": role,
                        "content_verbatim": text,
                    }
                )
                if role == "system":
                    prompt_context["system_messages_verbatim"].append(text)
                elif role == "developer":
                    prompt_context["developer_messages_verbatim"].append(text)
                if role == "user" and "<environment_context>" in text:
                    prompt_context["environment_context_verbatim"].append(text)

        elif payload_type == "function_call":
            tool_index += 1
            call_id = str(payload.get("call_id") or "")
            tool_events.append(
                {
                    "index": tool_index,
                    "timestamp": timestamp,
                    "tool_name": str(payload.get("name") or ""),
                    "call_id": call_id,
                    "input_verbatim": str(payload.get("arguments") or ""),
                    "output_verbatim": "",
                    "exit_code": None,
                }
            )
            if call_id:
                call_index_by_id[call_id] = len(tool_events) - 1

        elif payload_type == "function_call_output":
            call_id = str(payload.get("call_id") or "")
            output_text = str(payload.get("output") or "")
            if call_id and call_id in call_index_by_id:
                idx = call_index_by_id[call_id]
                tool_events[idx]["output_verbatim"] = output_text
                tool_name = str(tool_events[idx].get("tool_name") or "")
                tool_input = str(tool_events[idx].get("input_verbatim") or "")
                for mod in parse_file_modifications_from_tool_output(
                    tool_name, tool_input, output_text
                ):
                    key = (str(mod.get("path", "")), str(mod.get("action", "")))
                    if key in seen_file_mods:
                        continue
                    seen_file_mods.add(key)
                    file_modifications.append(mod)
            else:
                tool_index += 1
                tool_events.append(
                    {
                        "index": tool_index,
                        "timestamp": timestamp,
                        "tool_name": "unknown",
                        "call_id": call_id,
                        "input_verbatim": "",
                        "output_verbatim": output_text,
                        "exit_code": None,
                    }
                )

    if embed_raw_events:
        prompt_context["raw_session_events"] = events

    return (
        raw_text,
        messages,
        "parsed_codex_session_jsonl",
        prompt_context,
        tool_events,
        file_modifications,
        session_id,
    )


def parse_gemini_session_json(
    session_file: pathlib.Path, embed_raw_events: bool
) -> tuple[
    str,
    list[dict[str, Any]],
    str,
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    raw_text = read_text(session_file)
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return (
            raw_text,
            [],
            "invalid_gemini_json",
            {
                "system_messages_verbatim": [],
                "developer_messages_verbatim": [],
                "environment_context_verbatim": [],
            },
            [],
            [],
            None,
        )

    messages: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    file_modifications: list[dict[str, Any]] = []
    seen_file_mods: set[tuple[str, str]] = set()
    prompt_context: dict[str, Any] = {
        "system_messages_verbatim": [],
        "developer_messages_verbatim": [],
        "environment_context_verbatim": [],
    }

    role_map = {
        "user": "user",
        "gemini": "assistant",
        "assistant": "assistant",
        "system": "system",
        "info": "system",
    }

    msg_index = 0
    tool_index = 0
    items = data.get("messages")
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        timestamp = ts_to_iso(item.get("timestamp"))
        msg_type = str(item.get("type") or "unknown").lower()
        role = role_map.get(msg_type, msg_type)
        content = content_blocks_to_text(item.get("content", ""))
        if content:
            msg_index += 1
            messages.append(
                {
                    "index": msg_index,
                    "timestamp": timestamp,
                    "role": role,
                    "content_verbatim": content,
                    "source_message_type": msg_type,
                }
            )
            if role == "system":
                prompt_context["system_messages_verbatim"].append(content)
            if role == "user" and "<environment_context>" in content:
                prompt_context["environment_context_verbatim"].append(content)

        tool_calls = item.get("toolCalls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_index += 1
            tool_name = str(tool_call.get("name") or "")
            call_id = str(tool_call.get("id") or "")
            args = tool_call.get("args")
            status = str(tool_call.get("status") or "")
            tc_timestamp = ts_to_iso(tool_call.get("timestamp")) or timestamp
            output_text = extract_content(tool_call.get("result"))
            tool_events.append(
                {
                    "index": tool_index,
                    "timestamp": tc_timestamp,
                    "tool_name": tool_name,
                    "call_id": call_id,
                    "input_verbatim": json.dumps(args, ensure_ascii=False)
                    if not isinstance(args, str)
                    else args,
                    "output_verbatim": output_text,
                    "exit_code": 0 if status == "success" else None,
                    "status": status,
                }
            )
            for mod in infer_file_modifications_from_tool_metadata(
                tool_name=tool_name,
                args=args,
                output_text=output_text,
                status=status,
            ):
                key = (str(mod.get("path", "")), str(mod.get("action", "")))
                if key in seen_file_mods:
                    continue
                seen_file_mods.add(key)
                file_modifications.append(mod)

    if embed_raw_events:
        prompt_context["raw_session_events"] = data

    return (
        raw_text,
        messages,
        "parsed_gemini_session_json",
        prompt_context,
        tool_events,
        file_modifications,
        str(data.get("sessionId")) if data.get("sessionId") else None,
    )


def encode_claude_project_dir(cwd: pathlib.Path) -> str:
    parts: list[str] = []
    for part in cwd.parts:
        if part in {"/", ""}:
            continue
        cleaned = re.sub(r"[^A-Za-z0-9]+", "-", part).strip("-")
        if cleaned:
            parts.append(cleaned)
    return "-" + "-".join(parts)


def parse_claude_session_jsonl(
    session_file: pathlib.Path, embed_raw_events: bool
) -> tuple[
    str,
    list[dict[str, Any]],
    str,
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    raw_text = read_text(session_file)
    events: list[dict[str, Any]] = []
    for line in [line for line in raw_text.splitlines() if line.strip()]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    messages: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    file_modifications: list[dict[str, Any]] = []
    prompt_context: dict[str, Any] = {
        "system_messages_verbatim": [],
        "developer_messages_verbatim": [],
        "environment_context_verbatim": [],
    }
    seen_file_mods: set[tuple[str, str]] = set()
    call_index_by_id: dict[str, int] = {}
    session_id: str | None = None

    msg_index = 0
    tool_index = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        timestamp = ts_to_iso(event.get("timestamp"))
        sid = event.get("sessionId")
        if isinstance(sid, str) and sid:
            session_id = sid

        if event_type == "system":
            content = str(event.get("content") or "")
            if content:
                msg_index += 1
                messages.append(
                    {
                        "index": msg_index,
                        "timestamp": timestamp,
                        "role": "system",
                        "content_verbatim": content,
                        "source_message_type": "system",
                    }
                )
                prompt_context["system_messages_verbatim"].append(content)

        message_obj = event.get("message")
        if not isinstance(message_obj, dict):
            continue

        role = str(message_obj.get("role") or event_type or "unknown").lower()
        content_obj = message_obj.get("content", "")
        content_text = content_blocks_to_text(content_obj)
        if content_text:
            msg_index += 1
            messages.append(
                {
                    "index": msg_index,
                    "timestamp": timestamp,
                    "role": role,
                    "content_verbatim": content_text,
                    "source_message_type": event_type,
                }
            )
            if role == "system":
                prompt_context["system_messages_verbatim"].append(content_text)
            if role == "user" and "<environment_context>" in content_text:
                prompt_context["environment_context_verbatim"].append(content_text)

        if isinstance(content_obj, list):
            for block in content_obj:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "")
                if block_type == "tool_use":
                    tool_index += 1
                    tool_name = str(block.get("name") or "")
                    call_id = str(block.get("id") or "")
                    tool_input = block.get("input")
                    tool_events.append(
                        {
                            "index": tool_index,
                            "timestamp": timestamp,
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "input_verbatim": json.dumps(tool_input, ensure_ascii=False)
                            if not isinstance(tool_input, str)
                            else tool_input,
                            "output_verbatim": "",
                            "exit_code": None,
                        }
                    )
                    if call_id:
                        call_index_by_id[call_id] = len(tool_events) - 1
                    for mod in infer_file_modifications_from_tool_metadata(
                        tool_name=tool_name,
                        args=tool_input,
                        output_text="",
                        status=None,
                    ):
                        key = (str(mod.get("path", "")), str(mod.get("action", "")))
                        if key in seen_file_mods:
                            continue
                        seen_file_mods.add(key)
                        file_modifications.append(mod)
                elif block_type == "tool_result":
                    call_id = str(block.get("tool_use_id") or "")
                    output_text = extract_content(block.get("content"))
                    is_error = bool(block.get("is_error"))
                    if call_id and call_id in call_index_by_id:
                        idx = call_index_by_id[call_id]
                        tool_events[idx]["output_verbatim"] = output_text
                        if is_error:
                            tool_events[idx]["exit_code"] = 1
                        tool_name = str(tool_events[idx].get("tool_name") or "")
                        tool_input = str(tool_events[idx].get("input_verbatim") or "")
                        for mod in parse_file_modifications_from_tool_output(
                            tool_name=tool_name,
                            tool_input_verbatim=tool_input,
                            output_text=output_text,
                        ):
                            key = (str(mod.get("path", "")), str(mod.get("action", "")))
                            if key in seen_file_mods:
                                continue
                            seen_file_mods.add(key)
                            file_modifications.append(mod)
                    else:
                        tool_index += 1
                        tool_events.append(
                            {
                                "index": tool_index,
                                "timestamp": timestamp,
                                "tool_name": "unknown",
                                "call_id": call_id,
                                "input_verbatim": "",
                                "output_verbatim": output_text,
                                "exit_code": 1 if is_error else None,
                            }
                        )

        tool_result = event.get("toolUseResult")
        if not isinstance(tool_result, dict):
            continue
        call_id = ""
        maybe_content = (
            event.get("message", {}).get("content")
            if isinstance(event.get("message"), dict)
            else None
        )
        if isinstance(maybe_content, list):
            for block in maybe_content:
                if isinstance(block, dict) and isinstance(block.get("tool_use_id"), str):
                    call_id = block.get("tool_use_id")
                    break
        output_text = "\n".join(
            text
            for text in [
                str(tool_result.get("stdout") or ""),
                str(tool_result.get("stderr") or ""),
            ]
            if text
        )
        if call_id and call_id in call_index_by_id:
            idx = call_index_by_id[call_id]
            prev = str(tool_events[idx].get("output_verbatim") or "")
            if output_text and output_text not in prev:
                tool_events[idx]["output_verbatim"] = (
                    prev + ("\n" if prev and output_text else "") + output_text
                )
            if tool_result.get("stderr"):
                tool_events[idx]["exit_code"] = 1
            tool_name = str(tool_events[idx].get("tool_name") or "")
            tool_input = str(tool_events[idx].get("input_verbatim") or "")
            for mod in parse_file_modifications_from_tool_output(
                tool_name=tool_name,
                tool_input_verbatim=tool_input,
                output_text=output_text,
            ):
                key = (str(mod.get("path", "")), str(mod.get("action", "")))
                if key in seen_file_mods:
                    continue
                seen_file_mods.add(key)
                file_modifications.append(mod)

    if embed_raw_events:
        prompt_context["raw_session_events"] = events

    return (
        raw_text,
        messages,
        "parsed_claude_session_jsonl",
        prompt_context,
        tool_events,
        file_modifications,
        session_id,
    )
