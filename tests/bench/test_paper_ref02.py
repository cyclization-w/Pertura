from __future__ import annotations

import csv
import gzip
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_selection(path: Path, rows: list[tuple[str, str, str, bool]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["cell_id", "group_id", "unit_id", "is_control"])
        writer.writerows(rows)


def _write_guide_assets(root: Path) -> None:
    matrix = root / "guide_matrix"
    matrix.mkdir(parents=True)
    (matrix / "matrix.mtx").write_text(
        "%%MatrixMarket matrix coordinate integer general\n"
        "% fixture\n"
        "3 6 9\n"
        "1 1 20\n"
        "2 1 1\n"
        "2 2 22\n"
        "3 3 25\n"
        "1 4 18\n"
        "2 4 2\n"
        "2 5 19\n"
        "3 5 1\n"
        "3 6 21\n",
        encoding="ascii",
    )
    (matrix / "barcodes.tsv").write_text(
        "p1\np2\np3\np4\np5\np6\n", encoding="utf-8"
    )
    (matrix / "features.tsv").write_text(
        "gA\tgA\tCRISPR Guide Capture\n"
        "gB\tgB\tCRISPR Guide Capture\n"
        "gC\tgC\tCRISPR Guide Capture\n",
        encoding="utf-8",
    )
    (root / "rna_barcodes.tsv").write_text(
        "cell_id\np1\np2\np3\np4\np5\np6\n", encoding="utf-8"
    )
    (root / "guide_map.tsv").write_text(
        "guide\ttarget\tmapping_source\n"
        "gA\tA\tobserved_assignment\n"
        "gB\tB\tobserved_assignment\n"
        "gC\tC\tobserved_assignment\n",
        encoding="utf-8",
    )
    (root / "cell_metadata.tsv").write_text(
        "cell_id\tguide_ID\tgene\n"
        "p1\tgA\tA\n"
        "p2\tgB\tB\n"
        "p3\tgC\tC\n"
        "p4\tgA\tA\n"
        "p5\tgB\tB\n"
        "p6\tgC\tC\n",
        encoding="utf-8",
    )
    relative_files = [
        "guide_matrix/matrix.mtx",
        "guide_matrix/barcodes.tsv",
        "guide_matrix/features.tsv",
        "rna_barcodes.tsv",
        "guide_map.tsv",
        "cell_metadata.tsv",
    ]
    manifest = {
        "schema_version": "pertura-papalexi-guide-assets-v1",
        "dataset_id": "papalexi_thp1_eccite",
        "dimensions": {"cells": 6, "guides": 3, "nonzero_guide_counts": 9},
        "files": {name: _sha256(root / name) for name in relative_files},
    }
    (root / "guide_assets_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_ref02_generates_real_proxy_planted_truth_and_design_invariants(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    paper = tmp_path / "paper-v1"
    protocol = paper / "protocol"
    selections = protocol / "cell-selections"
    protocol.mkdir(parents=True)

    dataset_cells = {
        "replogle_k562_essential_2022": (["r1"], ["r2"]),
        "papalexi_thp1_eccite": (["p1", "p2", "p3"], ["p4", "p5", "p6"]),
        "norman_k562_crispra_2019": (["n1"], ["n2"]),
    }
    split_payload = {"datasets": {}}
    for dataset_id, (calibration, evaluation) in dataset_cells.items():
        split_payload["datasets"][dataset_id] = {}
        for split, cells in (
            ("calibration", calibration),
            ("evaluation", evaluation),
        ):
            relative = Path("protocol") / "cell-selections" / f"{dataset_id}.{split}.tsv.gz"
            path = paper / relative
            _write_selection(
                path,
                [
                    (cell, f"group-{cell}", f"unit-{cell}", index == 0)
                    for index, cell in enumerate(cells)
                ],
            )
            split_payload["datasets"][dataset_id][split] = {
                "cell_selection_path": relative.as_posix(),
                "cell_selection_file_sha256": _sha256(path),
            }
    splits = protocol / "splits.json"
    splits.write_text(json.dumps(split_payload), encoding="utf-8")
    datasets = protocol / "datasets.json"
    datasets.write_text(
        json.dumps(
            {
                "datasets": {
                    dataset_id: {"artifact_path": f"/{dataset_id}.h5ad"}
                    for dataset_id in dataset_cells
                }
            }
        ),
        encoding="utf-8",
    )
    guide_assets = tmp_path / "guide-assets"
    _write_guide_assets(guide_assets)
    output = paper / "references" / "REF-02"

    command = [
        sys.executable,
        str(root / "scripts" / "generate_paper_ref02.py"),
        "--datasets",
        str(datasets),
        "--splits",
        str(splits),
        "--papalexi-guide-assets",
        str(guide_assets),
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
    assert manifest["completed_jobs"] == ["REF-02-A", "REF-02-B", "REF-02-C"]
    assert manifest["pending_jobs"] == []
    assert manifest["readiness"] == "generated"
    assert manifest["counts"] == {
        "real_proxy_rows": 6,
        "planted_assignment_rows": 18,
        "screen_qc_rows": 10,
        "retained_truth_rows": 10,
    }
    assert set(manifest["output_files"]) == {
        "papalexi_guide_reconciliation.json",
        "papalexi_assignment_proxy.tsv",
        "guide_count_fixture",
        "guide_assignment_truth.tsv",
        "screen_qc_truth.tsv",
        "retained_cell_truth.tsv",
    }
    reconciliation = json.loads(
        (output / "papalexi_guide_reconciliation.json").read_text(encoding="utf-8")
    )
    assert reconciliation["barcode_alignment_rate"] == 1.0
    assert reconciliation["mapping_conflict_count"] == 0
    assert reconciliation["external_label_top_count_match_rate"] == 1.0

    with (output / "guide_assignment_truth.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        truth = list(csv.DictReader(handle, delimiter="\t"))
    assert Counter(row["assignment_class"] for row in truth) == Counter(
        {
            "true_positive_singlet": 12,
            "true_positive_multiguide": 2,
            "uncertain": 2,
            "no_guide": 2,
        }
    )


def test_ref02_rejects_guide_asset_hash_drift(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    guide_assets = tmp_path / "guide-assets"
    _write_guide_assets(guide_assets)
    (guide_assets / "guide_map.tsv").write_text("drift\n", encoding="utf-8")
    datasets = tmp_path / "datasets.json"
    splits = tmp_path / "splits.json"
    datasets.write_text(
        json.dumps({"datasets": {dataset_id: {} for dataset_id in (
            "replogle_k562_essential_2022",
            "papalexi_thp1_eccite",
            "norman_k562_crispra_2019",
        )}}),
        encoding="utf-8",
    )
    splits.write_text(json.dumps({"datasets": {}}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate_paper_ref02.py"),
            "--datasets", str(datasets),
            "--splits", str(splits),
            "--papalexi-guide-assets", str(guide_assets),
            "--output", str(tmp_path / "output"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
