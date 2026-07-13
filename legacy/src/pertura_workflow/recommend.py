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
                    recommendation=_recommendation_for(claim_type, missing, preflight),
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


def _recommendation_for(claim_type: str, missing: str, preflight: PreflightReport) -> str:
    metadata = preflight.detected_metadata
    if missing == "perturbation or condition column":
        return "Identify the obs column that encodes perturbation, guide, condition, or treatment identity before building a design manifest."
    if missing == "negative-control or vehicle labels":
        column = _first_column(metadata.get("candidate_perturbation_columns")) or _first_column(metadata.get("candidate_condition_columns"))
        if column:
            return f"Confirm or add negative-control/vehicle labels for obs.{column} before measured contrasts."
        return "Define negative-control, vehicle, untreated, or NTC labels before measured contrasts."
    if missing == "guide-to-target map":
        column = _first_column(metadata.get("candidate_guide_columns"))
        if column:
            return f"Provide or infer guide-to-target mapping for obs.{column} before target-level claims."
        return "Provide a guide-to-target map before guide assignment or target-level measured claims."
    if missing == "registered DesignManifest UID scope" or "DesignManifest" in missing or "UID" in missing:
        column = _first_column(metadata.get("candidate_perturbation_columns"))
        if column:
            return f"Build and register a PerturbationDesignManifest from obs.{column}; preflight candidates are not evidence."
        return "Create or register a PerturbationDesignManifest so claims and artifacts can bind by canonical UID."
    if missing == "registered guide assignment" or "guide assignment" in missing:
        column = _first_column(metadata.get("candidate_guide_columns"))
        if column:
            return f"Register structured guide assignment from obs.{column} after validating assignment rules and guide-to-target mapping."
        return "Provide guide assignment or guide-to-target metadata; do not rely on guide-label prose."
    if missing == "registered target/cell QC eligibility" or "QC" in missing or "eligibility" in missing:
        return "Register structured target QC or cell QC before measured claims; preflight QC columns are diagnostic only."
    if missing == "registered QC policy and thresholds":
        return "Register cell QC with explicit thresholds and pass/fail policy; boolean-only QC cannot raise strength."
    if missing == "count or normalized expression layer":
        return "Provide a declared counts or normalized expression layer before running measured-effect wrappers."
    if missing == "MOI risk review":
        return "Review MOI or multi-guide assignment before measured claims; preflight detected possible high-MOI or unvalidated guide assignment."
    if missing == "batch-perturbation confounding review":
        return "Review the batch x perturbation crosstab before measured claims; preflight detected possible nesting or confounding."
    if missing == "target expression or effect metadata":
        return "Register target engagement or perturbation-efficiency evidence; preflight does not compute target response."
    if missing == "cell-state reference" or missing == "registered state reference artifact":
        return "Run and register the cell_state_reference stage before composition, module, or state-stratified effects."
    if missing == "donor/sample replicate metadata":
        return "Add donor, sample, replicate, lane, or library metadata before aggregate replication or strict measured claims."
    if missing == "model provenance":
        return "Record model name, version, training/context scope, and prediction method for prediction artifacts."
    if missing == "compatible measured artifact" or "measured artifact" in missing:
        return "Register a compatible measured artifact; prediction or prior evidence cannot create measured strength."
    if "vehicle" in missing or "control" in missing:
        return "Register a control or vehicle condition in the design manifest before treatment-vs-control claims."
    if "replicate" in missing:
        return "Register independent replicate metadata and compatible measured artifacts before replication claims."
    if "validated_mechanism" in missing or "validation" in missing or "rescue" in missing:
        return "Mechanism validation remains disabled unless future orthogonal validation artifacts are registered."
    return f"Provide structured evidence for missing field: {missing}."


def _first_column(items: object) -> str | None:
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if isinstance(first, dict):
        column = first.get("column")
        if column:
            return str(column)
    return None


def _priority_for(claim_type: str, missing: str) -> str:
    high_missing = (
        "UID",
        "DesignManifest",
        "guide-to-target",
        "negative-control",
        "vehicle",
        "QC",
        "count or normalized",
        "measured artifact",
    )
    if claim_type in {"measured_de", "target_engagement", "composition_effect", "global_effect", "module_effect"} and any(token in missing for token in high_missing):
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
