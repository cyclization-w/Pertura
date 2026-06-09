"""Perturb-seq branch and sweep projection."""

from __future__ import annotations

from typing import Any

from pertura.models import Snapshot, _model_dump


BRANCHABLE_BY_CAPABILITY = {
    "assign_guides": ["guide_count_threshold", "multi_guide_policy"],
    "compare_thresholds": ["guide_count_threshold", "target_coverage_threshold"],
    "check_target_coverage": ["min_cells_per_target", "min_cells_per_guide"],
    "run_de": ["method", "covariates", "filtering_threshold", "contrast"],
    "compare_methods": ["method", "covariates"],
    "composition_test": ["method", "cell_state_column"],
}


def compile_branch_board(snap: Snapshot | None, *, active_capability_id: str = "") -> dict[str, Any]:
    if snap is None:
        return {"branches": [], "sweep_candidates": [], "promotion_required": False}
    branches = []
    for branch in getattr(snap, "branches", []) or []:
        observations = [
            obs.observation_id for obs in getattr(snap, "observations", []) or []
            if obs.branch_id == branch.branch_id
        ]
        artifacts = [
            art.artifact_id for art in getattr(snap, "artifacts", []) or []
            if any(att.attempt_id == art.attempt_id and att.branch_id == branch.branch_id for att in getattr(snap, "attempts", []) or [])
        ]
        item = _model_dump(branch)
        item.update({
            "observation_count": len(observations),
            "artifact_count": len(artifacts),
            "promotion_required": branch.branch_id != "main" and branch.status == "active",
        })
        branches.append(item)
    sweep_candidates = [
        {"capability_id": active_capability_id, "parameter": param, "risk_level": "medium"}
        for param in BRANCHABLE_BY_CAPABILITY.get(active_capability_id, [])
    ]
    return {
        "branches": branches,
        "sweep_candidates": sweep_candidates,
        "promotion_required": any(item.get("promotion_required") for item in branches),
    }
