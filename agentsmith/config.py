"""Shared paths and constants for Agentsmith."""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
CACHE_DIR = Path(os.environ.get("CW_CACHE", HOME / ".cache" / "cw"))
HARNESSES = ("copilot", "claude")
