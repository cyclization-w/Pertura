from __future__ import annotations

import json
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
    "control_calibration",
    "cell_state_reference",
    "composition_effect",
    "measured_de",
    "target_engagement",
    "curated_enrichment",
    "module_effect",
    "global_effect",
    "prediction_artifact",
    "virtual_perturbation_prediction",
    "prediction_measured_concordance",
    "virtual_cell_state_transition",
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


def test_control_calibration_contract_is_eligibility_only() -> None:
    contract = load_stage_contract("control_calibration")
    assert contract["stage_role"] == "analysis_eligibility"
    assert contract["evidence_role"] == "control_calibration"
    assert contract["evidence_producing"] is False
    assert contract["turn_final_surface_type"] == "evidence_summary"
    assert "measured_perturbation_effect" in contract["must_not_support"]
    assert "validated_mechanism" in contract["must_not_support"]
    assert "mcp__pertura_evidence__register_control_calibration_artifact" in contract["allowed_mcp_tools"]


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

def test_stage_extension_interface_docs_and_templates_exist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    extension_doc = repo_root / "docs" / "extensions" / "extension_interface.md"
    contract_template = STAGE_DOC_ROOT / "templates" / "contract_template.yaml"
    card_template = STAGE_DOC_ROOT / "templates" / "card_template.md"

    assert extension_doc.exists()
    assert contract_template.exists()
    assert card_template.exists()

    contract = json.loads(contract_template.read_text(encoding="utf-8"))
    for key in [
        "stage_id",
        "stage_role",
        "evidence_role",
        "evidence_producing",
        "turn_final_surface_type",
        "allowed_mcp_tools",
        "required_outputs",
        "can_support",
        "must_not_support",
        "failure_modes",
        "next_stage_recommendations",
        "benchmark_expectations",
    ]:
        assert key in contract
    assert "validated_mechanism" in contract["must_not_support"]

    card = card_template.read_text(encoding="utf-8")
    assert "This stage card guides exploration" in card
    assert "not a scientific conclusion surface" in card
    assert "Candidate claims are handoff material only" in card


def test_extension_interface_documents_boundary_invariants() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / "docs" / "extensions" / "extension_interface.md").read_text(encoding="utf-8")

    for phrase in [
        "Stage Extension",
        "Evidence Extension",
        "Runtime Extension",
        "Benchmark Extension",
        "Every user-visible scientific conclusion must pass through ClaimDecision",
        "They are not Claude-facing MCP tools in P2.1",
        "Do not expose abstract family registrars as MCP tools",
    ]:
        assert phrase in text


def test_extension_docs_are_ascii_safe() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    paths = [
        repo_root / "docs" / "extensions" / "extension_interface.md",
        STAGE_DOC_ROOT / "templates" / "contract_template.yaml",
        STAGE_DOC_ROOT / "templates" / "card_template.md",
    ]
    for path in paths:
        path.read_text(encoding="utf-8").encode("ascii")


def test_composition_effect_contract_is_measured_composition_handoff() -> None:
    contract = load_stage_contract("composition_effect")
    assert contract["stage_role"] == "effect_evidence"
    assert contract["evidence_role"] == "cell_state_composition_association"
    assert contract["evidence_producing"] is True
    assert contract["turn_final_surface_type"] == "evidence_summary"
    assert "measured_cell_state_composition_association" in contract["can_support"]
    assert "causal_fate_conversion" in contract["must_not_support"]
    assert "gene_specific_differential_expression" in contract["must_not_support"]
    assert "mcp__pertura_evidence__register_composition_effect_artifact" in contract["allowed_mcp_tools"]
    assert "claim_report" in contract["next_stage_recommendations"]



def test_virtual_perturbation_stage_contracts_are_prediction_only() -> None:
    prediction = load_stage_contract("virtual_perturbation_prediction")
    assert prediction["stage_role"] == "prediction_evidence"
    assert prediction["evidence_role"] == "predicted_perturbation_response"
    assert prediction["turn_final_surface_type"] == "evidence_summary"
    assert "predicted_effect" in prediction["can_support"]
    assert "measured_association" in prediction["must_not_support"]
    assert "mcp__pertura_evidence__register_virtual_perturbation_prediction_artifact" in prediction["allowed_mcp_tools"]

    concordance = load_stage_contract("prediction_measured_concordance")
    assert concordance["evidence_role"] == "prediction_measured_concordance"
    assert "validated_mechanism" in concordance["must_not_support"]
    assert "mcp__pertura_evidence__register_prediction_measured_concordance_artifact" in concordance["allowed_mcp_tools"]

    transition = load_stage_contract("virtual_cell_state_transition")
    assert transition["evidence_role"] == "predicted_cell_state_transition"
    assert "causal_fate_conversion" in transition["must_not_support"]
    assert "mcp__pertura_evidence__register_virtual_cell_state_transition_artifact" in transition["allowed_mcp_tools"]
