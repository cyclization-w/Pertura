from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path


DATASET_IDS = (
    "replogle_k562_essential_2022",
    "papalexi_thp1_eccite",
    "norman_k562_crispra_2019",
)


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(root: Path) -> tuple[Path, Path]:
    datasets = root / "datasets.json"
    datasets.write_text(
        json.dumps(
            {
                "datasets": {
                    dataset_id: {"artifact_path": f"/frozen/{dataset_id}.h5ad"}
                    for dataset_id in DATASET_IDS
                }
            }
        ),
        encoding="utf-8",
    )
    ref01 = root / "REF-01"
    ref01.mkdir()
    (ref01 / "manifest.json").write_text(
        json.dumps(
            {
                "reference_pack_id": "REF-01",
                "readiness": "generated",
                "pending_jobs": [],
            }
        ),
        encoding="utf-8",
    )
    (ref01 / "dataset_profiles.json").write_text(
        json.dumps(
            {
                "datasets": {
                    dataset_id: {
                        "artifact_sha256": "sha256:" + str(index + 1) * 64,
                        "shape": [1000 + index, 500 + index],
                    }
                    for index, dataset_id in enumerate(DATASET_IDS)
                }
            }
        ),
        encoding="utf-8",
    )
    return datasets, ref01


def test_ref07_is_deterministic_compact_and_recovers_modules(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    datasets, ref01 = _inputs(tmp_path)
    output = tmp_path / "REF-07"
    command = [
        sys.executable,
        str(repo / "scripts" / "generate_paper_ref07.py"),
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
    assert manifest["completed_jobs"] == ["REF-07-A", "REF-07-B", "REF-07-C"]
    assert manifest["pending_jobs"] == []
    assert manifest["counts"] == {
        "perturbations": 12,
        "effect_genes": 48,
        "gmt_modules": 4,
        "nmf_cells": 360,
        "nmf_control_cells": 240,
        "nmf_genes": 80,
        "nmf_rank_seed_fits": 9,
    }
    assert manifest["metrics"]["module_recovery"] >= 0.70
    assert manifest["metrics"]["component_matching"] >= 0.80
    assert manifest["metrics"]["cross_seed_stability"] >= 0.80
    assert manifest["metrics"]["control_identity_match"] == 1.0
    assert manifest["metrics"]["leakage_count"] == 0
    assert (output / "planted_module_fixture.h5ad").stat().st_size < 5_000_000

    with (output / "effect_matrix_reference.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        effects = list(csv.DictReader(handle, delimiter="\t"))
    missing = [row for row in effects if row["observed"] == "false"]
    observed_zero = [
        row
        for row in effects
        if row["observed"] == "true" and row["effect"] not in ("", "0")
    ]
    assert len(effects) == 12 * 48
    assert missing
    assert all(row["effect"] == "" for row in missing)
    assert observed_zero

    gmt = json.loads((output / "gmt_reference.json").read_text(encoding="utf-8"))
    assert gmt["valid_module_count"] == 4
    assert gmt["fixtures"]["duplicate_module.gmt"].startswith("blocked")
    assert gmt["fixtures"]["empty_module.gmt"].startswith("blocked")

    with (output / "control_nmf_truth.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        controls = list(csv.DictReader(handle, delimiter="\t"))
    assert sum(row["expected_in_control_fit"] == "true" for row in controls) == 240
    assert sum(row["expected_in_control_fit"] == "false" for row in controls) == 120


def test_ref07_does_not_import_pertura_results() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "scripts" / "generate_paper_ref07.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert "import pertura_" not in script
    assert "expression matrices are not read by REF-07" in script
    assert "not promoted to measured biological facts" in script
