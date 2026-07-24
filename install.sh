#!/bin/sh
# Install the non-interactive AgentSmith executable. Shell integrations are sourced.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
bin_dir=${1:-"$HOME/.local/bin"}
destination=$bin_dir/asmith
source=$root/bin/asmith

mkdir -p "$bin_dir"
if [ -e "$destination" ] || [ -L "$destination" ]; then
    current=$(readlink "$destination" 2>/dev/null || true)
    if [ "$current" != "$source" ]; then
        printf 'install: refusing to replace existing path: %s\n' "$destination" >&2
        exit 1
    fi
else
    ln -s "$source" "$destination"
fi

printf '%s\n' "$destination"
printf 'Source %s/agentsmith.sh for resume, cd, wrappers, and completion.\n' "$root" >&2
