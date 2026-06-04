"""Run-level audit for scientific traceability and delivery readiness."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def audit_run(snap, graph: dict | None = None, *, run_dir: str | Path | None = None) -> dict[str, Any]:
    """Audit a concrete run snapshot for replayable scientific evidence.

    This is intentionally deterministic and non-LLM. Spec audits answer
    whether a workflow is well authored; run audits answer whether a specific
    execution is ready to inspect, report, or publish.
    """
    graph = graph or {"nodes": [], "edges": []}
    run_root = Path(run_dir).resolve() if run_dir else None
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []

    _audit_graph(graph, errors, warnings)
    _audit_open_controls(snap, errors, warnings)
    _audit_analysis_nodes(snap, errors, warnings, info)
    _audit_reference_integrity(snap, errors, warnings)
    _audit_capability_outputs(snap, errors, warnings)
    _audit_conclusions(snap, graph, errors, warnings)
    _audit_artifacts(snap, run_root, errors, warnings)
    _audit_runtime_risks(snap, errors, warnings)

    severity = "ok"
    if errors:
        severity = "error"
    elif warnings:
        severity = "warning"
    return {
        "audit_type": "run_audit",
        "run_id": getattr(snap, "run_id", ""),
        "ok": not errors,
        "severity": severity,
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "info": len(info),
            "attempts": len(getattr(snap, "attempts", [])),
            "observations": len(getattr(snap, "observations", [])),
            "artifacts": len(getattr(snap, "artifacts", [])),
            "conclusions": len(getattr(snap, "conclusions", [])),
            "active_node_id": getattr(snap, "active_node_id", ""),
        },
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "coverage": {
            "completed_nodes": _completed_nodes(snap),
            "skipped_nodes": _skipped_nodes(snap),
            "open_interrupts": [item.interrupt_id for item in getattr(snap, "interrupts", []) if item.status == "open"],
            "open_approvals": [item.approval_id for item in getattr(snap, "approvals", []) if item.status == "open"],
            "stale_ids": sorted(_stale_ids(snap)),
        },
        "advice": _audit_advice(errors, warnings),
        "next_actions": _audit_next_actions(errors, warnings),
    }


def _issue(code: str, message: str, *, severity: str, **details) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "details": {k: v for k, v in details.items() if v not in (None, "", [], {})},
    }


def _audit_graph(graph: dict, errors: list, warnings: list) -> None:
    from pertura.core.graph import validate_graph

    violations = validate_graph(graph)
    for violation in violations:
        errors.append(_issue(
            "invalid_graph_projection",
            "Stored graph projection has a validation violation.",
            severity="error",
            violation=violation,
        ))


def _audit_open_controls(snap, errors: list, warnings: list) -> None:
    for interrupt in getattr(snap, "interrupts", []):
        if interrupt.status == "open":
            errors.append(_issue(
                "open_interrupt",
                "Run is waiting for unresolved human input.",
                severity="error",
                interrupt_id=interrupt.interrupt_id,
                source=interrupt.source,
                question=interrupt.question,
            ))
    for approval in getattr(snap, "approvals", []):
        if approval.status == "open":
            errors.append(_issue(
                "open_approval",
                "Run has an unresolved approval gate.",
                severity="error",
                approval_id=approval.approval_id,
                approval_type=approval.approval_type,
                subject_id=approval.subject_id,
            ))


def _audit_analysis_nodes(snap, errors: list, warnings: list, info: list) -> None:
    spec = getattr(snap, "analysis_spec", {}) or {}
    nodes = spec.get("nodes", []) if isinstance(spec, dict) else []
    if not nodes:
        warnings.append(_issue(
            "missing_analysis_spec",
            "Run has no analysis graph spec; node-level progress cannot be audited.",
            severity="warning",
        ))
        return
    completed = set(_completed_nodes(snap))
    skipped = set(_skipped_nodes(snap))
    visited = {visit.node_id for visit in getattr(snap, "node_visits", [])}
    required_nodes = [node.get("node_id", "") for node in nodes if node.get("node_id")]
    incomplete = [
        node_id for node_id in required_nodes
        if node_id not in completed and node_id not in skipped
    ]
    if incomplete:
        errors.append(_issue(
            "incomplete_analysis_nodes",
            "Some analysis nodes are neither completed nor explicitly skipped.",
            severity="error",
            node_ids=incomplete[:20],
            remaining_count=len(incomplete),
        ))
    unvisited = [node_id for node_id in required_nodes if node_id not in visited]
    if unvisited:
        info.append(_issue(
            "unvisited_analysis_nodes",
            "Some analysis nodes were not entered.",
            severity="info",
            node_ids=unvisited[:20],
            count=len(unvisited),
        ))


def _audit_reference_integrity(snap, errors: list, warnings: list) -> None:
    attempts = {attempt.attempt_id: attempt for attempt in getattr(snap, "attempts", [])}
    observations = {obs.observation_id: obs for obs in getattr(snap, "observations", [])}
    artifacts = {artifact.artifact_id: artifact for artifact in getattr(snap, "artifacts", [])}
    conclusions = {con.conclusion_id: con for con in getattr(snap, "conclusions", [])}
    known_ids = set(attempts) | set(observations) | set(artifacts) | set(conclusions)
    capability_ids = {
        (cap.get("capability_id") or cap.get("id") or "")
        for cap in getattr(snap, "capabilities", [])
        if isinstance(cap, dict)
    }
    node_ids, allowed_by_node = _analysis_node_index(snap)

    for attempt in attempts.values():
        if attempt.parent_ids:
            missing_parent_ids = [item for item in attempt.parent_ids if item not in attempts]
            if missing_parent_ids:
                errors.append(_issue(
                    "missing_attempt_parent",
                    "Attempt references parent attempt ids that do not exist.",
                    severity="error",
                    attempt_id=attempt.attempt_id,
                    missing_parent_ids=missing_parent_ids,
                ))
        if attempt.analysis_node_id and node_ids and attempt.analysis_node_id not in node_ids:
            errors.append(_issue(
                "unknown_attempt_analysis_node",
                "Attempt is bound to an analysis node that is not in the run spec.",
                severity="error",
                attempt_id=attempt.attempt_id,
                analysis_node_id=attempt.analysis_node_id,
            ))
        if attempt.capability_ids:
            missing_caps = [cap for cap in attempt.capability_ids if cap not in capability_ids]
            if missing_caps:
                errors.append(_issue(
                    "unknown_attempt_capability",
                    "Attempt declares capability ids that are not loaded in the run.",
                    severity="error",
                    attempt_id=attempt.attempt_id,
                    capability_ids=missing_caps,
                ))
        if attempt.capability_ids and attempt.analysis_node_id and allowed_by_node:
            allowed = allowed_by_node.get(attempt.analysis_node_id, set())
            disallowed = [cap for cap in attempt.capability_ids if allowed and cap not in allowed]
            if disallowed:
                errors.append(_issue(
                    "capability_not_allowed_by_node",
                    "Attempt declares capabilities that are not allowed by its analysis node.",
                    severity="error",
                    attempt_id=attempt.attempt_id,
                    analysis_node_id=attempt.analysis_node_id,
                    capability_ids=disallowed,
                ))
        if (
            attempt.analysis_node_id
            and allowed_by_node.get(attempt.analysis_node_id)
            and not attempt.capability_ids
            and attempt.notebook_cells
        ):
            warnings.append(_issue(
                "missing_attempt_capability_declaration",
                "Executable attempt in an analysis node did not declare capability_ids.",
                severity="warning",
                attempt_id=attempt.attempt_id,
                analysis_node_id=attempt.analysis_node_id,
            ))

    for artifact in artifacts.values():
        if artifact.attempt_id and artifact.attempt_id not in attempts:
            errors.append(_issue(
                "missing_artifact_attempt",
                "Artifact references an attempt id that does not exist.",
                severity="error",
                artifact_id=artifact.artifact_id,
                attempt_id=artifact.attempt_id,
            ))
        missing_inputs = [
            item for item in list((artifact.metadata or {}).get("input_ids", []) or [])
            if item not in known_ids
        ]
        if missing_inputs:
            errors.append(_issue(
                "missing_artifact_input",
                "Artifact declares input ids that do not exist in the run graph.",
                severity="error",
                artifact_id=artifact.artifact_id,
                missing_input_ids=missing_inputs,
            ))

    for obs in observations.values():
        if obs.attempt_id and obs.attempt_id not in attempts:
            errors.append(_issue(
                "missing_observation_attempt",
                "Observation references an attempt id that does not exist.",
                severity="error",
                observation_id=obs.observation_id,
                attempt_id=obs.attempt_id,
            ))
        if obs.artifact_id and obs.artifact_id not in artifacts:
            errors.append(_issue(
                "missing_observation_artifact",
                "Observation references an artifact id that does not exist.",
                severity="error",
                observation_id=obs.observation_id,
                artifact_id=obs.artifact_id,
            ))
        missing_inputs = [item for item in obs.input_ids if item not in known_ids]
        if missing_inputs:
            errors.append(_issue(
                "missing_observation_input",
                "Observation declares input ids that do not exist in the run graph.",
                severity="error",
                observation_id=obs.observation_id,
                missing_input_ids=missing_inputs,
            ))


def _analysis_node_index(snap) -> tuple[set[str], dict[str, set[str]]]:
    spec = getattr(snap, "analysis_spec", {}) or {}
    nodes = spec.get("nodes", []) if isinstance(spec, dict) else []
    node_ids = {node.get("node_id", "") for node in nodes if node.get("node_id")}
    allowed_by_node = {
        node.get("node_id", ""): set(node.get("allowed_capabilities", []) or [])
        for node in nodes
        if node.get("node_id")
    }
    return node_ids, allowed_by_node


def _audit_capability_outputs(snap, errors: list, warnings: list) -> None:
    from pertura.capabilities import CapabilityRegistry
    from pertura.core.capability_contracts import capability_output_gaps

    registry = CapabilityRegistry(getattr(snap, "capabilities", []) or [])
    observations_by_attempt: dict[str, list] = {}
    for obs in getattr(snap, "observations", []):
        observations_by_attempt.setdefault(obs.attempt_id, []).append(obs)
    artifacts_by_attempt: dict[str, list] = {}
    for artifact in getattr(snap, "artifacts", []):
        artifacts_by_attempt.setdefault(artifact.attempt_id, []).append(artifact)
    outcome_status_by_attempt = {
        outcome.attempt_id: str(outcome.status).lower()
        for outcome in getattr(snap, "outcomes", [])
    }

    for attempt in getattr(snap, "attempts", []):
        if not attempt.capability_ids:
            continue
        attempt_observations = observations_by_attempt.get(attempt.attempt_id, [])
        attempt_artifacts = artifacts_by_attempt.get(attempt.attempt_id, [])
        has_material_output = bool(attempt_observations or attempt_artifacts)
        status = str(getattr(attempt, "status", "")).lower()
        outcome_status = outcome_status_by_attempt.get(attempt.attempt_id, "")
        committed = (
            status in {"succeeded", "success", "completed"}
            or outcome_status in {"success", "succeeded", "completed"}
            or has_material_output
        )
        if not committed:
            continue
        for capability_id in attempt.capability_ids:
            cap = registry.get(capability_id)
            if cap is None:
                continue
            gaps = capability_output_gaps(cap, attempt_observations, attempt_artifacts)
            missing_observations = gaps["missing_observations"]
            missing_artifacts = gaps["missing_artifacts"]
            if not missing_observations and not missing_artifacts:
                continue
            errors.append(_issue(
                "missing_capability_outputs",
                "Attempt declared a capability but did not register its expected outputs.",
                severity="error",
                attempt_id=attempt.attempt_id,
                analysis_node_id=attempt.analysis_node_id,
                capability_id=capability_id,
                missing_observations=missing_observations,
                missing_artifacts=missing_artifacts,
            ))


def _audit_conclusions(snap, graph: dict, errors: list, warnings: list) -> None:
    from pertura.core.evidence_chain import latest_outcomes_by_attempt, observation_evidence_status

    attempts = {attempt.attempt_id: attempt for attempt in getattr(snap, "attempts", [])}
    observations = {obs.observation_id: obs for obs in getattr(snap, "observations", [])}
    artifacts = {artifact.artifact_id: artifact for artifact in getattr(snap, "artifacts", [])}
    outcomes_by_attempt = latest_outcomes_by_attempt(getattr(snap, "outcomes", []))
    graph_nodes = {node.get("node_id") for node in graph.get("nodes", [])}
    stale = _stale_ids(snap)
    for conclusion in getattr(snap, "conclusions", []):
        support_ids = list(getattr(conclusion, "support_ids", []) or [])
        if not support_ids:
            errors.append(_issue(
                "unsupported_conclusion",
                "Conclusion has no support observation ids.",
                severity="error",
                conclusion_id=conclusion.conclusion_id,
            ))
            continue
        missing = [item for item in support_ids if item not in observations]
        if missing:
            errors.append(_issue(
                "missing_conclusion_support",
                "Conclusion references support ids that are not observations.",
                severity="error",
                conclusion_id=conclusion.conclusion_id,
                missing_support_ids=missing,
            ))
        unsupported = []
        for support_id in support_ids:
            obs = observations.get(support_id)
            if obs is None:
                continue
            status = observation_evidence_status(
                obs,
                attempts=attempts,
                artifacts=artifacts,
                observations=observations,
                outcomes_by_attempt=outcomes_by_attempt,
            )
            if not status.get("successful"):
                unsupported.append({
                    "support_id": support_id,
                    "trace_status": status.get("trace_status", ""),
                    "attempt_id": status.get("attempt_id", ""),
                    "outcome_status": status.get("outcome_status", ""),
                    "path": status.get("path", []),
                    "reason": status.get("reason", ""),
                })
        if unsupported:
            errors.append(_issue(
                "unverified_conclusion_evidence",
                "Conclusion support observations are not backed by successful executable evidence.",
                severity="error",
                conclusion_id=conclusion.conclusion_id,
                unsupported_support=unsupported[:12],
                unsupported_count=len(unsupported),
            ))
        stale_support = [item for item in support_ids if item in stale]
        if stale_support or conclusion.conclusion_id in stale:
            warnings.append(_issue(
                "stale_conclusion_evidence",
                "Conclusion depends on evidence marked stale by a finding.",
                severity="warning",
                conclusion_id=conclusion.conclusion_id,
                stale_support_ids=stale_support,
            ))
        if conclusion.conclusion_id not in graph_nodes:
            warnings.append(_issue(
                "conclusion_not_in_graph",
                "Conclusion is not present in the derivation graph projection.",
                severity="warning",
                conclusion_id=conclusion.conclusion_id,
            ))


def _audit_artifacts(snap, run_root: Path | None, errors: list, warnings: list) -> None:
    for artifact in getattr(snap, "artifacts", []):
        path = Path(artifact.path)
        if not path.is_absolute() and run_root:
            path = run_root / path
        if not path.exists():
            warnings.append(_issue(
                "missing_artifact_file",
                "Registered artifact path does not exist on disk.",
                severity="warning",
                artifact_id=artifact.artifact_id,
                path=artifact.path,
                kind=artifact.kind,
            ))


def _audit_runtime_risks(snap, errors: list, warnings: list) -> None:
    for finding in getattr(snap, "findings", []):
        if finding.finding_type in {"finish_audit_failed", "finish_audit_warning"}:
            continue
        if finding.severity in {"blocking", "error"}:
            errors.append(_issue(
                "blocking_finding",
                "Run contains a blocking/error finding.",
                severity="error",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                summary=finding.summary,
                affected_ids=finding.affected_ids,
            ))
        elif finding.severity == "high":
            warnings.append(_issue(
                "high_severity_finding",
                "Run contains a high-severity finding.",
                severity="warning",
                finding_id=finding.finding_id,
                finding_type=finding.finding_type,
                summary=finding.summary,
                affected_ids=finding.affected_ids,
            ))
    runtime = _latest_runtime_state(snap)
    for job in runtime.get("jobs", []):
        if str(job.get("status", "")).lower() in {"running", "queued"}:
            warnings.append(_issue(
                "active_job",
                "Runtime still reports an active job.",
                severity="warning",
                job_id=job.get("job_id"),
                status=job.get("status"),
            ))
    for process in runtime.get("processes", []):
        if str(process.get("status", "")).lower() in {"running", "active"}:
            warnings.append(_issue(
                "active_process",
                "Runtime still reports an active process.",
                severity="warning",
                process_id=process.get("pid") or process.get("process_id"),
                status=process.get("status"),
            ))


def _latest_runtime_state(snap) -> dict[str, Any]:
    for outcome in reversed(getattr(snap, "outcomes", [])):
        metrics = getattr(outcome, "metrics", {}) or {}
        runtime = metrics.get("runtime_state") or metrics.get("kernel_state") or {}
        if isinstance(runtime, dict):
            return runtime
    return {}


def _completed_nodes(snap) -> list[str]:
    return [
        visit.node_id
        for visit in getattr(snap, "node_visits", [])
        if visit.status == "completed"
    ]


def _skipped_nodes(snap) -> list[str]:
    return [
        visit.node_id
        for visit in getattr(snap, "node_visits", [])
        if visit.status == "skipped"
    ]


def _stale_ids(snap) -> set[str]:
    stale: set[str] = set()
    for finding in getattr(snap, "findings", []):
        if finding.finding_type == "potentially_stale_dependency":
            stale.update(item for item in finding.affected_ids if item)
    return stale


def _audit_advice(errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, str]]:
    advice = []
    codes = {item["code"] for item in [*errors, *warnings]}
    if "open_interrupt" in codes:
        advice.append({"code": "resolve_interrupts", "message": "Answer or close human interrupts before reporting."})
    if "unsupported_conclusion" in codes or "missing_conclusion_support" in codes or "unverified_conclusion_evidence" in codes:
        advice.append({"code": "repair_evidence", "message": "Register successful, traceable observations and cite them in conclusion support_ids."})
    if "stale_conclusion_evidence" in codes:
        advice.append({"code": "refresh_stale_evidence", "message": "Re-run or explicitly waive analyses affected by stale design dependencies."})
    if "missing_artifact_file" in codes:
        advice.append({"code": "repair_artifacts", "message": "Regenerate missing files or remove stale artifact registrations."})
    if "incomplete_analysis_nodes" in codes:
        advice.append({"code": "complete_or_skip_nodes", "message": "Complete remaining nodes or record explicit skips with reasons."})
    if any(code.startswith("missing_observation") or code.startswith("missing_artifact") for code in codes):
        advice.append({"code": "repair_graph_references", "message": "Fix missing attempt/artifact/input ids before finishing."})
    if "unknown_attempt_capability" in codes or "capability_not_allowed_by_node" in codes:
        advice.append({"code": "repair_capability_binding", "message": "Bind attempts to capabilities allowed by their analysis node."})
    if "missing_capability_outputs" in codes:
        advice.append({"code": "repair_capability_outputs", "message": "Register the observations/artifacts promised by each executed capability contract, or run a different capability."})
    return advice


def _audit_next_actions(errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for issue in [*errors, *warnings]:
        code = issue.get("code", "")
        details = issue.get("details", {}) or {}
        if code == "incomplete_analysis_nodes":
            for node_id in details.get("node_ids", [])[:4]:
                actions.append(_audit_action(
                    "inspect_node_contract",
                    "get_node_contract",
                    {"node_id": node_id},
                    f"Inspect completion requirements for node {node_id}.",
                    issue_code=code,
                    target_id=node_id,
                ))
        elif code == "missing_capability_outputs":
            capability_id = details.get("capability_id", "")
            attempt_id = details.get("attempt_id", "")
            if capability_id:
                actions.append(_audit_action(
                    "repair_capability_output",
                    "get_capability_template",
                    {"capability_id": capability_id},
                    f"Use the capability template to regenerate missing outputs for {capability_id}.",
                    issue_code=code,
                    target_id=attempt_id or capability_id,
                ))
            if attempt_id:
                actions.append(_audit_action(
                    "trace_attempt",
                    "trace_upstream",
                    {"node_id": attempt_id, "depth": 4},
                    f"Trace the attempt that failed its capability contract: {attempt_id}.",
                    issue_code=code,
                    target_id=attempt_id,
                ))
        elif code in {"missing_conclusion_support", "unsupported_conclusion", "unverified_conclusion_evidence", "stale_conclusion_evidence"}:
            conclusion_id = details.get("conclusion_id", "")
            if conclusion_id:
                actions.append(_audit_action(
                    "review_conclusion_evidence",
                    "review_evidence_chain",
                    {"node_id": conclusion_id, "limit": 12},
                    f"Self-audit support status for conclusion {conclusion_id}.",
                    issue_code=code,
                    target_id=conclusion_id,
                ))
                actions.append(_audit_action(
                    "trace_conclusion",
                    "trace_upstream",
                    {"node_id": conclusion_id, "depth": 5},
                    f"Trace evidence for conclusion {conclusion_id}.",
                    issue_code=code,
                    target_id=conclusion_id,
                ))
            for support in details.get("unsupported_support", [])[:4]:
                support_id = support.get("support_id", "")
                if support_id:
                    actions.append(_audit_action(
                        "review_support_observation",
                        "review_evidence_chain",
                        {"node_id": support_id, "limit": 12},
                        f"Self-audit unsupported observation {support_id}.",
                        issue_code=code,
                        target_id=support_id,
                    ))
                    actions.append(_audit_action(
                        "trace_support_observation",
                        "trace_upstream",
                        {"node_id": support_id, "depth": 4},
                        f"Trace unsupported observation {support_id}.",
                        issue_code=code,
                        target_id=support_id,
                    ))
            for support_id in details.get("stale_support_ids", [])[:4]:
                actions.append(_audit_action(
                    "review_stale_support",
                    "review_evidence_chain",
                    {"node_id": support_id, "limit": 12},
                    f"Self-audit stale support observation {support_id}.",
                    issue_code=code,
                    target_id=support_id,
                ))
                actions.append(_audit_action(
                    "trace_stale_support",
                    "trace_upstream",
                    {"node_id": support_id, "depth": 4},
                    f"Trace stale support observation {support_id}.",
                    issue_code=code,
                    target_id=support_id,
                ))
        elif code in {"missing_observation_input", "missing_observation_artifact"}:
            observation_id = details.get("observation_id", "")
            if observation_id:
                actions.append(_audit_action(
                    "review_observation_evidence",
                    "review_evidence_chain",
                    {"node_id": observation_id, "limit": 12},
                    f"Self-audit broken observation reference {observation_id}.",
                    issue_code=code,
                    target_id=observation_id,
                ))
                actions.append(_audit_action(
                    "trace_observation",
                    "trace_upstream",
                    {"node_id": observation_id, "depth": 4},
                    f"Trace broken observation reference {observation_id}.",
                    issue_code=code,
                    target_id=observation_id,
                ))
        elif code in {"missing_artifact_file", "missing_artifact_attempt", "missing_artifact_input"}:
            artifact_id = details.get("artifact_id", "")
            if artifact_id:
                actions.append(_audit_action(
                    "inspect_artifact",
                    "inspect_artifact_summary",
                    {"artifact_id": artifact_id},
                    f"Inspect registered artifact {artifact_id}.",
                    issue_code=code,
                    target_id=artifact_id,
                ))
        elif code in {"unknown_attempt_capability", "capability_not_allowed_by_node", "missing_attempt_capability_declaration"}:
            node_id = details.get("analysis_node_id", "")
            if node_id:
                actions.append(_audit_action(
                    "inspect_allowed_capabilities",
                    "list_capabilities",
                    {"node_id": node_id},
                    f"Inspect capabilities allowed in analysis node {node_id}.",
                    issue_code=code,
                    target_id=node_id,
                ))
        elif code in {"open_interrupt", "open_approval"}:
            actions.append(_audit_action(
                "resolve_human_gate",
                "get_context_review",
                {"purpose": "audit"},
                "Review open human gates before continuing.",
                issue_code=code,
                target_id=details.get("interrupt_id") or details.get("approval_id", ""),
            ))
    actions.append(_audit_action(
        "expand_full_audit",
        "audit_run",
        {},
        "Inspect the full deterministic run audit.",
        issue_code="audit_run",
        target_id="",
    ))
    return _dedupe_actions(actions)[:12]


def _audit_action(action_id: str, tool: str, args: dict[str, Any], why: str, *, issue_code: str, target_id: str = "") -> dict[str, Any]:
    return {
        "action_id": action_id,
        "tool": tool,
        "args": args,
        "why": why,
        "issue_code": issue_code,
        "target_id": target_id,
    }


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for action in actions:
        key = (
            action.get("action_id", ""),
            action.get("tool", ""),
            tuple(sorted((action.get("args") or {}).items())),
            action.get("issue_code", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out
