"""Bounded graph/snapshot views for LLM, GUI, and API consumers.

Views are the read boundary of the runtime: agents should consume scoped,
typed projections instead of full event logs, full graphs, or full notebooks.
"""

from __future__ import annotations

import json
from typing import Any

from pertura.models import Snapshot, IntentEntry, _model_dump
from pertura.core.graph import impact_of_change, trace_upstream
from pertura.core.observation_memory import (
    build_coverage_entries,
    build_memory_entries,
    build_observation_memory_view,
)
from pertura.core.relations import relation_summary
from pertura.spec.conditions import evaluate_condition
from pertura.spec.gating import GateEvaluator
from pertura.capabilities import CapabilityRegistry


VIEW_PURPOSES = {"deliberation", "codegen", "critic", "audit"}


def build_context_view(
    snap: Snapshot,
    graph: dict | None = None,
    *,
    purpose: str = "planner",
    max_items: int = 12,
) -> dict[str, Any]:
    """Build the compact view used as LLM working context."""
    open_approvals = [
        {
            "approval_id": a.approval_id,
            "subject_id": a.subject_id,
            "approval_type": a.approval_type,
            "reason": a.reason,
        }
        for a in snap.approvals
        if a.status == "open"
    ][-max_items:]
    open_interrupts = [
        {
            "interrupt_id": i.interrupt_id,
            "source": i.source,
            "question": i.question,
            "options": i.options,
        }
        for i in snap.interrupts
        if i.status == "open"
    ][-max_items:]
    node_view = _analysis_node_view(snap)
    return {
        "view_type": "context",
        "purpose": purpose,
        "run_id": snap.run_id,
        "phase": snap.phase,
        "goal": snap.goal,
        "active_branch": snap.active_branch,
        "active_node_id": snap.active_node_id,
        "analysis_node": node_view["current"],
        "reachable_nodes": node_view["reachable"],
        "blocked_transitions": node_view["blocked"],
        "gate_requirements": node_view["requirements"],
        "current_node_progress": node_view["progress"],
        "design": snap.design,
        "design_meta": snap.design_meta,
        "protocol": snap.protocol,
        "workspace_files": _workspace_file_view(snap, max_items=max_items),
        "active_stage": _active_stage(snap),
        "attempts_done": len([a for a in snap.attempts if a.status != "planned"]),
        "budget_remaining": {
            "attempts": max(0, snap.budget.max_attempts - len(snap.attempts)),
            "branches": max(0, snap.budget.max_branches - len([b for b in snap.branches if b.status == "active"])),
        },
        "open_approvals": open_approvals,
        "open_interrupts": open_interrupts,
        "open_triggers": [
            {"trigger_id": t.trigger_id, "type": t.trigger_type, "severity": t.severity, "summary": t.summary}
            for t in snap.triggers if t.status == "open"
        ][-max_items:],
        "capabilities": _capability_view(snap, node_view, max_items=max_items),
        "recent_attempts": build_attempt_view(snap, max_items=max_items)["attempts"],
        "recent_artifacts": build_artifact_view(snap, limit=max_items)["artifacts"],
        "memory": [_model_dump(item) for item in build_memory_entries(snap)[:max_items]],
        "coverage": [_model_dump(item) for item in build_coverage_entries(snap)[:max_items]],
        "observation_memory": build_observation_memory_view(snap, limit=max_items),
        "intent": [_model_dump(item) for item in build_intent_entries(snap)[:max_items]],
        "recent_findings": [
            {
                "finding_id": f.finding_id,
                "type": f.finding_type,
                "severity": f.severity,
                "summary": f.summary,
                "action": f.suggested_action,
                "affected_ids": f.affected_ids,
            }
            for f in snap.findings[-max_items:]
        ],
        "graph_summary": _graph_summary(graph),
        "truncated": (
            len(snap.observations) > max_items
            or len(snap.attempts) > max_items
            or len(snap.triggers) > max_items
            or len(snap.artifacts) > max_items
        ),
    }


def build_attempt_view(snap: Snapshot, *, branch_id: str = "", max_items: int = 12) -> dict:
    attempts = [
        {
            "attempt_id": a.attempt_id,
            "branch_id": a.branch_id,
            "stage": a.stage,
            "status": a.status,
            "title": a.title,
            "objective": a.objective,
            "parameters": a.parameters,
            "parent_ids": a.parent_ids,
            "repair_count": a.repair_count,
        }
        for a in snap.attempts
        if not branch_id or a.branch_id == branch_id
    ]
    return {"view_type": "attempts", "count": len(attempts), "attempts": attempts[-max_items:]}


def build_observation_view(
    snap: Snapshot,
    *,
    target: str = "",
    metric: str = "",
    contrast: str = "",
    method: str = "",
    branch_id: str = "",
    limit: int = 10,
) -> dict:
    observations = []
    for obs in snap.observations:
        if target and obs.target.lower() != target.lower():
            continue
        if metric and obs.metric.lower() != metric.lower():
            continue
        if contrast and obs.contrast.lower() != contrast.lower():
            continue
        if method and obs.method.lower() != method.lower():
            continue
        if branch_id and obs.branch_id != branch_id:
            continue
        observations.append({
            "observation_id": obs.observation_id,
            "target": obs.target,
            "metric": obs.metric,
            "value": obs.value,
            "contrast": obs.contrast,
            "method": obs.method,
            "variable_key": obs.variable_key,
            "parameters": obs.parameters,
            "input_ids": obs.input_ids,
            "artifact_id": obs.artifact_id,
            "attempt_id": obs.attempt_id,
            "branch_id": obs.branch_id,
            "parameter_hash": obs.parameter_hash,
            "method_version": obs.method_version,
        })
    observations.sort(key=lambda item: (item["target"], item["metric"], item["attempt_id"]))
    return {
        "view_type": "observations",
        "query": {
            "target": target,
            "metric": metric,
            "contrast": contrast,
            "method": method,
            "branch_id": branch_id,
        },
        "count": len(observations),
        "observations": observations[-limit:],
        "memory": build_observation_memory_view(
            snap,
            target=target,
            metric=metric,
            contrast=contrast,
            method=method,
            branch_id=branch_id,
            limit=limit,
        ),
    }


def build_artifact_view(snap: Snapshot, *, artifact_id: str = "", limit: int = 12) -> dict:
    artifacts = [
        {
            "artifact_id": a.artifact_id,
            "attempt_id": a.attempt_id,
            "kind": a.kind,
            "path": a.path,
            "summary": a.summary,
            "metadata": a.metadata,
        }
        for a in snap.artifacts
        if not artifact_id or a.artifact_id == artifact_id
    ]
    return {"view_type": "artifacts", "count": len(artifacts), "artifacts": artifacts[-limit:]}


def build_branch_view(snap: Snapshot, *, branch_id: str = "", limit: int = 12) -> dict:
    branches = [
        {
            "branch_id": b.branch_id,
            "title": b.title,
            "parent_id": b.parent_id,
            "reason": b.reason,
            "question": b.question,
            "hypothesis": b.hypothesis,
            "status": b.status,
            "summary": b.summary,
            "conclusion": b.conclusion,
            "evidence_ids": b.evidence_ids,
        }
        for b in snap.branches
        if not branch_id or b.branch_id == branch_id
    ]
    return {"view_type": "branches", "count": len(branches), "branches": branches[-limit:]}


def build_trace_view(graph: dict, node_id: str, *, depth: int = 4, limit: int = 30) -> dict:
    return _bounded_walk_view(trace_upstream(graph, node_id, depth=depth), "trace", limit)


def build_impact_view(graph: dict, node_id: str, *, depth: int = 4, limit: int = 30) -> dict:
    return _bounded_walk_view(impact_of_change(graph, node_id, depth=depth), "impact", limit)


def build_intent_entries(snap: Snapshot) -> list[IntentEntry]:
    entries = []
    active_goal = snap.goals[-1].text if snap.goals else snap.goal
    for branch in snap.branches:
        attempts = [a for a in snap.attempts if a.branch_id == branch.branch_id]
        if branch.reason == "main":
            intent, drift = "serve_goal", "low"
        elif branch.reason in ("parameter_sensitivity", "tool_alternative"):
            intent, drift = "explore", "medium"
        elif branch.reason == "biological_hypothesis":
            intent, drift = "explore", "high" if active_goal and "story" not in active_goal else "medium"
        elif branch.reason == "negative_pivot":
            intent, drift = "pivot", "high"
        else:
            intent, drift = "unknown", "medium"
        repair_attempts = [a for a in attempts if a.repair_count > 0 or a.parent_intervention]
        if repair_attempts:
            intent = "repair" if len(repair_attempts) > len(attempts) // 2 else intent
        summary = f"{branch.title or branch.branch_id}: {len(attempts)} attempts"
        if branch.reason != "main":
            summary += f" (reason: {branch.reason})"
        entries.append(IntentEntry(branch_id=branch.branch_id, intent=intent, drift=drift, summary=summary))
    return entries


def _workspace_file_view(snap: Snapshot, *, max_items: int) -> list[dict]:
    return [
        {"subject": o.target, "metric": o.metric, "value": o.value}
        for o in snap.observations
        if o.type == "workspace_file"
    ][-max_items:]


def _active_stage(snap: Snapshot) -> str:
    for attempt in reversed(snap.attempts):
        if attempt.stage:
            return attempt.stage
    return "start"


def _graph_summary(graph: dict | None) -> dict:
    if not graph:
        return {"nodes": 0, "edges": 0}
    by_type: dict[str, int] = {}
    for node in graph.get("nodes", []):
        by_type[node.get("node_type", "unknown")] = by_type.get(node.get("node_type", "unknown"), 0) + 1
    return {
        "nodes": len(graph.get("nodes", [])),
        "edges": len(graph.get("edges", [])),
        "nodes_by_type": by_type,
        "relations": relation_summary(graph.get("edges", [])),
    }


def _analysis_node_view(snap: Snapshot) -> dict:
    evaluator = GateEvaluator(snap.analysis_spec)
    spec = evaluator.spec
    if spec is None:
        return {"current": {}, "reachable": [], "blocked": [], "requirements": [], "progress": {}}
    current = spec.node(snap.active_node_id) if snap.active_node_id else spec.node(spec.start_node_id)
    reachable = [
        {
            "node_id": node.node_id,
            "title": node.title,
            "purpose": node.purpose,
            "allowed_capabilities": node.allowed_capabilities,
        }
        for node in evaluator.reachable_nodes(snap.active_node_id)
    ][:12]
    blocked = [
        {
            "evaluation_id": gate.evaluation_id,
            "target_node_id": gate.target_node_id,
            "decision": gate.decision,
            "reason": gate.reason,
            "messages": gate.messages,
        }
        for gate in snap.gate_evaluations
        if gate.decision in {"human_interrupt", "autonomous_recovery", "block"}
    ][-8:]
    requirements = []
    progress = {}
    if current:
        requires = [_condition_status(cond, snap) for cond in current.requires]
        must_confirm = [_condition_status(cond, snap) for cond in current.must_confirm]
        completion = [_condition_status(cond, snap) for cond in current.completion]
        requirements = [*requires, *must_confirm, *completion][:20]
        node_attempts = [
            attempt for attempt in snap.attempts
            if attempt.analysis_node_id == current.node_id
            and attempt.branch_id == snap.active_branch
        ]
        node_attempt_ids = {attempt.attempt_id for attempt in node_attempts}
        node_observations = [
            obs for obs in snap.observations
            if obs.attempt_id in node_attempt_ids
        ]
        node_artifacts = [
            artifact for artifact in snap.artifacts
            if artifact.attempt_id in node_attempt_ids
        ]
        node_findings = [
            finding for finding in snap.findings
            if finding.attempt_id in node_attempt_ids
            and finding.severity in {"warning", "blocking"}
        ]
        missing_completion = [
            item for item in completion
            if not item["passed"] and item.get("hard", True)
        ]
        progress = {
            "node_id": current.node_id,
            "attempts": len(node_attempts),
            "completed_attempts": len([a for a in node_attempts if a.status in {"succeeded", "failed", "stopped"}]),
            "observations": len(node_observations),
            "artifacts": len(node_artifacts),
            "open_findings": [
                {
                    "finding_id": finding.finding_id,
                    "type": finding.finding_type,
                    "severity": finding.severity,
                    "summary": finding.summary,
                    "action": finding.suggested_action,
                }
                for finding in node_findings[-8:]
            ],
            "requires": requires,
            "must_confirm": must_confirm,
            "completion": completion,
            "completion_passed": len([item for item in completion if item["passed"]]),
            "completion_total": len(completion),
            "missing_completion": missing_completion,
            "recommended_actions": current.recommended_actions,
            "expected_outputs": current.expected_outputs,
        }
    return {
        "current": {
            "node_id": current.node_id if current else "",
            "title": current.title if current else "",
            "purpose": current.purpose if current else "",
            "allowed_capabilities": current.allowed_capabilities if current else [],
            "recommended_actions": current.recommended_actions if current else [],
            "expected_outputs": current.expected_outputs if current else [],
        } if current else {},
        "reachable": reachable,
        "blocked": blocked,
        "requirements": requirements,
        "progress": progress,
    }


def build_view(
    snap: Snapshot,
    graph: dict | None = None,
    *,
    purpose: str = "deliberation",
    focus_ids: list[str] | None = None,
    token_budget: int = 6000,
    runtime_state: dict[str, Any] | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Build an action-local context envelope for LLM calls.

    This is the top-level view composer. The full event graph remains in the
    store; the LLM sees a purpose-scoped working set plus affordances for
    expansion. `build_context_view` stays as the legacy broad planner view.
    """
    purpose = purpose if purpose in VIEW_PURPOSES else "deliberation"
    focus_ids = list(focus_ids or [])
    item_budget = max_items if max_items is not None else _items_for_budget(token_budget)
    base = build_context_view(
        snap, graph, purpose=f"view:{purpose}", max_items=item_budget
    )
    effective_runtime_state = _runtime_state_from_snapshot(snap, runtime_state or {})
    runtime_symbols, symbol_truncation = _runtime_symbols(
        snap,
        runtime_state=effective_runtime_state,
        focus_ids=focus_ids,
        limit=_asset_limit_for_purpose(purpose, item_budget),
    )
    provenance_index = _provenance_index(
        snap,
        graph,
        purpose=purpose,
        focus_ids=focus_ids,
        limit=_provenance_limit_for_purpose(purpose, item_budget),
    )
    audit_preview = _audit_preview(snap, graph, purpose, item_budget)
    sections = {
        "protected_context": _protected_context(snap, base),
        "runtime_symbols": runtime_symbols,
        "working_set": _working_set(snap, base, purpose, runtime_symbols, focus_ids),
        "analysis_state": _analysis_state_for_purpose(base, purpose),
        "provenance_index": provenance_index,
        "active_contract": _active_contract(base, purpose),
        "runtime_state": _runtime_state_view(snap, effective_runtime_state, runtime_symbols),
        "audit_preview": audit_preview,
        "trace_driven_rethinking": _trace_driven_rethinking_preview(
            snap,
            graph,
            base,
            purpose,
            focus_ids,
            audit_preview,
            item_budget,
        ),
        "risks_and_gates": _risks_and_gates(base, runtime_symbols, purpose),
        "affordances": _affordances(base, runtime_symbols, focus_ids, purpose, provenance_index),
    }
    budget_report = _budget_report(
        sections, token_budget=token_budget,
        truncations=[symbol_truncation] if symbol_truncation else [],
    )
    return {
        "view_type": "context_envelope",
        "purpose": purpose,
        "run_id": snap.run_id,
        "focus_ids": focus_ids,
        **sections,
        "budget_report": budget_report,
    }


def _items_for_budget(token_budget: int) -> int:
    if token_budget <= 3000:
        return 6
    if token_budget <= 6000:
        return 10
    return 14


def _asset_limit_for_purpose(purpose: str, item_budget: int) -> int:
    if purpose == "codegen":
        return max(8, item_budget)
    if purpose == "audit":
        return max(6, item_budget // 2)
    if purpose == "critic":
        return max(6, item_budget // 2)
    return max(6, item_budget // 2)


def _provenance_limit_for_purpose(purpose: str, item_budget: int) -> int:
    if purpose == "audit":
        return max(8, item_budget)
    if purpose == "critic":
        return max(8, item_budget)
    if purpose == "codegen":
        return max(6, item_budget // 2)
    return max(6, item_budget // 2)


def _protected_context(snap: Snapshot, base: dict[str, Any]) -> dict[str, Any]:
    confirmed_sources = {
        "pi_confirmed", "user_confirmed", "api_confirmed",
        "manual_confirmation", "domain_default",
    }
    confirmed_design = {
        field: value
        for field, value in (snap.design or {}).items()
        if (snap.design_meta or {}).get(field, {}).get("source") in confirmed_sources
        or field not in (snap.design_meta or {})
    }
    return {
        "goal": snap.goals[-1].text if snap.goals else snap.goal,
        "active_constraints": _active_constraints(snap),
        "open_interrupts": base.get("open_interrupts", []),
        "open_approvals": base.get("open_approvals", []),
        "pi_confirmed_design": confirmed_design,
        "design_meta": {
            field: (snap.design_meta or {}).get(field, {})
            for field in confirmed_design
        },
    }


def _active_constraints(snap: Snapshot) -> list[str]:
    constraints = []
    for finding in snap.findings:
        if finding.severity == "blocking":
            constraints.append(f"{finding.finding_type}: {finding.summary}")
    for trigger in snap.triggers:
        if trigger.status == "open" and trigger.severity == "blocking":
            constraints.append(f"{trigger.trigger_type}: {trigger.summary}")
    active = next((a for a in reversed(snap.attempts) if a.status in {"planned", "running"}), None)
    if active:
        constraints.append(f"active_attempt={active.attempt_id}; do not start unrelated execution")
    return constraints[-8:]


def _runtime_state_from_snapshot(
    snap: Snapshot,
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    if _has_runtime_inventory(runtime_state):
        return _compact_runtime_state(runtime_state)
    for outcome in reversed(snap.outcomes):
        metrics = outcome.metrics or {}
        for key in ("kernel_state", "runtime_state"):
            candidate = metrics.get(key)
            if _has_runtime_inventory(candidate):
                return _compact_runtime_state(candidate)
    return _compact_runtime_state(runtime_state or {})


def _has_runtime_inventory(state: Any) -> bool:
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except Exception:
            return False
    return isinstance(state, dict) and bool(state.get("variables") or state.get("imports"))


def _compact_runtime_state(state: Any, *, limit: int = 50) -> dict[str, Any]:
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except Exception:
            return {}
    if not isinstance(state, dict):
        return {}
    variables = state.get("variables", {})
    imports = state.get("imports", [])
    jobs = state.get("jobs", [])
    processes = state.get("processes", [])
    notebook = state.get("notebook", {})
    if not isinstance(variables, dict):
        variables = {}
    if not isinstance(imports, list):
        imports = []
    if not isinstance(jobs, list):
        jobs = []
    if not isinstance(processes, list):
        processes = []
    if not isinstance(notebook, dict):
        notebook = {}
    variables = dict(sorted(variables.items(), key=lambda item: str(item[0])))
    imports = sorted(str(item) for item in imports)
    return {
        "variables": dict(list(variables.items())[:limit]),
        "imports": imports[:limit],
        "jobs": _compact_runtime_rows(jobs, limit=12),
        "processes": _compact_runtime_rows(processes, limit=12),
        "notebook": _compact_mapping(notebook, limit=8),
    }


def _compact_runtime_rows(rows: list[Any], *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    allowed = {
        "job_id", "job_type", "run_id", "status", "stale", "retryable",
        "attempt", "created_at", "started_at", "heartbeat_at",
        "finished_at", "error", "pid", "name", "kind", "cmd", "state",
    }
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        compact = {
            key: value
            for key, value in row.items()
            if key in allowed and value not in ("", None, [], {})
        }
        if compact:
            out.append(compact)
    return out


def _runtime_symbols(
    snap: Snapshot,
    *,
    runtime_state: dict[str, Any],
    focus_ids: list[str],
    limit: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    symbols: dict[str, dict[str, Any]] = {}
    variables = runtime_state.get("variables", {}) if isinstance(runtime_state, dict) else {}
    for name, summary in sorted((variables or {}).items()):
        symbols[name] = _symbol_asset(name, summary)
    for artifact in snap.artifacts:
        symbol_id = _artifact_symbol_id(artifact, symbols)
        symbols[symbol_id] = {
            "kind": "artifact",
            "symbol_id": symbol_id,
            "artifact_id": artifact.artifact_id,
            "path": artifact.path,
            "type": (
                artifact.metadata.get("type")
                or artifact.metadata.get("data_type")
                or artifact.kind
                or "artifact"
            ),
            "source": "artifact",
            "status": "available",
            "created_by_attempt": artifact.attempt_id,
            "summary": artifact.summary or artifact.path,
            "metadata": _compact_mapping(artifact.metadata, limit=8),
        }
    selected_ids = _select_asset_ids(symbols, snap, focus_ids, limit)
    selected = {symbol_id: symbols[symbol_id] for symbol_id in selected_ids}
    if len(symbols) <= len(selected):
        return selected, None
    return selected, {
        "section": "runtime_symbols",
        "kept": len(selected),
        "dropped": len(symbols) - len(selected),
        "total": len(symbols),
    }


def _symbol_asset(name: str, summary: Any) -> dict[str, Any]:
    text = str(summary)
    type_name = text.split("(", 1)[0] if "(" in text else text
    shape = ""
    if "(" in text and text.endswith(")"):
        shape = text.split("(", 1)[1][:-1]
        if shape.startswith("(") and shape.endswith(")"):
            shape = shape[1:-1]
        parts = [part.strip() for part in shape.split(",") if part.strip()]
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            shape = "x".join(parts)
    return {
        "kind": "kernel_symbol",
        "symbol_id": name,
        "name": name,
        "type": type_name,
        "shape": shape,
        "source": "kernel",
        "status": "available",
        "summary": text,
    }


def _artifact_symbol_id(artifact: Any, existing: dict[str, dict[str, Any]]) -> str:
    path = (artifact.path or "").replace("\\", "/")
    base = path.rsplit("/", 1)[-1] if path else ""
    symbol_id = base or artifact.artifact_id
    if symbol_id not in existing:
        return symbol_id
    return f"{symbol_id}#{artifact.artifact_id}"


def _select_asset_ids(
    assets: dict[str, dict[str, Any]],
    snap: Snapshot,
    focus_ids: list[str],
    limit: int,
) -> list[str]:
    scored: list[tuple[int, str]] = []
    recent_attempt_ids = {
        attempt.attempt_id
        for attempt in snap.attempts[-5:]
    }
    focus = set(focus_ids)
    for asset_id, asset in assets.items():
        score = 0
        if asset_id in focus or asset.get("artifact_id") in focus or asset.get("name") in focus:
            score += 100
        if asset.get("created_by_attempt") in recent_attempt_ids:
            score += 30
        if asset.get("kind") == "kernel_symbol":
            score += 20
        if "adata" in asset_id.lower():
            score += 15
        if asset.get("type") in {"figure", "table", "DataFrame", "AnnData"}:
            score += 10
        scored.append((score, asset_id))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [asset_id for _, asset_id in scored[:limit]]


def _working_set(
    snap: Snapshot,
    base: dict[str, Any],
    purpose: str,
    runtime_symbols: dict[str, dict[str, Any]],
    focus_ids: list[str],
) -> dict[str, Any]:
    return {
        "active_node": base.get("active_node_id", ""),
        "next_decision_needed": _next_decision_needed(snap, purpose),
        "current_assets": [
            {"ref": symbol_id, "summary": asset.get("summary", ""), "kind": asset.get("kind", "")}
            for symbol_id, asset in runtime_symbols.items()
        ],
        "recent_delta": _recent_delta(snap, limit=4),
        "open_items": _open_items(base),
        "focus_ids": focus_ids,
    }


def _provenance_index(
    snap: Snapshot,
    graph: dict | None,
    *,
    purpose: str,
    focus_ids: list[str],
    limit: int,
) -> dict[str, Any]:
    attempts = {attempt.attempt_id: attempt for attempt in snap.attempts}
    artifacts = {artifact.artifact_id: artifact for artifact in snap.artifacts}
    observations = {obs.observation_id: obs for obs in snap.observations}
    conclusions = {con.conclusion_id: con for con in snap.conclusions}
    from pertura.core.evidence_chain import latest_outcomes_by_attempt
    outcomes_by_attempt = latest_outcomes_by_attempt(snap.outcomes)
    stale_ids = _stale_dependency_ids(snap)
    candidates = _provenance_candidates(
        snap,
        focus_ids=focus_ids,
        stale_ids=stale_ids,
        observations=observations,
        conclusions=conclusions,
        limit=limit * 3,
    )
    entries: dict[str, dict[str, Any]] = {}
    for node_id in candidates:
        if len(entries) >= limit:
            break
        if node_id in observations:
            entries[node_id] = _observation_provenance(
                observations[node_id],
                attempts=attempts,
                artifacts=artifacts,
                conclusions=conclusions,
                stale_ids=stale_ids,
                graph=graph,
            )
        elif node_id in conclusions:
            entries[node_id] = _conclusion_provenance(
                conclusions[node_id],
                observations=observations,
                attempts=attempts,
                artifacts=artifacts,
                outcomes_by_attempt=outcomes_by_attempt,
                stale_ids=stale_ids,
                graph=graph,
            )
        elif node_id in artifacts:
            entries[node_id] = _artifact_provenance(
                artifacts[node_id],
                attempts=attempts,
                stale_ids=stale_ids,
                graph=graph,
            )
    expandable = [
        node_id
        for node_id, entry in entries.items()
        if entry.get("trace_available")
    ][:6]
    return {
        "view_type": "provenance_index",
        "purpose": purpose,
        "entries": entries,
        "count": len(entries),
        "truncated": len(candidates) > len(entries),
        "expand": [
            {
                "tool": "trace_upstream",
                "args": {"node_id": node_id, "depth": 4},
                "why": "expand full dependency path for this evidence node",
            }
            for node_id in expandable
        ],
    }


def _provenance_candidates(
    snap: Snapshot,
    *,
    focus_ids: list[str],
    stale_ids: set[str],
    observations: dict[str, Any],
    conclusions: dict[str, Any],
    limit: int,
) -> list[str]:
    candidates: list[str] = []
    candidates.extend(focus_ids)
    for focus_id in focus_ids:
        obs = observations.get(focus_id)
        if obs:
            candidates.extend(obs.input_ids)
            if obs.artifact_id:
                candidates.append(obs.artifact_id)
            candidates.extend(
                con.conclusion_id
                for con in snap.conclusions
                if focus_id in con.support_ids or focus_id in con.limitation_ids
            )
        con = conclusions.get(focus_id)
        if con:
            candidates.extend(con.support_ids)
            candidates.extend(con.limitation_ids)
    candidates.extend(sorted(stale_ids))
    for con in reversed(snap.conclusions):
        candidates.append(con.conclusion_id)
        candidates.extend(con.support_ids[:3])
    for obs in reversed(snap.observations):
        candidates.append(obs.observation_id)
    seen = set()
    out = []
    for node_id in candidates:
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        out.append(node_id)
        if len(out) >= limit:
            break
    return out


def _observation_provenance(
    obs: Any,
    *,
    attempts: dict[str, Any],
    artifacts: dict[str, Any],
    conclusions: dict[str, Any],
    stale_ids: set[str],
    graph: dict | None,
) -> dict[str, Any]:
    attempt = attempts.get(obs.attempt_id)
    artifact = artifacts.get(obs.artifact_id)
    supports = [
        con_id
        for con_id, con in conclusions.items()
        if obs.observation_id in con.support_ids
    ][:8]
    limits = [
        con_id
        for con_id, con in conclusions.items()
        if obs.observation_id in con.limitation_ids
    ][:8]
    design_fields = sorted(set(obs.design_fields_used or []) | set(getattr(attempt, "design_fields_used", []) or []))
    return {
        "node_type": "observation",
        "attempt": obs.attempt_id,
        "analysis_node": getattr(attempt, "analysis_node_id", "") if attempt else "",
        "branch": obs.branch_id,
        "artifact": obs.artifact_id,
        "artifact_path": artifact.path if artifact else "",
        "derived_from": obs.input_ids[:8],
        "supports": supports,
        "limits": limits,
        "target": obs.target,
        "metric": obs.metric,
        "contrast": obs.contrast,
        "method": obs.method,
        "variable_key": obs.variable_key,
        "parameter_hash": obs.parameter_hash,
        "method_version": obs.method_version,
        "depends_on_design": design_fields[:8],
        "stale": obs.observation_id in stale_ids,
        "trace_available": _graph_has_node(graph, obs.observation_id),
    }


def _conclusion_provenance(
    conclusion: Any,
    *,
    observations: dict[str, Any],
    attempts: dict[str, Any],
    artifacts: dict[str, Any],
    outcomes_by_attempt: dict[str, Any],
    stale_ids: set[str],
    graph: dict | None,
) -> dict[str, Any]:
    from pertura.core.evidence_chain import observation_evidence_status

    support_status = [
        {
            "id": support_id,
            "node_type": "observation" if support_id in observations else "unknown",
            "stale": support_id in stale_ids,
            "evidence": observation_evidence_status(
                observations.get(support_id),
                attempts=attempts,
                artifacts=artifacts,
                observations=observations,
                outcomes_by_attempt=outcomes_by_attempt,
            ),
        }
        for support_id in conclusion.support_ids[:12]
    ]
    limitation_status = [
        {
            "id": limitation_id,
            "stale": limitation_id in stale_ids,
        }
        for limitation_id in conclusion.limitation_ids[:12]
    ]
    return {
        "node_type": "conclusion",
        "grade": conclusion.grade,
        "support_ids": conclusion.support_ids[:12],
        "limitation_ids": conclusion.limitation_ids[:12],
        "support_status": support_status,
        "limitation_status": limitation_status,
        "stale": conclusion.conclusion_id in stale_ids or any(item["stale"] for item in support_status),
        "evidence_verified": bool(support_status) and all(item.get("evidence", {}).get("successful") for item in support_status),
        "trace_available": _graph_has_node(graph, conclusion.conclusion_id),
    }


def _artifact_provenance(
    artifact: Any,
    *,
    attempts: dict[str, Any],
    stale_ids: set[str],
    graph: dict | None,
) -> dict[str, Any]:
    attempt = attempts.get(artifact.attempt_id)
    return {
        "node_type": "artifact",
        "attempt": artifact.attempt_id,
        "analysis_node": getattr(attempt, "analysis_node_id", "") if attempt else "",
        "path": artifact.path,
        "kind": artifact.kind,
        "input_ids": list((artifact.metadata or {}).get("input_ids", []))[:8],
        "stale": artifact.artifact_id in stale_ids,
        "trace_available": _graph_has_node(graph, artifact.artifact_id),
    }


def _stale_dependency_ids(snap: Snapshot) -> set[str]:
    stale: set[str] = set()
    for finding in snap.findings:
        if finding.finding_type == "potentially_stale_dependency":
            stale.update(item for item in finding.affected_ids if item)
    return stale


def _graph_has_node(graph: dict | None, node_id: str) -> bool:
    if not graph:
        return False
    return any(node.get("node_id") == node_id for node in graph.get("nodes", []))


def _next_decision_needed(snap: Snapshot, purpose: str) -> str:
    if any(i.status == "open" for i in snap.interrupts):
        return "answer_interrupt"
    if any(a.status in {"planned", "running"} for a in snap.attempts):
        return "execute_or_wait_active_attempt"
    return {
        "deliberation": "choose_capability_or_node_transition",
        "codegen": "write_code_for_selected_capability",
        "critic": "assess_last_attempt",
        "audit": "trace_or_compare_evidence",
    }.get(purpose, "choose_next_action")


def _recent_delta(snap: Snapshot, *, limit: int) -> list[dict[str, Any]]:
    out = []
    for attempt in snap.attempts[-limit:]:
        observations = [o for o in snap.observations if o.attempt_id == attempt.attempt_id]
        artifacts = [a for a in snap.artifacts if a.attempt_id == attempt.attempt_id]
        outcome = next((o for o in reversed(snap.outcomes) if o.attempt_id == attempt.attempt_id), None)
        out.append({
            "attempt_id": attempt.attempt_id,
            "node": attempt.analysis_node_id,
            "stage": attempt.stage,
            "status": attempt.status,
            "title": attempt.title,
            "outcome": outcome.status if outcome else "",
            "new_observations": len(observations),
            "new_artifacts": len(artifacts),
        })
    return out


def _open_items(base: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for trigger in base.get("open_triggers", []):
        items.append({"kind": "trigger", **trigger})
    for finding in base.get("recent_findings", []):
        if finding.get("severity") in {"warning", "blocking"}:
            items.append({"kind": "finding", **finding})
    for item in (base.get("current_node_progress") or {}).get("missing_completion", []):
        items.append({"kind": "missing_completion", **item})
    return items[-12:]


def _analysis_state_for_purpose(base: dict[str, Any], purpose: str) -> dict[str, Any]:
    if purpose == "codegen":
        return {
            "active_node_id": base.get("active_node_id", ""),
            "analysis_node": base.get("analysis_node", {}),
            "design": base.get("design", {}),
            "design_meta": base.get("design_meta", {}),
            "current_node_progress": base.get("current_node_progress", {}),
        }
    if purpose == "critic":
        return {
            "active_node_id": base.get("active_node_id", ""),
            "current_node_progress": base.get("current_node_progress", {}),
            "recent_findings": base.get("recent_findings", [])[-6:],
        }
    if purpose == "audit":
        return {
            "active_node_id": base.get("active_node_id", ""),
            "design": base.get("design", {}),
            "design_meta": base.get("design_meta", {}),
            "blocked_transitions": base.get("blocked_transitions", []),
            "graph_summary": base.get("graph_summary", {}),
        }
    return {
        "active_node_id": base.get("active_node_id", ""),
        "analysis_node": base.get("analysis_node", {}),
        "reachable_nodes": base.get("reachable_nodes", []),
        "blocked_transitions": base.get("blocked_transitions", []),
        "gate_requirements": base.get("gate_requirements", []),
        "current_node_progress": base.get("current_node_progress", {}),
        "design": base.get("design", {}),
        "design_meta": base.get("design_meta", {}),
    }


def _active_contract(base: dict[str, Any], purpose: str) -> dict[str, Any]:
    node = base.get("analysis_node", {}) or {}
    progress = base.get("current_node_progress", {}) or {}
    capability_cards = _capability_cards(base)
    selected = _selected_capability_card(base, capability_cards)
    contract = {
        "node_id": node.get("node_id", ""),
        "title": node.get("title", ""),
        "purpose": node.get("purpose", ""),
        "allowed_capabilities": node.get("allowed_capabilities", []),
        "selected_capability": selected,
        "capability_options": capability_cards[:4],
        "recommended_actions": node.get("recommended_actions", []),
        "expected_outputs": node.get("expected_outputs", []),
        "missing_completion": progress.get("missing_completion", []),
        "audit_checklist": _contract_audit_checklist(node, progress, selected),
    }
    if purpose == "codegen":
        contract["capabilities"] = capability_cards
    return contract


def _audit_preview(
    snap: Snapshot,
    graph: dict | None,
    purpose: str,
    item_budget: int,
) -> dict[str, Any]:
    from pertura.core.audit import audit_run

    audit = audit_run(snap, graph or {})
    issue_limit = max(3, min(8, item_budget // 2))
    top_issues = [
        _compact_audit_issue(item)
        for item in [*audit.get("errors", []), *audit.get("warnings", [])][:issue_limit]
    ]
    return {
        "audit_type": audit.get("audit_type", "run_audit"),
        "purpose": purpose,
        "ok": audit.get("ok", False),
        "severity": audit.get("severity", "error"),
        "summary": audit.get("summary", {}),
        "coverage": audit.get("coverage", {}),
        "top_issue_codes": [item.get("code", "") for item in top_issues if item.get("code")],
        "top_issues": top_issues,
        "advice": audit.get("advice", [])[:issue_limit],
        "next_actions": audit.get("next_actions", [])[:issue_limit],
        "truncated": len(audit.get("errors", [])) + len(audit.get("warnings", [])) > len(top_issues),
        "expand": {
            "tool": "audit_run",
            "args": {},
            "why": "inspect the full deterministic run audit before committing or finishing",
        },
    }


def _trace_driven_rethinking_preview(
    snap: Snapshot,
    graph: dict | None,
    base: dict[str, Any],
    purpose: str,
    focus_ids: list[str],
    audit_preview: dict[str, Any],
    item_budget: int,
) -> dict[str, Any]:
    """Compact rethinking plan surfaced directly in every context envelope.

    `plan_rethinking` remains the expandable operator. This preview is the
    prompt-facing reminder that questionable results should be traced upstream
    before rerunning, branching, or reporting.
    """
    target_id, issue = _rethinking_target_from_view(snap, base, focus_ids, audit_preview)
    needs_plan = bool(target_id or issue or not audit_preview.get("ok", True))
    if not needs_plan:
        return {
            "view_type": "rethinking_plan_preview",
            "status": "not_needed",
            "target_id": "",
            "issue": "",
            "summary": "No focused finding, audit issue, stale evidence, or observation-memory conflict needs trace-driven rethinking in this view.",
            "suspected_roots": [],
            "recommended_actions": [],
            "expand": {
                "tool": "plan_rethinking",
                "args": {},
                "why": "generate a trace-driven rethinking plan if a result becomes suspicious",
            },
        }

    from pertura.core.rethinking import plan_rethinking

    plan = plan_rethinking(
        snap,
        target_id,
        issue=issue or "audit preview reports unresolved scientific or runtime issues",
        depth=5,
        graph=graph,
    )
    action_limit = max(3, min(8, item_budget // 2))
    root_limit = max(2, min(6, item_budget // 3))
    return {
        "view_type": "rethinking_plan_preview",
        "status": plan.get("status", "needs_review"),
        "target_id": plan.get("target_id", target_id),
        "issue": plan.get("issue", issue),
        "summary": plan.get("summary", ""),
        "evidence_status": (plan.get("evidence_review") or {}).get("status", ""),
        "upstream_node_count": (plan.get("upstream_trace") or {}).get("node_count", 0),
        "impact_summary": (plan.get("impact") or {}).get("affected", {}),
        "suspected_roots": [
            {
                "root_id": root.get("root_id", ""),
                "root_type": root.get("root_type", ""),
                "priority": root.get("priority", ""),
                "reason": root.get("reason", ""),
                "next_action": root.get("next_action", {}),
            }
            for root in (plan.get("suspected_roots") or [])[:root_limit]
        ],
        "recommended_actions": (plan.get("recommended_actions") or [])[:action_limit],
        "policy": plan.get("policy", {}),
        "expand": {
            "tool": "plan_rethinking",
            "args": {"node_id": plan.get("target_id", target_id), "issue": plan.get("issue", issue)},
            "why": "expand the evidence review, upstream trace, impact walk, and repair menu",
        },
    }


def _rethinking_target_from_view(
    snap: Snapshot,
    base: dict[str, Any],
    focus_ids: list[str],
    audit_preview: dict[str, Any],
) -> tuple[str, str]:
    if focus_ids:
        return focus_ids[0], "focused evidence or result needs trace-driven review"

    for finding in reversed(base.get("recent_findings", []) or []):
        if finding.get("severity") in {"blocking", "error", "high", "warning"}:
            affected = list(finding.get("affected_ids") or [])
            return affected[0] if affected else finding.get("finding_id", ""), finding.get("summary", "") or finding.get("type", "")

    for issue in audit_preview.get("top_issues", []) or []:
        details = issue.get("details", {}) or {}
        target_id = (
            details.get("conclusion_id")
            or details.get("observation_id")
            or details.get("artifact_id")
            or details.get("attempt_id")
            or ""
        )
        if target_id:
            return target_id, issue.get("message", "") or issue.get("code", "")

    memory_summary = (base.get("observation_memory", {}) or {}).get("summary", {}) or {}
    if memory_summary.get("cross_context_divergences", 0) or memory_summary.get("conflicts", 0):
        return "", "observation memory reports divergence or conflict"

    if not audit_preview.get("ok", True):
        return "", "audit preview reports unresolved issues"

    for collection, attr in (
        (getattr(snap, "conclusions", []) or [], "conclusion_id"),
        (getattr(snap, "observations", []) or [], "observation_id"),
        (getattr(snap, "artifacts", []) or [], "artifact_id"),
        (getattr(snap, "attempts", []) or [], "attempt_id"),
    ):
        if collection:
            return getattr(collection[-1], attr, ""), ""
    return "", ""


def _compact_audit_issue(issue: dict[str, Any]) -> dict[str, Any]:
    details = issue.get("details", {}) or {}
    compact_details = {}
    for key in (
        "attempt_id",
        "analysis_node_id",
        "capability_id",
        "conclusion_id",
        "observation_id",
        "artifact_id",
        "node_ids",
        "missing_support_ids",
        "missing_observations",
        "missing_artifacts",
        "unsupported_count",
    ):
        if key in details and details[key] not in (None, "", [], {}):
            compact_details[key] = details[key]
    unsupported = details.get("unsupported_support") or []
    if unsupported:
        compact_details["unsupported_support"] = unsupported[:3]
    return {
        "code": issue.get("code", ""),
        "severity": issue.get("severity", ""),
        "message": issue.get("message", ""),
        "details": compact_details,
    }


def _capability_cards(base: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = list((base.get("analysis_node", {}) or {}).get("allowed_capabilities", []) or [])
    if not allowed:
        return []
    by_id = {
        cap.get("id"): cap
        for cap in base.get("capabilities", [])
        if isinstance(cap, dict) and cap.get("id")
    }
    cards = []
    for cap_id in allowed:
        cap = by_id.get(cap_id, {"id": cap_id, "missing": True})
        cards.append(_capability_card(cap_id, cap))
    return cards


def _capability_card(cap_id: str, cap: dict[str, Any]) -> dict[str, Any]:
    if cap.get("missing"):
        return {"id": cap_id, "missing": True}
    return {
        "id": cap_id,
        "title": cap.get("title", cap_id.replace("_", " ")),
        "description": cap.get("description", ""),
        "kind": cap.get("kind", "execute"),
        "risk": cap.get("risk", "low"),
        "tools": cap.get("tools", []),
        "backend": cap.get("backend", "kernel"),
        "required_inputs": cap.get("required_inputs", []),
        "expected_observations": cap.get("expected_observations", []),
        "expected_artifacts": cap.get("expected_artifacts", []),
        "packages": cap.get("packages", [])[:6],
        "functions": cap.get("functions", [])[:8],
        "analysis_modes": cap.get("analysis_modes", [])[:6],
    }


def _selected_capability_card(base: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    selected_id = _best_affordance_capability(base)
    if selected_id:
        for card in cards:
            if card.get("id") == selected_id:
                return card
    return cards[0] if cards else {}


def _contract_audit_checklist(node: dict[str, Any], progress: dict[str, Any], selected: dict[str, Any]) -> list[str]:
    checks = []
    for requirement in progress.get("requires", []):
        if not requirement.get("passed"):
            checks.append(f"resolve requirement: {requirement.get('description') or requirement.get('condition_id')}")
    for requirement in progress.get("must_confirm", []):
        if not requirement.get("passed"):
            checks.append(f"confirm design gate: {requirement.get('description') or requirement.get('condition_id')}")
    for value in selected.get("required_inputs", [])[:4]:
        checks.append(f"verify input: {value}")
    for value in selected.get("expected_observations", [])[:4]:
        checks.append(f"register observation: {value}")
    for value in selected.get("expected_artifacts", [])[:4]:
        checks.append(f"register artifact: {value}")
    for value in node.get("expected_outputs", [])[:4]:
        checks.append(f"node output: {value}")
    if selected.get("risk") in {"medium", "high"}:
        checks.append("record risk rationale before committing result")
    return _dedupe_preserve_order(checks)[:10]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _runtime_state_view(
    snap: Snapshot,
    runtime_state: dict[str, Any],
    runtime_symbols: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    symbol_refs = [
        symbol_id
        for symbol_id, asset in runtime_symbols.items()
        if asset.get("kind") == "kernel_symbol"
    ]
    artifact_refs = [
        symbol_id
        for symbol_id, asset in runtime_symbols.items()
        if asset.get("kind") == "artifact"
    ]
    active_attempt = _active_attempt_runtime(snap)
    recent_executions = _recent_execution_runtime(snap, limit=6)
    kernel_alive = bool((runtime_state or {}).get("variables") or (runtime_state or {}).get("imports"))
    return {
        "kernel_alive": kernel_alive,
        "runtime_symbol_count": len(runtime_symbols),
        "selected_symbol_count": len(symbol_refs),
        "selected_artifact_count": len(artifact_refs),
        "symbol_refs": symbol_refs,
        "artifact_refs": artifact_refs,
        "imports": (runtime_state or {}).get("imports", [])[:20],
        "active_attempt": active_attempt,
        "recent_executions": recent_executions,
        "notebook": _notebook_runtime(snap, runtime_state),
        "jobs": _jobs_runtime(runtime_state.get("jobs", [])),
        "processes": _processes_runtime(runtime_state.get("processes", [])),
    }


def _active_attempt_runtime(snap: Snapshot) -> dict[str, Any]:
    attempt = next((item for item in snap.attempts if item.attempt_id == snap.active_attempt), None)
    if attempt is None:
        attempt = next((item for item in reversed(snap.attempts) if item.status in {"planned", "running"}), None)
    if attempt is None:
        return {}
    outcome = next((item for item in reversed(snap.outcomes) if item.attempt_id == attempt.attempt_id), None)
    return {
        "attempt_id": attempt.attempt_id,
        "status": attempt.status,
        "stage": attempt.stage,
        "analysis_node": attempt.analysis_node_id,
        "branch": attempt.branch_id,
        "capability_ids": attempt.capability_ids,
        "outcome": outcome.status if outcome else "",
        "execution": _execution_metrics(outcome),
    }


def _recent_execution_runtime(snap: Snapshot, *, limit: int) -> list[dict[str, Any]]:
    out = []
    for attempt in reversed(snap.attempts[-limit:]):
        outcome = next((item for item in reversed(snap.outcomes) if item.attempt_id == attempt.attempt_id), None)
        observations = [obs for obs in snap.observations if obs.attempt_id == attempt.attempt_id]
        artifacts = [artifact for artifact in snap.artifacts if artifact.attempt_id == attempt.attempt_id]
        out.append({
            "attempt_id": attempt.attempt_id,
            "status": attempt.status,
            "stage": attempt.stage,
            "analysis_node": attempt.analysis_node_id,
            "branch": attempt.branch_id,
            "outcome": outcome.status if outcome else "",
            "execution": _execution_metrics(outcome),
            "observations": len(observations),
            "artifacts": len(artifacts),
        })
    return out


def _execution_metrics(outcome: Any | None) -> dict[str, Any]:
    if outcome is None:
        return {}
    metrics = outcome.metrics or {}
    return {
        key: metrics.get(key)
        for key in (
            "returncode", "timed_out", "timed_out_at", "soft_timeout_hit",
            "execution_time", "observations_registered", "stdout_chars",
        )
        if key in metrics
    }


def _notebook_runtime(snap: Snapshot, runtime_state: dict[str, Any]) -> dict[str, Any]:
    notebook = dict((runtime_state or {}).get("notebook", {}) or {})
    if snap.attempts and "path" not in notebook:
        notebook["path"] = "notebooks/execution.ipynb"
    if snap.attempts:
        notebook.setdefault("latest_attempt", snap.attempts[-1].attempt_id)
        notebook.setdefault("recorded", True)
    return _compact_mapping(notebook, limit=8)


def _jobs_runtime(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    active = [job for job in jobs if job.get("status") in {"queued", "running"}]
    failed = [job for job in jobs if job.get("status") == "failed" or job.get("retryable")]
    return {
        "count": len(jobs),
        "active_count": len(active),
        "retryable_count": len(failed),
        "active": active[:4],
        "recent": jobs[:6],
    }


def _processes_runtime(processes: list[dict[str, Any]]) -> dict[str, Any]:
    active = [
        proc for proc in processes
        if proc.get("status") in {"running", "queued"} or proc.get("state") in {"running", "sleeping"}
    ]
    return {
        "count": len(processes),
        "active_count": len(active),
        "active": active[:4],
        "recent": processes[:6],
    }


def _risks_and_gates(base: dict[str, Any], assets: dict[str, dict[str, Any]], purpose: str) -> dict[str, Any]:
    blocked_actions = []
    if any(asset.get("name") == "adata" for asset in assets.values()):
        blocked_actions.append({
            "action": "reload_dataset",
            "reason": "kernel symbol `adata` is already available; inspect or reuse it first",
        })
    if base.get("active_node_id") and base.get("analysis_node", {}).get("allowed_capabilities"):
        allowed = base["analysis_node"]["allowed_capabilities"]
    else:
        allowed = []
    return {
        "allowed_capabilities": allowed,
        "blocked_actions": blocked_actions,
        "open_risks": _open_items(base),
        "purpose": purpose,
    }


def _affordances(
    base: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    focus_ids: list[str],
    purpose: str,
    provenance_index: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    capability_id = _best_affordance_capability(base)
    template_args = _template_affordance_args(base)
    out = [
        {"tool": "get_node_contract", "args": {}, "why": "inspect active node contract, readiness, and next actions"},
        {"tool": "list_capabilities", "args": {}, "why": "inspect capabilities allowed in the active node"},
        {"tool": "list_artifacts", "args": {}, "why": "expand run outputs beyond selected assets"},
    ]
    if focus_ids:
        out.insert(0, {
            "tool": "plan_rethinking",
            "args": {"node_id": focus_ids[0], "issue": "focused evidence or recent finding needs trace-driven review"},
            "why": "convert evidence review, upstream trace, and downstream impact into a compact repair/branch/intervention plan",
        })
    else:
        recent_findings = base.get("recent_findings", []) or []
        actionable = next(
            (
                item for item in reversed(recent_findings)
                if item.get("severity") in {"blocking", "error", "high", "warning"}
            ),
            None,
        )
        if actionable:
            affected = (actionable.get("affected_ids") or [])
            out.insert(0, {
                "tool": "plan_rethinking",
                "args": {
                    "node_id": affected[0] if affected else actionable.get("finding_id", ""),
                    "issue": actionable.get("summary", "") or actionable.get("type", ""),
                },
                "why": "turn the latest actionable finding into a trace-driven recovery plan",
            })
    if capability_id:
        out.append({
            "tool": "get_capability_template",
            "args": {"capability_id": capability_id, **template_args},
            "why": "get a bounded code skeleton for the selected capability",
        })
    symbol_name = next(
        (
            asset.get("name")
            for asset in assets.values()
            if asset.get("kind") == "kernel_symbol"
            and str(asset.get("name", "")).isidentifier()
        ),
        "",
    )
    if symbol_name:
        args = {"code": f"print(type({symbol_name}), getattr({symbol_name}, 'shape', None))"}
        if capability_id:
            args["capability_ids"] = [capability_id]
        out.append({
            "tool": "execute_code",
            "args": args,
            "why": "inspect selected kernel symbols with a small code cell",
        })
    provenance_entries = (provenance_index or {}).get("entries", {})
    provenance_refs = [
        node_id
        for node_id, entry in provenance_entries.items()
        if entry.get("trace_available")
    ]
    for focus_id in focus_ids[:4]:
        out.append({"tool": "review_evidence_chain", "args": {"node_id": focus_id}, "why": "self-audit whether focused evidence is verified and non-stale"})
        out.append({"tool": "plan_rethinking", "args": {"node_id": focus_id}, "why": "plan repair or reinterpretation if focused evidence is weak, stale, or unsupported"})
    for node_id in provenance_refs[:4]:
        if node_id not in focus_ids:
            out.append({"tool": "review_evidence_chain", "args": {"node_id": node_id}, "why": "review indexed evidence before expanding provenance"})
    if purpose == "audit":
        out.append({"tool": "compare_branches", "args": {}, "why": "compare branch-specific evidence"})
    if base.get("observation_memory", {}).get("summary", {}).get("cross_context_divergences", 0):
        out.append({"tool": "query_observation_memory", "args": {}, "why": "inspect conflicts and divergences"})
        out.append({"tool": "plan_rethinking", "args": {"issue": "observation memory reports divergence or conflict"}, "why": "choose whether to trace, branch, rerun, or downgrade conflicting scientific observations"})
    return out[:10]


def _template_affordance_args(base: dict[str, Any]) -> dict[str, Any]:
    design = base.get("design", {}) or {}
    columns = {}
    if design.get("target_column"):
        columns["target"] = design.get("target_column")
        columns["perturbation"] = design.get("target_column")
    if design.get("guide_column"):
        columns["guide"] = design.get("guide_column")
    if design.get("state_column"):
        columns["state"] = design.get("state_column")
    args: dict[str, Any] = {}
    if columns:
        args["columns"] = columns
    if isinstance(design.get("control_labels"), list) and design.get("control_labels"):
        args["control_labels"] = design.get("control_labels")
    if design.get("target"):
        args["target"] = design.get("target")
    if isinstance(design.get("parameters"), dict) and design.get("parameters"):
        args["parameters"] = design.get("parameters")
    return args


def _best_affordance_capability(base: dict[str, Any]) -> str:
    allowed = list((base.get("analysis_node", {}) or {}).get("allowed_capabilities", []) or [])
    if not allowed:
        return ""
    caps = {
        cap.get("id"): cap
        for cap in base.get("capabilities", [])
        if isinstance(cap, dict) and cap.get("id")
    }
    preferred_kinds = {"read", "review"}
    for cap_id in allowed:
        cap = caps.get(cap_id, {})
        if cap.get("kind") in preferred_kinds and cap.get("risk", "low") == "low":
            return cap_id
    for cap_id in allowed:
        cap = caps.get(cap_id, {})
        tools = cap.get("tools", []) or []
        if "execute_code" in tools and cap.get("risk", "low") == "low":
            return cap_id
    return allowed[0]


def _budget_report(
    sections: dict[str, Any],
    *,
    token_budget: int,
    truncations: list[dict[str, Any]],
) -> dict[str, Any]:
    section_tokens = {
        name: _estimate_tokens(value)
        for name, value in sections.items()
    }
    return {
        "total_budget_tokens": token_budget,
        "used_estimate": sum(section_tokens.values()),
        "sections": section_tokens,
        "truncated": truncations,
    }


def _estimate_tokens(value: Any) -> int:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return max(1, len(text) // 4)


def _compact_mapping(value: dict[str, Any], *, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in list(value)[:limit]}


def _capability_view(snap: Snapshot, node_view: dict, *, max_items: int) -> list[dict]:
    registry = CapabilityRegistry(snap.capabilities)
    active_ids = (node_view.get("current") or {}).get("allowed_capabilities", [])
    if active_ids:
        return registry.summarize(active_ids, limit=max_items)
    return registry.summarize(limit=max_items)


def _condition_status(cond, snap: Snapshot) -> dict:
    result = evaluate_condition(cond, snap)
    return {
        "condition_id": result.condition_id,
        "passed": result.passed,
        "tier": result.tier,
        "failure_mode": result.failure_mode,
        "description": cond.description,
        "message": result.message,
        "hard": result.hard,
        "details": result.details or {},
    }


def _bounded_walk_view(walk: dict, view_type: str, limit: int) -> dict:
    nodes = walk.get("nodes", [])[:limit]
    node_ids = {node.get("node_id") for node in nodes}
    edges = [
        edge for edge in walk.get("edges", [])
        if edge.get("source_id") in node_ids and edge.get("target_id") in node_ids
    ][:limit]
    return {
        "view_type": view_type,
        "start_node_id": walk.get("start_node_id"),
        "direction": walk.get("direction"),
        "depth": walk.get("depth"),
        "count": {"nodes": len(walk.get("nodes", [])), "edges": len(walk.get("edges", []))},
        "nodes": nodes,
        "edges": edges,
        "relation_summary": relation_summary(edges),
        "walk_relation_summary": walk.get("relation_summary", {}),
        "affected": walk.get("affected", {}),
        "truncated": len(walk.get("nodes", [])) > limit or len(walk.get("edges", [])) > limit,
    }
