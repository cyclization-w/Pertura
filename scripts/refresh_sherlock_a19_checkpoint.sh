#!/usr/bin/env bash
set -euo pipefail

BENCHMARK_ROOT=${BENCHMARK_ROOT:-/scratch/users/twang05/Project/PerturaBenchmark}
PERTURA_REPO=${PERTURA_REPO:-/scratch/users/twang05/Project/Pertura}
BRANCH=${1:-codex/a17-prebench-final}
PAPER_ROOT="$BENCHMARK_ROOT/paper-v1"
PAPER_MANIFESTS="$PAPER_ROOT/manifests"
MAIN_ENV="$BENCHMARK_ROOT/environments/pertura-aaai-py311-a19"
SUBSET_CATALOG="$BENCHMARK_ROOT/checkpoints/1e3406f6d259ea565333dab8f4421ddf53a61649/subset-catalog.json"
CANARY_SCRIPT="$BENCHMARK_ROOT/scripts/run-a19-canary.sbatch"
RESOURCE_BUILDER="$BENCHMARK_ROOT/scripts/build-sherlock-resource-lock-a19.py"

export BENCHMARK_ROOT PERTURA_REPO PAPER_ROOT PAPER_MANIFESTS MAIN_ENV
export PERTURA_BENCH_CACHE="$BENCHMARK_ROOT/cache"

for required in \
  "$MAIN_ENV/bin/python" \
  "$RESOURCE_BUILDER" \
  "$PAPER_MANIFESTS/sherlock-environment-paths.env"; do
  test -e "$required" || { echo "missing: $required" >&2; exit 1; }
done

git -C "$PERTURA_REPO" fetch origin "$BRANCH"
COMMIT=$(git -C "$PERTURA_REPO" rev-parse FETCH_HEAD)
git -C "$PERTURA_REPO" checkout "$COMMIT"
export BENCHMARK_COMMIT="$COMMIT"

CHECKPOINT_ROOT="$BENCHMARK_ROOT/checkpoints/$COMMIT"
DIST_DIR="$CHECKPOINT_ROOT/distributions"
mkdir -p "$DIST_DIR" "$BENCHMARK_ROOT/tmp" "$PAPER_ROOT/logs"

BUILD_ROOT=$(mktemp -d "$BENCHMARK_ROOT/tmp/a19-build-$COMMIT.XXXXXX")
trap 'rm -rf "$BUILD_ROOT"' EXIT
git -C "$PERTURA_REPO" archive "$COMMIT" | tar -x -C "$BUILD_ROOT"

"$MAIN_ENV/bin/python" -m build --outdir "$DIST_DIR" "$BUILD_ROOT"
WHEEL="$DIST_DIR/pertura-0.2.0a19-py3-none-any.whl"
SDIST="$DIST_DIR/pertura-0.2.0a19.tar.gz"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/check_distribution_contents.py" \
  "$WHEEL" "$SDIST"
sha256sum "$WHEEL" "$SDIST" | tee "$CHECKPOINT_ROOT/distribution-sha256.txt"
"$MAIN_ENV/bin/python" -m pip install --force-reinstall --no-deps "$WHEEL"

set -a
source "$PAPER_MANIFESTS/sherlock-environment-paths.env"
set +a

CONTRACT_CATALOG="$PAPER_MANIFESTS/capability-contract-catalog.a19.json"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/generate_a19_capability_contract_catalog.py" \
  --output "$CONTRACT_CATALOG"

RESOURCE_LOCK_SET="$PAPER_MANIFESTS/resource-lock-set.a19.sherlock.json"
export RESOURCE_LOCK_SET
"$MAIN_ENV/bin/python" "$RESOURCE_BUILDER"
sha256sum "$RESOURCE_LOCK_SET" | tee "$PAPER_MANIFESTS/resource-lock-set.a19.sherlock.sha256"

TASKS="$PERTURA_REPO/benchmarks/paper_v1/agent_tasks.v2.json"
LEGACY_TASK_REFS="$PAPER_MANIFESTS/task-reference-catalog.a18.bound.json"
TASK_REFS="$PAPER_MANIFESTS/task-reference-catalog.a19.bound.json"
ANCHORS="$PERTURA_REPO/benchmarks/paper_v1/paper_anchors.v1.json"
ASSETS="$PAPER_MANIFESTS/paper-agent-assets.a18.sherlock.bound.json"
PLAN_TEMPLATE="$CHECKPOINT_ROOT/server-plan.a19.sherlock.template.json"
BOUND_PLAN="$CHECKPOINT_ROOT/server-plan.a19.sherlock.bound.json"

"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/bind_paper_agent_catalogs.py" \
  references \
  --candidate "$PERTURA_REPO/benchmarks/paper_v1/task_references.v1.json" \
  --previous-bound "$LEGACY_TASK_REFS" \
  --task-reference-root "$PAPER_ROOT/task_references" \
  --paper-root "$PAPER_ROOT" \
  --output "$TASK_REFS"
sha256sum "$TASK_REFS" | \
  tee "$PAPER_MANIFESTS/task-reference-catalog.a19.bound.sha256"

"$MAIN_ENV/bin/python" -m pertura_bench export-server-plan \
  --repo "$PERTURA_REPO" \
  --paper-task-catalog "$TASKS" \
  --paper-task-reference-catalog "$TASK_REFS" \
  --paper-anchor-catalog "$ANCHORS" \
  --paper-asset-catalog "$ASSETS" \
  --capability-contract-catalog "$CONTRACT_CATALOG" \
  --output "$PLAN_TEMPLATE"

"$MAIN_ENV/bin/python" -m pertura_bench bind-server-plan \
  --template "$PLAN_TEMPLATE" \
  --git-commit "$COMMIT" \
  --wheel "$WHEEL" \
  --resource-lock-manifest "$RESOURCE_LOCK_SET" \
  --subset-catalog "$SUBSET_CATALOG" \
  --paper-task-catalog "$TASKS" \
  --paper-task-reference-catalog "$TASK_REFS" \
  --paper-anchor-catalog "$ANCHORS" \
  --paper-asset-catalog "$ASSETS" \
  --capability-contract-catalog "$CONTRACT_CATALOG" \
  --output "$BOUND_PLAN"

sha256sum "$PLAN_TEMPLATE" "$BOUND_PLAN" | \
  tee "$CHECKPOINT_ROOT/server-plan-sha256.txt"

"$MAIN_ENV/bin/python" - "$BOUND_PLAN" "$COMMIT" <<'PY'
import json
import sys
from importlib.metadata import version

from pertura_runtime.agent_bundle import bundled_skill_manifest

path, commit = sys.argv[1:]
plan = json.load(open(path, encoding="utf-8"))
jobs = [job for job in plan["jobs"] if job["kind"] == "paper_agent_workflow"]
turns = sum(int(job["required_task_count"]) for job in jobs)
skills = bundled_skill_manifest()
assert version("pertura") == "0.2.0a19"
assert plan["executable"] is True
assert plan["checkpoint_binding"]["git_commit"] == commit
assert len(jobs) == 24 and turns == 120
assert len(skills["skills"]) == 7
print("paper_workflow_jobs:", len(jobs))
print("required_scored_turns:", turns)
print("skill_count:", len(skills["skills"]))
PY

if test -f "$CANARY_SCRIPT"; then
  sed -i "s/^COMMIT=.*/COMMIT=$COMMIT/" "$CANARY_SCRIPT"
  sed -i \
    's/task-reference-catalog\.a18\.bound\.json/task-reference-catalog.a19.bound.json/g' \
    "$CANARY_SCRIPT"
  bash -n "$CANARY_SCRIPT"
fi

printf '%s\n' "$COMMIT" > "$PAPER_MANIFESTS/current-a19-commit.txt"
echo "a19 checkpoint refresh passed"
echo "commit=$COMMIT"
echo "wheel=$WHEEL"
echo "plan=$BOUND_PLAN"
