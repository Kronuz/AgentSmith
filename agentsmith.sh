#!/usr/bin/env bash
# agentsmith.sh — Agentsmith: a swiss-army knife for AI coding-agent sessions.
#
# Source me from your ~/.profile / ~/.zshrc / ~/.bashrc:
#     source ~/code/Agentsmith/agentsmith.sh
#
# Provides the `cw` command plus the copilot()/claude() auto-resume wrappers.
# Compatible with bash & zsh. All reporting lives in the `agentsmith` package next
# to this file; the shell wrappers exist because they must exec the CLI in your
# interactive shell.

# --- locate this file (bash + zsh) ---------------------------------------
if [ -n "${BASH_SOURCE:-}" ]; then
  _CW_SELF="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  _CW_SELF="${(%):-%x}"
else
  _CW_SELF="$0"
fi
_CW_DIR="$(cd "$(dirname "$_CW_SELF")" >/dev/null 2>&1 && pwd)"
: "${CW_PYTHON:=python3}"
unset _CW_SELF

# Run the agentsmith package (kept importable via PYTHONPATH = its parent dir).
_cw_py() { PYTHONPATH="$_CW_DIR${PYTHONPATH:+:$PYTHONPATH}" "$CW_PYTHON" -m agentsmith "$@"; }

cw() {
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
      out="$(_cw_py resolve --resumable --with-harness --exact $hflag "$target")" || return 1
      [ -z "$out" ] && return 1
      h="${out%%$'\t'*}"; id="${out#*$'\t'}"
      printf 'cw: resuming %s session %s\n' "$h" "${id%%-*}" >&2
      case "$h" in
        copilot) command copilot --resume="$id" --yolo ;;
        claude)  command claude --resume "$id" ;;
        *) printf 'cw: unknown harness %s\n' "$h" >&2; return 1 ;;
      esac
      ;;
    cd)
      # cd into the on-disk state dir of a session (default: newest for cwd)
      shift
      local dir
      dir="$(_cw_py path "${1:-.}")" && cd "$dir" || return 1
      ;;
    help|-h|--help|"")
      _cw_py --help
      cat <<'EOF'

Shell-only extras (from agentsmith.sh):
  cw resume [-H copilot|claude] [dir]   resume newest resumable session for dir
  cw cd [sess]                          cd into a session's on-disk state dir
EOF
      ;;
    *)
      _cw_py "$@"
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
    id="$(_cw_py find --one --resumable --exact -H copilot . 2>/dev/null)"
    command copilot ${id:+--resume="$id"} --yolo
  else
    command copilot "$@"
  fi
}

claude() {
  if [ $# -eq 0 ]; then
    local id
    id="$(_cw_py find --one --resumable --exact -H claude . 2>/dev/null)"
    command claude ${id:+--resume "$id"}
  else
    command claude "$@"
  fi
}

# Convenience aliases (safe no-ops if you prefer just `cw`).
alias cwls='cw list'
alias cwtree='cw tree'
alias cwdirs='cw dirs'
alias cwfind='cw find'
alias cwdump='cw dump'

# Tab-completion for `cw` (bash + zsh). The candidates are computed by the CLI
# itself (`cw __complete`, which introspects the argparse parser), so this hook is
# tiny and never drifts. No python runs until you actually press <Tab>. The same
# script is available standalone via `cw completion bash|zsh`.
if [ -n "${ZSH_VERSION:-}" ]; then
  _cw_complete() {
    local -a lines; local l
    lines=("${(@f)$(cw __complete -- "${(@)words[2,CURRENT]}" 2>/dev/null)}")
    for l in $lines; do
      if [[ $l == __DIRS__ ]]; then _files -/ 2>/dev/null
      elif [[ -n $l ]]; then compadd -- $l; fi
    done
  }
  # Register with the completion system. When agentsmith.sh is sourced from
  # ~/.zprofile / ~/.profile (login), `compinit` hasn't run yet, so `compdef`
  # isn't defined; retry once on the first prompt, by which point ~/.zshrc has run.
  if whence compdef >/dev/null 2>&1; then
    compdef _cw_complete cw
  else
    autoload -Uz add-zsh-hook 2>/dev/null
    if whence add-zsh-hook >/dev/null 2>&1; then
      _cw_deferred_compdef() {
        whence compdef >/dev/null 2>&1 && compdef _cw_complete cw
        add-zsh-hook -d precmd _cw_deferred_compdef
        unfunction _cw_deferred_compdef 2>/dev/null
      }
      add-zsh-hook precmd _cw_deferred_compdef
    fi
  fi
elif [ -n "${BASH_VERSION:-}" ]; then
  if command -v complete >/dev/null 2>&1; then
    _cw_complete() {
      local cur="${COMP_WORDS[COMP_CWORD]}" out
      out="$(cw __complete -- "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null)"
      COMPREPLY=()
      case "$out" in
        *__DIRS__*) COMPREPLY+=($(compgen -d -- "$cur")); out="${out//__DIRS__/}" ;;
      esac
      COMPREPLY+=($(compgen -W "$out" -- "$cur"))
    }
    complete -F _cw_complete cw
  fi
fi

true  # ensure sourcing returns success
