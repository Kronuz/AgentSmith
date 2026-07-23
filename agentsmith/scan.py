"""Everywhere-scanner: find or redact a string across ALL of a harness's state.

``cw grep`` reads only rendered transcripts. This module is the heavy hammer for
secret hygiene: it walks *every* file under a harness home (any type, no size cap by
default) and every SQLite database it finds there -- including Copilot's FTS5 search
index -- so a leaked password can be located and scrubbed wherever it came to rest.

Redaction is same-length and in place: each match is overwritten with a mask byte,
so file offsets, JSON/YAML structure and DB page layout are preserved and no copy of
the secret is left behind in a backup. SQLite content is rewritten via SQL (never by
touching the raw file bytes), the FTS index is rebuilt, and the database is VACUUMed
with its WAL truncated so the old value cannot linger in freed pages.
"""

from __future__ import annotations

import mmap
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

SQLITE_MAGIC = b"SQLite format 3\x00"
# Transient SQLite sidecars: never byte-edit these (it would corrupt the DB's
# write-ahead log / rollback journal). Their committed content is handled through
# the database itself (SQL redaction + wal_checkpoint(TRUNCATE)).
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

# A byte buffer we can slice, ``find``/``rfind`` and ``len`` uniformly, whether it
# comes from a memory-mapped file or an in-memory column value.
Buffer = Union[bytes, "mmap.mmap"]


@dataclass
class Hit:
    """A single location where the pattern was found."""

    path: Path
    where: str  # "line 42" for a file, "turns.user_message#7" for a DB cell
    snippet: str  # context with the match masked, unless reveal=True
    count: int  # matches at this location


@dataclass
class ScanResult:
    files_scanned: int = 0
    dbs_scanned: int = 0
    total_matches: int = 0
    files_matched: int = 0  # files/dbs with >=1 hit (meaningful even in dry-run)
    files_changed: int = 0  # files/dbs actually rewritten (apply mode)
    stopped_early: bool = False  # finder hit its result limit; more may exist
    errors: list[str] = field(default_factory=list)


Emit = Callable[[Hit], None]


def compile_pattern(pattern: str, fixed: bool, ignore_case: bool) -> re.Pattern[bytes]:
    raw = re.escape(pattern.encode("utf-8")) if fixed else pattern.encode("utf-8")
    return re.compile(raw, re.IGNORECASE if ignore_case else 0)


def is_sqlite(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def is_db_sidecar(p: Path) -> bool:
    """True for a `-wal`/`-shm`/`-journal` file whose base file is a SQLite DB."""
    name = p.name
    for suf in _SIDECAR_SUFFIXES:
        if name.endswith(suf):
            base = p.with_name(name[: -len(suf)])
            try:
                return base.exists() and is_sqlite(base)
            except OSError:
                return False
    return False


# --- shared display helper -------------------------------------------------


def _snippet(
    buf: Buffer, s: int, e: int, mask: int, reveal: bool, ctx: int = 60
) -> str:
    """A one-line window around ``buf[s:e]``, with the match masked unless reveal."""
    ls = buf.rfind(b"\n", 0, s) + 1
    le = buf.find(b"\n", e)
    if le == -1:
        le = len(buf)
    a = max(ls, s - ctx)
    b = min(le, e + ctx)
    left = bytes(buf[a:s])
    matched = bytes(buf[s:e]) if reveal else bytes([mask]) * (e - s)
    right = bytes(buf[e:b])
    txt = (left + matched + right).decode("utf-8", "replace").replace("\n", " ")
    return ("…" if a > ls else "") + txt + ("…" if b < le else "")


# --- plain files -----------------------------------------------------------


def scan_file(
    path: Path,
    rx: re.Pattern[bytes],
    mask: int,
    apply: bool,
    reveal: bool,
    limit: int,
    collect: bool,
) -> tuple[list[Hit], int, bool]:
    """Find (and, if apply, same-length overwrite) matches in one non-DB file.

    Always counts matches. ``collect`` additionally builds a :class:`Hit` per match
    (with a masked snippet) for display. Finder mode (``apply=False``) stops after
    ``limit`` matches when ``limit`` is positive (0 = unlimited); ``apply`` always
    processes every match.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0, False
    if size == 0:
        return [], 0, False
    mode = "r+b" if apply else "rb"
    access = mmap.ACCESS_WRITE if apply else mmap.ACCESS_READ
    hits: list[Hit] = []
    matches = 0
    changed = False
    with open(path, mode) as f:
        buf = mmap.mmap(f.fileno(), 0, access=access)
        try:
            line = 1
            pos = 0
            for m in rx.finditer(buf):
                s, e = m.start(), m.end()
                if e <= s:
                    continue
                matches += 1
                if collect:
                    j = buf.find(b"\n", pos, s)
                    while j != -1:
                        line += 1
                        pos = j + 1
                        j = buf.find(b"\n", pos, s)
                    pos = s
                    hits.append(
                        Hit(path, f"line {line}", _snippet(buf, s, e, mask, reveal), 1)
                    )
                if apply:
                    buf[s:e] = bytes([mask]) * (e - s)
                    changed = True
                elif limit and matches >= limit:
                    break
            if changed:
                buf.flush()
        finally:
            buf.close()
    return hits, matches, changed


# --- SQLite databases ------------------------------------------------------


def _fts_tables(con: sqlite3.Connection) -> set[str]:
    out: set[str] = set()
    for (name,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%USING fts5%'"
    ):
        out.add(name.decode() if isinstance(name, bytes) else name)
    return out


def scan_db(
    path: Path,
    rx: re.Pattern[bytes],
    mask: int,
    apply: bool,
    reveal: bool,
    limit: int,
    collect: bool,
) -> tuple[list[Hit], int, bool]:
    """Find (and, if apply, rewrite) matches across every table of a SQLite file.

    Content is edited only through SQL. FTS5 shadow tables are skipped; the FTS
    virtual table's own ``content`` copy is redacted and then rebuilt so the secret
    is gone from the index segments too. A final VACUUM + WAL truncate purges any
    old value left in freed pages or the write-ahead log.

    Always counts matches; ``collect`` builds a :class:`Hit` per matching cell.
    ``apply`` rewrites every match; finder mode stops after ``limit`` matches when
    ``limit`` is positive (0 = unlimited).
    """
    hits: list[Hit] = []
    matches = 0
    changed = False
    con = sqlite3.connect(str(path), timeout=10.0)
    con.isolation_level = None
    try:
        con.execute("PRAGMA busy_timeout = 5000")
        fts = _fts_tables(con)
        shadow = {
            f"{t}{suf}"
            for t in fts
            for suf in ("_data", "_idx", "_docsize", "_config", "_content")
        }
        tables = [
            r[0].decode() if isinstance(r[0], bytes) else r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        touched_fts: set[str] = set()

        def repl(m: re.Match[bytes]) -> bytes:
            return bytes([mask]) * (m.end() - m.start())

        for t in tables:
            if t in shadow or t.startswith("sqlite_"):
                continue
            try:
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
                colnames = [c.decode() if isinstance(c, bytes) else c for c in cols]
                rows = con.execute(f'SELECT rowid, * FROM "{t}"').fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                rowid = row[0]
                for cval, colname in zip(row[1:], colnames):
                    # Preserve each cell's storage class: TEXT stays TEXT (decode
                    # back to str before binding), BLOB stays BLOB. surrogateescape
                    # round-trips any bytes losslessly, so no content is altered.
                    if isinstance(cval, str):
                        data = cval.encode("utf-8", "surrogateescape")
                        is_text = True
                    elif isinstance(cval, (bytes, bytearray)):
                        data = bytes(cval)
                        is_text = False
                    else:
                        continue
                    found = list(rx.finditer(data))
                    if not found:
                        continue
                    matches += len(found)
                    if collect:
                        first = found[0]
                        hits.append(
                            Hit(
                                path,
                                f"{t}.{colname}#{rowid}",
                                _snippet(
                                    data, first.start(), first.end(), mask, reveal
                                ),
                                len(found),
                            )
                        )
                    if apply:
                        newbytes = rx.sub(repl, data)
                        newval: str | bytes = (
                            newbytes.decode("utf-8", "surrogateescape")
                            if is_text
                            else newbytes
                        )
                        con.execute(
                            f'UPDATE "{t}" SET "{colname}" = ? WHERE rowid = ?',
                            (newval, rowid),
                        )
                        changed = True
                        if t in fts:
                            touched_fts.add(t)
                    elif limit and matches >= limit:
                        return hits, matches, changed
        if apply and changed:
            for t in touched_fts:
                con.execute(f'INSERT INTO "{t}"("{t}") VALUES(\'rebuild\')')
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    return hits, matches, changed


# --- walk + driver ---------------------------------------------------------


def _walk(home: Path, max_bytes: int) -> Iterator[Path]:
    for root, _dirs, files in os.walk(home, followlinks=False):
        for name in files:
            p = Path(root) / name
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                if is_db_sidecar(p):
                    continue  # transient; handled via the DB, never byte-edited
                if max_bytes and p.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield p


def run(
    homes: list[tuple[str, Path]],
    rx: re.Pattern[bytes],
    mask: int,
    apply: bool,
    reveal: bool,
    max_bytes: int,
    emit: Emit,
    limit: int = 0,
    collect: bool = False,
) -> ScanResult:
    """Scan (and optionally redact) every file + DB under each home.

    ``apply`` always processes everything (a redaction can't stop early). In finder
    mode a positive ``limit`` stops the sweep after that many matches (0 =
    unlimited), setting ``stopped_early`` when it bails. ``collect`` builds a
    per-match :class:`Hit` for display (skipped by default for speed).
    """
    res = ScanResult()
    seen: set[Path] = set()
    for _name, home in homes:
        if not home.exists():
            continue
        for p in _walk(home, max_bytes):
            if not apply and limit and res.total_matches >= limit:
                res.stopped_early = True
                return res
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            budget = 0 if apply or not limit else max(1, limit - res.total_matches)
            try:
                if is_sqlite(p):
                    res.dbs_scanned += 1
                    hits, m, changed = scan_db(
                        p, rx, mask, apply, reveal, budget, collect
                    )
                else:
                    res.files_scanned += 1
                    hits, m, changed = scan_file(
                        p, rx, mask, apply, reveal, budget, collect
                    )
            except Exception as ex:  # noqa: BLE001 - report, never abort the sweep
                res.errors.append(f"{p}: {ex}")
                continue
            res.total_matches += m
            if m:
                res.files_matched += 1
            if changed:
                res.files_changed += 1
            for h in hits:
                emit(h)
    return res
