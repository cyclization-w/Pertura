"""Active work-order projection for LLM and GUI surfaces.

The work order is not durable state. It is a compact, action-oriented view of
the current graph state so the LLM does not need to infer "where am I and what
can I do?" from a large debug envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pertura.models import Snapshot, _model_dump

from .execution_state import compile_runtime_issues


def build_active_work_order(
    snap: Snapshot,
    context_view=None,
    context_envelope: dict[str, Any] | None = None,
    *,
    outcome_text: str = "",
    last_attempt_delta: dict[str, Any] | None = None,
    trace_driven_rethinking: dict[str, Any] | None = None,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return a compact next-action work order for the current run state."""
    if context_view is None:
        source = _snapshot_work_order_source(snap)
        analysis_node = source["analysis_node"]
        progress = source["progress"]
        capabilities = source["capabilities"]
        open_interrupts = source["open_interrupts"]
        open_triggers = source["open_triggers"]
        recent_findings = source["recent_findings"]
        workspace_files = source["workspace_files"]
        active_node_id = source["active_node_id"]
        active_branch = source["active_branch"]
        observation_memory = source["observation_memory"]
    else:
        analysis_node = dict(getattr(context_view, "analysis_node", {}) or {})
        progress = dict(getattr(context_view, "current_node_progress", {}) or {})
        capabilities = _active_capabilities(context_view, analysis_node)
        open_interrupts = list(getattr(context_view, "open_interrupts", []) or [])
        open_triggers = list(getattr(context_view, "open_triggers", []) or [])
        recent_findings = [
            item for item in (getattr(context_view, "recent_findings", []) or [])
            if item.get("severity") in {"warning", "blocking"}
        ][-5:]
        workspace_files = _workspace_files(context_view)
        active_node_id = getattr(context_view, "active_node_id", "") or analysis_node.get("node_id", "")
        active_branch = getattr(context_view, "active_branch", "main")
        observation_memory = _compact_observation_memory(snap)
    audit_preview = (context_envelope or {}).get("audit_preview", {}) or {}
    if not audit_preview:
        audit_preview = _snapshot_issue_preview(snap)
    rethink = trace_driven_rethinking or (context_envelope or {}).get("trace_driven_rethinking", {}) or {}
    runtime_issues = compile_runtime_issues(snap)
    from .node_navigation import evaluate_node_navigation
    navigation = evaluate_node_navigation(snap)
    from .workflow_controller import workflow_gap
    gap = workflow_gap(snap)
    perturbseq_view = _perturbseq_projection(
        snap,
        navigation=navigation,
        outcome_text=outcome_text,
        last_attempt_delta=last_attempt_delta,
    )
    mode = _work_order_mode(open_interrupts, open_triggers, recent_findings, audit_preview, rethink)
    missing = _missing_items(progress, audit_preview)
    recommended = _recommended_actions(
        mode=mode,
        analysis_node=analysis_node,
        capabilities=capabilities,
        missing=missing,
        audit_preview=audit_preview,
        rethink=rethink,
    )
    payload = {
        "view_type": "active_work_order",
        "mode": mode,
        "run_goal": _latest_goal(snap),
        "active_node": {
            "id": active_node_id,
            "title": analysis_node.get("title") or analysis_node.get("node_id", ""),
            "purpose": analysis_node.get("purpose", ""),
            "next_nodes": analysis_node.get("next_nodes", []),
        },
        "branch_id": active_branch,
        "node_progress": {
            "attempts": progress.get("attempts", 0),
            "observations": progress.get("observations", 0),
            "artifacts": progress.get("artifacts", 0),
            "completed": progress.get("completed", False),
            "missing_completion": missing,
        },
        "workspace": {
            "path": snap.workspace,
            "files": workspace_files,
        },
        "available_capabilities": capabilities[:8],
        "selected_capability": _selected_capability(capabilities),
        "observation_memory": observation_memory,
        "navigation": navigation,
        "workflow_gap": gap,
        "perturbseq": perturbseq_view,
        "node_execution_guidance": _node_execution_guidance(
            navigation=navigation,
            progress=progress,
            capabilities=capabilities,
        ),
        "open_interrupts": open_interrupts[:3],
        "open_issues": {
            "runtime_issues": runtime_issues[:8],
            "triggers": open_triggers[-5:],
            "findings": recent_findings,
            "audit_next_actions": (audit_preview.get("next_actions") or [])[:5],
        },
        "rethinking": _compact_rethinking(rethink),
        "last_attempt_delta": last_attempt_delta or {},
        "outcome": outcome_text,
        "allowed_tools": list(tool_names or [])[:12],
        "recommended_actions": recommended,
        "contract": {
            "must_declare_capability": True,
            "state_changes_go_through": "gated_dispatch",
            "raw_state_mutation_allowed": False,
        },
    }
    if perturbseq_view:
        catalog = perturbseq_view.get("capability_catalog") or {}
        product_caps = catalog.get("cards") or []
        selected = catalog.get("selected_capability") or {}
        product_guidance = perturbseq_view.get("node_execution_guidance") or {}
        if product_caps:
            payload["available_capabilities"] = product_caps[:8]
        if selected:
            payload["selected_capability"] = selected
        if product_guidance.get("primary_instruction"):
            payload["node_execution_guidance"] = product_guidance
        payload["recommended_actions"] = _perturbseq_recommended_actions(
            perturbseq_view,
            fallback=payload.get("recommended_actions", []),
        )
    candidate_path = _dataset_candidate_path(payload["workspace"]["files"], snap.workspace)
    dataset_loaded = bool(
        ((perturbseq_view.get("design_ledger") or {}).get("dataset_profile") or {}).get("loaded")
    ) if perturbseq_view else False
    if candidate_path and not dataset_loaded:
        payload["dataset_load_plan"] = {
            "path": candidate_path,
            "instruction": "Call load_dataset(path=...) first, then execute the returned code with capability_ids=['load_dataset'].",
        }
        payload["recommended_actions"] = _prioritize_load_dataset_action(
            payload.get("recommended_actions", []),
            candidate_path,
        )
    if perturbseq_view:
        payload["markdown"] = perturbseq_view.get("turn_card_markdown") or render_active_work_order(payload)
    else:
        payload["markdown"] = render_active_work_order(payload)
    return payload


def render_active_work_order(work_order: dict[str, Any]) -> str:
    """Render a human-readable work order for the LLM prompt."""
    node = work_order.get("active_node", {}) or {}
    progress = work_order.get("node_progress", {}) or {}
    workspace = work_order.get("workspace", {}) or {}
    caps = work_order.get("available_capabilities", []) or []
    issues = work_order.get("open_issues", {}) or {}
    rethink = work_order.get("rethinking", {}) or {}
    lines = [
        "# Active Work Order",
        "",
        f"Mode: {work_order.get('mode', 'normal')}",
        f"Run goal: {_line(work_order.get('run_goal') or 'No goal recorded')}",
        f"Current node: {node.get('id') or 'none'}",
        f"Purpose: {_line(node.get('purpose') or node.get('title') or 'No active analysis node')}",
        f"Branch: {work_order.get('branch_id') or 'main'}",
    ]
    navigation = work_order.get("navigation") or {}
    roadmap = navigation.get("roadmap") or {}
    if roadmap:
        next_nodes = roadmap.get("next_node_ids") or node.get("next_nodes") or []
        lines.extend([
            "",
            "## Analysis Roadmap",
            f"- position: {roadmap.get('current_index', 0)}/{roadmap.get('total_nodes', 0)}",
            f"- next candidates: {', '.join(str(item) for item in next_nodes[:5]) or 'none'}",
            f"- navigation status: {navigation.get('status', 'stay')}",
            f"- navigation reason: {_line(navigation.get('reason') or '')}",
        ])
        for item in (navigation.get("candidates") or [])[:3]:
            marker = "ready" if item.get("can_enter") else item.get("decision", "blocked")
            lines.append(f"- candidate {item.get('node_id')}: {marker} {_line(item.get('reason') or item.get('purpose') or '')}")
    gap = work_order.get("workflow_gap") or {}
    if gap:
        lines.extend([
            "",
            "## Workflow Gap",
            f"- gap type: {gap.get('gap_type', 'none')}",
            f"- next runtime action: {gap.get('next_runtime_action', 'continue')}",
            f"- reason: {_line(gap.get('reason') or '')}",
        ])
        for item in (gap.get("missing") or [])[:5]:
            lines.append(f"- missing: {_line(item)}")
    guidance = work_order.get("node_execution_guidance") or {}
    if guidance:
        lines.extend(["", "## Execution Guidance"])
        if guidance.get("primary_instruction"):
            lines.append(f"- primary: {_line(guidance.get('primary_instruction'))}")
        for item in (guidance.get("avoid_actions") or [])[:5]:
            lines.append(f"- avoid: {_line(item)}")
        for item in (guidance.get("preferred_tools") or [])[:5]:
            lines.append(f"- prefer tool: {_line(item)}")
    lines.extend([
        "",
        "## Node Progress",
        f"- attempts: {progress.get('attempts', 0)}",
        f"- observations: {progress.get('observations', 0)}",
        f"- artifacts: {progress.get('artifacts', 0)}",
        f"- completed: {bool(progress.get('completed', False))}",
        "",
        "## Missing Before Completion",
    ])
    missing = progress.get("missing_completion") or []
    lines.extend([f"- [ ] {_line(item)}" for item in missing] or ["- none reported"])
    lines.extend(["", "## Workspace Summary"])
    files = workspace.get("files") or []
    lines.extend([f"- {_line(item.get('target') or item.get('name') or item)}: {_line(item.get('summary') or item.get('metric') or '')}" for item in files] or ["- no workspace files observed yet"])
    lines.extend(["", "## Available Capabilities In This Node"])
    for cap in caps:
        expected = ", ".join((cap.get("expected_observations") or cap.get("expected_artifacts") or [])[:4])
        detail = f" expected: {expected}" if expected else ""
        lines.append(f"- {cap.get('id') or cap.get('capability_id')}: {_line(cap.get('description') or '')}{detail}")
    if not caps:
        lines.append("- none; request a node transition or ask the user if blocked")
    selected = work_order.get("selected_capability") or {}
    if selected:
        lines.extend(["", "## Selected Capability Card"])
        lines.append(f"- id: {selected.get('id') or selected.get('capability_id')}")
        if selected.get("missing_inputs"):
            lines.append(f"- missing inputs: {', '.join(str(item) for item in selected.get('missing_inputs', [])[:6])}")
        else:
            lines.append("- missing inputs: none")
        expected_obs = selected.get("expected_observations") or []
        expected_art = selected.get("expected_artifacts") or []
        if expected_obs:
            lines.append(f"- expected observations: {', '.join(str(item) for item in expected_obs[:6])}")
        if expected_art:
            lines.append(f"- expected artifacts: {', '.join(str(item) for item in expected_art[:6])}")
        if selected.get("packages_hint"):
            lines.append(f"- packages/functions: {_line(selected.get('packages_hint'))}")
        if selected.get("next_repair"):
            lines.append(f"- next repair: {_line(selected.get('next_repair'))}")
        for item in (selected.get("common_errors") or [])[:3]:
            lines.append(f"- common error: {_line(item)}")
    memory = work_order.get("observation_memory") or {}
    summary = memory.get("summary") or {}
    if summary:
        lines.extend(["", "## Observation Memory"])
        lines.append(f"- variables: {summary.get('variables', summary.get('variable_count', 0))}")
        lines.append(f"- strict conflicts: {summary.get('strict_conflicts', summary.get('conflicts', 0))}")
        lines.append(f"- cross-context divergences: {summary.get('cross_context_divergences', 0)}")
        labels = summary.get("coverage_labels") or {}
        if labels:
            label_text = ", ".join(f"{key}={value}" for key, value in list(labels.items())[:6])
            lines.append(f"- coverage: {label_text}")
        for item in (memory.get("needs_review") or [])[:3]:
            lines.append(f"- review: {_line(item.get('variable_key') or item.get('subject_metric') or item)}")
    delta = work_order.get("last_attempt_delta") or {}
    if delta:
        lines.extend(["", "## Last Attempt Delta"])
        if delta.get("attempt_id"):
            lines.append(f"- attempt: {_line(delta.get('attempt_id'))} ({_line(delta.get('status'))})")
        execution = delta.get("execution") or {}
        if execution:
            returncode = execution.get("returncode")
            timed_out = execution.get("timed_out") or execution.get("soft_timeout_hit")
            lines.append(f"- execution: returncode={returncode}, timed_out={bool(timed_out)}")
        if delta.get("observations_registered") is not None:
            lines.append(f"- observations registered: {delta.get('observations_registered')}")
        for item in (delta.get("new_observations") or [])[:4]:
            lines.append(f"- observation: {_delta_observation_line(item)}")
        for item in (delta.get("new_artifacts") or [])[:3]:
            lines.append(f"- artifact: {_delta_artifact_line(item)}")
        for item in (delta.get("new_findings") or [])[:3]:
            lines.append(f"- finding: {_delta_finding_line(item)}")
        if delta.get("runtime_refs"):
            lines.append(f"- runtime refs: {', '.join(str(item) for item in delta.get('runtime_refs', [])[:6])}")
    load_plan = work_order.get("dataset_load_plan") or {}
    if load_plan.get("path"):
        lines.extend([
            "",
            "## Dataset Loading Plan",
            f"- Call load_dataset(path={load_plan.get('path')!r}) before hand-writing loading code.",
            "- Then call execute_code with the returned code and capability_ids=['load_dataset'].",
            "- Do not use subprocess for data loading or file inspection.",
        ])
    issue_rows = (issues.get("triggers") or []) + (issues.get("findings") or [])
    if issue_rows or rethink.get("status") not in {"", "not_needed", None}:
        lines.extend(["", "## Open Issue / Rethinking"])
        for item in issue_rows[:5]:
            lines.append(f"- {item.get('severity', 'issue')}: {_line(item.get('summary') or item.get('type') or '')}")
        if rethink.get("summary"):
            lines.append(f"- rethink: {_line(rethink.get('summary'))}")
        for action in (rethink.get("recommended_actions") or [])[:4]:
            lines.append(f"- suggested repair: {_line(action.get('tool') or action.get('action') or '')} - {_line(action.get('why') or '')}")
    lines.extend(["", "## Recommended Next Actions"])
    lines.extend([f"- {_line(item)}" for item in (work_order.get("recommended_actions") or [])] or ["- choose one allowed action"])
    lines.extend([
        "",
        "## Commit Contract",
        "- Choose one state-changing action.",
        "- For execute_code or submit_job, declare capability_ids.",
        "- Durable scientific state is written only through gated_dispatch.",
    ])
    return "\n".join(lines)


def _latest_goal(snap: Snapshot) -> str:
    return snap.goals[-1].text if snap.goals else snap.goal


def _active_capabilities(context_view, analysis_node: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = set(analysis_node.get("allowed_capabilities") or [])
    rows = []
    for item in getattr(context_view, "capabilities", []) or []:
        cid = item.get("id") or item.get("capability_id")
        if allowed and cid not in allowed:
            continue
        rows.append({
            "id": cid,
            "title": item.get("title") or cid,
            "description": item.get("description", ""),
            "ready": item.get("ready", item.get("llm_actionable", False)),
            "missing_inputs": item.get("missing_inputs", []),
            "expected_artifacts": item.get("expected_artifacts", []),
            "expected_observations": item.get("expected_observations", []),
            "required_inputs": item.get("required_inputs", []),
            "packages": item.get("packages", []),
            "functions": item.get("functions", []),
            "packages_hint": _packages_hint(item),
            "next_repair": _next_repair(item.get("missing_inputs", []), capability_id=cid),
            "common_errors": _common_errors(item),
        })
    if rows:
        return rows
    return [{"id": cid, "title": cid, "description": ""} for cid in sorted(allowed)]


def _workspace_files(context_view) -> list[dict[str, Any]]:
    rows = []
    for item in (getattr(context_view, "workspace_files", []) or [])[:8]:
        if isinstance(item, dict):
            rows.append({
                "target": item.get("target") or item.get("subject") or item.get("name") or item.get("path") or "",
                "metric": item.get("metric", ""),
                "summary": item.get("summary") or item.get("value") or item.get("metric") or "",
                "type": item.get("type", ""),
                "value": item.get("value", ""),
            })
        else:
            rows.append({"target": str(item), "summary": ""})
    return rows


def _missing_items(progress: dict[str, Any], audit_preview: dict[str, Any]) -> list[str]:
    out = []
    for key in ("missing_completion", "missing_inputs", "blocked_requirements"):
        value = progress.get(key)
        if isinstance(value, list):
            out.extend(str(item.get("condition_id") if isinstance(item, dict) else item) for item in value)
    for action in audit_preview.get("next_actions", []) or []:
        if isinstance(action, dict) and action.get("why"):
            out.append(str(action.get("why")))
    return _dedupe([item for item in out if item])[:8]


def _work_order_mode(open_interrupts, open_triggers, recent_findings, audit_preview, rethink) -> str:
    if open_interrupts:
        return "human_interrupt"
    if rethink and rethink.get("status") not in {"", "not_needed", None}:
        return "rethink"
    if (
        audit_preview.get("errors")
        or audit_preview.get("blocking_findings")
        or audit_preview.get("severity") in {"error", "blocking"}
    ):
        return "audit_repair"
    if open_triggers or any(item.get("severity") == "blocking" for item in recent_findings):
        return "issue_repair"
    return "normal"


def _recommended_actions(
    *,
    mode: str,
    analysis_node: dict[str, Any],
    capabilities: list[dict[str, Any]],
    missing: list[str],
    audit_preview: dict[str, Any],
    rethink: dict[str, Any],
) -> list[str]:
    if mode == "human_interrupt":
        return ["answer or refine the open HumanInterrupt before running new analysis"]
    if mode in {"rethink", "audit_repair", "issue_repair"}:
        actions = []
        for item in (rethink.get("recommended_actions") or [])[:3]:
            label = item.get("tool") or item.get("action") or ""
            why = item.get("why") or ""
            actions.append(f"{label}: {why}".strip(": "))
        for item in (audit_preview.get("next_actions") or [])[:3]:
            if isinstance(item, dict):
                actions.append(f"{item.get('tool', 'repair')}: {item.get('why', '')}".strip(": "))
        return _dedupe(actions) or ["repair the open issue before continuing"]
    ready = [cap for cap in capabilities if cap.get("ready") or not cap.get("missing_inputs")]
    if ready:
        if ready[0].get("id") == "load_dataset":
            return ["call load_dataset(path=<detected data path>) before writing custom loading code"]
        return [f"execute_code with capability_ids=['{ready[0].get('id')}']"]
    if missing:
        return [f"resolve missing item: {missing[0]}"]
    rec = analysis_node.get("recommended_actions") or []
    return list(rec[:3]) or ["inspect current node contract, then choose one allowed capability"]


def _compact_rethinking(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {
        "status": payload.get("status", ""),
        "summary": payload.get("summary", ""),
        "suspected_roots": (payload.get("suspected_roots") or [])[:5],
        "recommended_actions": (payload.get("recommended_actions") or [])[:5],
    }


def _perturbseq_projection(
    snap: Snapshot,
    *,
    navigation: dict[str, Any],
    outcome_text: str = "",
    last_attempt_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if getattr(snap, "domain", "") != "perturbseq":
        return {}
    try:
        from pertura.product.perturbseq import compile_perturbseq_view

        return compile_perturbseq_view(
            snap,
            navigation=navigation,
            outcome_text=outcome_text,
            last_attempt_delta=last_attempt_delta,
        )
    except Exception as exc:
        return {
            "view_type": "perturbseq_workbench",
            "error": str(exc),
        }


def _perturbseq_recommended_actions(perturbseq_view: dict[str, Any], *, fallback: list[str]) -> list[str]:
    navigation = perturbseq_view.get("navigation") or {}
    if navigation.get("status") == "advance":
        target = navigation.get("target_node_id") or "next node"
        return [f"complete current stage, then request_node_transition(target_node_id={target!r})"]
    if navigation.get("status") == "complete":
        return ["complete_node or finish/report; do not run another inspection cell"]
    questions = perturbseq_view.get("suggested_questions") or []
    if questions:
        first = questions[0]
        return [f"ask_user to confirm {first.get('field_id')}: {first.get('question')}"]
    ready = perturbseq_view.get("ready_capabilities") or []
    if ready:
        cap_id = ready[0].get("id")
        return [f"execute_code with capability_ids=['{cap_id}']"]
    blocked = perturbseq_view.get("blocked_capabilities") or []
    if blocked:
        missing = blocked[0].get("missing") or []
        if missing:
            return [f"resolve missing input: {missing[0]}"]
    return list(fallback or [])[:5]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _dataset_candidate_path(workspace_files: list[dict[str, Any]], workspace: str) -> str:
    candidates = []
    for item in workspace_files:
        target = str(item.get("target") or "").rstrip("/")
        metric = str(item.get("metric") or "")
        value = str(item.get("value") or item.get("summary") or "")
        if metric == "detected_format" and value in {"h5ad", "hdf5_or_10x_h5", "10x_mtx_directory"}:
            candidates.append(target)
        elif target.lower().endswith((".h5ad", ".h5", ".hdf5")):
            candidates.append(target)
    if not candidates:
        return ""
    first = candidates[0]
    path = Path(first)
    if path.is_absolute():
        return str(path)
    return str(Path(workspace) / first) if workspace else first


def _prioritize_load_dataset_action(actions: list[str], path: str) -> list[str]:
    first = f"call load_dataset(path={path!r}), then execute returned code with capability_ids=['load_dataset']"
    return _dedupe([first] + list(actions))[:5]


def _line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _delta_observation_line(item: dict[str, Any]) -> str:
    observation_id = item.get("observation_id") or ""
    target = item.get("target") or ""
    metric = item.get("metric") or item.get("type") or ""
    value = item.get("value")
    method = item.get("method") or ""
    return _line(f"{observation_id} {target} {metric}={value} {method}".strip())


def _delta_artifact_line(item: dict[str, Any]) -> str:
    kind = item.get("kind") or "artifact"
    path = item.get("path") or item.get("artifact_id") or ""
    summary = item.get("summary") or ""
    return _line(f"{kind} {path} {summary}".strip())


def _delta_finding_line(item: dict[str, Any]) -> str:
    severity = item.get("severity") or "finding"
    kind = item.get("type") or item.get("finding_type") or ""
    summary = item.get("summary") or item.get("action") or ""
    return _line(f"{severity} {kind}: {summary}".strip(": "))


def _snapshot_work_order_source(snap: Snapshot) -> dict[str, Any]:
    analysis_node = _analysis_node_from_snapshot(snap)
    progress = _node_progress_from_snapshot(snap, analysis_node)
    capabilities = _active_capabilities_from_snapshot(snap, analysis_node)
    recent_findings = [
        {
            "finding_id": item.finding_id,
            "type": item.finding_type,
            "severity": item.severity,
            "summary": item.summary,
            "action": item.suggested_action,
            "affected_ids": list(item.affected_ids or []),
        }
        for item in getattr(snap, "findings", [])[-12:]
        if item.severity in {"warning", "blocking", "high", "error"}
    ][-5:]
    return {
        "analysis_node": analysis_node,
        "progress": progress,
        "capabilities": capabilities,
        "open_interrupts": [
            {
                "interrupt_id": item.interrupt_id,
                "source": item.source,
                "question": item.question,
                "options": list(item.options or []),
            }
            for item in getattr(snap, "interrupts", []) or []
            if item.status == "open"
        ][-5:],
        "open_triggers": [
            {
                "trigger_id": item.trigger_id,
                "type": item.trigger_type,
                "severity": item.severity,
                "summary": item.summary,
            }
            for item in getattr(snap, "triggers", []) or []
            if item.status == "open"
        ][-5:],
        "recent_findings": recent_findings,
        "workspace_files": _workspace_files_from_snapshot(snap),
        "active_node_id": getattr(snap, "active_node_id", "") or analysis_node.get("node_id", ""),
        "active_branch": getattr(snap, "active_branch", "main") or "main",
        "observation_memory": _compact_observation_memory(snap),
    }


def _analysis_node_from_snapshot(snap: Snapshot) -> dict[str, Any]:
    from pertura.spec.gating import GateEvaluator

    evaluator = GateEvaluator(getattr(snap, "analysis_spec", {}) or {})
    spec = evaluator.spec
    if spec is None:
        return {}
    current = spec.node(snap.active_node_id) if snap.active_node_id else spec.node(spec.start_node_id)
    if current is None:
        return {}
    return {
        "node_id": current.node_id,
        "title": current.title,
        "purpose": current.purpose,
        "allowed_capabilities": list(current.allowed_capabilities or []),
        "recommended_actions": list(current.recommended_actions or []),
        "expected_outputs": list(current.expected_outputs or []),
        "next_nodes": list(current.next_nodes or []),
    }


def _node_progress_from_snapshot(snap: Snapshot, analysis_node: dict[str, Any]) -> dict[str, Any]:
    node_id = analysis_node.get("node_id", "")
    if not node_id:
        return {}
    from pertura.spec.gating import GateEvaluator

    evaluator = GateEvaluator(getattr(snap, "analysis_spec", {}) or {})
    spec = evaluator.spec
    current = spec.node(node_id) if spec else None
    requires = [_condition_status(cond, snap) for cond in (current.requires if current else [])]
    must_confirm = [_condition_status(cond, snap) for cond in (current.must_confirm if current else [])]
    completion = [_condition_status(cond, snap) for cond in (current.completion if current else [])]
    node_attempts = [
        attempt for attempt in getattr(snap, "attempts", []) or []
        if attempt.analysis_node_id == node_id and attempt.branch_id == snap.active_branch
    ]
    attempt_ids = {attempt.attempt_id for attempt in node_attempts}
    node_observations = [obs for obs in getattr(snap, "observations", []) or [] if obs.attempt_id in attempt_ids]
    node_artifacts = [art for art in getattr(snap, "artifacts", []) or [] if art.attempt_id in attempt_ids]
    missing_completion = [item for item in completion if not item["passed"] and item.get("hard", True)]
    visit = next(
        (
            item for item in reversed(getattr(snap, "node_visits", []) or [])
            if item.node_id == node_id and item.branch_id == snap.active_branch
        ),
        None,
    )
    return {
        "node_id": node_id,
        "attempts": len(node_attempts),
        "completed_attempts": len([item for item in node_attempts if item.status in {"succeeded", "failed", "stopped"}]),
        "observations": len(node_observations),
        "artifacts": len(node_artifacts),
        "completed": bool(visit and visit.status == "completed"),
        "requires": requires,
        "must_confirm": must_confirm,
        "completion": completion,
        "completion_passed": len([item for item in completion if item["passed"]]),
        "completion_total": len(completion),
        "missing_completion": missing_completion,
        "recommended_actions": analysis_node.get("recommended_actions", []),
        "expected_outputs": analysis_node.get("expected_outputs", []),
    }


def _condition_status(cond, snap: Snapshot) -> dict[str, Any]:
    from pertura.spec.conditions import evaluate_condition

    result = evaluate_condition(cond, snap)
    return {
        "condition_id": result.condition_id,
        "passed": result.passed,
        "tier": result.tier,
        "failure_mode": result.failure_mode,
        "description": getattr(cond, "description", ""),
        "message": result.message,
        "hard": result.hard,
        "details": result.details or {},
    }


def _active_capabilities_from_snapshot(snap: Snapshot, analysis_node: dict[str, Any]) -> list[dict[str, Any]]:
    from pertura.capabilities import CapabilityRegistry

    allowed = list(analysis_node.get("allowed_capabilities") or [])
    registry = CapabilityRegistry(getattr(snap, "capabilities", []) or [])
    ids = allowed or registry.ids()
    rows = []
    for cap_id in ids:
        cap = registry.get(cap_id)
        if cap is None:
            rows.append({"id": cap_id, "title": cap_id, "description": "", "missing": True})
            continue
        raw = cap.model_dump(mode="json")
        missing = [
            item for item in list(raw.get("required_inputs", []) or [])
            if not _input_available(snap, str(item))
        ]
        rows.append({
            "id": raw.get("capability_id", cap_id),
            "title": raw.get("title") or cap_id,
            "description": raw.get("description", ""),
            "ready": not missing,
            "missing_inputs": missing,
            "required_inputs": list(raw.get("required_inputs", []) or []),
            "expected_artifacts": list(raw.get("expected_artifacts", []) or []),
            "expected_observations": list(raw.get("expected_observations", []) or []),
            "packages": list(raw.get("packages", []) or []),
            "functions": list(raw.get("functions", []) or []),
            "analysis_modes": list(raw.get("analysis_modes", []) or []),
            "packages_hint": _packages_hint(raw),
            "next_repair": _next_repair(missing, capability_id=cap_id),
            "common_errors": _common_errors(raw),
        })
    return rows


def _workspace_files_from_snapshot(snap: Snapshot, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = []
    for obs in getattr(snap, "observations", []) or []:
        if obs.type not in {"workspace_file", "workspace_probe"}:
            continue
        summary = ""
        if isinstance(obs.parameters, dict):
            summary = str(obs.parameters.get("summary", ""))
        rows.append({
            "target": obs.target,
            "metric": obs.metric,
            "summary": summary or str(obs.value if obs.value is not None else obs.metric),
            "type": obs.type,
            "value": obs.value,
        })
    return rows[-limit:]


def _compact_observation_memory(snap: Snapshot) -> dict[str, Any]:
    try:
        from pertura.core.observation_memory import build_observation_memory_view

        view = build_observation_memory_view(snap, limit=6)
    except Exception:
        return {}
    return {
        "summary": view.get("summary", {}),
        "needs_review": (view.get("needs_review") or [])[:4],
        "coverage": (view.get("coverage") or [])[:6],
        "truncated": view.get("truncated", False),
    }


def _snapshot_issue_preview(snap: Snapshot) -> dict[str, Any]:
    issues = compile_runtime_issues(snap, limit=8)
    blocking = [
        item for item in issues
        if item.get("severity") in {"blocking", "error", "high"}
    ]
    return {
        "ok": not blocking,
        "severity": "error" if blocking else "ok",
        "summary": {"issues": len(issues), "blocking": len(blocking)},
        "top_issue_codes": [item.get("source", "") for item in issues[:5] if item.get("source")],
        "top_issues": issues[:5],
        "next_actions": [
            {
                "tool": item.get("suggested_action", "repair"),
                "why": item.get("summary", ""),
                "target_id": (item.get("affected_ids") or [""])[0],
            }
            for item in issues[:5]
        ],
    }


def _selected_capability(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    if not capabilities:
        return {}
    for cap in capabilities:
        if cap.get("ready") and not cap.get("missing"):
            return cap
    return capabilities[0]


def _node_execution_guidance(
    *,
    navigation: dict[str, Any],
    progress: dict[str, Any],
    capabilities: list[dict[str, Any]],
) -> dict[str, Any]:
    status = str((navigation or {}).get("status") or "")
    observations = int(progress.get("observations") or 0)
    artifacts = int(progress.get("artifacts") or 0)
    attempts = int(progress.get("attempts") or 0)
    completed = bool(progress.get("completed"))
    material_output = observations > 0 or artifacts > 0
    selected_id = str((capabilities[0] if capabilities else {}).get("id") or "")
    guidance: dict[str, Any] = {
        "material_output": material_output,
        "preferred_tools": [],
        "avoid_actions": [],
    }
    if status == "advance":
        target = navigation.get("target_node_id") or "the ready next node"
        guidance["primary_instruction"] = (
            f"Current node has passed completion. Do not run another inspection cell; call "
            f"complete_node if needed, then request_node_transition to {target}."
        )
        guidance["preferred_tools"] = ["complete_node", "request_node_transition"]
        guidance["avoid_actions"] = ["Do not repeat inspect/load code for this completed node."]
    elif status == "complete":
        guidance["primary_instruction"] = (
            "Current node has passed completion and has no configured next node. "
            "Complete the node or finish/report instead of running more code."
        )
        guidance["preferred_tools"] = ["complete_node", "finish"]
        guidance["avoid_actions"] = ["Do not execute another cell unless the user asks for extra evidence."]
    elif completed:
        guidance["primary_instruction"] = (
            "This node visit is already completed. Request a transition, finish, or ask the user."
        )
        guidance["preferred_tools"] = ["request_node_transition", "finish", "ask_user"]
        guidance["avoid_actions"] = ["Do not execute code in a completed node."]
    elif material_output and attempts:
        guidance["primary_instruction"] = (
            "This node already has registered output. Inspect missing completion gates first; "
            "complete or transition if the gates are satisfied."
        )
        guidance["preferred_tools"] = ["evaluate_node_conditions", "complete_node", "request_node_transition"]
        guidance["avoid_actions"] = [
            "Do not repeat the same workspace/data inspection unless a specific missing gate requires it."
        ]
    elif selected_id:
        guidance["primary_instruction"] = (
            f"Use the selected capability `{selected_id}` once to create registered evidence, "
            "then reassess completion before rerunning."
        )
        guidance["preferred_tools"] = ["get_capability_template", "load_dataset", "execute_code"]
    else:
        guidance["primary_instruction"] = "Choose one meaningful scientific action, then reassess."
        guidance["preferred_tools"] = ["execute_code", "ask_user"]
    return guidance


def _input_available(snap: Snapshot, item: str) -> bool:
    value = _input_value(snap, item)
    return value not in (None, "", [], {})


def _input_value(snap: Snapshot, item: str):
    design = getattr(snap, "design", {}) or {}
    aliases = {
        "control_labels": ["control_labels", "controls"],
        "guide_column": ["guide_column", "guide", "grna_column", "sgrna_column"],
        "target_column": ["target_column", "target", "perturbation_column"],
        "state_labels": ["state_labels", "state_column"],
    }
    for key in aliases.get(item, [item]):
        if design.get(key) not in (None, "", [], {}):
            return design.get(key)
    if item == "adata":
        return "adata" if "adata" in _runtime_symbol_names(snap) else None
    if item in {"workspace_files", "workspace"}:
        return _workspace_files_from_snapshot(snap, limit=1)
    if item in {"supported_observations", "observation_memory"}:
        return getattr(snap, "observations", []) or None
    if item == "conclusions":
        return getattr(snap, "conclusions", []) or None
    if item == "artifacts":
        return getattr(snap, "artifacts", []) or None
    if item == "branches":
        return getattr(snap, "branches", []) or None
    if item in {"effect_result", "effect_table"}:
        return [
            art for art in getattr(snap, "artifacts", []) or []
            if art.kind in {"table", "csv", "tsv", "parquet", "de_result"} or "de" in str(art.summary).lower()
        ] or None
    if item == "state_reference":
        return [
            art for art in getattr(snap, "artifacts", []) or []
            if art.kind in {"embedding", "cluster_table", "annotation_table", "module_table", "checkpoint"}
        ] or None
    if item == "node_id":
        return getattr(snap, "active_node_id", "")
    return design.get(item)


def _runtime_symbol_names(snap: Snapshot) -> set[str]:
    names: set[str] = set()
    for outcome in getattr(snap, "outcomes", []) or []:
        metrics = getattr(outcome, "metrics", {}) or {}
        if not isinstance(metrics, dict):
            continue
        for key in ("kernel_state", "runtime_state"):
            state = metrics.get(key, {})
            if not isinstance(state, dict):
                continue
            variables = state.get("variables", {})
            if isinstance(variables, dict):
                names.update(str(name) for name in variables)
    return names


def _next_repair(missing_inputs: list[Any], *, capability_id: str = "") -> str:
    missing = [str(item) for item in missing_inputs if item]
    if not missing:
        return ""
    design_fields = [
        item for item in missing
        if item in {"control_labels", "guide_column", "target_column", "state_labels", "perturbation_modality", "moi"}
    ]
    if design_fields:
        return "confirm design: " + ", ".join(design_fields)
    if "adata" in missing or "workspace_files" in missing:
        return "load or restore dataset before executing " + capability_id
    if "state_reference" in missing:
        return "build state reference before executing " + capability_id
    if "effect_result" in missing or "effect_table" in missing:
        return "run or locate differential-effect results before executing " + capability_id
    return "resolve missing inputs: " + ", ".join(missing[:6])


def _packages_hint(capability: dict[str, Any]) -> str:
    values = []
    values.extend(str(item) for item in capability.get("packages", []) or [])
    values.extend(str(item) for item in capability.get("functions", []) or [])
    return ", ".join(_dedupe(values)[:8])


def _common_errors(capability: dict[str, Any]) -> list[str]:
    contract = capability.get("contract", {}) or {}
    errors = contract.get("common_errors") or contract.get("common_failures") or []
    if isinstance(errors, str):
        return [errors]
    return [str(item) for item in list(errors)[:5]]
