"""Product-facing event projection for the perturb-seq workbench."""

from __future__ import annotations

from typing import Any

from pertura.models import Event, _model_dump


class ProductEventCompiler:
    """Compile raw event-store records into user-facing live-run events."""

    EVENT_MAP = {
        "goal_recorded": "planning",
        "node_entered": "planning",
        "node_transition_requested": "planning",
        "attempt_planned": "running_code",
        "execution_output": "execution_output",
        "outcome_recorded": "result_recorded",
        "artifact_registered": "artifact_ready",
        "observation_registered": "observation_recorded",
        "interrupt_opened": "question_opened",
        "patch_proposed": "repair_proposed",
        "patch_applied": "repair_applied",
        "branch_opened": "branch_started",
        "branch_activated": "branch_started",
        "finding_recorded": "blocked",
        "run_complete": "complete",
        "job_submitted": "running_code",
        "job_completed": "result_recorded",
    }

    def compile(self, events: list[Event], *, max_items: int = 30) -> list[dict[str, Any]]:
        out = []
        for event in reversed(events or []):
            kind = self.EVENT_MAP.get(event.event_type)
            if not kind:
                continue
            payload = event.payload or {}
            out.append({
                "event_id": event.event_id,
                "event_type": event.event_type,
                "product_type": kind,
                "timestamp": str(event.timestamp),
                "title": self._title(kind, payload),
                "summary": self._summary(event.event_type, payload),
            })
            if len(out) >= max_items:
                break
        return list(reversed(out))

    def _title(self, kind: str, payload: dict[str, Any]) -> str:
        if kind == "planning":
            return payload.get("reason") or payload.get("node_id") or "Planning"
        if kind == "running_code":
            attempt = payload.get("attempt") or {}
            return attempt.get("title") or payload.get("job_type") or "Running code"
        if kind == "artifact_ready":
            artifact = payload.get("artifact") or {}
            return artifact.get("summary") or artifact.get("kind") or "Artifact ready"
        if kind == "observation_recorded":
            obs = payload.get("observation") or {}
            return f"{obs.get('target', 'observation')} {obs.get('metric', '')}".strip()
        if kind == "repair_proposed":
            patch = payload.get("patch") or {}
            return patch.get("rationale") or "Repair proposed"
        if kind == "complete":
            return "Analysis complete"
        return kind.replace("_", " ")

    def _summary(self, event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "execution_output":
            output = payload.get("output") or payload
            return str(output.get("stderr") or output.get("stdout") or output)[:500]
        if event_type == "outcome_recorded":
            outcome = payload.get("outcome") or {}
            return outcome.get("summary") or outcome.get("status") or ""
        if event_type == "finding_recorded":
            finding = payload.get("finding") or {}
            return finding.get("summary") or ""
        return str(_model_dump(payload))[:500] if payload else ""


def compile_product_timeline(events: list[Event], *, max_items: int = 30) -> list[dict[str, Any]]:
    """Compatibility wrapper used by API/UI callers."""
    return ProductEventCompiler().compile(events, max_items=max_items)
