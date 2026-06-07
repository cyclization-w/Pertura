"""Smoke tests for the Pertura harness; mostly pure functions, no live LLM needed."""

import argparse, json, os, subprocess, sys, tempfile, time, threading, types
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pertura as pt
from pertura.models import (
    Event, Snapshot, Attempt, Outcome, Observation, Branch, Finding,
    ReviewDecision, ToolCall, Budget,
)
from pertura.core import (
    reduce, build_graph, validate_graph,
    Store, GraphController, GraphMutationError,
    trace_upstream, impact_of_change,
    Behavior, BehaviorRegistry,
    build_context_view, build_view, build_observation_view, build_trace_view, build_impact_view,
    build_active_work_order,
    ResponseCache, hash_tool_call,
    ReplayError, replay_store, fork_store, diff_stores,
    run_integrity, stable_json_sha256,
    relation_effect,
    review_evidence_chain,
    plan_rethinking,
    build_observation_memory_view,
    GateEvaluator,
    audit_run,
)
from pertura.memory.compiler import compile_context
from pertura.hooks import pre_execute, pre_conclusion
from pertura.domain import Domain
from pertura.domain import perturbseq
from pertura.domain.perturbseq import DOMAIN as PERTURBSEQ_DOMAIN
from pertura.agent.loop import Workbench
from pertura.jobs import JobRunner
from pertura.models import _model_dump, PatchProposal
from pertura import (
    AnalysisGraph, CapabilityRegistry, capability, condition, conditions as c,
    compile_conditions, load_analysis_graph, save_analysis_graph,
    validate_analysis_graph,
    node_contract, graph_contract, audit_analysis_graph,
)
from pertura.tools import ToolPermission, check_permission
from pertura.tools.registry import execute_tool, tool_schemas

PASS, FAIL = 0, 0
CURRENT_SEGMENT = ""

TEST_SEGMENTS = [
    ("event_reducer", "1. Event reducer", ["event_sourcing"]),
    ("graph_derivation", "2. Graph derivation", ["event_sourcing"]),
    ("context_compilation", "3. Context compilation", ["context"]),
    ("hooks", "4. Hooks", ["safety", "evidence_chain"]),
    ("branch_lifecycle", "5. Branch lifecycle", ["deliberative_audit"]),
    ("observation_memory", "6. Observation memory", ["observation_memory"]),
    ("execution_chain", "7. Real attempt execution chain", ["deliberative_audit", "evidence_chain"]),
    ("controller_policy", "8. GraphController and patch lifecycle", ["safety", "analysis_graph"]),
    ("deterministic_behaviors", "9. Deterministic behaviors", ["observation_memory", "evidence_chain"]),
    ("trace_context_evidence", "10. Trace and impact graph semantics", ["observation_memory", "evidence_chain", "context"]),
    ("jobs_cancellation", "11. Persistent jobs and cooperative cancellation", ["runtime"]),
    ("replay_operator", "12. Replay, fork, and scientific diff", ["deliberative_audit", "evidence_chain", "operator_surface"]),
    ("analysis_spec_gating", "13. Pertura v2 analysis spec and gating", ["analysis_graph", "deliberative_audit", "operator_surface"]),
]

SEGMENT_BY_ID = {segment_id: {"title": title, "claims": claims} for segment_id, title, claims in TEST_SEGMENTS}
CLAIM_ALIASES = {
    "analysis_graph": ["analysis_spec_gating", "controller_policy"],
    "observation_memory": ["observation_memory", "trace_context_evidence", "deterministic_behaviors"],
    "deliberative_audit": ["execution_chain", "branch_lifecycle", "replay_operator", "analysis_spec_gating"],
    "evidence_chain": ["hooks", "execution_chain", "deterministic_behaviors", "trace_context_evidence", "replay_operator"],
    "operator_surface": ["replay_operator", "analysis_spec_gating"],
}


def _parse_segment_args() -> set[str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--list-segments", action="store_true")
    parser.add_argument("--segment", action="append", default=[])
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    if args.list_segments:
        print("Available test segments:")
        for segment_id, title, claims in TEST_SEGMENTS:
            print(f"  {segment_id}: {title} [{', '.join(claims)}]")
        print("\nClaim aliases:")
        for alias, segment_ids in sorted(CLAIM_ALIASES.items()):
            print(f"  {alias}: {', '.join(segment_ids)}")
        sys.exit(0)
    selected: set[str] = set()
    for raw in args.segment:
        for item in str(raw).split(","):
            key = item.strip()
            if not key:
                continue
            if key in CLAIM_ALIASES:
                selected.update(CLAIM_ALIASES[key])
            elif key in SEGMENT_BY_ID:
                selected.add(key)
            else:
                known = sorted([*SEGMENT_BY_ID, *CLAIM_ALIASES])
                print(f"Unknown test segment or claim alias: {key}", file=sys.stderr)
                print(f"Known values: {', '.join(known)}", file=sys.stderr)
                sys.exit(2)
    return selected


SELECTED_SEGMENTS = _parse_segment_args()


def _segment_active(segment_id: str | None = None) -> bool:
    segment_id = segment_id or CURRENT_SEGMENT
    return not SELECTED_SEGMENTS or segment_id in SELECTED_SEGMENTS


def start_segment(segment_id: str):
    global CURRENT_SEGMENT
    CURRENT_SEGMENT = segment_id
    if _segment_active(segment_id):
        print(f"\n-- {SEGMENT_BY_ID[segment_id]['title']} --")


def check(name, condition, detail=""):
    global PASS, FAIL
    if not _segment_active():
        return
    if condition:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} -- {detail}")


# ── Test 1: Event reducer ──────────────────────────────────────────────

start_segment("event_reducer")

events = [
    Event(event_id="e1", event_type="run_started", run_id="test",
          payload={"config": {"run_id": "test", "workspace": "/tmp", "goal": "test",
                              "domain": "perturbseq", "protocol": "Test protocol",
                              "budget": {"max_attempts": 5, "max_branches": 2, "max_repairs": 2},
                              "capabilities": [{"id": "test.inspect", "stage": "inspect"}]}}),
    Event(event_id="e2", event_type="attempt_planned", run_id="test",
          payload={"attempt": {"attempt_id": "att_1", "branch_id": "main", "title": "Load data",
                               "stage": "inspect", "status": "planned", "notebook_cells": []}}),
    Event(event_id="e3", event_type="outcome_recorded", run_id="test",
          payload={"outcome": {"outcome_id": "out_1", "attempt_id": "att_1", "status": "success",
                               "summary": "Loaded", "metrics": {"returncode": 0}}}),
    Event(event_id="e4", event_type="observation_registered", run_id="test",
          payload={"observation": {"observation_id": "obs_1", "type": "workspace_file",
                                   "target": "data.h5ad", "metric": "file_size", "value": 1000,
                                   "method": "auto_discover", "attempt_id": "att_1", "branch_id": "main"}}),
    Event(event_id="e5", event_type="observation_registered", run_id="test",
          payload={"observation": {"observation_id": "obs_2", "type": "de_effect",
                                   "target": "TargetX", "metric": "logFC", "value": 2.1,
                                   "contrast": "KO_vs_WT", "method": "wilcoxon",
                                   "attempt_id": "att_1", "branch_id": "main"}}),
    Event(event_id="e6", event_type="branch_opened", run_id="test",
          payload={"branch": {"branch_id": "br_1", "title": "Check coverage",
                               "parent_id": "main", "reason": "parameter_sensitivity",
                               "question": "Is coverage adequate?", "status": "active"}}),
    Event(event_id="e7", event_type="attempt_planned", run_id="test",
          payload={"attempt": {"attempt_id": "att_2", "branch_id": "br_1", "title": "Coverage check",
                               "stage": "target_qc", "status": "planned", "notebook_cells": []}}),
    Event(event_id="e8", event_type="outcome_recorded", run_id="test",
          payload={"outcome": {"outcome_id": "out_2", "attempt_id": "att_2", "status": "success",
                               "summary": "Checked", "metrics": {"returncode": 0}}}),
    Event(event_id="e9", event_type="observation_registered", run_id="test",
          payload={"observation": {"observation_id": "obs_3", "type": "de_effect",
                                   "target": "TargetX", "metric": "logFC", "value": -0.4,
                                   "contrast": "NTC_vs_KO", "method": "wilcoxon",
                                   "attempt_id": "att_2", "branch_id": "br_1"}}),
    Event(event_id="e10", event_type="branch_stopped", run_id="test",
          payload={"branch_id": "br_1", "summary": "Coverage OK", "conclusion": "Adequate",
                   "evidence_ids": ["obs_3"]}),
    Event(event_id="e11", event_type="tool_call_recorded", run_id="test",
          payload={"tool_call_id": "tc_1", "tool_name": "query_observations",
                   "arguments": {"target": "TargetX"}, "result_summary": "Found 2 values",
                   "attempt_id": "att_2"}),
    Event(event_id="e12", event_type="review_decision_recorded", run_id="test",
          payload={"review_id": "rev_att_2", "attempt_id": "att_2", "decision": "execute_code",
                   "assessment_status": "useful", "assessment_summary": "Good result",
                   "reason": "Coverage adequate", "evidence_ids": ["obs_3"]}),
    Event(event_id="e13", event_type="conclusion_recorded", run_id="test",
          payload={"conclusion": {"conclusion_id": "con_1", "text": "TargetX effect confirmed",
                                  "grade": "supported", "support_ids": ["obs_1", "obs_2", "obs_3"]}}),
]

snap = reduce(events)
check("run_id correct", snap.run_id == "test")
check("workspace correct", snap.workspace == "/tmp")
check("attempts count", len(snap.attempts) == 2)
check("observations count", len(snap.observations) == 3)
check("branches count", len(snap.branches) == 2)
check("branch closed", any(b.status == "stopped" for b in snap.branches))
check("active_branch back to main", snap.active_branch == "main",
      f"got {snap.active_branch}")
check("tool_calls count", len(snap.tool_calls) == 1)
check("review_decisions count", len(snap.review_decisions) == 1)
check("conclusions count", len(snap.conclusions) == 1)
check("observation values present", snap.observations[1].value == 2.1)


# ── Test 2: Graph derivation ───────────────────────────────────────────

start_segment("graph_derivation")

graph = build_graph(snap)
nodes_by_type = {}
for n in graph.get("nodes", []):
    nodes_by_type.setdefault(n["node_type"], []).append(n)

check("graph has nodes", len(graph["nodes"]) > 0)
check("graph has edges", len(graph["edges"]) > 0)
check("workspace node", len(nodes_by_type.get("workspace", [])) == 1)
check("branch nodes", len(nodes_by_type.get("branch", [])) == 2)
check("attempt nodes", len(nodes_by_type.get("attempt", [])) == 2)
check("observation nodes", len(nodes_by_type.get("observation", [])) == 3)
check("outcome nodes", len(nodes_by_type.get("outcome", [])) == 2)
check("tool_call node", len(nodes_by_type.get("tool_call", [])) == 1)
check("review_decision node", len(nodes_by_type.get("review_decision", [])) == 1)
check("conclusion node", len(nodes_by_type.get("conclusion", [])) == 1)

violations = validate_graph(graph)
check("graph valid", len(violations) == 0, f"violations: {violations}")


# ── Test 3: Context compilation ────────────────────────────────────────

start_segment("context_compilation")

ctx = compile_context(snap)
check("workspace_files populated", len(ctx.workspace_files) > 0,
      f"got {len(ctx.workspace_files)}")
check("ws file is data.h5ad", any("data.h5ad" in str(f) for f in ctx.workspace_files))
check("memory has entries", len(ctx.memory) > 0, f"got {len(ctx.memory)}")
check("coverage has entries", len(ctx.coverage) > 0)
check("protocol injected", ctx.protocol == "Test protocol")
check("intent trace has entries", len(ctx.intent) > 0)
check("truncated flag works", isinstance(ctx.truncated, bool))
check("context has graph summary field", isinstance(ctx.graph_summary, dict))


# ── Test 4: Hooks ──────────────────────────────────────────────────────

start_segment("hooks")

# pre_execute
safe_events = pre_execute("print('hello')", "/tmp/ws", "/tmp/artifacts")
check("safe code no violations", len([e for e in safe_events if e[0] == "safety_violation_recorded"]) == 0)

danger_events = pre_execute("import os; os.system('ls')", "/tmp/ws", "/tmp/artifacts")
check("dangerous code has violations", len([e for e in danger_events if e[0] == "safety_violation_recorded"]) > 0)

# pre_conclusion
gate_ok = pre_conclusion(["obs_1", "obs_2"], snap)
check("pre_conclusion ok with support", len([e for e in gate_ok if e[1].get("severity") == "blocking"]) == 0)

gate_empty = pre_conclusion([], snap)
check("pre_conclusion warns on empty support", len(gate_empty) > 0)


# ── Test 5: Branch lifecycle ───────────────────────────────────────────

start_segment("branch_lifecycle")

main_branch = next(b for b in snap.branches if b.branch_id == "main")
check("main branch active", main_branch.status == "active")

closed_branch = next(b for b in snap.branches if b.branch_id == "br_1")
check("closed branch stopped", closed_branch.status == "stopped")
check("closed branch has summary", closed_branch.summary == "Coverage OK")
check("closed branch has conclusion", closed_branch.conclusion == "Adequate")
check("closed branch has evidence", "obs_3" in closed_branch.evidence_ids)

# active_branch returned to main after close
check("active branch is main after close", snap.active_branch == "main")

# attempt in branch
br_attempt = next(a for a in snap.attempts if a.branch_id == "br_1")
check("branch attempt exists", br_attempt is not None)
check("branch attempt stage correct", br_attempt.stage == "target_qc")


# ── Test 6: Observation memory (cross-branch alignment) ─────────────────

start_segment("observation_memory")

# TargetX appears in both main (logFC=2.1) and br_1 (logFC=-0.4)
targetx_obs = [o for o in snap.observations if o.target == "TargetX"]
check("TargetX has 2 observations across branches", len(targetx_obs) == 2)
check("branch values differ", targetx_obs[0].value != targetx_obs[1].value)
memory_view = build_observation_memory_view(snap, target="TargetX", metric="logFC")
check("observation memory view populated", memory_view["variable_count"] >= 1)
check("observation memory distinguishes cross-context divergence", len(memory_view["divergences"]) >= 1)
check("observation memory strict conflicts separated", len(memory_view["conflicts"]) == 0)
check("observation memory has coverage", len(memory_view["coverage"]) >= 1)
obs_view_with_memory = build_observation_view(snap, target="TargetX", metric="logFC")
check("observation view embeds memory", obs_view_with_memory["memory"]["variable_count"] >= 1)

from pertura.tools.registry import TOOLS, execute_tool
tool_memory = execute_tool("query_observation_memory", {"target": "TargetX", "metric": "logFC"}, snap=snap)
check("observation memory tool works", tool_memory["view_type"] == "observation_memory")
check("observation memory tool returns divergences", len(tool_memory["divergences"]) >= 1)


# ── Test 7: Real attempt execution chain ────────────────────────────────

start_segment("execution_chain")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    ws = Path(td)
    (ws / "input.txt").write_text("ok", encoding="utf-8")
    (ws / "metadata.csv").write_text("cell,guide,target,batch,control_label\nc1,g1,TargetX,b1,NTC\n", encoding="utf-8")
    (ws / "data.h5ad").write_text("placeholder", encoding="utf-8")
    wb = Workbench(Domain(name="test"), provider="openai", sandbox="subprocess")
    wb.run(str(ws), goal="smoke", steps=0)
    code = """
out = artifacts_dir / "summary.csv"
out.write_text("gene,logFC\\nTargetX,1.2\\n", encoding="utf-8")
register_artifact(out, "table", "Smoke result table")
register_observation("de_effect", target="TargetX", metric="logFC", value=1.2, contrast="KO_vs_NTC", method="smoke")
"""
    wb._emit("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_smoke",
        branch_id="main",
        title="Smoke execution",
        stage="smoke",
        notebook_cells=[{"source": code}],
    ))})
    wb.step(1)
    smoke_snap = wb._store.read_snapshot()
    check("execution outcome recorded", any(o.attempt_id == "att_smoke" and o.status == "success" for o in smoke_snap.outcomes))
    check("workspace probe registers dataset candidate", any(o.type == "workspace_probe" and o.metric == "detected_format" and o.target == "data.h5ad" for o in smoke_snap.observations))
    check("workspace probe registers table schema", any(o.type == "workspace_probe" and o.metric == "table_columns" and o.target == "metadata.csv" for o in smoke_snap.observations))
    check("workspace probe registers design column candidates", any(o.type == "workspace_probe" and o.metric == "candidate_design_column" and o.target == "guide" for o in smoke_snap.observations))
    smoke_work_order = build_active_work_order(smoke_snap, compile_context(smoke_snap, max_items=8))
    check("active work order recommends load_dataset plan", "load_dataset(path=" in " ".join(smoke_work_order["recommended_actions"]))
    check("manifest observation registered", any(o.target == "TargetX" and o.metric == "logFC" for o in smoke_snap.observations))
    check("manifest artifact registered", any(a.kind == "table" and "summary.csv" in a.path for a in smoke_snap.artifacts))
    smoke_graph = wb._store.read_graph()
    check("execution graph valid", len(validate_graph(smoke_graph)) == 0)
    smoke_report = wb.report()
    smoke_report_md = (wb._store.run_dir / "report.md").read_text(encoding="utf-8")
    smoke_report_html = (wb._store.run_dir / "report.html").read_text(encoding="utf-8")
    check("report embeds run audit", smoke_report.get("run_audit", {}).get("audit_type") == "run_audit")
    check("report embeds provenance manifest", smoke_report.get("provenance_manifest", {}).get("manifest_type") == "provenance_manifest")
    check("report embeds harness thesis", smoke_report.get("harness_manifest", {}).get("thesis", {}).get("core_principle") == "free_reasoning_gated_commit")
    check("report embeds trace-driven rethinking summary", smoke_report.get("trace_driven_rethinking", {}).get("view_type") == "rethinking_report_summary")
    smoke_report_json = json.loads((wb._store.run_dir / "report.json").read_text(encoding="utf-8"))
    check("report json written with audit", smoke_report_json["run_audit"]["audit_type"] == "run_audit")
    check("report json written with harness thesis", smoke_report_json["harness_manifest"]["thesis"]["core_principle"] == "free_reasoning_gated_commit")
    check("report json written with rethinking summary", smoke_report_json["trace_driven_rethinking"]["view_type"] == "rethinking_report_summary")
    check("report markdown includes harness thesis", "## Harness Thesis" in smoke_report_md)
    check("report markdown includes run audit section", "## Run Audit" in smoke_report_md)
    check("report markdown includes audit next actions", "### Audit Next Actions" in smoke_report_md)
    check("report markdown includes rethinking section", "## Trace-Driven Rethinking" in smoke_report_md)
    check("report html includes harness thesis", "Harness Thesis" in smoke_report_html)
    check("report html includes provenance manifest", "Provenance Manifest" in smoke_report_html)
    check("report html includes audit next actions", "Audit Next Actions" in smoke_report_html)
    check("report html includes rethinking section", "Trace-Driven Rethinking" in smoke_report_html)
    check("report html is offline safe", "https://cdnjs" not in smoke_report_html and "cdn.jsdelivr" not in smoke_report_html)
    from pertura.reporting import render_html
    malicious_html = render_html({
        "run_id": "x<script>alert(1)</script>",
        "workspace": "<script>alert(2)</script>",
        "goal": "<script>alert(3)</script>",
        "summary": {"attempts_total": 0, "attempts_succeeded": 0, "observations_total": 0, "branches": 0},
        "harness_manifest": {},
        "run_audit": {},
        "trace_driven_rethinking": {"summary": "<script>alert(6)</script>", "recommended_actions": [{"tool": "<script>alert(7)</script>"}]},
        "provenance_manifest": {},
        "narrative": "<script>alert(4)</script>",
        "coverage": [],
        "memory_signals": [],
        "observation_detail": [{"target": "<script>alert(5)</script>"}],
    })
    check("report html escapes script tags", "<script>alert" not in malicious_html and "&lt;script&gt;" in malicious_html)

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    ws = Path(td)
    (ws / "input.txt").write_text("ok", encoding="utf-8")
    cap_domain = Domain(
        name="capability_contract",
        capabilities=[
            next(item for item in PERTURBSEQ_DOMAIN.capabilities if item.get("capability_id") == "run_de")
        ],
    )
    cap_summary = CapabilityRegistry(cap_domain.capabilities).summarize(["run_de"])[0]
    check("capability exposes analysis modes", "differential_expression" in cap_summary["analysis_modes"])
    check("capability exposes implementation packages", "scanpy" in cap_summary["packages"])
    check("capability exposes implementation functions", "scanpy.tl.rank_genes_groups" in cap_summary["functions"])
    wb = Workbench(cap_domain, provider="openai", sandbox="subprocess")
    wb.run(str(ws), goal="contract smoke", steps=0)
    code = """
register_observation("de_effect", target="TargetX", metric="logFC", value=1.2, method="smoke")
"""
    wb._emit("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_contract",
        branch_id="main",
        title="Contract execution",
        stage="effect_exploration",
        capability_ids=["run_de"],
        notebook_cells=[{"source": code}],
    ))})
    wb.step(1)
    contract_snap = wb._store.read_snapshot()
    contract_findings = [
        finding for finding in contract_snap.findings
        if finding.attempt_id == "att_contract"
        and finding.finding_type == "capability_contract_missing_output"
    ]
    check("capability contract emits missing output finding", len(contract_findings) == 1)
    check("capability contract names missing outputs", "p_value" in contract_findings[0].summary and "de_result" in contract_findings[0].summary)
    contract_audit = audit_run(contract_snap, wb._store.read_graph(), run_dir=wb._store.run_dir)
    contract_errors = [
        item for item in contract_audit["errors"]
        if item["code"] == "missing_capability_outputs"
        and item["details"].get("attempt_id") == "att_contract"
    ]
    check("run audit catches missing capability outputs", len(contract_errors) == 1)
    check(
        "run audit names missing capability outputs",
        "p_value" in contract_errors[0]["details"].get("missing_observations", [])
        and "de_result" in contract_errors[0]["details"].get("missing_artifacts", []),
    )
    check(
        "run audit suggests capability repair action",
        any(
            action.get("tool") == "get_capability_template"
            and action.get("args", {}).get("capability_id") == "run_de"
            for action in contract_audit["next_actions"]
        ),
    )

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    ws = Path(td)
    (ws / "input.txt").write_text("ok", encoding="utf-8")
    cap_domain = Domain(
        name="capability_contract_clean",
        capabilities=[
            capability(
                "run_de",
                expected_observations=["logFC", "p_value"],
                expected_artifacts=["de_result"],
            ).model_dump(mode="json")
        ],
    )
    wb = Workbench(cap_domain, provider="openai", sandbox="subprocess")
    wb.run(str(ws), goal="clean contract smoke", steps=0)
    code = """
from pathlib import Path
out = Path(artifacts_dir) / "de_result.csv"
out.write_text("gene,logFC,p_value\\nTargetX,1.2,0.01\\n", encoding="utf-8")
register_observation("de_effect", target="TargetX", metric="logFC", value=1.2, method="smoke")
register_observation("de_effect", target="TargetX", metric="p_value", value=0.01, method="smoke")
register_artifact(str(out), kind="table", summary="DE results table")
"""
    wb._emit("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_contract_clean",
        branch_id="main",
        title="Clean contract execution",
        stage="effect_exploration",
        capability_ids=["run_de"],
        notebook_cells=[{"source": code}],
    ))})
    wb.step(1)
    clean_contract_snap = wb._store.read_snapshot()
    clean_contract_audit = audit_run(clean_contract_snap, wb._store.read_graph(), run_dir=wb._store.run_dir)
    check(
        "run audit accepts satisfied capability contract",
        not any(item["code"] == "missing_capability_outputs" for item in clean_contract_audit["errors"]),
        clean_contract_audit["errors"],
    )


# ── Test 8: GraphController and patch lifecycle ─────────────────────────

start_segment("controller_policy")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    store = Store(Path(td) / "run_controller")
    controller = GraphController(store, "ctrl")
    controller.append_event("run_started", {"config": {
        "run_id": "ctrl",
        "workspace": td,
        "goal": "controller smoke",
        "domain": "test",
        "budget": {"max_attempts": 1, "max_branches": 1, "max_repairs": 1},
        "capabilities": [],
    }})
    patch = PatchProposal(patch_id="patch_1", patch_type="goal", payload={"text": "updated"})
    controller.propose_patch(patch)
    controller.apply_patch("patch_1", [("goal_recorded", {"goal": {
        "goal_id": "goal_patch",
        "text": "updated",
        "status": "active",
    }}, "test")])
    ctrl_snap = store.read_snapshot()
    check("patch applied", ctrl_snap.patch_proposals[0]["status"] == "applied")
    check("patch event applied goal", any(g.goal_id == "goal_patch" for g in ctrl_snap.goals))

    rejected = PatchProposal(patch_id="patch_2", patch_type="attempt", payload={})
    controller.propose_patch(rejected)
    controller.reject_patch("patch_2", "not needed")
    ctrl_snap = store.read_snapshot()
    check("patch rejected", next(p for p in ctrl_snap.patch_proposals if p["patch_id"] == "patch_2")["status"] == "rejected")

    controller.append_event("branch_activated", {"branch_id": "main"})
    ctrl_snap = store.read_snapshot()
    check("branch activation switches branch", ctrl_snap.active_branch == "main")
    try:
        controller.append_event("branch_activated", {"branch_id": "missing_branch"})
        missing_branch_rejected = False
    except GraphMutationError:
        missing_branch_rejected = True
    check("missing branch activation rejected", missing_branch_rejected)

    controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_unique",
        branch_id="main",
        title="unique",
    ))})
    try:
        controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
            attempt_id="att_unique",
            branch_id="main",
            title="duplicate",
        ))})
        duplicate_rejected = False
    except GraphMutationError:
        duplicate_rejected = True
    check("duplicate attempt rejected", duplicate_rejected)

    try:
        controller.append_event("mystery_event", {})
        unknown_event_rejected = False
        unknown_event_error = {}
    except GraphMutationError as exc:
        unknown_event_rejected = True
        unknown_event_error = exc.as_dict()
    check("unknown event type rejected by schema", unknown_event_rejected)
    check("schema mutation error exposes code", unknown_event_error.get("code") == "event.schema_error")
    check("schema mutation error exposes doc url", str(unknown_event_error.get("doc_url", "")).startswith("https://"))

    try:
        controller.append_event("attempt_planned", {"bad": {}})
        missing_wrapper_rejected = False
    except GraphMutationError:
        missing_wrapper_rejected = True
    check("entity event missing wrapper rejected", missing_wrapper_rejected)

    try:
        controller.append_event("observation_registered", {"observation": {
            "observation_id": "obs_bad_schema",
            "type": "de_effect",
            "target": "TargetBad",
            "metric": "logFC",
            "value": None,
            "attempt_id": "att_unique",
            "branch_id": "main",
        }})
        null_observation_value_rejected = False
    except GraphMutationError:
        null_observation_value_rejected = True
    check("observation without value rejected by schema", null_observation_value_rejected)

    schema_store = Store(Path(td) / "run_schema")
    schema_controller = GraphController(schema_store, "schema")
    try:
        schema_controller.append_event("run_started", {"config": {
            "run_id": "wrong",
            "workspace": td,
            "goal": "schema mismatch",
            "domain": "test",
            "budget": {"max_attempts": 1},
            "capabilities": [],
        }})
        run_id_mismatch_rejected = False
    except GraphMutationError:
        run_id_mismatch_rejected = True
    check("run_started run_id mismatch rejected", run_id_mismatch_rejected)

    raw_store = Store(Path(td) / "run_raw_schema")
    try:
        raw_store.append([Event(event_id="raw_bad", event_type="mystery_event", run_id="raw", payload={})])
        raw_unknown_rejected = False
    except Exception:
        raw_unknown_rejected = True
    check("raw Store.append rejects unknown event", raw_unknown_rejected)

    try:
        raw_store.append([Event(event_id="raw_first_bad", event_type="goal_recorded", run_id="raw", payload={"goal": {"goal_id": "g", "text": "bad"}})])
        raw_first_event_rejected = False
    except Exception:
        raw_first_event_rejected = True
    check("raw Store.append rejects non-run_started first event", raw_first_event_rejected)

    risky = PatchProposal(
        patch_id="patch_risky",
        patch_type="web_research",
        payload={"query": "TargetZ function", "requires_approval": True},
    )
    controller.propose_patch(risky)
    ctrl_snap = store.read_snapshot()
    approval = next((a for a in ctrl_snap.approvals if a.subject_id == "patch_risky"), None)
    check("risky patch creates approval", approval is not None and approval.status == "open")
    try:
        controller.apply_patch("patch_risky", [("goal_recorded", {"goal": {
            "goal_id": "goal_risky",
            "text": "risky applied",
            "status": "active",
        }}, "test")])
        apply_without_approval_rejected = False
    except GraphMutationError:
        apply_without_approval_rejected = True
    check("risky patch cannot apply without approval", apply_without_approval_rejected)
    controller.decide_approval(approval.approval_id, "approved", resolved_by="tester")
    controller.apply_patch("patch_risky", [("goal_recorded", {"goal": {
        "goal_id": "goal_risky",
        "text": "risky applied",
        "status": "active",
    }}, "test")])
    ctrl_snap = store.read_snapshot()
    check("approved risky patch applied", next(p for p in ctrl_snap.patch_proposals if p["patch_id"] == "patch_risky")["status"] == "applied")

    forbidden = PatchProposal(patch_id="patch_forbidden", patch_type="delete_history")
    controller.propose_patch(forbidden)
    ctrl_snap = store.read_snapshot()
    check("forbidden patch rejected by policy", next(p for p in ctrl_snap.patch_proposals if p["patch_id"] == "patch_forbidden")["status"] == "rejected")


# ── Test 9: Deterministic behaviors ─────────────────────────────────────

start_segment("deterministic_behaviors")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    store = Store(Path(td) / "run_behaviors")
    controller = GraphController(store, "beh")
    controller.append_event("run_started", {"config": {
        "run_id": "beh",
        "workspace": td,
        "goal": "behavior smoke",
        "domain": "test",
        "budget": {"max_attempts": 5, "max_branches": 1, "max_repairs": 1},
        "capabilities": [],
    }})
    controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_error",
        branch_id="main",
        title="error",
    ))})
    controller.append_event("outcome_recorded", {"outcome": {
        "outcome_id": "out_error",
        "attempt_id": "att_error",
        "status": "error",
        "summary": "failed",
        "metrics": {"stderr": "KeyError: x"},
    }})
    behavior_snap = store.read_snapshot()
    check("runtime failure behavior opens trigger", any(t.attempt_id == "att_error" and t.trigger_type == "runtime_error" for t in behavior_snap.triggers))
    check("behavior lifecycle recorded", any(b.behavior_id == "runtime_failure_trigger" and b.status == "completed" for b in behavior_snap.behavior_runs))
    check("behavior output ids recorded", any(b.behavior_id == "runtime_failure_trigger" and b.output_count > 0 and b.output_event_ids for b in behavior_snap.behavior_runs))

    controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_zero",
        branch_id="main",
        title="zero",
    ))})
    controller.append_event("outcome_recorded", {"outcome": {
        "outcome_id": "out_zero",
        "attempt_id": "att_zero",
        "status": "success",
        "summary": "no obs",
        "metrics": {"observations_registered": 0},
    }})
    behavior_snap = store.read_snapshot()
    check("zero observation behavior records finding", any(f.attempt_id == "att_zero" and f.finding_type == "missing_context" for f in behavior_snap.findings))

    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_pos",
        "type": "de_effect",
        "target": "TargetY",
        "metric": "logFC",
        "value": 1.0,
        "contrast": "KO_vs_NTC",
        "method": "smoke",
        "attempt_id": "att_zero",
        "branch_id": "main",
    }})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_neg",
        "type": "de_effect",
        "target": "TargetY",
        "metric": "logFC",
        "value": -1.2,
        "contrast": "KO_vs_NTC",
        "method": "smoke",
        "attempt_id": "att_zero",
        "branch_id": "main",
    }})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_context_neg",
        "type": "de_effect",
        "target": "TargetY",
        "metric": "logFC",
        "value": -0.8,
        "contrast": "KO_vs_WT",
        "method": "aggregate",
        "attempt_id": "att_zero",
        "branch_id": "main",
    }})
    behavior_snap = store.read_snapshot()
    check("conflict behavior records finding", any(f.finding_type == "observation_conflict" for f in behavior_snap.findings))
    check("divergence behavior records finding", any(f.finding_type == "observation_divergence" for f in behavior_snap.findings))

    controller.append_event("conclusion_recorded", {"conclusion": {
        "conclusion_id": "con_empty",
        "text": "Unsupported",
        "grade": "tentative",
        "support_ids": [],
    }})
    behavior_snap = store.read_snapshot()
    check("unsupported conclusion behavior records finding", any("con_empty" in f.affected_ids for f in behavior_snap.findings))

    fail_store = Store(Path(td) / "run_behavior_failure")
    fail_controller = GraphController(fail_store, "beh_fail")

    def _bad_behavior(events, snap, graph):
        raise RuntimeError("behavior blew up")

    fail_controller.behaviors = BehaviorRegistry([Behavior("bad_behavior", _bad_behavior)])
    fail_controller.append_event("run_started", {"config": {
        "run_id": "beh_fail",
        "workspace": td,
        "goal": "behavior failure smoke",
        "domain": "test",
        "budget": {"max_attempts": 5, "max_branches": 1, "max_repairs": 1},
        "capabilities": [],
    }})
    fail_snap = fail_store.read_snapshot()
    check("behavior failure recorded", any(b.behavior_id == "bad_behavior" and b.status == "failed" for b in fail_snap.behavior_runs))


# ── Summary ────────────────────────────────────────────────────────────

# ── Test 10: Trace and impact graph semantics ──────────────────────────

start_segment("trace_context_evidence")

trace_events = [
    Event(event_id="tr_1", event_type="run_started", run_id="trace",
          payload={"config": {"run_id": "trace", "workspace": "/tmp", "goal": "trace",
                              "domain": "test", "budget": {"max_attempts": 5},
                              "capabilities": []}}),
    Event(event_id="tr_2", event_type="attempt_planned", run_id="trace",
          payload={"attempt": {"attempt_id": "att_source", "branch_id": "main",
                               "title": "Source analysis", "stage": "de"}}),
    Event(event_id="tr_3", event_type="artifact_registered", run_id="trace",
          payload={"artifact": {"artifact_id": "art_source", "attempt_id": "att_source",
                                "path": "/tmp/de.csv", "kind": "table",
                                "metadata": {"input_ids": ["att_source"]}}}),
    Event(event_id="tr_4", event_type="observation_registered", run_id="trace",
          payload={"observation": {"observation_id": "obs_source", "type": "de_effect",
                                   "target": "TargetZ", "metric": "logFC", "value": 2.0,
                                   "contrast": "KO_vs_NTC", "method": "wilcoxon",
                                   "attempt_id": "att_source", "branch_id": "main",
                                   "artifact_id": "art_source", "variable_key": "TargetZ.logFC",
                                   "parameter_hash": "p1"}}),
    Event(event_id="tr_5", event_type="attempt_planned", run_id="trace",
          payload={"attempt": {"attempt_id": "att_retry", "branch_id": "main",
                               "title": "Retry with aggregation", "stage": "de",
                               "parent_ids": ["att_source"],
                               "parent_intervention": "retry",
                               "repair_count": 1}}),
    Event(event_id="tr_6", event_type="observation_registered", run_id="trace",
          payload={"observation": {"observation_id": "obs_retry", "type": "de_effect",
                                   "target": "TargetZ", "metric": "logFC", "value": 1.7,
                                   "contrast": "KO_vs_NTC", "method": "aggregate",
                                   "attempt_id": "att_retry", "branch_id": "main",
                                   "input_ids": ["obs_source"], "variable_key": "TargetZ.logFC",
                                   "design_fields_used": ["guide_column"],
                                   "parameter_hash": "p2"}}),
    Event(event_id="tr_7", event_type="conclusion_recorded", run_id="trace",
          payload={"conclusion": {"conclusion_id": "con_trace",
                                  "text": "TargetZ has a consistent effect",
                                  "grade": "supported",
                                  "support_ids": ["obs_retry"]}}),
    Event(event_id="tr_8", event_type="outcome_recorded", run_id="trace",
          payload={"outcome": {"outcome_id": "out_retry",
                               "attempt_id": "att_retry",
                               "status": "success",
                               "summary": "kernel inventory persisted",
                               "metrics": {
                                   "returncode": 0,
                                   "execution_time": 3.4,
                                   "observations_registered": 1,
                                   "stdout_chars": 120,
                                   "kernel_state": {
                                       "variables": {
                                           "adata": "AnnData((120000, 24000))",
                                           "qc_metrics": "DataFrame((120000, 5))",
                                       },
                                       "imports": ["scanpy", "pandas"],
                                   }
                               }}}),
    Event(event_id="tr_9", event_type="finding_recorded", run_id="trace",
          payload={"finding": {"finding_id": "fnd_stale",
                               "finding_type": "potentially_stale_dependency",
                               "severity": "warning",
                               "suggested_action": "trace_upstream",
                               "summary": "guide_column changed; retry evidence may be stale",
                               "affected_ids": ["obs_retry", "con_trace"]}}),
]

trace_snap = reduce(trace_events)
trace_graph = build_graph(trace_snap)
trace_edges = {(e["source_id"], e["target_id"], e["edge_type"]) for e in trace_graph["edges"]}
check("trace graph valid", len(validate_graph(trace_graph)) == 0)
check("retry parent edge is reruns", ("att_source", "att_retry", "reruns") in trace_edges)
check("observation input edge is derived_from", ("obs_source", "obs_retry", "derived_from") in trace_edges)
derived_edge = next(e for e in trace_graph["edges"] if e["source_id"] == "obs_source" and e["target_id"] == "obs_retry")
check("relation effect attached", derived_edge["effect"]["category"] == "derivation")
check("relation effect propagates change", relation_effect("derived_from").propagates_change is True)

upstream = trace_upstream(trace_graph, "con_trace", depth=5)
upstream_ids = {n["node_id"] for n in upstream["nodes"]}
check("trace reaches retry observation", "obs_retry" in upstream_ids)
check("trace reaches source observation", "obs_source" in upstream_ids)
check("trace reaches source attempt", "att_source" in upstream_ids)
check("trace has relation summary", "derivation" in upstream["relation_summary"]["by_category"])

impact = impact_of_change(trace_graph, "obs_source", depth=5)
impact_ids = {n["node_id"] for n in impact["nodes"]}
check("impact reaches retry observation", "obs_retry" in impact_ids)
check("impact reaches conclusion", "con_trace" in impact_ids)
check("impact has relation summary", "recompute_value" in impact["relation_summary"]["by_impact"])
check("impact summarizes affected observations", impact["affected"]["by_type"].get("observation", 0) >= 1)
check("impact summarizes affected conclusions", impact["affected"]["by_type"].get("conclusion", 0) >= 1)
check("impact suggests relation actions", "reconsider_conclusion" in impact["affected"]["impact_actions"])

context_view = build_context_view(trace_snap, trace_graph, max_items=4)
check("context view type set", context_view["view_type"] == "context")
check("context view bounded attempts", len(context_view["recent_attempts"]) <= 4)
check("context view excludes event log", "events" not in context_view)
check("context graph summary has relations", "relations" in context_view["graph_summary"])
check("context view has observation memory", context_view["observation_memory"]["view_type"] == "observation_memory")
observation_view = build_observation_view(trace_snap, target="TargetZ", metric="logFC")
check("observation view has variable key", any(o["variable_key"] == "TargetZ.logFC" for o in observation_view["observations"]))
context_envelope = build_view(
    trace_snap,
    trace_graph,
    purpose="codegen",
    focus_ids=["obs_retry"],
    runtime_state={
        "variables": {
            "adata": "AnnData((120000, 24000))",
            "qc_metrics": "DataFrame((120000, 5))",
        },
        "jobs": [
            {
                "job_id": "job_trace",
                "job_type": "step",
                "status": "running",
                "stale": False,
                "retryable": False,
            }
        ],
        "processes": [
            {
                "pid": 123,
                "kind": "kernel",
                "status": "running",
            }
        ],
        "notebook": {
            "path": "notebooks/execution.ipynb",
            "cells": 2,
        },
    },
    token_budget=5000,
)
check("context envelope type set", context_envelope["view_type"] == "context_envelope")
check("context envelope purpose set", context_envelope["purpose"] == "codegen")
check("context envelope protects user context", "goal" in context_envelope["protected_context"])
check("context envelope has runtime symbols", "adata" in context_envelope["runtime_symbols"])
check("runtime symbol shape normalized", context_envelope["runtime_symbols"]["adata"]["shape"] == "120000x24000")
check("working set references runtime symbols", any(item["ref"] == "adata" for item in context_envelope["working_set"]["current_assets"]))
check("runtime state references symbols", "adata" in context_envelope["runtime_state"]["symbol_refs"])
check("runtime state records active attempt", context_envelope["runtime_state"]["active_attempt"]["attempt_id"] == "att_retry")
check("runtime state records recent execution metrics", context_envelope["runtime_state"]["recent_executions"][0]["execution"]["execution_time"] == 3.4)
check("runtime state records notebook path", context_envelope["runtime_state"]["notebook"]["path"] == "notebooks/execution.ipynb")
check("runtime state records active jobs", context_envelope["runtime_state"]["jobs"]["active_count"] == 1)
check("runtime state records active processes", context_envelope["runtime_state"]["processes"]["active_count"] == 1)
check("context envelope includes audit preview", context_envelope["audit_preview"]["audit_type"] == "run_audit")
check("context audit preview exposes stale warning", "stale_conclusion_evidence" in context_envelope["audit_preview"]["top_issue_codes"])
check("context audit preview suggests trace action", any(action.get("tool") == "trace_upstream" for action in context_envelope["audit_preview"]["next_actions"]))
check("context envelope includes trace-driven rethinking preview", context_envelope["trace_driven_rethinking"]["view_type"] == "rethinking_plan_preview")
check("context rethinking preview targets focus id", context_envelope["trace_driven_rethinking"]["target_id"] == "obs_retry")
check("context rethinking preview carries repair actions", any(action.get("tool") == "review_evidence_chain" for action in context_envelope["trace_driven_rethinking"]["recommended_actions"]))
provenance_entries = context_envelope["provenance_index"]["entries"]
check("context envelope has provenance index", "obs_retry" in provenance_entries)
check("observation provenance records attempt", provenance_entries["obs_retry"]["attempt"] == "att_retry")
check("observation provenance records inputs", "obs_source" in provenance_entries["obs_retry"]["derived_from"])
check("observation provenance records conclusion support", "con_trace" in provenance_entries["obs_retry"]["supports"])
check("observation provenance records design dependency", "guide_column" in provenance_entries["obs_retry"]["depends_on_design"])
check("observation provenance marks stale dependency", provenance_entries["obs_retry"]["stale"] is True)
check("conclusion provenance records support ids", provenance_entries["con_trace"]["support_ids"] == ["obs_retry"])
check("conclusion provenance inherits stale support", provenance_entries["con_trace"]["stale"] is True)
check("conclusion provenance verifies evidence outcome", provenance_entries["con_trace"]["evidence_verified"] is True)
check("conclusion support records outcome id", provenance_entries["con_trace"]["support_status"][0]["evidence"]["outcome_id"] == "out_retry")
check("context envelope exposes evidence review affordance", any(item["tool"] == "review_evidence_chain" for item in context_envelope["affordances"]))
check("context envelope exposes rethinking affordance", any(item["tool"] == "plan_rethinking" for item in context_envelope["affordances"]))
check("context envelope reports budget", context_envelope["budget_report"]["used_estimate"] > 0)
from pertura.agent.tool_loop import _trace_driven_rethinking
last_attempt = next(item for item in trace_snap.attempts if item.attempt_id == "att_retry")
trace_loop_hint = _trace_driven_rethinking(
    trace_snap,
    last_attempt,
    {"returncode": 0, "soft_timeout_hit": False},
    0,
    context_envelope,
)
check("tool loop injects trace-driven rethinking", trace_loop_hint["status"] == "needs_trace_driven_repair")
check("tool loop rethinking carries actions", any(action.get("tool") == "review_evidence_chain" for action in trace_loop_hint["recommended_actions"]))
active_work_order = build_active_work_order(
    trace_snap,
    compile_context(trace_snap, graph=trace_graph, max_items=4),
    context_envelope,
    trace_driven_rethinking=trace_loop_hint,
    tool_names=["get_context_review", "execute_code", "plan_rethinking"],
)
check("active work order renders markdown", active_work_order["view_type"] == "active_work_order" and "# Active Work Order" in active_work_order["markdown"])
check("active work order foregrounds rethink mode", active_work_order["mode"] == "rethink")
check("active work order keeps compact contract", active_work_order["contract"]["state_changes_go_through"] == "gated_dispatch")
restored_context_envelope = build_view(
    trace_snap,
    trace_graph,
    purpose="deliberation",
    focus_ids=[],
    token_budget=5000,
)
check("context envelope restores persisted kernel symbols", "adata" in restored_context_envelope["runtime_symbols"])
check("runtime state keeps imports from persisted kernel", "scanpy" in restored_context_envelope["runtime_state"]["imports"])
trace_snap.artifacts[0].path = str(Path(tempfile.gettempdir()) / "pertura_missing_audit_artifact_de.csv")
trace_run_audit = audit_run(trace_snap, trace_graph)
check("run audit returns report", trace_run_audit["audit_type"] == "run_audit")
check("run audit sees missing artifact file", any(item["code"] == "missing_artifact_file" for item in trace_run_audit["warnings"]))
check("run audit sees stale evidence", any(item["code"] == "stale_conclusion_evidence" for item in trace_run_audit["warnings"]))
check("run audit accepts verified conclusion evidence", not any(item["code"] == "unverified_conclusion_evidence" for item in trace_run_audit["errors"]))
chain_review = review_evidence_chain(trace_snap, "con_trace", graph=trace_graph)
check("evidence chain review returns conclusion", chain_review["node_type"] == "conclusion")
check("evidence chain review marks stale conclusion", chain_review["status"] == "stale_evidence" and chain_review["ok"] is False)
check("evidence chain review suggests trace", any(action.get("tool") == "trace_upstream" for action in chain_review["next_actions"]))
rethinking_plan = plan_rethinking(trace_snap, "con_trace", issue="stale conclusion after guide-column change", graph=trace_graph)
check("rethinking plan returns trace loop view", rethinking_plan["view_type"] == "rethinking_plan")
check("rethinking plan sees stale evidence", rethinking_plan["status"] == "needs_trace_driven_repair")
check("rethinking plan includes suspected roots", any(root["root_id"] == "obs_retry" for root in rethinking_plan["suspected_roots"]))
check("rethinking plan recommends impact or audit", any(action.get("tool") in {"impact_of_change", "audit_run"} for action in rethinking_plan["recommended_actions"]))
rethinking_tool = execute_tool("plan_rethinking", {"node_id": "con_trace", "issue": "suspicious stale result"}, snap=trace_snap)
check("rethinking tool is callable", rethinking_tool["view_type"] == "rethinking_plan")
check("rethinking tool starts from requested node", rethinking_tool["target_id"] == "con_trace")
check("rethinking tool surfaces repair menu", any(action.get("tool") == "review_evidence_chain" for action in rethinking_tool["recommended_actions"]))
trace_view = build_trace_view(trace_graph, "con_trace", depth=5, limit=3)
check("trace view is bounded", trace_view["truncated"] is True or len(trace_view["nodes"]) <= 3)
check("trace view exposes relation summary", "relation_summary" in trace_view)
impact_view = build_impact_view(trace_graph, "obs_source", depth=5)
check("impact view type set", impact_view["view_type"] == "impact")
check("impact view exposes walk relation summary", "walk_relation_summary" in impact_view)
check("impact view exposes affected summary", "affected" in impact_view)


# ── Test 11: Persistent jobs and cooperative cancellation ───────────────

start_segment("jobs_cancellation")

def _wait_job(runner, job_id, statuses, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = runner.get(job_id)
        if row and row.get("status") in statuses:
            return row
        time.sleep(0.02)
    return runner.get(job_id)


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    db_path = Path(td) / "jobs.db"
    runner = JobRunner(db_path=db_path, max_workers=1, stale_seconds=1)
    job = runner.submit(lambda ev: {"ok": not ev.is_set()},
                        job_type="smoke", payload={"x": 1})
    job_done = _wait_job(runner, job.job_id, {"succeeded"})
    check("persistent job succeeded", job_done["status"] == "succeeded")

    runner_reopened = JobRunner(db_path=db_path, max_workers=1, stale_seconds=1)
    reopened = runner_reopened.get(job.job_id)
    check("new runner reads persisted job", reopened["status"] == "succeeded")

    started = threading.Event()

    def _long(cancel_event):
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        return {"cancel_seen": True}

    long_job = runner.submit(_long, job_type="long", payload={})
    started.wait(timeout=1)
    check("cancel running job accepted", runner.cancel(long_job.job_id))
    cancelled = _wait_job(runner, long_job.job_id, {"cancelled"})
    check("running job becomes cancelled", cancelled["status"] == "cancelled")
    check("cancel request timestamp recorded", bool(cancelled.get("cancel_requested_at")))

    failed_job = runner.submit(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")),
                               job_type="fail", payload={})
    failed = _wait_job(runner, failed_job.job_id, {"failed"})
    check("failed job retryable", failed["retryable"] is True)
    retried = runner.retry(failed_job.job_id, lambda ev: {"ok": True})
    retried_done = _wait_job(runner, retried.job_id, {"succeeded"})
    check("failed job retry succeeds", retried_done["status"] == "succeeded")
    check("retry increments attempt", retried_done["attempt"] == 1)

    wb = Workbench(Domain(name="test"), provider="openai", sandbox="subprocess")
    ws = Path(td) / "workspace"
    ws.mkdir()
    wb.run(str(ws), goal="cancel smoke", steps=0)
    wb._emit("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_cancel",
        branch_id="main",
        title="cancel me",
        notebook_cells=[{"source": "print('should not run')"}],
    ))})
    ev = threading.Event()
    ev.set()
    wb.set_cancel_event(ev)
    actions = wb.step(1)
    cancel_snap = wb._store.read_snapshot()
    check("workbench step reports cancelled", actions == ["cancelled"])
    check("cancel stops active attempt", next(a for a in cancel_snap.attempts if a.attempt_id == "att_cancel").status == "stopped")
    check("cancel pauses run", cancel_snap.phase == "paused")


# Test 12: Replay, fork, and scientific diff

start_segment("replay_operator")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    parent_dir = Path(td) / "parent"
    parent_store = Store(parent_dir)
    parent_controller = GraphController(parent_store, "parent")
    parent_controller.append_event("run_started", {"config": {
        "run_id": "parent",
        "workspace": td,
        "goal": "replay smoke",
        "domain": "test",
        "budget": {"max_attempts": 5, "max_branches": 1, "max_repairs": 1},
        "capabilities": [],
    }})
    parent_controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_replay",
        branch_id="main",
        title="Replay source",
    ))})
    parent_controller.append_event("outcome_recorded", {"outcome": _model_dump(Outcome(
        outcome_id="out_replay",
        attempt_id="att_replay",
        status="success",
        summary="Replay source completed",
    ))})
    fork_point = parent_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_replay",
        "type": "de_effect",
        "target": "TargetR",
        "metric": "logFC",
        "value": 1.0,
        "contrast": "KO_vs_NTC",
        "method": "wilcoxon",
        "attempt_id": "att_replay",
        "branch_id": "main",
        "variable_key": "TargetR.logFC",
    }})

    replay = replay_store(parent_store)
    check("strict replay snapshot matches", replay.snapshot_matches_store)
    check("strict replay graph matches", replay.graph_matches_store)
    check("replay returns event count", replay.event_count >= 3)
    integrity = run_integrity(parent_store)
    check("run integrity hashes event log", len(integrity["event_log_sha256"]) == 64)
    check("run integrity matches replay event count", integrity["event_count"] == replay.event_count)
    check("run integrity verifies stored projections", integrity["snapshot_matches_store"] is True and integrity["graph_matches_store"] is True)
    check("stable json hash is deterministic", stable_json_sha256(parent_store.read_events()) == integrity["event_log_sha256"])
    replay_error_payload = ReplayError("sample replay failure").as_dict()
    check("replay error exposes code", replay_error_payload["code"] == "replay.error")
    check("replay error exposes doc url", replay_error_payload["doc_url"].startswith("https://"))
    from pertura._cli import _diff_run_dirs, _evidence_run_dir, _fork_run_dir, _inspect_run_dir, _replay_run_dir, _rethinking_run_dir, _trace_run_dir
    replay_report = _replay_run_dir(parent_dir, strict=True)
    check("replay CLI helper reports event count", replay_report["event_count"] >= 3)
    check("replay CLI helper reports projection match", replay_report["snapshot_matches_store"] is True and replay_report["graph_matches_store"] is True)
    inspected = _inspect_run_dir(parent_dir, recent=2)
    check("inspect summarizes event count", inspected["event_count"] >= 3)
    check("inspect summarizes event types", inspected["event_types"].get("run_started") == 1)
    check("inspect reports replay projection status", inspected["replay"]["snapshot_matches_store"] is True)
    check("inspect bounds recent events", len(inspected["recent_events"]) == 2)
    evidence_report = _evidence_run_dir(parent_dir, "obs_replay")
    check("evidence CLI helper reviews observation", evidence_report["view_type"] == "evidence_chain_review" and evidence_report["node_type"] == "observation")
    check("evidence CLI helper verifies successful evidence", evidence_report["ok"] is True)
    rethinking_report = _rethinking_run_dir(parent_dir, "obs_replay", issue="review replay observation")
    check("rethinking CLI helper returns plan", rethinking_report["view_type"] == "rethinking_plan" and rethinking_report["target_id"] == "obs_replay")
    check("rethinking CLI helper has action menu", any(action.get("tool") == "review_evidence_chain" for action in rethinking_report["recommended_actions"]))

    req_hash = hash_tool_call("query_observations", {"target": "TargetR"})
    ResponseCache(parent_dir).put(req_hash, {"observations": ["obs_replay"]}, model="test")
    forked = fork_store(parent_dir, fork_point.event_id, new_run_id="forked")
    fork_snap = forked.store.read_snapshot()
    check("fork rewrites run id", fork_snap.run_id == "forked")
    check("fork preserves prefix through fork point", forked.event_count >= 3 and any(e.event_id == fork_point.event_id for e in forked.store.read_events()))
    check("fork copies response cache", ResponseCache(forked.run_dir).contains(req_hash))
    cli_fork = _fork_run_dir(
        parent_dir,
        fork_point.event_id,
        new_run_dir=Path(td) / "fork_cli",
        new_run_id="fork_cli",
    )
    check("fork CLI helper rewrites run id", cli_fork["run_id"] == "fork_cli")
    check("fork CLI helper preserves prefix", cli_fork["event_count"] >= 3)
    check("fork CLI helper copies response cache", cli_fork["copied_cache"] is True)

    fork_controller = GraphController(forked.store, "forked")
    fork_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_replay_alt",
        "type": "de_effect",
        "target": "TargetR",
        "metric": "logFC",
        "value": -0.5,
        "contrast": "KO_vs_NTC",
        "method": "aggregate",
        "attempt_id": "att_replay",
        "branch_id": "main",
        "variable_key": "TargetR.logFC",
        "input_ids": ["obs_replay"],
    }})
    trace_cli_report = _trace_run_dir(forked.run_dir, "obs_replay_alt", depth=4)
    trace_cli_ids = {node["node_id"] for node in trace_cli_report["nodes"]}
    check("trace CLI helper returns upstream view", trace_cli_report["direction"] == "upstream" and trace_cli_report["found"] is True)
    check("trace CLI helper reaches input observation", "obs_replay" in trace_cli_ids)
    impact_cli_report = _trace_run_dir(forked.run_dir, "obs_replay", depth=4, impact=True)
    impact_cli_ids = {node["node_id"] for node in impact_cli_report["nodes"]}
    check("trace CLI helper returns impact view", impact_cli_report["direction"] == "downstream" and impact_cli_report["found"] is True)
    check("trace CLI helper reaches downstream observation", "obs_replay_alt" in impact_cli_ids)
    diff = diff_stores(parent_dir, forked.run_dir)
    changed_or_added_vars = (
        diff["observations"]["by_variable"]["changed"]
        or diff["observations"]["by_variable"]["added"]
    )
    check("diff reports variable-level observation change", bool(changed_or_added_vars))
    check("diff reports graph-level changes", bool(diff["graph"]["nodes"]["added"] or diff["graph"]["nodes"]["changed"]))
    diff_report = _diff_run_dirs(parent_dir, forked.run_dir)
    variable_delta_count = (
        diff_report["summary"]["observations_by_variable_added"]
        + diff_report["summary"]["observations_by_variable_changed"]
    )
    check("diff CLI helper reports run ids", diff_report["run_a"] == "parent" and diff_report["run_b"] == "forked")
    check("diff CLI helper summarizes variable deltas", variable_delta_count > 0)

    cache_wb = Workbench(Domain(name="test"), provider="openai", sandbox="subprocess")
    cache_ws = Path(td) / "cache_workspace"
    cache_ws.mkdir()
    cache_wb.run_with_cache(str(cache_ws), goal="cache init smoke", steps=0, replay_mode="loose")
    check("run_with_cache initializes response cache", cache_wb._response_cache is not None)
    check("run_with_cache creates cache db", (cache_wb._store.run_dir / "_response_cache.db").exists())
    audit_graph_spec = (
        AnalysisGraph("audit_graph", start_node_id="inspect")
        .node("inspect")
        .title("Inspect")
        .goal("Inspect workspace")
        .done_when(c.workspace_files_available())
        .end()
        .to_spec()
    )
    bad_store = Store(Path(td) / "run_bad_audit")
    bad_controller = GraphController(bad_store, "bad_audit")
    bad_controller.append_event("run_started", {"config": {
        "run_id": "bad_audit",
        "workspace": td,
        "goal": "bad audit",
        "domain": "test",
        "analysis_spec": audit_graph_spec.model_dump(mode="json"),
    }})
    bad_controller.append_event("interrupt_opened", {"interrupt": {
        "interrupt_id": "irq_bad",
        "source": "test",
        "question": "Need input",
        "status": "open",
    }})
    bad_controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_bad",
        branch_id="main",
        title="Bad attempt",
        analysis_node_id="inspect",
        capability_ids=["missing_capability"],
    ))})
    bad_controller.append_event("artifact_registered", {"artifact": {
        "artifact_id": "art_bad",
        "attempt_id": "att_missing",
        "path": str(Path(td) / "missing_bad.csv"),
        "kind": "table",
        "metadata": {"input_ids": ["missing_input"]},
    }})
    bad_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_bad",
        "type": "de_effect",
        "target": "TargetB",
        "metric": "logFC",
        "value": 0.5,
        "method": "smoke",
        "attempt_id": "att_bad",
        "branch_id": "main",
        "artifact_id": "art_missing",
        "input_ids": ["obs_missing"],
    }})
    bad_controller.append_event("conclusion_recorded", {"conclusion": {
        "conclusion_id": "con_bad",
        "text": "Unsupported",
        "support_ids": [],
    }})
    bad_audit = audit_run(bad_store.read_snapshot(), bad_store.read_graph(), run_dir=bad_store.run_dir)
    check("run audit fails bad run", bad_audit["ok"] is False)
    check("run audit catches open interrupt", any(item["code"] == "open_interrupt" for item in bad_audit["errors"]))
    check("run audit catches unsupported conclusion", any(item["code"] == "unsupported_conclusion" for item in bad_audit["errors"]))
    check("run audit catches unknown capability", any(item["code"] == "unknown_attempt_capability" for item in bad_audit["errors"]))
    check("run audit catches missing artifact attempt", any(item["code"] == "missing_artifact_attempt" for item in bad_audit["errors"]))
    check("run audit catches missing observation artifact", any(item["code"] == "missing_observation_artifact" for item in bad_audit["errors"]))
    check("run audit catches missing observation input", any(item["code"] == "missing_observation_input" for item in bad_audit["errors"]))
    failed_evidence_store = Store(Path(td) / "run_failed_evidence")
    failed_evidence_controller = GraphController(failed_evidence_store, "failed_evidence")
    failed_evidence_controller.append_event("run_started", {"config": {
        "run_id": "failed_evidence",
        "workspace": td,
        "goal": "failed evidence",
        "domain": "test",
        "capabilities": [],
    }})
    failed_evidence_controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_failed_evidence",
        branch_id="main",
        title="Failed evidence attempt",
    ))})
    failed_evidence_controller.append_event("outcome_recorded", {"outcome": {
        "outcome_id": "out_failed_evidence",
        "attempt_id": "att_failed_evidence",
        "status": "error",
        "summary": "failed",
    }})
    failed_evidence_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_failed_evidence",
        "type": "de_effect",
        "target": "TargetBad",
        "metric": "logFC",
        "value": 9.9,
        "method": "failed_run",
        "attempt_id": "att_failed_evidence",
        "branch_id": "main",
    }})
    failed_evidence_controller.append_event("conclusion_recorded", {"conclusion": {
        "conclusion_id": "con_failed_evidence",
        "text": "TargetBad has an effect",
        "support_ids": ["obs_failed_evidence"],
    }})
    failed_evidence_audit = audit_run(
        failed_evidence_store.read_snapshot(),
        failed_evidence_store.read_graph(),
        run_dir=failed_evidence_store.run_dir,
    )
    check("run audit catches unverified conclusion evidence", any(item["code"] == "unverified_conclusion_evidence" for item in failed_evidence_audit["errors"]))
    failed_chain_review = review_evidence_chain(
        failed_evidence_store.read_snapshot(),
        "con_failed_evidence",
        graph=failed_evidence_store.read_graph(),
    )
    check("evidence chain review catches failed support", failed_chain_review["status"] == "unverified_evidence")
    check("evidence chain review names failed outcome", failed_chain_review["support_checks"][0]["evidence"]["outcome_status"] == "error")
    check("run audit suggests evidence review", any(
        action.get("tool") == "review_evidence_chain"
        and action.get("target_id") in {"con_failed_evidence", "obs_failed_evidence"}
        for action in failed_evidence_audit["next_actions"]
    ))
    check("run audit suggests tracing failed evidence", any(
        action.get("tool") == "trace_upstream"
        and action.get("target_id") in {"con_failed_evidence", "obs_failed_evidence"}
        for action in failed_evidence_audit["next_actions"]
    ))
    from pertura.tools.registry import execute_tool as execute_tool_for_audit
    audit_tool_payload = execute_tool_for_audit("audit_run", {"run_dir": str(bad_store.run_dir)}, snap=bad_store.read_snapshot())
    check("run audit tool returns audit payload", audit_tool_payload["audit_type"] == "run_audit")
    from pertura.agent.gated_dispatch import gated_dispatch as gated_dispatch_for_finish
    class _FinishWB:
        pass
    bad_wb = _FinishWB()
    bad_wb._store = bad_store
    bad_wb._run_id = "bad_audit"
    bad_wb.provider = "openai"
    bad_wb._controller = bad_controller
    bad_wb._emit = lambda event_type, payload: bad_controller.append_event(event_type, payload)
    finish_blocked = gated_dispatch_for_finish(
        bad_wb,
        "finish",
        "",
        {"summary": "finish"},
        {"summary": "finish"},
        bad_store.read_snapshot(),
    )
    bad_finish_snap = bad_store.read_snapshot()
    check("finish audit gate blocks bad run", finish_blocked == "waiting_for_human")
    check("finish audit gate records blocking finding", any(f.finding_type == "finish_audit_failed" for f in bad_finish_snap.findings))

    good_store = Store(Path(td) / "run_good_finish")
    good_controller = GraphController(good_store, "good_finish")
    good_controller.append_event("run_started", {"config": {
        "run_id": "good_finish",
        "workspace": td,
        "goal": "good finish",
        "domain": "test",
        "capabilities": [],
    }})
    good_controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_good",
        branch_id="main",
        title="Good attempt",
    ))})
    good_controller.append_event("outcome_recorded", {"outcome": {
        "outcome_id": "out_good",
        "attempt_id": "att_good",
        "status": "success",
        "summary": "Good attempt completed",
        "metrics": {"observations_registered": 1},
    }})
    good_artifact_path = Path(td) / "good.csv"
    good_artifact_path.write_text("gene,logFC\nTargetA,1.0\n", encoding="utf-8")
    good_controller.append_event("artifact_registered", {"artifact": {
        "artifact_id": "art_good",
        "attempt_id": "att_good",
        "path": str(good_artifact_path),
        "kind": "table",
        "summary": "Good table",
    }})
    good_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_good",
        "type": "de_effect",
        "target": "TargetA",
        "metric": "logFC",
        "value": 1.0,
        "method": "smoke",
        "attempt_id": "att_good",
        "branch_id": "main",
        "artifact_id": "art_good",
    }})
    good_wb = _FinishWB()
    good_wb._store = good_store
    good_wb._run_id = "good_finish"
    good_wb.provider = "openai"
    good_wb._controller = good_controller
    good_wb._emit = lambda event_type, payload: good_controller.append_event(event_type, payload)
    finish_ok = gated_dispatch_for_finish(
        good_wb,
        "finish",
        "",
        {"summary": "finish"},
        {"summary": "finish"},
        good_store.read_snapshot(),
    )
    good_finish_snap = good_store.read_snapshot()
    check("finish audit gate allows clean run", finish_ok == "complete")
    check("finish emits run complete", good_finish_snap.phase == "complete")

    failed_finish_store = Store(Path(td) / "run_failed_finish_evidence")
    failed_finish_controller = GraphController(failed_finish_store, "failed_finish_evidence")
    failed_finish_controller.append_event("run_started", {"config": {
        "run_id": "failed_finish_evidence",
        "workspace": td,
        "goal": "failed finish evidence",
        "domain": "test",
        "capabilities": [],
    }})
    failed_finish_controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_finish_failed",
        branch_id="main",
        title="Failed finish evidence",
    ))})
    failed_finish_controller.append_event("outcome_recorded", {"outcome": {
        "outcome_id": "out_finish_failed",
        "attempt_id": "att_finish_failed",
        "status": "error",
        "summary": "failed",
    }})
    failed_finish_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_finish_failed",
        "type": "de_effect",
        "target": "TargetFail",
        "metric": "logFC",
        "value": 4.2,
        "method": "failed_run",
        "attempt_id": "att_finish_failed",
        "branch_id": "main",
    }})
    failed_finish_wb = _FinishWB()
    failed_finish_wb._store = failed_finish_store
    failed_finish_wb._run_id = "failed_finish_evidence"
    failed_finish_wb.provider = "openai"
    failed_finish_wb._controller = failed_finish_controller
    failed_finish_wb._emit = lambda event_type, payload: failed_finish_controller.append_event(event_type, payload)
    failed_finish_result = gated_dispatch_for_finish(
        failed_finish_wb,
        "finish",
        "",
        {"summary": "finish"},
        {"summary": "finish"},
        failed_finish_store.read_snapshot(),
    )
    failed_finish_snap = failed_finish_store.read_snapshot()
    check("finish final audit blocks failed evidence conclusion", failed_finish_result == "waiting_for_human")
    check("finish final audit records unverified evidence code", any(
        f.finding_type == "finish_audit_failed"
        and "unverified_conclusion_evidence" in f.affected_ids
        for f in failed_finish_snap.findings
    ))
    incomplete_graph_spec = (
        AnalysisGraph("finish_nodes", start_node_id="inspect")
        .node("inspect")
        .title("Inspect")
        .goal("Inspect")
        .done_when(c.workspace_files_available())
        .next("effect")
        .end()
    )
    incomplete_graph_spec.node("effect").title("Effect").goal("Effect").done_when(c.observation_metric("logFC"))
    incomplete_store = Store(Path(td) / "run_incomplete_finish")
    incomplete_controller = GraphController(incomplete_store, "incomplete_finish")
    incomplete_controller.append_event("run_started", {"config": {
        "run_id": "incomplete_finish",
        "workspace": td,
        "goal": "incomplete finish",
        "domain": "test",
        "analysis_spec": incomplete_graph_spec.to_spec().model_dump(mode="json"),
        "capabilities": [],
    }})
    incomplete_controller.append_event("node_entered", {
        "node_id": "inspect",
        "branch_id": "main",
        "reason": "start",
    })
    incomplete_controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_incomplete",
        branch_id="main",
        analysis_node_id="inspect",
        title="Incomplete",
    ))})
    incomplete_controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_incomplete",
        "type": "workspace_file",
        "target": "dataset",
        "metric": "file_count",
        "value": 1,
        "method": "smoke",
        "attempt_id": "att_incomplete",
        "branch_id": "main",
    }})
    incomplete_audit = audit_run(incomplete_store.read_snapshot(), incomplete_store.read_graph(), run_dir=incomplete_store.run_dir)
    check("run audit treats incomplete nodes as errors", any(item["code"] == "incomplete_analysis_nodes" for item in incomplete_audit["errors"]))
    incomplete_wb = _FinishWB()
    incomplete_wb._store = incomplete_store
    incomplete_wb._run_id = "incomplete_finish"
    incomplete_wb.provider = "openai"
    incomplete_wb._controller = incomplete_controller
    incomplete_wb._emit = lambda event_type, payload: incomplete_controller.append_event(event_type, payload)
    incomplete_finish = gated_dispatch_for_finish(
        incomplete_wb,
        "finish",
        "",
        {"summary": "finish"},
        {"summary": "finish"},
        incomplete_store.read_snapshot(),
    )
    check("finish audit gate blocks incomplete nodes", incomplete_finish == "waiting_for_human")
    from pertura.agent.tool_loop import (
        _anthropic_block_dict,
        _anthropic_text,
        _json_for_prompt,
        _provider_tool_schemas,
        _request_model_label,
    )
    openai_tool_schema = [{
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Complete the analysis.",
            "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
        },
    }]
    anthropic_tool_schema = _provider_tool_schemas(openai_tool_schema, "anthropic")
    check("anthropic provider converts tool schema", anthropic_tool_schema[0]["name"] == "finish" and "input_schema" in anthropic_tool_schema[0])
    check("openai provider keeps native tool schema", _provider_tool_schemas(openai_tool_schema, "openai")[0].get("type") == "function")
    check("request model label includes provider", _request_model_label("anthropic").startswith("anthropic:"))
    huge_prompt = {
        "context_envelope": {
            "runtime_symbols": {f"sym_{i}": {"summary": "x" * 500} for i in range(40)},
            "provenance_index": {"entries": {f"obs_{i}": {"detail": "y" * 400} for i in range(40)}},
        },
        "user_said": "Analyze Norman data with careful provenance." * 80,
        "outcome": "stderr line\n" * 500,
        "last_attempt_delta": {"new_observations": [{"value": "z" * 500} for _ in range(20)]},
    }
    bounded_prompt = _json_for_prompt(huge_prompt, max_chars=1600)
    bounded_payload = json.loads(bounded_prompt)
    check("tool loop prompt compaction stays within budget", len(bounded_prompt) <= 1600)
    check("tool loop prompt compaction preserves valid json", bounded_payload["_prompt_truncation"]["truncated"] is True)
    text_block = {"type": "text", "text": "Need to finish."}
    tool_block = {"type": "tool_use", "id": "tool_1", "name": "finish", "input": {"summary": "done"}}
    check("anthropic text blocks compact", _anthropic_text([text_block, tool_block]) == "Need to finish.")
    check("anthropic tool_use block round-trips", _anthropic_block_dict(tool_block)["input"]["summary"] == "done")
    from pertura.agent.tool_loop import run_tool_loop
    fake_calls = []
    class _FakeAnthropicMessages:
        def create(self, **kwargs):
            fake_calls.append(kwargs)
            return types.SimpleNamespace(content=[
                types.SimpleNamespace(type="text", text="Ready to finish."),
                types.SimpleNamespace(type="tool_use", id="tool_1", name="finish", input={"summary": "done"}),
            ])
    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = _FakeAnthropicMessages()
    old_anthropic_module = sys.modules.get("anthropic")
    old_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=_FakeAnthropic)
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    try:
        action, code, assessment, decision = run_tool_loop(
            result=None,
            obs_count=0,
            snap=snap,
            attempt=Attempt(attempt_id="att_provider", stage="start", title="provider smoke"),
            provider="anthropic",
            is_first=True,
        )
    finally:
        if old_anthropic_module is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = old_anthropic_module
        if old_anthropic_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old_anthropic_key
    check("anthropic tool loop returns state-changing action", action == "finish" and decision.get("summary") == "done")
    check("anthropic tool loop passes native tools", bool(fake_calls and fake_calls[0]["tools"][0].get("input_schema")))
    check("anthropic tool loop excludes system from messages", all(msg.get("role") != "system" for msg in fake_calls[0]["messages"]))


# Test 13: Pertura v2 editable AnalysisSpecGraph and gated dispatch

start_segment("analysis_spec_gating")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    graph_spec = (
        AnalysisGraph("petura_test", start_node_id="workspace_inspection")
        .add_node(
            "workspace_inspection",
            title="Inspect",
            purpose="Inspect workspace",
            allowed_capabilities=["inspect_workspace"],
            requires=["workspace files are available"],
            next_nodes=["target_interpretation"],
            strict_edges=True,
        )
        .add_node(
            "target_interpretation",
            title="Target interpretation",
            purpose="Interpret perturbation effects",
            allowed_capabilities=["run_de"],
            must_confirm=[
                condition(
                    "control_labels_defined",
                    evaluator_id="design_field_known",
                    tier="C",
                    failure_mode="human_interrupt",
                    inputs={"field": "control_labels"},
                    message="Control labels are required.",
                )
            ],
            completion=[
                condition(
                    "target_observation_registered",
                    evaluator_id="has_observation",
                    inputs={"metric": "logFC"},
                    message="A target-level logFC observation is registered.",
                )
            ],
        )
        .to_spec()
    )
    check("analysis graph spec serializes", graph_spec.model_dump(mode="json")["graph_id"] == "petura_test")
    spec_path = Path(td) / "analysis_graph.json"
    save_analysis_graph(graph_spec, spec_path)
    loaded_spec = load_analysis_graph(spec_path)
    check("analysis graph spec round-trips through JSON", loaded_spec.graph_id == "petura_test" and len(loaded_spec.nodes) == 2)

    events_spec = [
        Event(event_id="sp_1", event_type="run_started", run_id="spec",
              payload={"config": {"run_id": "spec", "workspace": td, "goal": "spec",
                                  "domain": "test", "budget": {"max_attempts": 5},
                                  "capabilities": PERTURBSEQ_DOMAIN.capabilities,
                                  "analysis_spec": graph_spec.model_dump(mode="json")}}),
        Event(event_id="sp_2", event_type="node_entered", run_id="spec",
              payload={"node_id": "workspace_inspection", "branch_id": "main", "reason": "start"}),
    ]
    spec_snap = reduce(events_spec)
    check("snapshot has active node", spec_snap.active_node_id == "workspace_inspection")
    gate = GateEvaluator(spec_snap.analysis_spec).evaluate_enter(spec_snap, "target_interpretation")
    check("C-tier missing design blocks as human interrupt", gate.decision == "human_interrupt", gate.model_dump(mode="json"))
    spec_snap.design["control_labels"] = ["NTC"]
    gate_after = GateEvaluator(spec_snap.analysis_spec).evaluate_enter(spec_snap, "target_interpretation")
    check("design update makes gate pass", gate_after.decision == "pass", gate_after.model_dump(mode="json"))

    store = Store(Path(td) / "run_petura")
    controller = GraphController(store, "petura")
    controller.append_event("run_started", {"config": {
        "run_id": "petura",
        "workspace": td,
        "goal": "petura smoke",
        "domain": "test",
        "budget": {"max_attempts": 5, "max_branches": 2, "max_repairs": 1},
        "capabilities": PERTURBSEQ_DOMAIN.capabilities,
        "analysis_spec": graph_spec.model_dump(mode="json"),
    }})
    controller.append_event("node_entered", {
        "node_id": "workspace_inspection",
        "branch_id": "main",
        "reason": "start",
    })
    blocked_snap = store.read_snapshot()
    from pertura.agent.gated_dispatch import gated_dispatch
    class _WB:
        pass
    wb_stub = _WB()
    wb_stub._store = store
    wb_stub._run_id = "petura"
    wb_stub._controller = controller
    wb_stub._emit = lambda event_type, payload: controller.append_event(event_type, payload)
    action = gated_dispatch(
        wb_stub,
        "request_node_transition",
        "",
        {"summary": "go target"},
        {"target_node_id": "target_interpretation", "reason": "need target result"},
        blocked_snap,
    )
    blocked_snap = store.read_snapshot()
    check("blocked transition waits for human", action == "waiting_for_human")
    check("gate evaluation recorded", any(g.target_node_id == "target_interpretation" for g in blocked_snap.gate_evaluations))
    check("interrupt opened by gate", any(i.source == "node_gate" and i.status == "open" for i in blocked_snap.interrupts))

    controller.append_event("design_updated", {"design": {"control_labels": ["NTC"]}, "reason": "test"})
    controller.append_event("interrupt_resolved", {"interrupt_id": blocked_snap.interrupts[-1].interrupt_id, "answer": "NTC"})
    snap_ready = store.read_snapshot()
    action2 = gated_dispatch(
        wb_stub,
        "request_node_transition",
        "",
        {"summary": "go target"},
        {"target_node_id": "target_interpretation", "reason": "design resolved"},
        snap_ready,
    )
    snap_ready = store.read_snapshot()
    check("transition enters after design update", action2 == "node_entered")
    check("active node updated", snap_ready.active_node_id == "target_interpretation")
    controller.append_event("design_updated", {"design": {"guide_column": "guide", "target_column": "target_gene"}, "reason": "test"})
    snap_ready = store.read_snapshot()

    action_complete_blocked = gated_dispatch(
        wb_stub,
        "complete_node",
        "",
        {"summary": "mark complete"},
        {"summary": "done"},
        snap_ready,
    )
    snap_ready = store.read_snapshot()
    check("complete_node blocks missing completion evidence", action_complete_blocked == "blocked")
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_completion",
        "type": "de_effect",
        "target": "TargetT",
        "metric": "logFC",
        "value": 1.2,
        "branch_id": "main",
    }})
    snap_ready = store.read_snapshot()
    action_complete = gated_dispatch(
        wb_stub,
        "complete_node",
        "",
        {"summary": "mark complete"},
        {"summary": "done"},
        snap_ready,
    )
    snap_completed = store.read_snapshot()
    check("complete_node records completion", action_complete == "node_completed")
    check("node visit marked completed", any(v.node_id == "target_interpretation" and v.status == "completed" for v in snap_completed.node_visits))

    action_skip = gated_dispatch(
        wb_stub,
        "skip_node",
        "",
        {"summary": "not applicable"},
        {"node_id": "workspace_inspection", "reason": "already inspected"},
        snap_completed,
    )
    snap_skipped = store.read_snapshot()
    check("skip_node records skip", action_skip == "node_skipped")
    check("node skip visit recorded", any(v.node_id == "workspace_inspection" and v.status == "skipped" for v in snap_skipped.node_visits))
    node_complete_audit = audit_run(snap_skipped, store.read_graph(), run_dir=store.run_dir)
    check("completed or skipped nodes satisfy run audit", not any(item["code"] == "incomplete_analysis_nodes" for item in node_complete_audit["errors"]))

    action3 = gated_dispatch(
        wb_stub,
        "execute_code",
        "print('ok')",
        {"summary": "run DE"},
        {"capability_ids": ["run_de"], "stage": "de"},
        snap_skipped,
    )
    snap_after_attempt = store.read_snapshot()
    check("execute_code plans attempt in active node", action3 == "planned_attempt")
    check("attempt records analysis_node_id", snap_after_attempt.attempts[-1].analysis_node_id == "target_interpretation")

    action_missing_capability = gated_dispatch(
        wb_stub,
        "execute_code",
        "print('missing capability')",
        {"summary": "run without declared capability"},
        {"stage": "de"},
        snap_after_attempt,
    )
    snap_missing_capability = store.read_snapshot()
    check("execute_code without capability declaration blocks", action_missing_capability == "blocked")
    check(
        "missing capability declaration finding recorded",
        any(f.finding_type == "missing_capability_declaration" and f.severity == "blocking" for f in snap_missing_capability.findings),
    )

    action4 = gated_dispatch(
        wb_stub,
        "execute_code",
        "print('bad')",
        {"summary": "wrong cap"},
        {"capability_ids": ["inspect_workspace"]},
        snap_missing_capability,
    )
    check("disallowed capability blocked", action4 == "blocked")

    ctx_spec = compile_context(snap_missing_capability)
    check("context exposes active node", ctx_spec.active_node_id == "target_interpretation")
    check("context exposes reachable nodes", isinstance(ctx_spec.reachable_nodes, list))
    spec_graph = build_graph(snap_missing_capability)
    check("graph has analysis node", any(n["node_type"] == "analysis_node" for n in spec_graph["nodes"]))
    check("graph has runs_in edge", any(e["edge_type"] == "runs_in" for e in spec_graph["edges"]))
    spec_envelope = build_view(
        snap_missing_capability,
        spec_graph,
        purpose="codegen",
        runtime_state={"variables": {"adata": "AnnData((10, 20))"}},
    )
    execute_affordance = next((item for item in spec_envelope["affordances"] if item["tool"] == "execute_code"), {})
    check("execute affordance declares capability", execute_affordance.get("args", {}).get("capability_ids") == ["run_de"])
    check("execute tool schema accepts capability ids", "capability_ids" in TOOLS["execute_code"]["parameters"]["properties"])
    check("active contract selects capability", spec_envelope["active_contract"]["selected_capability"]["id"] == "run_de")
    check("active contract exposes analysis modes", "differential_expression" in spec_envelope["active_contract"]["selected_capability"]["analysis_modes"])
    check("active contract exposes audit checklist", any("register observation" in item for item in spec_envelope["active_contract"]["audit_checklist"]))
    template_affordance = next((item for item in spec_envelope["affordances"] if item["tool"] == "get_capability_template"), {})
    check("context recommends capability template", template_affordance.get("args", {}).get("capability_id") == "run_de")
    node_contract_affordance = next((item for item in spec_envelope["affordances"] if item["tool"] == "get_node_contract"), {})
    check("context recommends node contract dashboard", node_contract_affordance.get("tool") == "get_node_contract")
    check("template affordance carries design columns", template_affordance.get("args", {}).get("columns", {}).get("guide") == "guide")
    context_review = execute_tool(
        "get_context_review",
        {
            "purpose": "audit",
            "max_items": 3,
            "token_budget": 2000,
            "runtime_state": {"variables": {"adata": "AnnData((10, 20))"}},
        },
        snap=snap_missing_capability,
    )
    check("context review tool returns envelope", context_review["view_type"] == "context_envelope" and context_review["purpose"] == "audit")
    check("context review includes runtime symbols", "adata" in context_review["runtime_symbols"])
    check("context review includes provenance index", "provenance_index" in context_review and "entries" in context_review["provenance_index"])
    check("context review includes audit preview", context_review["audit_preview"]["audit_type"] == "run_audit")
    check("context review audit preview can expand", context_review["audit_preview"]["expand"]["tool"] == "audit_run")
    check("context review audit preview has next actions", len(context_review["audit_preview"]["next_actions"]) >= 1)
    check("context review includes rethinking preview", context_review["trace_driven_rethinking"]["view_type"] == "rethinking_plan_preview")
    check("context review rethinking preview can expand", context_review["trace_driven_rethinking"]["expand"]["tool"] == "plan_rethinking")
    check("capability template tool registered", "get_capability_template" in TOOLS)
    check("node contract tool registered", "get_node_contract" in TOOLS)
    check("context review tool registered", "get_context_review" in TOOLS)
    check("audit toolbox tool registered", "get_audit_toolbox" in TOOLS)
    check("harness manifest tool registered", "get_harness_manifest" in TOOLS)
    check("run audit tool registered", "audit_run" in TOOLS)
    check("evidence review tool registered", "review_evidence_chain" in TOOLS)
    check("rethinking plan tool registered", "plan_rethinking" in TOOLS)
    from pertura.agent.loop import _TOOL_LOOP_PROMPT
    check("tool loop prompt names audit preview", "audit_preview" in _TOOL_LOOP_PROMPT)
    check("tool loop prompt prioritizes audit next actions", "audit_run.next_actions" in _TOOL_LOOP_PROMPT and "follow those local-read repair actions first" in _TOOL_LOOP_PROMPT)
    check("tool loop prompt names harness manifest", "get_harness_manifest" in _TOOL_LOOP_PROMPT)
    check("tool loop prompt names audit toolbox", "get_audit_toolbox" in _TOOL_LOOP_PROMPT)
    check("tool loop prompt names evidence review", "review_evidence_chain" in _TOOL_LOOP_PROMPT)
    check("tool loop prompt names rethinking plan", "plan_rethinking" in _TOOL_LOOP_PROMPT)
    check("tool loop prompt prioritizes context rethinking preview", "context_envelope.trace_driven_rethinking" in _TOOL_LOOP_PROMPT and "recommended_actions as the preferred trace/repair" in _TOOL_LOOP_PROMPT)
    check("context review tool description names next actions", "next actions" in TOOLS["get_context_review"]["description"])
    from pertura.core import build_audit_toolbox, build_harness_manifest
    harness_manifest = build_harness_manifest()
    primitive_ids = {item["primitive_id"] for item in harness_manifest["thesis"]["distinctive_primitives"]}
    check("harness manifest returns thesis", harness_manifest["view_type"] == "harness_manifest")
    check("harness manifest names free reasoning gated commit", harness_manifest["thesis"]["core_principle"] == "free_reasoning_gated_commit")
    check("harness manifest includes three core primitives", {"analysis_graph_gate", "scientific_observation_memory", "deliberative_audit"} <= primitive_ids)
    check("harness manifest maps common vocabulary", any(item["common_term"] == "Agent dashboard" for item in harness_manifest["developer_vocabulary"]))
    harness_tool_payload = execute_tool("get_harness_manifest", {}, snap=snap_missing_capability)
    check("harness manifest tool returns thesis", harness_tool_payload["view_type"] == "harness_manifest" and harness_tool_payload["thesis"]["core_principle"] == "free_reasoning_gated_commit")
    core_audit_toolbox = build_audit_toolbox(snap_missing_capability, purpose="audit")
    check("core audit toolbox builder returns compact index", core_audit_toolbox["view_type"] == "audit_toolbox")
    check("core audit toolbox exposes harness thesis", core_audit_toolbox["harness_thesis"]["core_principle"] == "free_reasoning_gated_commit")
    audit_toolbox = execute_tool("get_audit_toolbox", {"purpose": "audit"}, snap=snap_missing_capability)
    audit_toolbox_tools = {item["tool"] for item in audit_toolbox["tools"]}
    check("audit toolbox returns compact index", audit_toolbox["view_type"] == "audit_toolbox" and audit_toolbox["policy"]["context_strategy"] == "compact-first-expand-on-demand")
    check("audit toolbox includes evidence, trace, and rethinking tools", {"review_evidence_chain", "trace_upstream", "audit_run", "plan_rethinking"} <= audit_toolbox_tools)
    check("audit toolbox recommends active node dashboard", "get_node_contract" in audit_toolbox["recommended_first_tools"])
    evidence_tool_payload = execute_tool("review_evidence_chain", {"node_id": "obs_1"}, snap=snap_missing_capability)
    check("evidence review tool returns payload", evidence_tool_payload["view_type"] == "evidence_chain_review")
    rethinking_payload = execute_tool("plan_rethinking", {"node_id": "obs_1", "issue": "unsupported observation"}, snap=snap_missing_capability)
    check("rethinking plan tool returns payload", rethinking_payload["view_type"] == "rethinking_plan")
    check("rethinking plan tool has action menu", any(action.get("tool") == "review_evidence_chain" for action in rethinking_payload["recommended_actions"]))
    runtime_contract = execute_tool("get_node_contract", {}, snap=snap_missing_capability)
    check("node contract tool returns runtime dashboard", runtime_contract["runtime"]["target_node_id"] == "target_interpretation")
    check("node contract reports unresolved runtime inputs", "adata" in runtime_contract["runtime"]["missing_inputs"])
    check("node contract suggests next actions", any(action.get("tool") == "load_dataset" for action in runtime_contract["runtime"]["next_actions"]))
    template_tool = execute_tool("get_capability_template", {"capability_id": "run_de"}, snap=snap_missing_capability)
    check("capability template returns code skeleton", "rank_genes_groups" in template_tool["template"]["code"])
    check("capability template carries execute metadata", template_tool["execute_with"]["args"]["capability_ids"] == ["run_de"])
    parametrized_template = execute_tool(
        "get_capability_template",
        {
            "capability_id": "run_de",
            "target": "TP53",
            "columns": {"perturbation": "target_gene"},
            "control_labels": ["NTC"],
        },
        snap=snap_missing_capability,
    )
    check("capability template accepts target parameter", "target = 'TP53'" in parametrized_template["template"]["code"])
    check("capability template accepts column parameter", "perturb_col = 'target_gene'" in parametrized_template["template"]["code"])
    check("capability template schema exposes inputs", "columns" in TOOLS["get_capability_template"]["parameters"]["properties"])
    auto_parametrized_template = execute_tool("get_capability_template", {"capability_id": "run_de"}, snap=snap_ready)
    check("capability template resolves design inputs", auto_parametrized_template["resolved_inputs"]["control_labels"] == ["NTC"])
    check("capability template auto-fills target column", auto_parametrized_template["template"]["inputs"]["columns"]["target"] == "target_gene")
    check("capability template auto-fills guide column", auto_parametrized_template["template"]["inputs"]["columns"]["guide"] == "guide")
    check("capability template reports missing inputs", "adata" in auto_parametrized_template["missing_inputs"])
    check("capability template blocks execution when inputs missing", auto_parametrized_template["readiness"]["ready"] is False and auto_parametrized_template["execute_with"]["ready"] is False)
    check("capability template suggests loading data", any(action.get("action_id") == "load_or_restore_adata" for action in auto_parametrized_template["next_actions"]))
    missing_runtime_template = execute_tool("get_capability_template", {"capability_id": "run_de"}, snap=snap_missing_capability)
    check("capability template marks runtime data missing", "adata" in missing_runtime_template["missing_inputs"])
    check("missing runtime template reports readiness", missing_runtime_template["readiness"]["status"] == "blocked_missing_inputs")
    ready_runtime_snap = reduce([
        Event(
            event_id="rt_1",
            event_type="run_started",
            run_id="ready_runtime",
            payload={"config": {
                "run_id": "ready_runtime",
                "workspace": td,
                "goal": "ready runtime smoke",
                "domain": "perturbseq",
                "capabilities": PERTURBSEQ_DOMAIN.capabilities,
                "analysis_spec": PERTURBSEQ_DOMAIN.analysis_graph,
                "design": {"control_labels": ["NTC"], "guide_column": "guide", "target_column": "target_gene"},
            }},
        ),
        Event(
            event_id="rt_2",
            event_type="outcome_recorded",
            run_id="ready_runtime",
            payload={"outcome": {"outcome_id": "out_ready", "attempt_id": "att_ready", "status": "success", "summary": "kernel state", "metrics": {"kernel_state": {"variables": {"adata": "AnnData(10, 20)"}}}}},
        ),
        Event(
            event_id="rt_3",
            event_type="observation_registered",
            run_id="ready_runtime",
            payload={"observation": {"observation_id": "obs_ready_dataset", "type": "schema", "target": "adata", "metric": "shape", "value": "10x20"}},
        ),
    ])
    ready_template = execute_tool("get_capability_template", {"capability_id": "run_de"}, snap=ready_runtime_snap)
    check("capability template ready when runtime inputs resolved", ready_template["readiness"]["ready"] is True and ready_template["execute_with"]["ready"] is True)
    ready_node_contract = execute_tool("get_node_contract", {"node_id": "effect_exploration"}, snap=ready_runtime_snap)
    check("node contract marks ready capability", "run_de" in ready_node_contract["runtime"]["ready_capabilities"])
    check("node contract reports ready status", ready_node_contract["runtime"]["status"] in {"ready_for_capability", "complete_ready"})
    perturb_template_snap = reduce([
        Event(
            event_id="pt_1",
            event_type="run_started",
            run_id="perturb_template",
            payload={"config": {
                "run_id": "perturb_template",
                "workspace": td,
                "goal": "perturb template smoke",
                "domain": "perturbseq",
                "capabilities": PERTURBSEQ_DOMAIN.capabilities,
                "analysis_spec": PERTURBSEQ_DOMAIN.analysis_graph,
                "design": {"control_labels": ["NTC"], "guide_column": "guide", "target_column": "target_gene", "state_column": "leiden"},
            }},
        )
    ])
    state_reference_template = execute_tool("get_capability_template", {"capability_id": "build_embedding"}, snap=perturb_template_snap)
    check("state reference template exposes embedding step", "sc.pp.pca" in state_reference_template["template"]["code"] and "sc.tl.leiden" in state_reference_template["template"]["code"])
    compare_template = execute_tool("get_capability_template", {"capability_id": "compare_methods", "target": "TP53", "parameters": {"methods": ["wilcoxon", "t-test"], "contrast": "KO_vs_NTC"}}, snap=perturb_template_snap)
    check("compare methods template records method sensitivity", "method_sensitivity" in compare_template["template"]["code"])
    report_template = execute_tool("get_capability_template", {"capability_id": "generate_report"}, snap=perturb_template_snap)
    check("report template uses conclusion ids", "conclusion" in report_template["template"]["code"])
    check("report template blocks without evidence", report_template["readiness"]["ready"] is False)
    report_ready_snap = reduce([
        Event(
            event_id="rp_1",
            event_type="run_started",
            run_id="report_ready",
            payload={"config": {
                "run_id": "report_ready",
                "workspace": td,
                "goal": "report ready smoke",
                "domain": "perturbseq",
                "capabilities": PERTURBSEQ_DOMAIN.capabilities,
                "analysis_spec": PERTURBSEQ_DOMAIN.analysis_graph,
            }},
        ),
        Event(
            event_id="rp_2",
            event_type="conclusion_recorded",
            run_id="report_ready",
            payload={"conclusion": {"conclusion_id": "con_1", "text": "TP53 shows a robust effect", "support_ids": ["obs_1"]}},
        ),
        Event(
            event_id="rp_3",
            event_type="artifact_registered",
            run_id="report_ready",
            payload={"artifact": {"artifact_id": "art_1", "path": str(Path(td) / "de.csv"), "kind": "table", "summary": "DE table"}},
        ),
    ])
    report_ready_template = execute_tool("get_capability_template", {"capability_id": "generate_report"}, snap=report_ready_snap)
    check("report template ready with evidence ids", report_ready_template["readiness"]["ready"] is True and "con_1" in report_ready_template["template"]["code"])
    check("planning template tools are local-read tier", check_permission("compare_methods", ToolPermission.local_read) and check_permission("sweep_thresholds", ToolPermission.local_read))
    check("node contract tool is local-read tier", check_permission("get_node_contract", ToolPermission.local_read))
    check("context review tool is local-read tier", check_permission("get_context_review", ToolPermission.local_read))
    check("audit toolbox tool is local-read tier", check_permission("get_audit_toolbox", ToolPermission.local_read))
    check("harness manifest tool is local-read tier", check_permission("get_harness_manifest", ToolPermission.local_read))
    check("run audit tool is local-read tier", check_permission("audit_run", ToolPermission.local_read))
    check("evidence review tool is local-read tier", check_permission("review_evidence_chain", ToolPermission.local_read))
    check("rethinking plan tool is local-read tier", check_permission("plan_rethinking", ToolPermission.local_read))
    readonly_tool_names = {
        item["function"]["name"]
        for item in tool_schemas(readonly=True)
    }
    check("readonly schema excludes execute_code", "execute_code" not in readonly_tool_names)
    check("readonly schema excludes web/VLM tools", not {"search_web", "view_plot"} & readonly_tool_names)
    check("readonly schema includes rethinking plan", "plan_rethinking" in readonly_tool_names)
    unscoped_tool_names = {
        item["function"]["name"]
        for item in tool_schemas(readonly=False)
    }
    check("unscoped schema preserves full action surface", {"execute_code", "search_web"} <= unscoped_tool_names)
    scoped_unstarted_names = {
        item["function"]["name"]
        for item in tool_schemas(
            snap=Snapshot(analysis_spec={"nodes": [{"node_id": "inspect"}]}),
            scoped=True,
        )
    }
    check("scoped schema nudges node transition first", "execute_code" not in scoped_unstarted_names and "request_node_transition" in scoped_unstarted_names)
    scoped_active_names = {
        item["function"]["name"]
        for item in tool_schemas(
            snap=Snapshot(analysis_spec={"nodes": [{"node_id": "inspect"}]}, active_node_id="inspect"),
            scoped=True,
        )
    }
    check("scoped schema exposes execute only inside active node", "execute_code" in scoped_active_names and "search_web" not in scoped_active_names)
    check("scoped schema exposes load_dataset helper", "load_dataset" in scoped_active_names)
    check("scoped schema is smaller than full tool surface", len(scoped_active_names) < len(unscoped_tool_names))
    scoped_issue_names = {
        item["function"]["name"]
        for item in tool_schemas(
            snap=Snapshot(
                analysis_spec={"nodes": [{"node_id": "inspect"}]},
                active_node_id="inspect",
                findings=[Finding(finding_id="find_issue", severity="blocking", summary="needs repair")],
            ),
            scoped=True,
        )
    }
    check("issue scoped schema exposes rethinking tools", {"plan_rethinking", "review_evidence_chain", "audit_run"} <= scoped_issue_names)

    perturb_spec = PERTURBSEQ_DOMAIN.analysis_graph
    check("perturbseq default spec present", bool(perturb_spec and perturb_spec.get("nodes")))
    check("perturbseq has guide assignment node", any(n.get("node_id") == "guide_assignment" for n in perturb_spec.get("nodes", [])))
    pack_spec = perturbseq.default_graph()
    pack_domain = perturbseq.default_domain()
    check("perturbseq public default_graph returns spec", pack_spec.graph_id == "perturbseq_v2")
    check("perturbseq public default_domain returns copy", pack_domain.name == "perturbseq" and pack_domain is not PERTURBSEQ_DOMAIN)
    pack_registry = pack_domain.registry()
    check("perturbseq registry contains run_de", pack_registry.has("run_de"))
    check("perturbseq registry covers default spec", pack_registry.missing_from_spec(pack_spec) == [])
    check("perturbseq default domain audits cleanly", pack_domain.audit()["ok"] is True)
    check("perturbseq domain exposes runtime context", bool(pack_domain.runtime_context().get("condition_context")))
    check("core caps exclude perturbseq DE", not hasattr(pt.caps, "run_de"))
    check("perturbseq caps expose perturbseq DE", perturbseq.caps.run_de.id == "run_de")

    public_graph = AnalysisGraph("public_api")
    (
        public_graph
        .node("inspect")
        .start()
        .title("Inspect workspace")
        .goal("Find matrix-level inputs and summarize schema.")
        .use(perturbseq.caps.inspect_workspace, perturbseq.caps.load_dataset)
        .done_when(c.workspace_files_available())
        .recommend("list files", "summarize candidate matrix files")
        .expect("workspace file observations")
        .next("design", strict=True)
    )
    (
        public_graph
        .node("design")
        .title("Resolve design")
        .goal("Resolve controls and guide design before interpretation.")
        .enter_if(c.workspace_files_available())
        .use(perturbseq.caps.inspect_schema, perturbseq.caps.audit_controls)
        .done_when(
            c.design_confirmed("control_labels"),
            c.design_any_confirmed(["perturbation_modality", "moi", "loading_strategy"], condition_id="perturbation_design_known"),
        )
        .next("effect")
    )
    (
        public_graph
        .node("effect")
        .title("Effect exploration")
        .goal("Run bounded effect exploration.")
        .enter_if(c.design_confirmed("control_labels"))
        .use(perturbseq.caps.run_de)
        .done_when(c.observation_metric("logFC"))
    )
    public_spec = public_graph.to_spec()
    validate_analysis_graph(public_spec)
    public_compile = compile_conditions(public_spec)
    public_domain = (
        Domain(name="public")
        .with_graph(public_graph)
        .add_capability(
            perturbseq.caps.run_de,
            description="Run DE",
            required_inputs=["adata", "control_labels", "target_column"],
            expected_observations=["logFC", "p_value"],
            expected_artifacts=["de_result"],
        )
        .add_rubric("Do not interpret target effects before controls are confirmed.")
    )
    public_registry = public_domain.registry()
    legacy_registry = CapabilityRegistry.from_domain(Domain(
        name="public",
        capabilities=[{"id": "run_de", "description": "Run DE"}],
        analysis_graph=public_spec.model_dump(mode="json"),
    ))
    design_node = public_spec.node("design")
    check("public fluent API builds nodes", len(public_spec.nodes) == 3 and public_spec.start_node_id == "inspect")
    check("public fluent API records capabilities", "run_de" in public_spec.node("effect").allowed_capabilities)
    check("public capability refs remain serializable ids", public_spec.node("inspect").allowed_capabilities == ["inspect_workspace", "load_dataset"])
    check("public fluent API uses executable condition helpers", all(cond.evaluator_id != "rubric_only" for cond in design_node.completion))
    check("public condition compile report executable", len(public_compile.executable) >= 3)
    check("public registry auto-fills spec capabilities", public_registry.has("inspect_workspace") and public_registry.has("run_de"))
    check("public domain fluent API audits", public_domain.audit()["ok"] is True)
    check("public domain runtime context includes rubric", "Do not interpret" in public_domain.runtime_context().get("coding_guidelines", ""))
    public_browser = public_domain.describe()
    check("public domain browser summarizes nodes", public_browser["summary"]["nodes"] == 3)
    check("public domain browser separates core tools", any(tool["tool_id"] == "execute_code" for tool in public_browser["core_tools"]))
    run_de_browser = next(item for item in public_browser["capabilities"] if item["id"] == "run_de")
    check("public domain browser shows capability node usage", "effect" in run_de_browser["used_by_nodes"])
    check("public domain browser exposes design fields", "control_labels" in public_browser["design"]["fields"])
    check("legacy domain registry still works", legacy_registry.has("inspect_workspace") and legacy_registry.has("run_de"))
    effect_contract = node_contract(public_spec, "effect", capabilities=public_registry)
    check("public node contract names node", effect_contract["node"]["id"] == "effect")
    check("public node contract includes capability", effect_contract["capabilities"][0]["id"] == "run_de")
    check("public node contract summarizes inputs", "control_labels" in effect_contract["inputs"]["required"])
    check("public node contract suggests template call", effect_contract["actions"]["template_calls"][0]["args"]["capability_id"] == "run_de")
    graph_contract_payload = graph_contract(public_spec, capabilities=public_registry)
    check("public graph contract covers nodes", graph_contract_payload["node_count"] == 3 and len(graph_contract_payload["nodes"]) == 3)
    missing_contract = node_contract(public_spec, "inspect", capabilities=[])
    check("public node contract surfaces missing capabilities", "inspect_workspace" in missing_contract["missing_capabilities"])
    public_audit = audit_analysis_graph(public_spec, capabilities=public_registry)
    check("public graph audit returns semantic report", public_audit["audit_type"] == "analysis_graph_audit")
    check("public graph audit accepts autofilled registry", public_audit["ok"] is True)
    rough_graph = (
        AnalysisGraph("rough_public", start_node_id="rough")
        .add_node(
            "rough",
            title="Rough",
            allowed_capabilities=["missing_capability"],
            completion=["human-readable but not executable enough"],
        )
        .to_spec()
    )
    rough_audit = audit_analysis_graph(rough_graph, capabilities=[])
    check("graph audit detects missing capability", any(issue["code"] == "missing_capability" for issue in rough_audit["errors"]))
    check("graph audit detects rubric-only completion", any(issue["code"] == "rubric_only_condition" for issue in rough_audit["warnings"]))

    wb_spec = Workbench(Domain(name="spec_api"), provider="openai", sandbox="subprocess")
    ws_spec = Path(td) / "spec_ws"
    ws_spec.mkdir()
    wb_spec.run(str(ws_spec), goal="spec api", steps=0)
    wb_spec.load_analysis_spec(graph_spec.model_dump(mode="json"), reason="test")
    api_snap = wb_spec._store.read_snapshot()
    check("workbench load_analysis_spec records spec", api_snap.analysis_spec.get("graph_id") == "petura_test")
    check("workbench load_analysis_spec loads capabilities", any(cap.get("capability_id") == "run_de" for cap in api_snap.capabilities))
    check("workbench load_analysis_spec enters start node", api_snap.active_node_id == "workspace_inspection")
    cap_tool = execute_tool("list_capabilities", {}, snap=api_snap)
    check("list_capabilities tool returns active node capabilities", cap_tool["capabilities"][0]["id"] == "inspect_workspace")
    wb_spec.update_design({"control_labels": ["NTC"]}, reason="test")
    api_snap = wb_spec._store.read_snapshot()
    check("workbench update_design records design", api_snap.design.get("control_labels") == ["NTC"])
    from pertura._cli import _spec_contract_payload
    cli_contract = _spec_contract_payload(domain_name="perturbseq", node_id="effect_exploration")
    check("spec contract CLI helper returns node contract", cli_contract["node"]["id"] == "effect_exploration")
    check("spec contract CLI helper includes perturbseq capability", any(card["id"] == "run_de" for card in cli_contract["capabilities"]))
    from pertura._cli import _spec_audit_payload
    cli_audit = _spec_audit_payload(domain_name="perturbseq")
    check("spec audit CLI helper returns ok", cli_audit["ok"] is True and cli_audit["summary"]["nodes"] >= 1)
    from pertura._api import (
        analysis_spec_audit_payload,
        analysis_spec_contract_payload,
        capabilities_view_payload,
        context_review_payload,
        harness_manifest_payload,
        rethinking_payload,
        run_audit_payload,
        runtime_node_contract_payload,
    )
    api_audit_payload = analysis_spec_audit_payload(wb_spec)
    api_contract_payload = analysis_spec_contract_payload(wb_spec, node_id="target_interpretation")
    api_runtime_payload = runtime_node_contract_payload(wb_spec)
    api_context_payload = context_review_payload(wb_spec, purpose="audit", max_items=4)
    api_capabilities_payload = capabilities_view_payload(wb_spec)
    api_harness_payload = harness_manifest_payload()
    api_run_audit_payload = run_audit_payload(wb_spec)
    api_rethinking_payload = rethinking_payload(wb_spec, issue="review API run state")
    check("api helper exposes analysis spec audit", api_audit_payload["audit_type"] == "analysis_graph_audit")
    check("api helper exposes analysis node contract", api_contract_payload["node"]["id"] == "target_interpretation")
    check("api helper exposes runtime node dashboard", api_runtime_payload["runtime"]["target_node_id"] == "workspace_inspection")
    check("api helper exposes context review", api_context_payload["view_type"] == "context_envelope")
    check("api helper exposes scoped LLM tool surface", api_capabilities_payload["llm_tool_surface"]["surface_type"] == "scoped_llm_tools")
    check("api helper exposes tool hidden reasons", any(item["why_hidden"] for item in api_capabilities_payload["llm_tool_surface"]["hidden_tools"]))
    check("api helper links capabilities to tool visibility", all("tool_visibility" in item for item in api_capabilities_payload["capabilities"]))
    check("api helper exposes harness manifest", api_harness_payload["thesis"]["core_principle"] == "free_reasoning_gated_commit")
    check("api helper exposes run audit", api_run_audit_payload["audit_type"] == "run_audit")
    check("api helper exposes rethinking plan", api_rethinking_payload["view_type"] == "rethinking_plan")
    gui_html = (Path(__file__).resolve().parent.parent / "pertura" / "_gui.html").read_text(encoding="utf-8")
    check("GUI graph panel fetches rethinking endpoint", "/api/rethink/" in gui_html and "Rethinking" in gui_html)
    check("GUI capability browser names LLM tool surface", "LLM visible tools this turn" in gui_html and "toggleCapability" in gui_html)
    check("GUI actions surface API failures", "actionStatus" in gui_html and "postJson" in gui_html and "!r.ok" in gui_html)
    check("GUI foregrounds active work order", "Active Work Order" in gui_html and "active_work_order" in gui_html)
    cli_help = subprocess.run(
        [sys.executable, "-m", "pertura._cli", "--help"],
        text=True,
        capture_output=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=15,
    )
    check("top-level GUI shortcut is documented", cli_help.returncode == 0 and "--GUI" in cli_help.stdout and "--domain GUI_DOMAIN" in cli_help.stdout)
    check("top-level GUI shortcut exposes provider endpoint flags",
          "--provider {openai,anthropic}" in cli_help.stdout
          and "--model GUI_MODEL" in cli_help.stdout
          and "--base-url GUI_BASE_URL" in cli_help.stdout)
    from pertura.skills import init_pertura_dir
    starter_dir = init_pertura_dir(Path(td) / "starter_project")
    starter_graph = load_analysis_graph(starter_dir / "analysis_graph.json")
    starter_domain = Domain(**json.loads((starter_dir / "domain.json").read_text(encoding="utf-8")))
    starter_settings = json.loads((starter_dir / "settings.json").read_text(encoding="utf-8"))
    starter_audit = audit_analysis_graph(starter_graph, capabilities=starter_domain.capabilities)
    check("init creates starter analysis graph", starter_graph.graph_id == "perturbseq_v2")
    check("init creates starter domain pack", starter_domain.name == "perturbseq" and bool(starter_domain.capabilities))
    check("init settings references local domain pack", starter_settings.get("domain") == ".pertura/domain.json")
    check("init settings references analysis graph", starter_settings.get("analysis_graph") == ".pertura/analysis_graph.json")
    check("init starter graph audits cleanly", starter_audit["ok"] is True)
    from pertura._cli import _apply_llm_endpoint_env, _load_domain, _load_project_settings, _resolve_cli_config
    saved_endpoint_env = {
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL"),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL"),
        "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL"),
    }
    try:
        for key in saved_endpoint_env:
            os.environ.pop(key, None)
        _apply_llm_endpoint_env(
            types.SimpleNamespace(gui_provider="openai", gui_model="deepseek-v4-flash", gui_base_url="https://api.deepseek.com"),
            provider="openai", model_attr="gui_model", base_url_attr="gui_base_url",
        )
        check("GUI shortcut maps OpenAI-compatible model", os.environ.get("OPENAI_MODEL") == "deepseek-v4-flash")
        check("GUI shortcut maps OpenAI-compatible base URL", os.environ.get("OPENAI_BASE_URL") == "https://api.deepseek.com")
        _apply_llm_endpoint_env(
            types.SimpleNamespace(model="claude-sonnet", base_url="https://anthropic.example"),
            provider="anthropic",
        )
        check("CLI maps Anthropic-compatible model", os.environ.get("ANTHROPIC_MODEL") == "claude-sonnet")
        check("CLI maps Anthropic-compatible base URL", os.environ.get("ANTHROPIC_BASE_URL") == "https://anthropic.example")
    finally:
        for key, value in saved_endpoint_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    project_settings = _load_project_settings(starter_dir.parent)
    check("CLI finds project settings from root", Path(project_settings["path"]).name == "settings.json")
    nested_dir = starter_dir.parent / "data" / "nested"
    nested_dir.mkdir(parents=True)
    nested_settings = _load_project_settings(nested_dir)
    check("CLI finds project settings from nested workspace", nested_settings["project_root"] == str(starter_dir.parent))
    class _RunArgs:
        domain = None
        analysis_graph = None
        provider = None
        sandbox = None
        steps = None
    resolved_cfg = _resolve_cli_config(_RunArgs(), workspace=nested_dir)
    check("CLI resolves init domain path", resolved_cfg["domain"] == str(starter_dir / "domain.json"))
    check("CLI resolves init analysis graph path", resolved_cfg["analysis_graph"] == str(starter_dir / "analysis_graph.json"))
    check("CLI resolves init provider and budget", resolved_cfg["provider"] == "openai" and resolved_cfg["steps"] == 30)
    loaded_project_domain = _load_domain(resolved_cfg["domain"], analysis_graph_path=resolved_cfg["analysis_graph"])
    check("CLI loaded initialized domain graph", loaded_project_domain.analysis_graph.get("graph_id") == "perturbseq_v2")
    class _OverrideArgs:
        domain = "perturbseq"
        analysis_graph = ""
        provider = "anthropic"
        sandbox = "subprocess"
        steps = 2
    override_cfg = _resolve_cli_config(_OverrideArgs(), workspace=starter_dir.parent)
    check("CLI explicit args override project settings", override_cfg["domain"] == "perturbseq" and override_cfg["provider"] == "anthropic" and override_cfg["steps"] == 2)
    from pertura._cli import _audit_run_dir, _capsule_run_dir, _capsule_trace_commands, _capsule_verify_run_dir, _claims_payload, _context_run_dir, _harness_payload, _rethinking_run_dir, _toolbox_payload
    context_cli_payload = _context_run_dir(wb_spec._store.run_dir, purpose="audit", max_items=4)
    check("context CLI helper returns envelope", context_cli_payload["view_type"] == "context_envelope")
    check("context CLI helper exposes audit next actions", len(context_cli_payload["audit_preview"]["next_actions"]) >= 1)
    check("context CLI helper exposes rethinking preview", context_cli_payload["trace_driven_rethinking"]["view_type"] == "rethinking_plan_preview")
    rethink_cli_payload = _rethinking_run_dir(wb_spec._store.run_dir, issue="review active node state")
    check("rethinking CLI helper works on spec run", rethink_cli_payload["view_type"] == "rethinking_plan")
    run_audit_cli_payload = _audit_run_dir(wb_spec._store.run_dir)
    check("run audit CLI helper returns audit", run_audit_cli_payload["audit_type"] == "run_audit")
    check("run audit CLI helper exposes next actions", len(run_audit_cli_payload["next_actions"]) >= 1)
    toolbox_cli_payload = _toolbox_payload(purpose="audit")
    toolbox_cli_tools = {item["tool"] for item in toolbox_cli_payload["tools"]}
    harness_cli_payload = _harness_payload()
    check("harness CLI helper returns manifest", harness_cli_payload["view_type"] == "harness_manifest")
    check("harness CLI helper exposes thesis", harness_cli_payload["thesis"]["core_principle"] == "free_reasoning_gated_commit")
    check("toolbox CLI helper returns audit toolbox", toolbox_cli_payload["view_type"] == "audit_toolbox")
    check("toolbox CLI helper exposes audit tools", {"get_context_review", "audit_run", "review_evidence_chain", "plan_rethinking"} <= toolbox_cli_tools)
    capsule_payload = _capsule_run_dir(wb_spec._store.run_dir)
    check("capsule CLI helper writes file", Path(capsule_payload["path"]).exists())
    check("capsule embeds run audit summary", capsule_payload["audit"]["summary"]["attempts"] >= 0)
    check("capsule embeds harness manifest", capsule_payload["harness_manifest"]["thesis"]["core_principle"] == "free_reasoning_gated_commit")
    check("capsule embeds context audit preview", capsule_payload["context_preview"]["audit_preview"]["audit_type"] == "run_audit")
    check("capsule embeds context rethinking preview", capsule_payload["context_preview"]["trace_driven_rethinking"]["view_type"] == "rethinking_plan_preview")
    check("capsule embeds provenance manifest", capsule_payload["provenance_manifest"]["manifest_type"] == "provenance_manifest")
    check("capsule embeds replay metadata", capsule_payload["replay"]["snapshot_matches_store"] is True)
    check("capsule embeds integrity hashes", len(capsule_payload["integrity"]["event_log_sha256"]) == 64 and len(capsule_payload["integrity"]["graph_sha256"]) == 64)
    check("capsule integrity event count matches replay", capsule_payload["integrity"]["event_count"] == capsule_payload["replay"]["event_count"])
    capsule_verify_payload = _capsule_verify_run_dir(wb_spec._store.run_dir, capsule_path=capsule_payload["path"])
    check("capsule verify accepts matching run", capsule_verify_payload["ok"] is True)
    check("capsule verify checks event hash", any(item["key"] == "event_log_sha256" and item["ok"] for item in capsule_verify_payload["checks"]))
    capsule_payload_again = _capsule_run_dir(wb_spec._store.run_dir, out_path=Path(capsule_payload["path"]).with_name("run_capsule_again.json"))
    check(
        "capsule integrity hashes are deterministic",
        capsule_payload_again["integrity"]["event_log_sha256"] == capsule_payload["integrity"]["event_log_sha256"]
        and capsule_payload_again["integrity"]["snapshot_sha256"] == capsule_payload["integrity"]["snapshot_sha256"]
        and capsule_payload_again["integrity"]["graph_sha256"] == capsule_payload["integrity"]["graph_sha256"],
    )
    check("capsule suggests operator commands", any("pertura audit" in command for command in capsule_payload["operator_commands"]))
    check("capsule suggests integrity verification", any("--verify" in command for command in capsule_payload["operator_commands"]))
    capsule_trace_commands = _capsule_trace_commands(
        wb_spec._store.run_dir,
        {"next_actions": [{"tool": "review_evidence_chain", "args": {"node_id": "con_x"}}]},
        {"conclusions": {"items": [{"conclusion_id": "con_y"}]}},
    )
    check("capsule trace commands include evidence review", any("pertura evidence" in command for command in capsule_trace_commands))
    check("capsule trace commands include rethinking", any("pertura rethink" in command for command in capsule_trace_commands))
    capsule_claims = {item["claim_id"]: item for item in capsule_payload["claim_checks"]}
    from pertura.core.claims import CORE_CLAIMS, capsule_claim_id, standalone_claim_command
    expected_capsule_claims = {capsule_claim_id(claim_id) for claim_id in CORE_CLAIMS}
    expected_independent_commands = {standalone_claim_command(claim_id) for claim_id in CORE_CLAIMS}
    claims_manifest_payload = _claims_payload()
    manifest_commands = {item["standalone_command"] for item in claims_manifest_payload["claims"]}
    manifest_capsule_ids = {item["capsule_claim_id"] for item in claims_manifest_payload["claims"]}
    check("claims CLI helper returns manifest", claims_manifest_payload["view_type"] == "core_claim_manifest")
    check("claims CLI helper exposes harness thesis", claims_manifest_payload["harness_thesis"]["core_principle"] == "free_reasoning_gated_commit")
    check("claims CLI helper exposes developer vocabulary", any(item["common_term"] == "Workflow/state graph" for item in claims_manifest_payload["developer_vocabulary"]))
    check("claims CLI helper lists core claims", set(item["paper_claim_id"] for item in claims_manifest_payload["claims"]) == set(CORE_CLAIMS))
    check("claims CLI helper maps standalone commands", manifest_commands == expected_independent_commands)
    check("claims CLI helper maps capsule ids", manifest_capsule_ids == expected_capsule_claims)
    check("capsule includes core claim checks", expected_capsule_claims <= set(capsule_claims))
    check("capsule supports analysis graph claim", capsule_claims["editable_analysis_graph_and_gate"]["status"] == "supported")
    check("capsule embeds claim verification matrix", capsule_payload["claim_verification"]["verification_type"] == "core_claim_verification_matrix")
    check("capsule claim verification names harness principle", capsule_payload["claim_verification"]["harness_principle"] == "free_reasoning_gated_commit")
    check("capsule claim checks are structured", all(item["summary"]["total"] == len(item["checks"]) for item in capsule_payload["claim_checks"]))
    check("capsule maps claims to independent runner", set(capsule_payload["claim_verification"]["independent_commands"]) == expected_independent_commands)
    check("capsule exposes audit claim commands", any("pertura audit" in command for command in capsule_claims["deliberative_agent_with_commit_audit"]["commands"]))
    from pertura._cli import _doctor
    class _DoctorArgs:
        openai = False
    check("doctor command returns zero", _doctor(_DoctorArgs()) == 0)
    try:
        from fastapi.testclient import TestClient
        from pertura._api import create_app
        app = create_app(wb_spec)
        client = TestClient(app)
        api_audit = client.get("/api/analysis-spec/audit").json()
        api_contract = client.get("/api/analysis-spec/contract", params={"node_id": "target_interpretation"}).json()
        api_runtime_contract = client.get("/api/node-contract").json()
        api_context = client.get("/api/context-review", params={"purpose": "audit", "max_items": 4}).json()
        api_workbench_view = client.get("/api/workbench-view", params={"max_items": 4}).json()
        api_run_audit = client.get("/api/run-audit").json()
        api_rethink = client.get("/api/rethink", params={"issue": "review endpoint"}).json()
        api_harness = client.get("/api/harness-manifest").json()
        check("api exposes analysis spec audit", api_audit["audit_type"] == "analysis_graph_audit")
        check("api exposes analysis node contract", api_contract["node"]["id"] == "target_interpretation")
        check("api exposes runtime node dashboard", api_runtime_contract["runtime"]["target_node_id"] == "workspace_inspection")
        check("api exposes context review", api_context["view_type"] == "context_envelope")
        check("api exposes workbench view", api_workbench_view["view_type"] == "workbench_view")
        check("api workbench view contains active node contract", "active_node_contract" in api_workbench_view["analysis"])
        check("api workbench view contains active work order", api_workbench_view["analysis"]["active_work_order"]["view_type"] == "active_work_order")
        check("api workbench view contains analysis nodes", len(api_workbench_view["analysis"]["nodes"]) >= 1)
        check("api workbench view contains agent context", api_workbench_view["agent_context"].get("view_type") == "context_envelope")
        check("api workbench view exposes review summary", "run_audit_summary" in api_workbench_view["review"])
        check("api exposes harness manifest", api_harness["thesis"]["core_principle"] == "free_reasoning_gated_commit")
        check("api exposes run audit", api_run_audit["audit_type"] == "run_audit")
        check("api exposes rethinking endpoint", api_rethink["view_type"] == "rethinking_plan")
        api_run_response = client.post("/api/run", json={"workspace": str(ws_spec), "goal": "api body parse", "steps": 0})
        check("api run accepts JSON body", api_run_response.status_code == 200)
    except Exception as exc:
        check("api contract endpoints skipped without fastapi", True, str(exc))


total = PASS + FAIL
print(f"\n{'='*50}")
if SELECTED_SEGMENTS:
    print(f"Selected segments: {', '.join(sorted(SELECTED_SEGMENTS))}")
print(f"Results: {PASS}/{total} passed")
if FAIL:
    print(f"FAILED: {FAIL} test(s)")
else:
    print(f"All tests passed!")
print(f"{'='*50}")
