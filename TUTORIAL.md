# Agentsmith Tutorial

A hands-on tour of `asmith`, the swiss-army knife for your Copilot CLI, Claude
Code, and Codex CLI sessions. Every example is a real command; output is trimmed
for brevity.

If you haven't yet, add this to your shell rc and open a new shell:

```sh
[ -r "$HOME/code/Agentsmith/agentsmith.sh" ] && . "$HOME/code/Agentsmith/agentsmith.sh"
```

That gives you `asmith`, the `copilot()` / `claude()` / `codex()` auto-resume
wrappers, and tab completion. Rows are tagged `co`, `cl`, or `cx`.

---

## 1. The five you'll use daily

```console
$ asmith recent            # what was I just working on, across all agents?
$ asmith ls --here         # sessions for the current directory
$ asmith tree              # everything grouped by directory
$ asmith resume            # reopen the newest resumable session for this dir
$ asmith dump <id>         # read a whole conversation
```

Anywhere you see `<id>` you can pass a full id, a **unique prefix** (`413fc324`),
or a **path** (`.`, `~/code/foo`) that resolves to the newest session there.

---

## 2. Finding and resuming

List, filter, and locate sessions:

```console
$ asmith ls -n 5
* co e2ae342a   36m    1t  Add Function Definition Comments
* co 413fc324   44m    6t  Build Copilot Session Toolset
* cl 10b72094   21d  219t  Enhance public blog projects pages
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
  * co ee22a500    4h   15t  Set Up KronuzBlog
  * cl 10b72094   21d  219t  Enhance public blog projects pages
/Users/gmendezb/code/Copilot
  * co 413fc324    1h   15t  Build Copilot Session Toolset
  ...
$ asmith tree --by agent           # group by agent first, then directory
$ asmith tree -H claude            # just one agent
```

The `*` marks a **resumable** session (Copilot keeps some "archived" sessions in
its DB with no transcript on disk; those show `.` and **cannot** be reopened — by
short hash or full id). Claude sessions are always resumable.

Reopen one:

```console
$ asmith resume                    # newest resumable session for the current dir
asmith: resuming copilot session 413fc324
$ asmith resume ~/code/other       # ...for another dir
$ asmith resume ee22a500           # ...a specific session by its short hash/prefix
$ asmith resume -H claude          # force the Claude one
```

The hash you see in listings (`ee22a500`) is just a short form of the full UUID;
`asmith resume` (and every other command) takes it directly. Need the full id?
`asmith resolve ee22a500` prints it. Trying to resume a `.`-marked session gives a
clear "not resumable" error rather than a silent failure.

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
$ asmith find --one --resumable --exact .        # print just the newest id for cwd
413fc324-d31f-4b69-8519-e92c1d917278
$ asmith find --one --with-harness --resumable . # "harness<TAB>id" for scripts
copilot	413fc324-d31f-4b69-8519-e92c1d917278
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
$ asmith dump 038f5820 --raw > raw.jsonl      # the transcript file, byte-for-byte
```

**Subagents** (spawned by the `task` tool) are shown nested and labeled under a
`┌── subagent: … ──` boundary. Copilot keeps them inline; Claude keeps them in
separate files that `asmith dump` merges in — a Claude session can have dozens, so
`--no-subagents` is handy there.

Note the default view is a cleaned reconstruction (system reminders stripped, tool
results truncated). Use `--raw` when you want the exact underlying file.

---

## 4. Searching

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

## 5. Understanding a session

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
$ asmith usage                         # leaderboard by wtc, weighted token count (+ cache-hit %)
$ asmith stats                         # per-harness totals
```

AIU (Copilot's billing unit) shows where available; Claude reports tokens only. The
leaderboard ranks by **wtc** (weighted token count = `input + output + cache-write + 0.1×cache-read`),
a cost-weighted proxy that tracks Copilot's AIU at ~0.99 and is comparable across both
harnesses. Multi-model sessions are tagged with the dominant model (`opus-4.8 +1 more`).
Run `asmith usage --help` for the full definition.

---

## 6. Housekeeping and shredding

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
$ asmith rm ~/code/oldproject          # every session under a directory (and below)
```

By default it won't edit **other** sessions' transcripts even if they mention the
id — those are listed as "still references id (left intact)". If you truly want
every textual mention gone, add `--aggressive` (it will edit other sessions'
files, and says so at the prompt):

```console
$ asmith rm 10b72094 --aggressive --dry-run
```

Guards: `asmith rm` refuses the session you're currently in (skips it in bulk) and
always confirms unless you pass `-y`.

**Purge the dead weight.** The store fills up with empty shells — sessions with no
transcript and 0 turns that can't be resumed or read. Clear them all at once:

```console
$ asmith purge --dry-run   # list the empties (fast)
$ asmith purge             # confirm, then shred them
```

---

## 7. Working across all agents

Everything defaults to **all** harnesses. Scope with `-H`:

```console
$ asmith ls                # all, tagged co/cl/cx
$ asmith ls -H copilot     # Copilot only
$ asmith stats -H claude   # Claude only
```

Session ids are UUIDs, so `asmith show <id>` / `asmith dump <id>` figure out which agent
owns the id automatically — you never qualify it.

---

## 8. Power tips

- **Pipe it.** Color auto-disables when output isn't a terminal, so
  `asmith dump . | less`, `asmith ls | grep KronuzBlog`, and `asmith find --one . | pbcopy`
  all Just Work. Force plain text anytime with `NO_COLOR=1`.
- **Jump into a session's files.** `asmith cd <id>` drops you into its on-disk state
  dir; `asmith path <id>` just prints it.
- **Scripting.** `asmith find --one --resumable --exact .` is the exact resolver the
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
| See recent work | `asmith recent` |
| List every session | `asmith ls` (path + name per row) |
| List a dir's sessions | `asmith ls --here` |
| Group sessions by dir / agent | `asmith tree` · `asmith tree --by agent` |
| Reopen the last session here | `asmith resume` (or bare `copilot` / `claude` / `codex`) |
| Read a conversation | `asmith dump <id>` (`-t` tools, `-R` reasoning, `--md`, `--no-subagents`) |
| Find which session discussed X | `asmith search <words>` |
| Grep transcripts | `asmith grep <regex> [id]` |
| Inspect a session | `asmith show <id>` · `asmith files <id>` · `asmith usage <id>` |
| Delete completely | `asmith rm <id> --dry-run` then `asmith rm <id> -y` |
| Overview | `asmith stats` · `asmith dirs` |

Full reference: `asmith --help` and `asmith <command> --help`, or `README.md`.
