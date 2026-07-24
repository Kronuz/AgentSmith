"""Command-line interface: argument parsing and command implementations."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import scan
from .backends import (
    Backend,
    all_sessions,
    backend_for,
    resolve,
    select_backends,
)
from .backends.claude import CLAUDE_HOME
from .backends.codex import CODEX_HOME
from .backends.copilot import COPILOT_HOME
from .continuation import (
    ContinuationResult,
    GlobalImportResult,
    global_launch_command,
    handoff_launch_command,
    launch_command,
    prepare_continuation,
    prepare_global_import,
)
from .export import ExportItem, export_bundle, export_global_bundle, verify_bundle
from .model import CACHE_READ_WEIGHT, Msg, PurgeReport, SearchHit, Session
from .receipt import audit as audit_receipt
from .receipt import create as create_receipt
from .receipt import rollback as rollback_receipt
from .usage_cache import usage_for
from .util import (
    ago,
    bold,
    c,
    color_enabled,
    cyan,
    die,
    dim,
    fmt_local,
    green,
    harness_badge,
    harness_label,
    looks_like_path,
    magenta,
    parse_ts,
    path,
    real,
    red,
    set_color,
    short,
    strip_ansi,
    trunc,
    trunc_lines,
    yellow,
)


def _print_artifact(path: Path, verbose: bool, *details: str) -> None:
    """Print one pipeable artifact path; optional human context goes to stderr."""
    print(path)
    if verbose:
        for detail in details:
            print(detail, file=sys.stderr)


def _print_handoff(path: Path, verbose: bool, summary: str) -> None:
    _print_artifact(
        path,
        verbose,
        summary,
        f"next: asmith launch AGENT {shlex.quote(str(path))}",
    )


def _term_cols() -> int:
    return shutil.get_terminal_size((100, 24)).columns


def _clip(text: str, budget: int) -> str:
    """Collapse whitespace to a single line and truncate to ``budget`` columns."""
    text = " ".join(text.split())
    budget = max(16, budget)
    return text if len(text) <= budget else text[: budget - 1] + "…"


def _pad(text: str, width: int, right: bool = False) -> str:
    """Pad ``text`` to a visible ``width``, ignoring any ANSI color codes it holds."""
    gap = max(0, width - len(strip_ansi(text)))
    return " " * gap + text if right else text + " " * gap


def _short_model(m: str) -> str:
    """Compact model label for tight columns (drop the 'claude-' vendor prefix)."""
    return m.split("-", 1)[1] if m.startswith("claude-") else m


def _row_tail(cwd: str | None, name: str | None, budget: int) -> str:
    """One-line 'path  name' tail: path first (colored), then the name."""
    if cwd:
        path_max = min(len(cwd), max(24, budget - 24))
        pc = _clip(cwd, path_max)
        rem = budget - len(pc) - 2
        tail = _clip(name, rem) if name else dim("(no name)")
        return path(pc) + "  " + tail
    return _clip(name, budget) if name else dim("(no name)")


def cmd_list(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    sessions = all_sessions(backends)
    if args.here:
        cwd = real(".")
        sessions = [s for s in sessions if s.cwd and real(s.cwd) == cwd]
    elif args.dir:
        needle = real(args.dir)
        sessions = [s for s in sessions if s.cwd and needle in real(s.cwd)]
    if args.repo:
        sessions = [s for s in sessions if s.repository and args.repo in s.repository]
    if args.grep:
        g = args.grep.lower()
        sessions = [
            s
            for s in sessions
            if (s.name and g in s.name.lower()) or (s.cwd and g in s.cwd.lower())
        ]
    sessions = _sort_sessions(backends, sessions, args.sort, args.reverse)
    if args.number:
        sessions = sessions[: args.number]
    if not sessions:
        print(dim("(no sessions)"))
        return
    cols = _term_cols()
    for s in sessions:
        mark = green("*") if s.resumable else dim(".")
        turns = _turn_count(backends, s)
        prefix = (
            f"{mark} {_pad(harness_label(s.harness), 7)}  {bold(short(s.id))}  "
            f"{dim(ago(s.updated_at).rjust(4))}  "
            f"{dim(str(turns).rjust(4) + ' turns')}  "
        )
        print(prefix + _row_tail(s.cwd, s.name, cols - len(strip_ansi(prefix))))
    print(dim(f"\n{len(sessions)} shown  (* = resumable)"))


def _turn_count(backends: list[Backend], s: Session) -> int:
    if s.turns is not None:
        return s.turns
    return backend_for(backends, s.harness).turn_count(s.id)


_SORT_KEYS = ("date", "agent", "id", "turns", "name", "dir")


def _sort_sessions(
    backends: list[Backend], sessions: list[Session], key: str, reverse: bool
) -> list[Session]:
    """Sort by a chosen field. Sessions arrive newest-first, a stable tiebreaker.

    ``date`` and ``turns`` default to descending (newest / most first); the rest to
    ascending (a→z, 0→9). ``reverse`` flips whichever default applies.
    """
    desc = key in ("date", "turns")
    if reverse:
        desc = not desc
    if key == "agent":
        return sorted(sessions, key=lambda s: s.harness, reverse=desc)
    if key == "id":
        return sorted(sessions, key=lambda s: s.id, reverse=desc)
    if key == "turns":
        return sorted(sessions, key=lambda s: _turn_count(backends, s), reverse=desc)
    if key == "name":
        return sorted(sessions, key=lambda s: (s.name or "").lower(), reverse=desc)
    if key == "dir":
        return sorted(sessions, key=lambda s: (s.cwd or "").lower(), reverse=desc)
    return sorted(sessions, key=lambda s: parse_ts(s.updated_at), reverse=desc)


def _session_line(backends: list[Backend], s: Session, indent: str, badge: bool) -> str:
    mark = green("*") if s.resumable else dim(".")
    tag = (harness_badge(s.harness) + " ") if badge else ""
    turns = _turn_count(backends, s)
    prefix = (
        f"{indent}{mark} {tag}{bold(short(s.id))}  {dim(ago(s.updated_at).rjust(4))}  "
        f"{dim(str(turns).rjust(4) + ' turns')}  "
    )
    if not s.name:
        return prefix + dim("(no name)")
    return prefix + _clip(s.name, _term_cols() - len(strip_ansi(prefix)))


def cmd_tree(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    sessions = all_sessions(backends)
    if args.resumable:
        sessions = [s for s in sessions if s.resumable]
    sessions = _sort_sessions(backends, sessions, args.sort, args.reverse)
    if args.number:
        sessions = sessions[: args.number]
    if not sessions:
        print(dim("(no sessions)"))
        return
    multi = len(backends) > 1

    def by_dir(items: list[Session], indent: str, badge: bool) -> None:
        groups: dict[str, list[Session]] = {}
        for s in items:
            groups.setdefault(s.cwd or "(unknown)", []).append(s)
        ordered = sorted(
            groups,
            key=lambda cwd: max(parse_ts(x.updated_at) for x in groups[cwd]),
            reverse=True,
        )
        for cwd in ordered:
            print(f"{indent}{path(cwd)}")
            for s in groups[cwd]:
                print(_session_line(backends, s, indent + "  ", badge))

    if args.by == "agent":
        for b in backends:
            group = [s for s in sessions if s.harness == b.name]
            if not group:
                continue
            print(f"{harness_badge(b.name)} {bold(b.name)}  {dim(f'({len(group)})')}")
            by_dir(group, "  ", badge=False)
    else:
        by_dir(sessions, "", badge=multi)

    ndirs = len({s.cwd for s in sessions})
    print(dim(f"\n{len(sessions)} sessions in {ndirs} directories  (* = resumable)"))


def cmd_dirs(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    agg: dict[str, dict[str, Any]] = {}
    for s in all_sessions(backends):
        if not s.cwd:
            continue
        a = agg.setdefault(s.cwd, {"n": 0, "res": 0, "last": "", "harnesses": set()})
        a["n"] += 1
        if s.resumable:
            a["res"] += 1
        if parse_ts(s.updated_at) > parse_ts(a["last"]):
            a["last"] = s.updated_at
        a["harnesses"].add(s.harness)
    rows = sorted(
        agg.items(),
        key=lambda kv: kv[1]["n"] if args.by_count else parse_ts(kv[1]["last"]),
        reverse=True,
    )
    if args.number:
        rows = rows[: args.number]
    multi = len(backends) > 1
    for cwd, a in rows:
        n = str(a["n"]).rjust(3)
        res_txt = f"{a['res']}*"
        res = _pad(green(res_txt) if a["res"] else dim(res_txt), 4, right=True)
        last = dim(ago(a["last"]).rjust(4))
        agent = ""
        if multi:
            badges = "".join(harness_badge(h) for h in sorted(a["harnesses"]))
            agent = "  " + _pad(badges, 4)
        print(f"{n}  {res}  {last}{agent}  {path(cwd)}")
    hint = "sessions / resumable* / last activity" + (" / agents" if multi else "")
    print(dim(f"\n{len(rows)} directories  ({hint})"))


def cmd_find(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    pairs: list[tuple[Backend, Session]] = []
    for b in backends:
        for s in b.sessions_for_dir(
            args.dir, resumable=args.resumable, exact=args.exact
        ):
            pairs.append((b, s))
    pairs.sort(key=lambda bs: parse_ts(bs[1].updated_at), reverse=True)
    if not pairs:
        kind = "resumable " if args.resumable else ""
        print(dim(f"(no {kind}session for {real(args.dir)})"), file=sys.stderr)
        raise SystemExit(1)
    multi = len(backends) > 1
    cols = _term_cols()
    for b, s in pairs:
        mark = green("*") if s.resumable else dim(".")
        badge = (harness_badge(s.harness) + " ") if multi else ""
        prefix = (
            f"{mark} {badge}{bold(short(s.id))}  {dim(ago(s.updated_at).rjust(4))}  "
        )
        print(prefix + _row_tail(s.cwd, s.name, cols - len(strip_ansi(prefix))))


def cmd_resolve(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    _b, s = resolve(backends, args.target, resumable=args.resumable, exact=args.exact)
    if args.resumable and not s.resumable:
        die(f"session {short(s.id)} is not resumable (no on-disk transcript)")
    print(s.id)


def cmd_resume(args: argparse.Namespace) -> None:
    cwd = Path(args.dir).expanduser().resolve()
    if not cwd.is_dir():
        die(f"directory does not exist: {cwd}")
    backend, session = resolve(
        select_backends(args.agent),
        str(cwd),
        resumable=True,
        exact=True,
    )
    command = backend.resume_command(session.id)
    print(
        f"asmith: resuming {args.agent} session {short(session.id)}",
        file=sys.stderr,
    )
    try:
        os.execvp(command[0], command)
    except FileNotFoundError:
        die(f"agent CLI is not installed or not on PATH: {command[0]}")


def cmd_show(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    b, s = resolve(backends, args.session)

    def field(k: str, v: str) -> None:
        print(f"  {dim(k.ljust(11))} {v}")

    print(bold(short(s.id)) + dim(f"  ({s.id})  [{s.harness}]"))
    field("name", s.name or dim("—"))
    field("cwd", path(s.cwd) if s.cwd else dim("—"))
    if s.repository:
        field("repo", s.repository + (f"  @{s.branch}" if s.branch else ""))
    elif s.branch:
        field("branch", s.branch)
    field("created", f"{fmt_local(s.created_at)}  ({dim(ago(s.created_at) + ' ago')})")
    field("updated", f"{fmt_local(s.updated_at)}  ({dim(ago(s.updated_at) + ' ago')})")
    field("turns", str(_turn_count(backends, s)))
    field("files", str(len(b.files(s.id))))
    rows = usage_for(b, s.id)
    itok = sum(r.input for r in rows)
    otok = sum(r.output for r in rows)
    ctok = sum(r.cache_read for r in rows)
    token_text = f"in {itok + ctok:,} (cached {ctok:,}) · out {otok:,}"
    if any(r.aiu is not None for r in rows):
        aiu = sum(r.aiu or 0 for r in rows)
        field("tokens", f"{token_text} · {aiu:.1f} AIU")
    else:
        field("tokens", token_text)
    field(
        "resumable", green("yes") if s.resumable else red("no (no on-disk transcript)")
    )
    if s.resumable:
        field("resume", " ".join(b.resume_command(s.id)))
    cps = b.checkpoints(s.id)
    if cps:
        print(dim("  checkpoints:"))
        for cp in cps:
            print(f"    {cp.number}. {cp.title or dim('(untitled)')}")


def _indent(text: str, pad: str) -> str:
    return "\n".join(pad + ln for ln in text.splitlines()) if pad else text


def _render_msg_term(m: Msg, args: argparse.Namespace, pad: str) -> str:
    lines: list[str] = []
    if m.role == "user":
        if args.assistant_only:
            return ""
        lines.append(pad + bold(green("● user")))
        lines.append(_indent(m.text, pad))
    else:
        if args.user_only:
            return ""
        lines.append(pad + bold(cyan("● assistant")))
        if args.reasoning and m.reasoning:
            lines.append(_indent(dim("💭 " + m.reasoning.strip()), pad))
        if m.text:
            lines.append(_indent(m.text, pad))
        for tl in m.tools:
            head = f"{pad}  {yellow('⚙ ' + str(tl['name']))}"
            if tl["summary"]:
                head += f"  {dim(str(tl['summary']))}"
            lines.append(head)
            if args.tools:
                arg_s = json.dumps(tl["arguments"], ensure_ascii=False)
                lines.append(_indent(dim("args: " + trunc(arg_s, 500)), pad + "    "))
                res = tl.get("result")
                if res:
                    ok = green("ok") if res.get("success") else red("fail")
                    body = trunc_lines(trunc(str(res.get("content", "")), 800), 4)
                    lines.append(_indent(dim(f"[{ok}] ") + body, pad + "    "))
    return "\n".join(lines)


def _render_msg_md(m: Msg, args: argparse.Namespace) -> str:
    lines: list[str] = []
    if m.role == "user":
        if args.assistant_only:
            return ""
        lines.append("### 🧑 User\n")
        lines.append(m.text)
    else:
        if args.user_only:
            return ""
        lines.append("### 🤖 Assistant\n")
        if args.reasoning and m.reasoning:
            lines.append("> 💭 " + m.reasoning.strip().replace("\n", "\n> ") + "\n")
        if m.text:
            lines.append(m.text)
        for tl in m.tools:
            desc = f" — {tl['summary']}" if tl["summary"] else ""
            lines.append(f"\n- ⚙ **{tl['name']}**{desc}")
            if args.tools:
                arg_s = json.dumps(tl["arguments"], ensure_ascii=False)
                lines.append(f"\n  ```json\n  {trunc(arg_s, 500)}\n  ```")
                res = tl.get("result")
                if res:
                    ok = "ok" if res.get("success") else "fail"
                    body = trunc(str(res.get("content", "")), 800)
                    lines.append(f"  ```text\n  [{ok}] {body}\n  ```")
    return "\n".join(lines)


def render_chat(msgs: list[Msg], args: argparse.Namespace, md: bool) -> str:
    blocks: list[str] = []
    cur: str | None = None
    for m in msgs:
        if m.agent != cur:
            if cur is not None:
                blocks.append(dim("  └── end subagent ──") if not md else "\n---")
            if m.agent is not None:
                label = f"subagent: {m.agent}"
                blocks.append(
                    magenta(f"  ┌── {label} ──") if not md else f"\n> **↳ {label}**"
                )
            cur = m.agent
        if md:
            body = _render_msg_md(m, args)
            if body and m.agent is not None:
                body = "\n".join("> " + ln for ln in body.splitlines())
        else:
            body = _render_msg_term(m, args, "  " if m.agent else "")
        if body:
            blocks.append(body)
    if cur is not None:
        blocks.append(dim("  └── end subagent ──") if not md else "\n---")
    return "\n\n".join(blocks)


def cmd_dump(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    b, s = resolve(backends, args.session)
    if args.raw:
        incompatible = (
            args.tools
            or args.reasoning
            or args.user_only
            or args.assistant_only
            or args.no_subagents
            or args.md
            or args.color
        )
        if incompatible:
            die("--raw cannot be combined with conversation-rendering options")
        raw = b.raw_path(s.id)
        if raw is None:
            die(f"no raw transcript for {short(s.id)}")
        if args.out:
            shutil.copyfile(raw, args.out)
            destination = Path(args.out).expanduser().resolve()
            _print_artifact(
                destination,
                args.verbose,
                f"copied raw transcript to {destination}",
            )
        else:
            with raw.open("rb") as source:
                shutil.copyfileobj(source, sys.stdout.buffer)
        return
    msgs = b.transcript(s.id, subagents=not args.no_subagents)

    # decide color: --no-color always wins; --color forces on; plain file → off; else auto
    if args.no_color or args.md:
        set_color(False)
    elif args.color:
        set_color(True)
    elif args.out:
        set_color(False)
    # else: keep the auto (tty) default

    if args.md:
        head = f"# {s.name or short(s.id)}\n\n`{s.cwd or ''}` · _{s.harness}_\n"
    else:
        head = (
            f"{bold(short(s.id))}  {s.name or ''}  {dim('[' + s.harness + ']')}\n"
            f"{path(s.cwd or '')}"
        )
    body = render_chat(msgs, args, md=args.md)
    text = head + "\n\n" + body + "\n"

    if args.out:
        destination = Path(args.out).expanduser().resolve()
        destination.write_text(
            text
            if (args.color and not args.no_color and not args.md)
            else strip_ansi(text)
        )
        _print_artifact(
            destination,
            args.verbose,
            f"wrote {len(msgs)} message(s) to {destination}",
        )
    else:
        print(text)


def _export_pairs(
    backends: list[Backend], target: str, recursive: bool
) -> tuple[list[tuple[Backend, Session]], Path | None]:
    pairs: list[tuple[Backend, Session]] = []
    project_root: Path | None = None
    if looks_like_path(target):
        root = real(target)
        project_root = Path(root)
        prefix = root.rstrip(os.sep) + os.sep
        for backend in backends:
            for session in backend.list_sessions():
                if not session.cwd:
                    continue
                cwd = real(session.cwd)
                if cwd == root or (recursive and cwd.startswith(prefix)):
                    pairs.append((backend, session))
        if not pairs:
            scope = "at or below" if recursive else "for"
            die(f"no sessions {scope} directory: {root}")
    else:
        pairs.append(resolve(backends, target))
        if pairs[0][1].cwd:
            project_root = Path(pairs[0][1].cwd)
    pairs.sort(key=lambda pair: parse_ts(pair[1].updated_at))
    return pairs, project_root


def _export_items(pairs: list[tuple[Backend, Session]]) -> list[ExportItem]:
    render_args = argparse.Namespace(
        assistant_only=False,
        color=False,
        no_color=True,
        reasoning=True,
        tools=True,
        user_only=False,
    )
    items: list[ExportItem] = []
    for backend, session in pairs:
        messages = backend.transcript(session.id, subagents=True)
        heading = (
            f"# {session.name or short(session.id)}\n\n"
            f"`{session.cwd or ''}` · _{session.harness}_ · `{session.id}`\n\n"
        )
        conversation = heading + render_chat(messages, render_args, md=True) + "\n"
        items.append(ExportItem(backend, session, conversation))
    return items


def cmd_export(args: argparse.Namespace) -> None:
    targets: list[str] = args.targets if args.global_scope else args.targets or ["."]
    global_homes = {
        real(str(COPILOT_HOME)): "copilot",
        real(str(CLAUDE_HOME)): "claude",
        real(str(CODEX_HOME)): "codex",
    }
    selected_homes: set[str] = set()
    ordinary_targets: list[str] = []
    for target in targets:
        harness = global_homes.get(real(target)) if looks_like_path(target) else None
        if harness:
            selected_homes.add(harness)
        else:
            ordinary_targets.append(target)
    inferred_global = bool(selected_homes)
    if args.global_scope or inferred_global:
        if ordinary_targets:
            die("cannot mix agent-home global targets with sessions/directories")
        if (
            selected_homes
            and args.harness != "all"
            and selected_homes != {args.harness}
        ):
            die("agent-home targets conflict with -H/--harness")
        if args.recursive:
            die("export --global cannot be combined with --recursive")
        args.global_harnesses = selected_homes or None
        cmd_export_global(args)
        return
    backends = select_backends(args.harness)
    pairs: list[tuple[Backend, Session]] = []
    project_roots: list[Path] = []
    seen_sessions: set[tuple[str, str]] = set()
    seen_roots: set[Path] = set()
    for target in targets:
        target_pairs, project_root = _export_pairs(backends, target, args.recursive)
        for pair in target_pairs:
            key = (pair[0].name, pair[1].id)
            if key not in seen_sessions:
                seen_sessions.add(key)
                pairs.append(pair)
        if project_root is not None:
            resolved_root = project_root.expanduser().resolve()
            if resolved_root not in seen_roots:
                seen_roots.add(resolved_root)
                project_roots.append(resolved_root)
    pairs.sort(key=lambda pair: parse_ts(pair[1].updated_at))
    items = _export_items(pairs)
    try:
        export_bundle(
            items,
            Path(args.out),
            target=targets,
            include_memory=args.include_memory,
            include_project_context=args.include_project_context,
            project_roots=project_roots,
            recursive=args.recursive,
        )
    except (FileExistsError, ValueError) as exc:
        die(str(exc))
    destination = Path(args.out).expanduser().resolve()
    _print_artifact(
        destination,
        args.verbose,
        green(f"exported {len(items)} session(s) to {destination}"),
    )


def cmd_export_global(args: argparse.Namespace) -> None:
    try:
        selected = getattr(args, "global_harnesses", None)
        harnesses = selected or (None if args.harness == "all" else {args.harness})
        count = export_global_bundle(Path(args.out), harnesses)
    except (FileExistsError, ValueError, OSError) as exc:
        die(str(exc))
    destination = Path(args.out).expanduser().resolve()
    _print_artifact(
        destination,
        args.verbose,
        green(f"exported {count} global agent configuration file(s) to {destination}"),
    )


def _bundle_source_cwds(sources: list[str]) -> list[Path]:
    """Collect distinct original cwd values preserved by portable bundles."""
    found: set[Path] = set()
    for value in sources:
        source = Path(value).expanduser()
        if not source.is_dir():
            continue
        try:
            manifest = json.loads((source / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("schema") != "agentsmith-export":
            continue
        sessions = manifest.get("sessions")
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if not isinstance(session, dict) or not isinstance(session.get("cwd"), str):
                continue
            found.add(Path(session["cwd"]).expanduser().resolve())
    return sorted(found)


def _select_launch_cwd(explicit: str | None, source_cwds: list[Path]) -> Path:
    if explicit:
        selected = Path(explicit).expanduser().resolve()
    elif len(source_cwds) == 1:
        selected = source_cwds[0]
    elif len(source_cwds) > 1:
        examples = ", ".join(str(path) for path in source_cwds[:3])
        suffix = " …" if len(source_cwds) > 3 else ""
        die(
            "sources span multiple working directories; pass --cwd to select the "
            f"single launch workspace ({examples}{suffix})"
        )
    else:
        selected = Path.cwd().resolve()
        print(
            yellow(
                "warning: sources contain no working-directory metadata; "
                f"launch cwd defaults to {selected} (override with --cwd)"
            ),
            file=sys.stderr,
        )
    if not selected.is_dir():
        die(f"launch working directory does not exist: {selected}; map it with --cwd")
    return selected


def cmd_merge(args: argparse.Namespace) -> None:
    """Normalize matching live sessions into one prepared continuation."""
    backends = select_backends(args.harness)
    targets: list[str] = args.targets or ["."]
    pairs: list[tuple[Backend, Session]] = []
    project_roots: list[Path] = []
    seen_sessions: set[tuple[str, str]] = set()
    seen_roots: set[Path] = set()
    for target in targets:
        target_pairs, project_root = _export_pairs(backends, target, args.recursive)
        for pair in target_pairs:
            key = (pair[0].name, pair[1].id)
            if key not in seen_sessions:
                seen_sessions.add(key)
                pairs.append(pair)
        if project_root:
            resolved_root = project_root.expanduser().resolve()
            if resolved_root not in seen_roots:
                seen_roots.add(resolved_root)
                project_roots.append(resolved_root)
    pairs.sort(key=lambda pair: parse_ts(pair[1].updated_at))
    session_cwds = {
        Path(pair[1].cwd).expanduser().resolve() for pair in pairs if pair[1].cwd
    }
    source_cwds = sorted(session_cwds)
    cwd = _select_launch_cwd(args.cwd, source_cwds)
    with tempfile.TemporaryDirectory(prefix="asmith-merge-") as temporary:
        bundle = Path(temporary) / "bundle"
        try:
            export_bundle(
                _export_items(pairs),
                bundle,
                target=targets,
                include_memory=args.include_memory,
                include_project_context=args.include_project_context,
                project_roots=project_roots,
                recursive=args.recursive,
            )
            result = prepare_continuation(
                [bundle],
                cwd,
                source_cwds,
                Path(args.out) if args.out else None,
            )
        except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
            die(str(exc))
    _print_handoff(
        result.handoff,
        args.verbose,
        green(
            f"merged {result.sessions} session(s) into continuation at {result.root}"
        ),
    )
    for warning in result.warnings:
        print(yellow(f"warning: {warning}"), file=sys.stderr)


def cmd_verify(args: argparse.Namespace) -> None:
    result = verify_bundle(Path(args.bundle))
    if result.errors:
        for error in result.errors:
            print(red(f"error: {error}"))
        die(f"bundle verification failed ({len(result.errors)} error(s))")
    print(
        green(
            f"verified {result.sessions} session(s), {result.files} checksummed file(s)"
        )
    )


def cmd_import(args: argparse.Namespace) -> None:
    inferred_global = False
    if len(args.sources) == 1:
        candidate = Path(args.sources[0]).expanduser()
        try:
            source_manifest = json.loads((candidate / "manifest.json").read_text())
            inferred_global = (
                source_manifest.get("schema") == "agentsmith-global-export"
            )
        except (OSError, json.JSONDecodeError):
            pass
    if inferred_global:
        args.bundle = args.sources[0]
        cmd_import_global(args)
        return
    source_cwds = _bundle_source_cwds(args.sources)
    cwd = _select_launch_cwd(args.cwd, source_cwds)
    try:
        result = prepare_continuation(
            [Path(source) for source in args.sources],
            cwd,
            source_cwds,
            Path(args.out) if args.out else None,
            args.source_harness,
        )
    except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
        die(str(exc))
    _print_handoff(
        result.handoff,
        args.verbose,
        green(
            f"prepared {result.sessions} recovered session(s) from "
            f"{result.sources} source(s) at {result.root}"
        ),
    )
    for warning in result.warnings:
        print(yellow(f"warning: {warning}"), file=sys.stderr)


def cmd_import_global(args: argparse.Namespace) -> None:
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else None
    if cwd is not None and not cwd.is_dir():
        die(f"launch working directory does not exist: {cwd}")
    try:
        result = prepare_global_import(
            Path(args.bundle),
            Path(args.out) if args.out else None,
            cwd,
        )
    except (FileExistsError, FileNotFoundError, ValueError, OSError) as exc:
        die(str(exc))
    _print_handoff(
        result.handoff,
        args.verbose,
        green(
            f"staged {result.files} global agent configuration file(s) at {result.root}"
        ),
    )


def _prepared_launch_cwd(
    override: str | None,
    recorded: object,
    default: Path,
) -> Path:
    """Resolve a launch cwd; only a missing recorded path falls back to current."""
    if override:
        cwd = Path(override).expanduser().resolve()
        if not cwd.is_dir():
            die(f"working directory does not exist: {cwd}")
        return cwd
    if isinstance(recorded, str):
        cwd = Path(recorded).expanduser().resolve()
        if cwd.is_dir():
            return cwd
        fallback = Path.cwd().resolve()
        print(
            yellow(
                f"warning: recorded launch cwd is missing: {cwd}; "
                f"using current directory: {fallback}"
            ),
            file=sys.stderr,
        )
        return fallback
    return default


def cmd_launch(args: argparse.Namespace) -> None:
    supplied = Path(args.handoff).expanduser().resolve()
    root = supplied if supplied.is_dir() else supplied.parent
    handoff = root / "HANDOFF.md" if supplied.is_dir() else supplied
    if not handoff.is_file():
        die(f"handoff does not exist or is not a file: {handoff}")
    manifest_path = root / "manifest.json"
    try:
        manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
        )
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read adjacent prepared-import manifest: {exc}")
    schema = manifest.get("schema")
    if schema == "agentsmith-global-import":
        result = GlobalImportResult(root, handoff, int(manifest.get("files", 0)))
        cwd = _prepared_launch_cwd(args.cwd, manifest.get("launch_cwd"), root)
        command = global_launch_command(result, args.agent, cwd)
    elif schema == "agentsmith-continuation":
        cwd = _prepared_launch_cwd(
            args.cwd,
            manifest.get("launch_cwd"),
            Path.cwd().resolve(),
        )
        result2 = ContinuationResult(
            root,
            handoff,
            len(manifest.get("sources", []))
            if isinstance(manifest.get("sources"), list)
            else 0,
            int(manifest.get("sessions", 0)),
            [],
        )
        command = launch_command(result2, args.agent, cwd)
    else:
        cwd = _prepared_launch_cwd(args.cwd, None, Path.cwd().resolve())
        try:
            command = handoff_launch_command(handoff, args.agent, cwd)
        except ValueError as exc:
            die(str(exc))
    print(dim(f"handoff: {handoff}"), file=sys.stderr)
    print(dim("launching: " + " ".join(command[:2]) + " …"), file=sys.stderr)
    try:
        completed = subprocess.run(command, cwd=cwd, check=False)
    except FileNotFoundError:
        die(f"destination CLI is not installed or not on PATH: {command[0]}")
    if completed.returncode:
        raise SystemExit(completed.returncode)


def cmd_snapshot(args: argparse.Namespace) -> None:
    try:
        receipt = create_receipt(
            [Path(target) for target in args.targets],
            Path(args.out),
        )
    except (FileExistsError, OSError, TypeError, ValueError) as exc:
        die(str(exc))
    _print_artifact(
        receipt,
        args.verbose,
        green(f"snapshotted {len(args.targets)} path(s) to {receipt}"),
    )


def _print_receipt_rows(rows: list[dict[str, object]]) -> None:
    for row in rows:
        state = str(row["action"])
        marker = green("✓") if bool(row.get("matches", True)) else yellow("!")
        print(f"{marker} {_pad(state, 10)}  {path(str(row['path']))}")


def cmd_audit(args: argparse.Namespace) -> None:
    try:
        rows = audit_receipt(Path(args.receipt), seal=args.seal)
    except (OSError, TypeError, ValueError) as exc:
        die(str(exc))
    _print_receipt_rows(rows)
    changed = sum(row["action"] != "unchanged" for row in rows)
    if args.seal:
        print(dim(f"\nsealed receipt: {changed} changed, {len(rows)} tracked"))
    else:
        drifted = sum(not bool(row["matches"]) for row in rows)
        print(dim(f"\n{drifted} drifted, {len(rows)} tracked"))
        if drifted:
            raise SystemExit(1)


def cmd_rollback(args: argparse.Namespace) -> None:
    try:
        preview = rollback_receipt(Path(args.receipt), apply=False)
    except (OSError, TypeError, ValueError) as exc:
        die(str(exc))
    changed = [row for row in preview if row["action"] != "unchanged"]
    _print_receipt_rows(preview)
    if not changed:
        print(dim("\n(nothing to roll back)"))
        return
    if args.dry_run:
        print(dim(f"\n(dry run — {len(changed)} path(s) would be restored)"))
        return
    if not args.yes:
        if not sys.stdin.isatty():
            die("refusing to roll back without confirmation; pass -y/--yes")
        response = input(
            f"Restore {len(changed)} path(s) to the receipt baseline? [y/N] "
        )
        if response.strip().lower() not in ("y", "yes"):
            print(dim("aborted"))
            return
    try:
        rollback_receipt(Path(args.receipt), apply=True)
    except (OSError, TypeError, ValueError) as exc:
        die(str(exc))
    print(green(f"restored {len(changed)} path(s) to the receipt baseline"))


def cmd_search(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    query = " ".join(args.query)
    hits: list[SearchHit] = []
    for b in backends:
        hits.extend(b.search(query, args.number))
    if not hits:
        print(dim("(no matches)"))
        return
    labels: dict[str, Session] = {s.id: s for s in all_sessions(backends)}
    hits.sort(
        key=lambda hit: parse_ts(
            labels[hit.session_id].updated_at if hit.session_id in labels else None
        ),
        reverse=True,
    )
    multi = len(backends) > 1
    cols = _term_cols()
    for h in hits[: args.number]:
        s = labels.get(h.session_id)
        label = (s.name or s.cwd or "") if s else ""
        badge = (harness_badge(h.harness) + " ") if multi else ""
        prefix = f"{badge}{bold(short(h.session_id))}  {dim(h.source)}  "
        print(prefix + _clip(label, cols - len(strip_ansi(prefix))))
        print(f"   {_clip(h.snippet, cols - 3)}")
    print(
        dim(
            f"\n{min(len(hits), args.number)} matches  ·  dump one with: asmith dump <id>"
        )
    )


def cmd_files(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    b, s = resolve(backends, args.session)
    touches = b.files(s.id, subagents=not args.main_only)
    if not touches:
        print(dim("(no files recorded)"))
        return
    for f in touches:
        tn = (f.tool or "").ljust(6)
        turn = f"t{f.turn}" if f.turn is not None else ""
        print(f"{dim(tn)}  {dim(turn)}  {f.path}")
    print(dim(f"\n{len(touches)} observed file touch(es)"))


def cmd_checkpoints(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    b, s = resolve(backends, args.session)
    cps = b.checkpoints(s.id)
    if not cps:
        print(dim("(no checkpoints)"))
        return
    for cp in cps:
        print(bold(f"#{cp.number}  {cp.title or ''}"))
        if cp.overview:
            print(f"  {cp.overview.strip()}")
        if args.verbose and cp.next_steps:
            print(dim("  next steps:"))
            print("    " + cp.next_steps.strip().replace("\n", "\n    "))
        print()


def cmd_usage(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    if args.session:
        b, s = resolve(backends, args.session)
        rows = usage_for(b, s.id, subagents=not args.main_only)
        if not rows:
            print(dim("(no usage recorded)"))
            return
        print(bold(short(s.id)) + f"  [{s.harness}] usage by model:")
        has_aiu = any(r.aiu is not None for r in rows)
        tot_aiu = 0.0
        ti = to = tcr = tcw = 0
        for r in rows:
            ti += r.input
            to += r.output
            tcr += r.cache_read
            tcw += r.cache_write
            tot_aiu += r.aiu or 0
            reason = f"  reason {r.reasoning:>7,}" if r.reasoning else ""
            tail = "  " + yellow(f"{r.aiu:8.1f} AIU") if r.aiu is not None else ""
            print(
                f"  {r.model.ljust(22)}  {str(r.calls).rjust(4)} calls  "
                f"fresh {r.input:>9,}  out {r.output:>7,}  "
                f"cache r {r.cache_read:>9,} w {r.cache_write:>7,}{reason}{tail}"
            )
        eff = ti + to + tcw + CACHE_READ_WEIGHT * tcr
        hit = tcr / (ti + tcr) * 100 if (ti + tcr) else 0.0
        parts = [f"{tot_aiu:,.1f} AIU"] if has_aiu else []
        parts.append(f"{eff:,.0f} wtc (estimate)")
        parts.append(f"cache hit {hit:.0f}%")
        if len(rows) > 1:
            parts.append(f"{len(rows)} models")
        print(dim("  total: " + " · ".join(parts)))
        return
    # Leaderboard ranked by a deliberately simple weighted-token estimate. Backends
    # normalize fresh and cached input into disjoint categories first.
    scored: list[tuple[Session, float, float, float, str, int]] = []
    for s in all_sessions(backends):
        b = backend_for(backends, s.harness)
        rows = usage_for(b, s.id, subagents=not args.main_only)
        if not rows:
            continue
        eff = sum(r.effective for r in rows)
        if eff <= 0:
            continue
        ti = sum(r.input for r in rows)
        tcr = sum(r.cache_read for r in rows)
        hit = tcr / (ti + tcr) * 100 if (ti + tcr) else 0.0
        aiu = sum(r.aiu or 0 for r in rows)
        dominant = max(rows, key=lambda r: r.effective).model
        scored.append((s, eff, hit, aiu, dominant, len(rows)))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[: args.number]
    # one fixed-width, right-aligned metric column so the id/path columns line up
    effs = [f"{eff:,.0f} wtc" for _, eff, *_ in top]
    ew = max((len(e) for e in effs), default=0)
    multi = len(backends) > 1
    cols = _term_cols()
    for (s, _eff, hit, aiu, dominant, nmodels), ecell in zip(top, effs):
        badge = (harness_badge(s.harness) + " ") if multi else ""
        prefix = (
            f"{yellow(ecell.rjust(ew))}  {dim(f'{hit:3.0f}%')}  "
            f"{badge}{bold(short(s.id))}  "
        )
        suffix_parts: list[str] = []
        if nmodels > 1:
            suffix_parts.append(f"{_short_model(dominant)} +{nmodels - 1} more")
        if aiu:
            suffix_parts.append(f"{aiu:,.0f} AIU")
        suffix = ("  " + dim(" · ".join(suffix_parts))) if suffix_parts else ""
        budget = cols - len(strip_ansi(prefix)) - len(strip_ansi(suffix))
        print(prefix + _row_tail(s.cwd, s.name, budget) + suffix)
    print(
        dim(
            f"\ntop {min(len(scored), args.number)} by estimated wtc "
            "(fresh-input + output + cache-write + 0.1×cache-read)"
            " · see: asmith usage --help"
        )
    )


_DIRS_SENTINEL = "__DIRS__"  # the shell expands this line to directory names


def _emit_positional(kind: str | None) -> None:
    if kind in ("ids", "both"):
        sessions: list[Session]
        try:
            sessions = all_sessions(select_backends("all"))
        except Exception:  # noqa: BLE001 - completion must never raise
            sessions = []
        for s in sessions:
            print(short(s.id))
    if kind in ("dirs", "both"):
        print(_DIRS_SENTINEL)


def cmd_complete(args: argparse.Namespace) -> None:
    """Hidden helper behind tab-completion: print candidates for the current word.

    Everything is read from the live argparse parser (subcommands, per-command
    flags, and choice-bearing option values), so nothing here drifts. The only
    declared knowledge is which positionals want a session id vs a directory —
    carried on the argument itself via a ``.completer`` attribute (see ``_mark``).
    """
    try:
        words: list[str] = args.words or [""]
        parser = build_parser()
        spa = next(
            (
                a
                for a in getattr(parser, "_actions", [])
                if isinstance(a, argparse._SubParsersAction)
            ),
            None,
        )
        choices: dict[str, argparse.ArgumentParser] = (
            dict(getattr(spa, "choices", {})) if spa else {}
        )
        commands = sorted(c for c in choices if not c.startswith("_"))
        if len(words) <= 1:  # completing the subcommand itself
            print("\n".join(commands))
            return

        cmd, prev, cur = words[0], words[-2], words[-1]
        sub = choices.get(cmd)
        actions = list(getattr(sub, "_actions", [])) if sub is not None else []

        # a value for an option that declares fixed choices (-S, --by, -H, ...)
        for a in actions:
            if prev in a.option_strings and a.choices:
                print("\n".join(str(c) for c in a.choices))
                return
        if prev in ("-H", "--harness"):  # also covers shell-only commands
            print("copilot\nclaude\ncodex\nall")
            return

        if cur.startswith("-"):  # completing a flag
            if sub is not None:
                print("\n".join(o for a in actions for o in a.option_strings))
            else:
                print("-H\n--harness")
            return

        # completing a positional: session ids and/or directories
        if sub is not None:
            kinds = {
                getattr(a, "completer", None) for a in actions if not a.option_strings
            }
            kind = next((k for k in ("both", "ids", "dirs") if k in kinds), None)
        else:
            kind = None
        _emit_positional(kind)
    except Exception:  # noqa: BLE001 - completion must never raise
        return


_BASH_COMPLETION = r"""_asmith_complete() {
  local cur="${COMP_WORDS[COMP_CWORD]}" out
  out="$(asmith __complete -- "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null)"
  COMPREPLY=()
  case "$out" in
    *__DIRS__*) COMPREPLY+=($(compgen -d -- "$cur")); out="${out//__DIRS__/}" ;;
  esac
  COMPREPLY+=($(compgen -W "$out" -- "$cur"))
}
complete -F _asmith_complete asmith
"""

_ZSH_COMPLETION = r"""#compdef asmith
_asmith_complete() {
  local -a lines; local l
  lines=("${(@f)$(asmith __complete -- "${(@)words[2,CURRENT]}" 2>/dev/null)}")
  for l in $lines; do
    if [[ $l == __DIRS__ ]]; then _files -/ 2>/dev/null
    elif [[ -n $l ]]; then compadd -- $l; fi
  done
}
compdef _asmith_complete asmith
"""


def cmd_completion(args: argparse.Namespace) -> None:
    print(_ZSH_COMPLETION if args.shell == "zsh" else _BASH_COMPLETION, end="")


def cmd_path(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    b, s = resolve(backends, args.session)
    loc = b.state_location(s.id)
    if loc is None:
        die(f"no on-disk location for {short(s.id)}")
    print(loc)


def cmd_grep(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    pattern = re.compile(args.pattern, 0 if args.case_sensitive else re.IGNORECASE)
    if args.session:
        b, s = resolve(backends, args.session)
        targets = [(b, s)]
    else:
        targets = [
            (backend_for(backends, s.harness), s) for s in all_sessions(backends)
        ]
    limit = max(0, args.max_count)  # 0 = unlimited (like grep with no -m)
    hits = 0
    for b, s in targets:
        for m in b.transcript(s.id, subagents=False):
            hay = (
                m.text
                + "\n"
                + "\n".join(str(t.get("summary", "") or "") for t in m.tools)
            )
            for line in hay.splitlines():
                if pattern.search(line):
                    hits += 1
                    tag = green("u") if m.role == "user" else cyan("a")
                    badge = harness_badge(s.harness)
                    print(
                        f"{badge} {bold(short(s.id))} {tag}  {highlight(line.strip(), pattern)}"
                    )
                    if limit and hits >= limit:
                        print(dim(f"\n(stopped at {limit} hits; raise with -m)"))
                        return
    print(dim(f"\n{hits} hits"))


def highlight(line: str, pattern: re.Pattern[str]) -> str:
    if not color_enabled():
        return line
    return pattern.sub(lambda m: c(m.group(0), "1;31"), line)


def cmd_redact(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    homes = [(b.name, b.home, b.scan_exclude_dirs()) for b in backends]
    fixed = not args.regex
    rx = scan.compile_pattern(args.pattern, fixed=fixed, ignore_case=args.ignore_case)
    mask_bytes = (args.mask or "*").encode("utf-8")
    if len(mask_bytes) != 1:
        die("--mask must be exactly one single-byte character")
    mask = mask_bytes[0]
    reveal = args.show_secret
    limit = max(0, args.max_count)  # 0 = unlimited (grep-style default)
    verbose = args.verbose
    quiet = args.quiet

    homes_desc = " + ".join(str(home) for _name, home, _excluded in homes)
    kind = "literal" if fixed else "regex"
    case = "case-insensitive" if args.ignore_case else "case-sensitive"

    def emit(h: scan.Hit) -> None:
        print(f"{path(str(h.path))}  {dim(h.where)}  {h.snippet}")

    # ---- find mode (--dry-run) ---------------------------------------------
    if args.dry_run:
        if verbose and not quiet:
            print(dim(f"scanning {homes_desc}  ({kind}, {case})\n"))
        res = scan.run(
            homes,
            rx,
            mask=mask,
            apply=False,
            reveal=reveal,
            max_bytes=args.max_bytes,
            emit=emit if verbose else (lambda h: None),
            limit=limit,
            collect=verbose,
        )
        _redact_summary(res, quiet=quiet)
        return

    # ---- apply mode: confirm, redact EVERYTHING, then verify ----------------
    if not args.yes:
        if not sys.stdin.isatty():
            die("refusing to redact without confirmation; pass -y/--yes")
        print(dim(f"target: {homes_desc}  ({kind}, {case})"))
        resp = input(
            red(
                f"Overwrite EVERY occurrence of this {kind} in place with "
                f"'{chr(mask)}'? This edits files and databases and cannot be "
                "undone. Run --dry-run first to preview. [y/N] "
            )
        )
        if resp.strip().lower() not in ("y", "yes"):
            print(dim("aborted"))
            return

    done = scan.run(
        homes,
        rx,
        mask=mask,
        apply=True,
        reveal=reveal,
        max_bytes=args.max_bytes,
        emit=emit if verbose else (lambda h: None),
        collect=verbose,
    )
    if done.total_matches == 0:
        print(green("no occurrences found; nothing to redact"))
        return
    if not quiet or done.total_matches:
        print(
            green(
                f"✔ redacted {done.total_matches} occurrence(s) across "
                f"{done.files_changed} file(s)/db(s)"
            )
        )
    # verify: we only need to know whether ANY remain, so stop at the first one
    verify = scan.run(
        homes,
        rx,
        mask=mask,
        apply=False,
        reveal=reveal,
        max_bytes=args.max_bytes,
        emit=lambda h: None,
        limit=1,
    )
    if verify.total_matches == 0:
        if not quiet:
            print(green("✔ verified: zero occurrences remain"))
    else:
        print(
            red(
                "⚠ at least one occurrence still remains "
                "(unreadable or locked; re-run, or close the agent first)"
            )
        )


def _redact_summary(res: scan.ScanResult, quiet: bool) -> None:
    if quiet:
        # scriptable: a bare count, with a trailing + if the sweep stopped early
        print(f"{res.total_matches}{'+' if res.stopped_early else ''}")
        return
    if res.errors:
        print(dim(f"\n{len(res.errors)} unreadable path(s) skipped"))
    scanned = f"{res.files_scanned} files + {res.dbs_scanned} db(s)"
    if res.total_matches == 0:
        print(green(f"\nno occurrences found (scanned {scanned})"))
        return
    if res.stopped_early:
        print(
            bold(
                f"\n≥{res.total_matches} occurrence(s) — stopped at the "
                "--max-count limit; drop -m for a full count"
            )
        )
    else:
        print(
            bold(
                f"\n{res.total_matches} occurrence(s) in {res.files_matched} "
                f"file(s)/db(s)  (scanned {scanned})"
            )
        )
    print(dim("(dry run — nothing modified; run without --dry-run to redact)"))


def cmd_stats(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    print(bold("Agent session store"))
    for b in backends:
        sessions = b.list_sessions()
        resumable = sum(1 for s in sessions if s.resumable)
        dirs = len({s.cwd for s in sessions if s.cwd})
        aiu = 0.0
        for s in sessions:
            aiu += sum(r.aiu or 0 for r in usage_for(b, s.id)) if args.usage else 0
        span = ""
        if sessions:
            created = [parse_ts(s.created_at) for s in sessions]
            updated = [parse_ts(s.updated_at) for s in sessions]
            valid_created = [ts for ts in created if ts > 0]
            valid_updated = [ts for ts in updated if ts > 0]
            if valid_created and valid_updated:
                lo = min(valid_created)
                hi = max(valid_updated)
                span = (
                    f"{datetime.fromtimestamp(lo, timezone.utc).astimezone():%Y-%m-%d}"
                    " → "
                    f"{datetime.fromtimestamp(hi, timezone.utc).astimezone():%Y-%m-%d}"
                )
        line = (
            f"  {harness_badge(b.name)} {bold(b.name.ljust(8))} "
            f"{len(sessions):>4} sessions  ({green(str(resumable) + ' resumable')})  "
            f"{dirs} dirs  {dim(span)}"
        )
        if args.usage and aiu:
            line += f"  {yellow(f'{aiu:,.0f} AIU')}"
        print(line)


def current_session_id() -> str | None:
    return (
        os.environ.get("COPILOT_SESSION_ID")
        or os.environ.get("COPILOT_AGENT_SESSION_ID")
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
    )


def _print_report(rep: PurgeReport, verbose: bool) -> None:
    if rep.db_rows:
        print(dim(f"   db rows:  {rep.db_rows}"))
    if rep.removed:
        print(dim(f"   deleted:  {len(rep.removed)} file(s)/dir(s)"))
        shown = rep.removed if verbose else rep.removed[:6]
        for p in shown:
            print(dim(f"     - {p}"))
        if not verbose and len(rep.removed) > 6:
            print(dim(f"     … (+{len(rep.removed) - 6} more; -v for all)"))
    if rep.scrubbed:
        total = sum(n for _, n in rep.scrubbed)
        print(dim(f"   scrubbed: {total} line(s) from {len(rep.scrubbed)} file(s)"))
        shown2 = rep.scrubbed if verbose else rep.scrubbed[:6]
        for p, n in shown2:
            print(dim(f"     ~ {p} ({n})"))
        if not verbose and len(rep.scrubbed) > 6:
            print(dim(f"     … (+{len(rep.scrubbed) - 6} more; -v for all)"))
    if rep.remaining:
        print(
            yellow(
                f"   still references id (left intact, review manually): {len(rep.remaining)}"
            )
        )
        for p in rep.remaining[:10]:
            print(yellow(f"     ? {p}"))


def _sessions_for_path(
    backends: list[Backend], arg: str, *, recursive: bool
) -> list[tuple[Backend, Session]]:
    """Sessions whose cwd is ``arg``, optionally including descendants."""
    root = real(arg if arg not in (".", "./") else os.getcwd())
    prefix = root.rstrip(os.sep) + os.sep
    out: list[tuple[Backend, Session]] = []
    for b in backends:
        for s in b.list_sessions():
            if not s.cwd:
                continue
            c = real(s.cwd)
            if c == root or (recursive and c.startswith(prefix)):
                out.append((b, s))
    return out


def _shred_targets(
    backends: list[Backend],
    targets: list[tuple[Backend, Session]],
    args: argparse.Namespace,
) -> None:
    """Preview / confirm / shred a resolved target list (live session excluded)."""
    cols_ = _term_cols()

    def line(b: Backend, s: Session) -> None:
        prefix = (
            f"{red('shred')} {harness_badge(s.harness)} {bold(short(s.id))}  "
            f"{dim(str(_turn_count(backends, s)).rjust(4) + ' turns')}  "
        )
        print(prefix + _row_tail(s.cwd, s.name, cols_ - len(strip_ansi(prefix))))

    if args.dry_run:
        detail = args.verbose or len(targets) <= 5
        for b, s in targets:
            line(b, s)
            if detail:
                _print_report(
                    b.remove(s.id, dry_run=True, aggressive=args.aggressive),
                    args.verbose,
                )
        if not detail:
            print(dim("(use -v for the per-session file/scrub detail)"))
        print(dim(f"\n(dry run — nothing removed; {len(targets)} would be shredded)"))
        return
    if not args.yes:
        if not sys.stdin.isatty():
            die(
                "refusing to delete without confirmation; pass -y/--yes (non-interactive)"
            )
        for b, s in targets:
            line(b, s)
        extra = (
            red(" (aggressive: also edits other sessions' files)")
            if args.aggressive
            else ""
        )
        resp = input(
            f"Permanently shred {len(targets)} session(s)? "
            f"No vestiges will remain{extra} [y/N] "
        )
        if resp.strip().lower() not in ("y", "yes"):
            print(dim("aborted"))
            return
    for b, s in targets:
        rep = b.remove(s.id, dry_run=False, aggressive=args.aggressive)
        print(f"{red('shredded')} {harness_badge(s.harness)} {bold(short(s.id))}")
        if args.verbose:
            _print_report(rep, args.verbose)
    print(green(f"shredded {len(targets)} session(s); no local vestiges remain"))


def cmd_rm(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    live = current_session_id()
    targets: list[tuple[Backend, Session]] = []

    def add(pair: tuple[Backend, Session]) -> None:
        if all(pair[1].id != t[1].id for t in targets):
            targets.append(pair)

    for token in args.sessions:
        if looks_like_path(token):
            matched = _sessions_for_path(backends, token, recursive=args.recursive)
            if not matched:
                die(f"no sessions for path: {real(token)}")
            for pair in matched:
                add(pair)
        else:
            add(resolve(backends, token))

    # never shred the session we're running inside; skip it in bulk, refuse if explicit
    live_hit = [t for t in targets if live and t[1].id == live]
    targets = [t for t in targets if not (live and t[1].id == live)]
    if live_hit and not targets:
        die(f"refusing to remove the current live session ({short(live or '')})")
    if live_hit:
        print(
            dim(f"(skipping the current live session {short(live or '')})"),
            file=sys.stderr,
        )
    if not targets:
        die("no sessions to remove")
    _shred_targets(backends, targets, args)


def cmd_purge(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    live = current_session_id()
    targets: list[tuple[Backend, Session]] = []
    for b in backends:
        for s in b.list_sessions():
            if live and s.id == live:
                continue
            # "useless": no on-disk transcript AND no recorded turns (empty shell)
            if not s.resumable and _turn_count(backends, s) == 0:
                targets.append((b, s))
    if not targets:
        print(dim("(nothing to purge — no empty sessions)"))
        return
    print(dim(f"{len(targets)} empty session(s) (no transcript, 0 turns):\n"))
    _shred_targets(backends, targets, args)


class _CommandHelpFormatter(argparse.HelpFormatter):
    """Use a wider option column while retaining argparse's native rendering."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30)


def _order_subcommand_help(parser: argparse.ArgumentParser) -> None:
    """Order native argparse command help by task without synthetic group headings."""
    order = (
        "list",
        "tree",
        "dirs",
        "find",
        "resolve",
        "resume",
        "show",
        "dump",
        "search",
        "grep",
        "files",
        "checkpoints",
        "usage",
        "path",
        "stats",
        "export",
        "verify",
        "import",
        "merge",
        "launch",
        "snapshot",
        "audit",
        "rollback",
        "redact",
        "rm",
        "purge",
        "completion",
    )
    ranks = {name: index for index, name in enumerate(order)}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        choices = list(getattr(action, "_choices_actions", []))
        choices.sort(
            key=lambda choice: ranks.get(
                str(getattr(choice, "dest", "")).split(" ", 1)[0],
                len(ranks),
            )
        )
        setattr(action, "_choices_actions", choices)  # noqa: B010 - argparse metadata


def build_parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI color (also honored via the NO_COLOR env var)",
    )
    common = argparse.ArgumentParser(add_help=False, parents=[output])
    common.add_argument(
        "-H",
        "--harness",
        choices=("copilot", "claude", "codex", "all"),
        default="all",
        help="which agent's sessions to use (default: all)",
    )
    p = argparse.ArgumentParser(
        prog="asmith",
        description="Swiss-army knife for AI coding-agent sessions.",
        formatter_class=_CommandHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", metavar="<command>", required=True)

    def add_number(sp: argparse.ArgumentParser, default: int | None) -> None:
        sp.add_argument(
            "-n",
            "--number",
            type=int,
            default=default,
            help="max results" + ("" if default else " (default: all)"),
        )

    def _mark(action: argparse.Action, kind: str) -> argparse.Action:
        """Tag a positional with the completion it wants: 'ids', 'dirs', or 'both'."""
        setattr(action, "completer", kind)  # noqa: B010 - argparse extension metadata
        return action

    def add_sort(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "-S",
            "--sort",
            choices=_SORT_KEYS,
            default="date",
            help="sort by date/agent/id/turns/name/dir (default: date, newest first)",
        )
        sp.add_argument(
            "-r", "--reverse", action="store_true", help="reverse the sort order"
        )

    _cols_list = (
        "Columns:  <*> <harness>  <id>  <age>  <turns>  <directory>  <name>\n"
        "  *       resumable (green *) or not (dim .)\n"
        "  harness copilot / claude / codex\n"
        "  id      8-char session id       age    time since last activity\n"
        "  turns   user-submitted turns    name   session name (/rename or aiTitle)"
    )
    sp = sub.add_parser(
        "list",
        aliases=["ls"],
        parents=[common],
        help="list sessions",
        description=_cols_list,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.add_argument("-d", "--dir", help="filter by cwd substring")
    sp.add_argument(
        "--here", action="store_true", help="only sessions for the current dir"
    )
    sp.add_argument("--repo", help="filter by repository substring")
    sp.add_argument("-g", "--grep", help="filter by name/cwd substring")
    add_sort(sp)
    add_number(sp, None)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser(
        "tree",
        parents=[common],
        help="sessions grouped by directory (or by agent)",
        description="Sessions grouped by directory (or --by agent).\n\n" + _cols_list,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.add_argument(
        "--by",
        choices=("dir", "agent"),
        default="dir",
        help="group by directory (default) or by agent",
    )
    sp.add_argument("--resumable", action="store_true", help="only resumable sessions")
    add_sort(sp)
    add_number(sp, None)
    sp.set_defaults(func=cmd_tree)

    sp = sub.add_parser(
        "dirs",
        parents=[common],
        help="directories that have sessions",
        description=(
            "Directories that have sessions, one per line.\n\n"
            "Columns:  <sessions>  <resumable>*  <last>  [<agents>]  <directory>\n"
            "  sessions   total sessions in that directory\n"
            "  resumable  how many can still be resumed (the *; green if any)\n"
            "  last       time since the most recent session there\n"
            "  agents     harness(es): co=copilot, cl=claude, cx=codex (-H all)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.add_argument("--by-count", action="store_true", help="sort by session count")
    add_number(sp, None)
    sp.set_defaults(func=cmd_dirs)

    sp = sub.add_parser("find", parents=[common], help="find sessions for a directory")
    _mark(
        sp.add_argument("dir", nargs="?", default=".", help="directory (default: cwd)"),
        "dirs",
    )
    sp.add_argument("--resumable", action="store_true", help="only resumable sessions")
    sp.add_argument("--exact", action="store_true", help="exact cwd match only")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser(
        "resolve",
        parents=[common],
        help="resolve a unique id prefix or path to a full session id",
    )
    _mark(
        sp.add_argument(
            "target",
            nargs="?",
            default=".",
            help="full id / unique id prefix / path / . (default: cwd)",
        ),
        "both",
    )
    sp.add_argument(
        "--resumable",
        action="store_true",
        help="require an on-disk (resumable) session",
    )
    sp.add_argument("--exact", action="store_true", help="exact cwd match (path args)")
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser(
        "resume",
        parents=[output],
        help="resume an agent's newest session for a directory",
    )
    sp.add_argument(
        "agent",
        choices=("copilot", "claude", "codex"),
        metavar="AGENT",
        help="agent to resume",
    )
    _mark(
        sp.add_argument(
            "dir",
            nargs="?",
            default=".",
            metavar="DIR",
            help="exact session working directory (default: current directory)",
        ),
        "dirs",
    )
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("show", parents=[common], help="show session metadata")
    _mark(
        sp.add_argument("session", help="full id / unique id prefix / path / ."),
        "ids",
    )
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser(
        "dump",
        parents=[common],
        help="dump one session's conversation (a path selects the newest)",
    )
    _mark(
        sp.add_argument("session", help="full id / unique id prefix / path / ."),
        "ids",
    )
    sp.add_argument(
        "-t", "--tools", action="store_true", help="include tool args + results"
    )
    sp.add_argument(
        "-R", "--reasoning", action="store_true", help="include reasoning text"
    )
    roles = sp.add_mutually_exclusive_group()
    roles.add_argument("--user-only", action="store_true", help="only user messages")
    roles.add_argument(
        "--assistant-only", action="store_true", help="only assistant messages"
    )
    sp.add_argument(
        "--no-subagents", action="store_true", help="hide subagent (task) turns"
    )
    sp.add_argument("--md", action="store_true", help="render as Markdown")
    sp.add_argument(
        "--color", action="store_true", help="force ANSI color (e.g. into a file)"
    )
    sp.add_argument(
        "--raw",
        action="store_true",
        help="emit one underlying transcript byte-for-byte (-o copies to FILE)",
    )
    sp.add_argument("-o", "--out", help="write to FILE")
    sp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="with -o, describe the artifact on stderr",
    )
    sp.set_defaults(func=cmd_dump)

    sp = sub.add_parser(
        "export",
        parents=[common],
        help="export portable session bundle(s) to a new directory",
        description=(
            "Export sessions/projects, defaulting to the current project. An exact "
            "agent-home target (~/.claude, ~/.copilot, or ~/.codex) selects that "
            "agent's globals; --global selects all globals. The bundle must be new."
        ),
    )
    _mark(
        sp.add_argument(
            "targets",
            nargs="*",
            metavar="TARGET",
            help="full session id, unique id prefix, project path, or exact agent home",
        ),
        "both",
    )
    sp.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="export user-wide agent instructions/configuration instead of sessions",
    )
    sp.add_argument(
        "-o",
        "--out",
        required=True,
        metavar="BUNDLE",
        help="new checksummed export bundle directory",
    )
    sp.add_argument(
        "--recursive",
        action="store_true",
        help="for a path, include sessions in nested directories",
    )
    sp.set_defaults(include_memory=True, include_project_context=True)
    sp.add_argument(
        "--no-memory",
        dest="include_memory",
        action="store_false",
        help="exclude attributable project-scoped memory",
    )
    sp.add_argument(
        "--no-project-context",
        dest="include_project_context",
        action="store_false",
        help="exclude project-scoped instructions, hooks, settings, and skills",
    )
    sp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="describe the exported bundle on stderr",
    )
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser(
        "verify",
        parents=[output],
        help="verify a portable export manifest and every checksum",
    )
    sp.add_argument(
        "bundle",
        metavar="BUNDLE",
        help="checksummed AgentSmith export bundle",
    )
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser(
        "import",
        parents=[output],
        help="prepare export bundle(s) or raw dump(s) for another agent",
        description=(
            "Create an agent-neutral, reviewable HANDOFF.md from existing bundles or "
            "dumps. Global bundles create an editable candidate tree. Use the separate "
            "`launch AGENT HANDOFF` command after review."
        ),
    )
    sp.add_argument(
        "sources",
        nargs="+",
        metavar="SOURCE",
        help="AgentSmith bundle, native JSONL dump, compressed dump, or archive directory",
    )
    sp.add_argument(
        "--from",
        dest="source_harness",
        choices=("copilot", "claude", "codex"),
        help="source dump format (normally auto-detected)",
    )
    sp.add_argument(
        "--cwd",
        help=(
            "launch workspace (inferred from one source cwd; required for multiple; "
            "global imports default to the prepared directory)"
        ),
    )
    sp.add_argument(
        "-o",
        "--out",
        metavar="PREPARED",
        help="new prepared import directory (default: XDG state directory)",
    )
    sp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="describe the prepared handoff and next command on stderr",
    )
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser(
        "launch",
        parents=[output],
        help="launch an agent with a prepared import or any handoff file",
        description=(
            "Launch an agent in YOLO mode with a prepared import directory, its "
            "HANDOFF.md, or any standalone handoff file."
        ),
    )
    sp.add_argument(
        "agent",
        choices=("copilot", "claude", "codex"),
        metavar="AGENT",
        help="agent to launch",
    )
    sp.add_argument(
        "handoff",
        metavar="HANDOFF",
        help="prepared import directory or handoff file",
    )
    sp.add_argument(
        "--cwd",
        help="workspace for a standalone handoff or override of the prepared workspace",
    )
    sp.set_defaults(func=cmd_launch)

    sp = sub.add_parser(
        "snapshot",
        parents=[output],
        help="snapshot exact paths before agent-managed changes",
        description=(
            "Create a new reversible change receipt containing the exact baseline "
            "state of each target. Broad targets such as the home directory are refused."
        ),
    )
    sp.add_argument(
        "targets",
        nargs="+",
        metavar="PATH",
        help="exact file or directory to track (it may not exist yet)",
    )
    sp.add_argument(
        "-o",
        "--out",
        required=True,
        metavar="RECEIPT",
        help="new receipt directory outside all tracked paths",
    )
    sp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="describe the snapshot on stderr",
    )
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser(
        "audit",
        parents=[output],
        help="audit or seal a filesystem change receipt",
    )
    sp.add_argument("receipt", metavar="RECEIPT", help="change receipt directory")
    sp.add_argument(
        "--seal",
        action="store_true",
        help="record the current state as the approved post-change state",
    )
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser(
        "rollback",
        parents=[output],
        help="restore paths from a filesystem change receipt",
        description=(
            "Restore every tracked path to its exact pre-change state. Files created "
            "after the snapshot are removed; modified/deleted paths are restored."
        ),
    )
    sp.add_argument("receipt", metavar="RECEIPT", help="change receipt directory")
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be restored without changing files",
    )
    sp.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sp.set_defaults(func=cmd_rollback)

    sp = sub.add_parser(
        "merge",
        parents=[common],
        help="combine live session/project targets into one prepared handoff",
        description=(
            "Resolve live session ids/prefixes and project paths, export the matching "
            "sessions temporarily, and normalize them chronologically into one "
            "agent-neutral handoff. Original sessions are never modified."
        ),
    )
    _mark(
        sp.add_argument(
            "targets",
            nargs="*",
            metavar="TARGET",
            help="full session id, unique id prefix, or project path (default: cwd)",
        ),
        "both",
    )
    sp.add_argument(
        "--cwd",
        help="launch workspace (inferred from one source cwd; required for multiple)",
    )
    sp.add_argument(
        "-o",
        "--out",
        metavar="PREPARED",
        help="new prepared continuation directory (default: XDG state directory)",
    )
    sp.add_argument(
        "--recursive",
        action="store_true",
        help="also include sessions in nested working directories",
    )
    sp.set_defaults(include_memory=True, include_project_context=True)
    sp.add_argument(
        "--no-memory",
        dest="include_memory",
        action="store_false",
        help="exclude attributable project-scoped memory",
    )
    sp.add_argument(
        "--no-project-context",
        dest="include_project_context",
        action="store_false",
        help="exclude project-scoped agent instructions/configuration",
    )
    sp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="describe the prepared handoff and next command on stderr",
    )
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("search", parents=[common], help="search across sessions")
    sp.add_argument("query", nargs="+", help="query terms")
    add_number(sp, 20)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser(
        "files",
        parents=[common],
        help="file touches recorded or inferred from session tool calls",
    )
    _mark(
        sp.add_argument("session", help="full id / unique id prefix / path / ."),
        "ids",
    )
    sp.add_argument(
        "--main-only", action="store_true", help="exclude distinguishable subagents"
    )
    sp.set_defaults(func=cmd_files)

    sp = sub.add_parser("checkpoints", parents=[common], help="checkpoints")
    _mark(
        sp.add_argument("session", help="full id / unique id prefix / path / ."),
        "ids",
    )
    sp.add_argument("-v", "--verbose", action="store_true", help="include next steps")
    sp.set_defaults(func=cmd_checkpoints)

    sp = sub.add_parser(
        "usage",
        parents=[common],
        help="token/AIU usage or leaderboard",
        description=(
            "Per-session token usage (input/output, cache read+write, reasoning) "
            "and AIU, or a cross-harness leaderboard when no session is given. "
            "Backends normalize fresh input and cache reads into disjoint counts. "
            "The leaderboard ranks by wtc (weighted token count) = fresh-input + "
            "output + cache-write + 0.1*cache-read. This is a rough, model-agnostic "
            "estimate, not a token total or currency cost: model prices and output/"
            "cache-write multipliers vary. Cache reads use 0.1 as a broadly useful "
            "approximation. 'cache hit' is cache-read / (fresh-input + cache-read). "
            "Multi-model sessions show the dominant model, e.g. 'opus-4.8 +1 more'."
        ),
    )
    _mark(
        sp.add_argument(
            "session",
            nargs="?",
            help="full id / unique id prefix / path / . (omit for leaderboard)",
        ),
        "ids",
    )
    sp.add_argument(
        "--main-only", action="store_true", help="exclude distinguishable subagents"
    )
    add_number(sp, 15)
    sp.set_defaults(func=cmd_usage)

    sp = sub.add_parser(
        "completion", help="print the tab-completion script (source it in your shell)"
    )
    sp.add_argument("shell", choices=("bash", "zsh"), help="which shell")
    sp.set_defaults(func=cmd_completion)

    # hidden helper the completion script calls on each <Tab>; no help= keeps it
    # out of the command listing.
    sp = sub.add_parser("__complete")
    sp.add_argument("words", nargs="*")
    sp.set_defaults(func=cmd_complete)

    sp = sub.add_parser("path", parents=[common], help="print on-disk location")
    _mark(
        sp.add_argument(
            "session",
            nargs="?",
            default=".",
            help="full id / unique id prefix / path / . (default: cwd)",
        ),
        "both",
    )
    sp.set_defaults(func=cmd_path)

    sp = sub.add_parser("grep", parents=[common], help="regex-search full transcripts")
    sp.add_argument("pattern", help="regex")
    sp.add_argument("session", nargs="?", help="limit to one session")
    sp.add_argument("-s", "--case-sensitive", action="store_true")
    sp.add_argument(
        "-m",
        "--max-count",
        type=int,
        default=0,
        metavar="NUM",
        help="stop after NUM matches (default 0 = all, like grep -m)",
    )
    sp.set_defaults(func=cmd_grep)

    sp = sub.add_parser(
        "redact",
        parents=[common],
        help="find/scrub a secret EVERYWHERE (all files + databases)",
    )
    sp.add_argument("pattern", help="the secret to find (literal by default)")
    sp.add_argument(
        "--regex",
        action="store_true",
        help="treat the pattern as a regex instead of a literal string",
    )
    sp.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="case-insensitive match (default: case-sensitive, safer for secrets)",
    )
    sp.add_argument(
        "--mask",
        default="*",
        metavar="CHAR",
        help="replacement character, same length as the match (default: *)",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="find and report only; never modify",
    )
    sp.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sp.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="list every match (default: totals only)",
    )
    sp.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print only the count / result",
    )
    sp.add_argument(
        "--show-secret",
        action="store_true",
        help="reveal the matched text in output (default masks it)",
    )
    sp.add_argument(
        "--max-bytes",
        type=int,
        default=0,
        metavar="N",
        help="skip files larger than N bytes (default: 0 = no limit)",
    )
    sp.add_argument(
        "-m",
        "--max-count",
        type=int,
        default=0,
        metavar="NUM",
        help="--dry-run: stop after NUM matches (default 0 = all, like grep -m). "
        "Ignored when redacting — that always processes everything.",
    )
    sp.set_defaults(func=cmd_redact)

    sp = sub.add_parser("stats", parents=[common], help="per-harness statistics")
    sp.add_argument(
        "--usage", action="store_true", help="also compute AIU totals (slower)"
    )
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("rm", parents=[common], help="fully delete session(s)")
    _mark(
        sp.add_argument(
            "sessions",
            nargs="+",
            help="full id(s), unique id prefix(es), or exact project path(s)",
        ),
        "both",
    )
    sp.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sp.add_argument("--dry-run", action="store_true", help="show what would be removed")
    sp.add_argument(
        "--recursive",
        action="store_true",
        help="for path targets, also remove sessions in nested directories",
    )
    sp.add_argument(
        "-v", "--verbose", action="store_true", help="list every path touched"
    )
    sp.add_argument(
        "--aggressive",
        action="store_true",
        help="also scrub the id out of OTHER sessions' transcripts/memory (edits their files)",
    )
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser(
        "purge",
        parents=[common],
        help="shred all empty sessions (no transcript, 0 turns)",
    )
    sp.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sp.add_argument("--dry-run", action="store_true", help="show what would be removed")
    sp.add_argument(
        "-v", "--verbose", action="store_true", help="list every path touched"
    )
    sp.set_defaults(aggressive=False)
    sp.set_defaults(func=cmd_purge)

    _order_subcommand_help(p)
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "no_color", False):
        set_color(False)
    try:
        args.func(args)
    except (KeyboardInterrupt, EOFError):
        print(dim("\naborted"), file=sys.stderr)
        return 130
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
