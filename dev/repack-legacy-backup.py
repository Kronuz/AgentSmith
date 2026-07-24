#!/usr/bin/env python3
"""Repack the July 2026 mixed agent backup into verified Agentsmith bundles.

This is intentionally conservative: originals are read-only, byte-identical dumps
are deduplicated, uncertain cwd assignments get explicit unclassified bundles, and
conflicting project memories are preserved under separate provenance directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentsmith.continuation import detect_dump, parse_dump


@dataclass
class LegacySession:
    source: Path
    harness: str
    session_id: str
    cwd: str
    project: str
    created_at: str | None
    updated_at: str | None
    name: str


_COPILOT_PROJECTS = {
    "Agentsmith-copilot.jsonl": ("Agentsmith", "/Users/gmendezb/code/Agentsmith"),
    "Copilot-copilot.jsonl": ("Copilot", "/Users/gmendezb/code/Copilot"),
    "Copilot.jsonl": ("Copilot", "/Users/gmendezb/code/Copilot"),
    "EternalTerminal-copilot.jsonl": (
        "EternalTerminal",
        "/Users/gmendezb/code/EternalTerminal",
    ),
    "EternalTerminal.jsonl": (
        "EternalTerminal",
        "/Users/gmendezb/Development/EternalTerminal",
    ),
    "KronuZSH-copilot.jsonl": (
        "KronuZSH",
        "/Users/gmendezb/Development/KronuZSH",
    ),
    "Kronuz-copilot.jsonl": ("Kronuz", "/Users/gmendezb/Development/Kronuz"),
    "ROM-copilot.jsonl": ("ROM", "/Users/gmendezb/Development/ROM"),
    "SublimeText-copilot.jsonl": (
        "SublimeText",
        "/Users/gmendezb/Development/SublimeText",
    ),
    "homebrew-tap-copilot.jsonl": (
        "homebrew-tap",
        "/Users/gmendezb/code/homebrew-tap",
    ),
    "tmp.jsonl": ("tmp", "/Users/gmendezb/Development/tmp"),
    "xxx-copilot.jsonl": ("xxx", "/Users/gmendezb/Development/xxx"),
}

_SKIP_NAMES = {".DS_Store", ".gitignore", "README.md"}


def _events(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _inventory(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    ):
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": _digest(path),
            }
        )
    return entries


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _copy(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")


def _claude_metadata(path: Path) -> tuple[str, str, str | None, str | None]:
    ids: Counter[str] = Counter()
    cwds: Counter[str] = Counter()
    timestamps: list[str] = []
    for event in _events(path):
        session_id = event.get("sessionId")
        cwd = event.get("cwd")
        timestamp = event.get("timestamp")
        if isinstance(session_id, str):
            ids[session_id] += 1
        if isinstance(cwd, str):
            cwds[cwd] += 1
        if isinstance(timestamp, str):
            timestamps.append(timestamp)
    session_id = ids.most_common(1)[0][0] if ids else path.stem
    cwd = cwds.most_common(1)[0][0] if cwds else "(unknown)"
    return (
        session_id,
        cwd,
        min(timestamps) if timestamps else None,
        max(timestamps) if timestamps else None,
    )


def _copilot_metadata(path: Path) -> tuple[str, str | None, str | None]:
    session_id = path.stem
    timestamps: list[str] = []
    for event in _events(path):
        data = event.get("data")
        if not isinstance(data, dict):
            data = {}
        if event.get("type") == "session.start" and isinstance(
            data.get("sessionId"), str
        ):
            session_id = data["sessionId"]
        timestamp = event.get("timestamp")
        if isinstance(timestamp, str):
            timestamps.append(timestamp)
    return (
        session_id,
        min(timestamps) if timestamps else None,
        max(timestamps) if timestamps else None,
    )


def _project_from_cwd(cwd: str) -> str:
    if cwd in {
        "/Users/gmendezb/bkup",
        "/Users/gmendezb/laptop-backup",
    } or cwd.startswith(("/Users/gmendezb/bkup/", "/Users/gmendezb/laptop-backup/")):
        return "backup-maintenance"
    marker = "/Development/"
    if marker in cwd:
        return cwd.split(marker, 1)[1].split("/", 1)[0]
    marker = "/code/"
    if marker in cwd:
        return cwd.split(marker, 1)[1].split("/", 1)[0]
    if cwd.endswith("/gmendezb"):
        return "home"
    home_marker = "/Users/gmendezb/"
    if cwd.startswith(home_marker):
        return cwd[len(home_marker) :].split("/", 1)[0]
    return "unclassified"


def _sessions(backup: Path) -> tuple[list[LegacySession], list[tuple[Path, Path]]]:
    sessions: list[LegacySession] = []
    duplicates: list[tuple[Path, Path]] = []
    hashes: dict[str, Path] = {}
    candidates = sorted((backup / "dumps").glob("*.jsonl"))
    candidates.extend(
        path
        for path in sorted((backup / "claude-memory").glob("*/*.jsonl"))
        if path.parent.name != "subagents"
    )
    for source in candidates:
        digest = _digest(source)
        if digest in hashes:
            duplicates.append((source, hashes[digest]))
            continue
        hashes[digest] = source
        harness = detect_dump(source)
        if harness not in {"claude", "copilot"}:
            continue
        if harness == "claude":
            session_id, cwd, created, updated = _claude_metadata(source)
            project = _project_from_cwd(cwd)
        else:
            session_id, created, updated = _copilot_metadata(source)
            project, cwd = _COPILOT_PROJECTS.get(
                source.name, ("unclassified", "(unknown)")
            )
        sessions.append(
            LegacySession(
                source,
                harness,
                session_id,
                cwd,
                project,
                created,
                updated,
                source.stem,
            )
        )
    return sessions, duplicates


def _memory_sources(backup: Path) -> dict[str, list[tuple[str, Path]]]:
    result: dict[str, list[tuple[str, Path]]] = {}
    roots = (
        ("legacy-claude-memory", backup / "claude-memory"),
        (
            "laptop-agent-backup",
            backup / "laptop-backup" / "agents" / "claude" / "memories",
        ),
    )
    for provenance, root in roots:
        if not root.is_dir():
            continue
        for encoded in sorted(path for path in root.iterdir() if path.is_dir()):
            name = encoded.name
            marker = "-Development-"
            project = (
                name.split(marker, 1)[1].split("-", 1)[0] if marker in name else ""
            )
            if name == "-Users-gmendezb-bkup":
                project = "backup-maintenance"
            if not project:
                project = "home" if name == "-Users-gmendezb" else "unclassified"
            memory = encoded / "memory" if (encoded / "memory").is_dir() else encoded
            files = [
                path
                for path in memory.rglob("*")
                if path.is_file()
                and path.name not in _SKIP_NAMES
                and path.suffix.lower() in {".md", ".txt"}
            ]
            if files:
                result.setdefault(project, []).append((provenance, memory))
    return result


def _instruction_sources(backup: Path) -> dict[str, list[Path]]:
    root = backup / "laptop-backup" / "agents" / "project-instructions"
    result: dict[str, list[Path]] = {}
    if not root.is_dir():
        return result
    for project in sorted(path for path in root.iterdir() if path.is_dir()):
        files = [
            path
            for path in project.rglob("*")
            if path.is_file() and path.name not in _SKIP_NAMES
        ]
        if files:
            result[project.name] = files
    return result


def _conversation(session: LegacySession) -> str:
    messages = parse_dump(session.source, session.harness)
    lines = [
        f"# {session.name}",
        "",
        f"`{session.cwd}` · _{session.harness}_ · `{session.session_id}`",
        "",
    ]
    for message in messages:
        role = "User" if message.role == "user" else "Assistant"
        lines.extend((f"### {role}", "", message.text, ""))
    return "\n".join(lines)


def _project_bundle(
    destination: Path,
    project: str,
    sessions: list[LegacySession],
    memories: list[tuple[str, Path]],
    instructions: list[Path],
    instruction_root: Path,
) -> None:
    destination.mkdir(parents=True)
    manifest_sessions: list[dict[str, object]] = []
    memory_paths: list[str] = []
    for provenance, source in memories:
        target = destination / "project-memory" / "claude" / provenance
        if target.exists():
            target = target / _slug(source.parent.name)
        _copy(source, target)
        memory_paths.append(str(target.relative_to(destination)))
    environment: list[dict[str, str | None]] = []
    project_root = (
        sessions[0].cwd if sessions else f"/Users/gmendezb/Development/{project}"
    )
    key = hashlib.sha256(project_root.encode()).hexdigest()[:12]
    for source in instructions:
        relative = source.relative_to(instruction_root / project)
        harness = "claude" if source.name == "CLAUDE.md" else "copilot"
        if source.name == "AGENTS.md":
            harness = "shared"
        target = destination / "environment" / "projects" / key / harness / relative
        _copy(source, target)
        environment.append(
            {
                "scope": "project",
                "harness": harness,
                "project_root": project_root,
                "source": str(source),
                "path": str(target.relative_to(destination)),
            }
        )
    for session in sessions:
        root = destination / "sessions" / session.harness / session.session_id
        root.mkdir(parents=True)
        metadata = {
            "id": session.session_id,
            "harness": session.harness,
            "cwd": session.cwd,
            "repository": None,
            "branch": None,
            "name": session.name,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "resumable": False,
            "turns": None,
        }
        _write_json(root / "metadata.json", metadata)
        _write_json(root / "usage.json", list[object]())
        _write_json(root / "files.json", list[object]())
        (root / "conversation.md").write_text(_conversation(session))
        native = root / "native" / session.source.name
        _copy(session.source, native)
        sidecar = session.source.with_suffix("")
        native_paths = [str(native.relative_to(destination))]
        if sidecar.is_dir():
            target = root / "native" / sidecar.name
            _copy(sidecar, target)
            native_paths.append(str(target.relative_to(destination)))
        manifest_sessions.append(
            {
                "harness": session.harness,
                "id": session.session_id,
                "cwd": session.cwd,
                "native": native_paths,
                "project_memory": memory_paths,
            }
        )
    manifest = {
        "schema": "agentsmith-export",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": project_root,
        "recursive": True,
        "include_memory": True,
        "include_project_context": True,
        "recovered_from": "mixed legacy backup",
        "project": project,
        "project_memory": memory_paths,
        "environment": environment,
        "sessions": manifest_sessions,
        "inventory": _inventory(destination),
    }
    _write_json(destination / "manifest.json", manifest)


def _global_bundle(backup: Path, destination: Path) -> dict[str, int]:
    destination.mkdir(parents=True)
    entries: list[dict[str, object]] = []
    counts = {
        "claude": 0,
        "copilot": 0,
        "shared_instructions": 0,
        "copilot_conflicts": 0,
    }

    claude = backup / "laptop-backup" / "agents" / "claude"
    for source in sorted(path for path in claude.rglob("*") if path.is_file()):
        relative = source.relative_to(claude)
        if source.name in _SKIP_NAMES or relative.parts[0] == "memories":
            continue
        target = destination / "claude" / relative
        _copy(source, target)
        entries.append(
            {
                "scope": "global",
                "harness": "claude",
                "project_root": None,
                "destination": str(Path(".claude") / relative),
                "source": str(source),
                "path": str(target.relative_to(destination)),
            }
        )
        counts["claude"] += 1

    old = backup / "laptop-backup" / "agents" / "copilot"
    latest = backup / "dotcopilot"
    shared_sources = [
        latest / "copilot-instructions.md",
        *sorted((latest / "instructions").glob("*.md")),
    ]
    codex_sections: list[str] = []
    for source in shared_sources:
        relative = (
            Path("copilot-instructions.md")
            if source.name == "copilot-instructions.md"
            else Path(source.name)
        )
        target = destination / "shared" / "instructions" / relative
        _copy(source, target)
        if source.name == "copilot-instructions.md":
            destinations = [
                ".copilot/copilot-instructions.md",
                ".claude/rules/copilot-instructions.md",
            ]
        else:
            destinations = [
                str(Path(".copilot/instructions") / source.name),
                str(Path(".claude/rules") / source.name),
            ]
        entries.append(
            {
                "scope": "global",
                "harness": "shared",
                "project_root": None,
                "destination": None,
                "destinations": destinations,
                "source": str(source),
                "path": str(target.relative_to(destination)),
            }
        )
        codex_sections.extend(
            (
                f"<!-- source: shared/instructions/{relative} -->",
                source.read_text(errors="replace").rstrip(),
                "",
            )
        )
        counts["shared_instructions"] += 1
    codex_adapter = destination / "adapters" / "codex" / "AGENTS.md"
    codex_adapter.parent.mkdir(parents=True, exist_ok=True)
    codex_adapter.write_text(
        "# Shared user instructions\n\n" + "\n".join(codex_sections).rstrip() + "\n"
    )
    entries.append(
        {
            "scope": "global",
            "harness": "codex",
            "project_root": None,
            "destination": ".codex/AGENTS.md",
            "source": "generated from shared/instructions",
            "path": str(codex_adapter.relative_to(destination)),
        }
    )

    selected: dict[Path, Path] = {}
    for root in (old, latest):
        for source in sorted(path for path in root.rglob("*") if path.is_file()):
            relative = source.relative_to(root)
            if source.name in _SKIP_NAMES or ".git" in relative.parts:
                continue
            if relative == Path("copilot-instructions.md") or (
                relative.parts and relative.parts[0] == "instructions"
            ):
                continue
            if relative in selected and root == latest:
                counts["copilot_conflicts"] += 1
            selected[relative] = source
    for relative, source in sorted(selected.items()):
        target = destination / "copilot" / relative
        _copy(source, target)
        entries.append(
            {
                "scope": "global",
                "harness": "copilot",
                "project_root": None,
                "destination": str(Path(".copilot") / relative),
                "source": str(source),
                "path": str(target.relative_to(destination)),
            }
        )
        counts["copilot"] += 1
    (destination / "README.md").write_text(
        "# Global agent configuration\n\n"
        "Visible, portable copies of user-wide Claude and Copilot configuration.\n\n"
        "- `claude/` maps to `~/.claude/`\n"
        "- `copilot/` maps to `~/.copilot/`\n\n"
        "- `shared/instructions/` is the canonical cross-agent instruction set\n"
        "- `adapters/codex/AGENTS.md` is its consolidated Codex form\n\n"
        "Run `asmith import-global .` to create an editable candidate tree and "
        "critical-review `HANDOFF.md`. Files are never silently installed over "
        "existing configuration.\n"
    )
    manifest = {
        "schema": "agentsmith-global-export",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_precedence": [
            str(latest),
            str(old),
            str(claude),
        ],
        "environment": entries,
        "sessions": list[object](),
        "inventory": _inventory(destination),
    }
    _write_json(destination / "manifest.json", manifest)
    return counts


def run(backup: Path, destination: Path) -> None:
    backup = backup.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        sessions, duplicates = _sessions(backup)
        memories = _memory_sources(backup)
        instructions = _instruction_sources(backup)
        projects = sorted(
            {session.project for session in sessions}
            | set(memories)
            | set(instructions)
        )
        instruction_root = backup / "laptop-backup" / "agents" / "project-instructions"
        for project in projects:
            _project_bundle(
                staging / "projects" / _slug(project),
                project,
                [session for session in sessions if session.project == project],
                memories.get(project, []),
                instructions.get(project, []),
                instruction_root,
            )
        global_counts = _global_bundle(backup, staging / "global")
        lines = [
            "# Recovered Agentsmith exports",
            "",
            f"- Source: `{backup}`",
            f"- Unique sessions: {len(sessions)}",
            f"- Project bundles: {len(projects)}",
            f"- Exact duplicate dumps omitted: {len(duplicates)}",
            f"- Global Claude files: {global_counts['claude']}",
            f"- Global Copilot files: {global_counts['copilot']}",
            f"- Shared instruction files: {global_counts['shared_instructions']}",
            (
                "- Copilot conflicts resolved in favor of dotcopilot: "
                f"{global_counts['copilot_conflicts']}"
            ),
            "",
            "## Project bundles",
            "",
        ]
        for project in projects:
            count = sum(session.project == project for session in sessions)
            lines.append(f"- `projects/{_slug(project)}` — {count} session(s)")
        lines.extend(("", "## Exact duplicates", ""))
        for duplicate, retained in duplicates:
            lines.append(f"- `{duplicate}` = `{retained}` (retained once)")
        lines.extend(
            (
                "",
                "## Notes",
                "",
                "- Any `unclassified` bundle is intentionally not assigned by guess.",
                "- Authentication material was not included.",
                "- Original files were not modified.",
            )
        )
        (staging / "INDEX.md").write_text("\n".join(lines) + "\n")
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    run(args.backup, args.destination)


if __name__ == "__main__":
    main()
