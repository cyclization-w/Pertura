from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class HarvestMode(str, Enum):
    candidate_only = "candidate_only"
    auto_register_strict = "auto_register_strict"
    interactive_confirm = "interactive_confirm"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(payload: Any) -> str:
    text = json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True)
class DetectedFile:
    path: str
    relative_path: str
    suffix: str
    file_kind: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "suffix": self.suffix,
            "file_kind": self.file_kind,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class EvidenceCandidate:
    candidate_id: str
    source_path: str
    relative_path: str
    candidate_kind: str
    artifact_subtype: str | None = None
    suggested_registrar: str | None = None
    uid_linked: bool = False
    validator_passed: bool = False
    ambiguous: bool = False
    confidence: float | None = None
    unresolved_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_path": self.source_path,
            "relative_path": self.relative_path,
            "candidate_kind": self.candidate_kind,
            "artifact_subtype": self.artifact_subtype,
            "suggested_registrar": self.suggested_registrar,
            "uid_linked": self.uid_linked,
            "validator_passed": self.validator_passed,
            "ambiguous": self.ambiguous,
            "confidence": self.confidence,
            "unresolved_fields": list(self.unresolved_fields),
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReadinessEntry:
    claim_type: str
    status: str
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "missing": list(self.missing),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class PreflightReport:
    workspace: str
    mode: str = "benchmark"
    detected_files: list[DetectedFile] = field(default_factory=list)
    detected_metadata: dict[str, Any] = field(default_factory=dict)
    candidate_artifacts: list[EvidenceCandidate] = field(default_factory=list)
    readiness_by_claim_type: dict[str, ReadinessEntry] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "mode": self.mode,
            "detected_files": [item.to_dict() for item in self.detected_files],
            "detected_metadata": dict(self.detected_metadata),
            "candidate_artifacts": [item.to_dict() for item in self.candidate_artifacts],
            "readiness_by_claim_type": {
                key: value.to_dict() for key, value in self.readiness_by_claim_type.items()
            },
            "created_at_utc": self.created_at_utc,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Pertura Preflight Report",
            "",
            f"- Workspace: `{self.workspace}`",
            f"- Mode: `{self.mode}`",
            f"- Files detected: `{len(self.detected_files)}`",
            f"- Candidate artifacts: `{len(self.candidate_artifacts)}`",
            "",
            "## Detected Metadata",
            "",
        ]
        for key, value in sorted(self.detected_metadata.items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.extend(["", "## Readiness", ""])
        for claim_type, readiness in self.readiness_by_claim_type.items():
            missing = ", ".join(readiness.missing) if readiness.missing else "none"
            lines.append(f"- `{claim_type}`: `{readiness.status}`; missing: {missing}")
        lines.extend(["", "## Candidate Artifacts", ""])
        if not self.candidate_artifacts:
            lines.append("No candidate artifacts detected.")
        for candidate in self.candidate_artifacts:
            unresolved = ", ".join(candidate.unresolved_fields) if candidate.unresolved_fields else "none"
            lines.append(
                f"- `{candidate.candidate_id}` `{candidate.candidate_kind}` from "
                f"`{candidate.relative_path}`; unresolved: {unresolved}"
            )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class HarvestReport:
    workspace: str
    mode: HarvestMode
    candidates: list[EvidenceCandidate] = field(default_factory=list)
    registered_artifact_ids: list[str] = field(default_factory=list)
    registry_path: str | None = None
    reasons: list[str] = field(default_factory=list)
    created_at_utc: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "mode": self.mode.value,
            "candidates": [item.to_dict() for item in self.candidates],
            "registered_artifact_ids": list(self.registered_artifact_ids),
            "registry_path": self.registry_path,
            "reasons": list(self.reasons),
            "created_at_utc": self.created_at_utc,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Pertura Harvest Report",
            "",
            f"- Workspace: `{self.workspace}`",
            f"- Mode: `{self.mode.value}`",
            f"- Candidates: `{len(self.candidates)}`",
            f"- Registered artifacts: `{len(self.registered_artifact_ids)}`",
            "",
        ]
        if self.reasons:
            lines.append("## Notes")
            lines.append("")
            for reason in self.reasons:
                lines.append(f"- {reason}")
        lines.append("## Candidates")
        lines.append("")
        if not self.candidates:
            lines.append("No candidates detected.")
        for candidate in self.candidates:
            lines.append(f"- `{candidate.candidate_id}` `{candidate.candidate_kind}` from `{candidate.relative_path}`")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class EvidenceGoal:
    goal_id: str
    claim_type: str
    missing: str
    recommendation: str
    priority: str = "medium"
    provenance: str = "workflow_recommendation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "claim_type": self.claim_type,
            "missing": self.missing,
            "recommendation": self.recommendation,
            "priority": self.priority,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class WorkflowRunStep:
    name: str
    status: str
    artifact_ids: list[str] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "artifact_ids": list(self.artifact_ids),
            "output_paths": list(self.output_paths),
            "errors": list(self.errors),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class WorkflowRunManifest:
    workflow_run_id: str
    command: str
    workspace: str
    mode: str
    policy_hash: str
    inputs: dict[str, Any] = field(default_factory=dict)
    steps: list[WorkflowRunStep] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at_utc: str = field(default_factory=utc_now)

    @property
    def workflow_run_hash(self) -> str:
        return canonical_hash(self._canonical_payload())

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "command": self.command,
            "workspace": self.workspace,
            "mode": self.mode,
            "policy_hash": self.policy_hash,
            "inputs": dict(self.inputs),
            "steps": [step.to_dict() for step in self.steps],
            "output_paths": list(self.output_paths),
            "errors": list(self.errors),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._canonical_payload()
        payload["workflow_run_hash"] = self.workflow_run_hash
        payload["created_at_utc"] = self.created_at_utc
        return payload


@dataclass(frozen=True)
class WorkflowStateManifest:
    workspace: str
    registry_path: str | None = None
    candidate_count: int = 0
    artifact_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    report_paths: list[str] = field(default_factory=list)
    updated_at_utc: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "registry_path": self.registry_path,
            "candidate_count": self.candidate_count,
            "artifact_ids": list(self.artifact_ids),
            "decision_ids": list(self.decision_ids),
            "report_paths": list(self.report_paths),
            "updated_at_utc": self.updated_at_utc,
        }

@dataclass(frozen=True)
class RecipeRunResult:
    recipe_name: str
    workspace: str
    mode: str
    preflight: PreflightReport
    harvest: HarvestReport
    evidence_goals: list[EvidenceGoal] = field(default_factory=list)
    candidate_claims: list[dict[str, Any]] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    report_markdown: str = ""
    workflow_run_manifest: WorkflowRunManifest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_name": self.recipe_name,
            "workspace": self.workspace,
            "mode": self.mode,
            "preflight": self.preflight.to_dict(),
            "harvest": self.harvest.to_dict(),
            "evidence_goals": [goal.to_dict() for goal in self.evidence_goals],
            "candidate_claims": list(self.candidate_claims),
            "decision_ids": list(self.decision_ids),
            "report_markdown": self.report_markdown,
            "workflow_run_manifest": self.workflow_run_manifest.to_dict() if self.workflow_run_manifest else None,
        }

def candidate_id_for(relative_path: str, candidate_kind: str) -> str:
    digest = hashlib.sha256(f"{candidate_kind}\n{relative_path}".encode("utf-8")).hexdigest()[:12]
    return f"cand_{candidate_kind}_{digest}"
