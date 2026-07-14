from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref02-v1"
DATASETS = (
    "replogle_k562_essential_2022",
    "papalexi_thp1_eccite",
    "norman_k562_crispra_2019",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        with item.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_one_column(path: Path, *, header: bool = False) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    if header and rows:
        rows = rows[1:]
    return [str(row[0]) for row in rows if row]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _write_tsv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _validate_guide_assets(root: Path) -> tuple[dict[str, Any], str]:
    manifest_path = root / "guide_assets_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != "pertura-papalexi-guide-assets-v1"
        or manifest.get("dataset_id") != "papalexi_thp1_eccite"
    ):
        raise ValueError("unsupported Papalexi guide asset manifest")
    for relative, expected_hash in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or _sha256(path) != expected_hash:
            raise ValueError(f"Papalexi guide asset hash drift: {relative}")
    return manifest, _sha256(manifest_path)


def _load_split_rows(splits_path: Path) -> dict[str, dict[str, list[dict[str, str]]]]:
    payload = json.loads(splits_path.read_text(encoding="utf-8"))
    paper_root = splits_path.resolve().parent.parent
    result: dict[str, dict[str, list[dict[str, str]]]] = {}
    for dataset_id in DATASETS:
        entry = payload["datasets"][dataset_id]
        result[dataset_id] = {}
        seen: set[str] = set()
        for split in ("calibration", "evaluation"):
            record = entry[split]
            path = paper_root / record["cell_selection_path"]
            if not path.is_file() or _sha256(path) != record["cell_selection_file_sha256"]:
                raise ValueError(f"split selection hash drift: {dataset_id}/{split}")
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                rows = [dict(row) for row in csv.DictReader(handle, delimiter="\t")]
            ids = {row["cell_id"] for row in rows}
            if seen & ids:
                raise ValueError(f"split cell overlap: {dataset_id}")
            seen.update(ids)
            result[dataset_id][split] = rows
    return result


def _split_lookup(
    split_rows: dict[str, dict[str, list[dict[str, str]]]], dataset_id: str
) -> dict[str, tuple[str, dict[str, str]]]:
    return {
        row["cell_id"]: (split, row)
        for split, rows in split_rows[dataset_id].items()
        for row in rows
    }


def _generate_ref02a(
    guide_root: Path,
    split_rows: dict[str, dict[str, list[dict[str, str]]]],
    output_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import numpy as np
    from scipy.io import mmread

    guide_manifest, guide_manifest_hash = _validate_guide_assets(guide_root)
    barcodes = _read_one_column(guide_root / "guide_matrix" / "barcodes.tsv")
    rna_barcodes = _read_one_column(guide_root / "rna_barcodes.tsv", header=True)
    features = _read_one_column(guide_root / "guide_matrix" / "features.tsv")
    metadata_rows = _read_tsv(guide_root / "cell_metadata.tsv")
    guide_map_rows = _read_tsv(guide_root / "guide_map.tsv")
    metadata = {row["cell_id"]: row for row in metadata_rows}
    guide_map = {row["guide"]: row["target"] for row in guide_map_rows}
    mapping_conflicts = len(guide_map_rows) - len(guide_map)

    matrix = mmread(guide_root / "guide_matrix" / "matrix.mtx").tocsc()
    if matrix.shape != (len(features), len(barcodes)):
        raise ValueError("Papalexi guide matrix dimensions disagree with manifests")
    if len(rna_barcodes) != len(set(rna_barcodes)):
        raise ValueError("Papalexi RNA barcodes are not unique")
    if len(barcodes) != len(set(barcodes)):
        raise ValueError("Papalexi guide barcodes are not unique")

    split_lookup = _split_lookup(split_rows, "papalexi_thp1_eccite")
    proxy_rows: list[dict[str, Any]] = []
    exact_top_matches = 0
    labelled_cells = 0
    class_counts: Counter[str] = Counter()
    for index, cell_id in enumerate(barcodes):
        start, end = matrix.indptr[index], matrix.indptr[index + 1]
        row_indices = matrix.indices[start:end]
        values = matrix.data[start:end]
        order = np.argsort(values)[::-1] if len(values) else []
        top_index = int(row_indices[order[0]]) if len(order) else -1
        second_index = int(row_indices[order[1]]) if len(order) > 1 else -1
        top_guide = features[top_index] if top_index >= 0 else ""
        second_guide = features[second_index] if second_index >= 0 else ""
        top_count = int(values[order[0]]) if len(order) else 0
        second_count = int(values[order[1]]) if len(order) > 1 else 0
        meta = metadata.get(cell_id, {})
        external_guide = str(meta.get("guide_ID") or "").strip()
        external_target = str(meta.get("gene") or "").strip()
        if external_guide:
            labelled_cells += 1
            if external_guide == top_guide:
                proxy_class = "external_label_top_count_match"
                exact_top_matches += 1
            else:
                proxy_class = "external_label_top_count_mismatch"
        elif top_count == 0:
            proxy_class = "no_external_label_no_count"
        else:
            proxy_class = "no_external_label_with_count"
        class_counts[proxy_class] += 1
        split, selection = split_lookup.get(cell_id, ("not_selected", {}))
        proxy_rows.append(
            {
                "cell_id": cell_id,
                "split": split,
                "external_guide": external_guide,
                "external_target": external_target,
                "top_count_guide": top_guide,
                "top_count_target": guide_map.get(top_guide, ""),
                "top_count": top_count,
                "second_count_guide": second_guide,
                "second_count": second_count,
                "total_guide_umi": int(values.sum()) if len(values) else 0,
                "nonzero_guide_count": len(values),
                "proxy_class": proxy_class,
                "is_control": selection.get("is_control", ""),
            }
        )

    barcode_overlap = len(set(barcodes) & set(rna_barcodes))
    reconciliation = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-02",
        "generator_job_id": "REF-02-A",
        "dataset_id": "papalexi_thp1_eccite",
        "independent_of_pertura_results": True,
        "guide_asset_manifest_sha256": guide_manifest_hash,
        "cells": len(barcodes),
        "guides": len(features),
        "matrix_nonzero_count": int(matrix.nnz),
        "rna_guide_barcode_overlap": barcode_overlap,
        "barcode_alignment_rate": barcode_overlap / max(1, len(barcodes)),
        "metadata_alignment_rate": len(set(barcodes) & set(metadata)) / max(1, len(barcodes)),
        "mapping_conflict_count": mapping_conflicts,
        "unmapped_feature_count": len(set(features) - set(guide_map)),
        "external_labelled_cell_count": labelled_cells,
        "external_label_top_count_match_rate": exact_top_matches / max(1, labelled_cells),
        "proxy_class_counts": dict(sorted(class_counts.items())),
        "limitations": [
            "External guide labels are a real-data proxy, not ground-truth posterior assignments.",
            "Nonzero raw counts are not interpreted as assignments because ambient counts may be present.",
        ],
        "source_dimensions": guide_manifest["dimensions"],
    }
    return reconciliation, proxy_rows


def _write_matrix_market(path: Path, rows: int, columns: int, entries: list[tuple[int, int, int]]) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("%%MatrixMarket matrix coordinate integer general\n")
        handle.write("% deterministic REF-02-B planted guide-count fixture\n")
        handle.write(f"{rows} {columns} {len(entries)}\n")
        for row, column, value in entries:
            handle.write(f"{row + 1} {column + 1} {value}\n")


def _generate_ref02b(output_dir: Path) -> tuple[Path, list[dict[str, Any]], dict[str, Any]]:
    fixture = output_dir / "guide_count_fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    guides = ["gA", "gB", "gC"]
    targets = ["TARGET_A", "TARGET_B", "TARGET_C"]
    planted: list[tuple[str, list[int], str, str, bool, float]] = []
    for guide_index, guide in enumerate(guides):
        for replicate in range(4):
            counts = [1, 1, 1]
            counts[guide_index] = 24 + replicate
            planted.append(
                (
                    f"{guide}-positive-{replicate}",
                    counts,
                    guide,
                    "true_positive_singlet",
                    True,
                    0.99,
                )
            )
    planted.extend(
        [
            ("multi-0", [24, 23, 1], "gA;gB", "true_positive_multiguide", False, 0.99),
            ("multi-1", [22, 25, 1], "gA;gB", "true_positive_multiguide", False, 0.99),
            ("uncertain-0", [4, 3, 1], "", "uncertain", False, 0.55),
            ("uncertain-1", [3, 4, 1], "", "uncertain", False, 0.55),
            ("no-guide-0", [1, 0, 1], "", "no_guide", False, 0.02),
            ("no-guide-1", [0, 1, 1], "", "no_guide", False, 0.02),
        ]
    )
    filtered_entries = [
        (guide_index, cell_index, count)
        for cell_index, (_, counts, *_rest) in enumerate(planted)
        for guide_index, count in enumerate(counts)
        if count
    ]
    empty = [(f"empty-{index}", [1 if index % 3 == guide else 0 for guide in range(3)]) for index in range(6)]
    raw_rows = [(cell_id, counts) for cell_id, counts, *_rest in planted] + empty
    raw_entries = [
        (guide_index, cell_index, count)
        for cell_index, (_, counts) in enumerate(raw_rows)
        for guide_index, count in enumerate(counts)
        if count
    ]

    _write_matrix_market(fixture / "filtered_matrix.mtx", 3, len(planted), filtered_entries)
    _write_matrix_market(fixture / "raw_matrix.mtx", 3, len(raw_rows), raw_entries)
    (fixture / "barcodes.tsv").write_text(
        "".join(f"{row[0]}\n" for row in planted), encoding="utf-8", newline="\n"
    )
    (fixture / "raw_barcodes.tsv").write_text(
        "".join(f"{cell_id}\n" for cell_id, _ in raw_rows), encoding="utf-8", newline="\n"
    )
    (fixture / "features.tsv").write_text(
        "".join(f"{guide}\t{guide}\tCRISPR Guide Capture\n" for guide in guides),
        encoding="utf-8",
        newline="\n",
    )
    (fixture / "guide_map.tsv").write_text(
        "guide\ttarget\n" + "".join(f"{guide}\t{target}\n" for guide, target in zip(guides, targets)),
        encoding="utf-8",
        newline="\n",
    )
    truth_rows = [
        {
            "cell_id": cell_id,
            "true_guides": true_guides,
            "true_targets": ";".join(guide_to_target[item] for item in true_guides.split(";") if item),
            "assignment_class": assignment_class,
            "expected_retained_low_moi": str(retained).lower(),
            "assignment_probability_truth": f"{probability:.2f}",
        }
        for cell_id, _counts, true_guides, assignment_class, retained, probability in planted
        for guide_to_target in [dict(zip(guides, targets))]
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-02",
        "generator_job_id": "REF-02-B",
        "seed": 1729,
        "guide_count": 3,
        "filtered_cell_count": len(planted),
        "raw_droplet_count": len(raw_rows),
        "empty_droplet_count": len(empty),
        "class_counts": dict(sorted(Counter(row["assignment_class"] for row in truth_rows).items())),
        "ambient_mean_guide_umi": {guide: 1 / 3 for guide in guides},
    }
    _write_json(fixture / "fixture_manifest.json", manifest)
    return fixture, truth_rows, manifest


def _generate_ref02c(
    split_rows: dict[str, dict[str, list[dict[str, str]]]],
    proxy_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    papalexi_proxy = {row["cell_id"]: row for row in proxy_rows}
    qc_rows: list[dict[str, Any]] = []
    retained_rows: list[dict[str, Any]] = []
    for dataset_id in DATASETS:
        for split, rows in split_rows[dataset_id].items():
            for selection in rows:
                cell_id = selection["cell_id"]
                is_control = str(selection["is_control"]).lower() == "true"
                if dataset_id == "norman_k562_crispra_2019":
                    design_class = "predefined_dual_sgrna_construct"
                    intended_combination = True
                    retained_state = "retain_by_design"
                    reason = "predefined construct is not a transcriptomic doublet"
                elif dataset_id == "replogle_k562_essential_2022":
                    design_class = "same_target_dual_sgrna_vector"
                    intended_combination = True
                    retained_state = "retain_by_design"
                    reason = "multiplexed same-target vector is not a multi-infection call"
                else:
                    proxy = papalexi_proxy.get(cell_id, {})
                    design_class = "external_single_guide_proxy"
                    intended_combination = False
                    if proxy.get("external_guide"):
                        retained_state = "retain_for_external_label_proxy"
                        reason = "external guide label is present"
                    else:
                        retained_state = "unresolved_without_assignment_truth"
                        reason = "external guide label is absent"
                qc_rows.append(
                    {
                        "dataset_id": dataset_id,
                        "split": split,
                        "cell_id": cell_id,
                        "group_id": selection["group_id"],
                        "is_control": str(is_control).lower(),
                        "design_class": design_class,
                        "intended_guide_construct": str(intended_combination).lower(),
                        "transcriptomic_doublet": "not_inferred",
                        "expected_action": retained_state,
                    }
                )
                retained_rows.append(
                    {
                        "dataset_id": dataset_id,
                        "split": split,
                        "cell_id": cell_id,
                        "expected_state": retained_state,
                        "reason": reason,
                    }
                )
    return qc_rows, retained_rows


def generate(datasets_path: Path, splits_path: Path, guide_root: Path, output_dir: Path) -> dict[str, Any]:
    datasets_payload = json.loads(datasets_path.read_text(encoding="utf-8"))
    missing = set(DATASETS) - set(datasets_payload.get("datasets", {}))
    if missing:
        raise ValueError(f"REF-02 datasets are missing: {sorted(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    print("REF-02: validating frozen split selections", flush=True)
    split_rows = _load_split_rows(splits_path)

    print("REF-02-A: reconciling Papalexi barcodes, guide map and count matrix", flush=True)
    reconciliation, proxy_rows = _generate_ref02a(guide_root, split_rows, output_dir)
    reconciliation_path = output_dir / "papalexi_guide_reconciliation.json"
    proxy_path = output_dir / "papalexi_assignment_proxy.tsv"
    _write_json(reconciliation_path, reconciliation)
    _write_tsv(
        proxy_path,
        [
            "cell_id", "split", "external_guide", "external_target",
            "top_count_guide", "top_count_target", "top_count",
            "second_count_guide", "second_count", "total_guide_umi",
            "nonzero_guide_count", "proxy_class", "is_control",
        ],
        proxy_rows,
    )
    print(f"REF-02-A: wrote {len(proxy_rows)} external-label proxy rows", flush=True)

    print("REF-02-B: generating deterministic sparse assignment fixture", flush=True)
    fixture_path, assignment_truth, fixture_manifest = _generate_ref02b(output_dir)
    assignment_truth_path = output_dir / "guide_assignment_truth.tsv"
    _write_tsv(
        assignment_truth_path,
        [
            "cell_id", "true_guides", "true_targets", "assignment_class",
            "expected_retained_low_moi", "assignment_probability_truth",
        ],
        assignment_truth,
    )
    print(f"REF-02-B: wrote {len(assignment_truth)} planted truth rows", flush=True)

    print("REF-02-C: evaluating split-scoped construct retention rules", flush=True)
    qc_rows, retained_rows = _generate_ref02c(split_rows, proxy_rows)
    qc_path = output_dir / "screen_qc_truth.tsv"
    retained_path = output_dir / "retained_cell_truth.tsv"
    _write_tsv(
        qc_path,
        [
            "dataset_id", "split", "cell_id", "group_id", "is_control",
            "design_class", "intended_guide_construct",
            "transcriptomic_doublet", "expected_action",
        ],
        qc_rows,
    )
    _write_tsv(
        retained_path,
        ["dataset_id", "split", "cell_id", "expected_state", "reason"],
        retained_rows,
    )
    print(f"REF-02-C: wrote {len(retained_rows)} retained-cell truth rows", flush=True)

    print("REF-02: hashing outputs and writing manifest", flush=True)
    outputs = {
        path.name: _path_sha256(path)
        for path in (
            reconciliation_path,
            proxy_path,
            fixture_path,
            assignment_truth_path,
            qc_path,
            retained_path,
        )
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-02",
        "completed_jobs": ["REF-02-A", "REF-02-B", "REF-02-C"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "splits.json": _sha256(splits_path),
            "papalexi_guide_assets": _path_sha256(guide_root),
        },
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": outputs,
        "counts": {
            "real_proxy_rows": len(proxy_rows),
            "planted_assignment_rows": len(assignment_truth),
            "screen_qc_rows": len(qc_rows),
            "retained_truth_rows": len(retained_rows),
        },
        "limitations": [
            "Papalexi published metadata is scored as an external-label proxy, not exact assignment truth.",
            "Replogle and Norman construct retention is a design invariant, not a transcriptomic-doublet call.",
            "Exact assignment, ambient and low-MOI retention metrics use the planted REF-02-B fixture.",
        ],
        "planted_fixture": fixture_manifest,
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "reference_pack_id": "REF-02",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "dataset_count": len(DATASETS),
        "output_count": len(outputs),
        "counts": manifest["counts"],
        "manifest_sha256": _sha256(manifest_path),
        "problems": [],
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate independent REF-02 guide and screen-QC references.")
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--papalexi-guide-assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(
        args.datasets.resolve(),
        args.splits.resolve(),
        args.papalexi_guide_assets.resolve(),
        args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
