from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STAGE_RESULT_STATUSES = {
    "completed",
    "partial",
    "blocked",
    "skipped",
    "exploratory_only",
    "no_evidence_registered",
    "failed",
}

TURN_FINAL_SURFACE_TYPES = {
    "progress_only",
    "evidence_summary",
    "claim_decision_surface",
}


@dataclass(frozen=True)
class TurnFinal:
    stage_id: str
    status: str
    surface_type: str = "progress_only"
    what_was_done: list[str] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    registered_artifacts: list[str] = field(default_factory=list)
    claim_decisions: list[str] = field(default_factory=list)
    blocked_or_downgraded_reasons: list[str] = field(default_factory=list)
    recommended_next_stages: list[str] = field(default_factory=list)
    report_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STAGE_RESULT_STATUSES:
            raise ValueError(f"unknown stage status: {self.status}")
        if self.surface_type not in TURN_FINAL_SURFACE_TYPES:
            raise ValueError(f"unknown turn final surface type: {self.surface_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "status": self.status,
            "surface_type": self.surface_type,
            "what_was_done": list(self.what_was_done),
            "generated_files": list(self.generated_files),
            "registered_artifacts": list(self.registered_artifacts),
            "claim_decisions": list(self.claim_decisions),
            "blocked_or_downgraded_reasons": list(self.blocked_or_downgraded_reasons),
            "recommended_next_stages": list(self.recommended_next_stages),
            "report_path": self.report_path,
            "metadata": dict(self.metadata),
        }