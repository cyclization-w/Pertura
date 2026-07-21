#!/usr/bin/env bash
set -euo pipefail

BENCHMARK_ROOT=${BENCHMARK_ROOT:-/scratch/users/twang05/Project/PerturaBenchmark}
PERTURA_REPO=${PERTURA_REPO:-/scratch/users/twang05/Project/Pertura}
BRANCH=${1:-codex/a19-minimal-rc}
PAPER_ROOT="$BENCHMARK_ROOT/paper-v1"
PAPER_MANIFESTS="$PAPER_ROOT/manifests"
MAIN_ENV="$BENCHMARK_ROOT/environments/pertura-aaai-py311-a19"
SUBSET_CATALOG="$BENCHMARK_ROOT/checkpoints/1e3406f6d259ea565333dab8f4421ddf53a61649/subset-catalog.json"
CANARY_SCRIPT="$BENCHMARK_ROOT/scripts/run-a19-canary.sbatch"
RESOURCE_BUILDER="$BENCHMARK_ROOT/scripts/build-sherlock-resource-lock-a19.py"
REFERENCE_INDEX=${REFERENCE_INDEX:-}

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

# The scientific-method checkpoint changes the frozen Python runner resources
# used by these two profiles without changing their package specifications.
# Refresh their manifests only when the newly installed wheel makes doctor fail.
for profile in python-science-v1 perturbseq-python-v1; do
  if "$MAIN_ENV/bin/pertura" env doctor "$profile" >/dev/null 2>&1; then
    echo "environment manifest current: $profile"
  else
    echo "relocking environment manifest: $profile"
    "$MAIN_ENV/bin/pertura" env setup "$profile"
  fi
done

CONTRACT_CATALOG="$PAPER_MANIFESTS/capability-contract-catalog.a19.json"
TASKS="$PERTURA_REPO/benchmarks/paper_v1/agent_tasks.v2.json"
CAPABILITY_AVAILABILITY="$CHECKPOINT_ROOT/task-capability-availability.a19.json"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/generate_a19_capability_contract_catalog.py" \
  --output "$CONTRACT_CATALOG" \
  --task-catalog "$TASKS" \
  --availability-output "$CAPABILITY_AVAILABILITY"
sha256sum "$CAPABILITY_AVAILABILITY" | \
  tee "$CHECKPOINT_ROOT/task-capability-availability-sha256.txt"

RESOURCE_LOCK_SET="$PAPER_MANIFESTS/resource-lock-set.a19.sherlock.json"
export RESOURCE_LOCK_SET
"$MAIN_ENV/bin/python" "$RESOURCE_BUILDER"
sha256sum "$RESOURCE_LOCK_SET" | tee "$PAPER_MANIFESTS/resource-lock-set.a19.sherlock.sha256"

LEGACY_TASK_REFS="$PAPER_MANIFESTS/task-reference-catalog.a18.bound.json"
TASK_REFS="$PAPER_MANIFESTS/task-reference-catalog.a19.bound.json"
ANCHORS="$PERTURA_REPO/benchmarks/paper_v1/paper_anchors.v1.json"
LEGACY_ASSETS="$PAPER_MANIFESTS/paper-agent-assets.a18.sherlock.bound.json"
ASSET_TEMPLATE="$CHECKPOINT_ROOT/paper-agent-assets.a19.sherlock.template.json"
ASSETS="$PAPER_MANIFESTS/paper-agent-assets.a19.sherlock.bound.json"
PLAN_TEMPLATE="$CHECKPOINT_ROOT/server-plan.a19.sherlock.template.json"
BOUND_PLAN="$CHECKPOINT_ROOT/server-plan.a19.sherlock.bound.json"

REFERENCE_BINDING_ARGS=(--previous-bound "$LEGACY_TASK_REFS")
if [[ -n "$REFERENCE_INDEX" ]]; then
  test -f "$REFERENCE_INDEX" || {
    echo "missing: $REFERENCE_INDEX" >&2
    exit 1
  }
  REFERENCE_BINDING_ARGS=(--reference-index "$REFERENCE_INDEX")
fi

"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/bind_paper_agent_catalogs.py" \
  references \
  --candidate "$PERTURA_REPO/benchmarks/paper_v1/task_references.v1.json" \
  "${REFERENCE_BINDING_ARGS[@]}" \
  --task-reference-root "$PAPER_ROOT/task_references" \
  --paper-root "$PAPER_ROOT" \
  --output "$TASK_REFS"
sha256sum "$TASK_REFS" | \
  tee "$PAPER_MANIFESTS/task-reference-catalog.a19.bound.sha256"

# The legacy catalog incorrectly tied PAPA-07 to the shared task-reference
# manifest. Updating the independent PAPA-06 reference must not invalidate
# PAPA-07. Derive an otherwise identical template with exactly that obsolete
# role removed, then bind every remaining asset against its current bytes.
"$MAIN_ENV/bin/python" - "$LEGACY_ASSETS" "$ASSET_TEMPLATE" <<'PY'
import copy
import json
import sys
from pathlib import Path

source_path, output_path = map(Path, sys.argv[1:])
source = json.loads(source_path.read_text(encoding="utf-8"))
assert source["schema_version"] == "pertura-paper-agent-assets-v1"
assert source["passed"] is True
workflows = copy.deepcopy(source["workflows"])
removed = []
for workflow_id, workflow in workflows.items():
    retained = []
    for asset in workflow.get("assets") or []:
        if asset.get("role") == "global_effect_reference_lock":
            removed.append((workflow_id, dict(asset)))
            continue
        asset.pop("content_sha256", None)
        retained.append(asset)
    workflow["assets"] = retained
assert len(removed) == 1 and removed[0][0] == "WF-PAPA", removed
payload = {
    "schema_version": "pertura-paper-agent-assets-template-v1",
    "source_catalogs": {
        "legacy_bound_catalog": str(source_path),
        "transformation": "remove_obsolete_global_effect_reference_lock",
    },
    "workflows": workflows,
}
output_path.write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print("removed_asset_role: global_effect_reference_lock")
PY

"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/bind_paper_agent_catalogs.py" \
  assets \
  --template "$ASSET_TEMPLATE" \
  --task-catalog "$TASKS" \
  --cache "$BENCHMARK_ROOT/cache" \
  --paper-root "$PAPER_ROOT" \
  --output "$ASSETS"

"$MAIN_ENV/bin/python" - "$LEGACY_ASSETS" "$ASSETS" <<'PY'
import json
import sys

old, new = (json.load(open(path, encoding="utf-8")) for path in sys.argv[1:])
assert new["passed"] is True
for workflow_id, old_workflow in old["workflows"].items():
    old_assets = {
        item["role"]: item for item in old_workflow.get("assets") or []
        if item["role"] != "global_effect_reference_lock"
    }
    new_assets = {
        item["role"]: item
        for item in new["workflows"][workflow_id].get("assets") or []
    }
    assert set(old_assets) == set(new_assets), workflow_id
    for role, old_asset in old_assets.items():
        current = new_assets[role]
        assert current["root"] == old_asset["root"], (workflow_id, role)
        assert current["relative_path"] == old_asset["relative_path"], (workflow_id, role)
        assert current["content_sha256"] == old_asset["content_sha256"], (
            workflow_id, role, old_asset["content_sha256"],
            current["content_sha256"],
        )
assert all(
    item["role"] != "global_effect_reference_lock"
    for workflow in new["workflows"].values()
    for item in workflow.get("assets") or []
)
print("asset_catalog_delta: obsolete_role_only")
PY
sha256sum "$ASSETS" | \
  tee "$PAPER_MANIFESTS/paper-agent-assets.a19.sherlock.bound.sha256"

EVALUATOR_QUALIFICATION="$CHECKPOINT_ROOT/evaluator-qualification.a19.json"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/qualify_a19_evaluators.py" \
  --repo "$PERTURA_REPO" \
  --wheel "$WHEEL" \
  --task-catalog "$TASKS" \
  --task-reference-catalog "$TASK_REFS" \
  --paper-root "$PAPER_ROOT" \
  --resource-lock "$RESOURCE_LOCK_SET" \
  --output "$EVALUATOR_QUALIFICATION"
sha256sum "$EVALUATOR_QUALIFICATION" | \
  tee "$CHECKPOINT_ROOT/evaluator-qualification-sha256.txt"

"$MAIN_ENV/bin/python" - "$EVALUATOR_QUALIFICATION" <<'PY'
import json
import sys

qualification = json.load(open(sys.argv[1], encoding="utf-8"))
assert qualification["schema_version"] == "pertura-evaluator-qualification-v1"
assert qualification["passed"] is True
assert qualification["status"] == "passed"
assert qualification["task_count"] == 11
assert len(qualification["records"]) == 11
assert all(record["positive_status"] == "passed" for record in qualification["records"])
print("qualified_scientific_evaluators:", qualification["task_count"])
print("evaluator_qualification_hash:", qualification["canonical_hash"])
PY

CAPABILITY_BINDING_QUALIFICATION="$CHECKPOINT_ROOT/capability-binding-qualification.a19.json"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/qualify_a19_capability_bindings.py" \
  --repo "$PERTURA_REPO" \
  --wheel "$WHEEL" \
  --task-catalog "$TASKS" \
  --task-reference-catalog "$TASK_REFS" \
  --paper-anchor-catalog "$ANCHORS" \
  --asset-catalog "$ASSETS" \
  --capability-contract-catalog "$CONTRACT_CATALOG" \
  --paper-root "$PAPER_ROOT" \
  --cache "$BENCHMARK_ROOT/cache" \
  --resource-lock "$RESOURCE_LOCK_SET" \
  --output "$CAPABILITY_BINDING_QUALIFICATION"
sha256sum "$CAPABILITY_BINDING_QUALIFICATION" | \
  tee "$CHECKPOINT_ROOT/capability-binding-qualification-sha256.txt"

"$MAIN_ENV/bin/python" - "$CAPABILITY_BINDING_QUALIFICATION" <<'PY'
import json
import sys

from pertura_bench.paper_agent_execution import (
    CAPABILITY_BINDING_QUALIFICATION_STATUSES,
)

qualification = json.load(open(sys.argv[1], encoding="utf-8"))
assert qualification["schema_version"] == "pertura-capability-binding-qualification-v1"
assert qualification["passed"] is True
assert qualification["status"] == "passed"
assert qualification["provider_schema_parity_passed"] is True
assert qualification["provider_result_visibility_passed"] is True
assert qualification["scientific_parity_passed"] is True
assert set(qualification["scientific_parity_task_ids"]) == {
    "PAPA-02",
    "PAPA-03",
    "PAPA-04",
    "PAPA-05",
    "KANG-02",
}
assert all(
    record["status"] == "passed" and record["task_status"] == "passed"
    for record in qualification["scientific_parity_records"]
)
assert set(qualification["provider_tool_schema_hashes"]) == {
    "inspect_dataset",
    "run_diagnostic",
    "run_analysis",
    "evaluate_virtual_model",
    "finalize_report",
}
assert qualification["qualified_binding_count"] > 0
assert all(
    record["qualification_status"]
    in CAPABILITY_BINDING_QUALIFICATION_STATUSES
    and record["provider_schema_validation_status"] == "passed"
    and record["provider_result_visibility_status"] == "passed"
    for record in qualification["records"]
)
print("qualified_capability_bindings:", qualification["qualified_binding_count"])
print("qualified_binding_scientific_tasks:", len(qualification["scientific_parity_records"]))
print("capability_binding_qualification_hash:", qualification["canonical_hash"])
PY

SCIENTIFIC_METHOD_PARITY="$CHECKPOINT_ROOT/scientific-method-parity.a19.json"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/qualify_a19_scientific_methods.py" \
  --repo "$PERTURA_REPO" \
  --wheel "$WHEEL" \
  --task-catalog "$TASKS" \
  --task-reference-catalog "$TASK_REFS" \
  --asset-catalog "$ASSETS" \
  --paper-root "$PAPER_ROOT" \
  --resource-lock "$RESOURCE_LOCK_SET" \
  --binding-qualification "$CAPABILITY_BINDING_QUALIFICATION" \
  --work-root "$BUILD_ROOT/scientific-method-parity" \
  --output "$SCIENTIFIC_METHOD_PARITY"
sha256sum "$SCIENTIFIC_METHOD_PARITY" | \
  tee "$CHECKPOINT_ROOT/scientific-method-parity-sha256.txt"

"$MAIN_ENV/bin/python" - "$SCIENTIFIC_METHOD_PARITY" <<'PY'
import json
import sys

qualification = json.load(open(sys.argv[1], encoding="utf-8"))
assert qualification["schema_version"] == "pertura-scientific-method-parity-v1"
assert qualification["passed"] is True
assert qualification["status"] == "passed"
assert qualification["task_count"] == 7
assert set(qualification["required_task_ids"]) == {
    "PAPA-02",
    "PAPA-03",
    "PAPA-04",
    "PAPA-05",
    "PAPA-06",
    "KANG-01",
    "KANG-02",
}
assert not qualification["failure_summary"]
assert all(record["status"] == "passed" for record in qualification["records"])
print("qualified_scientific_method_tasks:", qualification["task_count"])
print("scientific_method_parity_hash:", qualification["canonical_hash"])
PY

SCIENTIFIC_SCOPE_AUDIT="$CHECKPOINT_ROOT/scientific-method-scope-audit.a19.json"
"$MAIN_ENV/bin/python" "$PERTURA_REPO/scripts/audit_a19_scientific_method_scope.py" \
  --repo "$PERTURA_REPO" \
  --task-catalog "$TASKS" \
  --output "$SCIENTIFIC_SCOPE_AUDIT"
sha256sum "$SCIENTIFIC_SCOPE_AUDIT" | \
  tee "$CHECKPOINT_ROOT/scientific-method-scope-audit-sha256.txt"

"$MAIN_ENV/bin/python" - "$SCIENTIFIC_SCOPE_AUDIT" <<'PY'
import json
import sys

audit = json.load(open(sys.argv[1], encoding="utf-8"))
assert audit["schema_version"] == "pertura-a19-scientific-method-scope-audit-v1"
assert audit["passed"] is True
assert audit["status"] == "passed"
assert not audit["problems"]
print("scientific_method_scope_audit_hash:", audit["canonical_hash"])
PY

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

export PERTURA_BENCH_CHECKPOINT_BINDING="$BOUND_PLAN"
export PERTURA_BENCH_WHEEL="$WHEEL"
"$MAIN_ENV/bin/python" - \
  "$PERTURA_REPO" \
  "$TASKS" \
  "$TASK_REFS" \
  "$ANCHORS" \
  "$ASSETS" \
  "$CONTRACT_CATALOG" \
  "$CAPABILITY_AVAILABILITY" \
  "$BOUND_PLAN" <<'PY'
import json
import sys
from pathlib import Path

from pertura_bench.paper_agent_execution import _verify_paper_checkpoint

(
    repo,
    task_catalog,
    task_references,
    anchors,
    assets,
    capability_catalog,
    availability_path,
    plan_path,
) = (Path(value) for value in sys.argv[1:])
availability = json.loads(availability_path.read_text(encoding="utf-8"))
plan = json.loads(plan_path.read_text(encoding="utf-8"))
jobs = [
    job for job in plan["jobs"]
    if job["kind"] == "paper_agent_workflow"
]
for job in jobs:
    _verify_paper_checkpoint(
        repo_root=repo,
        workflow_id=str(job["workflow_id"]),
        condition=str(job["benchmark_condition"]),
        repeat_index=int(job["repeat_index"]),
        task_catalog_path=task_catalog,
        task_reference_catalog_path=task_references,
        paper_anchor_catalog_path=anchors,
        asset_catalog_path=assets,
        capability_contract_catalog_path=capability_catalog,
        capability_availability_hash=availability["canonical_hash"],
    )
print("runtime_checkpoint_preflight_jobs:", len(jobs))
PY

"$MAIN_ENV/bin/python" - "$BOUND_PLAN" "$COMMIT" "$CAPABILITY_AVAILABILITY" <<'PY'
import json
import sys
from importlib.metadata import version

from pertura_runtime.agent_bundle import bundled_skill_manifest

path, commit, availability_path = sys.argv[1:]
plan = json.load(open(path, encoding="utf-8"))
availability = json.load(open(availability_path, encoding="utf-8"))
jobs = [job for job in plan["jobs"] if job["kind"] == "paper_agent_workflow"]
turns = sum(int(job["required_task_count"]) for job in jobs)
skills = bundled_skill_manifest()
assert version("pertura") == "0.2.0a19"
assert plan["executable"] is True
assert plan["checkpoint_binding"]["git_commit"] == commit
assert len(jobs) == 24 and turns == 120
assert len(skills["skills"]) == 7
assert availability["task_count"] == 21
assert all(
    job["capability_availability_hash"] == availability["canonical_hash"]
    for job in jobs
)
for job in jobs:
    expected_memory = 48.0 if job["workflow_id"] == "WF-REPL" else 32.0
    assert float(job["resources"]["memory_gb"]) == expected_memory
    assert int(job["resources"]["cpus"]) == 1
print("paper_workflow_jobs:", len(jobs))
print("required_scored_turns:", turns)
print("skill_count:", len(skills["skills"]))
print("capability_availability_hash:", availability["canonical_hash"])
print("workflow_memory_gb: WF-REPL=48, WF-PAPA/WF-NORM/WF-KANG=32")
PY

if test -f "$CANARY_SCRIPT"; then
  sed -i "s/^COMMIT=.*/COMMIT=$COMMIT/" "$CANARY_SCRIPT"
  sed -i "s#^export PERTURA_REPO=.*#export PERTURA_REPO=$PERTURA_REPO#" \
    "$CANARY_SCRIPT"
  sed -i \
    's/task-reference-catalog\.a18\.bound\.json/task-reference-catalog.a19.bound.json/g' \
    "$CANARY_SCRIPT"
  sed -i \
    's/paper-agent-assets\.a18\.sherlock\.bound\.json/paper-agent-assets.a19.sherlock.bound.json/g' \
    "$CANARY_SCRIPT"
  bash -n "$CANARY_SCRIPT"
fi

printf '%s\n' "$COMMIT" > "$PAPER_MANIFESTS/current-a19-commit.txt"
echo "a19 checkpoint refresh passed"
echo "commit=$COMMIT"
echo "wheel=$WHEEL"
echo "plan=$BOUND_PLAN"
echo "evaluator_qualification=$EVALUATOR_QUALIFICATION"
echo "capability_binding_qualification=$CAPABILITY_BINDING_QUALIFICATION"
echo "scientific_method_parity=$SCIENTIFIC_METHOD_PARITY"
echo "scientific_method_scope_audit=$SCIENTIFIC_SCOPE_AUDIT"
echo "reference_index=${REFERENCE_INDEX:-resolved-from-previous-binding}"
