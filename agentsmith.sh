#!/usr/bin/env bash
# agentsmith.sh — AgentSmith: a swiss-army knife for AI coding-agent sessions.
#
# Source me from your ~/.profile / ~/.zshrc / ~/.bashrc:
#     source ~/code/AgentSmith/agentsmith.sh
#
# Provides `ascd`, copilot()/claude()/codex() auto-resume wrappers, and completion.
# It deliberately does not define `asmith`; that name always means the executable.

# Change the calling shell into a session's native state directory.
ascd() {
  local dir
  dir="$(asmith path "${1:-.}")" && builtin cd -- "$dir" || return 1
}

# Agent wrappers — a bare command resumes the newest resumable session for the
# current directory, otherwise starts fresh; any arguments pass straight through to
# the real CLI. copilot() replaces the inline function that used to live in
# ~/.profile; all resolve via the toolset's exact-cwd index.
copilot() {
  if [ $# -eq 0 ]; then
    local id
    id="$(asmith resolve --resumable --exact -H copilot . 2>/dev/null)"
    command copilot ${id:+--resume="$id"} --yolo
  else
    command copilot "$@"
  fi
}

claude() {
  if [ $# -eq 0 ]; then
    local id
    id="$(asmith resolve --resumable --exact -H claude . 2>/dev/null)"
    command claude ${id:+--resume "$id"}
  else
    command claude "$@"
  fi
}

codex() {
  # Codex rejects iTerm prerelease versions such as 3.7.0beta7 when gating
  # terminal pets. Normalize the version for Codex without changing the shell.
  case "${TERM_PROGRAM:-}:${TERM_PROGRAM_VERSION:-}" in
    iTerm.app:*[!0-9.]*)
      local TERM_PROGRAM_VERSION="${TERM_PROGRAM_VERSION%%[!0-9.]*}"
      export TERM_PROGRAM_VERSION
      ;;
  esac

  if [ $# -eq 0 ]; then
    local id
    id="$(asmith resolve --resumable --exact -H codex . 2>/dev/null)"
    if [ -n "$id" ]; then
      command codex resume "$id" --dangerously-bypass-approvals-and-sandbox
    else
      command codex --dangerously-bypass-approvals-and-sandbox
    fi
  else
    command codex "$@"
  fi
}

# Tab-completion for `asmith` (bash + zsh). The candidates are computed by the CLI
# itself (`asmith __complete`, which introspects the argparse parser), so this hook is
# tiny and never drifts. No python runs until you actually press <Tab>. The same
# script is available standalone via `asmith completion bash|zsh`.
if [ -n "${ZSH_VERSION:-}" ]; then
  _asmith_complete() {
    local -a lines; local l
    if [[ ${words[2]:-} == resume ]]; then
      if (( CURRENT == 3 )); then
        compadd -- copilot claude codex
      else
        _files -/ 2>/dev/null
      fi
      return
    fi
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
      if [[ "${COMP_WORDS[1]:-}" == resume ]]; then
        if [ "$COMP_CWORD" -eq 2 ]; then
          COMPREPLY=($(compgen -W "copilot claude codex" -- "$cur"))
        else
          COMPREPLY=($(compgen -d -- "$cur"))
        fi
        return
      fi
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
