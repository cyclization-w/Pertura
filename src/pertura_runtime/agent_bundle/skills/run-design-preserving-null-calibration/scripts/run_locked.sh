#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: run_locked.sh CONFIG.json" >&2
  exit 2
fi

config=$1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -f "$config" ]; then
  echo "configuration does not exist: $config" >&2
  exit 2
fi

: "${PERTURA_EDGER_ENV:?PERTURA_EDGER_ENV is required}"
executable="$PERTURA_EDGER_ENV/bin/Rscript"
if [ ! -x "$executable" ]; then
  echo "locked executable is unavailable: $executable" >&2
  exit 1
fi

exec "$executable" "$script_dir/run_paired_label_null.R" "$config"
