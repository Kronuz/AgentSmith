# Agentsmith (`asmith`)

A swiss-army knife for your AI coding-agent sessions, across **three harnesses**:

- **copilot** — GitHub Copilot CLI (`~/.copilot`)
- **claude** — Claude Code (`~/.claude`)
- **codex** — OpenAI Codex CLI (`~/.codex`)

List the directories that have sessions, find/resume the right one, render full
conversations (with nested subagents), full-text search, token/AIU accounting,
and completely shred local session state you want gone. The project is
**Agentsmith**, and
the command is `asmith` (as in *Agent Smith* — the one who tracks sessions down
and erases them).

New here? Start with the **[TUTORIAL](TUTORIAL.md)** — a hands-on tour with
copy-paste examples.

Two parts:

- **`agentsmith/`** — the engine, a Python package (stdlib only, no dependencies):
  - `cli.py` (commands + argument parsing), `model.py` (dataclasses),
    `util.py` (helpers), `export.py` (portable bundles), `purge.py` (the shred),
    `config.py` (shared paths),
    and `backends/` with `base.py` (the `Backend` ABC), one module per harness,
    and `__init__.py` (selection + cross-harness resolution). Run it directly with
    `python -m agentsmith ...`.
- **`agentsmith.sh`** — a sourceable bash/zsh wrapper: the `asmith` command, the
  `copilot()` / `claude()` / `codex()` auto-resume wrappers, and completions.

## Install

Source the shell file from your `~/.profile` (or `~/.zshrc` / `~/.bashrc`):

```sh
[ -r "$HOME/code/Agentsmith/agentsmith.sh" ] && . "$HOME/code/Agentsmith/agentsmith.sh"
```

That single line defines `asmith`, all three agent wrappers, aliases, and **tab
completion** (bash + zsh). Requires `python3` (stdlib only) and each agent CLI you
use on `PATH`. Override the interpreter with
`ASMITH_PYTHON=/path/to/python3`.

Tab completion is registered automatically when you source the file. The
candidates are computed by the CLI itself (`asmith __complete`, which introspects the
argument parser), so they never drift from the actual commands and flags. If you
prefer to install completion without sourcing the whole file, use the standard
generator instead:

```sh
# bash (~/.bashrc)
source <(asmith completion bash)
# zsh (~/.zshrc, after compinit)
source <(asmith completion zsh)
```

## Picking a harness

Every command takes `-H/--harness {copilot,claude,codex,all}` (default: **all**).
Aggregate commands (`list`, `dirs`, `search`, `stats`, `usage` leaderboard) merge
all three. `list` and `recent` always show the full harness name; compact views
use `co` (copilot), `cl` (claude), or `cx` (codex). Session
commands auto-detect which harness owns an id (UUIDs never collide).

## Where the data comes from

- **copilot**: `~/.copilot/session-store.db` (SQLite) for metadata, and
  `~/.copilot/session-state/<id>/events.jsonl` for the full transcript. A session
  is **resumable** only if that events.jsonl still exists (the DB is a superset
  that also keeps "archived" sessions). Usage includes AIU.
- **claude**: `~/.claude/projects/<enc-cwd>/<id>.jsonl` transcripts (one file per
  session; titles come from the transcript). Always resumable. Usage is tokens
  only (Claude has no AIU). A per-file index is cached at `~/.cache/asmith/claude-index.json`.
- **codex**: the newest `~/.codex/state_*.sqlite` for the thread index and dated rollout
  JSONL files under `~/.codex/sessions`. Archived or missing rollouts are not
  resumable. Usage includes input/output, cache, and reasoning tokens.
  Delegated child threads are nested under their parent rather than listed as
  independent sessions; their transcript, usage, files, export, and deletion are
  aggregated with the parent.

Parsed usage summaries are cached by native-artifact mtime/size under
`~/.cache/asmith/usage/`, so repeated leaderboards do not reparse every transcript.

## The session name

Each row shows the session's **name** and its **path** (cwd), on one line — path
first (in color), then the name. The name comes straight from the agent's store:
Copilot's session `name` (the `workspace.yaml` `name:` / the `sessions.summary`
column, which you set with a rename) and Claude's `aiTitle` (updated by
`/rename`). Agents auto-name a session by default; a short one-off may get named
after its first prompt, so the name can read like a prompt. A session with no name
shows `(no name)`.

## Session references

Anywhere a command takes `<session>` you can pass:

- a full id or a **unique id prefix** (`10b72094`), resolved across all harnesses,
- a **path** (`~/code/x`, `/tmp`, or `.` for the current dir), resolved to the
  newest session for that directory.

The 8-character hash shown in listings (`10b72094`) is just a **display
abbreviation** of the full UUID — every command accepts it (or any unique prefix)
directly, and `asmith resolve <hash>` prints the full id. Note that only sessions
marked **`*`** in `asmith list` are **resumable**; a **`.`** means the session is kept
in the index for reference but its transcript is gone, so neither `asmith resume` nor
the underlying CLI can reopen it (regardless of whether you use the short hash or
the full id).

## Commands

| Command | What it does |
| --- | --- |
| `asmith list` (`ls`) | List **all** sessions (path + name, newest first). `-S/--sort date\|agent\|id\|turns\|name\|dir`, `-r`, `-d DIR`, `--here`, `--repo`, `-g GREP`, `-n N`. |
| `asmith tree` | Sessions grouped **by directory** (or `--by agent`), each as a one-liner. `-S/--sort`, `-r`, `--resumable`. |
| `asmith dirs` | Directories that have sessions (columns explained in `asmith dirs --help`). `--by-count`. |
| `asmith find [dir]` | Sessions for a directory. `-1` prints the newest id; `--with-harness`, `--resumable`, `--exact`. |
| `asmith resolve [id/prefix/path]` | Print the **full** session id for a short hash/prefix/dir (`--resumable`, `--with-harness`). |
| `asmith show <session>` | Metadata: cwd, repo/branch, times, turns, files, tokens/AIU, checkpoints, resume line. |
| `asmith dump <session>` | Render a conversation. `-t` tools, `-R` reasoning, `--no-subagents`, `--md`, `--color`, `--raw`, `-o FILE`. |
| `asmith export <id/path> -o DIR` | Create a portable, checksummed bundle. A path exports every exact-cwd session; `--recursive` includes nested cwd paths; `--include-memory` adds attributable project memory. |
| `asmith verify <bundle>` | Verify the export schema, safe relative paths, sizes, and every SHA-256 checksum. |
| `asmith search <query…>` | Literal-phrase search across sessions (Copilot FTS5; Claude/Codex scan), merged by recency. |
| `asmith grep <regex> [session]` | Regex over full **transcripts** (rendered conversation only). `-m/--max-count` (default: all). |
| `asmith redact <secret>` | Find/scrub a string **everywhere** — every file *and* database under all harness homes. `--dry-run` (find only), `-v`/`-q`, `-m/--max-count`, `--regex`, `-i`, `--mask`, `-y`, `--show-secret`, `--max-bytes`. |
| `asmith files <session>` | File touches recorded or inferred from tool calls, including distinguishable subagents by default (not a workspace snapshot). `--main-only` excludes them. |
| `asmith checkpoints <session>` (`cp`) | Checkpoints (copilot only). `-v` for next steps. |
| `asmith usage [session]` | Per-model fresh input/output/cache/reasoning + AIU, including distinguishable subagents by default, or a leaderboard ranked by estimated **wtc**. `--main-only` excludes them. |
| `asmith recent [-n N]` | Most recent sessions across all directories. |
| `asmith path [session]` | Print the on-disk location (default: session for cwd). |
| `asmith stats` | Per-harness totals (sessions, resumable, dirs, span). `--usage` adds AIU. |
| `asmith rm <id/path…>` (`prune`) | **Shred local state** for session(s). Takes ids/prefixes **or a path** (all sessions under it). `-y`, `--dry-run`, `-v`, `--aggressive`. |
| `asmith purge` | Shred all **empty** sessions (no transcript, 0 turns). `-y`, `--dry-run`, `-v`, `-H`. |
| `asmith resume [-H h] [target]` | Resume a session — `target` is a **dir** (default: cwd) *or* an **id/prefix** (shell; launches the right CLI). |
| `asmith cd [session]` | `cd` into a session's on-disk location (shell). |
| `asmith ids` | Print session ids, one per line (scripting / completion). `--full` for full uuids. |
| `asmith completion <bash\|zsh>` | Print the tab-completion script for your shell. |

## The agent wrappers

Sourcing the file defines all three. Bare `copilot`, `claude`, or `codex` resumes the newest
resumable session whose cwd exactly matches the current directory; with no match
it starts fresh. Any arguments pass straight through to the real CLI. They resolve
via `asmith find --one --resumable --exact -H <harness> .`. `copilot()` replaces the
inline function that used to live in `~/.profile`. Codex starts and resumes in
full bypass (“YOLO”) mode.

## Reading a conversation (`asmith dump`)

`asmith dump <session>` renders a session as a readable chat — colored in a terminal,
plain when piped:

```sh
asmith dump 10b72094               # user + assistant text (roles, tool calls one-lined)
asmith dump 10b72094 -t -R         # + tool args/results (truncated) and reasoning
asmith dump 10b72094 --no-subagents  # hide subagent (task) turns
asmith dump 10b72094 --md > chat.md  # Markdown (view with glow/bat/VS Code)
asmith dump 10b72094 --color -o chat.ansi   # keep ANSI in the file; `cat chat.ansi`
asmith dump 10b72094 --raw > raw.jsonl      # the underlying transcript file, verbatim
asmith dump 10b72094 --raw -o raw.jsonl     # equivalently, copy it to a file
```

**Subagents.** Copilot keeps `task`-tool subagent turns inline in the transcript;
Claude keeps them in separate `subagents/*.jsonl` files. `asmith dump` shows both by
default, **nested and labeled** under a `┌── subagent: … ──` boundary, and
`--no-subagents` hides them. (A Claude session can have dozens of subagents, so
that flag is handy there.)

Note the default view is a cleaned reconstruction (user/assistant text, system
reminders stripped, tool results truncated). Use `--raw` for the byte-for-byte
transcript file.

## Portable exports

`dump` intentionally remains a one-session, stream-friendly command. A path selects
the newest session, and `--raw` emits or copies that one native transcript. Use
`export` when a path has multiple sessions or when you need a moveable archive:

```sh
asmith export 10b72094 -o ~/exports/one-session
asmith export . -o ~/exports/all-sessions-here
asmith export ~/code/project --recursive --include-memory -o ~/exports/project
asmith verify ~/exports/project
```

The destination must be new. Each bundle has a versioned `manifest.json` with a
SHA-256 inventory, normalized `conversation.md`, metadata, usage and touched-file
records, plus the harness's native session-owned files. Project memory is excluded
unless explicitly requested because it can be shared by many sessions.

Exports are portable archives for inspection and transfer, not fabricated native
sessions. Native import and cross-agent merge require harness-specific adapters:
copying JSONL between agents would not preserve tool-call pairing, compactions,
indexes, or resumability. A safe cross-agent merge must create a new destination
session from a normalized handoff while retaining the originals.

## Shredding sessions (`asmith rm`)

`asmith rm <id…>` removes **every** trace of a session — no record, no transcript,
nothing:

- deletes every file/dir named after the id (transcripts, per-session dirs,
  id-named logs/tasks/env/history, and copilot's DB rows + FTS entries);
- line-scrubs the id out of shared bookkeeping (`history.jsonl`, process logs);
- prunes the session's entries from structured JSON state
  (`vscode.session.metadata.cache.json`, `command-history-state.json`);
- **reports** (but does not touch) references that live inside *other* sessions'
  transcripts or memory notes — use `--aggressive` to scrub those too (this edits
  other sessions' files, so it's opt-in and flagged at the prompt).

Verified by a sandbox that plants the id in every table and vestige location: after
one `rm` (or `purge`) there are **zero** id-named files, **zero** content
references, and **zero** DB rows left, while other sessions stay intact.

`asmith rm` takes **ids, unique prefixes, or a path** — a path expands to *every*
session for that directory and below (great for wiping a whole project). It
refuses the current live session (skips it in bulk), confirms unless `-y`, and
previews everything with `--dry-run` (`-v` to list every path).

```sh
asmith rm 386cd898 --dry-run          # preview the shred
asmith rm 386cd898 111b6b0f -y        # shred two, no confirmation
asmith rm 10b72094 --aggressive -v    # also scrub cross-references, list everything
asmith rm ~/code/oldproject --dry-run # every session under a directory
```

## Purging empty sessions (`asmith purge`)

Over time the store fills with **empty shells** — sessions with no on-disk
transcript and 0 turns (nothing was ever said), which can't be resumed or read.
`asmith purge` shreds all of them in one go:

```sh
asmith purge --dry-run    # list the empties (fast)
asmith purge              # confirm, then shred them all
asmith purge -H copilot   # scope to one agent
```

It uses the same shredding as `asmith rm`, skips the live session, and confirms unless
`-y`.

## Scrubbing a leaked secret (`asmith redact`)

`asmith grep` searches only the rendered **conversation**. If a password, token, or key
leaked into an agent's state, it can also land in logs, per-session `session.db`
files, Copilot's FTS5 search index, JSON/YAML bookkeeping, tool arguments, and
subagent transcripts. `asmith redact` is the leak hunter: it walks mutable files
under all harness homes (any type, no size cap by default) and every SQLite database
it finds there. Codex installation assets (`packages`, plugins, skills and caches)
are excluded: they are large immutable program data, not session state, and
byte-redacting them could corrupt installed executables.

```sh
asmith redact 'hunter2!' --dry-run       # find everywhere, totals only (read-only)
asmith redact 'hunter2!' --dry-run -v     # ...and list every match (masked)
asmith redact 'hunter2!' --dry-run -m 1   # quick "is it anywhere?" — stop at the first
asmith redact 'hunter2!'                  # confirm, then overwrite every occurrence
asmith redact 'sk-[a-z0-9]+' --regex      # regex mode (default is literal — safer)
asmith redact 'hunter2!' --mask '-'       # mask with a different character
```

- **`--dry-run` is the finder.** By default it counts **everything** (like `grep`, no
  cap) and prints only totals; `-v` lists every match, `-q` prints just the count, and
  `-m/--max-count N` stops after N (a fast "is it anywhere?"). **Redacting always
  processes everything** — `-m` is ignored there, so it can't miss occurrences.
- **Literal + case-sensitive by default** (a secret has regex metacharacters and
  exact case); opt into `--regex` / `-i`.
- **Same-length, in place.** Each match is overwritten with a mask character, so file
  offsets, JSON/YAML validity, and DB page layout are preserved — and no copy is left
  behind in a backup.
- **Databases are handled via SQL**, never by editing the file bytes: matching cells
  are rewritten, the FTS index is **rebuilt**, and the DB is **VACUUMed** with its WAL
  truncated, so the old value can't linger in freed pages.
- **The secret is masked in the tool's own output** (so it isn't re-leaked into your
  terminal/scrollback); pass `--show-secret` to reveal it.
- After redacting, it **re-scans to verify zero remain**. Close the running agent
  first if a live database is locked.

> Note: this scrubs state under `~/.copilot`, `~/.claude`, and `~/.codex`. A truly
> leaked secret should still be **rotated** — redaction cleans local copies, not
> anything that already left your machine.

## Environment overrides

- `COPILOT_HOME` / `COPILOT_DB` / `COPILOT_STATE` — copilot store locations.
- `CLAUDE_HOME` — claude store location.
- `CODEX_HOME` / `CODEX_DB` / `CODEX_SESSIONS` — Codex store locations.
- `ASMITH_CACHE` (default `~/.cache/asmith`) — where the Claude index is cached.
- `ASMITH_PYTHON` — interpreter used by the shell wrapper.
- `NO_COLOR` — disable ANSI color (or pass `--no-color` on any command).

These double as the way to point the tool at a sandbox for testing destructive
commands without touching real data.
