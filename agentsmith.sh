#!/usr/bin/env bash
# agentsmith.sh — Agentsmith: a swiss-army knife for AI coding-agent sessions.
#
# Source me from your ~/.profile / ~/.zshrc / ~/.bashrc:
#     source ~/code/Agentsmith/agentsmith.sh
#
# Provides the `asmith` command plus the copilot()/claude() auto-resume wrappers.
# Compatible with bash & zsh. All reporting lives in the `agentsmith` package next
# to this file; the shell wrappers exist because they must exec the CLI in your
# interactive shell.

# --- locate this file (bash + zsh) ---------------------------------------
if [ -n "${BASH_SOURCE:-}" ]; then
  _ASMITH_SELF="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  _ASMITH_SELF="${(%):-%x}"
else
  _ASMITH_SELF="$0"
fi
_ASMITH_DIR="$(cd "$(dirname "$_ASMITH_SELF")" >/dev/null 2>&1 && pwd)"
: "${ASMITH_PYTHON:=python3}"
unset _ASMITH_SELF

# Run the agentsmith package (kept importable via PYTHONPATH = its parent dir).
_asmith_py() { PYTHONPATH="$_ASMITH_DIR${PYTHONPATH:+:$PYTHONPATH}" "$ASMITH_PYTHON" -m agentsmith "$@"; }

asmith() {
  local cmd="${1:-help}"
  case "$cmd" in
    resume|r)
      shift
      # optional -H/--harness passthrough; default: resolve across all harnesses
      local hflag=""
      case "$1" in
        -H|--harness) hflag="-H $2"; shift 2 ;;
      esac
      # target may be a dir (default: cwd) OR a session id/prefix
      local target="${1:-.}"
      local out h id
      out="$(_asmith_py resolve --resumable --with-harness --exact $hflag "$target")" || return 1
      [ -z "$out" ] && return 1
      h="${out%%$'\t'*}"; id="${out#*$'\t'}"
      printf 'asmith: resuming %s session %s\n' "$h" "${id%%-*}" >&2
      case "$h" in
        copilot) command copilot --resume="$id" --yolo ;;
        claude)  command claude --resume "$id" ;;
        *) printf 'asmith: unknown harness %s\n' "$h" >&2; return 1 ;;
      esac
      ;;
    cd)
      # cd into the on-disk state dir of a session (default: newest for cwd)
      shift
      local dir
      dir="$(_asmith_py path "${1:-.}")" && cd "$dir" || return 1
      ;;
    help|-h|--help|"")
      _asmith_py --help
      cat <<'EOF'

Shell-only extras (from agentsmith.sh):
  asmith resume [-H copilot|claude] [dir]   resume newest resumable session for dir
  asmith cd [sess]                          cd into a session's on-disk state dir
EOF
      ;;
    *)
      _asmith_py "$@"
      ;;
  esac
}

# copilot() / claude() — bare command resumes the newest resumable session for the
# current directory, otherwise starts fresh; any arguments pass straight through to
# the real CLI. copilot() replaces the inline function that used to live in
# ~/.profile; both resolve via the toolset's exact-cwd index.
copilot() {
  if [ $# -eq 0 ]; then
    local id
    id="$(_asmith_py find --one --resumable --exact -H copilot . 2>/dev/null)"
    command copilot ${id:+--resume="$id"} --yolo
  else
    command copilot "$@"
  fi
}

claude() {
  if [ $# -eq 0 ]; then
    local id
    id="$(_asmith_py find --one --resumable --exact -H claude . 2>/dev/null)"
    command claude ${id:+--resume "$id"}
  else
    command claude "$@"
  fi
}

# Convenience aliases (safe no-ops if you prefer just `asmith`).
alias asls='asmith list'
alias astree='asmith tree'
alias asdirs='asmith dirs'
alias asfind='asmith find'
alias asdump='asmith dump'

# Tab-completion for `asmith` (bash + zsh). The candidates are computed by the CLI
# itself (`asmith __complete`, which introspects the argparse parser), so this hook is
# tiny and never drifts. No python runs until you actually press <Tab>. The same
# script is available standalone via `asmith completion bash|zsh`.
if [ -n "${ZSH_VERSION:-}" ]; then
  _asmith_complete() {
    local -a lines; local l
    lines=("${(@f)$(asmith __complete -- "${(@)words[2,CURRENT]}" 2>/dev/null)}")
    for l in $lines; do
      if [[ $l == __DIRS__ ]]; then _files -/ 2>/dev/null
      elif [[ -n $l ]]; then compadd -- $l; fi
    done
  }
  # Register with the completion system. When agentsmith.sh is sourced from
  # ~/.zprofile / ~/.profile (login), `compinit` hasn't run yet, so `compdef`
  # isn't defined; retry once on the first prompt, by which point ~/.zshrc has run.
  if whence compdef >/dev/null 2>&1; then
    compdef _asmith_complete asmith
  else
    autoload -Uz add-zsh-hook 2>/dev/null
    if whence add-zsh-hook >/dev/null 2>&1; then
      _asmith_deferred_compdef() {
        whence compdef >/dev/null 2>&1 && compdef _asmith_complete asmith
        add-zsh-hook -d precmd _asmith_deferred_compdef
        unfunction _asmith_deferred_compdef 2>/dev/null
      }
      add-zsh-hook precmd _asmith_deferred_compdef
    fi
  fi
elif [ -n "${BASH_VERSION:-}" ]; then
  if command -v complete >/dev/null 2>&1; then
    _asmith_complete() {
      local cur="${COMP_WORDS[COMP_CWORD]}" out
      out="$(asmith __complete -- "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null)"
      COMPREPLY=()
      case "$out" in
        *__DIRS__*) COMPREPLY+=($(compgen -d -- "$cur")); out="${out//__DIRS__/}" ;;
      esac
      COMPREPLY+=($(compgen -W "$out" -- "$cur"))
    }
    complete -F _asmith_complete asmith
  fi
fi

true  # ensure sourcing returns success
