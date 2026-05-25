#!/bin/bash
# Injects secrets via wsl-op-run and runs any script in this project.
# Usage:
#   ./run.sh ./exporter/export.py --budget <id>
#   ./run.sh ./importer/import.py --data data/brl audit
DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
. "$DIR/.venv/bin/activate"
exec wsl-op-run -e "$DIR/.env.template" "$@"
