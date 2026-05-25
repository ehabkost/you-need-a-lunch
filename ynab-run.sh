#!/bin/bash
# Wrapper that injects YNAB secrets via wsl-op-run and runs any script in this project.
# Usage:
#   ./ynab-run.sh python test_ynab.py
#   ./ynab-run.sh python exporter/export.py --budget <id>
DIR="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
. "$DIR/.venv/bin/activate"
exec wsl-op-run -e "$DIR/.env.template" "$@"
