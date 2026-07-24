"""Codex CLI backend: SQLite thread index + rollout JSONL transcripts."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, override

from ..model import (
    Checkpoint,
    FileTouch,
    Msg,
    PurgeReport,
    SearchHit,
    Session,
    UsageRow,
)
from ..purge import deep_purge
from ..util import clean_user
from .base import Backend

CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _default_db() -> Path:
    candidates = list(CODEX_HOME.glob("state_*.sqlite"))
    if candidates:
        return max(
            candidates,
            key=lambda path: (
                int(path.stem.removeprefix("state_"))
                if path.stem.removeprefix("state_").isdigit()
                else -1
            ),
        )
    return CODEX_HOME / "state_5.sqlite"


CODEX_DB = Path(os.environ["CODEX_DB"]) if "CODEX_DB" in os.environ else _default_db()
CODEX_SESSIONS = Path(os.environ.get("CODEX_SESSIONS", CODEX_HOME / "sessions"))

_FILE_TOOLS = {
    "apply_patch",
    "edit_file",
    "write_file",
    "create_file",
    "delete_file",
    "view_image",
}
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


def _iso_ms(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict)
        and block.get("type") in {"input_text", "output_text", "text"}
    )


class CodexBackend(Backend):
    name = "codex"
    home = CODEX_HOME

    def __init__(self) -> None:
        self._con: sqlite3.Connection | None = None
        self._sessions: dict[str, Session] | None = None
        self._paths: dict[str, Path] = {}

    @override
    def available(self) -> bool:
        return CODEX_DB.exists() or CODEX_SESSIONS.is_dir()

    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{CODEX_DB}?mode=ro", uri=True)
            self._con.row_factory = sqlite3.Row
        return self._con

    def _from_db(self) -> dict[str, Session]:
        if not CODEX_DB.exists():
            return {}
        rows = self.con().execute("SELECT * FROM threads").fetchall()
        out: dict[str, Session] = {}
        for row in rows:
            keys = set(row.keys())
            sid = str(row["id"])
            raw_path = Path(str(row["rollout_path"]))
            self._paths[sid] = raw_path
            created_ms = row["created_at_ms"] if "created_at_ms" in keys else None
            updated_ms = row["updated_at_ms"] if "updated_at_ms" in keys else None
            if not created_ms:
                created_ms = int(row["created_at"]) * 1000
            if not updated_ms:
                updated_ms = int(row["updated_at"]) * 1000
            name = None
            if "name" in keys and row["name"]:
                name = str(row["name"])
            elif row["title"]:
                name = str(row["title"])
            out[sid] = Session(
                id=sid,
                harness="codex",
                cwd=str(row["cwd"]) if row["cwd"] else None,
                repository=(
                    str(row["git_origin_url"])
                    if "git_origin_url" in keys and row["git_origin_url"]
                    else None
                ),
                branch=(
                    str(row["git_branch"])
                    if "git_branch" in keys and row["git_branch"]
                    else None
                ),
                name=name,
                created_at=_iso_ms(created_ms),
                updated_at=_iso_ms(updated_ms),
                resumable=raw_path.exists()
                and not bool(row["archived"] if "archived" in keys else 0),
            )
        return out

    def _from_rollouts(self) -> dict[str, Session]:
        out: dict[str, Session] = {}
        if not CODEX_SESSIONS.is_dir():
            return out
        for path in CODEX_SESSIONS.glob("**/*.jsonl"):
            try:
                first = next(self._iter_file(path))
                payload = first.get("payload", {})
                sid = str(
                    payload.get("id") or payload.get("session_id") or path.stem[-36:]
                )
                stat = path.stat()
            except (StopIteration, OSError):
                continue
            self._paths[sid] = path
            git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
            ts = payload.get("timestamp")
            out[sid] = Session(
                id=sid,
                harness="codex",
                cwd=payload.get("cwd"),
                repository=git.get("repository_url"),
                branch=git.get("branch"),
                created_at=ts
                if isinstance(ts, str)
                else _iso_ms(stat.st_ctime_ns / 1e6),
                updated_at=_iso_ms(stat.st_mtime_ns / 1e6),
                resumable=True,
            )
        return out

    def _index(self) -> dict[str, Session]:
        if self._sessions is None:
            self._sessions = self._from_db()
            for sid, session in self._from_rollouts().items():
                self._sessions.setdefault(sid, session)
        return self._sessions

    @override
    def list_sessions(self) -> list[Session]:
        children: set[str] = set()
        if CODEX_DB.exists():
            try:
                children = {
                    str(row[0])
                    for row in self.con().execute(
                        "SELECT child_thread_id FROM thread_spawn_edges"
                    )
                }
            except sqlite3.OperationalError:
                pass
        sessions = [
            session for sid, session in self._index().items() if sid not in children
        ]
        sessions.sort(key=lambda s: s.updated_at or "", reverse=True)
        return sessions

    @override
    def get(self, session_id: str) -> Session | None:
        return self._index().get(session_id)

    def _path(self, session_id: str) -> Path | None:
        self._index()
        return self._paths.get(session_id)

    def _iter_file(self, path: Path) -> Iterator[dict[str, Any]]:
        try:
            fh = path.open()
        except OSError:
            return
        with fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value

    def _events(self, session_id: str) -> Iterator[dict[str, Any]]:
        path = self._path(session_id)
        if path is not None:
            yield from self._iter_file(path)

    @override
    def transcript(self, session_id: str, subagents: bool = True) -> list[Msg]:
        results: dict[str, dict[str, Any]] = {}
        messages: list[Msg] = []
        for event in self._events(session_id):
            payload = event.get("payload", {})
            if (
                event.get("type") == "event_msg"
                and payload.get("type") == "user_message"
            ):
                text = clean_user(str(payload.get("message", "")))
                if text:
                    messages.append(Msg("user", text))
                continue
            if event.get("type") != "response_item":
                continue
            kind = payload.get("type")
            if kind in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(payload.get("call_id", ""))
                results[call_id] = {
                    "success": True,
                    "content": payload.get("output", ""),
                }
            elif kind == "message" and payload.get("role") == "assistant":
                text = _content_text(payload.get("content"))
                if text:
                    messages.append(Msg("assistant", text))
            elif kind == "reasoning":
                summary = _content_text(payload.get("summary"))
                if summary:
                    if not messages or messages[-1].role != "assistant":
                        messages.append(Msg("assistant", ""))
                    messages[-1].reasoning += summary
            elif kind in {"function_call", "custom_tool_call"}:
                raw_args = payload.get("arguments", {"input": payload.get("input", "")})
                args: dict[str, Any]
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"input": raw_args}
                else:
                    args = raw_args if isinstance(raw_args, dict) else {}
                if not messages or messages[-1].role != "assistant":
                    messages.append(Msg("assistant", ""))
                messages[-1].tools.append(
                    {
                        "name": payload.get("name", "?"),
                        "summary": args.get("description", "") or args.get("cmd", ""),
                        "arguments": args,
                        "result": None,
                        "id": str(payload.get("call_id", "")),
                    }
                )
        for message in messages:
            for tool in message.tools:
                tool["result"] = results.get(tool.pop("id", ""))

        if subagents and CODEX_DB.exists():
            try:
                for child_id in self._child_ids(session_id):
                    for message in self.transcript(child_id, subagents=False):
                        message.agent = "codex-agent#" + child_id[:6]
                        messages.append(message)
            except sqlite3.OperationalError:
                pass
        return messages

    @override
    def turn_count(self, session_id: str) -> int:
        return sum(
            1
            for event in self._events(session_id)
            if event.get("type") == "event_msg"
            and event.get("payload", {}).get("type") == "user_message"
        )

    @override
    def files(self, session_id: str) -> list[FileTouch]:
        seen: dict[str, FileTouch] = {}
        for sid in [session_id, *self._child_ids(session_id)]:
            for event in self._events(sid):
                payload = event.get("payload", {})
                if event.get("type") != "response_item" or payload.get("type") not in {
                    "function_call",
                    "custom_tool_call",
                }:
                    continue
                name = str(payload.get("name", ""))
                if name.rsplit(".", 1)[-1] not in _FILE_TOOLS:
                    continue
                raw = payload.get("arguments", {"input": payload.get("input", "")})
                args: dict[str, Any]
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    args = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    args = {}
                paths = [
                    str(args[key])
                    for key in ("file_path", "path")
                    if isinstance(args.get(key), str)
                ]
                patch = args.get("patch") or args.get("input")
                if isinstance(patch, str):
                    paths.extend(_PATCH_PATH.findall(patch))
                for path in paths:
                    seen.setdefault(path, FileTouch(path, name, None))
        return list(seen.values())

    @override
    def usage(self, session_id: str) -> list[UsageRow]:
        agg: dict[str, dict[str, int]] = {}
        for sid in [session_id, *self._child_ids(session_id)]:
            model = "?"
            for event in self._events(sid):
                payload = event.get("payload", {})
                if event.get("type") == "turn_context" and payload.get("model"):
                    model = str(payload["model"])
                if (
                    event.get("type") != "event_msg"
                    or payload.get("type") != "token_count"
                ):
                    continue
                info = payload.get("info")
                usage = info.get("last_token_usage") if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                row = agg.setdefault(
                    model, {"calls": 0, "i": 0, "o": 0, "cr": 0, "cw": 0, "r": 0}
                )
                row["calls"] += 1
                total_input = int(usage.get("input_tokens", 0) or 0)
                cached_input = int(usage.get("cached_input_tokens", 0) or 0)
                # OpenAI reports cached input as a subset of input_tokens. UsageRow
                # keeps fresh input and cache reads disjoint across backends.
                row["i"] += max(0, total_input - cached_input)
                row["o"] += int(usage.get("output_tokens", 0) or 0)
                row["cr"] += cached_input
                row["cw"] += int(usage.get("cache_write_input_tokens", 0) or 0)
                row["r"] += int(usage.get("reasoning_output_tokens", 0) or 0)
        return [
            UsageRow(model, a["calls"], a["i"], a["o"], a["cr"], a["cw"], a["r"])
            for model, a in agg.items()
        ]

    @override
    def checkpoints(self, session_id: str) -> list[Checkpoint]:
        return []

    @override
    def search(self, query: str, limit: int) -> list[SearchHit]:
        needle = query.lower()
        hits: list[SearchHit] = []
        for session in self.list_sessions():
            for message in self.transcript(session.id, subagents=False):
                index = message.text.lower().find(needle)
                if index >= 0:
                    start = max(0, index - 30)
                    snippet = message.text[start : index + len(query) + 40]
                    hits.append(
                        SearchHit(
                            "codex",
                            session.id,
                            message.role,
                            snippet.replace("\n", " ").strip(),
                        )
                    )
                    break
            if len(hits) >= limit:
                break
        return hits

    @override
    def resume_command(self, session_id: str) -> list[str]:
        return [
            "codex",
            "resume",
            session_id,
            "--dangerously-bypass-approvals-and-sandbox",
        ]

    @override
    def raw_path(self, session_id: str) -> Path | None:
        path = self._path(session_id)
        return path if path and path.exists() else None

    @override
    def state_location(self, session_id: str) -> Path | None:
        path = self.raw_path(session_id)
        return path.parent if path is not None else None

    def _child_ids(self, session_id: str) -> list[str]:
        if not CODEX_DB.exists():
            return []
        try:
            rows = self.con().execute(
                "WITH RECURSIVE descendants(id) AS ("
                " SELECT child_thread_id FROM thread_spawn_edges"
                " WHERE parent_thread_id = ?"
                " UNION ALL"
                " SELECT e.child_thread_id FROM thread_spawn_edges e"
                " JOIN descendants d ON e.parent_thread_id = d.id"
                ") SELECT id FROM descendants",
                (session_id,),
            )
            return [str(row[0]) for row in rows]
        except sqlite3.OperationalError:
            return []

    @override
    def artifact_paths(self, session_id: str) -> list[Path]:
        artifacts: list[Path] = []
        for sid in [session_id, *self._child_ids(session_id)]:
            path = self._path(sid)
            if path is not None and path.exists():
                artifacts.append(path)
            artifacts.extend(CODEX_HOME.glob(f"shell_snapshots/{sid}.*"))
        return artifacts

    @override
    def remove(
        self, session_id: str, dry_run: bool = False, aggressive: bool = False
    ) -> PurgeReport:
        session_ids = [session_id, *self._child_ids(session_id)]
        placeholders = ",".join("?" for _sid in session_ids)
        rows = 0
        if CODEX_DB.exists():
            try:
                rows = int(
                    self.con()
                    .execute(
                        f"SELECT COUNT(*) FROM threads WHERE id IN ({placeholders})",
                        session_ids,
                    )
                    .fetchone()[0]
                )
            except sqlite3.OperationalError:
                pass
            if not dry_run:
                rw = sqlite3.connect(str(CODEX_DB), timeout=10.0)
                rw.execute("PRAGMA busy_timeout = 5000")
                try:
                    with rw:
                        for table, column in (
                            ("thread_dynamic_tools", "thread_id"),
                            ("thread_spawn_edges", "child_thread_id"),
                            ("thread_spawn_edges", "parent_thread_id"),
                        ):
                            try:
                                rw.execute(
                                    f"DELETE FROM {table} "
                                    f"WHERE {column} IN ({placeholders})",
                                    session_ids,
                                )
                            except sqlite3.OperationalError:
                                pass
                        rw.execute(
                            f"DELETE FROM threads WHERE id IN ({placeholders})",
                            session_ids,
                        )
                finally:
                    rw.close()
        report = PurgeReport()
        for sid in session_ids:
            child_report = deep_purge(CODEX_HOME, sid, dry_run, aggressive)
            report.removed.extend(child_report.removed)
            report.scrubbed.extend(child_report.scrubbed)
            report.remaining.extend(child_report.remaining)
        report.db_rows = rows
        if not dry_run:
            self._sessions = None
        return report
