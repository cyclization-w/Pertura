from __future__ import annotations

from typing import Any


def route_analysis(design: dict[str, Any], *, objective: str = "measured_effect") -> dict[str, Any]:
    """Route explicit design facts to a conservative analysis family.

    This is a decision aid, not an executor or claim gate. Missing design facts
    remain blockers rather than being guessed from filenames or prose.
    """

    objective = _norm(objective)
    moi = _norm(design.get("moi"))
    n_replicates = _int(design.get("n_replicates"))
    controls = bool(design.get("controls_defined"))
    assignment = bool(design.get("guide_assignment_validated"))
    has_guide_counts = bool(design.get("guide_counts_available"))
    state_reference = bool(design.get("state_reference_available"))
    blockers: list[str] = []
    cautions: list[str] = []
    checks = ["negative-control calibration", "effect size alongside significance", "batch/donor sensitivity"]

    if not controls:
        blockers.append("control definition is missing")
    if objective in {"measured_effect", "target_efficacy", "guide_assignment"} and not assignment:
        blockers.append("guide/perturbation assignment is not validated")

    if objective == "guide_assignment":
        primary = "guide_umi_mixture_assignment" if has_guide_counts else "recover_guide_count_matrix"
        primary_capability = "guide.assignment.nb_mixture.v1" if has_guide_counts else None
        if not has_guide_counts:
            blockers.append("guide UMI/count matrix is unavailable")
        checks = ["ambient guide background", "barcode overlap", "MOI distribution", "guide-to-target mapping"]
    elif objective == "target_efficacy":
        primary = "target_reliability_audit_then_mixscape_or_mixscale"
        primary_capability = "target.reliability.aggregate.v1"
        checks = ["target-gene detectability", "expected direction", "escape-cell fraction", "guide concordance"]
    elif objective == "composition_effect":
        primary = "replicate_aware_compositional_model"
        primary_capability = "composition.propeller.v1"
        if not state_reference:
            blockers.append("cell-state reference is missing")
        if n_replicates < 2:
            cautions.append("fewer than two replicate units; compositional inference is exploratory")
        checks = ["state-reference stability", "replicate-level counts", "batch/state confounding", "global cell-yield shifts"]
    elif objective == "virtual_prediction":
        return assess_virtual_prediction_scope(design.get("request") or {}, design.get("training_scope") or {})
    elif objective == "measured_effect":
        if moi in {"high", "multi", "multiple", "pooled_high"}:
            primary = "sceptre_style_conditional_association"
            primary_capability = "association.sceptre.v1"
            if not has_guide_counts:
                blockers.append("high-MOI association needs observed guide counts or validated treatment assignment")
            checks.extend(["guide-count covariate", "conditional estimand", "null p-value calibration"])
        elif n_replicates >= 2:
            primary = "pseudobulk_de"
            primary_capability = "de.pseudobulk.edger.v1"
            checks.extend(["replicate overlap across contrast arms", "count-layer suitability"])
        else:
            primary = "exploratory_cell_level_effect"
            primary_capability = None
            cautions.append("no replicate-aware route is available; avoid population-level confirmatory claims")
    else:
        raise ValueError(f"unknown analysis objective: {objective}")

    return {
        "schema_version": "pertura-method-route-v1",
        "objective": objective,
        "status": "blocked" if blockers else ("caution" if cautions else "supported"),
        "primary_method": primary,
        "capability_id": primary_capability,
        "blockers": blockers,
        "cautions": cautions,
        "required_checks": list(dict.fromkeys(checks)),
        "design_facts_used": {
            "moi": moi or None,
            "n_replicates": n_replicates,
            "controls_defined": controls,
            "guide_assignment_validated": assignment,
            "guide_counts_available": has_guide_counts,
            "state_reference_available": state_reference,
        },
    }


def assess_virtual_prediction_scope(request: dict[str, Any], training_scope: dict[str, Any]) -> dict[str, Any]:
    perturbation_seen = bool(request.get("perturbation_seen"))
    context_seen = bool(request.get("cell_context_seen"))
    is_combo = bool(request.get("is_combination"))
    blockers: list[str] = []
    cautions: list[str] = []
    if is_combo and not bool(training_scope.get("contains_combinations")):
        blockers.append("combination prediction requested without combination training support")
    if not context_seen and not bool(training_scope.get("supports_cross_context")):
        blockers.append("new cellular context is outside the declared model scope")
    if not perturbation_seen:
        cautions.append("unseen perturbation requires a perturbation-held-out evaluation split")
    if not context_seen:
        cautions.append("unseen context requires a context-held-out evaluation split")
    task = (
        "combo_prediction" if is_combo else
        "unseen_perturbation_unseen_context" if not perturbation_seen and not context_seen else
        "unseen_perturbation_seen_context" if not perturbation_seen else
        "seen_perturbation_unseen_context" if not context_seen else
        "in_distribution_reconstruction"
    )
    return {
        "schema_version": "pertura-virtual-scope-v1",
        "objective": "virtual_prediction",
        "task_class": task,
        "status": "out_of_scope" if blockers else ("caution" if cautions else "supported"),
        "blockers": blockers,
        "cautions": cautions,
        "required_baselines": ["control mean", "context mean", "linear perturbation baseline"],
        "required_metrics": ["DE direction recall", "perturbation discriminability", "transposed rank", "MAE"],
        "anti_collapse_checks": ["prediction variance across perturbations", "mean-baseline win rate", "effect-vector rank heatmap"],
    }


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
