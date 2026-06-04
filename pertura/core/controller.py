"""Single write boundary for graph-affecting events and patch proposals."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pertura.models import Event, PatchProposal, ApprovalRequest, BehaviorRun, _model_dump
from pertura.core.behaviors import BehaviorRegistry
from pertura.core.errors import PerturaError
from pertura.core.event_schema import EventSchemaError, validate_event_payload
from pertura.core.graph import validate_graph, graph_violations_to_findings
from pertura.core.policy import PolicyEngine


class GraphMutationError(PerturaError, ValueError):
    """Raised when a proposed mutation violates harness invariants."""

    default_code = "graph.mutation_error"
    default_doc_path = "errors/graph-mutation"


class GraphController:
    def __init__(self, store, run_id: str, *, owner: str = "engine"):
        self.store = store
        self.run_id = run_id
        self.owner = owner
        self.behaviors = BehaviorRegistry()
        self.policy = PolicyEngine()

    def append_event(self, event_type: str, payload: dict, *, actor: str = "system") -> Event:
        return self.append_events([(event_type, payload, actor)])[0]

    def append_events(self, items: list[tuple[str, dict, str]]) -> list[Event]:
        events = [
            Event(
                event_id=f"evt_{uuid4().hex[:12]}",
                event_type=event_type,
                run_id=self.run_id,
                actor=actor,
                payload=payload,
            )
            for event_type, payload, actor in items
        ]
        self._validate(events)
        if not self.store.acquire_lease(self.owner, ttl_seconds=60):
            raise GraphMutationError("Run is locked by another writer.")
        try:
            self.store.append(events)
            self._run_behaviors(events)
            self._record_graph_violations()
        finally:
            self.store.release_lease(self.owner)
        return events

    def propose_patch(self, patch: PatchProposal) -> Event:
        decision = self.policy.evaluate_patch(patch)
        if decision.rejected:
            event = self.append_event("patch_proposed", {"patch": _model_dump(patch)}, actor=patch.proposed_by)
            self.reject_patch(patch.patch_id, decision.reason)
            return event
        items = [("patch_proposed", {"patch": _model_dump(patch)}, patch.proposed_by)]
        if decision.requires_approval:
            items.append(("approval_requested", {"approval": _model_dump(ApprovalRequest(
                approval_id=f"apr_{uuid4().hex[:12]}",
                subject_id=patch.patch_id,
                subject_type="patch",
                approval_type=decision.approval_type,
                reason=decision.reason,
            ))}, "policy"))
        return self.append_events(items)[0]

    def apply_patch(self, patch_id: str, event_items: list[tuple[str, dict, str]]) -> list[Event]:
        snap = self.store.read_snapshot()
        patch = next((p for p in (snap.patch_proposals if snap else []) if _patch_get(p, "patch_id") == patch_id), None)
        if patch is None:
            raise GraphMutationError(f"Unknown patch: {patch_id}")
        status = _patch_get(patch, "status")
        if status != "proposed":
            raise GraphMutationError(f"Patch is already {status}: {patch_id}")
        decision = self.policy.evaluate_patch(patch)
        if decision.requires_approval and not self.policy.approved(snap, patch_id):
            if not self.policy.open_approval(snap, patch_id):
                self.append_event("approval_requested", {"approval": _model_dump(ApprovalRequest(
                    approval_id=f"apr_{uuid4().hex[:12]}",
                    subject_id=patch_id,
                    subject_type="patch",
                    approval_type=decision.approval_type,
                    reason=decision.reason,
                ))}, actor="policy")
            raise GraphMutationError(f"Patch requires approval before apply: {patch_id}")
        if decision.rejected:
            self.reject_patch(patch_id, decision.reason)
            raise GraphMutationError(decision.reason)
        applied = self.append_events(event_items)
        self.append_event("patch_applied", {"patch_id": patch_id, "event_ids": [e.event_id for e in applied]})
        return applied

    def decide_approval(self, approval_id: str, decision: str, *, resolved_by: str = "user") -> Event:
        if decision not in {"approved", "rejected"}:
            raise GraphMutationError(f"Unknown approval decision: {decision}")
        snap = self.store.read_snapshot()
        approval = next((a for a in (snap.approvals if snap else []) if a.approval_id == approval_id), None)
        if approval is None:
            raise GraphMutationError(f"Unknown approval: {approval_id}")
        if approval.status != "open":
            raise GraphMutationError(f"Approval is already {approval.status}: {approval_id}")
        return self.append_event("approval_decided", {
            "approval_id": approval_id,
            "decision": decision,
            "resolved_by": resolved_by,
        }, actor=resolved_by)

    def reject_patch(self, patch_id: str, reason: str) -> Event:
        snap = self.store.read_snapshot()
        patch = next((p for p in (snap.patch_proposals if snap else []) if _patch_get(p, "patch_id") == patch_id), None)
        if patch is None:
            raise GraphMutationError(f"Unknown patch: {patch_id}")
        status = _patch_get(patch, "status")
        if status != "proposed":
            raise GraphMutationError(f"Patch is already {status}: {patch_id}")
        return self.append_event("patch_rejected", {"patch_id": patch_id, "reason": reason})

    def _validate(self, events: list[Event]) -> None:
        snap = self.store.read_snapshot()
        for event in events:
            try:
                validate_event_payload(event.event_type, event.payload)
            except EventSchemaError as exc:
                raise GraphMutationError(
                    str(exc),
                    code=exc.code,
                    doc_url=exc.doc_url,
                    details={"event_type": event.event_type},
                ) from exc
            payload = event.payload
            if event.event_type == "run_started":
                config_run_id = payload.get("config", {}).get("run_id", "")
                if config_run_id and config_run_id != event.run_id:
                    raise GraphMutationError(
                        f"run_started config run_id does not match event run_id: {config_run_id} != {event.run_id}"
                    )
            elif event.event_type == "attempt_planned" and snap is not None:
                attempt = payload.get("attempt", {})
                if any(a.attempt_id == attempt.get("attempt_id") for a in snap.attempts):
                    raise GraphMutationError(f"Duplicate attempt_id: {attempt.get('attempt_id')}")
                if len(snap.attempts) >= snap.budget.max_attempts:
                    raise GraphMutationError("Attempt budget exhausted.")
                branch_id = attempt.get("branch_id", snap.active_branch)
                branch = next((b for b in snap.branches if b.branch_id == branch_id), None)
                if branch is None:
                    raise GraphMutationError(f"Unknown branch_id: {branch_id}")
                if branch.status != "active":
                    raise GraphMutationError(f"Branch is not active: {branch_id}")
                node_id = attempt.get("analysis_node_id", "")
                if snap.analysis_spec and node_id and not _analysis_node_exists(snap, node_id):
                    raise GraphMutationError(f"Unknown analysis_node_id: {node_id}")
            elif event.event_type == "node_entered" and snap is not None:
                node_id = payload.get("node_id", "")
                if snap.analysis_spec and node_id and not _analysis_node_exists(snap, node_id):
                    raise GraphMutationError(f"Unknown analysis node: {node_id}")
            elif event.event_type == "branch_activated" and snap is not None:
                branch_id = payload.get("branch_id", "")
                branch = next((b for b in snap.branches if b.branch_id == branch_id), None)
                if branch is None:
                    raise GraphMutationError(f"Unknown branch_id: {branch_id}")
                if branch.status != "active":
                    raise GraphMutationError(f"Branch is not active: {branch_id}")
            elif event.event_type == "artifact_registered" and snap is not None:
                artifact = payload.get("artifact", {})
                self._validate_artifact_path(artifact.get("path", ""), snap.workspace)
            elif event.event_type == "observation_registered" and snap is not None:
                observation = payload.get("observation", {})
                attempt_id = observation.get("attempt_id", "")
                if attempt_id and not any(a.attempt_id == attempt_id for a in snap.attempts):
                    raise GraphMutationError(f"Observation references unknown attempt: {attempt_id}")

    def _validate_artifact_path(self, path: str, workspace: str) -> None:
        if not path:
            return
        resolved = Path(path).resolve()
        allowed = [self.store.run_dir.resolve()]
        if workspace:
            allowed.append(Path(workspace).resolve())
        if not any(_is_relative_to(resolved, root) for root in allowed):
            raise GraphMutationError(f"Artifact path outside workspace/run directory: {path}")

    def _record_graph_violations(self) -> None:
        graph = self.store.read_graph()
        if not graph:
            return
        violations = validate_graph(graph)
        if not violations:
            return
        events = [
            Event(
                event_id=f"evt_{uuid4().hex[:12]}",
                event_type="finding_recorded",
                run_id=self.run_id,
                actor="graph_validator",
                payload={"finding": payload},
            )
            for payload in graph_violations_to_findings(violations)
        ]
        self._validate(events)
        self.store.append(events)

    def _run_behaviors(self, events: list[Event]) -> None:
        snap = self.store.read_snapshot()
        if snap is None:
            return
        graph = self.store.read_graph()
        trigger_ids = [event.event_id for event in events]
        recorded: list[Event] = []
        for behavior in self.behaviors.behaviors:
            run_id = f"bhr_{uuid4().hex[:12]}"
            started = Event(
                event_id=f"evt_{uuid4().hex[:12]}",
                event_type="behavior_started",
                run_id=self.run_id,
                actor="behavior",
                payload={"behavior_run": _model_dump(BehaviorRun(
                    behavior_run_id=run_id,
                    behavior_id=behavior.name,
                    status="started",
                    trigger_event_ids=trigger_ids,
                ))},
            )
            try:
                emitted = behavior.run(events, snap, graph)
                output_events = [
                    Event(
                        event_id=f"evt_{uuid4().hex[:12]}",
                        event_type=event_type,
                        run_id=self.run_id,
                        actor=actor,
                        payload=payload,
                    )
                    for event_type, payload, actor in emitted
                ]
                self._validate(output_events)
                completed = Event(
                    event_id=f"evt_{uuid4().hex[:12]}",
                    event_type="behavior_completed",
                    run_id=self.run_id,
                    actor="behavior",
                    payload={
                        "behavior_run_id": run_id,
                        "output_event_ids": [event.event_id for event in output_events],
                        "output_count": len(output_events),
                    },
                )
                recorded.extend([started, *output_events, completed])
            except Exception as exc:
                recorded.extend([started, Event(
                    event_id=f"evt_{uuid4().hex[:12]}",
                    event_type="behavior_failed",
                    run_id=self.run_id,
                    actor="behavior",
                    payload={"behavior_run_id": run_id, "error": str(exc)},
                )])
        if recorded:
            self._validate(recorded)
            self.store.append(recorded)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _patch_get(patch, key: str):
    if isinstance(patch, dict):
        return patch.get(key)
    return getattr(patch, key)


def _analysis_node_exists(snap, node_id: str) -> bool:
    return any(node.get("node_id") == node_id for node in (snap.analysis_spec or {}).get("nodes", []))
