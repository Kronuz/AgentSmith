"""Command-line interface: argument parsing and command implementations."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, override

from . import scan
from .backends import (
    Backend,
    all_sessions,
    backend_for,
    resolve,
    select_backends,
)
from .model import Msg, PurgeReport, SearchHit, Session
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
    multi = len(backends) > 1
    cols = _term_cols()
    for s in sessions:
        mark = green("*") if s.resumable else dim(".")
        badge = (harness_badge(s.harness) + " ") if multi else ""
        turns = _turn_count(backends, s)
        prefix = (
            f"{mark} {badge}{bold(short(s.id))}  {dim(ago(s.updated_at).rjust(4))}  "
            f"{dim(str(turns).rjust(4) + 't')}  "
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
        f"{dim(str(turns).rjust(4) + 't')}  "
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
            badges = "".join(sorted(harness_badge(h) for h in a["harnesses"]))
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
        if not args.one:
            kind = "resumable " if args.resumable else ""
            print(dim(f"(no {kind}session for {real(args.dir)})"), file=sys.stderr)
        raise SystemExit(1)
    if args.one:
        b, s = pairs[0]
        if args.with_harness:
            print(f"{s.harness}\t{s.id}")
        else:
            print(s.id)
        return
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
    b, s = resolve(backends, args.target, resumable=args.resumable, exact=args.exact)
    if args.resumable and not s.resumable:
        die(f"session {short(s.id)} is not resumable (no on-disk transcript)")
    print(f"{s.harness}\t{s.id}" if args.with_harness else s.id)


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
    rows = b.usage(s.id)
    itok = sum(r.input for r in rows)
    otok = sum(r.output for r in rows)
    if any(r.aiu is not None for r in rows):
        aiu = sum(r.aiu or 0 for r in rows)
        field("tokens", f"↑{itok:,} ↓{otok:,}  ·  {aiu:.1f} AIU")
    else:
        field("tokens", f"↑{itok:,} ↓{otok:,}")
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
        raw = b.raw_path(s.id)
        if raw is None:
            die(f"no raw transcript for {short(s.id)}")
        sys.stdout.write(raw.read_text())
        return
    msgs = b.transcript(s.id, subagents=not args.no_subagents)

    # decide color: forced on with --color; off when writing a plain file; else auto
    if args.md:
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
        Path(args.out).write_text(
            text if (args.color and not args.md) else strip_ansi(text)
        )
        print(f"wrote {args.out} ({len(msgs)} messages)")
    else:
        print(text)


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
        dim(f"\n{min(len(hits), args.number)} matches  ·  dump one with: cw dump <id>")
    )


def cmd_files(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    b, s = resolve(backends, args.session)
    touches = b.files(s.id)
    if not touches:
        print(dim("(no files recorded)"))
        return
    for f in touches:
        tn = (f.tool or "").ljust(6)
        turn = f"t{f.turn}" if f.turn is not None else ""
        print(f"{dim(tn)}  {dim(turn)}  {f.path}")
    print(dim(f"\n{len(touches)} files"))


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
        rows = b.usage(s.id)
        if not rows:
            print(dim("(no usage recorded)"))
            return
        print(bold(short(s.id)) + f"  [{s.harness}] usage by model:")
        tot_aiu = 0.0
        for r in rows:
            model = r.model.ljust(20)
            tail = ""
            if r.aiu is not None:
                tot_aiu += r.aiu
                tail = "  " + yellow(f"{r.aiu:8.1f} AIU")
            print(
                f"  {model}  {str(r.calls).rjust(4)} calls  "
                f"↑{r.input:>9,} ↓{r.output:>7,}  cache {r.cache:>10,}{tail}"
            )
        if tot_aiu:
            print(dim(f"  total: {tot_aiu:.1f} AIU"))
        return
    # leaderboard across sessions, sorted by output tokens (common metric)
    scored: list[tuple[Session, int, float]] = []
    for s in all_sessions(backends):
        b = backend_for(backends, s.harness)
        rows = b.usage(s.id)
        otok = sum(r.output for r in rows)
        aiu = sum(r.aiu or 0 for r in rows)
        if otok or aiu:
            scored.append((s, otok, aiu))
    scored.sort(key=lambda x: x[1], reverse=True)
    multi = len(backends) > 1
    cols = _term_cols()
    for s, otok, aiu in scored[: args.number]:
        badge = (harness_badge(s.harness) + " ") if multi else ""
        metric = yellow(f"{aiu:8.1f} AIU") if aiu else dim(f"{otok:>10,} out")
        prefix = f"{metric}  {badge}{bold(short(s.id))}  "
        print(prefix + _row_tail(s.cwd, s.name, cols - len(strip_ansi(prefix))))
    print(dim(f"\ntop {min(len(scored), args.number)} by output tokens"))


def cmd_recent(args: argparse.Namespace) -> None:
    args.here = False
    args.dir = None
    args.repo = None
    args.grep = None
    cmd_list(args)


def cmd_ids(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    for s in all_sessions(backends):
        print(s.id if args.full else short(s.id))


# Shell-only subcommands (handled by the cw() wrapper, absent from this parser) and
# the kind of positional each completes: "ids", "dirs", or "both".
_SHELL_COMMANDS = {"resume": "both", "r": "both", "cd": "ids"}
_DIRS_SENTINEL = "__DIRS__"  # the shell expands this line to directory names


def _emit_positional(kind: str | None) -> None:
    if kind in ("ids", "both"):
        try:
            for s in all_sessions(select_backends("all")):
                print(short(s.id))
        except Exception:  # noqa: BLE001 - completion must never raise
            pass
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
        commands = sorted(
            [c for c in choices if not c.startswith("_")] + list(_SHELL_COMMANDS)
        )
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
            print("copilot\nclaude\nall")
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
            kind = _SHELL_COMMANDS.get(cmd)
        _emit_positional(kind)
    except Exception:  # noqa: BLE001 - completion must never raise
        return


_BASH_COMPLETION = r"""_cw_complete() {
  local cur="${COMP_WORDS[COMP_CWORD]}" out
  out="$(cw __complete -- "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null)"
  COMPREPLY=()
  case "$out" in
    *__DIRS__*) COMPREPLY+=($(compgen -d -- "$cur")); out="${out//__DIRS__/}" ;;
  esac
  COMPREPLY+=($(compgen -W "$out" -- "$cur"))
}
complete -F _cw_complete cw
"""

_ZSH_COMPLETION = r"""#compdef cw
_cw_complete() {
  local -a lines; local l
  lines=("${(@f)$(cw __complete -- "${(@)words[2,CURRENT]}" 2>/dev/null)}")
  for l in $lines; do
    if [[ $l == __DIRS__ ]]; then _files -/ 2>/dev/null
    elif [[ -n $l ]]; then compadd -- $l; fi
  done
}
compdef _cw_complete cw
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
    homes = [(b.name, b.home) for b in backends]
    fixed = not args.regex
    rx = scan.compile_pattern(args.pattern, fixed=fixed, ignore_case=args.ignore_case)
    mask = (args.mask or "*").encode("utf-8")[0]
    reveal = args.show_secret
    limit = max(0, args.max_count)  # 0 = unlimited (grep-style default)
    verbose = args.verbose
    quiet = args.quiet

    homes_desc = " + ".join(str(h) for _n, h in homes)
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
            aiu += sum(r.aiu or 0 for r in b.usage(s.id)) if args.usage else 0
        span = ""
        if sessions:
            lo = min(parse_ts(s.created_at) for s in sessions)
            hi = max(parse_ts(s.updated_at) for s in sessions)
            span = (
                f"{datetime.fromtimestamp(lo):%Y-%m-%d} → "
                f"{datetime.fromtimestamp(hi):%Y-%m-%d}"
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
    return os.environ.get("COPILOT_SESSION_ID") or os.environ.get(
        "COPILOT_AGENT_SESSION_ID"
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
    backends: list[Backend], arg: str
) -> list[tuple[Backend, Session]]:
    """All sessions whose cwd is ``arg`` or nested beneath it."""
    root = real(arg if arg not in (".", "./") else os.getcwd())
    prefix = root.rstrip(os.sep) + os.sep
    out: list[tuple[Backend, Session]] = []
    for b in backends:
        for s in b.list_sessions():
            if not s.cwd:
                continue
            c = real(s.cwd)
            if c == root or c.startswith(prefix):
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
            f"{dim(str(_turn_count(backends, s)).rjust(4) + 't')}  "
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
    print(green(f"shredded {len(targets)} session(s); no vestiges remain"))


def cmd_rm(args: argparse.Namespace) -> None:
    backends = select_backends(args.harness)
    live = current_session_id()
    targets: list[tuple[Backend, Session]] = []

    def add(pair: tuple[Backend, Session]) -> None:
        if all(pair[1].id != t[1].id for t in targets):
            targets.append(pair)

    for token in args.sessions:
        if looks_like_path(token):
            matched = _sessions_for_path(backends, token)
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
    """Drop the redundant '<command>' metavar header above the subcommand list."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=30)

    @override
    def _format_action(self, action: argparse.Action) -> str:
        text = super()._format_action(action)
        if action.nargs == argparse.PARSER:
            text = "\n".join(text.split("\n")[1:])
        return text


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-H",
        "--harness",
        choices=("copilot", "claude", "all"),
        default="all",
        help="which agent's sessions to use (default: all)",
    )

    p = argparse.ArgumentParser(
        prog="cw",
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
        setattr(action, "completer", kind)
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
        "Columns:  <*> <id>  <age>  <turns>t  <directory>  <name>\n"
        "  *       resumable (green *) or not (dim .)\n"
        "  id      8-char session id       age    time since last activity\n"
        "  turns   user/agent exchanges    name   session name (/rename or aiTitle)"
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
            "  agents     harness(es) present: co=copilot, cl=claude (only with -H all)"
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
    sp.add_argument("-1", "--one", action="store_true", help="print only the newest id")
    sp.add_argument(
        "--with-harness", action="store_true", help="with --one, print 'harness<TAB>id'"
    )
    sp.add_argument("--resumable", action="store_true", help="only resumable sessions")
    sp.add_argument("--exact", action="store_true", help="exact cwd match only")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser(
        "resolve",
        parents=[common],
        help="resolve an id/prefix/path to a full session id",
    )
    _mark(
        sp.add_argument(
            "target",
            nargs="?",
            default=".",
            help="id / prefix / path / . (default: cwd)",
        ),
        "both",
    )
    sp.add_argument(
        "--resumable",
        action="store_true",
        help="require an on-disk (resumable) session",
    )
    sp.add_argument(
        "--with-harness", action="store_true", help="print 'harness<TAB>id'"
    )
    sp.add_argument("--exact", action="store_true", help="exact cwd match (path args)")
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("show", parents=[common], help="show session metadata")
    _mark(sp.add_argument("session", help="id / prefix / path / ."), "ids")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("dump", parents=[common], help="dump a session's conversation")
    _mark(sp.add_argument("session", help="id / prefix / path / ."), "ids")
    sp.add_argument(
        "-t", "--tools", action="store_true", help="include tool args + results"
    )
    sp.add_argument(
        "-R", "--reasoning", action="store_true", help="include reasoning text"
    )
    sp.add_argument("--user-only", action="store_true", help="only user messages")
    sp.add_argument(
        "--assistant-only", action="store_true", help="only assistant messages"
    )
    sp.add_argument(
        "--no-subagents", action="store_true", help="hide subagent (task) turns"
    )
    sp.add_argument("--md", action="store_true", help="render as Markdown")
    sp.add_argument(
        "--color", action="store_true", help="force ANSI color (e.g. into a file)"
    )
    sp.add_argument("--raw", action="store_true", help="emit the raw transcript file")
    sp.add_argument("-o", "--out", help="write to FILE")
    sp.set_defaults(func=cmd_dump)

    sp = sub.add_parser("search", parents=[common], help="search across sessions")
    sp.add_argument("query", nargs="+", help="query terms")
    add_number(sp, 20)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("files", parents=[common], help="files touched in a session")
    _mark(sp.add_argument("session", help="id / prefix / path / ."), "ids")
    sp.set_defaults(func=cmd_files)

    sp = sub.add_parser(
        "checkpoints", aliases=["cp"], parents=[common], help="checkpoints"
    )
    _mark(sp.add_argument("session", help="id / prefix / path / ."), "ids")
    sp.add_argument("-v", "--verbose", action="store_true", help="include next steps")
    sp.set_defaults(func=cmd_checkpoints)

    sp = sub.add_parser(
        "usage", parents=[common], help="token/AIU usage or leaderboard"
    )
    _mark(
        sp.add_argument(
            "session", nargs="?", help="id / prefix / path / . (omit for leaderboard)"
        ),
        "ids",
    )
    add_number(sp, 15)
    sp.set_defaults(func=cmd_usage)

    sp = sub.add_parser("recent", parents=[common], help="most recent sessions")
    add_sort(sp)
    add_number(sp, 15)
    sp.set_defaults(func=cmd_recent)

    sp = sub.add_parser(
        "ids", parents=[common], help="print session ids (scripting / tab-completion)"
    )
    sp.add_argument(
        "--full", action="store_true", help="print full uuids instead of 8-char ids"
    )
    sp.set_defaults(func=cmd_ids)

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
            help="id / prefix / path / . (default: cwd)",
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

    sp = sub.add_parser(
        "rm", aliases=["prune"], parents=[common], help="fully delete session(s)"
    )
    _mark(
        sp.add_argument(
            "sessions",
            nargs="+",
            help="id(s), unique prefix(es), or path(s) (a path removes every session "
            "for that directory and below)",
        ),
        "both",
    )
    sp.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    sp.add_argument("--dry-run", action="store_true", help="show what would be removed")
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
    sp.add_argument("--aggressive", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_purge)

    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (KeyboardInterrupt, EOFError):
        print(dim("\naborted"), file=sys.stderr)
        return 130
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
