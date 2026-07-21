#!/usr/bin/env bash
#SBATCH --job-name=a19-finalize
#SBATCH --partition=normal
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/users/twang05/Project/PerturaBenchmark/paper-v1/logs/a19-finalize-%j.log

set -Eeuo pipefail

BENCHMARK_ROOT=${BENCHMARK_ROOT:-/scratch/users/twang05/Project/PerturaBenchmark}
PERTURA_REPO=${PERTURA_REPO:-/scratch/users/twang05/Project/Pertura-a19-minimal-rc}
BRANCH=${BRANCH:-codex/a19-minimal-rc}
PAPER_ROOT="$BENCHMARK_ROOT/paper-v1"
MANIFESTS="$PAPER_ROOT/manifests"
MAIN_ENV="$BENCHMARK_ROOT/environments/pertura-aaai-py311-a19"
CANARY="$BENCHMARK_ROOT/scripts/run-a19-canary.sbatch"
REFERENCE_INDEX=${REFERENCE_INDEX:-$MANIFESTS/reference-pack-index.a19.pb064.json}
STEP=initialization
NEW_REFS=

trap 'rc=$?; echo "FAILED step=$STEP exit=$rc"; exit $rc' ERR
cleanup() {
  if [[ -n "$NEW_REFS" && "$NEW_REFS" == "$BENCHMARK_ROOT/tmp/"* ]]; then
    rm -rf -- "$NEW_REFS"
  fi
}
trap cleanup EXIT

export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 BENCHMARK_ROOT PERTURA_REPO REFERENCE_INDEX
export PERTURA_BENCH_CACHE="$BENCHMARK_ROOT/cache"
mkdir -p "$PAPER_ROOT/logs" "$BENCHMARK_ROOT/tmp"

STEP=checkout
git -C "$PERTURA_REPO" diff --quiet
git -C "$PERTURA_REPO" diff --cached --quiet
git -C "$PERTURA_REPO" fetch origin "$BRANCH"
COMMIT=$(git -C "$PERTURA_REPO" rev-parse FETCH_HEAD)
git -C "$PERTURA_REPO" checkout --detach "$COMMIT"
export BENCHMARK_COMMIT="$COMMIT"
echo "commit=$COMMIT"

STEP=environment
set -a
source "$MANIFESTS/sherlock-environment-paths.env"
set +a
test -x "$PERTURA_EDGER_ENV/bin/Rscript"
test -f "$REFERENCE_INDEX"
"$MAIN_ENV/bin/pertura" env doctor edger-v1

STEP=generate-task-references
NEW_REFS=$(mktemp -d "$BENCHMARK_ROOT/tmp/taskrefs-$COMMIT.XXXXXX")
"$MAIN_ENV/bin/python" -u "$PERTURA_REPO/scripts/generate_paper_task_references.py" \
  --datasets "$PAPER_ROOT/protocol/datasets.json" \
  --splits "$PAPER_ROOT/protocol/splits.json" \
  --ref02-root "$PAPER_ROOT/references/REF-02" \
  --ref03-root "$PAPER_ROOT/references/REF-03" \
  --rscript "$PERTURA_EDGER_ENV/bin/Rscript" \
  --r-runner "$PERTURA_REPO/scripts/generate_paper_task_trans_de.R" \
  --output "$NEW_REFS" --max-memory-gb 8
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/validate_paper_task_references.py" "$NEW_REFS"

STEP=install-task-references
CURRENT="$PAPER_ROOT/task_references"
BACKUP="$PAPER_ROOT/task_references.backup-${COMMIT:0:7}-$SLURM_JOB_ID"
test -d "$CURRENT" && test ! -e "$BACKUP"
mv "$CURRENT" "$BACKUP"
if mv "$NEW_REFS" "$CURRENT"; then
  NEW_REFS=
else
  mv "$BACKUP" "$CURRENT"
  exit 1
fi
echo "task_reference_backup=$BACKUP"

STEP=refresh
bash "$PERTURA_REPO/scripts/refresh_sherlock_a19_checkpoint.sh" "$BRANCH"

STEP=checkpoint-verification
CHECKPOINT="$BENCHMARK_ROOT/checkpoints/$COMMIT"
"$MAIN_ENV/bin/python" - "$CHECKPOINT" "$COMMIT" <<'PY'
import json, sys
from pathlib import Path
root, commit = Path(sys.argv[1]), sys.argv[2]
names = {
    "evaluator": "evaluator-qualification.a19.json",
    "binding": "capability-binding-qualification.a19.json",
    "scientific": "scientific-method-parity.a19.json",
    "scope": "scientific-method-scope-audit.a19.json",
    "plan": "server-plan.a19.sherlock.bound.json",
}
p = {k: json.loads((root / v).read_text()) for k, v in names.items()}
assert p["evaluator"]["passed"] and p["evaluator"]["task_count"] == 11
assert p["binding"]["passed"] and not p["binding"].get("failure_summary")
assert p["scientific"]["passed"] and p["scientific"]["task_count"] == 7
assert p["scope"]["passed"] and p["plan"]["executable"]
assert p["plan"]["checkpoint_binding"]["git_commit"] == commit
print("FINAL CHECKPOINT PASSED")
PY

STEP=submit-canaries
bash -n "$CANARY"
submit() {
  local name=$1 wf=$2 cond=$3 tasks=$4 mem=$5 time=$6
  local id
  id=$(env WF="$wf" COND="$cond" REP=1 SMOKE_TASKS="$tasks" \
    sbatch --parsable --dependency="afterok:$SLURM_JOB_ID" \
    --job-name="$name" --cpus-per-task=1 --mem="$mem" --time="$time" \
    --export=ALL "$CANARY")
  printf '%s' "${id%%;*}"
}

KANG=$(submit a19-kang-final WF-KANG pertura_full KANG-01,KANG-02 32G 04:00:00)
PAPA12=$(submit a19-papa12-final WF-PAPA pertura_full PAPA-01,PAPA-02 32G 04:00:00)
PAPA06=$(submit a19-papa06-final WF-PAPA prompt_only PAPA-06 32G 03:00:00)
REPLFREE=$(submit a19-repl-free-final WF-REPL free_codeact REPL-01 48G 02:00:00)
REPLFULL=$(submit a19-repl-full-final WF-REPL pertura_full REPL-01 48G 02:00:00)
PAPAALL=$(submit a19-papa-all-final WF-PAPA pertura_full \
  PAPA-01,PAPA-02,PAPA-03,PAPA-04,PAPA-05,PAPA-06,PAPA-07,PAPA-08 32G 10:00:00)

LAUNCH="$PAPER_ROOT/logs/final-canaries-$COMMIT-$SLURM_JOB_ID.tsv"
printf 'KANG\t%s\nPAPA12\t%s\nPAPA06\t%s\nREPLFREE\t%s\nREPLFULL\t%s\nPAPAALL\t%s\n' \
  "$KANG" "$PAPA12" "$PAPA06" "$REPLFREE" "$REPLFULL" "$PAPAALL" | tee "$LAUNCH"
echo "FINALIZATION SUCCEEDED launch_manifest=$LAUNCH"
