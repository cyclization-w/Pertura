from pertura_workflow.method_router import assess_virtual_prediction_scope, route_analysis


def test_router_prefers_pseudobulk_with_replicates() -> None:
    route = route_analysis({"moi": "low", "n_replicates": 3, "controls_defined": True, "guide_assignment_validated": True})
    assert route["status"] == "supported"
    assert route["primary_method"] == "pseudobulk_de"


def test_router_blocks_unvalidated_high_moi_assignment() -> None:
    route = route_analysis({"moi": "high", "controls_defined": True, "guide_assignment_validated": False, "guide_counts_available": False})
    assert route["status"] == "blocked"
    assert route["primary_method"] == "sceptre_style_conditional_association"
    assert any("assignment" in reason for reason in route["blockers"])


def test_virtual_scope_rejects_combo_and_cross_context_extrapolation() -> None:
    result = assess_virtual_prediction_scope(
        {"perturbation_seen": False, "cell_context_seen": False, "is_combination": True},
        {"contains_combinations": False, "supports_cross_context": False},
    )
    assert result["status"] == "out_of_scope"
    assert result["task_class"] == "combo_prediction"
    assert "linear perturbation baseline" in result["required_baselines"]
    assert "perturbation discriminability" in result["required_metrics"]
