"""Prepare portable exports and native transcript dumps for continuation."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import STATE_DIR
from .export import verify_bundle
from .model import Msg


@dataclass
class ContinuationResult:
    root: Path
    handoff: Path
    sources: int
    sessions: int
    warnings: list[str]


def _json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def detect_dump(path: Path) -> str | None:
    """Identify the native JSONL dialect without relying on its filename."""
    for event in _json_lines(path):
        event_type = event.get("type")
        payload = event.get("payload")
        data = event.get("data")
        if event_type == "session_meta" or (
            isinstance(payload, dict)
            and payload.get("type") in {"user_message", "token_count"}
        ):
            return "codex"
        if event_type in {"user", "assistant", "ai-title"} and "message" in event:
            return "claude"
        if (
            event_type
            in {
                "user.message",
                "assistant.message",
                "tool.execution_complete",
                "subagent.started",
            }
            or isinstance(data, dict)
            and "parentToolCallId" in data
        ):
            return "copilot"
    return None


def _text_blocks(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if block.get("type") in {"text", "input_text", "output_text"} and isinstance(
            text, str
        ):
            parts.append(text)
    return "\n".join(parts)


def parse_dump(path: Path, harness: str) -> list[Msg]:
    """Extract the portable conversation subset from one native transcript."""
    messages: list[Msg] = []
    for event in _json_lines(path):
        event_type = event.get("type")
        if harness == "claude" and event_type in {"user", "assistant"}:
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            text = _text_blocks(message.get("content"))
            if text:
                messages.append(Msg(str(event_type), text))
        elif harness == "copilot":
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            if event_type == "user.message":
                text = data.get("content")
                if isinstance(text, str) and text:
                    messages.append(Msg("user", text))
            elif event_type == "assistant.message":
                text = data.get("content")
                if isinstance(text, str) and text:
                    messages.append(Msg("assistant", text))
        elif harness == "codex":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event_type == "event_msg" and payload.get("type") == "user_message":
                text = payload.get("message")
                if isinstance(text, str) and text:
                    messages.append(Msg("user", text))
            elif (
                event_type == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "assistant"
            ):
                text = _text_blocks(payload.get("content"))
                if text:
                    messages.append(Msg("assistant", text))
    return messages


def _default_destination(destination_harness: str) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return STATE_DIR / "imports" / f"{stamp}-{destination_harness}"


def _copy_source(source: Path, destination: Path, index: int) -> str:
    name = f"{index:03d}-{source.name}"
    target = destination / "sources" / name
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return str(target.relative_to(destination))


def _bundle_conversations(source: Path) -> tuple[list[str], int, list[str]]:
    verified = verify_bundle(source)
    if verified.errors:
        raise ValueError("invalid export bundle: " + "; ".join(verified.errors[:3]))
    manifest = json.loads((source / "manifest.json").read_text())
    sessions = manifest.get("sessions", [])
    conversations: list[str] = []
    warnings: list[str] = []
    for entry in sessions:
        if not isinstance(entry, dict):
            continue
        harness = entry.get("harness")
        session_id = entry.get("id")
        path = source / "sessions" / str(harness) / str(session_id) / "conversation.md"
        if path.is_file():
            conversations.append(path.read_text(errors="replace"))
        else:
            warnings.append(
                f"missing normalized conversation for {harness}:{session_id}"
            )
    return conversations, len(sessions), warnings


def _render_dump(path: Path, harness: str, messages: list[Msg]) -> str:
    lines = [f"## Native {harness} dump: `{path.name}`", ""]
    for message in messages:
        label = "User" if message.role == "user" else "Assistant"
        lines.extend((f"### {label}", "", message.text, ""))
    return "\n".join(lines)


def prepare_continuation(
    sources: list[Path],
    destination_harness: str,
    cwd: Path,
    destination: Path | None = None,
    source_harness: str | None = None,
) -> ContinuationResult:
    """Create an atomic, reviewable continuation directory."""
    root = (destination or _default_destination(destination_harness)).expanduser()
    root = root.resolve()
    if root.exists():
        raise FileExistsError(f"destination already exists: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    conversations: list[str] = []
    warnings: list[str] = []
    source_records: list[dict[str, object]] = []
    session_count = 0
    try:
        for index, original in enumerate(sources, 1):
            source = original.expanduser().resolve()
            if not source.exists():
                raise FileNotFoundError(f"source does not exist: {source}")
            if source.is_dir() and (source / "manifest.json").is_file():
                blocks, count, bundle_warnings = _bundle_conversations(source)
                conversations.extend(blocks)
                session_count += count
                warnings.extend(bundle_warnings)
                kind = "agentsmith-export"
                harness: str | None = None
            elif source.is_file():
                harness = source_harness or detect_dump(source)
                if harness is None:
                    raise ValueError(f"cannot detect dump format: {source}; use --from")
                messages = parse_dump(source, harness)
                if not messages:
                    warnings.append(f"no conversation messages recovered from {source}")
                conversations.append(_render_dump(source, harness, messages))
                session_count += 1
                kind = "native-dump"
                warnings.append(
                    f"{source.name}: raw dump may omit sidecars, memory, child sessions, "
                    "usage, and file metadata"
                )
            else:
                raise ValueError(f"source is not an export bundle or file: {source}")
            copied = _copy_source(source, staging, index)
            source_records.append(
                {"input": str(source), "copy": copied, "kind": kind, "harness": harness}
            )

        handoff = staging / "HANDOFF.md"
        header = (
            "# Agentsmith continuation handoff\n\n"
            f"- Destination agent: **{destination_harness}**\n"
            f"- Working directory: `{cwd.expanduser().resolve()}`\n"
            f"- Sources: {len(sources)}\n"
            f"- Recovered sessions: {session_count}\n\n"
            "Continue the work represented below. First inspect the current working "
            "tree and reconcile it with this history; do not assume the historical "
            "file state still matches disk. Preserve unfinished objectives, decisions, "
            "and constraints. The original inputs are retained under `sources/`.\n\n"
            "---\n\n"
        )
        handoff.write_text(header + "\n\n---\n\n".join(conversations) + "\n")
        manifest = {
            "schema": "agentsmith-continuation",
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "destination_harness": destination_harness,
            "cwd": str(cwd.expanduser().resolve()),
            "handoff": "HANDOFF.md",
            "sources": source_records,
            "sessions": session_count,
            "warnings": warnings,
            "handoff_sha256": hashlib.sha256(handoff.read_bytes()).hexdigest(),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        )
        staging.replace(root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ContinuationResult(
        root, root / "HANDOFF.md", len(sources), session_count, warnings
    )


def launch_command(result: ContinuationResult, harness: str, cwd: Path) -> list[str]:
    prompt = (
        f"Read {result.handoff}, verify its history against the current working tree, "
        "and continue the unfinished work."
    )
    if harness == "codex":
        return [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(cwd),
            prompt,
        ]
    if harness == "claude":
        return ["claude", "--dangerously-skip-permissions", prompt]
    if harness == "copilot":
        return ["copilot", "--yolo", prompt]
    raise ValueError(f"unsupported destination harness: {harness}")
