#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: run_locked.sh materialize|edger CONFIG.json" >&2
  exit 2
fi

mode=$1
config=$2
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -f "$config" ]; then
  echo "configuration does not exist: $config" >&2
  exit 2
fi

case "$mode" in
  materialize)
    : "${PERTURA_PERTURBSEQ_PYTHON_ENV:?PERTURA_PERTURBSEQ_PYTHON_ENV is required}"
    executable="$PERTURA_PERTURBSEQ_PYTHON_ENV/bin/python"
    script="$script_dir/materialize_pseudobulk.py"
    ;;
  edger)
    : "${PERTURA_EDGER_ENV:?PERTURA_EDGER_ENV is required}"
    executable="$PERTURA_EDGER_ENV/bin/Rscript"
    script="$script_dir/run_edger_ql.R"
    ;;
  *)
    echo "unknown mode: $mode" >&2
    exit 2
    ;;
esac

if [ ! -x "$executable" ]; then
  echo "locked executable is unavailable: $executable" >&2
  exit 1
fi

exec "$executable" "$script" "$config"
