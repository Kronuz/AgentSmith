"""Persistent usage summaries keyed by native artifact metadata."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import CACHE_DIR
from .model import UsageRow

if TYPE_CHECKING:
    from .backends.base import Backend


def _files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(child for child in path.rglob("*") if child.is_file())
    return files


def _signature(paths: list[Path]) -> list[list[str | int]]:
    signature: list[list[str | int]] = []
    for path in sorted(_files(paths)):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append([str(path), stat.st_mtime_ns, stat.st_size])
    return signature


def _decode_rows(value: Any) -> list[UsageRow] | None:
    if not isinstance(value, list):
        return None
    rows: list[UsageRow] = []
    try:
        for row in value:
            if not isinstance(row, dict):
                return None
            rows.append(UsageRow(**row))
    except (TypeError, ValueError):
        return None
    return rows


def usage_for(
    backend: Backend, session_id: str, subagents: bool = True
) -> list[UsageRow]:
    artifacts = backend.artifact_paths(session_id)
    signature = _signature(artifacts)
    if not signature:
        return backend.usage(session_id, subagents=subagents)
    suffix = "" if subagents else "-main"
    cache = CACHE_DIR / "usage" / backend.name / f"{session_id}{suffix}.json"
    try:
        value = json.loads(cache.read_text())
        if value.get("signature") == signature:
            rows = _decode_rows(value.get("rows"))
            if rows is not None:
                return rows
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    rows = backend.usage(session_id, subagents=subagents)
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"signature": signature, "rows": [asdict(row) for row in rows]},
        separators=(",", ":"),
    )
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=cache.parent, prefix=f".{cache.name}.", delete=False
        ) as stream:
            temporary = stream.name
            stream.write(payload)
        os.replace(temporary, cache)
    except OSError:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return rows
