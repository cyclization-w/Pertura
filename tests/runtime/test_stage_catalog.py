from __future__ import annotations

from pathlib import Path

import pytest

from pertura_runtime.stages import (
    StageCatalogError,
    available_stage_ids,
    build_stage_prompt_section,
    load_stage_card,
    load_stage_contract,
    load_stage_index,
)
from pertura_runtime.stages.turn_final import TurnFinal


STAGE_DOC_ROOT = Path(__file__).resolve().parents[2] / "docs" / "stages"


REQUIRED_STAGES = [
    "preflight",
    "experiment_design",
    "perturbation_design_manifest",
    "guide_assignment",
    "cell_qc",
    "target_qc",
    "cell_state_reference",
    "measured_de",
    "target_engagement",
    "curated_enrichment",
    "module_effect",
    "global_effect",
    "prediction_artifact",
    "claim_report",
]


def test_stage_catalog_index_has_cards_and_contracts() -> None:
    index = load_stage_index()
    ids = available_stage_ids()
    assert ids == REQUIRED_STAGES
    assert index["catalog_name"] == "Evidence-Aware Stage Catalog"

    for stage_id in ids:
        contract = load_stage_contract(stage_id)
        card = load_stage_card(stage_id)
        assert contract["stage_id"] == stage_id
        for key in ["stage_role", "failure_modes", "turn_final_surface_type", "next_stage_recommendations"]:
            assert key in contract
        assert card.startswith("# ")


def test_cell_state_reference_contract_is_context_only() -> None:
    contract = load_stage_contract("cell_state_reference")
    assert contract["stage_role"] == "scope_definition"
    assert contract["evidence_role"] == "state_context"
    assert contract["evidence_producing"] is False
    assert contract["turn_final_surface_type"] == "evidence_summary"
    assert "measured perturbation effect" in contract["must_not_support"]
    assert "validated mechanism" in contract["must_not_support"]
    assert "mcp__pertura_evidence__register_cell_state_reference_artifact" in contract["allowed_mcp_tools"]




def test_measured_de_contract_is_effect_evidence_handoff_only() -> None:
    contract = load_stage_contract("measured_de")
    assert contract["stage_role"] == "effect_evidence"
    assert contract["evidence_producing"] is True
    assert contract["turn_final_surface_type"] == "evidence_summary"
    assert any("measured_association" in item for item in contract["can_support"])
    assert "validated_mechanism" in contract["must_not_support"]
    assert "claim_report" in contract["next_stage_recommendations"]
    assert "mcp__pertura_evidence__register_measured_de_artifact" in contract["allowed_mcp_tools"]


def test_claim_report_contract_is_the_claim_decision_surface() -> None:
    contract = load_stage_contract("claim_report")
    assert contract["stage_role"] == "synthesis"
    assert contract["evidence_producing"] is False
    assert contract["turn_final_surface_type"] == "claim_decision_surface"
    assert "free_prose_scientific_surface" in contract["must_not_support"]
    assert "mcp__pertura_evidence__evaluate_claims" in contract["allowed_mcp_tools"]
    assert "mcp__pertura_evidence__render_evidence_report" in contract["allowed_mcp_tools"]

def test_only_claim_report_defaults_to_claim_decision_surface() -> None:
    for stage_id in available_stage_ids():
        contract = load_stage_contract(stage_id)
        if stage_id == "claim_report":
            assert contract["turn_final_surface_type"] == "claim_decision_surface"
        else:
            assert contract["turn_final_surface_type"] != "claim_decision_surface"


def test_runtime_stage_prompt_loads_only_selected_stage_card() -> None:
    text = build_stage_prompt_section("cell_state_reference")
    assert "Stage id: `cell_state_reference`" in text
    assert "# Cell State Reference" in text
    assert "register_cell_state_reference_artifact" in text
    assert "# Claim Report" not in text
    assert "# Measured De" not in text


def test_unknown_stage_id_lists_available_stages() -> None:
    with pytest.raises(StageCatalogError) as excinfo:
        build_stage_prompt_section("not_a_stage")
    text = str(excinfo.value)
    assert "unknown stage id" in text
    assert "cell_state_reference" in text


def test_turn_final_skeleton_validates_stage_lifecycle_values() -> None:
    final = TurnFinal(
        stage_id="cell_state_reference",
        status="completed",
        surface_type="evidence_summary",
        generated_files=["outputs/state_reference_summary.json"],
        registered_artifacts=["cell_state_reference_abc"],
        recommended_next_stages=["measured_de"],
    )
    assert final.to_dict()["surface_type"] == "evidence_summary"
    with pytest.raises(ValueError):
        TurnFinal(stage_id="x", status="done")

def test_stage_prompt_includes_language_and_encoding_rule() -> None:
    text = build_stage_prompt_section("cell_state_reference")
    assert "Write all stage outputs" in text
    assert "English" in text
    assert "ASCII punctuation" in text


def test_stage_catalog_files_are_ascii_safe() -> None:
    paths = [STAGE_DOC_ROOT / "index.yaml"]
    paths.extend((STAGE_DOC_ROOT / "contracts").glob("*.yaml"))
    paths.extend((STAGE_DOC_ROOT / "cards").glob("*.md"))
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
        try:
            text.encode("ascii")
        except UnicodeEncodeError as exc:
            raise AssertionError(f"stage catalog file is not ASCII-safe: {path}") from exc


def test_measured_de_prompt_keeps_claim_report_as_handoff() -> None:
    text = build_stage_prompt_section("measured_de")
    assert "Stage id: `measured_de`" in text
    assert "# Measured DE" in text
    assert "candidate claims are handoff material" in text.lower()
    assert "mcp__pertura_evidence__evaluate_claims" not in text
    assert "# Claim Report" not in text