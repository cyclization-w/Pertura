from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pertura_bench.models import GoldenComparison
from pertura_core import CapabilityRunRequest, ScopeKey
from pertura_core.hashing import file_sha256
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.capabilities.edger import run_edger_pseudobulk
from pertura_workflow.environment import environment_lock, environment_prefix, micromamba_path
from pertura_workflow.intake import inspect_dataset_path


VALID_CASES = {
    "paired_strict": {"paired": True, "baseline_units": ("r1", "r2", "r3"), "target_units": ("r1", "r2", "r3")},
    "unpaired_strict": {"paired": False, "baseline_units": ("b1", "b2", "b3"), "target_units": ("t1", "t2", "t3")},
    "two_unit_caution": {"paired": False, "baseline_units": ("b1", "b2"), "target_units": ("t1", "t2")},
}


def run_edger_golden(*, environment: str = "edger-v1", repo_root: str | Path | None = None) -> dict[str, Any]:
    if environment != "edger-v1":
        raise ValueError("the v0.2 golden harness supports only edger-v1")
    root = Path(repo_root or Path.cwd()).resolve()
    lock = environment_lock(environment)
    reference_script = Path(__file__).resolve().parent / "runners" / "edger_reference.R"
    runner_script = Path(__file__).resolve().parents[1] / "pertura_workflow" / "capabilities" / "runners" / "edger_ql.R"
    maximum_errors = {name: 0.0 for name in ("logFC", "F", "PValue", "FDR", "design_matrix", "sample_manifest", "status")}
    statuses: dict[str, str] = {}
    input_hashes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="pertura-edger-golden-") as temporary:
        temp = Path(temporary)
        for case_name, case in VALID_CASES.items():
            case_dir = temp / case_name
            counts, metadata = _write_case(case_dir, **case)
            input_hashes[f"{case_name}:counts"] = file_sha256(counts)
            input_hashes[f"{case_name}:metadata"] = file_sha256(metadata)
            observed, workspace = _run_pertura_case(case_name, case_dir, case)
            statuses[case_name] = observed["status"]
            expected_status = "completed_with_caution" if case_name == "two_unit_caution" else "completed"
            if observed["status"] != expected_status:
                maximum_errors["status"] = 1.0
                continue
            reference = _run_reference(case_name, case_dir, case, reference_script)
            actual_root = workspace
            output_by_name = {Path(item).name: actual_root / item for item in observed["output_paths"]}
            for column in ("logFC", "F", "PValue", "FDR"):
                maximum_errors[column] = max(
                    maximum_errors[column],
                    _compare_result_column(output_by_name["edger_results.csv"], reference["results"], column),
                )
            maximum_errors["design_matrix"] = max(
                maximum_errors["design_matrix"],
                _compare_numeric_csv(output_by_name["design_matrix.csv"], reference["design"], key="sample_id"),
            )
            maximum_errors["sample_manifest"] = max(
                maximum_errors["sample_manifest"],
                0.0 if _normalized_rows(output_by_name["pseudobulk_samples.csv"]) == _normalized_rows(reference["samples"]) else 1.0,
            )

        confounded_dir = temp / "confounded_blocked"
        _write_case(
            confounded_dir,
            paired=False,
            baseline_units=("b1", "b2", "b3"),
            target_units=("t1", "t2", "t3"),
            confounded=True,
        )
        blocked, _ = _run_pertura_case(
            "confounded_blocked",
            confounded_dir,
            {"paired": False, "baseline_units": ("b1", "b2", "b3"), "target_units": ("t1", "t2", "t3"), "confounded": True},
        )
        statuses["confounded_blocked"] = blocked["status"]
        if blocked["status"] != "blocked":
            maximum_errors["status"] = 1.0

    passed = all(value <= 1e-7 for value in maximum_errors.values())
    comparison = GoldenComparison(
        environment_lock_hash=lock["lock_hash"],
        input_hashes=input_hashes,
        reference_script_hash=file_sha256(reference_script),
        runner_hash=file_sha256(runner_script),
        maximum_errors=maximum_errors,
        cases=statuses,
        passed=passed,
    )
    output = root / "benchmarks" / "golden" / "edger_v1_verdict.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(comparison.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return comparison.model_dump(mode="json") | {"output": str(output)}


def _write_case(
    directory: Path,
    *,
    paired: bool,
    baseline_units: tuple[str, ...],
    target_units: tuple[str, ...],
    confounded: bool = False,
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cells: list[str] = []
    metadata: list[dict[str, str]] = []
    sample_index = 0
    for condition, units in (("baseline", baseline_units), ("target", target_units)):
        for unit_index, unit in enumerate(units):
            for cell_index in range(12):
                cell = f"c{sample_index:03d}_{cell_index:02d}"
                cells.append(cell)
                metadata.append({
                    "cell_id": cell,
                    "condition": condition,
                    "replicate": unit,
                    "batch": ("batch_baseline" if condition == "baseline" else "batch_target") if confounded else f"batch_{unit_index % 2}",
                })
            sample_index += 1
    counts_path = directory / "counts.csv"
    with counts_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene", *cells])
        for gene_index in range(30):
            row = [f"G{gene_index + 1:02d}"]
            for cell_index, meta in enumerate(metadata):
                base = 18 + (gene_index % 7) * 3 + (cell_index % 3)
                if meta["condition"] == "target" and gene_index < 5:
                    base *= 2
                elif meta["condition"] == "target" and 5 <= gene_index < 10:
                    base = max(1, base // 2)
                row.append(int(base))
            writer.writerow(row)
    metadata_path = directory / "metadata.csv"
    with metadata_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("cell_id", "condition", "replicate", "batch"))
        writer.writeheader()
        writer.writerows(metadata)
    return counts_path, metadata_path


def _run_pertura_case(
    case_name: str,
    case_dir: Path,
    case: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    """Run the bundled runner while keeping the statistical golden independent.

    Product-path dependency/session behavior is covered by synthetic_ci. This
    harness intentionally isolates the edgeR implementation so it can compare
    the exact numerical output against an independently authored R reference.
    """

    contract = inspect_dataset_path(case_dir)
    spec = CapabilityRegistry.load_default(include_external=False).get(
        "de.pseudobulk.edger.v1"
    )
    staging = case_dir / "pertura"
    staging.mkdir()
    parameters = {
        "counts_path": "counts.csv",
        "metadata_path": "metadata.csv",
        "target_condition": "target",
        "baseline_condition": "baseline",
        "paired": bool(case["paired"]),
        "minimum_cells_per_pseudobulk": 10,
    }
    if case.get("confounded"):
        parameters["covariates"] = ["batch"]
    request = CapabilityRunRequest(
        run_id=f"edger-golden-{case_name}",
        capability_id=spec.capability_id,
        capability_version=spec.version,
        contract_id=contract.contract_id,
        contract_hash=contract.canonical_hash,
        scope=ScopeKey(dataset_id=contract.dataset_id),
        parameters=parameters,
    )
    result = run_edger_pseudobulk(spec, request, contract, staging)
    return result.model_dump(mode="json"), staging


def _run_reference(case_name: str, case_dir: Path, case: dict[str, Any], script: Path) -> dict[str, Path]:
    output = case_dir / "reference"
    output.mkdir()
    config = {
        "counts_path": str(case_dir / "counts.csv"),
        "metadata_path": str(case_dir / "metadata.csv"),
        "cell_column": "cell_id",
        "condition_column": "condition",
        "replicate_column": "replicate",
        "state_column": "",
        "baseline": "baseline",
        "target": "target",
        "paired": bool(case["paired"]),
        "covariates": [],
        "minimum_cells": 10,
        "result_path": str(output / "results.csv"),
        "design_path": str(output / "design.csv"),
        "samples_result_path": str(output / "samples.csv"),
    }
    config_path = output / "config.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    completed = subprocess.run(
        [str(micromamba_path()), "run", "--prefix", str(environment_prefix()), "Rscript", str(script), str(config_path)],
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600, check=False,
        env=_minimal_env(output / ".mamba-root"),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"independent edgeR reference failed for {case_name}: {completed.stderr[-4000:]}")
    return {"results": output / "results.csv", "design": output / "design.csv", "samples": output / "samples.csv"}


def _compare_result_column(actual: Path, expected: Path, column: str) -> float:
    left = {row["gene"]: row for row in _read_rows(actual)}
    right = {row["gene"]: row for row in _read_rows(expected)}
    if set(left) != set(right):
        return 1.0
    return max((_numeric_error(float(left[gene][column]), float(right[gene][column])) for gene in left), default=0.0)


def _compare_numeric_csv(actual: Path, expected: Path, *, key: str) -> float:
    left_rows = _read_rows(actual)
    right_rows = _read_rows(expected)
    if [row[key] for row in left_rows] != [row[key] for row in right_rows]:
        return 1.0
    if not left_rows or set(left_rows[0]) != set(right_rows[0]):
        return 1.0
    columns = [name for name in left_rows[0] if name != key]
    return max(
        (_numeric_error(float(left[name]), float(right[name])) for left, right in zip(left_rows, right_rows) for name in columns),
        default=0.0,
    )


def _numeric_error(left: float, right: float) -> float:
    if math.isnan(left) or math.isnan(right):
        return 0.0 if math.isnan(left) and math.isnan(right) else 1.0
    if math.isinf(left) or math.isinf(right):
        return 0.0 if left == right else 1.0
    absolute = abs(left - right)
    relative = absolute / max(abs(right), 1e-300)
    return min(absolute, relative)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _normalized_rows(path: Path) -> list[dict[str, str]]:
    return [{key: value for key, value in row.items()} for row in _read_rows(path)]


def _minimal_env(mamba_root: Path) -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "PATH")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    mamba_root.mkdir(parents=True, exist_ok=True)
    environment["MAMBA_ROOT_PREFIX"] = str(mamba_root)
    return environment
