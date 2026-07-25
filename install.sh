#!/bin/sh
# Install or uninstall the non-interactive AgentSmith executable.
set -eu

# Keep a symlinked installation directory as the public executable target.
root=$(CDPATH='' cd -- "$(dirname "$0")" && pwd -L)
bin_dir=$HOME/.local/bin
bin_dir_set=
action=install
dry_run=
force=

usage() {
    cat <<EOF
Usage: $0 [OPTIONS] [BIN_DIR]

Install the asmith executable into ~/.local/bin by default.

  -n, --dry-run       show what would change
  -f, --force         replace a conflicting symlink, never a regular file
  -u, --uninstall     remove the symlink when AgentSmith owns it
      --bin-dir DIR   install into DIR instead of ~/.local/bin
  -h, --help          show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case $1 in
        -n|--dry-run) dry_run=1 ;;
        -f|--force) force=1 ;;
        -u|--uninstall) action=uninstall ;;
        --bin-dir)
            [ "$#" -gt 1 ] || {
                printf 'install: --bin-dir requires a directory\n' >&2
                exit 2
            }
            shift
            bin_dir=$1
            bin_dir_set=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            printf 'install: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [ -n "$bin_dir_set" ]; then
                printf 'install: only one BIN_DIR may be specified\n' >&2
                exit 2
            fi
            bin_dir=$1
            bin_dir_set=1
            ;;
    esac
    shift
done

destination=$bin_dir/asmith
source=$root/bin/asmith
current=$(readlink "$destination" 2>/dev/null || true)

if [ "$action" = uninstall ]; then
    if [ "$current" = "$source" ]; then
        if [ -n "$dry_run" ]; then
            printf 'would remove %s\n' "$destination"
        else
            rm "$destination"
            printf 'removed %s\n' "$destination"
        fi
    elif [ -e "$destination" ] || [ -L "$destination" ]; then
        printf 'install: refusing to remove path not owned by AgentSmith: %s\n' \
            "$destination" >&2
        exit 1
    else
        printf 'already absent %s\n' "$destination"
    fi
    exit 0
fi

if [ "$current" = "$source" ]; then
    printf 'already linked %s\n' "$destination"
elif [ -L "$destination" ]; then
    if [ -z "$force" ]; then
        printf 'install: refusing to replace existing symlink: %s\n' "$destination" >&2
        printf 'install: rerun with --force after reviewing its target: %s\n' \
            "$current" >&2
        exit 1
    elif [ -n "$dry_run" ]; then
        printf 'would replace %s -> %s\n' "$destination" "$current"
        printf 'with          %s -> %s\n' "$destination" "$source"
    else
        rm "$destination"
        ln -s "$source" "$destination"
        printf 'replaced %s\n' "$destination"
    fi
elif [ -e "$destination" ]; then
    printf 'install: refusing to replace existing non-symlink: %s\n' "$destination" >&2
    exit 1
elif [ -n "$dry_run" ]; then
    printf 'would link %s -> %s\n' "$destination" "$source"
else
    mkdir -p "$bin_dir"
    ln -s "$source" "$destination"
    printf 'linked %s\n' "$destination"
fi

printf 'Source %s/agentsmith.sh for resume, cd, wrappers, and completion.\n' "$root" >&2
