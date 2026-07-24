"""Backend abstraction shared by all harnesses."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from ..model import (
    Checkpoint,
    FileTouch,
    Msg,
    PurgeReport,
    SearchHit,
    Session,
    UsageRow,
)
from ..util import real


class Backend(ABC):
    name: str
    home: Path

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def list_sessions(self) -> list[Session]: ...

    @abstractmethod
    def get(self, session_id: str) -> Session | None: ...

    @abstractmethod
    def transcript(self, session_id: str, subagents: bool = True) -> list[Msg]: ...

    @abstractmethod
    def turn_count(self, session_id: str) -> int: ...

    @abstractmethod
    def files(self, session_id: str) -> list[FileTouch]: ...

    @abstractmethod
    def usage(self, session_id: str) -> list[UsageRow]: ...

    @abstractmethod
    def checkpoints(self, session_id: str) -> list[Checkpoint]: ...

    @abstractmethod
    def search(self, query: str, limit: int) -> list[SearchHit]: ...

    @abstractmethod
    def resume_command(self, session_id: str) -> list[str]: ...

    @abstractmethod
    def remove(
        self, session_id: str, dry_run: bool = False, aggressive: bool = False
    ) -> PurgeReport: ...

    @abstractmethod
    def raw_path(self, session_id: str) -> Path | None: ...

    @abstractmethod
    def state_location(self, session_id: str) -> Path | None: ...

    def artifact_paths(self, session_id: str) -> list[Path]:
        """Native, session-owned files/directories suitable for an export."""
        raw = self.raw_path(session_id)
        return [raw] if raw is not None else []

    def memory_paths(self, session_id: str) -> list[Path]:
        """Project-scoped memory associated with a session, if attributable."""
        return []

    # shared helpers -------------------------------------------------------

    def match_id(self, arg: str) -> list[Session]:
        exact = self.get(arg)
        if exact is not None:
            return [exact]
        return [s for s in self.list_sessions() if s.id.startswith(arg)]

    def sessions_for_dir(
        self, arg: str, resumable: bool = False, exact: bool = False
    ) -> list[Session]:
        cwd = real(arg if arg not in (".", "./") else os.getcwd())
        out = [s for s in self.list_sessions() if s.cwd and real(s.cwd) == cwd]
        if not out and not exact:
            prefix = cwd.rstrip(os.sep) + os.sep
            out = [
                s
                for s in self.list_sessions()
                if s.cwd and real(s.cwd).startswith(prefix)
            ]
        if resumable:
            out = [s for s in out if s.resumable]
        return out
