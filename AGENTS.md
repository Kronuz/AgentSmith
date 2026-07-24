# AGENTS.md — Agentsmith

Guidance for AI agents (and humans) working on this project.

## What this is

**Agentsmith** is a swiss-army knife for inspecting AI coding-agent sessions,
exposed as the `asmith` command. It reads (and can shred) the local session stores of
three harnesses:

- **copilot** — GitHub Copilot CLI (`~/.copilot`)
- **claude** — Claude Code (`~/.claude`)
- **codex** — OpenAI Codex CLI (`~/.codex`)

Everything is read-only except `asmith rm` (session shred). There are **no
AI/model-dependent commands** — an earlier `summarize`/`triage` pair was removed
because it needed a working agent (`copilot -p`) that isn't reliably available.

## Files

| Path | Role |
| --- | --- |
| `agentsmith/` | The engine, a Python package (stdlib only, no deps). Run as `python -m agentsmith`. |
| `agentsmith/cli.py` | Argument parsing + every `cmd_*` command. |
| `agentsmith/model.py` | Dataclasses: `Session`, `Msg`, `FileTouch`, `SearchHit`, `UsageRow` (normalized disjoint fresh-input/cache-read plus output/cache-write/reasoning/aiu; `effective` is the rough **wtc** estimate), `Checkpoint`, `PurgeReport`. |
| `agentsmith/util.py` | Color/time/text helpers, `die`. |
| `agentsmith/purge.py` | `deep_purge` (the shred). |
| `agentsmith/scan.py` | `run`/`scan_file`/`scan_db` (the everywhere-scanner behind `asmith redact`). |
| `agentsmith/export.py` | Atomic portable bundle writer (manifest, hashes, normalized and native files). |
| `agentsmith/config.py` | Shared paths (`CACHE_DIR`, `HARNESSES`). |
| `agentsmith/backends/base.py` | The `Backend` ABC + shared id/path resolution helpers. |
| `agentsmith/backends/copilot.py` | `CopilotBackend` + copilot store paths. |
| `agentsmith/backends/claude.py` | `ClaudeBackend` + claude store paths. |
| `agentsmith/backends/codex.py` | `CodexBackend` + Codex store paths. |
| `agentsmith/backends/__init__.py` | `select_backends`, `resolve`, `all_sessions`, `backend_for`. |
| `agentsmith.sh` | Sourceable bash/zsh wrapper: `asmith`, agent auto-resume wrappers, completions. |
| `README.md` / `AGENTS.md` / `TUTORIAL.md` | User docs / agent-facing docs / hands-on walkthrough. |
| `pyrefly.toml` | Strict type-check config (`project-includes = ["agentsmith"]`). |

The shell wrapper runs the package via `python -m agentsmith`, keeping it
importable by putting its parent dir on `PYTHONPATH`.

## Architecture

A package, three layers:

1. **Data model** (`model.py`): `Session`, `Msg`, `FileTouch`, `SearchHit`,
   `UsageRow`, `Checkpoint`, `PurgeReport`. `util.py` holds the color/time/text
   helpers and `die`; `config.py` holds shared paths; `purge.py` holds
   `deep_purge`.
2. **Backends** (`backends/`): an abstract `Backend` (`base.py`) with three
   implementations. Each maps its harness's native storage onto the shared model.
   - `CopilotBackend` — SQLite (`~/.copilot/session-store.db`) for metadata +
     the per-session `~/.copilot/session-state/<id>/events.jsonl` transcript.
     Usage carries AIU (`total_nano_aiu / 1e9`) plus token counts (input, output,
     cache read **and** write, reasoning). "Resumable" = the events.jsonl
     exists on disk.
   - `ClaudeBackend` — JSONL transcripts at
     `~/.claude/projects/<enc-cwd>/<id>.jsonl` (dir name encodes cwd, `/`→`-`).
     No DB, so it builds a lightweight index cached at `~/.cache/asmith/claude-index.json`
     keyed on `(mtime, size)`. The name comes from `ai-title` events; usage is
     tokens only (no AIU; cache split into read = `cache_read_input_tokens` and
     write = `cache_creation_input_tokens`; no separate reasoning count). Always
     "resumable".
   - `CodexBackend` — the newest SQLite `~/.codex/state_*.sqlite` thread index +
     dated rollout JSONL files under `~/.codex/sessions`. It parses both function
     and custom tools and accounts for input/output/cache/reasoning tokens.
     Archived sessions remain inspectable but are not resumable.
3. **Commands** (`cmd_*`): each selects backends via `-H/--harness`
   (`copilot`/`claude`/`codex`/`all`, default `all`), then iterates. Session-specific
   commands resolve an id/prefix/path across all selected backends
   (`resolve()`), so ids never need a harness qualifier.

Usage summaries are cached per session under `~/.cache/asmith/usage/`, keyed by
the native artifacts' paths, mtimes, and sizes. Both usage and Claude index caches
use atomic replacement so concurrent `asmith` invocations cannot leave partial JSON.

### Adding a harness

Implement every abstract method of `Backend`, then add it to `select_backends()`
and the `HARNESSES` tuple. If it can produce a resume command, wire it into
`asmith resume` (the shell) via the `harness` tag that `find --with-harness` prints.

## The shred (`asmith rm`)

`deep_purge(home, id, dry_run, aggressive)` removes *every* trace of a session id
under a harness home:

1. **Pass 1 — names**: delete any file/dir whose name contains the id
   (transcripts, `tasks/<id>`, `session-env/<id>`, `file-history/<id>/`,
   `seek/logs/<id>.jsonl`, `session-state/<id>/`).
2. **Pass 2 — content**: for surviving files that still mention the id,
   line-scrub only *shared bookkeeping* (`history.jsonl`, `*.log`, anything under
   a `logs/` dir). Everything else that references the id is **reported**
   (`PurgeReport.remaining`), not edited — deleting lines from another session's
   transcript could corrupt its resume. `--aggressive` also line-scrubs the id
   out of other text/line files (`.jsonl/.md/.txt/.log`), which *does* edit other
   sessions' files (opt-in, flagged at the confirm prompt).

Copilot's DB rows (all `session_id`-keyed tables + `sessions` + FTS
`search_index`) are deleted via SQL in `CopilotBackend.remove`.

`cmd_rm` accepts ids/prefixes **or a path** (`_sessions_for_path` expands to every
session whose cwd is at/under it). `cmd_purge` targets **empty shells**
(`not resumable and turn_count == 0`). Both build a target list and hand it to
`_shred_targets` (preview / confirm / shred). Safety: skips the current live
session (`COPILOT_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, or `CODEX_THREAD_ID`)
rather than shredding it
(refuses if it was the only
explicit target), confirms unless `-y`, previews with `--dry-run` (per-session
detail gated behind `-v` or ≤5 targets so bulk previews stay instant).

`deep_purge` walks the harness home in two passes: **pass 1** deletes anything
named after the id (per-session dirs, id-named logs/tasks/env, id-named dirs even
inside `file-history/`); **pass 2** scrubs the id out of shared content. It only
**reads** files up to a size cap (`_MAX_SCAN_BYTES` for scrubbable bookkeeping like
logs/history; a much smaller `_REF_SCAN_BYTES` for files it would only *report*),
which keeps `asmith rm` fast on a real `~/.copilot` full of multi-hundred-MB
transcripts. Skip sets are split: `_SKIP_ALWAYS` (`.git`, `node_modules`) is
skipped in **both** passes; `_SKIP_SCAN` (`rewind-file-snapshots`, `file-history`)
is skipped only in the **pass-2 content scan** (so pass 1 still deletes id-named
dirs there). Structured JSON state (`vscode.session.metadata.cache.json` keyed by
id, `command-history-state.json` list items with `sessionId`) is pruned surgically
via `_prune_json`/`_json_belongs` (drop the id's keys/entries, rewrite the rest);
if the id somehow survives pruning it's reported, never corrupted.

## The everywhere-scanner (`asmith redact`)

`deep_purge` targets one **session id** in known bookkeeping spots. `scan.py` is the
orthogonal tool: find/scrub an **arbitrary string** (a leaked secret) across *all*
harness state, not just rendered transcripts. `asmith grep` only reads `transcript()`;
`scan.run(homes, rx, mask, apply, reveal, max_bytes, emit)` walks every mutable-state
file under each home (excluding Codex installation assets) and every SQLite DB it
finds, so it also covers logs, per-session
`session.db` files, the FTS5 index, JSON/YAML, tool args, and subagent transcripts.

- **SQLite is detected by magic header** (`b"SQLite format 3\x00"`), not extension —
  there are per-session `session-state/<id>/session.db` files plus the top-level
  `session-store.db`, and extensionless files that are *not* SQLite.
- **Files** (`scan_file`) are redacted in place via `mmap` with a **same-length** mask
  (`bytes([mask]) * len`), preserving offsets/JSON/DB layout and leaving no `.bak`.
- **SQLite sidecars** (`-wal`/`-shm`/`-journal`, via `is_db_sidecar`) are **never**
  byte-edited (that would corrupt the WAL salt/checksums); their committed content is
  handled through the DB, and a modified DB's WAL is truncated.
- **DBs** (`scan_db`) are edited only through SQL, **preserving each cell's storage
  class**: TEXT is decoded back to `str` before binding (so `typeof` stays `text`, not
  `blob`), BLOB stays bytes, and `surrogateescape` round-trips any bytes losslessly.
  Do **not** set `con.text_factory = bytes` — it flips TEXT columns to BLOB on write
  and turns table names into bytes, which silently broke the FTS shadow-skip and
  double-counted. FTS5 **shadow** tables (`<fts>_data/_idx/_docsize/_config/_content`)
  are skipped; the
  FTS **virtual** table's `content` is redacted via the vtable, then
  `INSERT INTO <fts>(<fts>) VALUES('rebuild')`, then `VACUUM` + `wal_checkpoint(TRUNCATE)`
  so the secret is gone from index segments and freed pages too.
- **The match is masked in the tool's own output** (`_snippet`, unless `reveal`) so the
  finder never re-leaks the secret into the terminal/scrollback.
- Defaults are **literal + case-sensitive** (secrets carry regex metachars); `--regex` /
  `-i` opt out. `redact` previews (dry-run scan), confirms, applies, then re-scans to
  **verify zero remain**. A locked live DB is reported, not silently missed.

Proven by a sandbox that plants the string in a normal file, a log, a `.md`, a `.json`,
a 2MB file, the main DB (turns/checkpoints/summary/FTS), a per-session `session.db`
(todos/inbox), and Claude transcripts + subagents: after one redact, a raw byte grep of
every file (incl. the `.db`s) returns **0**, `PRAGMA integrity_check` is `ok`, and the
FTS `MATCH` finds nothing — while same-length is preserved and JSON stays valid.

## Transcripts and subagents

`transcript(session_id, subagents=True)` returns a flat `list[Msg]`, each `Msg`
carrying an optional `agent` label (`None` = main thread). Subagents:

- **Copilot** keeps `task`-tool subagent turns **inline** in `events.jsonl`, tagged
  with `parentToolCallId`; `subagent.started` names them (`agentName`). The backend
  tags those `Msg`s with `"{agentName}#{tail}"`.
- **Claude** keeps them in **separate** `projects/<enc>/<id>/subagents/*.jsonl`
  files (none inline); the backend parses each (with `skip_sidechain=False`) and
  appends them tagged by filename.
- `subagents=False` drops them. `cmd_dump` passes `not args.no_subagents`; `search`,
  `grep`, and internal callers use `subagents=False` (main thread only) for speed.

`asmith dump` renders via `render_chat()` (terminal ANSI or `--md`), nesting each
subagent run under a `┌── subagent: … ──` boundary. `--color` forces ANSI (e.g.
into a file); `--raw` streams the underlying transcript file verbatim.

## Conventions (must-hold)

- **Interpreter**: LinkedIn-managed `/export/apps/python/3.12/bin/python3`
  (stdlib only — do not add third-party deps; this file must stay `pip`-free).
- **Lint/type**: `ruff format` + `ruff check` clean, and `pyrefly check` at
  `preset = "strict"` with **0 errors**. Every overriding backend method carries
  `@override`. Full annotations; `die()` is `NoReturn`.
- **Color**: `util.set_color()` overrides tty auto-detection; build strings with
  the color helpers, which honor it at call time. Auto-on for a tty (unless
  `NO_COLOR`); `dump --color` forces it on into a non-tty sink; the global
  `--no-color` flag (and `NO_COLOR`) forces it off, applied in `main()` before
  any command runs.

## Testing

- Run `python -m unittest discover -s tests -v` for hermetic fixtures covering
  all three backends, portable export, and Codex parent/child aggregation.
- Also exercise real stores when available. Copilot has ~130 sessions; Claude has a handful of
  main sessions plus many nested `subagents/` transcripts (only the top-level
  `<id>.jsonl` are "sessions").
- **Never test destructive paths on real data.** Point the env overrides at a
  sandbox: `COPILOT_HOME`, `COPILOT_DB`, `COPILOT_STATE`, `CLAUDE_HOME`,
  `CODEX_HOME`, `CODEX_DB`, `CODEX_SESSIONS`, `ASMITH_CACHE`, `ASMITH_SUMMARIES`.
  The shred test builds a fake `CLAUDE_HOME` with
  id-named vestiges + a second session, shreds, and asserts zero traces of the
  target while the other session stays intact.
- `asmith --harness claude ...` / `-H copilot` scope a command to one harness.

## Gotchas

- Claude dir names are lossy (`/`→`-`); always read the real `cwd` from inside
  the JSONL, never decode the dir name.
- Claude `<synthetic>` assistant messages are skipped in usage.
- The FTS `search_index` is a normal (non-external-content) fts5 table, so
  `DELETE ... WHERE session_id = ?` works.
- Cross-harness id collisions are effectively impossible (both are UUIDs), so
  resolving an id across both backends is safe.
