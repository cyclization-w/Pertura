from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy

from pertura_core import CapabilityRunRequest, ScopeKey
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.intake import contract_with_confirmations, inspect_dataset_path


def _write_fixture(root: Path, *, collision: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rna = ["AAAA-1", "AAAC-1", "AACA-1", "AACC-1", "ACAA-1", "ACAC-1", "ACCA-1", "ACCC-1"]
    if collision:
        rna[1] = "AAAA-2"
    (root / "rna_barcodes.csv").write_text("barcode\n" + "\n".join(rna) + "\n", encoding="utf-8")
    with (root / "guide_counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["barcode", "g1", "g2"])
        for index, barcode in enumerate(rna):
            writer.writerow([barcode, 12 if index < 4 else 0, 0 if index < 4 else 11])
    (root / "guide_map.csv").write_text("guide,target\ng1,KLF1\ng2,NTC\n", encoding="utf-8")
    with (root / "raw_guide_counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["barcode", "g1", "g2"])
        for barcode in rna:
            writer.writerow([barcode, 1, 1])
        writer.writerow(["GGGG-1", 1, 0])
        writer.writerow(["GGGA-1", 0, 1])


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
        "diagnostic.guide_assignment.v1"
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


def test_guide_assignment_detects_mixture_ambient_moi_and_publishes_manifest(tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source)
    staging = tmp_path / "guide-output"
    result = _run_wrapper(
        source,
        staging,
        confirmations={
            "control": "NTC",
            "guide": "guide_counts.csv",
            "target": "guide_map.csv",
        },
        parameters={
            "guide_counts_path": "guide_counts.csv",
            "rna_barcodes_path": "rna_barcodes.csv",
            "guide_map_path": "guide_map.csv",
            "raw_guide_counts_path": "raw_guide_counts.csv",
            "design_moi": "low",
        },
    )
    assert result.status.value in {"screen_passed", "caution"}
    assert result.capability_trust.value == "builtin_trusted"
    assert len(result.output_paths) == 3
    assert all((staging / path).exists() for path in result.output_paths)

def test_barcode_suffix_collision_blocks_assignment(tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source, collision=True)
    result = _run_wrapper(
        source,
        tmp_path / "collision-output",
        parameters={
            "guide_counts_path": "guide_counts.csv",
            "rna_barcodes_path": "rna_barcodes.csv",
            "guide_map_path": "guide_map.csv",
        },
    )
    assert result.status.value == "blocked"
    assert any("collisions" in blocker for blocker in result.blockers)
