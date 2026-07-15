from __future__ import annotations

import csv
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np


DATASET_ID = "norman_k562_crispra_2019"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_selection(path: Path, split: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["cell_id", "group_id", "unit_id", "is_control"],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for index in range(30):
            writer.writerow(
                {
                    "cell_id": f"{split}_cell_{index:03d}",
                    "group_id": f"{split}_construct_{index:03d}",
                    "unit_id": f"{split}_unit_{index:03d}",
                    "is_control": "false",
                }
            )
        writer.writerow(
            {
                "cell_id": f"{split}_control_cell",
                "group_id": f"{split}_control",
                "unit_id": f"{split}_control_unit",
                "is_control": "true",
            }
        )


def _splits(root: Path) -> Path:
    paper = root / "paper-v1"
    protocol = paper / "protocol"
    selections = protocol / "cell-selections"
    protocol.mkdir(parents=True)
    payload = {"datasets": {DATASET_ID: {}}}
    for split in ("calibration", "evaluation"):
        relative = Path("protocol") / "cell-selections" / f"{split}.tsv.gz"
        path = paper / relative
        _write_selection(path, split)
        payload["datasets"][DATASET_ID][split] = {
            "split": split,
            "split_id": f"split_{split}",
            "cell_selection_path": relative.as_posix(),
            "cell_selection_file_sha256": _sha256(path),
        }
    path = protocol / "splits.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _read_metrics(path: Path) -> dict[str, dict[str, float | None]]:
    output: dict[str, dict[str, float | None]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            value = None if row["value"] == "not_available" else float(row["value"])
            output.setdefault(row["bundle_id"], {})[row["metric_id"]] = value
    return output


def test_ref10_is_deterministic_compact_and_detects_controlled_attacks(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    splits = _splits(tmp_path)
    output = tmp_path / "paper-v1" / "references" / "REF-10"
    command = [
        sys.executable,
        str(repo / "scripts" / "generate_paper_ref10.py"),
        "--splits",
        str(splits),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    first_hash = _sha256(output / "manifest.json")
    first_bundle_hash = _sha256(
        output
        / "controlled_prediction_bundles"
        / "clean_uncertainty"
        / "prediction_bundle.npz"
    )
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    assert _sha256(output / "manifest.json") == first_hash
    assert (
        _sha256(
            output
            / "controlled_prediction_bundles"
            / "clean_uncertainty"
            / "prediction_bundle.npz"
        )
        == first_bundle_hash
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_jobs"] == ["REF-10-A", "REF-10-B"]
    assert manifest["pending_jobs"] == []
    assert manifest["counts"]["controlled_prediction_bundles"] == 9
    assert manifest["counts"]["train_units"] == 24
    assert manifest["counts"]["evaluation_units"] == 24
    assert manifest["counts"]["features"] == 128
    assert manifest["counts"]["real_norman_expression_values_loaded"] == 0
    assert manifest["counts"]["trained_models"] == 0
    assert manifest["metrics"]["target_overlap_count"] == 0
    assert manifest["metrics"]["construct_overlap_count"] == 0
    assert manifest["metrics"]["cell_overlap_count"] == 0
    assert manifest["metrics"]["leakage_recall"] == 1.0
    assert manifest["metrics"]["leakage_precision"] == 1.0
    assert manifest["metrics"]["orientation_detection"] == 1.0
    assert manifest["metrics"]["duplicate_detection"] == 1.0
    assert manifest["metrics"]["source_class_accuracy"] == 1.0

    truth = json.loads(
        (output / "prediction_bundle_truth.json").read_text(encoding="utf-8")
    )
    assert truth["source_class"] == "prediction"
    assert truth["split_contract"]["overlap_counts"] == {
        "cell": 0,
        "construct": 0,
        "target": 0,
    }
    records = {row["bundle_id"]: row for row in truth["bundles"]}
    assert set(records) == {
        "clean",
        "clean_uncertainty",
        "leaky_target",
        "leaky_cell",
        "transposed",
        "duplicate_identity",
        "direction_flipped",
        "no_change",
        "training_mean",
    }
    assert records["leaky_target"]["expected_leakage"] == "blocked"
    assert records["leaky_cell"]["expected_leakage"] == "blocked"
    assert records["transposed"]["expected_ingest"] == "blocked"
    assert records["duplicate_identity"]["expected_ingest"] == "blocked"
    assert all(row["source_class"] == "prediction" for row in records.values())

    clean_path = (
        output
        / "controlled_prediction_bundles"
        / "clean_uncertainty"
        / "prediction_bundle.npz"
    )
    clean = np.load(clean_path, allow_pickle=False)
    assert clean["predictions"].shape == (48, 128)
    assert clean["observed"].shape == (48, 128)
    assert clean["standard_error"].shape == (48, 128)
    assert clean["lower"].shape == (48, 128)
    assert clean["upper"].shape == (48, 128)
    transposed = np.load(
        output
        / "controlled_prediction_bundles"
        / "transposed"
        / "prediction_bundle.npz",
        allow_pickle=False,
    )
    assert transposed["predictions"].shape == (128, 48)
    assert transposed["observed"].shape == (48, 128)
    duplicate = np.load(
        output
        / "controlled_prediction_bundles"
        / "duplicate_identity"
        / "prediction_bundle.npz",
        allow_pickle=False,
    )
    assert duplicate["row_ids"][0] == duplicate["row_ids"][1]

    metrics = _read_metrics(output / "virtual_metric_reference.tsv")
    clean_metrics = metrics["clean_uncertainty"]
    assert clean_metrics["effect_rmse"] < metrics["no_change"]["effect_rmse"]
    assert clean_metrics["effect_rmse"] < metrics["training_mean"]["effect_rmse"]
    assert clean_metrics["direction_accuracy"] > 0.90
    assert clean_metrics["spearman"] > 0.95
    assert clean_metrics["discriminability"] > 0.90
    assert clean_metrics["baseline_improvement"] > 0.80
    assert 0.85 <= clean_metrics["coverage"] <= 0.95
    assert clean_metrics["expected_calibration_error"] < 0.05
    assert metrics["clean"]["coverage"] is None
    assert metrics["direction_flipped"]["direction_accuracy"] < 0.10

    with (output / "virtual_baseline_reference.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        baselines = list(csv.DictReader(handle, delimiter="\t"))
    assert len(baselines) == 2 * 24 * 128
    assert {row["baseline_id"] for row in baselines} == {
        "no_change",
        "training_mean",
    }
    assert {row["evaluation_contact_count"] for row in baselines} == {"0"}

    total_size = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    assert total_size < 5 * 1024 * 1024


def test_ref10_is_protocol_only_and_does_not_import_product_results() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "scripts" / "generate_paper_ref10.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert "import pertura_" not in script
    assert "anndata" not in script
    assert "does not validate real Norman virtual-model performance" in script
    assert "real Norman expression matrix is not read" in script
    assert "All model outputs remain in the prediction source class" in script
    assert "P5 remains supplemental and optional" in script
