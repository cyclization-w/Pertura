from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path


DATASET_ID = "norman_k562_crispra_2019"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(root: Path) -> tuple[Path, Path]:
    datasets = root / "datasets.json"
    datasets.write_text(
        json.dumps(
            {
                "datasets": {
                    DATASET_ID: {
                        "artifact_path": "/frozen/norman.h5ad",
                        "auxiliary_assets": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    ref01 = root / "REF-01"
    ref01.mkdir()
    profiles = {
        "schema_version": "pertura-paper-ref01-v1",
        "reference_pack_id": "REF-01",
        "generator_job_id": "REF-01-A",
        "datasets": {
            DATASET_ID: {
                "artifact_sha256": "sha256:" + "a" * 64,
                "shape": [27658, 2000],
                "obs_columns": ["guide_identity", "guide_merged", "gene_program"],
                "layers": {"counts": {"shape": [27658, 2000]}},
                "obsm_keys": [],
            }
        },
    }
    (ref01 / "dataset_profiles.json").write_text(
        json.dumps(profiles), encoding="utf-8"
    )
    (ref01 / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "pertura-paper-ref01-v1",
                "reference_pack_id": "REF-01",
                "completed_jobs": ["REF-01-A", "REF-01-B"],
                "pending_jobs": [],
                "readiness": "generated",
            }
        ),
        encoding="utf-8",
    )
    return datasets, ref01


def test_ref06_is_deterministic_calibrated_and_correctly_refuses_norman(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    datasets, ref01 = _inputs(tmp_path)
    output = tmp_path / "REF-06"
    command = [
        sys.executable,
        str(repo / "scripts" / "generate_paper_ref06.py"),
        "--datasets",
        str(datasets),
        "--ref01",
        str(ref01),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    first_hash = _sha256(output / "manifest.json")
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    assert _sha256(output / "manifest.json") == first_hash

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_jobs"] == ["REF-06-A", "REF-06-B"]
    assert manifest["pending_jobs"] == []
    assert manifest["counts"] == {
        "synthetic_cells": 1200,
        "synthetic_targets": 10,
        "synthetic_guides": 20,
        "synthetic_response_genes": 60,
        "tested_pairs": 100,
        "positive_pairs": 20,
        "null_pairs": 80,
        "norman_cell_by_guide_count_assets": 0,
    }
    assert manifest["metrics"]["type_i_error"] <= 0.10
    assert manifest["metrics"]["power"] >= 0.90
    assert manifest["metrics"]["fdr"] <= 0.10
    assert manifest["metrics"]["effect_rank_concordance"] >= 0.90
    assert manifest["metrics"]["correct_refusal"] is True
    assert manifest["metrics"]["silent_fallback_count"] == 0

    suitability = json.loads(
        (output / "norman_sceptre_suitability.json").read_text(encoding="utf-8")
    )
    assert suitability["suitable_for_sceptre"] is False
    assert suitability["correct_refusal"] is True
    assert suitability["expected_outcome"] == "blocked"
    assert suitability["missing_inputs"] == ["cell_by_guide_counts"]
    assert suitability["observed_guide_label_columns"] == [
        "guide_identity",
        "guide_merged",
    ]

    with (output / "sceptre_synthetic_truth.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        truth = list(csv.DictReader(handle, delimiter="\t"))
    assert sum(row["is_positive"] == "true" for row in truth) == 20
    assert {row["expected_direction"] for row in truth} == {"up", "down", "null"}

    fixture = json.loads(
        (output / "sceptre_fixture" / "fixture_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert 2.0 <= fixture["high_moi"]["mean_targets_per_cell"] <= 4.0
    assert fixture["dimensions"]["discovery_pairs"] == 100


def test_ref06_does_not_import_pertura_results() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "scripts" / "generate_paper_ref06.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert "import pertura_" not in script
    assert "cell-by-guide count observations" in script
    assert "No real-data SCEPTRE performance claim is made." in script
