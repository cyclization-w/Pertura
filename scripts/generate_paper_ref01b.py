from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "pertura-paper-ref01-v1"


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


def _intake_truth() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-01",
        "generator_job_id": "REF-01-B",
        "independent_of_pertura_results": True,
        "cases": [
            {
                "case_id": "wrong_expression_layer",
                "fixture": {
                    "cell_ids": ["cell-1", "cell-2", "cell-3", "cell-4"],
                    "gene_ids": ["GENE1", "GENE2"],
                    "declared_expression_layer": "X",
                    "matrices": {
                        "X": {
                            "values": [
                                [0.0, 0.693147],
                                [1.098612, 0.0],
                                [0.693147, 1.386294],
                                [1.609438, 0.693147],
                            ],
                            "scale": "nonnegative_transformed",
                        },
                        "counts": {
                            "values": [[0, 1], [2, 0], [1, 3], [4, 1]],
                            "scale": "count_like",
                        },
                    },
                },
                "truth": {
                    "issue_codes": ["wrong_expression_layer"],
                    "recommended_expression_layer": "counts",
                    "must_not_classify_declared_layer_as_counts": True,
                    "expected_status": "blocked",
                },
            },
            {
                "case_id": "duplicate_cell_identity",
                "fixture": {
                    "cell_ids": ["cell-1", "cell-1", "cell-2"],
                    "gene_ids": ["GENE1"],
                    "X": [[1], [2], [3]],
                },
                "truth": {
                    "issue_codes": ["duplicate_cell_identity"],
                    "duplicate_ids": ["cell-1"],
                    "duplicate_row_count": 1,
                    "expected_status": "blocked",
                },
            },
        ],
    }


def _design_truth() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-01",
        "generator_job_id": "REF-01-B",
        "independent_of_pertura_results": True,
        "cases": [
            {
                "case_id": "missing_replicate",
                "fixture": {
                    "columns": ["cell_id", "condition", "batch"],
                    "rows": [
                        ["a1", "control", "b1"],
                        ["a2", "control", "b2"],
                        ["t1", "treated", "b1"],
                        ["t2", "treated", "b2"],
                    ],
                },
                "truth": {
                    "issue_codes": ["missing_independent_unit"],
                    "missing_fields": ["replicate"],
                    "cell_is_valid_independent_unit": False,
                    "expected_status": "needs_input",
                },
            },
            {
                "case_id": "condition_batch_confounding",
                "fixture": {
                    "columns": ["cell_id", "condition", "replicate", "batch"],
                    "rows": [
                        ["a1", "control", "r1", "batch-control"],
                        ["a2", "control", "r2", "batch-control"],
                        ["t1", "treated", "r3", "batch-treated"],
                        ["t2", "treated", "r4", "batch-treated"],
                    ],
                },
                "truth": {
                    "issue_codes": ["condition_batch_complete_confounding"],
                    "condition_to_batches": {
                        "control": ["batch-control"],
                        "treated": ["batch-treated"],
                    },
                    "estimable_condition_effect": False,
                    "expected_status": "blocked",
                },
            },
        ],
    }


def _validate_truth(payload: dict[str, Any], expected_cases: set[str]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("REF-01-B truth schema mismatch")
    if payload.get("reference_pack_id") != "REF-01":
        raise ValueError("REF-01-B reference pack mismatch")
    if payload.get("generator_job_id") != "REF-01-B":
        raise ValueError("REF-01-B generator job mismatch")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("REF-01-B cases are missing")
    case_ids = {str(case.get("case_id") or "") for case in cases}
    if case_ids != expected_cases:
        raise ValueError("REF-01-B case catalog mismatch")
    for case in cases:
        truth = case.get("truth")
        if (
            not isinstance(case.get("fixture"), dict)
            or not isinstance(truth, dict)
            or not truth.get("issue_codes")
            or truth.get("expected_status") not in {"blocked", "needs_input"}
        ):
            raise ValueError(f"invalid REF-01-B case: {case.get('case_id')}")


def generate(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            "REF-01-A manifest is missing; generate REF-01-A before REF-01-B"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("reference_pack_id") != "REF-01"
        or "REF-01-A" not in manifest.get("completed_jobs", [])
    ):
        raise ValueError("REF-01-A manifest is incompatible")

    for name, expected_hash in manifest.get("output_files", {}).items():
        path = output_dir / name
        if not path.is_file() or _sha256(path) != expected_hash:
            raise ValueError(f"REF-01-A output hash drift: {name}")

    intake = _intake_truth()
    design = _design_truth()
    _validate_truth(
        intake, {"wrong_expression_layer", "duplicate_cell_identity"}
    )
    _validate_truth(
        design, {"missing_replicate", "condition_batch_confounding"}
    )

    intake_path = output_dir / "intake_failure_truth.json"
    design_path = output_dir / "design_failure_truth.json"
    _write_json(intake_path, intake)
    _write_json(design_path, design)

    output_files = dict(manifest.get("output_files") or {})
    output_files[intake_path.name] = _sha256(intake_path)
    output_files[design_path.name] = _sha256(design_path)
    generator_scripts = dict(manifest.get("generator_scripts") or {})
    if manifest.get("generator_script_sha256"):
        generator_scripts.setdefault(
            "REF-01-A", manifest["generator_script_sha256"]
        )
    generator_scripts["REF-01-B"] = _sha256(Path(__file__).resolve())

    manifest.update(
        {
            "completed_jobs": ["REF-01-A", "REF-01-B"],
            "pending_jobs": [],
            "readiness": "generated",
            "generator_scripts": generator_scripts,
            "output_files": output_files,
            "limitations": [
                "Matrix scale labels are based on deterministic bounded samples.",
                "Design confirmations are not inferred and remain pending manual reference review.",
                "REF-01-B fixtures are planted protocol cases, not empirical performance estimates.",
            ],
        }
    )
    _write_json(manifest_path, manifest)

    return {
        "reference_pack_id": "REF-01",
        "readiness": "generated",
        "completed_jobs": ["REF-01-A", "REF-01-B"],
        "pending_jobs": [],
        "case_count": 4,
        "output_dir": str(output_dir),
        "manifest_sha256": _sha256(manifest_path),
        "problems": [],
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic REF-01-B planted intake and design "
            "failure truths, then complete the existing REF-01 manifest."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
