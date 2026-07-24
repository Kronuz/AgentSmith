"""Terminal, time, and text helpers (stdlib only)."""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn


_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def color_enabled() -> bool:
    return _COLOR


def set_color(enabled: bool) -> None:
    global _COLOR
    _COLOR = enabled


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t: str) -> str:
    return c(t, "2")


def bold(t: str) -> str:
    return c(t, "1")


def green(t: str) -> str:
    return c(t, "32")


def yellow(t: str) -> str:
    return c(t, "33")


def cyan(t: str) -> str:
    return c(t, "36")


def path(t: str) -> str:
    """One consistent color for filesystem paths, everywhere."""
    return c(t, "36")


def magenta(t: str) -> str:
    return c(t, "35")


def red(t: str) -> str:
    return c(t, "31")


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"asmith: {msg}", file=sys.stderr)
    raise SystemExit(code)


def harness_badge(name: str) -> str:
    if name == "copilot":
        return cyan("co")
    if name == "claude":
        return magenta("cl")
    return green("cx")


def short(session_id: str) -> str:
    return session_id[:8]


def real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def looks_like_path(arg: str) -> bool:
    if arg in (".", "..", "~") or arg.startswith(("/", "./", "../", "~/")):
        return True
    return "/" in arg or os.path.isdir(os.path.expanduser(arg))


def parse_ts(ts: str | None) -> float:
    if not ts:
        return 0.0
    s = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        try:
            return (
                datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except ValueError:
            return 0.0


def ago(ts: str | None) -> str:
    t = parse_ts(ts)
    if not t:
        return "?"
    d = max(0.0, time.time() - t)
    for unit, sec in (
        ("y", 31536000),
        ("mo", 2592000),
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
    ):
        if d >= sec:
            return f"{int(d // sec)}{unit}"
    return f"{int(d)}s"


def fmt_local(ts: str | None) -> str:
    t = parse_ts(ts)
    if not t:
        return "?"
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


_REMINDER_RE = re.compile(r"<system[_-]reminder>.*?</system[_-]reminder>", re.DOTALL)
_DATETIME_RE = re.compile(r"<current_datetime>.*?</current_datetime>", re.DOTALL)
_POLICY_RE = re.compile(r"^LinkedIn enterprise Copilot CLI policy:.*$", re.MULTILINE)


def clean_user(text: str) -> str:
    text = _REMINDER_RE.sub("", text)
    text = _DATETIME_RE.sub("", text)
    text = _POLICY_RE.sub("", text)
    return text.strip()


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + dim(f"…(+{len(s) - n})")


def trunc_lines(s: str, n: int) -> str:
    lines = s.splitlines()
    if len(lines) <= n:
        return s
    return "\n".join(lines[:n]) + dim(f"\n    …(+{len(lines) - n} lines)")


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)
