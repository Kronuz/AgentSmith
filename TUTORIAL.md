# AgentSmith Tutorial

A hands-on tour of `asmith`, the swiss-army knife for your Copilot CLI, Claude
Code, and Codex CLI sessions. Every example is a real command; output is trimmed
for brevity.

If you haven't yet, add this to your shell rc and open a new shell:

```sh
[ -r "$HOME/code/AgentSmith/agentsmith.sh" ] && . "$HOME/code/AgentSmith/agentsmith.sh"
```

That gives you `asmith`, the `copilot()` / `claude()` / `codex()` auto-resume
wrappers, and tab completion. Rows are tagged `co`, `cl`, or `cx`.

Choose the workflow that matches what you have:

| You have… | Prepare with… | Then… |
| --- | --- | --- |
| Live sessions for one or more projects | `asmith merge TARGET… -o PREPARED` | Review and `asmith launch AGENT HANDOFF` |
| Export bundles or old native dumps | `asmith import SOURCE… -o PREPARED` | Review and `asmith launch AGENT HANDOFF` |
| One session to read or extract | `asmith dump SESSION` | Pipe, render, or use `--raw` |
| Sessions to move between machines | `asmith export TARGET… -o BUNDLE` | Copy, `verify`, then `import` |
| Global instructions/configuration | `asmith export --global -o BUNDLE` | `import`, audit `candidate/`, then `launch` |
| Any standalone plan or handoff document | Nothing | `asmith launch AGENT FILE --cwd PROJECT` |

The transfer pipeline is intentionally agent-neutral until the final step:

```text
live sessions ── merge ──┐
                         ├── PREPARED/HANDOFF.md ── launch AGENT
bundles/dumps ─ import ──┘
```

---

## 1. The five you'll use daily

```console
$ asmith ls -n 15          # what was I just working on, across all agents?
$ asmith ls --here         # sessions for the current directory
$ asmith tree              # everything grouped by directory
$ asmith resume codex      # reopen the newest Codex session for this dir
$ asmith dump <id>         # read a whole conversation
```

Anywhere you see `<id>` you can pass a full id or a **unique id prefix**
(`413fc324`). A prefix is not a wildcard: AgentSmith refuses it if more than one
session matches. Commands that document a path also accept `.`, `~/code/foo`, and
other filesystem paths.

---

## 2. Finding and resuming

List, filter, and locate sessions:

```console
$ asmith ls -n 5
* copilot  e2ae342a   36m      1 turns  Add Function Definition Comments
* copilot  413fc324   44m      6 turns  Build Copilot Session Toolset
* claude   10b72094   21d    219 turns  Enhance public blog projects pages
  ...
$ asmith ls --here                 # only this directory
$ asmith ls -g kronuz              # name/cwd contains "kronuz"
$ asmith ls -H claude              # just Claude sessions
$ asmith ls -n 20                  # cap the count (default is all)
$ asmith dirs                      # every directory that has sessions
```

`asmith list` shows **all** sessions by default (all agents, newest first). Want them
grouped by directory instead of a flat list? Use `asmith tree`:

```console
$ asmith tree                      # path → its sessions, one-liner each
/Users/gmendezb/code/KronuzBlog
  * co ee22a500    4h    15 turns  Set Up KronuzBlog
  * cl 10b72094   21d   219 turns  Enhance public blog projects pages
/Users/gmendezb/code/Copilot
  * co 413fc324    1h    15 turns  Build Copilot Session Toolset
  ...
$ asmith tree --by agent           # group by agent first, then directory
$ asmith tree -H claude            # just one agent
```

The `*` marks a **resumable** session (Copilot keeps some "archived" sessions in
its DB with no transcript on disk; those show `.` and **cannot** be reopened — by
short hash or full id). Claude sessions are always resumable.

Reopen one:

```console
$ asmith resume copilot            # newest Copilot session for the current dir
asmith: resuming copilot session 413fc324
$ asmith resume claude ~/code/other # newest Claude session for another directory
$ asmith resume codex .             # explicit current-directory spelling
```

The hash you see in listings (`ee22a500`) is just a short form of the full UUID;
session-oriented inspection commands take it directly. `asmith resume` deliberately
selects by `AGENT [DIR]`; use the native agent CLI to reopen a specific id. Need the
full id? `asmith resolve ee22a500` prints it. Trying to resume a directory whose
matching session is marked `.` gives a clear "not resumable" error.

Or just type the agent's name — the wrappers auto-resume the current directory's
newest session, and pass through untouched when you give arguments:

```console
$ copilot                      # resumes newest Copilot session here (or starts fresh)
$ claude                       # same, for Claude
$ codex                        # same, for Codex (full bypass/YOLO mode)
$ copilot -p "quick question"  # arguments? straight through to the real CLI
```

Script-friendly lookup:

```console
$ asmith resolve --resumable --exact .           # print newest full id for cwd
413fc324-d31f-4b69-8519-e92c1d917278
$ asmith find --resumable --exact .              # list all resumable sessions for cwd
```

---

## 3. Reading a conversation

`asmith dump` renders a session as a chat — colored in your terminal, plain when piped:

```console
$ asmith dump 038f5820                 # user + assistant text, tool calls one-lined
$ asmith dump 038f5820 -t              # + tool arguments and (truncated) results
$ asmith dump 038f5820 -R              # + assistant reasoning/thinking
$ asmith dump 038f5820 --user-only     # just what you asked
$ asmith dump 038f5820 --no-subagents  # hide subagent (task) turns
$ asmith dump 038f5820 --md > chat.md  # Markdown (view with glow / bat / VS Code)
$ asmith dump 038f5820 --color -o chat.ansi   # keep ANSI in the file; then `cat chat.ansi`
$ asmith dump 038f5820 --raw -o raw.jsonl      # one transcript, byte-for-byte
$ asmith export . -o ~/exports/project-sessions # every session for this cwd
```

**Subagents** (spawned by the `task` tool) are shown nested and labeled under a
`┌── subagent: … ──` boundary. Copilot keeps them inline; Claude keeps them in
separate files that `asmith dump` merges in — a Claude session can have dozens, so
`--no-subagents` is handy there.

Note the default view is a cleaned reconstruction (system reminders stripped, tool
results truncated). Use `--raw` when you want the exact underlying file.

`dump --raw` is useful for one exact native transcript. For portable, complete
archives and cross-agent continuation, use the export/import workflow next.

---

## 4. Moving and continuing work

AgentSmith has two deliberately separate scopes:

- **Project/session bundles** contain conversations, native session artifacts,
  memory, and project-scoped agent context.
- **Global bundles** contain user-wide instructions, skills, hooks, and settings.

Both are checksummed exports. Import never fabricates records in an agent's private
database: it prepares a visible `HANDOFF.md`, and a destination agent creates a new
native session or critically merges configuration.

The nouns matter:

- `TARGET` is a live session id, unique id prefix, or project path selected by
  `export`/`merge`. Path selection is exact unless `--recursive` is explicit.
- `BUNDLE` is a checksummed, immutable AgentSmith export.
- `SOURCE` is an existing bundle, dump, compressed dump, or archive consumed by
  `import`.
- `PREPARED` is the reviewable directory produced by `import` or `merge`.
- `HANDOFF` is either that prepared directory, its `HANDOFF.md`, or any standalone
  document accepted by `launch`.

### Export one session

Use any full id or unique prefix:

```console
$ asmith export 10b72094 -o ~/exports/one-session
/Users/me/exports/one-session
$ asmith verify ~/exports/one-session
verified 1 session(s), 8 checksummed file(s)
```

The destination must not already exist. A project/session export includes everything
portable by default:

- normalized conversation and metadata;
- original native transcript and session-owned sidecars;
- usage and observed file-touch records;
- attributable project memory;
- project-scoped instructions, settings, hooks, and skills.

Use `--no-memory` or `--no-project-context` only when deliberately excluding those
parts.

### Export a directory or several projects

A path exports every session whose working directory exactly matches it:

```console
$ asmith export -o ~/exports/current-project
$ asmith export . -o ~/exports/current-project
$ asmith export ~/code/project -o ~/exports/project
```

With no target, `export` defaults to the current directory (`.`), just like
`asmith resume` and other path-aware commands.

Include sessions whose cwd is below the selected directory with `--recursive`:

```console
$ asmith export ~/code/monorepo --recursive -o ~/exports/monorepo
```

Multiple ids and directories can share one bundle. Sessions are deduplicated, and
each project's context remains in its own namespace:

```console
$ asmith export ~/code/one ~/code/two 10b72094 -o ~/exports/selected-work
```

Copy the resulting directory to the other machine, then verify it before import:

```console
$ asmith verify ~/exports/selected-work
```

### Import project work

Prepare a continuation without launching anything:

```console
$ asmith import ~/exports/project \
    --cwd ~/code/project \
    -o ~/imports/project-handoff
```

The prepared directory contains `HANDOFF.md`, `manifest.json`, and preserved sources.
Inspect it, then launch the exact prepared import:

```console
$ asmith launch codex ~/imports/project-handoff/HANDOFF.md
```

The adapter launches a fresh native Codex, Claude, or Copilot session in YOLO mode
and tells it to reconcile the handoff with the current working tree. Historical file
state may differ from disk, so the agent is explicitly told to inspect rather than
blindly replay old tool calls.

Imports can combine several bundles:

```console
$ asmith import ~/exports/phase-one ~/exports/phase-two \
    --cwd ~/code/project -o ~/imports/combined
```

Old native dumps are accepted as a recovery fallback:

```console
$ asmith import old-claude.jsonl --cwd ~/code/project
$ asmith import old-events.jsonl.gz --from copilot
$ asmith import old-session-archive/
```

AgentSmith auto-detects normal Claude/Codex/Copilot JSONL. `--from` resolves an
ambiguous dump. A directory source normalizes every recognizable top-level
`.jsonl`/`.jsonl.gz` transcript and preserves the entire directory, including companion
files. Raw recovery cannot restore memory, child sessions, or sidecars that were never
saved.

To combine all live sessions for a directory into one continuation:

```console
$ asmith merge ~/code/project -o ~/imports/merged
$ asmith launch codex ~/imports/merged/HANDOFF.md
```

`import` consumes artifacts you already have: AgentSmith bundles, native dumps, or
archive directories. `merge` starts from a live project path, discovers every current
Claude/Codex/Copilot session associated with it, and feeds a temporary export through
the same normalization pipeline. It leaves every original session untouched.

`merge` also accepts several live targets, including individual sessions:

```console
$ asmith merge ~/code/api ~/code/frontend 10b72094 \
    --cwd ~/code/workspace -o ~/imports/combined-live-work
```

Sessions selected more than once are deduplicated. If selected sessions span several
working directories and `--cwd` is omitted, the handoff defaults to your current
directory and prints a warning. `merge --cwd` does not launch anything: it records
the workspace that a later `launch` should use.

### Launch any handoff

The exact `HANDOFF.md` printed by `import` or `merge` is directly launchable:

```console
$ asmith launch codex ~/imports/combined/HANDOFF.md
$ asmith launch claude ~/imports/combined
```

`launch` is also useful without import/export:

```console
$ asmith launch copilot ./NEXT_STEPS.md --cwd ~/code/project
```

Prepared continuations carry their workspace in `manifest.json`; `--cwd` overrides
it. A standalone file uses the current directory unless `--cwd` is supplied. The
selected directory must already exist or AgentSmith refuses to launch.

### Exhaustive ingestion in generated handoffs

Every handoff generated by `import` or `merge` contains the exhaustive ingestion
protocol:

```console
$ asmith launch codex ~/imports/combined/HANDOFF.md
```

The agent is instructed to read the handoff in chunks, inventory its manifest and
preserved sources, cover every recovered session, and inspect memory, project context,
instructions, skills, hooks, and configuration. Before changing anything it must
present a coverage ledger and consolidated state: objectives, decisions, constraints,
open tasks, unresolved questions, and referenced files. It must explicitly report
anything unreadable, unsupported, duplicated, or deliberately omitted. It reads
transcript chunks through normal tools so the inspected material and its resulting
ledger become durable turns in the new native session. It does not fabricate old
user/assistant roles or write private JSONL/SQLite session records. This is mandatory:
there is no lightweight mode that may silently skip preserved sources.

Exhaustive ingestion still cannot recover data that was never exported or guarantee
perfect retention beyond an agent's finite context; its value is traceable coverage
rather than a vague, unaudited summary.

`launch` itself remains generic. For a standalone `NEXT_STEPS.md`, it adds no migration
or ingestion policy; it only tells the selected agent to read and follow that file.

### Export global agent configuration

Global instructions/configuration have a separate lifecycle so they are not copied
into every project bundle:

```console
$ asmith export --global -o ~/exports/global-agents
$ asmith verify ~/exports/global-agents
```

Select particular agent homes explicitly when desired:

```console
$ asmith export ~/.codex -o ~/exports/codex-global
$ asmith export ~/.claude ~/.copilot -o ~/exports/claude-copilot-global
```

If the current directory itself is an agent home, targetless export infers that
global scope:

```console
$ cd ~/.codex
$ asmith export -o ~/exports/codex-global
```

The global export is directly browsable:

```text
global-agents/
├── README.md
├── global/
│   ├── claude/
│   ├── copilot/
│   └── codex/
├── shared/
│   └── instructions/
└── manifest.json
```

Agent-specific configuration stays under its agent. Logically shared instructions
are stored once under `shared/instructions/` with destination mappings. When an
agent needs a different shape—for example Codex's single global `AGENTS.md`—the
bundle can carry a destination adapter.

Authentication/session stores are excluded. Settings may still contain inline
secrets, so review the export before sharing it.

### Import and audit globals

First prepare an editable review directory:

```console
$ asmith import ~/exports/global-agents -o ~/imports/global-review
```

`import` recognizes the global manifest schema automatically. The export itself does
not contain `candidate/`; import creates it after verifying the bundle:

```text
global-review/
├── HANDOFF.md
├── candidate/       # visible files you may edit or delete
│   ├── shared/
│   ├── claude/
│   ├── copilot/
│   └── adapters/
├── source/          # untouched verified export
└── manifest.json
```

Browse `candidate/` and delete anything you do not want. Candidate deletion means
explicit exclusion; the agent is instructed never to restore it from `source/`.
Edit a candidate when a policy is useful but needs adaptation.

After your manual review, launch an agent against that exact prepared directory:

```console
$ asmith launch codex ~/imports/global-review/HANDOFF.md
```

The prepared directory is the launch cwd by default, so the new session belongs to
the review workspace rather than `~`. Pass `--cwd DIR` to `import` to record another
workspace, or to `launch` for a final override.

The global handoff does not tell the agent to install everything. It requires the
agent to:

- inventory only files still present under `candidate/`;
- compare them with live configuration and avoid blind overwrites;
- preserve applicable instruction meaning verbatim, making only necessary and
  disclosed changes;
- deduplicate overlapping instructions and normalize paths into the destination
  agent's native layout;
- make the installed result self-contained, with no references to another agent
  home, the export bundle, `source/`, or `candidate/`;
- flag hooks, active configuration, restrictive policies, and corporate/internal
  or machine-specific assumptions;
- call out restrictions involving SSH, networking, tools, filesystem access,
  external services, or agent autonomy;
- detect references to deleted/missing candidates and propose adapting, removing,
  or restoring the dependency;
- present a **keep / adapt / omit** plan and receive explicit approval before
  writing live configuration.
- enumerate every exact live path it may change and create a durable pre-change
  receipt outside both the agent home and disposable migration directories;
- seal that receipt after the writes and report the complete ledger plus audit and
  rollback commands.

The reversible sequence generated into the handoff is:

```sh
asmith snapshot PATH... -o ~/.local/state/agentsmith/receipts/MIGRATION
# approved writes happen here
asmith audit ~/.local/state/agentsmith/receipts/MIGRATION --seal
asmith rollback ~/.local/state/agentsmith/receipts/MIGRATION --dry-run
asmith rollback ~/.local/state/agentsmith/receipts/MIGRATION -y
```

`snapshot` captures existing content and records paths that do not exist yet.
Rollback restores the former content and removes newly created tracked paths. The
whole home directory is intentionally refused: exact targets keep rollback bounded
and auditable.

After validating the live result, the export bundle and prepared import may both be
deleted; neither is a runtime dependency.

This is an instruction/approval gate, not a replacement for your manual candidate
review. The untouched `source/` remains available for provenance and recovery.

---

## 5. Searching

Two tools, different jobs:

```console
$ asmith search lazy imports           # fast index search (Copilot FTS + Claude scan)
co 45f8f86b  turn  ProductivityAgents
   …py-spy, memray, __slots__, [lazy] [imports], asyncio/GIL…
$ asmith grep "GLAMOUR_STYLE" 038f5820 # regex over one session's full transcript
cl 038f5820 u  "integrations/glow/init.zsh" adds env GLAMOUR_STYLE …
$ asmith grep "TODO|FIXME"             # regex across every resumable transcript
```

`search` is for "which session talked about X?"; `grep` is for "show me the exact
lines," optionally scoped to one session.

---

## 6. Understanding a session

```console
$ asmith show 10b72094
10b72094  (10b72094-…)  [claude]
  summary     Enhance public blog projects pages
  cwd         /Users/gmendezb/Development/KronuzBlog
  branch      main
  turns       219
  files       260
  tokens      ↑1,740,340 ↓20,749,376
  resumable   yes
  resume      claude --resume 10b72094-…

$ asmith files 10b72094                # every file the session touched
$ asmith checkpoints 0feccbe6 -v       # Copilot checkpoints + next steps
$ asmith usage 413fc324                # per-model tokens (in/out, cache r/w, reasoning) + AIU
$ asmith usage                         # leaderboard by estimated wtc (+ cache-hit %)
$ asmith stats                         # per-harness totals
```

AIU (Copilot's billing unit) shows where available. Backends normalize fresh and
cached input into disjoint counts. The leaderboard's estimated **wtc** is
`fresh-input + output + cache-write + 0.1×cache-read`: useful for rough ordering,
not a currency cost, because model prices and output/cache-write multipliers vary.
Multi-model sessions show the dominant model (`opus-4.8 +1 more`).

---

## 7. Housekeeping and shredding

Delete a session with **no vestiges** anywhere — DB rows, transcript, per-session
dirs, and id-bearing lines in shared logs/history:

```console
$ asmith rm 386cd898 --dry-run         # preview exactly what would go
shred co 386cd898  Reply with exactly: STDIN=…
   deleted:  1 file(s)/dir(s)
   scrubbed: 11 line(s) from 1 file(s)
(dry run — nothing removed; 1 would be shredded)

$ asmith rm 386cd898 111b6b0f -y       # shred two, skip the confirm
$ asmith rm 10b72094 -v                # list every path touched
$ asmith rm ~/code/oldproject          # sessions with this exact cwd
$ asmith rm ~/code/oldproject --recursive # also nested project directories
```

By default it won't edit **other** sessions' transcripts even if they mention the
id — those are listed as "still references id (left intact)". If you truly want
every textual mention gone, add `--aggressive` (it will edit other sessions'
files, and says so at the prompt):

```console
$ asmith rm 10b72094 --aggressive --dry-run
```

Guards: id prefixes must resolve uniquely; project paths are exact unless
`--recursive` is explicit; `asmith rm` refuses the session you're currently in
(skips it in bulk) and always confirms unless you pass `-y`.

**Purge the dead weight.** The store fills up with empty shells — sessions with no
transcript and 0 turns that can't be resumed or read. Clear them all at once:

```console
$ asmith purge --dry-run   # list the empties (fast)
$ asmith purge             # confirm, then shred them
```

---

## 8. Working across all agents

Everything defaults to **all** harnesses. Scope with `-H`:

```console
$ asmith ls                # all, tagged co/cl/cx
$ asmith ls -H copilot     # Copilot only
$ asmith stats -H claude   # Claude only
```

Session ids are UUIDs, so `asmith show <id>` / `asmith dump <id>` figure out which agent
owns the id automatically — you never qualify it.

---

## 9. Power tips

- **Pipe it.** Color auto-disables when output isn't a terminal, so
  `asmith dump . | less`, `asmith ls | grep KronuzBlog`, and `asmith resolve . | pbcopy`
  all Just Work. Force plain text anytime with `NO_COLOR=1`.
- **Jump into a session's files.** `asmith cd <id>` drops you into its on-disk state
  dir; `asmith path <id>` just prints it.
- **Scripting.** `asmith resolve --resumable --exact .` is the exact resolver the
  `copilot()` wrapper uses — handy in your own scripts.
- **Point it at a sandbox.** Every store path is an env override
  (`COPILOT_HOME`, `COPILOT_DB`, `COPILOT_STATE`, `CLAUDE_HOME`, `CODEX_HOME`,
  `CODEX_DB`, `CODEX_SESSIONS`, `ASMITH_CACHE`, `ASMITH_SUMMARIES`). This is how
  you test destructive commands without touching real
  data — build a fake home, run `asmith rm ... -y`, confirm zero traces.
- **Faster Python.** Set `ASMITH_PYTHON=/path/to/python3` before sourcing to pick the
  interpreter the wrapper uses.

---

## Command cheatsheet

| Want to… | Run |
| --- | --- |
| See recent work | `asmith ls -n 15` |
| List every session | `asmith ls` (path + name per row) |
| List a dir's sessions | `asmith ls --here` |
| Group sessions by dir / agent | `asmith tree` · `asmith tree --by agent` |
| Reopen an agent's last session here | `asmith resume AGENT` (or bare `copilot` / `claude` / `codex`) |
| Read a conversation | `asmith dump <id>` (`-t` tools, `-R` reasoning, `--md`, `--no-subagents`) |
| Export a session/project | `asmith export <id/PROJECT…> -o BUNDLE` |
| Export globals | `asmith export --global -o BUNDLE` (or target an agent home) |
| Prepare an import | `asmith import SOURCE -o PREPARED` |
| Combine live session/project targets | `asmith merge TARGET… -o PREPARED` |
| Launch a handoff | `asmith launch AGENT HANDOFF` |
| Snapshot configuration before changes | `asmith snapshot PATH… -o RECEIPT` |
| Seal or audit installed changes | `asmith audit RECEIPT --seal` · `asmith audit RECEIPT` |
| Undo installed changes | `asmith rollback RECEIPT --dry-run` then `asmith rollback RECEIPT -y` |
| Find which session discussed X | `asmith search <words>` |
| Grep transcripts | `asmith grep <regex> [id]` |
| Inspect a session | `asmith show <id>` · `asmith files <id>` · `asmith usage <id>` |
| Delete completely | `asmith rm <id> --dry-run` then `asmith rm <id> -y` |
| Overview | `asmith stats` · `asmith dirs` |

Full reference: `asmith --help` and `asmith <command> --help`, or `README.md`.
