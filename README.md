# AgentSmith (`asmith`)

A swiss-army knife for your AI coding-agent sessions, across **three harnesses**:

- **copilot** — GitHub Copilot CLI (`~/.copilot`)
- **claude** — Claude Code (`~/.claude`)
- **codex** — OpenAI Codex CLI (`~/.codex`)

List the directories that have sessions, find/resume the right one, render full
conversations (with nested subagents), full-text search, token/AIU accounting,
and completely shred local session state you want gone. The project is
**AgentSmith**, and
the command is `asmith` (as in *Agent Smith* — the one who tracks sessions down
and erases them).

New here? Start with the **[TUTORIAL](TUTORIAL.md)** — a hands-on tour with
copy-paste examples.

Two parts:

- **`agentsmith/`** — the engine, a Python package (stdlib only, no dependencies):
  - `cli.py` (commands + argument parsing), `model.py` (dataclasses),
    `util.py` (helpers), `export.py` (portable bundles),
    `continuation.py` (cross-agent imports), `environment.py` (portable agent
    configuration), `purge.py` (the shred), `config.py` (shared paths),
    and `backends/` with `base.py` (the `Backend` ABC), one module per harness,
    and `__init__.py` (selection + cross-harness resolution). Run it directly with
    `python -m agentsmith ...`.
- **`agentsmith.sh`** — a sourceable bash/zsh wrapper: the `asmith` command, the
  `copilot()` / `claude()` / `codex()` auto-resume wrappers, and completions.

## Install

Source the shell file from your `~/.profile` (or `~/.zshrc` / `~/.bashrc`):

```sh
[ -r "$HOME/code/AgentSmith/agentsmith.sh" ] && . "$HOME/code/AgentSmith/agentsmith.sh"
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

Commands that read agent stores take `-H/--harness {copilot,claude,codex,all}`
(default: **all**). Aggregate commands (`list`, `dirs`, `search`, `stats`, `usage`
leaderboard) merge all three. `list` always shows the full harness name; compact views
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
| `asmith find [DIR]` | List sessions for a directory. `--resumable`, `--exact`. |
| `asmith resolve [ID/PREFIX/PATH]` | Resolve a unique id prefix or path to one **full** session id (`--resumable`, `--exact`). |
| `asmith show <session>` | Metadata: cwd, repo/branch, times, turns, files, tokens/AIU, checkpoints, resume line. |
| `asmith dump <session>` | Render a conversation. `-t` tools, `-R` reasoning, `--no-subagents`, `--md`, `--color`, `--raw`, `-o FILE`, `-v`. |
| `asmith export [TARGET…] -o BUNDLE` | Export sessions/projects, defaulting to the current project. An exact agent-home target such as `~/.codex` exports that agent's globals; `--global` exports all globals. |
| `asmith verify <bundle>` | Verify the export schema, safe relative paths, sizes, and every SHA-256 checksum. |
| `asmith import SOURCE… [-o PREPARED]` | Build an agent-neutral handoff from existing bundles/dumps. Global imports create an editable `candidate/` and critical-review `HANDOFF.md`. |
| `asmith launch AGENT HANDOFF` | Launch an agent with a prepared directory, its printed `HANDOFF.md`, or any standalone handoff file. `--cwd` selects the workspace. |
| `asmith merge [TARGET…] [-o PREPARED]` | Discover and normalize live sessions selected by project paths or unique ids/prefixes into one agent-neutral handoff. |
| `asmith snapshot PATH… -o RECEIPT` | Snapshot exact paths before an agent changes live configuration. |
| `asmith audit RECEIPT [--seal]` | Seal the approved post-change state or detect later drift. |
| `asmith rollback RECEIPT` | Restore the exact baseline and remove newly created tracked paths. `--dry-run`, `-y`. |
| `asmith search <query…>` | Literal-phrase search across sessions (Copilot FTS5; Claude/Codex scan), merged by recency. |
| `asmith grep <regex> [session]` | Regex over full **transcripts** (rendered conversation only). `-m/--max-count` (default: all). |
| `asmith redact <secret>` | Find/scrub a string **everywhere** — every file *and* database under all harness homes. `--dry-run` (find only), `-v`/`-q`, `-m/--max-count`, `--regex`, `-i`, `--mask`, `-y`, `--show-secret`, `--max-bytes`. |
| `asmith files <session>` | File touches recorded or inferred from tool calls, including distinguishable subagents by default (not a workspace snapshot). `--main-only` excludes them. |
| `asmith checkpoints <session>` | Checkpoints (copilot only). `-v` for next steps. |
| `asmith usage [session]` | Per-model fresh input/output/cache/reasoning + AIU, including distinguishable subagents by default, or a leaderboard ranked by estimated **wtc**. `--main-only` excludes them. |
| `asmith path [session]` | Print the on-disk location (default: session for cwd). |
| `asmith stats` | Per-harness totals (sessions, resumable, dirs, span). `--usage` adds AIU. |
| `asmith rm <id/path…>` | **Shred local state** for session(s). Path targets are exact unless `--recursive` is explicit. `-y`, `--dry-run`, `-v`, `--aggressive`. |
| `asmith purge` | Shred all **empty** sessions (no transcript, 0 turns). `-y`, `--dry-run`, `-v`, `-H`. |
| `asmith resume AGENT [DIR]` | Resume the selected agent's newest resumable session for `DIR` (default: current directory). |
| `asmith cd [session]` | `cd` into a session's on-disk location (shell). |
| `asmith completion <bash\|zsh>` | Print the tab-completion script for your shell. |

## Complete option reference

Every command supports `-h/--help`. Commands that read agent stores also support
`-H/--harness copilot|claude|codex|all` (default `all`) and `--no-color`.
`SESSION`/`TARGET` accepts a full id, a **unique id prefix**, or a path where
documented. A prefix is never a wildcard: AgentSmith rejects it if zero or multiple
sessions match. Paths are exact for `export`, `merge`, and `rm` unless the command's
explicit `--recursive` option is supplied.

### Browse sessions

`asmith list` / `asmith ls`

- `-d/--dir TEXT`: cwd substring filter.
- `--here`: exact current-directory sessions.
- `--repo TEXT`: repository substring filter.
- `-g/--grep TEXT`: name or cwd substring filter.
- `-S/--sort date|agent|id|turns|name|dir`: sort key; date is default.
- `-r/--reverse`: reverse the selected order.
- `-n/--number N`: maximum rows; omitted means all.

```sh
asmith ls --here -S turns -r
asmith ls -H codex -g agentsmith -n 20
```

`asmith tree`

- `--by dir|agent`: group by directory (default) or agent.
- `--resumable`: exclude sessions that cannot be reopened.
- `-S/--sort`, `-r/--reverse`, `-n/--number`: same meanings as `list`.

`asmith dirs`

- `--by-count`: rank directories by session count instead of recency.
- `-n/--number N`: maximum directories; omitted means all.

`asmith find [DIR]`

- `--resumable`: require an on-disk resumable session.
- `--exact`: match only the exact cwd; otherwise nested paths may match.

`asmith resolve [TARGET]`

- `--resumable`: require a resumable result.
- `--exact`: exact cwd matching for path targets.

### Inspect and analyze

`asmith show SESSION` has no command-specific options. It reports identity, cwd,
repository, timestamps, turns, usage, files, checkpoints, and the native resume line.

`asmith dump SESSION`

- `-t/--tools`: include full tool arguments and results.
- `-R/--reasoning`: include recorded reasoning text.
- `--user-only` / `--assistant-only`: select one speaker.
- `--no-subagents`: omit distinguishable child-agent turns.
- `--md`: render Markdown instead of terminal chat.
- `--color`: retain ANSI color even when redirected.
- `--raw`: emit one underlying native transcript byte-for-byte.
- `-o/--out FILE`: write to a file; with `--raw`, copy the native transcript.
- `-v/--verbose`: with `-o`, describe the artifact on stderr.

`asmith search QUERY…`

- `-n/--number N`: maximum cross-agent literal-phrase hits; default 20.

`asmith grep REGEX [SESSION]`

- `-s/--case-sensitive`: disable the default case-insensitive matching.
- `-m/--max-count N`: stop after N transcript matches; `0` means all.

`asmith files SESSION`

- `--main-only`: exclude file touches attributed to distinguishable subagents.

`asmith checkpoints SESSION`

- `-v/--verbose`: include checkpoint next steps. Checkpoints are currently native
  to Copilot sessions.

`asmith usage [SESSION]`

- `--main-only`: exclude distinguishable subagents.
- `-n/--number N`: leaderboard size when no session is supplied; default 15.

With a session, usage is split by model and fresh input/output/cache/reasoning.
Without one, it shows the cross-agent weighted-token-count leaderboard.

`asmith path [SESSION]` prints the native on-disk artifact path; it defaults to the
newest session for the current directory.

`asmith stats`

- `--usage`: also aggregate AIU usage; slower because transcripts must be parsed.

### Move and continue

`asmith export [TARGET…] -o BUNDLE`

- With no target, export sessions for the current project.
- A target may be a full session id, unique id prefix, or project path.
- Every exported session retains its original cwd; multi-project bundles preserve all
  distinct project roots rather than collapsing them into one.
- An exact agent home (`~/.codex`, `~/.claude`, `~/.copilot`) selects that agent's
  global configuration.
- `--global`: export global configuration for all selected harnesses.
- `--recursive`: include sessions whose cwd is nested below a path target.
- `--no-memory`: omit attributable project memory.
- `--no-project-context`: omit project instructions, hooks, settings, and skills.
- `-o/--out BUNDLE`: required new destination; it must not already exist.
- `-v/--verbose`: describe the bundle on stderr.

```sh
asmith export -o ~/exports/current
asmith export 10b72094 ~/code/other -o ~/exports/selected
asmith export --global -o ~/exports/globals
```

`asmith verify BUNDLE` validates schema, safe paths, sizes, and every SHA-256 checksum.

`asmith import SOURCE… [-o PREPARED]`

- Accepts one or more AgentSmith bundles, native `.jsonl`/`.jsonl.gz` dumps, or
  archive directories. Multiple sources become one handoff.
- `--from copilot|claude|codex`: resolve an ambiguous native dump dialect.
- `--cwd PROJECT`: explicit launch workspace. With one recorded source cwd it is
  inferred; multiple recorded cwds require this option. Sources without cwd metadata
  use the current directory with a warning. Global imports default to their prepared
  directory.
- `-o/--out PREPARED`: new review directory; otherwise use the XDG state directory.
- `-v/--verbose`: describe the handoff and suggested launch command on stderr.

`asmith merge [TARGET…] [-o PREPARED]`

- Discovers live sessions; unlike `import`, it does not consume dump/bundle sources.
- Each target may be a live full session id, unique id prefix, or project path;
  default current project.
- Multiple targets are combined chronologically and deduplicated.
- `--recursive`: for path targets, include sessions in nested project directories.
- `--cwd PROJECT`: workspace recorded for a later `launch`; it does not launch now.
  When all sessions share one cwd it is inferred; mixed cwd selections require this
  option.
- `--no-memory` / `--no-project-context`: omit those exported inputs.
- `-o/--out PREPARED`: new review directory; otherwise use the XDG state directory.
- `-v/--verbose`: describe the handoff and suggested launch command on stderr.

Artifact-producing commands keep stdout composable: `export` prints only the bundle
directory; `import` and `merge` print only the generated `HANDOFF.md`; and
`dump -o` prints only its output file. Verbose context goes to stderr and therefore
does not change command substitution, pipes, or `xargs`:

```sh
handoff=$(asmith import ~/exports/project -o ~/imports/project)
asmith launch codex "$handoff"
```

```sh
asmith merge . -o ~/imports/current-history
asmith merge ~/code/api ~/code/web 10b72094 \
  --cwd ~/code/workspace -o ~/imports/combined
```

`asmith launch AGENT HANDOFF`

- `AGENT`: `copilot`, `claude`, or `codex`.
- `HANDOFF`: prepared directory, its `HANDOFF.md`, or any standalone document.
- `--cwd PROJECT`: workspace for standalone files or an override for a prepared
  handoff. The directory must already exist; otherwise nothing is launched.
- Prepared project handoffs use their recorded project cwd. Prepared global imports
  use their prepared directory unless `import --cwd` recorded another workspace.

Launch uses the selected CLI's YOLO mode and starts a new native session; it never
fabricates private session-store records. It remains a thin generic launcher:
import/merge ingestion, review, and snapshot policies come from the generated
`HANDOFF.md`, not from `launch`. For an arbitrary document, `launch` adds only the
instruction to read and follow that file. Its sole prepared-manifest behavior is
selecting `launch_cwd`.

`asmith snapshot PATH… -o RECEIPT`

- Creates a new durable receipt containing the exact pre-change state of every
  selected file, directory, or not-yet-existing path.
- Targets must be exact and non-overlapping. Broad targets such as `/` or the whole
  home directory are refused.
- Keep the receipt outside every tracked path. For global migrations, keep it outside
  the destination agent home and disposable export/import directories, normally under
  `~/.local/state/agentsmith/receipts/`.
- `-v/--verbose` describes the snapshot on stderr; stdout remains the receipt path.

After the approved writes, seal the post-change ledger:

```sh
receipt=$(asmith snapshot ~/.codex/AGENTS.md ~/.codex/skills/imported \
  -o ~/.local/state/agentsmith/receipts/global-20260724)
# perform only the approved writes
asmith audit "$receipt" --seal
```

`asmith audit RECEIPT` then compares live state with the sealed state and exits
nonzero if anything drifted. Before sealing, it compares with the baseline.

`asmith rollback RECEIPT`

- Restores modified or deleted targets from verified baseline copies.
- Removes tracked targets that did not exist before the snapshot.
- `--dry-run` inventories the rollback without writing.
- Confirmation is required unless `-y/--yes` is supplied.

```sh
asmith rollback "$receipt" --dry-run
asmith rollback "$receipt" -y
```

Generated global-import handoffs require the destination agent to enumerate all
proposed live paths, obtain approval, snapshot before its first live write, seal
afterward, and report the complete ledger and rollback commands. If an additional
path becomes necessary, the agent must stop and obtain approval for a new snapshot.

### Manage data

`asmith redact PATTERN`

- Literal matching by default; `--regex` enables regular expressions.
- `-i/--ignore-case`: case-insensitive matching.
- `--mask CHAR`: replacement character, repeated to the matched length; default `*`.
- `--dry-run`: report only; never modify.
- `-y/--yes`: skip confirmation.
- `-v/--verbose`: list every match; `-q/--quiet`: totals/results only.
- `--show-secret`: reveal matches instead of masking terminal output.
- `--max-bytes N`: skip files larger than N; `0` means unlimited.
- `-m/--max-count N`: dry-run match limit; ignored during real redaction so a secret
  is never partially scrubbed.

`asmith rm SESSION…`

- Accepts full ids, unique id prefixes, or exact project paths.
- `--recursive`: for path targets, also select sessions in nested directories.
- `--dry-run`: inventory without deleting.
- `-y/--yes`: skip confirmation.
- `-v/--verbose`: list every touched path.
- `--aggressive`: also scrub the removed id from other sessions' transcripts/memory.

`asmith purge`

- Selects only empty session shells: no transcript and zero turns.
- `--dry-run`, `-y/--yes`, and `-v/--verbose` match `rm`.

### Shell and scripting

`asmith completion bash|zsh` prints the completion program to source from the shell.
The sourced `agentsmith.sh` also provides `asmith resume` and `asmith cd`, which must
run in the calling shell to launch interactively or change its directory:

```sh
asmith resume codex                 # newest Codex session for cwd
asmith resume claude ~/code/project # newest Claude session for DIR
```

## The agent wrappers

Sourcing the file defines all three. Bare `copilot`, `claude`, or `codex` resumes the newest
resumable session whose cwd exactly matches the current directory; with no match
it starts fresh. Any arguments pass straight through to the real CLI. They resolve
via `asmith resolve --resumable --exact -H <harness> .`. `copilot()` replaces the
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
asmith export ~/code/project --recursive -o ~/exports/project
asmith export ~/code/one ~/code/two -o ~/exports/two-projects
asmith export --global -o ~/exports/global-agent-config
asmith verify ~/exports/project
```

The destination must be new. Project/session exports are complete by default:
normalized conversation, metadata, usage, touched-file records, native artifacts,
attributable memory, and project-scoped instructions/settings/hooks/skills. Use
`--no-memory` or `--no-project-context` only when deliberately making a smaller
bundle.

Project and global scope never mix. In a multi-project export, each context is
namespaced by its recorded project root and global files appear zero times. Use
Use `export --global` once to move user-wide Claude/Codex/Copilot instructions, settings,
hooks, commands, rules, and skills. Authentication and session stores are always
excluded; settings can still contain inline secrets, so inspect before sharing.
Global bundles use visible `global/claude`, `global/copilot`, and `global/codex`
directories; the manifest separately records their hidden-home destinations.
Logically shared instructions may live once under `shared/instructions` with
multiple destination mappings. Destination adapters can consolidate them when an
agent uses a single instruction file (for example Codex's global `AGENTS.md`).

## Importing and merging

Import creates a new directory under
`$XDG_STATE_HOME/agentsmith/imports/` (normally
`~/.local/state/agentsmith/imports/`) containing `HANDOFF.md`, a provenance
manifest, and preserved source material:

```sh
asmith import ~/exports/project --cwd ~/code/project
asmith import old-claude.jsonl old-copilot.jsonl.gz -o ~/handoff
asmith import old-session-archive/
asmith merge ~/code/project -o ~/merged-handoff
asmith import ~/exports/global-agent-config
```

`import` consumes artifacts that already exist: export bundles, native dumps, and
archive directories. `merge` discovers live sessions associated with a project path
across the configured agent stores, then runs them through the same export/import
normalization pipeline. Both only prepare agent-neutral handoffs; choose the agent
later with `asmith launch AGENT HANDOFF`.

Exports are preferred. Native Claude, Codex, and Copilot JSONL dumps are a recovery
fallback; gzip transcripts and archive directories containing one or more top-level
`.jsonl`/`.jsonl.gz` transcripts are supported. Dumps can omit memory, child sessions,
usage, and sidecars, and the
importer reports those limitations. Project context remains attached to its source
project namespace. A global-schema `import` preserves the verified bundle under `source/`,
copies visible files into an editable `candidate/`, and creates `HANDOFF.md` mapping
each retained file to its user-home destination. Deleting a candidate explicitly
excludes it; it is never silently restored or installed. The handoff flags hooks,
active configuration, restrictive policy language, work/internal dependencies,
SSH/network/tool limitations, and missing cross-file references. It requires the
destination agent to preserve applicable instruction meaning with minimal disclosed
changes, deduplicate overlapping sources, and normalize paths into a self-contained
native configuration that does not reference another agent home, the export, or the
prepared import. A launched agent must present a keep/adapt/omit plan and receive
explicit approval before changing live configuration.

The destination adapter starts a **new native session** with instructions to read
the handoff. AgentSmith does not fabricate private JSONL/SQLite records. `merge`
uses the same export/import pipeline, orders live sessions chronologically, retains
their native artifacts, and leaves every original untouched.

Every handoff generated by `import` or `merge` contains the exhaustive ingestion
protocol. Before acting, the destination agent is instructed
to read the complete handoff in chunks, inventory adjacent manifests and preserved
sources, inspect every normalized conversation plus memory, project context,
instructions, skills, hooks, and configuration, consult native artifacts for gaps,
and account for every source and recovered session in a coverage ledger. It must
extract objectives, decisions, constraints, open tasks, unresolved questions, and
referenced files; anything unreadable, unsupported, duplicated, or omitted must be
named explicitly. Transcript chunks are read through normal agent tools so those
reads, the coverage ledger, and the consolidated state are recorded in the new native
session. AgentSmith does not fake alternating historical roles or write private
session-store records. This avoids reducing a large migration to a vague overview
while preserving provenance.

No handoff can recover information absent from a raw dump or defeat the destination
model's finite context perfectly. The mandatory coverage protocol provides an
auditable ingestion procedure rather than a claim of lossless model memory.

`launch` also accepts an arbitrary handoff document; it is not limited to imports:

```sh
asmith launch codex ./HANDOFF.md
asmith launch claude ./design/next-steps.md --cwd ~/code/project
```

A prepared directory and its printed `HANDOFF.md` are interchangeable launch inputs.
Prepared manifests keep all original paths in `source_cwds` and one execution path in
`launch_cwd`. `launch --cwd` always overrides it. If the recorded `launch_cwd` has
disappeared since preparation, `launch` warns and uses the current directory.
Standalone files use the current directory unless overridden. `launch` does not
inject an ingestion or migration policy into arbitrary documents; it only tells the
selected agent to read and follow the supplied handoff.

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

`asmith rm` takes **full ids, unique id prefixes, or exact project paths**. An id
prefix must identify exactly one session and is rejected when ambiguous. A path
selects only sessions whose cwd exactly matches it; add `--recursive` to include
nested project directories. It refuses the current live session (skips it in bulk),
confirms unless `-y`, and previews everything with `--dry-run` (`-v` to list every
path).

```sh
asmith rm 386cd898 --dry-run          # preview the shred
asmith rm 386cd898 111b6b0f -y        # shred two, no confirmation
asmith rm 10b72094 --aggressive -v    # also scrub cross-references, list everything
asmith rm ~/code/oldproject --dry-run # exact cwd only
asmith rm ~/code/oldproject --recursive --dry-run # exact cwd + nested projects
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
asmith redact 'example-leaked-value' --dry-run       # find everywhere (read-only)
asmith redact 'example-leaked-value' --dry-run -v    # ...and list every match (masked)
asmith redact 'example-leaked-value' --dry-run -m 1  # quick check; stop at the first
asmith redact 'example-leaked-value'                 # confirm, then overwrite all
asmith redact 'sk-[a-z0-9]+' --regex      # regex mode (default is literal — safer)
asmith redact 'example-leaked-value' --mask '-'      # use a different mask character
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
