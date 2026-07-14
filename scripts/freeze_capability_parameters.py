from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from pertura_workflow.capabilities.parameter_schema import (
    expected_executor_parameter_names,
    schema_example,
)

ARRAY_FIELDS = {
    "axis_columns", "balance_columns", "candidates", "contrast", "control_ids",
    "control_values", "covariates", "gene_universe", "heldout_axes", "model_training_ids",
    "module_reference_training_ids", "negative_control_conditions", "preprocessing_training_ids",
    "ranks", "records", "responder_labels", "resolutions", "seeds", "signature_genes",
    "state_reference_training_ids", "target_conditions", "targets",
}
OBJECT_FIELDS = {"axes", "manual_labels", "marker_candidates", "weights"}
BOOLEAN_FIELDS = {
    "paired", "perturbation_labels_used", "robust", "scale",
    "signature_learned_from_same_perturbation", "signature_test_split_used",
    "test_split_used", "trend",
}
INTEGER_PREFIXES = ("n_", "min_", "max_", "minimum_", "bootstrap_", "guide_bootstrap_")
INTEGER_FIELDS = {
    "bootstraps", "calibration_group_size", "chunk_rows", "iter_num", "iterations", "max_iterations",
    "permutation_num", "permutations", "seed", "timeout_seconds",
}
INTEGER_ARRAY_FIELDS = {"ranks", "seeds"}
NUMBER_ARRAY_FIELDS = {"resolutions"}
NUMBER_MARKERS = (
    "alpha", "threshold", "tolerance", "coverage", "ratio", "budget", "cost",
    "rate", "fraction", "weight", "penalty", "temperature",
)
ASSET_ROLES = {
    "input_path": "primary_dataset",
    "h5ad_path": "primary_dataset",
    "counts_path": "primary_dataset",
    "expression_path": "primary_dataset",
    "response_matrix_path": "response_matrix",
    "metadata_path": "cell_metadata",
    "guide_counts_path": "guide_matrix",
    "raw_guide_counts_path": "guide_matrix",
    "filtered_guide_counts_path": "guide_matrix",
    "guide_matrix_path": "guide_matrix",
    "guide_map_path": "guide_map",
    "guide_target_map_path": "guide_map",
    "rna_barcodes_path": "rna_barcodes",
    "gmt_path": "gene_modules",
    "prediction_path": "prediction_bundle",
    "discovery_pairs_path": "discovery_pairs",
    "response_ids_path": "response_ids",
    "guide_ids_path": "guide_ids",
    "cell_ids_path": "cell_ids",
    "covariates_path": "cell_metadata",
    "effect_table_path": "effect_table",
    "null_results_path": "null_results",
    "guide_row_manifest_path": "guide_matrix_manifest",
    "guide_column_manifest_path": "guide_matrix_manifest",
    "row_manifest_path": "guide_matrix_manifest",
    "column_manifest_path": "guide_matrix_manifest",
    "assignment_path": "capability_artifact",
    "moi_doublet_path": "capability_artifact",
    "reference_model_path": "capability_artifact",
}
REQUIRED = {
    "diagnostic.design_balance.v1": ["metadata_path"],
    "guide.integrity.v1": ["guide_counts_path", "rna_barcodes_path", "guide_map_path"],
    "guide.assignment.nb_mixture.v1": ["guide_counts_path"],
    "module.import.gmt.v1": ["gmt_path", "species", "identifier_namespace"],
    "state.reference.fit.v1": ["h5ad_path", "control_column", "control_values"],
    "state.reference.map_knn.v1": ["h5ad_path"],
    "module.learn.control_nmf.v1": ["h5ad_path", "control_column", "control_values"],
    "target.responder.mixscape.v1": ["h5ad_path", "pert_key", "control"],
    "target.guide_efficacy.v1": [
        "expression_path", "metadata_path",
    ],
    "association.sceptre.v1": [
        "response_matrix_path", "guide_matrix_path", "guide_target_map_path",
        "discovery_pairs_path",
    ],
    "composition.propeller.v1": ["metadata_path"],
    "virtual.split.contract.v1": ["axes"],
    "virtual.prediction.ingest.v1": ["prediction_path"],
    "design.next_panel.v1": ["candidates", "budget"],
    "literature.europepmc.v1": ["query"],
}
ENUMS = {
    "moi": ["high"],
    "design_moi": ["low", "high", "unknown"],
    "guide_design": ["single", "combinatorial", "mixed", "unknown"],
    "side": ["both", "left", "right"],
    "expected_direction": ["down", "up"],
    "perturbation_type": ["KO", "KD", "OE"],
}
EXCLUSIVE_REQUIRED_FORMS = {
    "target.guide_efficacy.v1": [
        ["target_uid", "control_uid", "target_gene", "expected_direction"],
        ["targets"],
    ],
}


def _schema_for(name: str, existing: dict[str, Any]) -> dict[str, Any]:
    preserved = {
        key: value
        for key, value in dict(existing or {}).items()
        if key not in {"type", "items", "x-pertura-asset-role"}
    }
    if name == "targets":
        inferred = {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "target_uid": {"type": "string", "minLength": 1},
                    "control_uid": {"type": "string", "minLength": 1},
                    "target_gene": {"type": "string", "minLength": 1},
                    "expected_direction": {
                        "type": "string",
                        "enum": ["down", "up"],
                    },
                },
                "required": [
                    "target_uid", "control_uid", "target_gene", "expected_direction"
                ],
                "additionalProperties": False,
            },
        }
    elif name in OBJECT_FIELDS:
        inferred: dict[str, Any] = {"type": "object"}
    elif name in ARRAY_FIELDS or name.endswith("_ids") or name.endswith("_values"):
        inferred = {"type": "array", "items": {"type": "string"}}
        if name in INTEGER_ARRAY_FIELDS:
            inferred["items"] = {"type": "integer"}
        elif name in NUMBER_ARRAY_FIELDS:
            inferred["items"] = {"type": "number"}
        if name in {"contrast", "records", "candidates"}:
            inferred["items"] = {"type": ["string", "number", "integer", "object"]}
    elif name in BOOLEAN_FIELDS or name.endswith("_used"):
        inferred = {"type": "boolean"}
    elif name == "max_memory_gb" or name in {"alpha", "budget", "cost", "pval_cutoff", "tolerance", "maximum_type1_rate"} or name.endswith((
        "_alpha", "_threshold", "_tolerance", "_coverage", "_ratio", "_rate",
        "_fraction", "_weight", "_penalty", "_temperature", "_budget", "_cost",
    )):
        inferred = {"type": "number"}
    elif name in INTEGER_FIELDS or name.startswith(INTEGER_PREFIXES):
        inferred = {"type": "integer"}
    else:
        inferred = {"type": "string"}
    schema = inferred | preserved
    if name in ENUMS:
        schema["type"] = "string"
        schema["enum"] = ENUMS[name]
    if name in ASSET_ROLES:
        schema["type"] = "string"
        schema["x-pertura-asset-role"] = ASSET_ROLES[name]
        schema["description"] = f"Registered asset ID with role {ASSET_ROLES[name]}."
    if name == "max_memory_gb":
        schema.update({"default": 4.0, "exclusiveMinimum": 0})
    elif name == "n_jobs":
        schema.update({"default": 1, "minimum": 1})
    elif name == "chunk_rows":
        schema.update({"minimum": 1})
    elif name == "calibration_group_size":
        schema.update({"minimum": 1})
    return schema


def _example(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema["properties"]
    names = list(schema.get("required") or ())
    variants = schema.get("oneOf") or schema.get("anyOf") or ()
    if variants:
        names.extend(
            name
            for name in variants[0].get("required", ())
            if name not in names
        )
    if not names:
        names = [name for name in ("max_memory_gb", "n_jobs", "chunk_rows") if name in properties][:1]
    if not names and properties:
        names = [next(iter(properties))]
    return {name: schema_example(properties[name]) for name in names}


def freeze(root: Path, *, check: bool) -> list[str]:
    drift: list[str] = []
    for path in sorted((root / "src/pertura_workflow/capabilities/specs").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        executor_name = str(payload["executor"])
        keys = set(expected_executor_parameter_names(executor_name))
        existing = (payload.get("parameters_schema") or {}).get("properties", {})
        properties = {
            name: _schema_for(name, existing.get(name, {}))
            for name in sorted(keys)
        }
        if payload["capability_id"] == "target.guide_efficacy.v1":
            properties["expression_path"]["x-pertura-asset-role"] = "expression_table"
            properties["expression_path"]["description"] = (
                "Registered asset ID with role expression_table."
            )
        required = [name for name in REQUIRED.get(payload["capability_id"], ()) if name in properties]
        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        if payload["capability_id"] in EXCLUSIVE_REQUIRED_FORMS:
            forms = EXCLUSIVE_REQUIRED_FORMS[payload["capability_id"]]
            schema["oneOf"] = [
                {
                    "required": names,
                    "not": {
                        "anyOf": [
                            {"required": [excluded]}
                            for other in forms
                            if other is not names
                            for excluded in other
                        ]
                    },
                }
                for names in forms
            ]
        schema["examples"] = [_example(schema)]
        Draft202012Validator.check_schema(schema)
        for example in schema["examples"]:
            Draft202012Validator(schema).validate(example)
        payload["parameters_schema"] = schema
        rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        if path.read_text(encoding="utf-8") != rendered:
            if check:
                drift.append(path.relative_to(root).as_posix())
            else:
                path.write_text(rendered, encoding="utf-8", newline="\n")
    return drift


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift = freeze(args.repo.resolve(), check=args.check)
    for item in drift:
        print(item)
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
