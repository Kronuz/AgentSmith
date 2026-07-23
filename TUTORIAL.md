# Agentsmith Tutorial

A hands-on tour of `cw`, the swiss-army knife for your Copilot CLI and Claude Code
sessions. Every example is a real command; output is trimmed for brevity.

If you haven't yet, add this to your shell rc and open a new shell:

```sh
[ -r "$HOME/code/Agentsmith/agentsmith.sh" ] && . "$HOME/code/Agentsmith/agentsmith.sh"
```

That gives you `cw`, the `copilot()` / `claude()` auto-resume wrappers, and tab
completion. `cw` reads both `~/.copilot` (Copilot CLI) and `~/.claude` (Claude
Code); rows from the two are tagged `co` and `cl`.

---

## 1. The five you'll use daily

```console
$ cw recent            # what was I just working on, across both agents?
$ cw ls --here         # sessions for the current directory
$ cw tree              # everything grouped by directory
$ cw resume            # reopen the newest resumable session for this dir
$ cw dump <id>         # read a whole conversation
```

Anywhere you see `<id>` you can pass a full id, a **unique prefix** (`413fc324`),
or a **path** (`.`, `~/code/foo`) that resolves to the newest session there.

---

## 2. Finding and resuming

List, filter, and locate sessions:

```console
$ cw ls -n 5
* co e2ae342a   36m    1t  Add Function Definition Comments
* co 413fc324   44m    6t  Build Copilot Session Toolset
* cl 10b72094   21d  219t  Enhance public blog projects pages
  ...
$ cw ls --here                 # only this directory
$ cw ls -g kronuz              # name/cwd contains "kronuz"
$ cw ls -H claude              # just Claude sessions
$ cw ls -n 20                  # cap the count (default is all)
$ cw dirs                      # every directory that has sessions
```

`cw list` shows **all** sessions by default (both agents, newest first). Want them
grouped by directory instead of a flat list? Use `cw tree`:

```console
$ cw tree                      # path → its sessions, one-liner each
/Users/gmendezb/code/KronuzBlog
  * co ee22a500    4h   15t  Set Up KronuzBlog
  * cl 10b72094   21d  219t  Enhance public blog projects pages
/Users/gmendezb/code/Copilot
  * co 413fc324    1h   15t  Build Copilot Session Toolset
  ...
$ cw tree --by agent           # group by agent first, then directory
$ cw tree -H claude            # just one agent
```

The `*` marks a **resumable** session (Copilot keeps some "archived" sessions in
its DB with no transcript on disk; those show `.` and **cannot** be reopened — by
short hash or full id). Claude sessions are always resumable.

Reopen one:

```console
$ cw resume                    # newest resumable session for the current dir
cw: resuming copilot session 413fc324
$ cw resume ~/code/other       # ...for another dir
$ cw resume ee22a500           # ...a specific session by its short hash/prefix
$ cw resume -H claude          # force the Claude one
```

The hash you see in listings (`ee22a500`) is just a short form of the full UUID;
`cw resume` (and every other command) takes it directly. Need the full id?
`cw resolve ee22a500` prints it. Trying to resume a `.`-marked session gives a
clear "not resumable" error rather than a silent failure.

Or just type the agent's name — the wrappers auto-resume the current directory's
newest session, and pass through untouched when you give arguments:

```console
$ copilot                      # resumes newest Copilot session here (or starts fresh)
$ claude                       # same, for Claude
$ copilot -p "quick question"  # arguments? straight through to the real CLI
```

Script-friendly lookup:

```console
$ cw find --one --resumable --exact .        # print just the newest id for cwd
413fc324-d31f-4b69-8519-e92c1d917278
$ cw find --one --with-harness --resumable . # "harness<TAB>id" for scripts
copilot	413fc324-d31f-4b69-8519-e92c1d917278
```

---

## 3. Reading a conversation

`cw dump` renders a session as a chat — colored in your terminal, plain when piped:

```console
$ cw dump 038f5820                 # user + assistant text, tool calls one-lined
$ cw dump 038f5820 -t              # + tool arguments and (truncated) results
$ cw dump 038f5820 -R              # + assistant reasoning/thinking
$ cw dump 038f5820 --user-only     # just what you asked
$ cw dump 038f5820 --no-subagents  # hide subagent (task) turns
$ cw dump 038f5820 --md > chat.md  # Markdown (view with glow / bat / VS Code)
$ cw dump 038f5820 --color -o chat.ansi   # keep ANSI in the file; then `cat chat.ansi`
$ cw dump 038f5820 --raw > raw.jsonl      # the transcript file, byte-for-byte
```

**Subagents** (spawned by the `task` tool) are shown nested and labeled under a
`┌── subagent: … ──` boundary. Copilot keeps them inline; Claude keeps them in
separate files that `cw dump` merges in — a Claude session can have dozens, so
`--no-subagents` is handy there.

Note the default view is a cleaned reconstruction (system reminders stripped, tool
results truncated). Use `--raw` when you want the exact underlying file.

---

## 4. Searching

Two tools, different jobs:

```console
$ cw search lazy imports           # fast index search (Copilot FTS + Claude scan)
co 45f8f86b  turn  ProductivityAgents
   …py-spy, memray, __slots__, [lazy] [imports], asyncio/GIL…
$ cw grep "GLAMOUR_STYLE" 038f5820 # regex over one session's full transcript
cl 038f5820 u  "integrations/glow/init.zsh" adds env GLAMOUR_STYLE …
$ cw grep "TODO|FIXME"             # regex across every resumable transcript
```

`search` is for "which session talked about X?"; `grep` is for "show me the exact
lines," optionally scoped to one session.

---

## 5. Understanding a session

```console
$ cw show 10b72094
10b72094  (10b72094-…)  [claude]
  summary     Enhance public blog projects pages
  cwd         /Users/gmendezb/Development/KronuzBlog
  branch      main
  turns       219
  files       260
  tokens      ↑1,740,340 ↓20,749,376
  resumable   yes
  resume      claude --resume 10b72094-…

$ cw files 10b72094                # every file the session touched
$ cw checkpoints 0feccbe6 -v       # Copilot checkpoints + next steps
$ cw usage 413fc324                # tokens + AIU for one session, by model
$ cw usage                         # leaderboard: biggest sessions by output
$ cw stats                         # per-harness totals
```

AIU (Copilot's billing unit) shows where available; Claude reports tokens only.

---

## 6. Housekeeping and shredding

Delete a session with **no vestiges** anywhere — DB rows, transcript, per-session
dirs, and id-bearing lines in shared logs/history:

```console
$ cw rm 386cd898 --dry-run         # preview exactly what would go
shred co 386cd898  Reply with exactly: STDIN=…
   deleted:  1 file(s)/dir(s)
   scrubbed: 11 line(s) from 1 file(s)
(dry run — nothing removed; 1 would be shredded)

$ cw rm 386cd898 111b6b0f -y       # shred two, skip the confirm
$ cw rm 10b72094 -v                # list every path touched
$ cw rm ~/code/oldproject          # every session under a directory (and below)
```

By default it won't edit **other** sessions' transcripts even if they mention the
id — those are listed as "still references id (left intact)". If you truly want
every textual mention gone, add `--aggressive` (it will edit other sessions'
files, and says so at the prompt):

```console
$ cw rm 10b72094 --aggressive --dry-run
```

Guards: `cw rm` refuses the session you're currently in (skips it in bulk) and
always confirms unless you pass `-y`.

**Purge the dead weight.** The store fills up with empty shells — sessions with no
transcript and 0 turns that can't be resumed or read. Clear them all at once:

```console
$ cw purge --dry-run   # list the empties (fast)
$ cw purge             # confirm, then shred them
```

---

## 7. Working across both agents

Everything defaults to **both** harnesses. Scope with `-H`:

```console
$ cw ls                # both, tagged co/cl
$ cw ls -H copilot     # Copilot only
$ cw stats -H claude   # Claude only
```

Session ids are UUIDs, so `cw show <id>` / `cw dump <id>` figure out which agent
owns the id automatically — you never qualify it.

---

## 8. Power tips

- **Pipe it.** Color auto-disables when output isn't a terminal, so
  `cw dump . | less`, `cw ls | grep KronuzBlog`, and `cw find --one . | pbcopy`
  all Just Work. Force plain text anytime with `NO_COLOR=1`.
- **Jump into a session's files.** `cw cd <id>` drops you into its on-disk state
  dir; `cw path <id>` just prints it.
- **Scripting.** `cw find --one --resumable --exact .` is the exact resolver the
  `copilot()` wrapper uses — handy in your own scripts.
- **Point it at a sandbox.** Every store path is an env override
  (`COPILOT_HOME`, `COPILOT_DB`, `COPILOT_STATE`, `CLAUDE_HOME`, `CW_CACHE`,
  `CW_SUMMARIES`). This is how you test destructive commands without touching real
  data — build a fake home, run `cw rm ... -y`, confirm zero traces.
- **Faster Python.** Set `CW_PYTHON=/path/to/python3` before sourcing to pick the
  interpreter the wrapper uses.

---

## Command cheatsheet

| Want to… | Run |
| --- | --- |
| See recent work | `cw recent` |
| List every session | `cw ls` (path + name per row) |
| List a dir's sessions | `cw ls --here` |
| Group sessions by dir / agent | `cw tree` · `cw tree --by agent` |
| Reopen the last session here | `cw resume` (or just `copilot` / `claude`) |
| Read a conversation | `cw dump <id>` (`-t` tools, `-R` reasoning, `--md`, `--no-subagents`) |
| Find which session discussed X | `cw search <words>` |
| Grep transcripts | `cw grep <regex> [id]` |
| Inspect a session | `cw show <id>` · `cw files <id>` · `cw usage <id>` |
| Delete completely | `cw rm <id> --dry-run` then `cw rm <id> -y` |
| Overview | `cw stats` · `cw dirs` |

Full reference: `cw --help` and `cw <command> --help`, or `README.md`.
