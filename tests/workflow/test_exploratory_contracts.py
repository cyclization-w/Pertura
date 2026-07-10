from __future__ import annotations

import json
from pathlib import Path

import pytest

from pertura_core import DependencyRef, ScientificStatement, ScopeKey, SourceClass
from pertura_gate.promotion import decide_promotion
from pertura_runtime.claude.tools.product_tools import PRODUCT_TOOL_NAMES
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.exploratory import (
    ExploratoryPredictionEnvelope,
    ExploratoryResponseProgramContract,
    ExploratoryVirtualSplitContract,
    audit_virtual_leakage,
)


def test_exploratory_split_detects_all_planted_reference_leakage() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "exploratory"
    split = ExploratoryVirtualSplitContract.model_validate_json((fixture / "virtual_split_clean.json").read_text(encoding="utf-8"))
    leaky = json.loads((fixture / "leaky_inputs.json").read_text(encoding="utf-8"))
    audit = audit_virtual_leakage(
        split,
        state_reference_training_ids=tuple(leaky["state_reference_training_ids"]),
        module_reference_training_ids=tuple(leaky["module_reference_training_ids"]),
    )
    assert audit.status == "blocked"
    assert len(audit.reasons) == 2
    clean = audit_virtual_leakage(split, state_reference_training_ids=("d1",), module_reference_training_ids=("KLF1",))
    assert clean.status == "clear"


def test_virtual_split_rejects_overlapping_partitions() -> None:
    with pytest.raises(ValueError, match="overlapping"):
        ExploratoryVirtualSplitContract(
            dataset_id="d",
            axes={"perturbation": {"train": ("KLF1",), "validation": (), "test": ("KLF1",)}},
        )


def test_response_program_requires_committed_effect_dependency() -> None:
    with pytest.raises(ValueError, match="committed effect"):
        ExploratoryResponseProgramContract(
            effect_result_id="result_effect", effect_result_hash="sha256:" + "1" * 64,
            effect_matrix_hash="sha256:" + "2" * 64, learning_scope=ScopeKey(dataset_id="d"),
            algorithm="nmf", program_ids=("program_1",), dependencies=(),
        )


def test_prediction_stays_prediction_and_cannot_promote_to_measured() -> None:
    prediction = ExploratoryPredictionEnvelope(
        model_id="m", model_version="1", split_id="s", split_hash="sha256:" + "1" * 64,
        prediction_hash="sha256:" + "2" * 64, prediction_unit="gene", prediction_scale="log1p",
    )
    assert prediction.source_class == SourceClass.prediction
    statement = ScientificStatement(
        run_id="r", text="Predicted effect", source_class=SourceClass.prediction,
        scope=ScopeKey(dataset_id="d"), requested_strength="measured_association",
    )
    decision = decide_promotion(
        statement, results=(), receipts=(), capability_specs=(), authoritative_public_key=""
    )
    assert decision.status == "downgraded"
    assert decision.max_strength == "prediction"


def test_exploratory_contracts_do_not_change_product_surface() -> None:
    assert len(PRODUCT_TOOL_NAMES) == 5
    assert len(CapabilityRegistry.load_default(include_external=False).list()) == 29
