"""Prepare portable exports and native transcript dumps for continuation."""

from __future__ import annotations

import gzip
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


@dataclass
class GlobalImportResult:
    root: Path
    handoff: Path
    files: int


def _ingestion_protocol() -> str:
    return """## Required ingestion protocol

Before acting, perform exhaustive ingestion. Read this entire handoff in chunks if necessary.

1. Inventory the adjacent manifest and preserved sources. Inspect every normalized conversation, memory, project context, instruction, skill, hook, configuration, and other relevant artifact.
2. Consult native artifacts where normalized material has gaps. Read transcript chunks through normal tools so the inspected material is evidenced in the new native session.
3. Account for every source and recovered session in a coverage ledger in your response.
4. Extract concrete objectives, decisions, constraints, open tasks, unresolved questions, and referenced files. Make the coverage ledger and consolidated continuation state durable conversation turns.
5. Do not substitute a vague overview or silently skip material because it is long. Explicitly identify anything unreadable, unsupported, duplicated, or intentionally omitted.
6. Do not fabricate historical user/assistant turns or write private session-store records; preserve provenance instead.
7. Complete ingestion before changing the project or live configuration."""


def _json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with (
        gzip.open(path, mode="rt", errors="replace")
        if path.suffix == ".gz"
        else path.open(errors="replace")
    ) as stream:
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


def _default_destination() -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return STATE_DIR / "imports" / f"{stamp}-handoff"


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
        memory = entry.get("project_memory")
        if isinstance(memory, list) and memory:
            conversations.append(
                "## Preserved project memory\n\n"
                + "\n".join(f"- `{item}`" for item in memory)
            )
    environment = manifest.get("environment")
    if isinstance(environment, list) and environment:
        lines = [
            "## Staged agent environment",
            "",
            (
                "These files are preserved for review. Reconcile them with the "
                "destination project/machine; do not blindly overwrite active "
                "configuration."
            ),
            "",
        ]
        for entry in environment:
            if isinstance(entry, dict):
                lines.append(
                    f"- `{entry.get('path', '?')}` "
                    f"({entry.get('scope', '?')}, {entry.get('harness', '?')})"
                )
        conversations.append("\n".join(lines))
        warnings.append(
            f"{len(environment)} environment file(s) staged for review, not installed"
        )
    bundle_memory = manifest.get("project_memory")
    if isinstance(bundle_memory, list) and bundle_memory:
        conversations.append(
            "## Preserved project memory\n\n"
            + "\n".join(f"- `{item}`" for item in bundle_memory)
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
    cwd: Path,
    destination: Path | None = None,
    source_harness: str | None = None,
) -> ContinuationResult:
    """Create an atomic, reviewable continuation directory."""
    root = (destination or _default_destination()).expanduser()
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
            elif source.is_dir():
                candidates = [
                    candidate
                    for name in ("events.jsonl", "events.jsonl.gz")
                    if (candidate := source / name).is_file()
                ]
                if not candidates:
                    candidates = sorted(
                        (
                            *source.glob("*.jsonl"),
                            *source.glob("*.jsonl.gz"),
                        )
                    )
                if not candidates:
                    raise ValueError(
                        f"native archive contains no recognizable transcripts: {source}"
                    )
                detected_harnesses: set[str] = set()
                for transcript in candidates:
                    transcript_harness = source_harness or detect_dump(transcript)
                    if transcript_harness is None:
                        raise ValueError(
                            f"cannot detect dump format: {transcript}; use --from"
                        )
                    detected_harnesses.add(transcript_harness)
                    messages = parse_dump(transcript, transcript_harness)
                    if not messages:
                        warnings.append(
                            f"no conversation messages recovered from {transcript}"
                        )
                    conversations.append(
                        _render_dump(transcript, transcript_harness, messages)
                    )
                session_count += len(candidates)
                harness = (
                    next(iter(detected_harnesses))
                    if len(detected_harnesses) == 1
                    else None
                )
                kind = "native-archive"
                warnings.append(
                    f"{source.name}: companion files were preserved and "
                    f"{len(candidates)} transcript(s) were normalized into HANDOFF.md"
                )
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
            "# AgentSmith continuation handoff\n\n"
            f"- Working directory: `{cwd.expanduser().resolve()}`\n"
            f"- Sources: {len(sources)}\n"
            f"- Recovered sessions: {session_count}\n\n"
            "This handoff is agent-neutral. Launch it with "
            "`asmith launch AGENT HANDOFF.md`.\n\n"
            f"{_ingestion_protocol()}\n\n"
            "---\n\n"
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


def handoff_launch_command(
    handoff: Path,
    harness: str,
    cwd: Path,
) -> list[str]:
    prompt = f"Read and follow the handoff at {handoff}."
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


def launch_command(
    result: ContinuationResult,
    harness: str,
    cwd: Path,
) -> list[str]:
    return handoff_launch_command(result.handoff, harness, cwd)


def global_launch_command(
    result: GlobalImportResult, harness: str, cwd: Path
) -> list[str]:
    return handoff_launch_command(result.handoff, harness, cwd)


def prepare_global_import(
    source: Path, destination: Path | None = None
) -> GlobalImportResult:
    """Stage a global configuration bundle without overwriting live configuration."""
    source = source.expanduser().resolve()
    verified = verify_bundle(source)
    if verified.errors:
        raise ValueError("invalid global bundle: " + "; ".join(verified.errors[:3]))
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("schema") != "agentsmith-global-export":
        raise ValueError("bundle is not a global agent configuration export")
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    root = destination or STATE_DIR / "global-imports" / f"{stamp}-agent-configuration"
    root = root.expanduser().resolve()
    if root.exists():
        raise FileExistsError(f"destination already exists: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    environment = manifest.get("environment")
    entries = environment if isinstance(environment, list) else []
    candidate_records: list[dict[str, object]] = []
    try:
        shutil.copytree(source, staging / "source")
        lines = [
            "# Global agent configuration handoff",
            "",
            (
                "Nothing has been installed. `source/` is the untouched verified "
                "export; `candidate/` is the visible, editable selection proposed "
                "for import."
            ),
            "",
            (
                "The agent launched with this handoff is the sole destination agent. "
                "Treat all Claude/Codex/Copilot material as migration input to reconcile "
                "into that agent's native layout; do not restore every source agent."
            ),
            "",
            _ingestion_protocol(),
            "",
            "## Required review protocol",
            "",
            (
                "1. Treat deletion from `candidate/` as an explicit user exclusion. "
                "Never restore a deleted candidate from `source/`."
            ),
            (
                "2. Inventory the files that actually remain in `candidate/`; do not "
                "rely blindly on this handoff or the original manifest."
            ),
            (
                "3. Compare every remaining candidate with live configuration. Merge "
                "deliberately; never overwrite a live file wholesale without "
                "justification."
            ),
            (
                "4. Preserve applicable instruction meaning verbatim whenever the "
                "destination supports it. Make only the minimum changes required for "
                "destination syntax, deduplication, conflicts, path portability, or "
                "user-approved policy changes, and disclose every substantive change."
            ),
            (
                "5. Normalize paths and cross-file references into the destination "
                "agent's native layout. The installed result must not depend at runtime "
                "on another agent's home (such as `~/.claude` or `~/.copilot`) merely "
                "because that agent supplied the source."
            ),
            (
                "6. The installed result must be self-contained after migration. Copy "
                "or merge every retained dependency into the destination's live "
                "configuration; never leave references to the original export bundle, "
                "`source/`, `candidate/`, or any other prepared-import path. The user "
                "will delete both the export and prepared import after validation."
            ),
            (
                "7. Consolidate overlapping instruction sources for the selected "
                "destination instead of installing parallel Claude/Codex/Copilot copies. "
                "Treat the destination map below as source provenance and native restore "
                "hints, not as an order to install every agent's configuration."
            ),
            (
                "8. Critically assess instructions and hooks for this machine and work "
                "environment. Flag restrictions on SSH, networking, tools, filesystem "
                "access, external services, internal/company systems, or agent autonomy."
            ),
            (
                "9. Check references and dependencies among kept files. If a kept file "
                "references a deleted or missing file, propose removing/adapting the "
                "reference or restoring the dependency, and ask the user which."
            ),
            (
                "10. Present a keep/adapt/omit plan, all conflicts, and all potentially "
                "functionality-limiting policies. Ask for explicit approval before "
                "writing anything under the live home configuration."
            ),
            "",
            "## Candidate destination map",
            "",
        ]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            bundle_path = str(entry.get("path", "?"))
            relative_source = Path(bundle_path)
            if relative_source.is_absolute() or ".." in relative_source.parts:
                raise ValueError(f"unsafe environment path: {bundle_path}")
            exported = source / relative_source
            if not exported.is_file():
                raise ValueError(f"missing exported environment file: {bundle_path}")
            candidate_parts = relative_source.parts
            if candidate_parts and candidate_parts[0] == "global":
                candidate_relative = Path(*candidate_parts[1:])
            else:
                candidate_relative = relative_source
            candidate = staging / "candidate" / candidate_relative
            candidate.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exported, candidate)

            destinations: list[str] = []
            multiple_destinations = entry.get("destinations")
            if isinstance(multiple_destinations, list):
                for mapped in multiple_destinations:
                    if isinstance(mapped, str):
                        destinations.append(mapped)
            else:
                explicit_destination = entry.get("destination")
                if isinstance(explicit_destination, str):
                    destinations.append(explicit_destination)
                else:
                    parts = Path(bundle_path).parts
                    fallback = Path(*parts[3:]) if len(parts) > 3 else Path("?")
                    destinations.append(str(fallback))

            text = exported.read_text(errors="replace")[:1_000_000].lower()
            flags: list[str] = []
            lower_path = str(candidate_relative).lower()
            if "/hooks/" in f"/{lower_path}/" or lower_path.endswith(".sh"):
                flags.append("executable behavior")
            if any(
                part in lower_path for part in ("settings", "mcp-config", "config.")
            ):
                flags.append("active configuration")
            policy_terms = [
                term
                for term in (
                    "deny",
                    "forbid",
                    "must not",
                    "never ",
                    "ssh",
                    "network",
                    "mcp",
                    "external service",
                    "corporate",
                    "company",
                    "enterprise",
                    "employer",
                    "work-only",
                    "internal",
                )
                if term in text
            ]
            if policy_terms:
                flags.append("review policy terms: " + ", ".join(policy_terms))
            display = str(Path("candidate") / candidate_relative)
            suffix = f" — **review: {'; '.join(flags)}**" if flags else ""
            lines.append(
                f"- `{display}` → "
                + ", ".join(f"`~/{mapped}`" for mapped in destinations)
                + suffix
            )
            candidate_records.append(
                {
                    "candidate": display,
                    "source": bundle_path,
                    "harness": entry.get("harness"),
                    "destinations": destinations,
                    "review_flags": flags,
                }
            )
        handoff = staging / "HANDOFF.md"
        handoff.write_text("\n".join(lines) + "\n")
        staged_manifest = {
            "schema": "agentsmith-global-import",
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "source",
            "candidate": "candidate",
            "files": len(entries),
            "handoff": "HANDOFF.md",
            "entries": candidate_records,
        }
        (staging / "manifest.json").write_text(
            json.dumps(staged_manifest, indent=2, ensure_ascii=False) + "\n"
        )
        staging.replace(root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return GlobalImportResult(root, root / "HANDOFF.md", len(entries))
