"""Active work-order projection for LLM and GUI surfaces.

The work order is not durable state. It is a compact, action-oriented view of
the current graph state so the LLM does not need to infer "where am I and what
can I do?" from a large debug envelope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pertura.models import Snapshot, _model_dump


def build_active_work_order(
    snap: Snapshot,
    context_view,
    context_envelope: dict[str, Any] | None = None,
    *,
    outcome_text: str = "",
    last_attempt_delta: dict[str, Any] | None = None,
    trace_driven_rethinking: dict[str, Any] | None = None,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return a compact next-action work order for the current run state."""
    analysis_node = dict(getattr(context_view, "analysis_node", {}) or {})
    progress = dict(getattr(context_view, "current_node_progress", {}) or {})
    capabilities = _active_capabilities(context_view, analysis_node)
    open_interrupts = list(getattr(context_view, "open_interrupts", []) or [])
    open_triggers = list(getattr(context_view, "open_triggers", []) or [])
    recent_findings = [
        item for item in (getattr(context_view, "recent_findings", []) or [])
        if item.get("severity") in {"warning", "blocking"}
    ][-5:]
    audit_preview = (context_envelope or {}).get("audit_preview", {}) or {}
    rethink = trace_driven_rethinking or (context_envelope or {}).get("trace_driven_rethinking", {}) or {}
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
            "id": getattr(context_view, "active_node_id", "") or analysis_node.get("node_id", ""),
            "title": analysis_node.get("title") or analysis_node.get("node_id", ""),
            "purpose": analysis_node.get("purpose", ""),
        },
        "branch_id": getattr(context_view, "active_branch", "main"),
        "node_progress": {
            "attempts": progress.get("attempts", 0),
            "observations": progress.get("observations", 0),
            "artifacts": progress.get("artifacts", 0),
            "completed": progress.get("completed", False),
            "missing_completion": missing,
        },
        "workspace": {
            "path": snap.workspace,
            "files": _workspace_files(context_view),
        },
        "available_capabilities": capabilities[:8],
        "open_interrupts": open_interrupts[:3],
        "open_issues": {
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
    candidate_path = _dataset_candidate_path(payload["workspace"]["files"], snap.workspace)
    if candidate_path:
        payload["dataset_load_plan"] = {
            "path": candidate_path,
            "instruction": "Call load_dataset(path=...) first, then execute the returned code with capability_ids=['load_dataset'].",
        }
        payload["recommended_actions"] = _prioritize_load_dataset_action(
            payload.get("recommended_actions", []),
            candidate_path,
        )
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
        "",
        "## Node Progress",
        f"- attempts: {progress.get('attempts', 0)}",
        f"- observations: {progress.get('observations', 0)}",
        f"- artifacts: {progress.get('artifacts', 0)}",
        f"- completed: {bool(progress.get('completed', False))}",
        "",
        "## Missing Before Completion",
    ]
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
    if (audit_preview.get("errors") or audit_preview.get("blocking_findings")):
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
