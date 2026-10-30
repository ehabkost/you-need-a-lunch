#!/bin/bash
# Runs a command with the secrets referenced (op://...) in the env file injected via 1Password's
# `op run`. Without 1Password, any loader that exports the same env vars works instead.
DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
. "$DIR/.venv/bin/activate"
exec op run --env-file="$DIR/.env.testing" -- "$@"
