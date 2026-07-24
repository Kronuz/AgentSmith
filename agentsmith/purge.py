"""deep_purge: shred every on-disk trace of a session id under a home dir."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .model import PurgeReport

# Line-oriented files we scrub line-by-line (they mix many sessions).
_LINE_EXTS = {".jsonl", ".ndjson", ".log"}
# With --aggressive we also scrub these text formats (memory notes, prose logs).
_AGGRESSIVE_EXTS = _LINE_EXTS | {".md", ".txt", ".markdown", ".mdx"}
# Binary / structured files we never scan or rewrite (DB handled via SQL).
_SKIP_EXTS = {
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".wal",
    ".shm",
}
# Never touched in either pass (deleting inside would be unsafe).
_SKIP_ALWAYS = {
    ".git",
    ".tmp",
    "cache",
    "node_modules",
    "packages",
    "plugins",
    "skills",
}
# Skipped only for the *content* scan (pass 2), for speed. Pass 1 still DELETES
# id-named files/dirs inside these (they are exactly the vestiges to remove).
_SKIP_SCAN = {"rewind-file-snapshots", "file-history"}
# Cap on files we actually rewrite (logs/history are small); huge transcripts skipped.
_MAX_SCAN_BYTES = 32_000_000
# Much smaller cap for files we only read to *report* a stray reference.
_REF_SCAN_BYTES = 2_000_000


def _under(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _should_scrub(p: Path) -> bool:
    """Only auto-scrub shared bookkeeping files. Never rewrite another session's
    primary transcript (line-deleting there could corrupt its resume)."""
    if p.suffix.lower() == ".log":
        return True
    if p.name in ("history.jsonl", "command-history.jsonl"):
        return True
    return "logs" in p.parts


def _json_belongs(x: Any, sid: str) -> bool:
    return isinstance(x, dict) and any(
        x.get(f) == sid for f in ("sessionId", "session_id", "id", "sid")
    )


def _prune_json(obj: Any, sid: str) -> Any:
    """Recursively drop dict keys == sid and list/dict entries owned by sid."""
    if isinstance(obj, dict):
        return {
            k: _prune_json(v, sid)
            for k, v in obj.items()
            if k != sid and not _json_belongs(v, sid)
        }
    if isinstance(obj, list):
        return [
            _prune_json(x, sid) for x in obj if x != sid and not _json_belongs(x, sid)
        ]
    return obj


def deep_purge(
    home: Path, session_id: str, dry_run: bool, aggressive: bool = False
) -> PurgeReport:
    """Remove every trace of ``session_id`` beneath ``home``.

    Two passes:
    1. delete any file/dir whose name contains the id (transcripts, per-session
       dirs, id-named logs);
    2. scrub id-bearing lines from shared line-oriented files (history.jsonl,
       process logs). Other files that still mention the id are reported, not
       modified -- unless ``aggressive`` is set, which also line-scrubs the id
       out of every text/line file that references it (this edits OTHER sessions'
       transcripts and memory notes, so it is opt-in).
    """
    report = PurgeReport()
    if not home.is_dir():
        return report
    idb = session_id.encode()
    removed_roots: list[Path] = []

    # pass 1: id-named paths (shallowest first so we rmtree a dir once)
    matches = sorted(home.rglob(f"*{session_id}*"), key=lambda p: len(p.parts))
    for p in matches:
        if any(part in _SKIP_ALWAYS for part in p.parts):
            continue
        if any(_under(p, r) for r in removed_roots):
            continue
        report.removed.append(p)
        removed_roots.append(p)
        if not dry_run:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    # pass 2: content references in surviving files
    for dirpath, dirnames, filenames in os.walk(home):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_ALWAYS and d not in _SKIP_SCAN
        ]
        d = Path(dirpath)
        if any(_under(d, r) for r in removed_roots):
            dirnames[:] = []
            continue
        for fn in filenames:
            p = d / fn
            if p.suffix.lower() in _SKIP_EXTS or any(
                _under(p, r) for r in removed_roots
            ):
                continue
            scrub_line = p.suffix.lower() in (
                _AGGRESSIVE_EXTS if aggressive else _LINE_EXTS
            ) and (aggressive or _should_scrub(p))
            # structured bookkeeping JSON (vscode cache, command-history) at the
            # home root -- prune the session's entries surgically.
            scrub_json = p.suffix.lower() == ".json" and (
                p.parent == home or aggressive
            )
            cap = _MAX_SCAN_BYTES if (scrub_line or scrub_json) else _REF_SCAN_BYTES
            try:
                if p.stat().st_size > cap:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if idb not in data:
                continue
            if scrub_line:
                lines = data.split(b"\n")
                kept = [ln for ln in lines if idb not in ln]
                report.scrubbed.append((p, len(lines) - len(kept)))
                if not dry_run:
                    try:
                        p.write_bytes(b"\n".join(kept))
                    except OSError:
                        pass
            elif scrub_json:
                try:
                    pruned = _prune_json(json.loads(data), session_id)
                    out = json.dumps(pruned, indent=2)
                except (json.JSONDecodeError, ValueError):
                    report.remaining.append(p)
                    continue
                if session_id in out:  # reference we couldn't safely remove
                    report.remaining.append(p)
                else:
                    report.scrubbed.append((p, data.count(idb)))
                    if not dry_run:
                        try:
                            p.write_text(out + "\n")
                        except OSError:
                            pass
            else:
                report.remaining.append(p)
    return report
