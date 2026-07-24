"""Portable, non-destructive session bundle export."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backends.base import Backend
from .model import Session

SCHEMA_VERSION = 1


@dataclass
class ExportItem:
    backend: Backend
    session: Session
    conversation_md: str


def _copy_artifact(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _inventory(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        files.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return files


def export_bundle(
    items: list[ExportItem],
    destination: Path,
    target: str,
    include_memory: bool,
    recursive: bool,
) -> None:
    """Atomically create a portable export directory."""
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    manifest_sessions: list[dict[str, object]] = []
    copied_memory: dict[Path, str] = {}
    try:
        for item in items:
            backend = item.backend
            session = item.session
            session_root = staging / "sessions" / session.harness / session.id
            session_root.mkdir(parents=True)
            _write_json(session_root / "metadata.json", asdict(session))
            _write_json(
                session_root / "usage.json",
                [asdict(row) for row in backend.usage(session.id)],
            )
            _write_json(
                session_root / "files.json",
                [asdict(touch) for touch in backend.files(session.id)],
            )
            (session_root / "conversation.md").write_text(item.conversation_md)

            native_paths: list[str] = []
            native_root = session_root / "native"
            for index, source in enumerate(backend.artifact_paths(session.id), 1):
                if not source.exists():
                    continue
                name = source.name
                target_path = native_root / name
                if target_path.exists():
                    target_path = native_root / f"{index}-{name}"
                _copy_artifact(source, target_path)
                native_paths.append(str(target_path.relative_to(staging)))

            memory_paths: list[str] = []
            if include_memory:
                for source in backend.memory_paths(session.id):
                    resolved = source.resolve()
                    relative = copied_memory.get(resolved)
                    if relative is None:
                        key = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
                        target_path = staging / "project-memory" / session.harness / key
                        _copy_artifact(source, target_path)
                        relative = str(target_path.relative_to(staging))
                        copied_memory[resolved] = relative
                    memory_paths.append(relative)

            manifest_sessions.append(
                {
                    "harness": session.harness,
                    "id": session.id,
                    "cwd": session.cwd,
                    "native": native_paths,
                    "project_memory": memory_paths,
                }
            )

        manifest = {
            "schema": "agentsmith-export",
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "target": target,
            "recursive": recursive,
            "include_memory": include_memory,
            "sessions": manifest_sessions,
            "inventory": _inventory(staging),
        }
        _write_json(staging / "manifest.json", manifest)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
