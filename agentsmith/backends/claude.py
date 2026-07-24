"""Claude Code backend: JSONL transcripts under ~/.claude/projects."""

from __future__ import annotations

import glob
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, override

from ..config import CACHE_DIR
from ..model import (
    Checkpoint,
    FileTouch,
    PurgeReport,
    SearchHit,
    Session,
    UsageRow,
)
from ..purge import deep_purge
from ..util import clean_user, iso_from_mtime, parse_ts
from .base import Backend, Msg

CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
CLAUDE_PROJECTS = CLAUDE_HOME / "projects"


_CLAUDE_FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}


class ClaudeBackend(Backend):
    name = "claude"
    home = CLAUDE_HOME

    def __init__(self) -> None:
        self._index: dict[str, Session] | None = None
        self._paths: dict[str, Path] = {}

    @override
    def available(self) -> bool:
        return CLAUDE_PROJECTS.is_dir()

    def _main_files(self) -> list[Path]:
        return [Path(p) for p in glob.glob(str(CLAUDE_PROJECTS / "*" / "*.jsonl"))]

    def _cache_file(self) -> Path:
        return CACHE_DIR / "claude-index.json"

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self._cache_file().read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        temporary: str | None = None
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            destination = self._cache_file()
            with tempfile.NamedTemporaryFile(
                "w", dir=CACHE_DIR, prefix=f".{destination.name}.", delete=False
            ) as stream:
                temporary = stream.name
                json.dump(cache, stream)
            os.replace(temporary, destination)
        except OSError:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def _scan_file(self, path: Path) -> dict[str, Any]:
        cwd: str | None = None
        branch: str | None = None
        created: str | None = None
        name: str | None = None
        turns = 0
        with path.open() as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cwd is None and e.get("cwd"):
                    cwd = e.get("cwd")
                    created = e.get("timestamp")
                    branch = e.get("gitBranch")
                t = e.get("type")
                if t == "ai-title" and e.get("aiTitle"):
                    name = e.get("aiTitle")
                elif t == "user" and not e.get("isMeta"):
                    msg = e.get("message", {})
                    content = msg.get("content")
                    if (
                        isinstance(content, str)
                        or isinstance(content, list)
                        and any(
                            isinstance(b, dict) and b.get("type") == "text"
                            for b in content
                        )
                    ):
                        turns += 1
        return {
            "cwd": cwd,
            "branch": branch,
            "created": created,
            "name": name,
            "turns": turns,
        }

    def _build_index(self) -> dict[str, Session]:
        if self._index is not None:
            return self._index
        cache = self._load_cache()
        new_cache: dict[str, dict[str, Any]] = {}
        index: dict[str, Session] = {}
        for path in self._main_files():
            sid = path.stem
            self._paths[sid] = path
            st = path.stat()
            key = f"{st.st_mtime_ns}:{st.st_size}"
            entry = cache.get(str(path))
            if not entry or entry.get("key") != key:
                meta = self._scan_file(path)
                entry = {"key": key, **meta}
            new_cache[str(path)] = entry
            raw_turns = entry.get("turns")
            turns_val = int(raw_turns) if isinstance(raw_turns, (int, float)) else None
            index[sid] = Session(
                id=sid,
                harness="claude",
                cwd=entry.get("cwd"),
                branch=entry.get("branch"),
                name=entry.get("name"),
                created_at=entry.get("created"),
                updated_at=iso_from_mtime(path),
                resumable=True,
                turns=turns_val,
            )
        self._save_cache(new_cache)
        self._index = index
        return index

    @override
    def list_sessions(self) -> list[Session]:
        sessions = list(self._build_index().values())
        sessions.sort(key=lambda s: parse_ts(s.updated_at), reverse=True)
        return sessions

    @override
    def get(self, session_id: str) -> Session | None:
        return self._build_index().get(session_id)

    def _path(self, session_id: str) -> Path | None:
        self._build_index()
        return self._paths.get(session_id)

    def _iter_lines(self, session_id: str) -> Iterator[dict[str, Any]]:
        path = self._path(session_id)
        if path is None:
            return
        yield from self._iter_file(path)

    def _session_event_paths(self, session_id: str, subagents: bool) -> list[Path]:
        path = self._path(session_id)
        if path is None:
            return []
        paths = [path]
        subdir = path.with_suffix("") / "subagents"
        if subagents and subdir.is_dir():
            paths.extend(sorted(subdir.glob("*.jsonl")))
        return paths

    def _iter_file(self, path: Path) -> Iterator[dict[str, Any]]:
        try:
            fh = path.open()
        except OSError:
            return
        with fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @override
    def turn_count(self, session_id: str) -> int:
        s = self.get(session_id)
        return s.turns if s and s.turns is not None else 0

    @override
    def transcript(self, session_id: str, subagents: bool = True) -> list[Msg]:
        main = self._parse_stream(self._iter_lines(session_id), skip_sidechain=True)
        if subagents:
            path = self._path(session_id)
            if path is not None:
                subdir = path.with_suffix("") / "subagents"
                if subdir.is_dir():
                    for sf in sorted(
                        subdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime
                    ):
                        label = "claude-agent#" + sf.stem.replace("agent-", "")[:6]
                        sub = self._parse_stream(
                            self._iter_file(sf), skip_sidechain=False
                        )
                        for m in sub:
                            m.agent = label
                        main.extend(sub)
        return main

    def _parse_stream(
        self, events: Iterator[dict[str, Any]], skip_sidechain: bool
    ) -> list[Msg]:
        results: dict[str, dict[str, Any]] = {}
        msgs: list[Msg] = []
        for e in events:
            if e.get("isMeta") or (skip_sidechain and e.get("isSidechain")):
                continue
            t = e.get("type")
            msg = e.get("message", {})
            if t == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    text = clean_user(content)
                    if text:
                        msgs.append(Msg("user", text))
                elif isinstance(content, list):
                    texts: list[str] = []
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text":
                            texts.append(b.get("text", ""))
                        elif b.get("type") == "tool_result":
                            results[b.get("tool_use_id", "")] = {
                                "success": not b.get("is_error"),
                                "content": _claude_block_text(b.get("content")),
                            }
                    joined = clean_user("\n".join(texts))
                    if joined:
                        msgs.append(Msg("user", joined))
            elif t == "assistant":
                content = msg.get("content", [])
                m = Msg("assistant", "")
                text_parts: list[str] = []
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text":
                        text_parts.append(b.get("text", ""))
                    elif bt == "thinking":
                        m.reasoning += b.get("thinking", "")
                    elif bt == "tool_use":
                        raw_in = b.get("input")
                        args: dict[str, Any] = (
                            raw_in if isinstance(raw_in, dict) else {}
                        )
                        m.tools.append(
                            {
                                "name": b.get("name", "?"),
                                "summary": args.get("description", "")
                                or args.get("command", ""),
                                "arguments": raw_in if raw_in is not None else {},
                                "result": None,
                                "id": b.get("id", ""),
                            }
                        )
                m.text = "".join(text_parts)
                if m.text or m.tools or m.reasoning:
                    msgs.append(m)
        # attach tool results discovered on later user lines
        for m in msgs:
            for tl in m.tools:
                if tl.get("result") is None:
                    tl["result"] = results.get(tl.get("id", ""))
        return msgs

    @override
    def files(self, session_id: str, subagents: bool = True) -> list[FileTouch]:
        seen: dict[str, FileTouch] = {}
        for path in self._session_event_paths(session_id, subagents):
            for e in self._iter_file(path):
                if e.get("type") != "assistant":
                    continue
                content = e.get("message", {}).get("content", [])
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        name = b.get("name", "")
                        raw_in = b.get("input")
                        fp = (
                            raw_in.get("file_path")
                            if isinstance(raw_in, dict)
                            else None
                        )
                        if name in _CLAUDE_FILE_TOOLS and fp and fp not in seen:
                            seen[fp] = FileTouch(fp, name, None)
        return list(seen.values())

    @override
    def usage(self, session_id: str, subagents: bool = True) -> list[UsageRow]:
        agg: dict[str, dict[str, int]] = {}
        for path in self._session_event_paths(session_id, subagents):
            for e in self._iter_file(path):
                if e.get("type") != "assistant":
                    continue
                msg = e.get("message", {})
                u = msg.get("usage")
                if not isinstance(u, dict):
                    continue
                model = msg.get("model", "?")
                if model.startswith("<synthetic"):
                    continue
                a = agg.setdefault(
                    model, {"calls": 0, "i": 0, "o": 0, "cr": 0, "cw": 0}
                )
                a["calls"] += 1
                a["i"] += u.get("input_tokens", 0) or 0
                a["o"] += u.get("output_tokens", 0) or 0
                a["cr"] += u.get("cache_read_input_tokens", 0) or 0
                a["cw"] += u.get("cache_creation_input_tokens", 0) or 0
        return [
            UsageRow(
                model,
                a["calls"],
                a["i"],
                a["o"],
                cache_read=a["cr"],
                cache_write=a["cw"],
                aiu=None,
            )
            for model, a in agg.items()
        ]

    @override
    def checkpoints(self, session_id: str) -> list[Checkpoint]:
        return []

    @override
    def search(self, query: str, limit: int) -> list[SearchHit]:
        needle = query.lower()
        hits: list[SearchHit] = []
        for s in self.list_sessions():
            for m in self.transcript(s.id, subagents=False):
                hay = m.text
                idx = hay.lower().find(needle)
                if idx >= 0:
                    start = max(0, idx - 30)
                    snip = hay[start : idx + len(query) + 40].replace("\n", " ")
                    hits.append(SearchHit("claude", s.id, m.role, snip.strip()))
                    break
            if len(hits) >= limit:
                break
        return hits

    @override
    def resume_command(self, session_id: str) -> list[str]:
        return ["claude", "--resume", session_id]

    @override
    def raw_path(self, session_id: str) -> Path | None:
        return self._path(session_id)

    @override
    def state_location(self, session_id: str) -> Path | None:
        path = self._path(session_id)
        return path.parent if path is not None else None

    @override
    def artifact_paths(self, session_id: str) -> list[Path]:
        path = self._path(session_id)
        if path is None:
            return []
        artifacts = [path]
        subagents = path.with_suffix("") / "subagents"
        if subagents.is_dir():
            artifacts.append(subagents)
        return artifacts

    @override
    def memory_paths(self, session_id: str) -> list[Path]:
        path = self._path(session_id)
        if path is None:
            return []
        memory = path.parent / "memory"
        return [memory] if memory.is_dir() else []

    @override
    def remove(
        self, session_id: str, dry_run: bool = False, aggressive: bool = False
    ) -> PurgeReport:
        report = deep_purge(CLAUDE_HOME, session_id, dry_run, aggressive)
        if not dry_run:
            self._index = None
        return report


def _claude_block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""
