"""Backend registry: selection and cross-harness session resolution."""

from __future__ import annotations

from ..config import HARNESSES
from ..model import Session
from ..util import die, harness_badge, looks_like_path, parse_ts, real, short
from .base import Backend
from .claude import ClaudeBackend
from .codex import CodexBackend
from .copilot import CopilotBackend

__all__ = [
    "Backend",
    "ClaudeBackend",
    "CodexBackend",
    "CopilotBackend",
    "all_sessions",
    "backend_for",
    "resolve",
    "select_backends",
]


def select_backends(harness: str) -> list[Backend]:
    wanted = HARNESSES if harness == "all" else (harness,)
    out: list[Backend] = []
    for name in wanted:
        backends: dict[str, type[Backend]] = {
            "copilot": CopilotBackend,
            "claude": ClaudeBackend,
            "codex": CodexBackend,
        }
        b = backends[name]()
        if b.available():
            out.append(b)
    if not out:
        die(f"no available backend for harness={harness}")
    return out


def resolve(
    backends: list[Backend], arg: str, resumable: bool = False, exact: bool = False
) -> tuple[Backend, Session]:
    if looks_like_path(arg):
        candidates: list[tuple[Backend, Session]] = []
        for b in backends:
            for s in b.sessions_for_dir(arg, resumable=resumable, exact=exact):
                candidates.append((b, s))
        if not candidates:
            kind = "resumable " if resumable else ""
            die(f"no {kind}session for directory: {real(arg)}")
        candidates.sort(key=lambda bs: parse_ts(bs[1].updated_at), reverse=True)
        return candidates[0]
    matches: list[tuple[Backend, Session]] = []
    for b in backends:
        for s in b.match_id(arg):
            matches.append((b, s))
    if not matches:
        die(f"no session matching id/prefix: {arg}")
    exact_hits = [m for m in matches if m[1].id == arg]
    if exact_hits:
        return exact_hits[0]
    if len(matches) > 1:
        listing = "\n  ".join(
            f"{harness_badge(b.name)} {short(s.id)}  {s.name or s.cwd or ''}"
            for b, s in matches[:10]
        )
        die(f"ambiguous prefix '{arg}' matches {len(matches)}:\n  {listing}")
    return matches[0]


def all_sessions(backends: list[Backend]) -> list[Session]:
    out: list[Session] = []
    for b in backends:
        out.extend(b.list_sessions())
    out.sort(key=lambda s: parse_ts(s.updated_at), reverse=True)
    return out


def backend_for(backends: list[Backend], name: str) -> Backend:
    for b in backends:
        if b.name == name:
            return b
    die(f"backend not available: {name}")
