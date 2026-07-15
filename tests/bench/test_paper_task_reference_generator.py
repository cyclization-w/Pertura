from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[2]


def _generator():
    path = ROOT / "scripts" / "generate_paper_task_references.py"
    spec = importlib.util.spec_from_file_location("paper_task_refs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_leakage_is_rejected() -> None:
    module = _generator()
    with pytest.raises(ValueError, match="cell leakage"):
        module._require_disjoint_splits(
            [{"cell_id": "shared"}], [{"cell_id": "shared"}]
        )


def test_ref02_retention_is_hash_bound_and_filters_unresolved_cells(
    tmp_path: Path,
) -> None:
    module = _generator()
    ref02 = tmp_path / "REF-02"
    ref02.mkdir()
    retained = ref02 / "retained_cell_truth.tsv"
    retained.write_text(
        "dataset_id\tsplit\tcell_id\texpected_state\treason\n"
        "papalexi_thp1_eccite\tevaluation\tc1\t"
        "retain_for_external_label_proxy\tlabel present\n"
        "papalexi_thp1_eccite\tevaluation\tc2\t"
        "retain_for_external_label_proxy\tlabel present\n"
        "papalexi_thp1_eccite\tevaluation\tc3\t"
        "unresolved_without_assignment_truth\tlabel absent\n",
        encoding="utf-8",
    )
    (ref02 / "manifest.json").write_text(
        json.dumps(
            {
                "reference_pack_id": "REF-02",
                "readiness": "generated",
                "pending_jobs": [],
                "output_files": {
                    "retained_cell_truth.tsv": module._sha256(retained)
                },
            }
        ),
        encoding="utf-8",
    )
    selected, accounting = module._apply_ref02_retention(
        [
            {"cell_id": "c1", "is_control": "true"},
            {"cell_id": "c2", "is_control": "false"},
            {"cell_id": "c3", "is_control": "false"},
        ],
        ref02,
    )
    assert [row["cell_id"] for row in selected] == ["c1", "c2"]
    assert accounting["selected_cell_count"] == 3
    assert accounting["retained_cell_count"] == 2
    assert accounting["excluded_cell_count"] == 1
    assert accounting["retained_control_count"] == 1

    retained.write_text(retained.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash drift"):
        module._apply_ref02_retention(
            [{"cell_id": "c1", "is_control": "true"}], ref02
        )


def test_e_distance_is_deterministic_and_preserves_arm_size() -> None:
    module = _generator()
    rng = np.random.default_rng(3)
    pcs = rng.normal(size=(240, 15))
    records = []
    for replicate_index, replicate in enumerate(("rep2", "rep3")):
        start = replicate_index * 120
        for index in range(60):
            records.append({"target_uid": "T1", "replicate": replicate, "is_control": False})
        for index in range(60):
            records.append({"target_uid": "NTC", "replicate": replicate, "is_control": True})
    first = module._target_energy(
        pcs, records, "T1", n=50, rng=np.random.default_rng(module.SEED)
    )
    second = module._target_energy(
        pcs, records, "T1", n=50, rng=np.random.default_rng(module.SEED)
    )
    assert first == second
    assert 0 < first[2] <= 1


def test_protocol_validator_rejects_seed_and_permutation_unit_drift(tmp_path: Path) -> None:
    module = _generator()
    root = tmp_path / "task_refs"
    protocol = root / "PAPA-07/global_effect_protocol.json"
    evidence = root / "PAPA-07/global_effect_evidence.tsv"
    protocol.parent.mkdir(parents=True)
    protocol.write_text(
        json.dumps(
            {
                "dimensions": 15,
                "replicates": ["rep2", "rep3"],
                "permutations": 1000,
                "permutation_unit": "within_replicate_label",
                "multiple_testing": "BH across eligible targets",
                "seed": 1729,
                "claim_class_withheld_from_agent": True,
            }
        ),
        encoding="utf-8",
    )
    evidence.write_text(
        "target_uid\tFDR\tcells_per_arm\treplicates\nT1\t0.2\t50\trep2,rep3\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "pertura-paper-task-reference-pack-v1",
        "readiness": "generated",
        "pending_jobs": [],
        "problems": [],
        "passed": True,
        "input_files": {
            "ref02_manifest": "sha256:" + "1" * 64,
            "ref02_retained_cell_truth": "sha256:" + "2" * 64,
        },
        "counts": {
            "evaluation_selected_cells": 3,
            "evaluation_retained_cells": 2,
            "evaluation_excluded_cells": 1,
            "retained_controls": 1,
        },
        "parameters": {
            "retained_cell_policy": "REF-02 expected_state starts with retain_"
        },
        "output_files": {
            "PAPA-07/global_effect_protocol.json": module._sha256(protocol),
            "PAPA-07/global_effect_evidence.tsv": module._sha256(evidence),
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert module.validate_task_reference_pack(root)["passed"] is True

    payload = json.loads(protocol.read_text())
    payload["seed"] = 7
    payload["permutation_unit"] = "across_replicates"
    protocol.write_text(json.dumps(payload), encoding="utf-8")
    result = module.validate_task_reference_pack(root)
    assert result["passed"] is False
    assert any("seed" in problem for problem in result["problems"])
    assert any("permutation_unit" in problem for problem in result["problems"])

    payload = json.loads((root / "manifest.json").read_text())
    payload["passed"] = False
    payload["problems"] = ["planted generation failure"]
    (root / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    result = module.validate_task_reference_pack(root)
    assert result["passed"] is False
    assert any("generation checks" in problem for problem in result["problems"])
