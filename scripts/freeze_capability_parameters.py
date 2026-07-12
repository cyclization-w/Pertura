from __future__ import annotations

import argparse
import inspect
import re
from pathlib import Path

import yaml

from pertura_workflow.capabilities.executors import _EXECUTORS


KEY_PATTERN = re.compile(r"(?:parameters|params)\.get\([\"']([^\"']+)[\"']")
ARRAY_FIELDS = {
    "axes", "axis_columns", "balance_columns", "candidates", "contrast", "control_ids",
    "control_values", "covariates", "gene_universe", "heldout_axes", "marker_candidates",
    "model_training_ids", "module_reference_training_ids", "negative_control_conditions",
    "preprocessing_training_ids", "ranks", "records", "responder_labels", "resolutions",
    "seeds", "signature_genes", "state_reference_training_ids",
}
OBJECT_FIELDS = {"axes", "manual_labels", "marker_candidates", "weights"}
BOOLEAN_FIELDS = {
    "paired", "perturbation_labels_used", "scale", "signature_learned_from_same_perturbation",
    "signature_test_split_used", "test_split_used",
}
INTEGER_PREFIXES = ("n_", "min_", "max_", "minimum_", "bootstrap_", "guide_bootstrap_")
INTEGER_FIELDS = {
    "chunk_rows", "iter_num", "iterations", "max_iterations", "permutation_num",
    "permutations", "seed", "timeout_seconds",
}
NUMBER_MARKERS = ("alpha", "threshold", "tolerance", "coverage", "ratio", "budget", "cost")
ASSET_ROLES = {
    "input_path": "primary_dataset",
    "h5ad_path": "primary_dataset",
    "counts_path": "primary_dataset",
    "expression_path": "primary_dataset",
    "metadata_path": "cell_metadata",
    "guide_counts_path": "guide_matrix",
    "raw_guide_counts_path": "guide_matrix",
    "filtered_guide_counts_path": "guide_matrix",
    "guide_map_path": "guide_map",
    "rna_barcodes_path": "rna_barcodes",
    "gmt_path": "gene_modules",
    "prediction_path": "prediction_bundle",
}
REQUIRED = {
    "module.import.gmt.v1": ["gmt_path", "species", "identifier_namespace"],
    "state.reference.fit.v1": ["h5ad_path", "control_column", "control_values"],
    "state.reference.map_knn.v1": ["h5ad_path"],
    "module.learn.control_nmf.v1": ["h5ad_path", "control_column", "control_values"],
    "target.responder.mixscape.v1": ["h5ad_path", "pert_key", "control"],
    "virtual.split.contract.v1": ["axes"],
    "virtual.prediction.ingest.v1": ["prediction_path"],
    "design.next_panel.v1": ["candidates", "budget"],
    "literature.europepmc.v1": ["query"],
}


def _schema_for(name: str, existing: dict) -> dict:
    if name in OBJECT_FIELDS:
        schema = {"type": "object"}
    elif name in ARRAY_FIELDS or name.endswith("_ids") or name.endswith("_values"):
        schema = {"type": "array", "items": {"type": ["string", "number", "integer", "object"]}}
    elif name in BOOLEAN_FIELDS or name.endswith("_used"):
        schema = {"type": "boolean"}
    elif name in INTEGER_FIELDS or name.startswith(INTEGER_PREFIXES):
        schema = {"type": "integer"}
    elif name == "max_memory_gb" or any(marker in name for marker in NUMBER_MARKERS):
        schema = {"type": "number"}
    else:
        schema = {"type": "string"}
    schema.update(existing or {})
    if name in ASSET_ROLES:
        schema["x-pertura-asset-role"] = ASSET_ROLES[name]
        schema["description"] = f"Registered asset ID with role {ASSET_ROLES[name]}."
    return schema


def freeze(root: Path, *, check: bool) -> list[str]:
    drift = []
    for path in sorted((root / "src/pertura_workflow/capabilities/specs").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        executor = _EXECUTORS[payload["executor"]]
        module_source = inspect.getsource(inspect.getmodule(executor))
        keys = set(KEY_PATTERN.findall(module_source))
        keys.update((payload.get("parameters_schema") or {}).get("properties", {}))
        if payload.get("kind") in {"analysis", "diagnostic", "virtual"}:
            keys.update({"max_memory_gb", "n_jobs", "chunk_rows"})
        existing = (payload.get("parameters_schema") or {}).get("properties", {})
        schema = {
            "type": "object",
            "properties": {name: _schema_for(name, existing.get(name, {})) for name in sorted(keys)},
            "additionalProperties": False,
            "examples": [{}],
        }
        required = [name for name in REQUIRED.get(payload["capability_id"], ()) if name in keys]
        if required:
            schema["required"] = required
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
