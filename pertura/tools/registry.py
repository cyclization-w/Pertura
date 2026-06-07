"""Tool registry: all tools the LLM can call, plus schema generation."""

from __future__ import annotations

import base64
import csv
import json
import os
from pathlib import Path


def _allowed_path(path: str, snap=None) -> Path:
    """Resolve and validate a file path. Only workspace + artifacts_dir allowed."""
    p = Path(path).resolve()
    allowed_dirs = []
    if snap:
        ws = getattr(snap, 'workspace', '')
        if ws:
            allowed_dirs.append(Path(ws).resolve())
    try:
        runs_dir = Path("runs").resolve()
        allowed_dirs.append(runs_dir)
    except Exception:
        pass
    for allowed in allowed_dirs:
        try:
            p.relative_to(allowed)
            return p
        except ValueError:
            continue
    raise PermissionError(f"Path not allowed: {path}. Must be under workspace or runs/.")


def _vlm_config():
    return {
        "provider": os.getenv("PETURA_VLM_PROVIDER") or os.getenv("BLACKBOARD_VLM_PROVIDER", ""),
        "api_key": os.getenv("PETURA_VLM_API_KEY") or os.getenv("BLACKBOARD_VLM_API_KEY", ""),
        "base_url": os.getenv("PETURA_VLM_BASE_URL") or os.getenv("BLACKBOARD_VLM_BASE_URL", ""),
        "model": os.getenv("PETURA_VLM_MODEL") or os.getenv("BLACKBOARD_VLM_MODEL", "gpt-4o"),
    }


# ── Tool implementations ────────────────────────────────────────────────

def view_plot(path: str, snap=None) -> dict:
    """Read an image file and describe it using a VLM."""
    try:
        p = _allowed_path(path, snap=snap)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"File not found: {path}"}
    vlm = _vlm_config()
    if not vlm["api_key"]:
        return {"path": str(p), "size_bytes": p.stat().st_size,
                "note": "No VLM configured."}
    try:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        ext = p.suffix.lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(ext, "image/png")
        from openai import OpenAI
        client = OpenAI(api_key=vlm["api_key"], base_url=vlm["base_url"] or None)
        response = client.chat.completions.create(
            model=vlm["model"],
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Describe this scientific plot in detail. What does it show? Any issues?"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}},
            ]}],
            max_tokens=500,
        )
        return {"path": str(p), "size_bytes": p.stat().st_size,
                "description": response.choices[0].message.content}
    except Exception as exc:
        return {"path": str(p), "size_bytes": p.stat().st_size, "error": str(exc)}


def read_file(path: str, max_lines: int = 30, snap=None) -> dict:
    try:
        p = _allowed_path(path, snap=snap)
    except PermissionError as e:
        return {"error": str(e)}
    if not p.exists():
        return {"error": f"File not found: {path}"}
    try:
        text = p.read_text(encoding="utf-8")
        lines = text.split("\n")
        return {"path": str(p), "size_bytes": p.stat().st_size, "lines": len(lines),
                "preview": "\n".join(lines[:max_lines]),
                "suffix": "..." if len(lines) > max_lines else ""}
    except Exception as exc:
        return {"error": str(exc)}


def search_web(query: str) -> dict:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                       base_url=os.getenv("OPENAI_BASE_URL") or None)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            input=query,
            tools=[{"type": "web_search_preview"}],
            max_output_tokens=2000,
        )
        return {"query": query, "result": response.output_text[:3000]}
    except Exception as exc:
        return {"query": query, "error": str(exc)}


def query_observations(
    snap,
    target: str = "",
    metric: str = "",
    contrast: str = "",
    method: str = "",
    branch_id: str = "",
    limit: int = 10,
) -> dict:
    from pertura.core.views import build_observation_view
    view = build_observation_view(
        snap, target=target, metric=metric, contrast=contrast,
        method=method, branch_id=branch_id, limit=limit,
    )
    return {**view, "results": view["observations"]}


def query_observation_memory(
    snap,
    target: str = "",
    metric: str = "",
    contrast: str = "",
    method: str = "",
    branch_id: str = "",
    limit: int = 10,
) -> dict:
    from pertura.core.observation_memory import build_observation_memory_view
    return build_observation_memory_view(
        snap, target=target, metric=metric, contrast=contrast,
        method=method, branch_id=branch_id, limit=limit,
    )


def list_artifacts(snap) -> dict:
    from pertura.core.views import build_artifact_view
    return build_artifact_view(snap)


def inspect_artifact_summary(artifact_id: str = "", path: str = "", snap=None) -> dict:
    artifact = None
    if snap and artifact_id:
        artifact = next((a for a in snap.artifacts if a.artifact_id == artifact_id), None)
        if artifact:
            path = artifact.path
    if not path:
        return {"error": "artifact_id or path is required"}
    try:
        p = _allowed_path(path, snap=snap)
    except PermissionError as e:
        return {"error": str(e)}
    return _preview_artifact_path(p, kind=(artifact.kind if artifact else ""))


def _preview_artifact_path(path: Path, *, kind: str = "") -> dict:
    if not path.exists() or not path.is_file():
        return {"path": str(path), "kind": kind, "typed_kind": "missing", "summary": "File not found."}
    size = path.stat().st_size
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"} or kind == "figure":
        return {"path": str(path), "kind": kind, "typed_kind": "figure", "size_bytes": size}
    if suffix in {".csv", ".tsv"} or kind == "table":
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            rows = []
            for idx, row in enumerate(reader):
                rows.append(row[:20])
                if idx >= 5:
                    break
        return {"path": str(path), "kind": kind, "typed_kind": "table",
                "size_bytes": size, "head_rows": rows}
    if suffix == ".json" or kind in {"audit", "json"}:
        text = path.read_text(encoding="utf-8", errors="replace")[:5000]
        try:
            data = json.loads(text)
            keys = list(data)[:20] if isinstance(data, dict) else []
            return {"path": str(path), "kind": kind, "typed_kind": "json",
                    "size_bytes": size, "keys": keys, "preview": data if size < 5000 else keys}
        except Exception:
            return {"path": str(path), "kind": kind, "typed_kind": "text",
                    "size_bytes": size, "preview": text[:1000]}
    if suffix == ".h5ad" or kind == "anndata":
        return {"path": str(path), "kind": kind, "typed_kind": "h5ad",
                "size_bytes": size, "summary": "AnnData checkpoint; open through analysis code for shape/obs metadata."}
    text = path.read_text(encoding="utf-8", errors="replace")[:2000]
    return {"path": str(path), "kind": kind, "typed_kind": "text",
            "size_bytes": size, "preview": text}


def _open_branch_tool(question: str, reason: str, hypothesis: str = "") -> dict:
    return {"status": "branch_opened", "question": question, "reason": reason, "hypothesis": hypothesis}


def _close_branch_tool(branch_id: str, summary: str, conclusion: str = "", evidence_ids: list | None = None) -> dict:
    return {"status": "branch_closed", "branch_id": branch_id, "summary": summary,
            "conclusion": conclusion, "evidence_ids": evidence_ids or []}


def _compare_branches_tool(snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.views import build_branch_view
    active = [b for b in snap.branches if b.status == "active"]
    by_branch = {}
    for b in active:
        obs = [o for o in snap.observations if o.branch_id == b.branch_id]
        by_branch[b.branch_id] = {
            "title": b.title, "reason": b.reason,
            "observations": [{"target": o.target, "metric": o.metric, "value": o.value,
                              "contrast": o.contrast, "method": o.method} for o in obs[-5:]],
            "attempts": len([a for a in snap.attempts if a.branch_id == b.branch_id]),
        }
    return {"view": build_branch_view(snap), "branches": by_branch}


def _switch_branch_tool(branch_id: str) -> dict:
    return {"status": "branch_switched", "branch_id": branch_id}


def _request_node_transition_tool(target_node_id: str, reason: str = "") -> dict:
    return {"status": "request_node_transition", "target_node_id": target_node_id, "reason": reason}


def _update_design_tool(design: dict, reason: str = "") -> dict:
    return {"status": "update_design", "design": design, "reason": reason}


def _list_analysis_nodes_tool(snap=None) -> dict:
    if snap is None or not snap.analysis_spec:
        return {"nodes": [], "active_node_id": ""}
    from pertura.core.views import build_context_view
    view = build_context_view(snap, purpose="tool:list_analysis_nodes", max_items=8)
    nodes = []
    for node in snap.analysis_spec.get("nodes", []):
        nodes.append({
            "node_id": node.get("node_id", ""),
            "title": node.get("title", ""),
            "purpose": node.get("purpose", ""),
            "allowed_capabilities": node.get("allowed_capabilities", []),
            "next_nodes": node.get("next_nodes", []),
            "recommended_actions": node.get("recommended_actions", []),
            "expected_outputs": node.get("expected_outputs", []),
        })
    return {
        "active_node_id": snap.active_node_id,
        "current_node_progress": view.get("current_node_progress", {}),
        "reachable_nodes": view.get("reachable_nodes", []),
        "blocked_transitions": view.get("blocked_transitions", []),
        "nodes": nodes,
    }


def _list_capabilities_tool(node_id: str = "", snap=None) -> dict:
    if snap is None:
        return {"capabilities": []}
    from pertura.capabilities import CapabilityRegistry
    from pertura.spec.gating import GateEvaluator
    registry = CapabilityRegistry(snap.capabilities)
    target_node_id = node_id or snap.active_node_id
    capability_ids = []
    if target_node_id:
        capability_ids = sorted(GateEvaluator(snap.analysis_spec).allowed_capabilities(target_node_id))
    return {
        "active_node_id": snap.active_node_id,
        "node_id": target_node_id,
        "capabilities": registry.summarize(capability_ids or None, limit=50),
    }


def _get_context_review_tool(
    purpose: str = "audit",
    max_items: int = 8,
    token_budget: int = 6000,
    runtime_state: dict | None = None,
    snap=None,
) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.views import build_view
    safe_max_items = max(1, min(int(max_items or 8), 25))
    safe_token_budget = max(1000, min(int(token_budget or 6000), 20000))
    safe_purpose = purpose if purpose in {"deliberation", "codegen", "critic", "audit"} else "audit"
    return build_view(
        snap,
        purpose=safe_purpose,
        runtime_state=runtime_state or {},
        token_budget=safe_token_budget,
        max_items=safe_max_items,
    )


def _get_audit_toolbox_tool(purpose: str = "deliberation", snap=None) -> dict:
    from pertura.core.audit_toolbox import build_audit_toolbox

    return build_audit_toolbox(snap=snap, purpose=purpose)


def _get_harness_manifest_tool() -> dict:
    from pertura.core import build_harness_manifest

    return build_harness_manifest()


def _audit_run_tool(run_dir: str = "", snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.audit import audit_run
    from pertura.core.graph import build_graph
    return audit_run(snap, build_graph(snap), run_dir=run_dir or None)


def _plan_rethinking_tool(node_id: str = "", issue: str = "", depth: int = 5, snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.rethinking import plan_rethinking

    return plan_rethinking(snap, node_id=node_id, issue=issue, depth=depth)


def _get_node_contract_tool(node_id: str = "", snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    if not snap.analysis_spec:
        return {"error": "No analysis spec available."}
    from pertura.capabilities import CapabilityRegistry
    from pertura.spec.conditions import evaluate_conditions
    from pertura.spec.contracts import node_contract
    from pertura.spec.gating import GateEvaluator

    evaluator = GateEvaluator(snap.analysis_spec)
    spec = evaluator.spec
    if spec is None:
        return {"error": "No analysis spec available."}
    target_node_id = node_id or snap.active_node_id or spec.start_node_id
    node = spec.node(target_node_id)
    if node is None:
        return {"error": f"Unknown analysis node: {target_node_id}"}
    registry = CapabilityRegistry(snap.capabilities)
    contract = node_contract(spec, target_node_id, capabilities=registry)
    entry_gate = evaluator.evaluate_enter(snap, target_node_id)
    completion_gate = evaluator.evaluate_completion(snap, target_node_id)
    requires = evaluate_conditions(node.requires, snap)
    must_confirm = evaluate_conditions(node.must_confirm, snap)
    completion = evaluate_conditions(node.completion, snap)
    capability_readiness = [
        _capability_readiness_card(card, snap=snap)
        for card in contract.get("capabilities", [])
    ]
    missing_inputs = _dedupe_values(
        item
        for card in capability_readiness
        for item in card.get("missing_inputs", [])
    )
    next_actions = _node_next_actions(
        entry_gate=entry_gate,
        completion_gate=completion_gate,
        capability_readiness=capability_readiness,
    )
    completion_ready = all(item.passed or not item.hard for item in completion)
    ready_capabilities = [
        card["id"]
        for card in capability_readiness
        if card.get("ready") and not card.get("missing")
    ]
    status = _node_runtime_status(
        entry_gate=entry_gate,
        completion_ready=completion_ready,
        ready_capabilities=ready_capabilities,
        missing_inputs=missing_inputs,
    )
    return {
        **contract,
        "runtime": {
            "active_node_id": snap.active_node_id,
            "target_node_id": target_node_id,
            "status": status,
            "ready_to_enter": entry_gate.can_enter,
            "ready_to_complete": completion_ready,
            "entry_gate": entry_gate.model_dump(mode="json"),
            "completion_gate": completion_gate.model_dump(mode="json"),
            "condition_results": {
                "requires": [_condition_result_card(item) for item in requires],
                "must_confirm": [_condition_result_card(item) for item in must_confirm],
                "completion": [_condition_result_card(item) for item in completion],
            },
            "capability_readiness": capability_readiness,
            "ready_capabilities": ready_capabilities,
            "missing_inputs": missing_inputs,
            "next_actions": next_actions,
        },
    }


def _get_capability_template_tool(
    capability_id: str,
    mode: str = "",
    target: str = "",
    columns: dict | None = None,
    control_labels: list | None = None,
    parameters: dict | None = None,
    snap=None,
) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.capabilities import CapabilityRegistry
    registry = CapabilityRegistry(snap.capabilities)
    cap = registry.get(capability_id)
    if cap is None:
        return {"error": f"Unknown capability: {capability_id}"}
    mode = mode or (cap.analysis_modes[0] if cap.analysis_modes else "")
    resolved = _resolve_template_inputs(
        snap,
        required_inputs=cap.required_inputs,
        target=target,
        columns=columns or {},
        control_labels=control_labels or [],
        parameters=parameters or {},
    )
    template = _capability_template(
        cap.compact(),
        mode=mode,
        target=resolved["target"],
        columns=resolved["columns"],
        control_labels=resolved["control_labels"],
        parameters=resolved["parameters"],
    )
    readiness = _template_readiness(
        capability_id=cap.capability_id,
        missing_inputs=resolved["missing_inputs"],
    )
    execute_with = {
        "tool": "execute_code",
        "ready": readiness["ready"],
        "blocked_by": readiness["missing_inputs"],
        "args": {
            "capability_ids": [cap.capability_id],
            "stage": cap.stage or mode,
            "design_fields_used": cap.required_inputs,
        },
    }
    return {
        "capability": cap.compact(),
        "mode": mode,
        "ready": readiness["ready"],
        "readiness": readiness,
        "resolved_inputs": resolved["resolved_inputs"],
        "missing_inputs": resolved["missing_inputs"],
        "next_actions": readiness["next_actions"],
        "template": template,
        "execute_with": execute_with,
    }


def _capability_readiness_card(card: dict, *, snap) -> dict:
    if card.get("missing"):
        return {
            "id": card.get("id", ""),
            "missing": True,
            "ready": False,
            "status": "missing_capability",
            "missing_inputs": [],
            "resolved_inputs": {},
            "next_actions": [{
                "action_id": "define_capability",
                "tool": "list_capabilities",
                "reason": f"Capability `{card.get('id', '')}` is referenced by the node but missing from the registry.",
            }],
        }
    resolved = _resolve_template_inputs(
        snap,
        required_inputs=card.get("required_inputs", []),
        target="",
        columns={},
        control_labels=[],
        parameters={},
    )
    readiness = _template_readiness(
        capability_id=card.get("id", ""),
        missing_inputs=resolved["missing_inputs"],
    )
    return {
        "id": card.get("id", ""),
        "kind": card.get("kind", ""),
        "ready": readiness["ready"],
        "status": readiness["status"],
        "missing_inputs": readiness["missing_inputs"],
        "resolved_inputs": resolved["resolved_inputs"],
        "next_actions": readiness["next_actions"],
        "expected_observations": card.get("expected_observations", []),
        "expected_artifacts": card.get("expected_artifacts", []),
        "template_call": (
            {"tool": "get_capability_template", "args": {"capability_id": card.get("id", "")}}
            if card.get("kind") in {"execute", "review", "report"} else {}
        ),
    }


def _node_runtime_status(
    *,
    entry_gate,
    completion_ready: bool,
    ready_capabilities: list[str],
    missing_inputs: list[str],
) -> str:
    if not entry_gate.can_enter:
        return f"blocked_entry_{entry_gate.decision}"
    if completion_ready:
        return "complete_ready"
    if ready_capabilities:
        return "ready_for_capability"
    if missing_inputs:
        return "blocked_missing_inputs"
    return "needs_analysis"


def _node_next_actions(*, entry_gate, completion_gate, capability_readiness: list[dict]) -> list[dict]:
    actions = []
    seen = set()

    def add(action_id: str, tool: str, reason: str, args: dict | None = None):
        if action_id in seen:
            return
        seen.add(action_id)
        action = {
            "action_id": action_id,
            "tool": tool,
            "reason": reason,
        }
        if args:
            action["args"] = args
        actions.append(action)

    if not entry_gate.can_enter:
        tool = "ask_user" if entry_gate.decision == "human_interrupt" else "request_node_transition"
        add(
            "resolve_entry_gate",
            tool,
            entry_gate.reason or "Resolve failed entry gate before executing this analysis node.",
            {"question": entry_gate.reason} if tool == "ask_user" and entry_gate.reason else {},
        )
    for card in capability_readiness:
        if card.get("ready") and card.get("template_call"):
            add(
                f"use_template_{card['id']}",
                "get_capability_template",
                f"Capability `{card['id']}` has its required inputs resolved.",
                {"capability_id": card["id"]},
            )
        for action in card.get("next_actions", []):
            add(
                action.get("action_id", f"resolve_{card.get('id', '')}"),
                action.get("tool", "ask_user"),
                action.get("reason", ""),
                action.get("args", {}),
            )
    if entry_gate.can_enter and not completion_gate.can_enter:
        add(
            "work_toward_completion",
            "execute_code",
            completion_gate.reason or "Register the missing observations/artifacts required to complete this node.",
        )
    return actions[:10]


def _condition_result_card(result) -> dict:
    return {
        "condition_id": result.condition_id,
        "passed": result.passed,
        "tier": result.tier,
        "failure_mode": result.failure_mode,
        "message": result.message,
        "hard": result.hard,
        "details": result.details or {},
    }


def _dedupe_values(values) -> list:
    seen = set()
    out = []
    for value in values:
        if value in ("", None):
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _resolve_template_inputs(
    snap,
    *,
    required_inputs: list[str],
    target: str,
    columns: dict,
    control_labels: list,
    parameters: dict,
) -> dict:
    design = getattr(snap, "design", {}) or {}
    runtime_symbols = _runtime_symbol_names(snap)
    cols = dict(columns or {})
    params = dict(parameters or {})
    if not control_labels and isinstance(design.get("control_labels"), list):
        control_labels = list(design.get("control_labels") or [])
    if "target" not in cols and design.get("target_column"):
        cols["target"] = design.get("target_column")
    if "perturbation" not in cols and design.get("target_column"):
        cols["perturbation"] = design.get("target_column")
    if "guide" not in cols and design.get("guide_column"):
        cols["guide"] = design.get("guide_column")
    if "state" not in cols and design.get("state_column"):
        cols["state"] = design.get("state_column")

    resolved_inputs = {}
    missing_inputs = []
    for item in required_inputs:
        value = _template_input_value(
            item,
            snap=snap,
            target=target,
            columns=cols,
            control_labels=control_labels,
            parameters=params,
            design=design,
        )
        if value in ("", None, [], {}):
            missing_inputs.append(item)
        else:
            resolved_inputs[item] = value
            params.setdefault(item, value)
    return {
        "target": target,
        "columns": cols,
        "control_labels": control_labels,
        "parameters": params,
        "resolved_inputs": resolved_inputs,
        "missing_inputs": missing_inputs,
    }


def _template_readiness(*, capability_id: str, missing_inputs: list[str]) -> dict:
    missing = [str(item) for item in missing_inputs]
    return {
        "ready": not missing,
        "status": "ready" if not missing else "blocked_missing_inputs",
        "missing_inputs": missing,
        "next_actions": [] if not missing else _template_next_actions(capability_id, missing),
    }


def _template_next_actions(capability_id: str, missing_inputs: list[str]) -> list[dict]:
    actions = []
    seen = set()

    def add(action_id: str, tool: str, reason: str, args: dict | None = None):
        if action_id in seen:
            return
        seen.add(action_id)
        action = {
            "action_id": action_id,
            "tool": tool,
            "reason": reason,
        }
        if args:
            action["args"] = args
        actions.append(action)

    for item in missing_inputs:
        if item in {"adata", "adata.obs", "workspace_files"}:
            add(
                "load_or_restore_adata",
                "load_dataset",
                "Load or restore the AnnData object, then confirm it appears in runtime_symbols.",
                {"path": "<dataset_path>"},
            )
        elif item in {"control_labels", "guide_column", "target_column", "state_labels"}:
            add(
                "confirm_design_fields",
                "ask_user",
                "Confirm the missing experimental design fields before generating executable code.",
                {"question": "Please confirm control labels and perturbation/guide/state columns."},
            )
        elif item == "state_reference":
            add(
                "build_state_reference",
                "get_capability_template",
                "Create a reusable embedding/cluster state reference before trajectory analysis.",
                {"capability_id": "build_embedding"},
            )
        elif item in {"effect_result", "effect_table"}:
            add(
                "run_or_find_effect_results",
                "get_capability_template",
                "Produce or locate differential-effect results before downstream effect analysis.",
                {"capability_id": "run_de"},
            )
        elif item in {"conclusions", "supported_observations", "observation_memory"}:
            add(
                "review_evidence_chain",
                "review_evidence_chain",
                "Self-audit conclusion support and execution provenance before summarizing claims.",
                {},
            )
        elif item == "artifacts":
            add(
                "inspect_artifacts",
                "trace_upstream",
                "Trace available artifacts and their upstream evidence before reporting.",
                {"node_id": "<artifact_or_observation_id>"},
            )
        elif item == "branches":
            add(
                "compare_available_branches",
                "compare_branches",
                "Compare existing branch evidence before branch-level conclusions.",
                {},
            )
        elif item == "node_id":
            add(
                "choose_trace_target",
                "trace_upstream",
                "Select a concrete observation, artifact, or node id to expand provenance.",
                {"node_id": "<node_id>"},
            )
        else:
            add(
                f"resolve_{item}",
                "ask_user",
                f"Resolve missing input `{item}` before executing capability `{capability_id}`.",
                {"question": f"Please provide or confirm `{item}` for `{capability_id}`."},
            )
    return actions


def _runtime_symbol_names(snap) -> set[str]:
    names = set()
    runtime_state = getattr(snap, "runtime_state", {}) if hasattr(snap, "runtime_state") else {}
    for source in (
        runtime_state,
        getattr(snap, "design", {}) if hasattr(snap, "design") else {},
    ):
        if isinstance(source, dict):
            variables = source.get("variables", {}) if isinstance(source.get("variables", {}), dict) else {}
            if isinstance(variables, dict):
                names.update(str(key) for key in variables.keys())
    for outcome in getattr(snap, "outcomes", []) or []:
        metrics = getattr(outcome, "metrics", {}) or {}
        kernel_state = metrics.get("kernel_state", {}) if isinstance(metrics, dict) else {}
        if isinstance(kernel_state, dict):
            variables = kernel_state.get("variables", {}) if isinstance(kernel_state.get("variables", {}), dict) else {}
            if isinstance(variables, dict):
                names.update(str(key) for key in variables.keys())
    return names


def _template_input_value(
    item: str,
    *,
    snap,
    target: str,
    columns: dict,
    control_labels: list,
    parameters: dict,
    design: dict,
):
    if item == "control_labels":
        return control_labels or design.get("control_labels")
    if item == "guide_column":
        return columns.get("guide") or design.get("guide_column")
    if item == "target_column":
        return columns.get("target") or columns.get("perturbation") or design.get("target_column")
    if item == "state_labels":
        return columns.get("state") or design.get("state_column")
    if item in {"target", "perturbation"}:
        return target or parameters.get(item) or design.get(item)
    if item == "adata":
        return "adata" if "adata" in _runtime_symbol_names(snap) else None
    if item == "supported_observations":
        ids = [getattr(obs, "observation_id", "") for obs in getattr(snap, "observations", []) or [] if getattr(obs, "observation_id", "")]
        return parameters.get(item) or design.get(item) or ids
    if item == "conclusions":
        ids = [getattr(con, "conclusion_id", "") for con in getattr(snap, "conclusions", []) or [] if getattr(con, "conclusion_id", "")]
        return parameters.get(item) or design.get(item) or ids
    if item == "artifacts":
        ids = [getattr(art, "artifact_id", "") for art in getattr(snap, "artifacts", []) or [] if getattr(art, "artifact_id", "")]
        return parameters.get(item) or design.get(item) or ids
    if item == "branches":
        ids = [getattr(branch, "branch_id", "") for branch in getattr(snap, "branches", []) or [] if getattr(branch, "branch_id", "")]
        return parameters.get(item) or design.get(item) or ids
    if item == "effect_table":
        ids = [
            getattr(art, "artifact_id", "")
            for art in getattr(snap, "artifacts", []) or []
            if getattr(art, "artifact_id", "") and getattr(art, "kind", "") in {"table", "csv", "tsv", "parquet"}
        ]
        return parameters.get(item) or design.get(item) or ids
    if item == "effect_result":
        ids = [
            getattr(art, "artifact_id", "")
            for art in getattr(snap, "artifacts", []) or []
            if getattr(art, "artifact_id", "") and (
                getattr(art, "kind", "") in {"table", "csv", "tsv", "parquet"}
                or "de" in str(getattr(art, "summary", "")).lower()
            )
        ]
        return parameters.get(item) or design.get(item) or ids
    if item == "state_reference":
        ids = [
            getattr(art, "artifact_id", "")
            for art in getattr(snap, "artifacts", []) or []
            if getattr(art, "artifact_id", "") and getattr(art, "kind", "") in {"embedding", "cluster_table", "annotation_table", "module_table", "checkpoint"}
        ]
        return parameters.get(item) or design.get(item) or ids
    if item == "node_id":
        return parameters.get(item) or design.get(item) or getattr(snap, "active_node_id", "")
    if item == "observation_memory":
        return parameters.get(item) or design.get(item) or {
            "summary": {
                "variable_count": len(getattr(snap, "observations", []) or []),
                "available": bool(getattr(snap, "observations", []) or []),
            }
        }
    return parameters.get(item) or design.get(item)


def _capability_template(
    capability: dict,
    *,
    mode: str = "",
    target: str = "",
    columns: dict | None = None,
    control_labels: list | None = None,
    parameters: dict | None = None,
) -> dict:
    cap_id = capability.get("id", "")
    mode = mode or (capability.get("analysis_modes") or [""])[0]
    params = parameters or {}
    cols = columns or {}
    code = _template_code_for(
        cap_id,
        mode=mode,
        target=target,
        columns=cols,
        control_labels=control_labels or [],
        parameters=params,
    )
    checklist = []
    for value in capability.get("required_inputs", []):
        checklist.append(f"confirm input available: {value}")
    for value in capability.get("expected_observations", []):
        checklist.append(f"register_observation for {value}")
    for value in capability.get("expected_artifacts", []):
        checklist.append(f"register_artifact for {value}")
    return {
        "template_id": f"{cap_id}:{mode or 'default'}",
        "capability_id": cap_id,
        "mode": mode,
        "packages": capability.get("packages", []),
        "functions": capability.get("functions", []),
        "analysis_modes": capability.get("analysis_modes", []),
        "inputs": {
            "target": target,
            "columns": cols,
            "control_labels": control_labels or [],
            "parameters": params,
        },
        "code": code,
        "checklist": checklist,
        "notes": [
            "Inspect runtime_symbols before reloading data.",
            "Replace placeholder column names with confirmed design fields.",
            "Register observations/artifacts through the runtime helpers.",
        ],
    }


def _template_code_for(
    capability_id: str,
    *,
    mode: str = "",
    target: str = "",
    columns: dict | None = None,
    control_labels: list | None = None,
    parameters: dict | None = None,
) -> str:
    columns = columns or {}
    control_labels = control_labels or []
    parameters = parameters or {}
    target_expr = repr(target or "<target>")
    perturb_col_expr = repr(columns.get("perturbation") or columns.get("target") or "<target_or_perturbation_column>")
    guide_col_expr = repr(columns.get("guide") or "<guide_column>")
    target_col_expr = repr(columns.get("target") or "<target_column>")
    state_col_expr = repr(columns.get("state") or "<state_column>")
    control_expr = repr(control_labels)
    if capability_id == "run_de":
        return f"""# Differential expression / effect-size skeleton.
# Assumes `adata` is loaded and control_labels / target labels are confirmed.
import pandas as pd
import scanpy as sc

target = {target_expr}
perturb_col = {perturb_col_expr}
control_labels = {control_expr} or (list(design.get("control_labels", [])) if "design" in globals() else [])
groupby = perturb_col

if not control_labels:
    raise ValueError("control_labels must be confirmed before run_de")
if groupby not in adata.obs:
    raise ValueError(f"Missing perturbation column: {{groupby}}")

sc.tl.rank_genes_groups(
    adata,
    groupby=groupby,
    groups=[target],
    reference=control_labels[0],
    method="wilcoxon",
)
de = sc.get.rank_genes_groups_df(adata, group=target)
de_path = artifacts_dir / f"de_{{target}}.csv"
de.to_csv(de_path, index=False)

top = de.iloc[0].to_dict() if len(de) else {{}}
register_observation("de_effect", target=target, metric="logFC", value=float(top.get("logfoldchanges", 0.0)), method="scanpy.rank_genes_groups")
register_observation("de_effect", target=target, metric="p_value", value=float(top.get("pvals_adj", top.get("pvals", 1.0))), method="scanpy.rank_genes_groups")
register_artifact(str(de_path), kind="table", summary=f"DE results for {{target}}")"""
    if capability_id == "check_target_coverage":
        return f"""# Target coverage skeleton.
import pandas as pd

target_col = {target_col_expr}
guide_col = {guide_col_expr}
if target_col not in adata.obs:
    raise ValueError(f"Missing target column: {{target_col}}")

coverage = adata.obs.groupby(target_col).size().reset_index(name="n_cells")
coverage_path = artifacts_dir / "target_coverage.csv"
coverage.to_csv(coverage_path, index=False)
register_observation("target_coverage", target="all_targets", metric="n_targets", value=int(coverage.shape[0]), method="groupby_count")
register_artifact(str(coverage_path), kind="table", summary="Cells per perturbation target")"""
    if capability_id in {"run_qc", "plot_qc", "filter_cells"}:
        return """# scRNA-seq QC skeleton.
import scanpy as sc

sc.pp.calculate_qc_metrics(adata, inplace=True)
register_observation("qc_metric", target="cells", metric="n_cells", value=int(adata.n_obs), method="scanpy.calculate_qc_metrics")
register_observation("qc_metric", target="genes", metric="n_genes", value=int(adata.n_vars), method="scanpy.calculate_qc_metrics")
qc_path = artifacts_dir / "qc_obs_metrics.csv"
adata.obs.to_csv(qc_path)
register_artifact(str(qc_path), kind="table", summary="Cell-level QC metrics")"""
    if capability_id in {"assign_guides", "audit_guide_counts", "audit_guide_mapping"}:
        return f"""# Guide assignment / audit skeleton.
import pandas as pd

guide_col = {guide_col_expr}
if guide_col not in adata.obs:
    raise ValueError(f"Missing guide column: {{guide_col}}")

guide_counts = adata.obs[guide_col].value_counts(dropna=False).reset_index()
guide_counts.columns = [guide_col, "n_cells"]
guide_path = artifacts_dir / "guide_counts.csv"
guide_counts.to_csv(guide_path, index=False)
register_observation("guide_count", target="all_guides", metric="n_guides", value=int(guide_counts.shape[0]), method="value_counts")
register_artifact(str(guide_path), kind="table", summary="Guide count distribution")"""
    if capability_id in {"state_reference", "build_embedding", "cluster_cells", "annotate_states", "score_modules", "learn_gene_modules"}:
        return f"""# State-reference skeleton.
import scanpy as sc
import pandas as pd

if 'adata' not in globals():
    raise ValueError("adata must be loaded before state_reference")

sc.pp.pca(adata)
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.tl.leiden(adata)

embedding_path = artifacts_dir / "state_embedding.csv"
cluster_path = artifacts_dir / "cluster_assignments.csv"
adata.obsm["X_umap"][:].tolist() if "X_umap" in adata.obsm else None
pd.DataFrame(adata.obsm["X_umap"], columns=["UMAP1", "UMAP2"]).to_csv(embedding_path, index=False)
pd.DataFrame({{"cluster": adata.obs["leiden"]}}).to_csv(cluster_path, index=False)
register_observation("embedding_summary", target="all_cells", metric="embedding", value="umap", method="scanpy.tl.umap")
register_observation("cluster_summary", target="all_cells", metric="clusters", value=int(adata.obs["leiden"].nunique()), method="scanpy.tl.leiden")
register_artifact(str(embedding_path), kind="table", summary="UMAP embedding coordinates")
register_artifact(str(cluster_path), kind="table", summary="Cluster assignments")"""
    if capability_id == "trajectory_analysis":
        return f"""# Trajectory / fate-bias skeleton.
import scanpy as sc
import pandas as pd

state_col = {state_col_expr}
if 'adata' not in globals():
    raise ValueError("adata must be loaded before trajectory_analysis")
if state_col not in adata.obs:
    raise ValueError(f"Missing state column: {{state_col}}")

sc.tl.diffmap(adata)
sc.tl.dpt(adata)
traj_path = artifacts_dir / "trajectory_scores.csv"
pd.DataFrame({{"dpt_pseudotime": adata.obs.get("dpt_pseudotime", pd.Series(index=adata.obs_names))}}).to_csv(traj_path, index=False)
register_observation("trajectory_effect", target="all_cells", metric="trajectory", value="dpt", method="scanpy.tl.dpt")
register_artifact(str(traj_path), kind="table", summary="Trajectory or fate-bias scores")"""
    if capability_id in {"compare_methods", "composition_test", "co_regulated_modules"}:
        methods = parameters.get("methods") or ["wilcoxon", "t-test"]
        contrast = parameters.get("contrast") or "<contrast>"
        return f"""# Effect exploration / method comparison skeleton.
import pandas as pd

target = {target_expr}
state_col = {state_col_expr}
mode = {repr(mode or '<analysis_mode>')}
methods = {repr(methods)}
contrast = {repr(contrast)}

plan_path = artifacts_dir / f"{{target}}_{{mode}}_plan.txt"
plan_path.write_text(f"target={{target}}\\nmode={{mode}}\\nstate_col={{state_col}}\\n", encoding="utf-8")
register_observation("effect_plan", target=target, metric="mode", value=mode, method="template")
for method in methods:
    register_observation("method_sensitivity", target=target, metric="method", value=method, contrast=contrast, method="template")
register_artifact(str(plan_path), kind="text", summary="Method comparison or effect exploration plan")"""
    if capability_id == "generate_report":
        conclusion_ids = parameters.get("conclusions", [])
        artifact_ids = parameters.get("artifacts", [])
        return f"""# Report skeleton.
import pandas as pd

report_path = artifacts_dir / "perturbseq_report.md"
rows = []
for conclusion_id in {repr(conclusion_ids)}:
    rows.append(f"- conclusion: {{conclusion_id}}")
for artifact_id in {repr(artifact_ids)}:
    rows.append(f"- artifact: {{artifact_id}}")
report_path.write_text("\\n".join([
    "# Perturb-seq report",
    "",
    "## Observations",
    *rows,
]), encoding="utf-8")
register_artifact(str(report_path), kind="report", summary="Perturb-seq report")
register_observation("report_summary", target="run", metric="sections", value="observations/conclusions", method="template")"""
    return """# Capability skeleton.
# Inspect active_contract and runtime_symbols, then implement one bounded step.
register_observation("analysis_step", target="<target>", metric="<metric>", value="<value>", method="template")"""


def _evaluate_node_conditions_tool(target_node_id: str, snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.spec.gating import GateEvaluator
    gate = GateEvaluator(snap.analysis_spec).evaluate_enter(snap, target_node_id)
    return gate.model_dump(mode="json")


def _sweep_thresholds(parameter: str, values: list, metric: str) -> dict:
    return {
        "plan": f"Sweep {parameter} across {values} and register {metric} for each",
        "pattern": f"for val in {values}:\\n    register_observation('sweep', target='{parameter}', metric='{metric}', value=result, parameters={{'{parameter}': val}})",
        "register_each": True, "expected_obs": len(values),
    }


def _compare_methods(target: str, methods: list, contrast: str = "") -> dict:
    return {
        "plan": f"Compare methods {methods} for {target}",
        "pattern": f"for method in {methods}:\\n    register_observation('de_effect', target='{target}', metric='logFC', value=result, contrast='{contrast}', method=method)",
        "register_each": True, "expected_obs": len(methods),
    }


def _trace_upstream_tool(node_id: str, depth: int = 4, snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.graph import build_graph
    from pertura.core.views import build_trace_view
    return build_trace_view(build_graph(snap), node_id, depth=depth)


def _review_evidence_chain_tool(node_id: str = "", limit: int = 12, snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.evidence_chain import review_evidence_chain
    from pertura.core.graph import build_graph
    return review_evidence_chain(snap, node_id=node_id, graph=build_graph(snap), limit=limit)


def _impact_of_change_tool(node_id: str, depth: int = 4, snap=None) -> dict:
    if snap is None:
        return {"error": "No snapshot available."}
    from pertura.core.graph import build_graph
    from pertura.core.views import build_impact_view
    return build_impact_view(build_graph(snap), node_id, depth=depth)


def _load_dataset_tool(path: str, dataset_id: str = "", snap=None) -> dict:
    """Detect format of a single-cell data file or directory, return ready-to-execute
    code and column hints so the LLM does not need to hand-write data loading.

    This is a read tool — the LLM must follow up with execute_code to load into
    the kernel. It returns a compact code block and per-column pattern hints.
    """
    try:
        p = _allowed_path(path, snap=snap) if snap is not None else Path(path).expanduser().resolve()
    except PermissionError as exc:
        return {"status": "error", "error": str(exc)}
    if not p.exists():
        return {"status": "error", "error": f"Path not found: {path}"}

    fmt = ""
    target = p
    if p.is_dir():
        h5s = list(p.glob("*filtered*h5")) or list(p.glob("*.h5"))
        mtxs = list(p.glob("*.mtx")) + list(p.glob("*.mtx.gz"))
        zarrs = [d for d in p.iterdir() if d.is_dir() and (d / ".zarray").exists()]
        if h5s:
            target, fmt = h5s[0], "10x_h5"
        elif mtxs:
            fmt = "10x_mtx"
        elif zarrs:
            target, fmt = zarrs[0], "zarr"
    suffix = p.suffix.lower()
    suffixes = "".join(p.suffixes).lower()
    if fmt:
        pass
    elif suffix == ".h5ad":
        fmt = "h5ad"
    elif suffix in {".h5", ".hdf5"}:
        fmt = "10x_h5"
    elif suffixes.endswith(".csv.gz") or suffix == ".csv":
        fmt = "csv"
    elif suffixes.endswith(".tsv.gz") or suffix == ".tsv":
        fmt = "tsv"
    elif suffix == ".parquet":
        fmt = "parquet"

    if not fmt:
        return {"status": "error",
                "error": f"Cannot detect data format for {path}. "
                         f"Supported: .h5ad, .h5 (10X), 10X mtx directory, zarr, csv/tsv/parquet."}

    target_repr = repr(str(target))
    if fmt == "h5ad":
        code = (
            f"import scanpy as sc\n"
            f"adata = sc.read_h5ad({target_repr})\n"
            f"print(f'Shape: {{adata.shape[0]}}x{{adata.shape[1]}}')\n"
            f"print(f'obs: {{\", \".join(list(adata.obs.columns)[:40])}}')\n"
            f"register_observation('schema', target='anndata', metric='shape', "
            f"value=f'{{adata.shape[0]}}x{{adata.shape[1]}}', method='scanpy.read_h5ad')\n"
            f"register_observation('schema', target='obs_columns', metric='columns', "
            f"value=list(adata.obs.columns)[:50], method='auto_inspect')"
        )
    elif fmt == "10x_h5":
        code = (
            f"import scanpy as sc\n"
            f"adata = sc.read_10x_h5({target_repr})\n"
            f"adata.var_names_make_unique()\n"
            f"print(f'Shape: {{adata.shape[0]}}x{{adata.shape[1]}}')\n"
            f"print(f'obs: {{\", \".join(list(adata.obs.columns)[:40])}}')\n"
            f"register_observation('schema', target='anndata', metric='shape', "
            f"value=f'{{adata.shape[0]}}x{{adata.shape[1]}}', method='scanpy.read_10x_h5')"
        )
    elif fmt == "10x_mtx":
        code = (
            f"import scanpy as sc\n"
            f"adata = sc.read_10x_mtx({repr(str(p))}, gex_only=False)\n"
            f"adata.var_names_make_unique()\n"
            f"print(f'Shape: {{adata.shape[0]}}x{{adata.shape[1]}}')\n"
            f"print(f'obs: {{\", \".join(list(adata.obs.columns)[:40])}}')\n"
            f"register_observation('schema', target='anndata', metric='shape', "
            f"value=f'{{adata.shape[0]}}x{{adata.shape[1]}}', method='scanpy.read_10x_mtx')"
        )
    elif fmt == "zarr":
        code = (
            f"import anndata as ad\n"
            f"adata = ad.read_zarr({target_repr})\n"
            f"print(f'Shape: {{adata.shape[0]}}x{{adata.shape[1]}}')\n"
            f"print(f'obs: {{\", \".join(list(adata.obs.columns)[:40])}}')\n"
            f"register_observation('schema', target='anndata', metric='shape', "
            f"value=f'{{adata.shape[0]}}x{{adata.shape[1]}}', method='anndata.read_zarr')\n"
            f"register_observation('schema', target='obs_columns', metric='columns', "
            f"value=list(adata.obs.columns)[:50], method='auto_inspect')"
        )
    else:
        reader = "pd.read_parquet"
        read_expr = f"pd.read_parquet({target_repr})"
        if fmt == "csv":
            reader = "pd.read_csv"
            read_expr = f"pd.read_csv({target_repr}, nrows=50)"
        elif fmt == "tsv":
            reader = "pd.read_csv"
            read_expr = f"pd.read_csv({target_repr}, sep='\\t', nrows=50)"
        code = (
            f"import pandas as pd\n"
            f"df = {read_expr}\n"
            f"print(f'Shape: {{df.shape}}')\n"
            f"print(f'Columns: {{\", \".join(df.columns.tolist())}}')\n"
            f"register_observation('schema', target='dataframe', metric='shape', "
            f"value=f'{{df.shape[0]}}x{{df.shape[1]}}', method='{reader}')"
        )

    return {
        "status": "ok",
        "format": fmt,
        "path": str(target),
        "code": code,
        "note": "Call execute_code with the 'code' field to load data into the kernel as 'adata' or 'df'.",
    }


def _execute_code_stub(
    code: str,
    title: str = "",
    stage: str = "",
    capability_ids: list | None = None,
    parameters: dict | None = None,
    design_fields_used: list | None = None,
    expected_runtime_seconds: float | None = None,
) -> dict:
    return {
        "status": "execute_code",
        "code": code,
        "title": title,
        "stage": stage,
        "capability_ids": capability_ids or [],
        "parameters": parameters or {},
        "design_fields_used": design_fields_used or [],
        "expected_runtime_seconds": expected_runtime_seconds,
    }


def _submit_job_stub(
    script: str,
    title: str = "",
    stage: str = "",
    capability_ids: list | None = None,
    backend: str = "subprocess",
    resources: dict | None = None,
    parameters: dict | None = None,
    design_fields_used: list | None = None,
    expected_outputs: list | None = None,
    expected_observations: list | None = None,
    manifest_path: str = "",
) -> dict:
    return {
        "status": "submit_job",
        "script": script,
        "title": title,
        "stage": stage,
        "capability_ids": capability_ids or [],
        "backend": backend,
        "resources": resources or {},
        "parameters": parameters or {},
        "design_fields_used": design_fields_used or [],
        "expected_outputs": expected_outputs or [],
        "expected_observations": expected_observations or [],
        "manifest_path": manifest_path,
    }


def _retry_stub(
    code: str,
    capability_ids: list | None = None,
    parameters: dict | None = None,
    design_fields_used: list | None = None,
    expected_runtime_seconds: float | None = None,
) -> dict:
    return {
        "status": "retry",
        "code": code,
        "capability_ids": capability_ids or [],
        "parameters": parameters or {},
        "design_fields_used": design_fields_used or [],
        "expected_runtime_seconds": expected_runtime_seconds,
    }


def _ask_user_stub(question: str) -> dict:
    return {"status": "ask_user", "question": question}


def _finish_stub(summary: str = "") -> dict:
    return {"status": "finish", "summary": summary}


def _complete_node_stub(summary: str = "", node_id: str = "") -> dict:
    return {"status": "complete_node", "summary": summary, "node_id": node_id}


def _skip_node_stub(node_id: str = "", reason: str = "") -> dict:
    return {"status": "skip_node", "node_id": node_id, "reason": reason}


# ── Registry ────────────────────────────────────────────────────────────

TOOLS = {
    "query_observations": {
        "fn": query_observations,
        "description": "Query prior observations for a target/metric across all attempts and branches.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Gene, target, or feature name"},
                "metric": {"type": "string", "description": "Metric like logFC, p_value, coverage"},
                "contrast": {"type": "string", "description": "Optional contrast filter"},
                "method": {"type": "string", "description": "Optional method filter"},
                "branch_id": {"type": "string", "description": "Optional branch filter"},
            },
        },
    },
    "query_observation_memory": {
        "fn": query_observation_memory,
        "description": "Query variable-level observation memory, including conflicts, coverage, methods, branches, and prior values.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Gene, target, or feature name"},
                "metric": {"type": "string", "description": "Metric like logFC, p_value, coverage"},
                "contrast": {"type": "string", "description": "Optional contrast filter"},
                "method": {"type": "string", "description": "Optional method filter"},
                "branch_id": {"type": "string", "description": "Optional branch filter"},
                "limit": {"type": "integer", "description": "Maximum variables to return"},
            },
        },
    },
    "view_plot": {
        "fn": view_plot,
        "description": "View a generated plot/image using VLM.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    "read_file": {
        "fn": read_file,
        "description": "Read first lines of a text/CSV/JSON output file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_lines": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    "list_artifacts": {
        "fn": list_artifacts,
        "description": "List compact metadata for artifacts registered in the run.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "inspect_artifact_summary": {
        "fn": inspect_artifact_summary,
        "description": "Return a compact typed preview for an artifact. Does not return full large files.",
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
    "load_dataset": {
        "fn": _load_dataset_tool,
        "description": "Detect format and return ready-to-run loading code for single-cell data (.h5ad, 10X .h5, 10X mtx dir, zarr, csv/tsv/parquet). Use BEFORE execute_code to avoid hand-writing data loading.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (.h5ad, .h5) or directory path (10X mtx, zarr)."},
                "dataset_id": {"type": "string", "description": "Optional label for the dataset."},
            },
            "required": ["path"],
        },
    },
    "list_analysis_nodes": {
        "fn": _list_analysis_nodes_tool,
        "description": "List analysis SOP nodes, active node, purpose, capabilities, and adjacency.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "list_capabilities": {
        "fn": _list_capabilities_tool,
        "description": "List typed capability contracts for the active node or a specified analysis node.",
        "parameters": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": [],
        },
    },
    "get_node_contract": {
        "fn": _get_node_contract_tool,
        "description": "Return a public analysis-node contract plus current runtime readiness, missing inputs, gate status, and next actions.",
        "parameters": {
            "type": "object",
            "properties": {"node_id": {"type": "string", "description": "Optional analysis node id. Defaults to the active node."}},
            "required": [],
        },
    },
    "get_context_review": {
        "fn": _get_context_review_tool,
        "description": "Return the bounded LLM context dashboard: protected context, runtime symbols, active contract, provenance index, audit preview with next actions, risks, affordances, and budget report.",
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string", "description": "deliberation, codegen, critic, or audit. Defaults to audit."},
                "max_items": {"type": "integer", "description": "Maximum recent items per bounded section, capped by the harness."},
                "token_budget": {"type": "integer", "description": "Soft context budget used by the view builder."},
                "runtime_state": {"type": "object", "description": "Optional live kernel/job/process state to merge into the dashboard."},
            },
            "required": [],
        },
    },
    "get_audit_toolbox": {
        "fn": _get_audit_toolbox_tool,
        "description": "Return a compact index of local-read self-audit tools, when to call each one, and which tools expand context versus keep it bounded.",
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": {"type": "string", "description": "deliberation, codegen, critic, audit, or report. Defaults to deliberation."},
            },
            "required": [],
        },
    },
    "get_harness_manifest": {
        "fn": _get_harness_manifest_tool,
        "description": "Return the public Pertura-v2 harness thesis, core primitives, and mapping from common agent-harness vocabulary to Pertura surfaces.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "audit_run": {
        "fn": _audit_run_tool,
        "description": "Run a deterministic audit of the current analysis: graph validity, node coverage, evidence support, stale dependencies, artifact files, open gates, blocking findings, and issue-aware next actions.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_dir": {"type": "string", "description": "Optional run directory used to verify artifact paths."},
            },
            "required": [],
        },
    },
    "plan_rethinking": {
        "fn": _plan_rethinking_tool,
        "description": "Turn a suspicious, failed, stale, weak, or unsupported result into a trace-driven repair plan: evidence review, upstream roots, downstream impact, and recommended next tool calls.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Finding, conclusion, observation, artifact, attempt, or trigger id. Defaults to the most relevant current issue."},
                "issue": {"type": "string", "description": "Short description of why this needs rethinking, e.g. empty DE, suspicious biology, stale support, bad plot."},
                "depth": {"type": "integer", "description": "Maximum graph walk depth. Defaults to 5."},
            },
            "required": [],
        },
    },
    "get_capability_template": {
        "fn": _get_capability_template_tool,
        "description": "Return a bounded code skeleton, packages/functions, and audit checklist for a capability analysis mode.",
        "parameters": {
            "type": "object",
            "properties": {
                "capability_id": {"type": "string", "description": "Capability id such as run_de, run_qc, check_target_coverage."},
                "mode": {"type": "string", "description": "Optional analysis mode from the capability contract."},
                "target": {"type": "string", "description": "Optional perturbation target, gene, or feature to splice into the template."},
                "columns": {
                    "type": "object",
                    "description": "Confirmed design columns, e.g. {'target': 'gene', 'guide': 'guide_id', 'perturbation': 'target_gene', 'state': 'leiden'}.",
                },
                "control_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Confirmed control labels for contrast-based templates.",
                },
                "parameters": {"type": "object", "description": "Optional template parameters."},
            },
            "required": ["capability_id"],
        },
    },
    "evaluate_node_conditions": {
        "fn": _evaluate_node_conditions_tool,
        "description": "Preview whether a target analysis node can be entered under current memory/design state.",
        "parameters": {
            "type": "object",
            "properties": {"target_node_id": {"type": "string"}},
            "required": ["target_node_id"],
        },
    },
    "request_node_transition": {
        "fn": _request_node_transition_tool,
        "description": "Request transition to another analysis node. The harness evaluates gates before entering.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_node_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["target_node_id"],
        },
    },
    "open_branch": {
        "fn": _open_branch_tool,
        "description": "Open a new analysis branch for hypothesis testing or parameter exploration.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "hypothesis": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["question", "reason"],
        },
    },
    "compare_branches": {
        "fn": _compare_branches_tool,
        "description": "Compare observations across all active branches side by side.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "switch_branch": {
        "fn": _switch_branch_tool,
        "description": "Switch the active analysis branch.",
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "string"}},
            "required": ["branch_id"],
        },
    },
    "close_branch": {
        "fn": _close_branch_tool,
        "description": "Close a branch with summary and evidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_id": {"type": "string"},
                "summary": {"type": "string"},
                "conclusion": {"type": "string"},
            },
            "required": ["branch_id", "summary"],
        },
    },
    "sweep_thresholds": {
        "fn": _sweep_thresholds,
        "description": "Plan a parameter sweep. Returns code template to test multiple threshold values.",
        "parameters": {
            "type": "object",
            "properties": {
                "parameter": {"type": "string"},
                "values": {"type": "array", "items": {"type": "number"}},
                "metric": {"type": "string"},
            },
            "required": ["parameter", "values", "metric"],
        },
    },
    "compare_methods": {
        "fn": _compare_methods,
        "description": "Plan a method comparison. Returns code template to compare DE methods.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "methods": {"type": "array", "items": {"type": "string"}},
                "contrast": {"type": "string"},
            },
            "required": ["target", "methods"],
        },
    },
    "trace_upstream": {
        "fn": _trace_upstream_tool,
        "description": "Trace upstream graph dependencies for a conclusion, observation, artifact, or attempt.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer"},
            },
            "required": ["node_id"],
        },
    },
    "review_evidence_chain": {
        "fn": _review_evidence_chain_tool,
        "description": "Self-audit whether a conclusion, observation, or artifact is backed by successful, non-stale evidence; returns checks and next actions.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Conclusion, observation, or artifact id. Defaults to the latest conclusion/observation."},
                "limit": {"type": "integer", "description": "Maximum support ids to inspect."},
            },
            "required": [],
        },
    },
    "impact_of_change": {
        "fn": _impact_of_change_tool,
        "description": "List downstream graph nodes that may be affected if a node is changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer"},
            },
            "required": ["node_id"],
        },
    },
    "search_web": {
        "fn": search_web,
        "description": "Search the web for gene function, pathway info, or scientific context.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    # ── State-change tools (call these to act on the analysis) ──────────
    "execute_code": {
        "fn": _execute_code_stub,
        "description": "Execute Python code in the analysis kernel. Use for each analysis step. The code runs in a persistent Jupyter kernel — variables and imports persist across calls.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "title": {"type": "string", "description": "Short title for this step"},
                "stage": {"type": "string", "description": "Analysis stage: inspect, qc, guide_assignment, de, ..."},
                "capability_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability contract(s) this code is committing under, selected from the active node's allowed capabilities.",
                },
                "parameters": {"type": "object", "description": "Structured analysis parameters used by this attempt"},
                "design_fields_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Design fields this code depends on, such as control_labels, guide_column, target_column, batch_column",
                },
                "expected_runtime_seconds": {
                    "type": "number",
                    "description": "Best estimate for this cell. Long UMAP/Harmony/DE jobs should request a larger soft timeout.",
                },
            },
            "required": ["code"],
        },
    },
    "submit_job": {
        "fn": _submit_job_stub,
        "description": "Submit a long-running Python analysis job as a run-directory script. Use for heavy DE, UMAP, Harmony, or batch computations; keep execute_code for short notebook exploration.",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Python script body to run as a durable job"},
                "title": {"type": "string", "description": "Short job title"},
                "stage": {"type": "string", "description": "Analysis stage or active node"},
                "capability_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability contract(s) this job commits under.",
                },
                "backend": {
                    "type": "string",
                    "enum": ["subprocess", "docker"],
                    "description": "Job backend. Docker is the production isolation path.",
                },
                "resources": {
                    "type": "object",
                    "description": "Requested resources such as cpus, memory_gb, timeout_minutes, docker_image.",
                },
                "parameters": {"type": "object", "description": "Structured analysis parameters used by this job"},
                "design_fields_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Design fields this job depends on.",
                },
                "expected_outputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Expected output artifact kinds or relative paths.",
                },
                "expected_observations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Expected observation metrics/types that the manifest should register.",
                },
                "manifest_path": {
                    "type": "string",
                    "description": "Optional manifest path under the run artifacts directory. Defaults to attempt manifest.",
                },
            },
            "required": ["script"],
        },
    },
    "retry": {
        "fn": _retry_stub,
        "description": "Retry the last failed code execution with corrected code.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Corrected Python code"},
                "capability_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability contract(s) this retry is committing under, selected from the active node's allowed capabilities.",
                },
                "parameters": {"type": "object", "description": "Structured analysis parameters used by this retry"},
                "design_fields_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Design fields this retry depends on",
                },
                "expected_runtime_seconds": {
                    "type": "number",
                    "description": "Best estimate for this retry cell.",
                },
            },
            "required": ["code"],
        },
    },
    "ask_user": {
        "fn": _ask_user_stub,
        "description": "Request input from the user. Use when you cannot proceed without human guidance (control labels, design questions, biological interpretation).",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "What you need from the user"},
            },
            "required": ["question"],
        },
    },
    "complete_node": {
        "fn": _complete_node_stub,
        "description": "Mark the current analysis node complete. The harness evaluates completion criteria first.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": [],
        },
    },
    "skip_node": {
        "fn": _skip_node_stub,
        "description": "Skip an analysis node when it is not applicable. This records an explicit node_skipped event.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    "update_design": {
        "fn": _update_design_tool,
        "description": "Record structured design information obtained from the user or dataset audit.",
        "parameters": {
            "type": "object",
            "properties": {
                "design": {"type": "object"},
                "reason": {"type": "string"},
            },
            "required": ["design"],
        },
    },
    "finish": {
        "fn": _finish_stub,
        "description": "Complete the analysis and generate final conclusions. Call when you have sufficient evidence to report.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One-sentence summary of what was accomplished"},
            },
            "required": [],
        },
    },
}


def execute_tool(name: str, args: dict, snap=None) -> dict:
    """Execute a tool by name. snap is injected for tools that need it."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}
    fn = TOOLS[name]["fn"]
    try:
        snap_tools = (
            "query_observations", "query_observation_memory",
            "list_artifacts", "compare_branches",
            "read_file", "view_plot", "inspect_artifact_summary",
            "trace_upstream", "review_evidence_chain", "plan_rethinking", "impact_of_change",
            "list_analysis_nodes", "evaluate_node_conditions",
            "list_capabilities", "get_node_contract", "get_context_review",
            "get_audit_toolbox", "audit_run", "get_capability_template",
            "load_dataset",
        )
        if name in snap_tools and snap is not None:
            return fn(**args, snap=snap)
        return fn(**args)
    except Exception as exc:
        return {"error": str(exc)}


def _scoped_tool_names(snap=None) -> set[str]:
    """Return the current LLM action surface without changing hard gates.

    This is an ergonomics layer, not an authorization boundary. Gated dispatch
    and the permission model remain the authoritative enforcement points.
    """
    names = {
        "get_context_review",
        "get_node_contract",
        "get_capability_template",
        "load_dataset",
        "request_node_transition",
        "evaluate_node_conditions",
        "execute_code",
        "submit_job",
        "complete_node",
        "ask_user",
        "finish",
    }

    open_interrupts = [
        item for item in (getattr(snap, "interrupts", []) or [])
        if getattr(item, "status", "") == "open"
    ] if snap is not None else []
    if open_interrupts:
        names = {"get_context_review", "get_node_contract", "ask_user", "update_design", "finish"}
        return {name for name in names if name in TOOLS}

    has_issue = False
    if snap is not None:
        has_issue = any(
            getattr(item, "status", "") == "open"
            for item in (getattr(snap, "triggers", []) or [])
        ) or any(
            getattr(item, "severity", "") in {"warning", "blocking"}
            for item in (getattr(snap, "findings", []) or [])
        ) or any(
            getattr(item, "status", "") in {"failed", "error"}
            for item in (getattr(snap, "outcomes", []) or [])[-3:]
        )
    if has_issue:
        names.update({
            "get_audit_toolbox",
            "audit_run",
            "plan_rethinking",
            "trace_upstream",
            "review_evidence_chain",
            "impact_of_change",
            "inspect_artifact_summary",
            "retry",
        })

    if snap is not None and getattr(snap, "active_branch", "main") != "main":
        names.update({"close_branch", "switch_branch", "compare_branches"})

    if snap is not None:
        has_analysis_graph = bool(getattr(snap, "analysis_spec", {}) or {})
        if has_analysis_graph and not getattr(snap, "active_node_id", ""):
            names.difference_update({"execute_code", "submit_job", "retry", "complete_node"})
            names.update({"list_analysis_nodes"})

        try:
            budget = getattr(snap, "budget", None)
            max_attempts = int(getattr(budget, "max_attempts", 0) or 0)
            attempts = getattr(snap, "attempts", []) or []
            if max_attempts and len(attempts) >= max_attempts:
                names.difference_update({"execute_code", "submit_job", "retry"})
        except Exception:
            pass

    return {name for name in names if name in TOOLS}


def tool_schemas(readonly: bool = False, *, snap=None, scoped: bool = False) -> list[dict]:
    """Return OpenAI-compatible tool schemas.

    When readonly=True, only local_read tools are returned. External reads
    such as web/VLM calls and all execute/state-change tools are excluded.

    When scoped=True, state-changing and execution tools are filtered to the
    current run phase. This reduces LLM tool-choice noise but does not replace
    runtime gate checks.
    """
    from .permissions import ToolPermission, _TOOL_TIERS

    if readonly:
        tools = {k: v for k, v in TOOLS.items()
                 if _TOOL_TIERS.get(k, ToolPermission.local_read) == ToolPermission.local_read}
    elif scoped:
        scoped_names = _scoped_tool_names(snap)
        tools = {k: v for k, v in TOOLS.items() if k in scoped_names}
    else:
        tools = TOOLS
    return [{
        "type": "function",
        "function": {
            "name": name,
            "description": spec["description"],
            "parameters": spec["parameters"],
        },
    } for name, spec in tools.items()]
