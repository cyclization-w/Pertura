from __future__ import annotations

from pertura_workflow.models import EvidenceGoal, PreflightReport, canonical_hash


def recommend_next_evidence(preflight: PreflightReport) -> list[EvidenceGoal]:
    goals: list[EvidenceGoal] = []
    for claim_type, readiness in preflight.readiness_by_claim_type.items():
        for missing in readiness.missing:
            goals.append(
                EvidenceGoal(
                    goal_id=_goal_id(claim_type, missing),
                    claim_type=claim_type,
                    missing=missing,
                    recommendation=_recommendation_for(claim_type, missing),
                    priority=_priority_for(claim_type, missing),
                )
            )
    metadata = preflight.detected_metadata
    if metadata.get("has_prediction_table") and not metadata.get("has_de_table"):
        goals.append(
            EvidenceGoal(
                goal_id=_goal_id("prediction_concordance", "compatible measured artifact"),
                claim_type="prediction_concordance",
                missing="compatible measured artifact",
                recommendation="Register a measured artifact with compatible manifest UID scope before reporting prediction-measured concordance.",
                priority="high",
            )
        )
    return _dedupe_goals(goals)


def _goal_id(claim_type: str, missing: str) -> str:
    digest = canonical_hash({"claim_type": claim_type, "missing": missing}).split(":", 1)[1][:12]
    return f"goal_{digest}"


def _recommendation_for(claim_type: str, missing: str) -> str:
    if "DesignManifest" in missing or "UID" in missing:
        return "Create or register a PerturbationDesignManifest so claims and artifacts can bind by canonical UID."
    if "guide" in missing:
        return "Provide guide assignment or guide-to-target metadata; do not rely on guide-label prose."
    if "QC" in missing or "eligibility" in missing:
        return "Register structured eligibility evidence such as target QC or cell QC before measured claims."
    if "vehicle" in missing or "control" in missing:
        return "Register a control or vehicle condition in the design manifest before treatment-vs-control claims."
    if "model provenance" in missing:
        return "Record model name, version, training/context scope, and prediction method for prediction artifacts."
    if "measured artifact" in missing:
        return "Register a compatible measured artifact; prediction or prior evidence cannot create measured strength."
    if "replicate" in missing:
        return "Register independent replicate metadata and compatible measured artifacts before replication claims."
    if "validated_mechanism" in missing or "validation" in missing or "rescue" in missing:
        return "Mechanism validation remains disabled unless future orthogonal validation artifacts are registered."
    return f"Provide structured evidence for missing field: {missing}."


def _priority_for(claim_type: str, missing: str) -> str:
    if claim_type in {"measured_de", "target_engagement"} and ("UID" in missing or "QC" in missing or "guide" in missing):
        return "high"
    if claim_type in {"mechanism", "replication"}:
        return "low"
    return "medium"


def _dedupe_goals(goals: list[EvidenceGoal]) -> list[EvidenceGoal]:
    seen: set[str] = set()
    unique: list[EvidenceGoal] = []
    for goal in goals:
        key = f"{goal.claim_type}\0{goal.missing}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(goal)
    return unique
