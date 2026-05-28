#!/bin/bash
# NOTE: `wsl-op-run` is a user-specific wrapper that is NOT included in this
# repository — it shells out to the 1Password CLI from inside WSL. If you don't
# have it, replace the `wsl-op-run` invocation below with the official:
#
#     op run --env-file="$DIR/.env.production" -- "$@"
#
# (assuming your .env.production uses `op://` secret references). Or swap in
# any other env-var loader you prefer; nothing in the codebase depends on
# 1Password specifically — it just needs the env vars set.
DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
. "$DIR/.venv/bin/activate"
exec wsl-op-run -e "$DIR/.env.production" "$@"
