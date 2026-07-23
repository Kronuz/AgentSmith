"""Shared data model across harnesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Session:
    id: str
    harness: str
    cwd: str | None = None
    repository: str | None = None
    branch: str | None = None
    name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    resumable: bool = False
    turns: int | None = None


class Msg:
    __slots__ = ("role", "text", "tools", "reasoning", "agent")

    def __init__(self, role: str, text: str, agent: str | None = None) -> None:
        self.role = role
        self.text = text
        self.tools: list[dict[str, Any]] = []
        self.reasoning: str = ""
        self.agent = agent  # subagent label, or None for the main thread


@dataclass
class FileTouch:
    path: str
    tool: str | None = None
    turn: int | None = None


@dataclass
class SearchHit:
    harness: str
    session_id: str
    source: str
    snippet: str


CACHE_READ_WEIGHT = 0.1  # cached input ≈ 10% the price of fresh input (both vendors)


@dataclass
class UsageRow:
    model: str
    calls: int
    input: int
    output: int
    cache_read: int
    cache_write: int
    reasoning: int = 0
    aiu: float | None = None

    @property
    def effective(self) -> float:
        """Cost-weighted token proxy, comparable across harnesses.

        Built only from token counts (which both stores expose), it tracks
        Copilot's real AIU at ~0.99, so it stands in as a universal cost signal
        where AIU is absent (Claude). Cache reads are discounted since cached
        input is far cheaper than fresh input.
        """
        return (
            self.input
            + self.output
            + self.cache_write
            + CACHE_READ_WEIGHT * self.cache_read
        )


@dataclass
class Checkpoint:
    number: int
    title: str | None = None
    overview: str | None = None
    next_steps: str | None = None


@dataclass
class PurgeReport:
    """What a full session shred removed. Paths are absolute."""

    db_rows: int = 0
    removed: list[Path] = field(default_factory=list)  # id-named files/dirs deleted
    scrubbed: list[tuple[Path, int]] = field(
        default_factory=list
    )  # (file, lines removed)
    remaining: list[Path] = field(
        default_factory=list
    )  # still reference id, left alone

    def touched(self) -> bool:
        return bool(self.db_rows or self.removed or self.scrubbed)
