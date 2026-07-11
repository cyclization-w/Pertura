from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy

from pertura_core import CapabilityRunRequest, ScopeKey
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.intake import contract_with_confirmations, inspect_dataset_path


def _write_fixture(root: Path, *, control_detected: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "expression.csv").open("w", newline="", encoding="utf-8") as expression, (root / "metadata.csv").open("w", newline="", encoding="utf-8") as metadata:
        expr = csv.writer(expression)
        meta = csv.writer(metadata)
        expr.writerow(["cell_id", "KLF1", "SIG1", "SIG2"])
        meta.writerow(["cell_id", "perturbation_uid", "guide", "batch", "replicate", "mixscape_class"])
        for replicate in ("r1", "r2", "r3"):
            for index in range(12):
                cell = f"t_{replicate}_{index}"
                expr.writerow([cell, 1, 1, 2])
                meta.writerow([cell, "target:KLF1", f"g{index % 3 + 1}", "b1", replicate, "KO"])
            for index in range(12):
                cell = f"c_{replicate}_{index}"
                expr.writerow([cell, 8 if control_detected else 0, 4, 4])
                meta.writerow([cell, "control:NTC", "NTC", "b1", replicate, "NT"])


def _run_wrapper(
    source: Path,
    staging: Path,
    *,
    confirmations: dict | None = None,
    parameters: dict,
):
    contract = inspect_dataset_path(source)
    if confirmations:
        contract = contract_with_confirmations(contract, confirmations)
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "target.reliability.v2"
    )
    request = CapabilityRunRequest(
        run_id="legacy-wrapper-regression",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters=parameters,
    )
    staging.mkdir()
    return execute_capability(spec, request, contract, staging)


def test_target_reliability_v2_reports_bootstrap_guides_loo_and_responder(tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source)
    staging = tmp_path / "target-output"
    result = _run_wrapper(
        source,
        staging,
        confirmations={
            "control": "control:NTC",
            "target": "target:KLF1",
            "replicate": "replicate",
        },
        parameters={
            "expression_path": "expression.csv",
            "metadata_path": "metadata.csv",
            "target_uid": "target:KLF1",
            "control_uid": "control:NTC",
            "target_gene": "KLF1",
            "mixscape_class_column": "mixscape_class",
            "signature_genes": ["SIG1", "SIG2"],
            "bootstrap_iterations": 100,
        },
    )
    assert result.status.value == "caution"
    assert result.capability_trust.value == "builtin_trusted"
    assert any("not benchmark-validated" in item for item in result.cautions)
    assert (staging / result.output_paths[0]).exists()

def test_low_detectability_without_signature_blocks_target_reliability(tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source, control_detected=False)
    result = _run_wrapper(
        source,
        tmp_path / "target-low-output",
        parameters={
            "expression_path": "expression.csv",
            "metadata_path": "metadata.csv",
            "target_uid": "target:KLF1",
            "control_uid": "control:NTC",
            "target_gene": "KLF1",
            "bootstrap_iterations": 20,
        },
    )
    assert result.status.value == "blocked"
    assert any("detectability" in blocker for blocker in result.blockers)
