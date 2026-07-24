"""Copilot CLI backend: SQLite session-store + on-disk session-state."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, override

from ..model import (
    Checkpoint,
    FileTouch,
    PurgeReport,
    SearchHit,
    Session,
    UsageRow,
)
from ..purge import deep_purge
from ..util import clean_user, die
from .base import Backend, Msg

COPILOT_HOME = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
DB_PATH = Path(os.environ.get("COPILOT_DB", COPILOT_HOME / "session-store.db"))
STATE_DIR = Path(os.environ.get("COPILOT_STATE", COPILOT_HOME / "session-state"))


class CopilotBackend(Backend):
    name = "copilot"
    home = COPILOT_HOME

    def __init__(self) -> None:
        self._con: sqlite3.Connection | None = None

    @override
    def available(self) -> bool:
        return DB_PATH.exists()

    def con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            self._con.row_factory = sqlite3.Row
        return self._con

    def _events_path(self, sid: str) -> Path:
        return STATE_DIR / sid / "events.jsonl"

    def _resumable(self, sid: str) -> bool:
        return self._events_path(sid).exists()

    def _row_to_session(self, r: sqlite3.Row) -> Session:
        return Session(
            id=r["id"],
            harness="copilot",
            cwd=r["cwd"],
            repository=r["repository"],
            branch=r["branch"],
            name=r["summary"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            resumable=self._resumable(r["id"]),
        )

    @override
    def list_sessions(self) -> list[Session]:
        rows = (
            self.con()
            .execute(
                "SELECT id, cwd, repository, branch, summary, created_at, updated_at "
                "FROM sessions ORDER BY updated_at DESC"
            )
            .fetchall()
        )
        return [self._row_to_session(r) for r in rows]

    @override
    def get(self, session_id: str) -> Session | None:
        r = (
            self.con()
            .execute(
                "SELECT id, cwd, repository, branch, summary, created_at, updated_at "
                "FROM sessions WHERE id = ?",
                (session_id,),
            )
            .fetchone()
        )
        return self._row_to_session(r) if r else None

    @override
    def turn_count(self, session_id: str) -> int:
        return (
            self.con()
            .execute("SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,))
            .fetchone()[0]
        )

    def _iter_events(self, session_id: str) -> Iterator[dict[str, Any]]:
        path = self._events_path(session_id)
        if not path.exists():
            return
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @override
    def transcript(self, session_id: str, subagents: bool = True) -> list[Msg]:
        if self._resumable(session_id):
            return self._transcript_from_events(session_id, subagents)
        return self._transcript_from_turns(session_id)

    def _transcript_from_events(
        self, session_id: str, subagents: bool = True
    ) -> list[Msg]:
        results: dict[str, dict[str, Any]] = {}
        sub_names: dict[str, str] = {}
        for e in self._iter_events(session_id):
            d = e.get("data", {})
            if e.get("type") == "tool.execution_complete":
                results[d.get("toolCallId", "")] = {
                    "success": d.get("success"),
                    "content": (d.get("result") or {}).get("content", ""),
                }
            elif e.get("type") == "subagent.started":
                tcid = d.get("toolCallId", "")
                sub_names[tcid] = (
                    d.get("agentName") or d.get("agentDisplayName") or "subagent"
                )
        msgs: list[Msg] = []
        for e in self._iter_events(session_id):
            t = e.get("type")
            d = e.get("data", {})
            ptc = d.get("parentToolCallId")
            agent = None
            if ptc:
                if not subagents:
                    continue
                agent = f"{sub_names.get(ptc, 'subagent')}#{str(ptc)[-6:]}"
            if t == "user.message":
                content = clean_user(d.get("content", ""))
                if content:
                    msgs.append(Msg("user", content, agent))
            elif t == "assistant.message":
                m = Msg("assistant", d.get("content", "") or "", agent)
                m.reasoning = d.get("reasoningText", "") or ""
                for tr in d.get("toolRequests", []) or []:
                    if not isinstance(tr, dict):
                        continue
                    call_id = tr.get("toolCallId", "")
                    raw_args = tr.get("arguments")
                    args_dict: dict[str, Any] = (
                        raw_args if isinstance(raw_args, dict) else {}
                    )
                    m.tools.append(
                        {
                            "name": tr.get("name", "?"),
                            "summary": tr.get("intentionSummary", "")
                            or args_dict.get("description", ""),
                            "arguments": raw_args if raw_args is not None else {},
                            "result": results.get(call_id),
                        }
                    )
                if m.text or m.tools:
                    msgs.append(m)
        return msgs

    def _transcript_from_turns(self, session_id: str) -> list[Msg]:
        msgs: list[Msg] = []
        for r in self.con().execute(
            "SELECT user_message, assistant_response FROM turns "
            "WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ):
            if r["user_message"]:
                msgs.append(Msg("user", clean_user(r["user_message"])))
            if r["assistant_response"]:
                msgs.append(Msg("assistant", r["assistant_response"]))
        return msgs

    @override
    def files(self, session_id: str) -> list[FileTouch]:
        rows = (
            self.con()
            .execute(
                "SELECT file_path, tool_name, turn_index FROM session_files "
                "WHERE session_id = ? ORDER BY turn_index, file_path",
                (session_id,),
            )
            .fetchall()
        )
        return [
            FileTouch(r["file_path"], r["tool_name"], r["turn_index"]) for r in rows
        ]

    @override
    def usage(self, session_id: str) -> list[UsageRow]:
        rows = (
            self.con()
            .execute(
                "SELECT model, COUNT(*) calls, SUM(input_tokens) i, SUM(output_tokens) o, "
                "SUM(cache_read_tokens) cr, SUM(cache_write_tokens) cw, "
                "SUM(reasoning_tokens) rt, SUM(total_nano_aiu) aiu "
                "FROM assistant_usage_events WHERE session_id = ? GROUP BY model",
                (session_id,),
            )
            .fetchall()
        )
        return [
            UsageRow(
                model=r["model"],
                calls=r["calls"],
                input=r["i"] or 0,
                output=r["o"] or 0,
                cache_read=r["cr"] or 0,
                cache_write=r["cw"] or 0,
                reasoning=r["rt"] or 0,
                aiu=(r["aiu"] or 0) / 1e9,
            )
            for r in rows
        ]

    @override
    def checkpoints(self, session_id: str) -> list[Checkpoint]:
        rows = (
            self.con()
            .execute(
                "SELECT checkpoint_number, title, overview, next_steps FROM checkpoints "
                "WHERE session_id = ? ORDER BY checkpoint_number",
                (session_id,),
            )
            .fetchall()
        )
        return [
            Checkpoint(
                r["checkpoint_number"], r["title"], r["overview"], r["next_steps"]
            )
            for r in rows
        ]

    @override
    def search(self, query: str, limit: int) -> list[SearchHit]:
        try:
            rows = (
                self.con()
                .execute(
                    "SELECT session_id, source_type, "
                    "snippet(search_index, 0, '[', ']', '…', 10) AS snip "
                    "FROM search_index WHERE search_index MATCH ? LIMIT ?",
                    (query, limit),
                )
                .fetchall()
            )
        except sqlite3.OperationalError as exc:
            die(f"bad FTS query: {exc}")
        return [
            SearchHit("copilot", r["session_id"], r["source_type"], r["snip"].strip())
            for r in rows
        ]

    @override
    def resume_command(self, session_id: str) -> list[str]:
        return ["copilot", f"--resume={session_id}", "--yolo"]

    @override
    def raw_path(self, session_id: str) -> Path | None:
        p = self._events_path(session_id)
        return p if p.exists() else None

    @override
    def state_location(self, session_id: str) -> Path | None:
        p = STATE_DIR / session_id
        return p if p.exists() else None

    @override
    def artifact_paths(self, session_id: str) -> list[Path]:
        state = STATE_DIR / session_id
        return [state] if state.exists() else []

    _TABLES = (
        "turns",
        "checkpoints",
        "session_files",
        "session_refs",
        "assistant_usage_events",
        "forge_trajectory_events",
        "search_index",
    )

    @override
    def remove(
        self, session_id: str, dry_run: bool = False, aggressive: bool = False
    ) -> PurgeReport:
        rows = 0
        for table in self._TABLES:
            try:
                rows += (
                    self.con()
                    .execute(
                        f"SELECT COUNT(*) FROM {table} WHERE session_id = ?",
                        (session_id,),
                    )
                    .fetchone()[0]
                )
            except sqlite3.OperationalError:
                pass
        if not dry_run:
            rw = sqlite3.connect(str(DB_PATH), timeout=10.0)
            rw.execute("PRAGMA busy_timeout = 5000")
            try:
                with rw:
                    for table in self._TABLES:
                        try:
                            rw.execute(
                                f"DELETE FROM {table} WHERE session_id = ?",
                                (session_id,),
                            )
                        except sqlite3.OperationalError:
                            pass
                    rw.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            finally:
                rw.close()
        report = deep_purge(COPILOT_HOME, session_id, dry_run, aggressive)
        report.db_rows = rows
        return report
