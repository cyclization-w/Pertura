from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "pertura-paper-ref05-v1"
DATASET_ID = "kang18_8vs8_pbmc"
SEED = 1729
BASELINE = "ctrl"
TARGET = "stim"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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


def _read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _selection_rows(splits_path: Path, split: str) -> list[dict[str, str]]:
    payload = json.loads(splits_path.read_text(encoding="utf-8"))
    record = payload["datasets"][DATASET_ID][split]
    path = splits_path.resolve().parent.parent / record["cell_selection_path"]
    if not path.is_file() or _sha256(path) != record["cell_selection_file_sha256"]:
        raise ValueError(f"Kang {split} selection hash drift")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle, delimiter="\t")]
    required = {"cell_id", "unit_id", "is_control"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"Kang {split} selection is missing required fields")
    identities = [row["cell_id"] for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError(f"duplicate Kang {split} cell identity")
    return rows


def _split_design(rows: list[dict[str, str]], split: str) -> dict[str, str]:
    design: dict[str, str] = {}
    arms: dict[str, set[str]] = {}
    for row in rows:
        cell = str(row["cell_id"])
        donor = str(row["unit_id"])
        control_flag = str(row["is_control"]).lower()
        if control_flag not in {"true", "false"}:
            raise ValueError(f"Kang {split} has an invalid control flag for {cell}")
        condition = BASELINE if control_flag == "true" else TARGET
        if not donor:
            raise ValueError(f"Kang {split} contains an empty donor identity")
        design[cell] = donor + "\x1f" + condition
        arms.setdefault(donor, set()).add(condition)
    incomplete = sorted(donor for donor, values in arms.items() if values != {BASELINE, TARGET})
    if incomplete:
        raise ValueError(f"Kang {split} donors lack paired conditions: {incomplete}")
    if len(arms) < 3:
        raise ValueError(f"Kang {split} requires at least three paired donors")
    return design


def _condition_matches(value: str, condition: str) -> bool:
    normalized = value.strip().lower()
    if condition == BASELINE:
        return normalized in {"ctrl", "control", "unstim", "unstimulated"}
    return normalized in {"stim", "stimulated"}


def _materialize_split_inputs(
    source: Any,
    selection: list[dict[str, str]],
    output_dir: Path,
    split: str,
    *,
    chunk_rows: int = 1024,
) -> dict[str, Any]:
    import numpy as np
    from scipy import sparse

    required_obs = {"ind", "stim", "cell"}
    missing_obs = sorted(required_obs - set(source.obs.columns))
    if missing_obs:
        raise ValueError("Kang H5AD is missing: " + ", ".join(missing_obs))
    split_design = _split_design(selection, split)
    source_names = source.obs_names.astype(str)
    requested = set(split_design)
    missing_cells = sorted(requested.difference(source_names))
    if missing_cells:
        raise ValueError(f"Kang artifact is missing {len(missing_cells)} {split} cells")
    selected = np.flatnonzero(source_names.isin(requested))
    selected_cells = [str(source_names[index]) for index in selected]
    sample_keys = sorted(set(split_design[cell] for cell in selected_cells))
    sample_index = {key: index for index, key in enumerate(sample_keys)}
    counts = np.zeros((len(sample_keys), int(source.n_vars)), dtype=np.int64)
    sample_cell_counts: Counter[str] = Counter()
    cell_rows: list[dict[str, str]] = []

    print(
        f"REF-05: sequentially scanning {source.n_obs} Kang rows for "
        f"{len(selected)} {split} cells",
        flush=True,
    )
    for start in range(0, int(source.n_obs), chunk_rows):
        stop = min(start + chunk_rows, int(source.n_obs))
        in_chunk = selected[(selected >= start) & (selected < stop)]
        if not len(in_chunk):
            continue
        block = source.X[start:stop, :]
        if hasattr(block, "to_memory"):
            block = block.to_memory()
        block = block[in_chunk - start, :]
        block = block.tocsr() if sparse.issparse(block) else np.asarray(block)
        values = block.data if sparse.issparse(block) else block.reshape(-1)
        if values.size and (
            not np.isfinite(values).all()
            or (values < 0).any()
            or not np.allclose(values, np.rint(values))
        ):
            raise ValueError("Kang counts must be finite nonnegative integers")
        obs = source.obs.iloc[in_chunk]
        for local_index, (source_index, row) in enumerate(obs.iterrows()):
            cell = str(source_index)
            donor, condition = split_design[cell].split("\x1f")
            if str(row["ind"]) != donor:
                raise ValueError(f"Kang split donor mismatch for {cell}")
            if not _condition_matches(str(row["stim"]), condition):
                raise ValueError(f"Kang split condition mismatch for {cell}")
            state = str(row["cell"]).strip()
            if not state or state.lower() in {"na", "nan", "none"}:
                raise ValueError(f"Kang cell-state identity is missing for {cell}")
            key = donor + "\x1f" + condition
            vector = block[local_index, :]
            summed = (
                np.asarray(vector.sum(axis=0)).reshape(-1)
                if sparse.issparse(vector)
                else np.asarray(vector).reshape(-1)
            )
            counts[sample_index[key], :] += np.rint(summed).astype(np.int64)
            sample_cell_counts[key] += 1
            cell_rows.append(
                {
                    "cell_id": cell,
                    "sample_id": f"{donor}::{condition}",
                    "donor": donor,
                    "condition": condition,
                    "state": state,
                }
            )

    if len(cell_rows) != len(selection):
        raise ValueError(f"Kang {split} materialization lost cells")
    sample_rows = []
    for key in sample_keys:
        donor, condition = key.split("\x1f")
        n_cells = sample_cell_counts[key]
        if n_cells < 20:
            raise ValueError(f"Kang {split} sample {donor}/{condition} has fewer than 20 cells")
        sample_rows.append(
            {
                "sample_id": f"{donor}::{condition}",
                "donor": donor,
                "condition": condition,
                "n_cells": n_cells,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    counts_path = output_dir / f"{split}_pseudobulk_counts.tsv"
    samples_path = output_dir / f"{split}_sample_metadata.tsv"
    cells_path = output_dir / f"{split}_cell_metadata.tsv"
    genes = [str(value) for value in source.var_names]
    with counts_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("gene\t" + "\t".join(row["sample_id"] for row in sample_rows) + "\n")
        for gene_index, gene in enumerate(genes):
            handle.write(
                gene
                + "\t"
                + "\t".join(str(int(value)) for value in counts[:, gene_index])
                + "\n"
            )
    _write_tsv(samples_path, ["sample_id", "donor", "condition", "n_cells"], sample_rows)
    _write_tsv(cells_path, ["cell_id", "sample_id", "donor", "condition", "state"], cell_rows)
    return {
        "counts_path": counts_path,
        "samples_path": samples_path,
        "cells_path": cells_path,
        "n_cells": len(cell_rows),
        "n_genes": len(genes),
        "n_samples": len(sample_rows),
        "n_donors": len({row["donor"] for row in sample_rows}),
    }


def _validate_outputs(output_dir: Path) -> dict[str, int]:
    required = {
        "edger_reference.tsv": {"gene", "logFC", "F", "PValue", "FDR"},
        "edger_design_matrix.tsv": {"sample_id"},
        "propeller_reference.tsv": {
            "cluster", "baseline_proportion", "target_proportion", "effect", "PValue", "FDR"
        },
        "propeller_design_matrix.tsv": {"sample_id"},
        "method_null_reference.tsv": {"permutation_id", "gene", "PValue", "FDR"},
        "replicate_null_reference.tsv": {
            "permutation_id", "type1_rate", "null_effect_bias", "exchangeability_violation_count"
        },
    }
    counts: dict[str, int] = {}
    for name, columns in required.items():
        fields, rows = _read_tsv(output_dir / name)
        missing = sorted(columns - set(fields))
        if missing:
            raise ValueError(f"{name} is missing: {missing}")
        if not rows:
            raise ValueError(f"{name} is empty")
        counts[name] = len(rows)
        for row in rows:
            for column in columns & {"PValue", "FDR", "type1_rate"}:
                value = float(row[column])
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"{name} has invalid {column}")
    _, replicate_rows = _read_tsv(output_dir / "replicate_null_reference.tsv")
    if any(int(row["exchangeability_violation_count"]) != 0 for row in replicate_rows):
        raise ValueError("REF-05 replicate null violated donor exchangeability")
    return counts


def generate(
    datasets_path: Path,
    splits_path: Path,
    repo: Path,
    rscript: Path,
    output_dir: Path,
) -> dict[str, Any]:
    import anndata as ad

    datasets = json.loads(datasets_path.read_text(encoding="utf-8"))
    record = datasets["datasets"][DATASET_ID]
    artifact = Path(record["artifact_path"]).resolve()
    if not artifact.is_file() or _sha256(artifact) != record["artifact_sha256"]:
        raise ValueError("Kang artifact is missing or has drifted")
    calibration = _selection_rows(splits_path, "calibration")
    evaluation = _selection_rows(splits_path, "evaluation")
    calibration_ids = {row["cell_id"] for row in calibration}
    evaluation_ids = {row["cell_id"] for row in evaluation}
    if calibration_ids & evaluation_ids:
        raise ValueError("Kang calibration/evaluation cell overlap")
    calibration_donors = {row["unit_id"] for row in calibration}
    evaluation_donors = {row["unit_id"] for row in evaluation}
    if calibration_donors & evaluation_donors:
        raise ValueError("Kang calibration/evaluation donor overlap")

    output_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = output_dir / "reference_inputs"
    source = ad.read_h5ad(artifact, backed="r")
    try:
        evaluation_inputs = _materialize_split_inputs(
            source, evaluation, inputs_dir, "evaluation"
        )
        calibration_inputs = _materialize_split_inputs(
            source, calibration, inputs_dir, "calibration"
        )
    finally:
        if getattr(source, "file", None) is not None:
            source.file.close()

    runner = (repo / "scripts" / "generate_paper_ref05.R").resolve()
    if not runner.is_file() or not rscript.is_file():
        raise FileNotFoundError(runner if not runner.is_file() else rscript)
    config = {
        "schema_version": "pertura-paper-ref05-r-config-v1",
        "baseline": BASELINE,
        "target": TARGET,
        "seed": SEED,
        "evaluation_counts": str(evaluation_inputs["counts_path"]),
        "evaluation_samples": str(evaluation_inputs["samples_path"]),
        "evaluation_cells": str(evaluation_inputs["cells_path"]),
        "calibration_counts": str(calibration_inputs["counts_path"]),
        "calibration_samples": str(calibration_inputs["samples_path"]),
        "output_dir": str(output_dir.resolve()),
    }
    config_path = inputs_dir / "ref05-r-config.json"
    _write_json(config_path, config)
    print("REF-05-A/B/C: running independent donor-aware R references", flush=True)
    completed = subprocess.run(
        [str(rscript), "--vanilla", str(runner), str(config_path)],
        cwd=repo,
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"REF-05 R reference failed with exit code {completed.returncode}; "
            "inspect the streamed benchmark log"
        )
    row_counts = _validate_outputs(output_dir)
    environment_path = output_dir / "r_environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))

    output_names = [
        "edger_reference.tsv",
        "edger_design_matrix.tsv",
        "edger_session_info.txt",
        "propeller_reference.tsv",
        "propeller_design_matrix.tsv",
        "propeller_session_info.txt",
        "method_null_reference.tsv",
        "replicate_null_reference.tsv",
        "r_environment.json",
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-05",
        "readiness": "generated",
        "completed_jobs": ["REF-05-A", "REF-05-B", "REF-05-C"],
        "pending_jobs": [],
        "independent_of_pertura_results": True,
        "input_files": {
            "datasets.json": _sha256(datasets_path),
            "splits.json": _sha256(splits_path),
            "kang_artifact": _sha256(artifact),
            "r_runner": _sha256(runner),
            **{
                f"evaluation_{name}": _sha256(path)
                for name, path in (
                    ("counts", evaluation_inputs["counts_path"]),
                    ("samples", evaluation_inputs["samples_path"]),
                    ("cells", evaluation_inputs["cells_path"]),
                )
            },
            **{
                f"calibration_{name}": _sha256(path)
                for name, path in (
                    ("counts", calibration_inputs["counts_path"]),
                    ("samples", calibration_inputs["samples_path"]),
                )
            },
        },
        "output_files": {name: _sha256(output_dir / name) for name in output_names},
        "counts": {
            "evaluation_cells": evaluation_inputs["n_cells"],
            "evaluation_donors": evaluation_inputs["n_donors"],
            "calibration_cells": calibration_inputs["n_cells"],
            "calibration_donors": calibration_inputs["n_donors"],
            "genes": evaluation_inputs["n_genes"],
            "edger_rows": row_counts["edger_reference.tsv"],
            "propeller_rows": row_counts["propeller_reference.tsv"],
            "method_null_rows": row_counts["method_null_reference.tsv"],
            "replicate_null_rows": row_counts["replicate_null_reference.tsv"],
            "exchangeability_violation_count": 0,
            "cell_label_permutation_count": 0,
        },
        "parameters": {
            "seed": SEED,
            "baseline": BASELINE,
            "target": TARGET,
            "analysis_unit": "donor",
            "design": "~ donor + condition",
            "null_permutation_unit": "paired_donor_condition_label",
            "cell_label_permutation": False,
        },
        "environment": environment,
        "limitations": [
            "Kang is replicated stimulation scRNA-seq and is not presented as Perturb-seq.",
            "edgeR and Propeller references use four held-out evaluation donors.",
            "Null references use mixed within-donor condition swaps on calibration donors; cells are never permuted.",
            "REF-05 distinguishes composition changes from within-state expression effects.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-05: manifest written", flush=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-05",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "passed": True,
        "problems": [],
        "manifest_sha256": _sha256(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate independent REF-05 edgeR, Propeller, and null references."
    )
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--rscript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(
        args.datasets.resolve(),
        args.splits.resolve(),
        args.repo.resolve(),
        args.rscript.resolve(),
        args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
