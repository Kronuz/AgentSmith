"""Shared paths and constants for Agentsmith."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
CACHE_DIR = Path(os.environ.get("ASMITH_CACHE", HOME / ".cache" / "asmith"))
HARNESSES = ("copilot", "claude")
