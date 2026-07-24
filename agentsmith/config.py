"""Shared paths and constants for Agentsmith."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
CACHE_DIR = Path(os.environ.get("ASMITH_CACHE", HOME / ".cache" / "asmith"))
_DEFAULT_STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state"))
STATE_DIR = Path(os.environ.get("ASMITH_STATE", _DEFAULT_STATE / "agentsmith"))
HARNESSES = ("copilot", "claude", "codex")
