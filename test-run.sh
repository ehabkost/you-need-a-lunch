#!/bin/bash
DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
. "$DIR/.venv/bin/activate"
exec wsl-op-run -e "$DIR/.env.testing" "$@"
