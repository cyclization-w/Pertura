from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy

from pertura_core import CapabilityRunRequest, ScopeKey
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.executors import execute_capability
from pertura_workflow.intake import contract_with_confirmations, inspect_dataset_path


def _write_fixture(root: Path) -> None:
    root.mkdir()
    cells = []
    metadata = []
    conditions = ("target", "baseline", "ntc_a", "ntc_b")
    for replicate in ("r1", "r2", "r3"):
        for condition in conditions:
            for index in range(4):
                cell = f"{replicate}_{condition}_{index}"
                cells.append(cell)
                metadata.append((cell, condition, replicate))
    with (root / "counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene", *cells])
        writer.writerow(["G1", *(12 if "target" in cell else 3 for cell in cells)])
        writer.writerow(["G2", *(5 for _ in cells)])
        writer.writerow(["G3", *(2 if "ntc_a" in cell else 3 for cell in cells)])
    with (root / "metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_id", "condition", "replicate"])
        writer.writerows(metadata)


def test_calibration_permutes_replicate_labels_never_cells(tmp_path: Path) -> None:
    source = tmp_path / "screen"
    _write_fixture(source)
    contract = contract_with_confirmations(
        inspect_dataset_path(source),
        {"control": ["baseline", "ntc_a", "ntc_b"], "replicate": "replicate"},
    )
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "calibration.replicate_null.v1"
    )
    request = CapabilityRunRequest(
        run_id="legacy-wrapper-regression",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters={
            "counts_path": "counts.csv",
            "metadata_path": "metadata.csv",
            "target_condition": "target",
            "baseline_condition": "baseline",
            "negative_control_conditions": ["ntc_a", "ntc_b"],
            "permutations": 50,
        },
    )
    staging = tmp_path / "calibration-output"
    staging.mkdir()
    result = execute_capability(spec, request, contract, staging)

    assert result.status.value == "completed"
    payload = json.loads((staging / result.output_paths[0]).read_text(encoding="utf-8"))
    assert payload["label_permutation"]["permutation_unit"] == "replicate_label"
    assert payload["cell_label_permutation_performed"] is False

\n