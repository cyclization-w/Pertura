"""Independent smoke checks for Pertura-v2 paper claims."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pertura import AnalysisGraph, capability, conditions as c, audit_analysis_graph
from pertura.capabilities import CapabilityRegistry
from pertura.core import (
    Store,
    GraphController,
    CORE_CLAIMS,
    core_claim_ids,
    build_context_view,
    build_graph,
    build_observation_memory_view,
    review_evidence_chain,
)
from pertura.domain import Domain
from pertura.models import Attempt, Outcome, _model_dump
from pertura.tools.registry import execute_tool
from pertura.agent.gated_dispatch import gated_dispatch


PASS = 0
FAIL = 0
CHECKS: list[dict[str, object]] = []
CURRENT_CLAIM = ""
JSON_OUTPUT = False

CLAIM_FUNCTIONS = {
    "analysis_graph": "claim_analysis_graph_and_gate",
    "observation_memory": "claim_observation_memory",
    "deliberative_audit": "claim_deliberative_audit_and_evidence",
}

CLAIMS = {
    claim_id: {
        "title": CORE_CLAIMS[claim_id]["title"],
        "fn": CLAIM_FUNCTIONS[claim_id],
    }
    for claim_id in core_claim_ids()
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(condition)
    CHECKS.append({
        "claim_id": CURRENT_CLAIM,
        "name": name,
        "ok": ok,
        "detail": "" if ok else detail,
    })
    if ok:
        PASS += 1
        if not JSON_OUTPUT:
            print(f"  PASS {name}")
    else:
        FAIL += 1
        if not JSON_OUTPUT:
            print(f"  FAIL {name} -- {detail}")


def claim_header(title: str) -> None:
    if not JSON_OUTPUT:
        print(f"\n-- Claim: {title} --")


def claim_analysis_graph_and_gate(tmp_root: Path) -> None:
    claim_header("editable analysis graph + gate")
    spec = (
        AnalysisGraph("claim_graph", start_node_id="inspect")
        .node("inspect")
        .title("Inspect")
        .use("inspect_workspace")
        .done_when(c.workspace_files_available())
        .next("effect")
        .end()
    )
    spec.node("effect").title("Effect").enter_if(
        c.design_confirmed("control_labels")
    ).use("run_de").done_when(
        c.observation_metric("logFC")
    )
    domain = Domain(
        name="claim_domain",
        capabilities=[
            capability("inspect_workspace", description="Inspect workspace").model_dump(mode="json"),
            capability("run_de", description="Run DE").model_dump(mode="json"),
        ],
        analysis_graph=spec.to_spec().model_dump(mode="json"),
    )
    registry = CapabilityRegistry.from_domain(domain)
    graph_audit = audit_analysis_graph(spec.to_spec(), capabilities=registry)
    check("graph audit accepts editable spec", graph_audit["ok"] is True)

    store = Store(tmp_root / "claim_graph")
    controller = GraphController(store, "claim_graph")
    controller.append_event("run_started", {"config": {
        "run_id": "claim_graph",
        "workspace": str(tmp_root),
        "goal": "claim graph",
        "domain": "claim",
        "analysis_spec": spec.to_spec().model_dump(mode="json"),
        "capabilities": domain.capabilities,
    }})
    controller.append_event("node_entered", {
        "node_id": "inspect",
        "branch_id": "main",
        "reason": "start",
    })
    snap = store.read_snapshot()
    class _WB:
        pass
    wb_stub = _WB()
    wb_stub._controller = controller
    wb_stub._store = store
    wb_stub._run_id = "claim_graph"
    wb_stub.provider = "openai"
    wb_stub._emit = lambda event_type, payload: controller.append_event(event_type, payload)
    result = gated_dispatch(
        wb_stub,
        "request_node_transition",
        "",
        {"summary": "enter effect"},
        {"target_node_id": "effect", "reason": "need effect evidence"},
        snap,
    )
    fresh = store.read_snapshot()
    check("gate blocks missing design", result == "waiting_for_human")
    check("gate opens interrupt", any(item.source == "node_gate" and item.status == "open" for item in fresh.interrupts))


def claim_observation_memory(tmp_root: Path) -> None:
    claim_header("scientific observation memory")
    store = Store(tmp_root / "claim_memory")
    controller = GraphController(store, "claim_memory")
    controller.append_event("run_started", {"config": {
        "run_id": "claim_memory",
        "workspace": str(tmp_root),
        "goal": "claim memory",
        "domain": "claim",
        "capabilities": [],
    }})
    controller.append_event("branch_opened", {"branch": {
        "branch_id": "alt",
        "title": "Alternative context",
        "parent_id": "main",
        "reason": "check context sensitivity",
        "question": "Does the sign flip in another context?",
        "status": "active",
    }})
    for idx, branch in enumerate(["main", "alt"]):
        controller.append_event("observation_registered", {"observation": {
            "observation_id": f"obs_mem_{idx}",
            "type": "de_effect",
            "target": "TargetM",
            "metric": "logFC",
            "value": 1.0 if branch == "main" else -1.0,
            "contrast": "KO_vs_NTC",
            "method": "wilcoxon",
            "branch_id": branch,
            "variable_key": "TargetM.logFC",
        }})
    memory = build_observation_memory_view(store.read_snapshot(), target="TargetM", metric="logFC")
    check("memory groups repeated scientific observations", memory["variable_count"] >= 1)
    check("memory detects scientific disagreement", len(memory["conflicts"]) + len(memory["divergences"]) >= 1)
    check("memory exposes coverage labels", any(item.get("label") for item in memory["coverage"]))
    context = build_context_view(store.read_snapshot(), max_items=4)
    check("context exposes intent entries", len(context["intent"]) >= 1)
    tool_view = execute_tool("query_observation_memory", {"target": "TargetM", "metric": "logFC"}, snap=store.read_snapshot())
    check("LLM can query observation memory", tool_view["view_type"] == "observation_memory")


def claim_deliberative_audit_and_evidence(tmp_root: Path) -> None:
    claim_header("deliberative audit + evidence chain")
    spec = (
        AnalysisGraph("claim_evidence_graph", start_node_id="effect")
        .node("effect")
        .title("Effect")
        .use("run_de")
        .done_when(c.observation_metric("logFC"))
        .end()
        .to_spec()
    )
    cap_pack = [
        capability("run_de", description="Run differential effect").model_dump(mode="json"),
        capability("inspect_workspace", description="Inspect workspace").model_dump(mode="json"),
    ]
    store = Store(tmp_root / "claim_evidence")
    controller = GraphController(store, "claim_evidence")
    controller.append_event("run_started", {"config": {
        "run_id": "claim_evidence",
        "workspace": str(tmp_root),
        "goal": "claim evidence",
        "domain": "claim",
        "analysis_spec": spec.model_dump(mode="json"),
        "capabilities": cap_pack,
    }})
    controller.append_event("node_entered", {
        "node_id": "effect",
        "branch_id": "main",
        "reason": "start",
    })
    class _WB:
        pass
    wb_stub = _WB()
    wb_stub._controller = controller
    wb_stub._store = store
    wb_stub._run_id = "claim_evidence"
    wb_stub.provider = "openai"
    wb_stub._emit = lambda event_type, payload: controller.append_event(event_type, payload)
    exploratory_result = gated_dispatch(
        wb_stub,
        "execute_code",
        "print('explore')",
        {"summary": "exploratory run"},
        {"stage": "explore"},
        store.read_snapshot(),
    )
    exploratory_snap = store.read_snapshot()
    check("missing capability declaration is blocked", exploratory_result == "blocked")
    check(
        "missing capability declaration is blocking",
        any(f.finding_type == "missing_capability_declaration" and f.severity == "blocking" for f in exploratory_snap.findings),
    )
    blocked_result = gated_dispatch(
        wb_stub,
        "execute_code",
        "print('wrong capability')",
        {"summary": "wrong capability"},
        {"capability_ids": ["inspect_workspace"], "stage": "inspect"},
        exploratory_snap,
    )
    check("disallowed capability is blocked at commit", blocked_result == "blocked")
    controller.append_event("attempt_planned", {"attempt": _model_dump(Attempt(
        attempt_id="att_claim",
        branch_id="main",
        analysis_node_id="effect",
        title="Evidence attempt",
        capability_ids=["run_de"],
    ))})
    controller.append_event("outcome_recorded", {"outcome": _model_dump(Outcome(
        outcome_id="out_claim",
        attempt_id="att_claim",
        status="success",
        summary="ok",
    ))})
    controller.append_event("observation_registered", {"observation": {
        "observation_id": "obs_claim",
        "type": "de_effect",
        "target": "TargetE",
        "metric": "logFC",
        "value": 2.0,
        "attempt_id": "att_claim",
        "branch_id": "main",
    }})
    controller.append_event("conclusion_recorded", {"conclusion": {
        "conclusion_id": "con_claim",
        "text": "TargetE has evidence",
        "grade": "supported",
        "support_ids": ["obs_claim"],
    }})
    snap = store.read_snapshot()
    graph = build_graph(snap)
    review = review_evidence_chain(snap, "con_claim", graph=graph)
    check("evidence review verifies successful support", review["ok"] is True)
    tool_review = execute_tool("review_evidence_chain", {"node_id": "con_claim"}, snap=snap)
    check("LLM can call evidence review tool", tool_review["view_type"] == "evidence_chain_review")
    toolbox = execute_tool("get_audit_toolbox", {"purpose": "audit"}, snap=snap)
    toolbox_tools = {item["tool"] for item in toolbox["tools"]}
    check("LLM can discover audit toolbox", toolbox["view_type"] == "audit_toolbox")
    check("audit toolbox points to evidence review", "review_evidence_chain" in toolbox_tools)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run independent Pertura-v2 paper-claim smoke checks.")
    parser.add_argument("--list-claims", action="store_true", help="List runnable paper claims.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    parser.add_argument(
        "--claim",
        action="append",
        default=[],
        help="Claim id to run. May be repeated or comma-separated. Defaults to all.",
    )
    return parser.parse_args()


def _selected_claims(raw_claims: list[str]) -> list[str]:
    selected: list[str] = []
    for raw in raw_claims:
        for item in str(raw).split(","):
            claim_id = item.strip()
            if not claim_id:
                continue
            if claim_id not in CLAIMS:
                known = ", ".join(sorted(CLAIMS))
                raise SystemExit(f"Unknown claim: {claim_id}\nKnown claims: {known}")
            if claim_id not in selected:
                selected.append(claim_id)
    return selected or list(CLAIMS)


def _claim_list_payload() -> dict[str, object]:
    return {
        "test_type": "paper_claim_segments",
        "claims": [
            {"claim_id": claim_id, "title": payload["title"]}
            for claim_id, payload in CLAIMS.items()
        ],
    }


def _result_payload(selected: list[str]) -> dict[str, object]:
    claim_results = []
    for claim_id in selected:
        checks = [item for item in CHECKS if item["claim_id"] == claim_id]
        passed = sum(1 for item in checks if item["ok"])
        failed = len(checks) - passed
        claim_results.append({
            "claim_id": claim_id,
            "title": CLAIMS[claim_id]["title"],
            "ok": failed == 0,
            "passed": passed,
            "failed": failed,
            "checks": checks,
        })
    return {
        "test_type": "paper_claim_segments",
        "selected_claims": selected,
        "ok": FAIL == 0,
        "summary": {
            "passed": PASS,
            "failed": FAIL,
            "total": PASS + FAIL,
        },
        "claims": claim_results,
    }


def run_claims(raw_claims: list[str] | None = None, *, json_output: bool = False) -> dict[str, object]:
    import tempfile

    global PASS, FAIL, CHECKS, CURRENT_CLAIM, JSON_OUTPUT

    PASS = 0
    FAIL = 0
    CHECKS = []
    CURRENT_CLAIM = ""
    JSON_OUTPUT = bool(json_output)
    selected = _selected_claims(raw_claims or [])
    functions = {
        "claim_analysis_graph_and_gate": claim_analysis_graph_and_gate,
        "claim_observation_memory": claim_observation_memory,
        "claim_deliberative_audit_and_evidence": claim_deliberative_audit_and_evidence,
    }
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        for claim_id in selected:
            CURRENT_CLAIM = claim_id
            functions[CLAIMS[claim_id]["fn"]](root)
    CURRENT_CLAIM = ""
    return _result_payload(selected)


def main() -> int:
    args = _parse_args()
    if args.list_claims:
        if args.json:
            print(json.dumps(_claim_list_payload(), indent=2))
            return 0
        print("Available paper claims:")
        for claim_id, payload in CLAIMS.items():
            print(f"  {claim_id}: {payload['title']}")
        return 0
    selected = _selected_claims(args.claim)
    payload = run_claims(selected, json_output=bool(args.json))

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 1
    print(f"\n{'=' * 50}")
    print(f"Selected claims: {', '.join(selected)}")
    print(f"Claim segment results: {payload['summary']['passed']}/{payload['summary']['total']} passed")
    if not payload["ok"]:
        print(f"FAILED: {payload['summary']['failed']} test(s)")
        return 1
    print("All claim segments passed!")
    print(f"{'=' * 50}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
