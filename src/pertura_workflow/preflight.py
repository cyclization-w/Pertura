from __future__ import annotations

import importlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pertura_workflow.models import (
    DetectedFile,
    EvidenceCandidate,
    PreflightReport,
    ReadinessEntry,
    candidate_id_for,
)


_SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache", ".claude_runs", ".p07_runs"}
_MAX_FILES = 500
_SAMPLE_VALUE_LIMIT = 12

_GUIDE_COLUMN_TOKENS = ("guide", "sgrna", "grna", "protospacer", "crispr")
_PERTURBATION_COLUMN_TOKENS = ("perturb", "target", "gene", "condition", "treatment", "guide", "sgrna", "grna")
_CONDITION_COLUMN_TOKENS = ("condition", "treatment", "drug", "compound", "dose", "stim", "vehicle")
_REPLICATE_COLUMN_TOKENS = ("donor", "sample", "replicate", "rep", "lane", "library")
_BATCH_COLUMN_TOKENS = ("batch", "lane", "library", "run")
_QC_COLUMN_TOKENS = (
    "n_genes",
    "n_genes_by_counts",
    "total_counts",
    "pct_counts_mt",
    "percent_mito",
    "pct_mito",
    "mito",
    "doublet",
    "ambient",
    "scrublet",
)
_STATE_COLUMN_TOKENS = ("cell_type", "celltype", "cell_state", "state", "cluster", "leiden", "louvain", "annotation", "annot")
_CONTROL_VALUE_RE = re.compile(
    r"(^|[_\-\s])(?:negctrl\w*|ntc\w*|non[_\-\s]?targeting\w*|nontargeting\w*|negative[_\-\s]?control\w*|safe[_\-\s]?targeting\w*|dmso|vehicle|untreated|mock)(?:$|[_\-\s])",
    re.IGNORECASE,
)
_GUIDE_VALUE_RE = re.compile(r"(^sg[A-Za-z0-9]+|[A-Za-z0-9]+_sg\d+|[A-Za-z0-9]+_[A-Za-z0-9]+__|NTC_?\d+|NegCtrl\d*)", re.IGNORECASE)
_COUNTS_LAYER_TOKENS = ("counts", "raw_counts", "umi", "spliced")
_NORMALIZED_LAYER_TOKENS = ("lognorm", "log1p", "normalized", "norm", "scaled")
_EMBEDDING_KEYS = ("x_pca", "x_umap", "x_tsne", "x_scvi", "x_diffmap")


def preflight_workspace(workspace: str | Path, *, mode: str = "benchmark", max_files: int = _MAX_FILES) -> PreflightReport:
    root = Path(workspace).resolve()
    detected = _detect_files(root, max_files=max_files)
    metadata = _metadata_from_files(detected)
    candidates = []
    for file in detected:
        candidate = _candidate_from_file(file)
        if candidate is not None:
            candidates.append(candidate)
    candidates.extend(_content_candidates_from_metadata(metadata))
    readiness = _readiness(metadata)
    return PreflightReport(
        workspace=str(root),
        mode=mode,
        detected_files=detected,
        detected_metadata=metadata,
        candidate_artifacts=candidates,
        readiness_by_claim_type=readiness,
    )


def _detect_files(root: Path, *, max_files: int) -> list[DetectedFile]:
    if root.is_file():
        return [_detected_file(root, root.parent)]
    if not root.exists():
        return []
    files: list[DetectedFile] = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        files.append(_detected_file(path, root))
    return files


def _detected_file(path: Path, root: Path) -> DetectedFile:
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        relative = path.name
    suffix = path.suffix.lower()
    return DetectedFile(
        path=str(path),
        relative_path=relative.replace("\\", "/"),
        suffix=suffix,
        file_kind=_classify_file(path),
        size_bytes=path.stat().st_size,
    )


def _classify_file(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == ".h5ad":
        return "anndata"
    if suffix in {".rds", ".rda"}:
        return "seurat"
    if "manifest" in name or "design" in name:
        return "design_manifest"
    if "guide_to_target" in name or "guide-target" in name or "protospacer" in name:
        return "guide_metadata"
    if "guide" in name or "sgrna" in name or "crispr" in name:
        return "guide_metadata"
    if "target_qc" in name or "cell_qc" in name or name.endswith("_qc.json") or "quality" in name:
        return "qc_summary"
    if "enrich" in name or "gsea" in name or "reactome" in name or "pathway" in name or "go_" in name:
        return "enrichment_table"
    if "predict" in name or "virtual" in name or "scgpt" in name or "gears" in name or "celloracle" in name:
        return "prediction_table"
    if "de" in name or "differential" in name or "rank_genes" in name or "contrast" in name:
        return "measured_de_table"
    if suffix in {".csv", ".tsv", ".json", ".jsonl"}:
        return "structured_table"
    return "other"


def _metadata_from_files(files: list[DetectedFile]) -> dict[str, Any]:
    counts = Counter(file.file_kind for file in files)
    metadata: dict[str, Any] = {
        "n_files": len(files),
        "file_counts_by_kind": dict(sorted(counts.items())),
        "has_anndata": counts.get("anndata", 0) > 0,
        "has_design_manifest": counts.get("design_manifest", 0) > 0,
        "has_guide_metadata": counts.get("guide_metadata", 0) > 0,
        "has_de_table": counts.get("measured_de_table", 0) > 0,
        "has_enrichment_table": counts.get("enrichment_table", 0) > 0,
        "has_prediction_table": counts.get("prediction_table", 0) > 0,
        "has_qc_summary": counts.get("qc_summary", 0) > 0,
    }
    metadata.update(_inspect_anndata_files([file for file in files if file.file_kind == "anndata"]))
    return metadata


def _inspect_anndata_files(files: list[DetectedFile]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "anndata_files": [],
        "obs_columns": [],
        "obs_column_summaries": {},
        "candidate_perturbation_columns": [],
        "candidate_control_values": [],
        "candidate_guide_columns": [],
        "candidate_condition_columns": [],
        "candidate_replicate_columns": [],
        "candidate_batch_columns": [],
        "candidate_qc_columns": [],
        "candidate_state_columns": [],
        "candidate_moi_risk": [],
        "batch_perturbation_confounding": [],
        "x_counts_hint": {},
        "var_columns": [],
        "feature_type_summary": {},
        "layer_keys": [],
        "has_counts_layer": False,
        "has_normalized_layer": False,
        "obsm_keys": [],
        "candidate_embedding_keys": [],
        "uns_keys": [],
        "has_raw": False,
        "anndata_dependency_missing": False,
        "anndata_inspection_errors": [],
    }
    if not files:
        return base

    try:
        anndata = importlib.import_module("anndata")
    except ImportError:
        base["anndata_dependency_missing"] = True
        base["anndata_inspection_errors"].append("anndata dependency is not installed; content preflight skipped")
        return base

    for file in files:
        try:
            adata = anndata.read_h5ad(file.path, backed="r")
            try:
                summary = _inspect_one_anndata(file, adata)
            finally:
                close = getattr(getattr(adata, "file", None), "close", None)
                if callable(close):
                    close()
            _merge_anndata_summary(base, summary)
        except Exception as exc:  # pragma: no cover - exact backend errors vary by h5py/anndata version.
            base["anndata_inspection_errors"].append(f"{file.relative_path}: {type(exc).__name__}: {exc}")
    return base


def _inspect_one_anndata(file: DetectedFile, adata: Any) -> dict[str, Any]:
    obs = adata.obs
    var = adata.var
    obs_columns = [str(column) for column in obs.columns]
    var_columns = [str(column) for column in var.columns]
    layer_keys = [str(key) for key in adata.layers.keys()]
    obsm_keys = [str(key) for key in adata.obsm.keys()]
    uns_keys = [str(key) for key in adata.uns.keys()]
    obs_summaries = {column: _summarize_obs_column(obs[column]) for column in obs_columns}
    feature_type_summary = _feature_type_summary(var)
    return {
        "anndata_files": [
            {
                "path": file.path,
                "relative_path": file.relative_path,
                "n_obs": int(adata.n_obs),
                "n_vars": int(adata.n_vars),
                "is_backed": True,
            }
        ],
        "obs_columns": obs_columns,
        "obs_column_summaries": {file.relative_path: obs_summaries},
        "candidate_perturbation_columns": _candidate_columns(file, obs_summaries, _is_perturbation_column, "perturbation_or_guide"),
        "candidate_control_values": _candidate_control_values(file, obs_summaries),
        "candidate_guide_columns": _candidate_columns(file, obs_summaries, _is_guide_column, "guide_assignment"),
        "candidate_condition_columns": _candidate_columns(file, obs_summaries, _is_condition_column, "condition_or_treatment"),
        "candidate_replicate_columns": _candidate_columns(file, obs_summaries, _is_replicate_column, "replicate_axis"),
        "candidate_batch_columns": _candidate_columns(file, obs_summaries, _is_batch_column, "batch_axis"),
        "candidate_qc_columns": _candidate_columns(file, obs_summaries, _is_qc_column, "cell_qc"),
        "candidate_state_columns": _candidate_columns(file, obs_summaries, _is_state_column, "cell_state_reference"),
        "candidate_moi_risk": _candidate_moi_risk(file, obs_summaries),
        "batch_perturbation_confounding": _batch_perturbation_confounding(file, obs, obs_summaries),
        "x_counts_hint": _x_counts_hint(adata),
        "var_columns": var_columns,
        "feature_type_summary": feature_type_summary,
        "layer_keys": layer_keys,
        "has_counts_layer": any(_contains_token(key, _COUNTS_LAYER_TOKENS) for key in layer_keys) or bool(getattr(adata, "raw", None)) or _x_counts_hint(adata).get("looks_like_counts", False),
        "has_normalized_layer": any(_contains_token(key, _NORMALIZED_LAYER_TOKENS) for key in layer_keys),
        "obsm_keys": obsm_keys,
        "candidate_embedding_keys": [key for key in obsm_keys if _contains_token(key, _EMBEDDING_KEYS)],
        "uns_keys": uns_keys,
        "has_raw": bool(getattr(adata, "raw", None)),
    }


def _summarize_obs_column(series: Any) -> dict[str, Any]:
    non_null = series.dropna()
    sample_values = [str(value) for value in non_null.astype(str).unique()[:_SAMPLE_VALUE_LIMIT]]
    n_unique = int(non_null.astype(str).nunique()) if len(non_null) else 0
    return {
        "dtype": str(series.dtype),
        "n_unique": n_unique,
        "sample_values": sample_values,
    }


def _feature_type_summary(var: Any) -> dict[str, int]:
    for column in ("feature_types", "feature_type", "modality", "gene_biotype"):
        if column in var.columns:
            counts = var[column].dropna().astype(str).value_counts().head(20)
            return {str(key): int(value) for key, value in counts.items()}
    return {}


def _candidate_columns(file: DetectedFile, summaries: dict[str, dict[str, Any]], detector: Any, role: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for column, summary in summaries.items():
        result = detector(column, summary)
        if result is None:
            continue
        confidence, reasons = result
        candidates.append(
            {
                "file": file.relative_path,
                "column": column,
                "role": role,
                "confidence": confidence,
                "reasons": reasons,
                "sample_values": summary.get("sample_values", []),
                "n_unique": summary.get("n_unique"),
            }
        )
    return candidates


def _candidate_control_values(file: DetectedFile, summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for column, summary in summaries.items():
        control_values = [value for value in summary.get("sample_values", []) if _is_control_value(value)]
        if not control_values:
            continue
        values.append(
            {
                "file": file.relative_path,
                "column": column,
                "values": control_values,
                "confidence": 0.9 if _contains_token(column, _PERTURBATION_COLUMN_TOKENS + _GUIDE_COLUMN_TOKENS + _CONDITION_COLUMN_TOKENS) else 0.7,
                "reasons": ["values match negative-control, vehicle, or untreated aliases"],
            }
        )
    return values


def _candidate_moi_risk(file: DetectedFile, summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for column, summary in summaries.items():
        guide_like = _is_guide_column(column, summary) is not None or _is_perturbation_column(column, summary) is not None
        if not guide_like:
            continue
        sample_values = [str(value) for value in summary.get("sample_values", [])]
        multi_value_examples = [value for value in sample_values if _looks_multi_guide_value(value)]
        combo_examples = [value for value in sample_values if "__" in value]
        if multi_value_examples or combo_examples:
            risks.append(
                {
                    "file": file.relative_path,
                    "column": column,
                    "risk": "possible_high_moi_or_combinatorial_assignment",
                    "confidence": 0.75,
                    "examples": (multi_value_examples + combo_examples)[:_SAMPLE_VALUE_LIMIT],
                    "reasons": ["sample values contain multi-guide delimiters or combinatorial perturbation labels"],
                }
            )
        else:
            risks.append(
                {
                    "file": file.relative_path,
                    "column": column,
                    "risk": "low_moi_candidate_unvalidated",
                    "confidence": 0.45,
                    "examples": sample_values[: min(3, len(sample_values))],
                    "reasons": ["guide-like values are present but MOI requires guide calling or assignment validation"],
                }
            )
    return risks


def _batch_perturbation_confounding(file: DetectedFile, obs: Any, summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    perturbation_columns = [item["column"] for item in _candidate_columns(file, summaries, _is_perturbation_column, "perturbation_or_guide")]
    batch_columns = [item["column"] for item in _candidate_columns(file, summaries, _is_batch_column, "batch_axis")]
    flags: list[dict[str, Any]] = []
    for perturbation_column in perturbation_columns:
        for batch_column in batch_columns:
            if perturbation_column == batch_column:
                continue
            crosstab = _safe_crosstab(obs, perturbation_column, batch_column)
            if not crosstab:
                continue
            total_pairs = len(crosstab["nonzero_pairs"])
            n_perturbations = crosstab["n_perturbations"]
            n_batches = crosstab["n_batches"]
            nested = total_pairs <= max(n_perturbations, n_batches)
            status = "possible_batch_perturbation_confounding" if nested else "batch_perturbation_crossing_detected"
            flags.append(
                {
                    "file": file.relative_path,
                    "perturbation_column": perturbation_column,
                    "batch_column": batch_column,
                    "status": status,
                    "confidence": 0.75 if nested else 0.45,
                    "n_perturbations": n_perturbations,
                    "n_batches": n_batches,
                    "nonzero_pairs": crosstab["nonzero_pairs"][:20],
                    "reasons": ["batch x perturbation crosstab was computed from obs metadata"],
                }
            )
    return flags


def _safe_crosstab(obs: Any, perturbation_column: str, batch_column: str) -> dict[str, Any] | None:
    try:
        grouped = obs.groupby([perturbation_column, batch_column], observed=True).size()
    except Exception:
        return None
    nonzero_pairs = [
        {"perturbation": str(index[0]), "batch": str(index[1]), "n_cells": int(value)}
        for index, value in grouped.items()
        if int(value) > 0
    ]
    if not nonzero_pairs:
        return None
    return {
        "n_perturbations": int(obs[perturbation_column].dropna().astype(str).nunique()),
        "n_batches": int(obs[batch_column].dropna().astype(str).nunique()),
        "nonzero_pairs": nonzero_pairs,
    }


def _x_counts_hint(adata: Any) -> dict[str, Any]:
    try:
        matrix = adata.X[: min(int(adata.n_obs), 20), : min(int(adata.n_vars), 20)]
        if hasattr(matrix, "toarray"):
            matrix = matrix.toarray()
        numpy = importlib.import_module("numpy")
        array = numpy.asarray(matrix)
        if array.size == 0:
            return {"checked": True, "looks_like_counts": False, "reason": "empty sampled matrix"}
        finite = array[numpy.isfinite(array)]
        if finite.size == 0:
            return {"checked": True, "looks_like_counts": False, "reason": "sampled matrix has no finite values"}
        nonnegative = bool((finite >= 0).all())
        integer_like = bool(numpy.allclose(finite, numpy.round(finite), atol=1e-8))
        return {
            "checked": True,
            "looks_like_counts": bool(nonnegative and integer_like),
            "sample_shape": [int(array.shape[0]), int(array.shape[1])],
            "nonnegative": nonnegative,
            "integer_like": integer_like,
            "reason": "sampled .X values are nonnegative and integer-like" if nonnegative and integer_like else "sampled .X values are not count-like",
        }
    except Exception as exc:
        return {"checked": False, "looks_like_counts": False, "reason": f"could not sample .X: {type(exc).__name__}: {exc}"}


def _looks_multi_guide_value(value: str) -> bool:
    text = str(value).strip()
    lower = text.lower()
    if "__" in text:
        return True
    if any(delimiter in text for delimiter in (";", ",", "|", "+")) and any(token in lower for token in ("sg", "grna", "sgrna", "negctrl", "ntc")):
        return True
    return bool(re.search(r"\b(?:sg\w+|NegCtrl\w*|NTC\w*)\s*(?:[;,|+]\s*(?:sg\w+|NegCtrl\w*|NTC\w*))+", text, re.IGNORECASE))

def _merge_anndata_summary(base: dict[str, Any], summary: dict[str, Any]) -> None:
    for key in ("anndata_files", "candidate_perturbation_columns", "candidate_control_values", "candidate_guide_columns", "candidate_condition_columns", "candidate_replicate_columns", "candidate_batch_columns", "candidate_qc_columns", "candidate_state_columns", "candidate_moi_risk", "batch_perturbation_confounding"):
        base[key].extend(summary.get(key, []))
    for key in ("obs_columns", "var_columns", "layer_keys", "obsm_keys", "candidate_embedding_keys", "uns_keys"):
        base[key] = sorted(set([*base[key], *summary.get(key, [])]))
    base["obs_column_summaries"].update(summary.get("obs_column_summaries", {}))
    base["feature_type_summary"] = _merge_count_maps(base["feature_type_summary"], summary.get("feature_type_summary", {}))
    base["has_counts_layer"] = bool(base["has_counts_layer"] or summary.get("has_counts_layer"))
    base["has_normalized_layer"] = bool(base["has_normalized_layer"] or summary.get("has_normalized_layer"))
    base["has_raw"] = bool(base["has_raw"] or summary.get("has_raw"))
    if summary.get("x_counts_hint"):
        base["x_counts_hint"] = summary["x_counts_hint"]


def _merge_count_maps(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    for key, value in right.items():
        merged[key] = int(merged.get(key, 0)) + int(value)
    return dict(sorted(merged.items()))


def _is_perturbation_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    reasons: list[str] = []
    if _contains_token(column, _PERTURBATION_COLUMN_TOKENS):
        reasons.append("column name resembles perturbation, guide, target, or condition metadata")
    if any(_looks_like_guide_or_perturbation_value(value) for value in summary.get("sample_values", [])):
        reasons.append("sample values resemble guide or perturbation labels")
    if not reasons:
        return None
    return (0.85 if len(reasons) > 1 else 0.65, reasons)


def _is_guide_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    reasons: list[str] = []
    if _contains_token(column, _GUIDE_COLUMN_TOKENS):
        reasons.append("column name resembles guide assignment metadata")
    if any(_looks_like_guide_or_perturbation_value(value) for value in summary.get("sample_values", [])):
        reasons.append("sample values resemble guide labels")
    if not reasons:
        return None
    return (0.9 if len(reasons) > 1 else 0.7, reasons)


def _is_condition_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    if not _contains_token(column, _CONDITION_COLUMN_TOKENS):
        return None
    return (0.75, ["column name resembles treatment or condition metadata"])


def _is_replicate_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    if not _contains_token(column, _REPLICATE_COLUMN_TOKENS):
        return None
    return (0.8, ["column name resembles donor, sample, lane, library, or replicate metadata"])


def _is_batch_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    if not _contains_token(column, _BATCH_COLUMN_TOKENS):
        return None
    return (0.75, ["column name resembles batch metadata"])


def _is_qc_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    if not _contains_token(column, _QC_COLUMN_TOKENS):
        return None
    return (0.8, ["column name resembles cell-level QC metadata"])


def _is_state_column(column: str, summary: dict[str, Any]) -> tuple[float, list[str]] | None:
    if not _contains_token(column, _STATE_COLUMN_TOKENS):
        return None
    return (0.8, ["column name resembles cluster, cell type, or cell-state annotation"])


def _contains_token(value: str, tokens: tuple[str, ...]) -> bool:
    normalized = _normalize(value)
    return any(_normalize(token) in normalized for token in tokens)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _is_control_value(value: str) -> bool:
    raw = str(value).strip()
    normalized = _normalize(raw).replace("_", "")
    if normalized.startswith(("sgntc", "sgnegctrl", "sgnontargeting", "sgnontarget", "sgsafetargeting", "sgnegativecontrol")):
        return True
    return bool(_CONTROL_VALUE_RE.search(raw))


def _looks_like_guide_or_perturbation_value(value: str) -> bool:
    text = str(value).strip()
    return bool(_GUIDE_VALUE_RE.search(text)) or _is_control_value(text)


def _candidate_from_file(file: DetectedFile) -> EvidenceCandidate | None:
    registrar_by_kind = {
        "design_manifest": "register_scope_artifact",
        "guide_metadata": "register_eligibility_artifact",
        "qc_summary": "register_eligibility_artifact",
        "measured_de_table": "register_measured_effect_artifact",
        "enrichment_table": "register_prior_artifact",
        "prediction_table": "register_prediction_artifact",
        "anndata": "preflight_metadata_only",
        "seurat": "preflight_metadata_only",
    }
    subtype_by_kind = {
        "design_manifest": "perturbation_design_manifest",
        "guide_metadata": "guide_metadata",
        "qc_summary": "qc_summary",
        "measured_de_table": "measured_de",
        "enrichment_table": "curated_enrichment_result",
        "prediction_table": "predicted_effect",
        "anndata": "anndata_metadata",
        "seurat": "seurat_metadata",
    }
    registrar = registrar_by_kind.get(file.file_kind)
    if registrar is None:
        return None
    unresolved = _unresolved_fields(file.file_kind)
    return EvidenceCandidate(
        candidate_id=candidate_id_for(file.relative_path, file.file_kind),
        source_path=file.path,
        relative_path=file.relative_path,
        candidate_kind=file.file_kind,
        artifact_subtype=subtype_by_kind.get(file.file_kind),
        suggested_registrar=registrar,
        uid_linked=False,
        validator_passed=False,
        ambiguous=True,
        confidence=0.5,
        unresolved_fields=unresolved,
        reasons=[
            "harvester candidate only; validator pass is required before evidence registration",
            "candidate confidence is diagnostic and cannot raise claim strength",
        ],
        metadata={"size_bytes": file.size_bytes, "suffix": file.suffix},
    )


def _content_candidates_from_metadata(metadata: dict[str, Any]) -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []
    candidates.extend(_candidate_for_column_group(metadata, "candidate_perturbation_columns", "perturbation_assignment_candidate", "perturbation_design_manifest", "preflight_metadata_only", ["validator-pass manifest construction", "guide-to-target map", "control definition"]))
    candidates.extend(_candidate_for_column_group(metadata, "candidate_control_values", "control_definition_candidate", "control_definition", "preflight_metadata_only", ["validated control role", "design manifest UID binding"]))
    candidates.extend(_candidate_for_column_group(metadata, "candidate_replicate_columns", "replicate_scope_candidate", "replicate_scope", "preflight_metadata_only", ["replicate independence policy", "claim-level replicate interpretation"]))
    candidates.extend(_candidate_for_column_group(metadata, "candidate_qc_columns", "cell_qc_candidate", "cell_qc", "preflight_metadata_only", ["registered cell QC policy", "QC pass/fail thresholds"]))
    candidates.extend(_candidate_for_column_group(metadata, "candidate_state_columns", "cell_state_reference_candidate", "cell_state_reference", "preflight_metadata_only", ["registered state reference summary", "annotation or marker provenance"]))
    return candidates


def _candidate_for_column_group(metadata: dict[str, Any], key: str, candidate_kind: str, artifact_subtype: str, registrar: str, unresolved: list[str]) -> list[EvidenceCandidate]:
    items = metadata.get(key, [])
    candidates: list[EvidenceCandidate] = []
    for item in items:
        column = item.get("column", "unknown")
        relative_path = item.get("file", "anndata")
        candidates.append(
            EvidenceCandidate(
                candidate_id=candidate_id_for(f"{relative_path}:{column}:{candidate_kind}", candidate_kind),
                source_path=relative_path,
                relative_path=relative_path,
                candidate_kind=candidate_kind,
                artifact_subtype=artifact_subtype,
                suggested_registrar=registrar,
                uid_linked=False,
                validator_passed=False,
                ambiguous=True,
                confidence=item.get("confidence"),
                unresolved_fields=list(unresolved),
                reasons=[
                    "preflight detection is diagnostic only",
                    "validator pass is required before evidence registration",
                    *list(item.get("reasons", [])),
                ],
                metadata={"column": column, "role": item.get("role"), "sample_values": item.get("sample_values", []), "detected_values": item.get("values", [])},
            )
        )
    return candidates


def _unresolved_fields(file_kind: str) -> list[str]:
    if file_kind == "design_manifest":
        return ["manifest_schema_validation"]
    if file_kind == "guide_metadata":
        return ["design_manifest_id", "perturbation_uid", "control_uid"]
    if file_kind == "qc_summary":
        return ["design_manifest_id", "scope_uid", "qc_policy"]
    if file_kind == "measured_de_table":
        return ["design_manifest_id", "contrast_uid", "method", "multiple_testing", "eligibility_profile"]
    if file_kind == "enrichment_table":
        return ["input_measured_artifact_id", "database_version", "background_universe"]
    if file_kind == "prediction_table":
        return ["model_name", "model_version", "prediction_scope", "model_provenance"]
    if file_kind == "anndata":
        return ["content-derived candidates require validator pass"]
    return ["structured_validator"]


def _readiness(metadata: dict[str, Any]) -> dict[str, ReadinessEntry]:
    has_files = bool(metadata.get("n_files"))
    has_manifest = bool(metadata.get("has_design_manifest"))
    has_guide_metadata = bool(metadata.get("has_guide_metadata"))
    has_de = bool(metadata.get("has_de_table"))
    has_qc_summary = bool(metadata.get("has_qc_summary"))
    has_prediction = bool(metadata.get("has_prediction_table"))
    has_anndata = bool(metadata.get("has_anndata"))
    has_content_perturbation = bool(metadata.get("candidate_perturbation_columns"))
    has_content_guide = bool(metadata.get("candidate_guide_columns"))
    has_content_control = bool(metadata.get("candidate_control_values"))
    has_content_qc = bool(metadata.get("candidate_qc_columns"))
    has_content_state = bool(metadata.get("candidate_state_columns") or metadata.get("candidate_embedding_keys"))
    has_content_replicates = bool(metadata.get("candidate_replicate_columns"))
    has_layers = bool(metadata.get("has_counts_layer") or metadata.get("has_normalized_layer"))
    has_moi_risk = bool(metadata.get("candidate_moi_risk"))
    has_batch_confounding = any(item.get("status") == "possible_batch_perturbation_confounding" for item in metadata.get("batch_perturbation_confounding", []))
    has_any_guide = has_guide_metadata or has_content_guide
    has_any_qc = has_qc_summary or has_content_qc
    has_any_perturbation = has_manifest or has_content_perturbation
    notes = _global_readiness_notes(metadata)
    return {
        "observation": ReadinessEntry(
            claim_type="observation",
            status="ready" if has_files else "not_ready",
            missing=[] if has_files else ["input files"],
            notes=notes,
        ),
        "perturbation_design_manifest": ReadinessEntry(
            claim_type="perturbation_design_manifest",
            status="maybe" if has_any_perturbation else "not_ready",
            missing=_missing(
                {
                    "perturbation or condition column": has_any_perturbation,
                    "negative-control or vehicle labels": has_content_control or has_manifest,
                    "guide-to-target map": has_guide_metadata,
                }
            ),
            notes=["preflight candidates do not create a validated PerturbationDesignManifest"],
        ),
        "guide_assignment": ReadinessEntry(
            claim_type="guide_assignment",
            status="maybe" if has_any_guide else "not_ready",
            missing=_missing(
                {
                    "guide or protospacer column": has_any_guide,
                    "guide-to-target map": has_guide_metadata,
                    "registered DesignManifest UID scope": has_manifest,
                }
            ),
        ),
        "cell_qc": ReadinessEntry(
            claim_type="cell_qc",
            status="maybe" if has_any_qc else "not_ready",
            missing=_missing(
                {
                    "cell-level QC columns": has_any_qc,
                    "registered QC policy and thresholds": has_qc_summary,
                }
            ),
        ),
        "cell_state_reference": ReadinessEntry(
            claim_type="cell_state_reference",
            status="maybe" if has_content_state else "not_ready",
            missing=_missing(
                {
                    "state annotation or embedding": has_content_state,
                    "registered state reference artifact": False,
                }
            ),
        ),
        "measured_de": ReadinessEntry(
            claim_type="measured_de",
            status="maybe" if (has_de or (has_any_perturbation and has_content_control and has_layers)) else "not_ready",
            missing=_missing(
                {
                    "registered DesignManifest UID scope": has_manifest,
                    "registered guide assignment": has_guide_metadata,
                    "negative-control or vehicle labels": has_content_control or has_manifest,
                    "registered target/cell QC eligibility": has_qc_summary,
                    "measured DE artifact": has_de,
                    "count or normalized expression layer": has_layers,
                    "MOI risk review": not has_moi_risk,
                    "batch-perturbation confounding review": not has_batch_confounding,
                }
            ),
            notes=["measured strength requires registered evidence and warrant evaluation, not preflight detection"],
        ),
        "target_engagement": ReadinessEntry(
            claim_type="target_engagement",
            status="maybe" if has_any_perturbation and (has_any_guide or has_anndata) else "not_ready",
            missing=_missing(
                {
                    "perturbation modality": has_manifest,
                    "guide or treatment assignment": has_any_guide or has_content_perturbation,
                    "target expression or effect metadata": False,
                    "registered target QC eligibility": has_qc_summary,
                }
            ),
        ),
        "composition_effect": ReadinessEntry(
            claim_type="composition_effect",
            status="maybe" if has_content_state and has_any_perturbation else "not_ready",
            missing=_missing(
                {
                    "cell-state reference": has_content_state,
                    "perturbation/control assignment": has_any_perturbation and has_content_control,
                    "registered composition-effect artifact": False,
                }
            ),
        ),
        "global_effect": ReadinessEntry(
            claim_type="global_effect",
            status="maybe" if metadata.get("candidate_embedding_keys") and has_any_perturbation else "not_ready",
            missing=_missing(
                {
                    "embedding or feature space": bool(metadata.get("candidate_embedding_keys")),
                    "perturbation/control assignment": has_any_perturbation and has_content_control,
                    "registered global-effect artifact": False,
                }
            ),
        ),
        "module_effect": ReadinessEntry(
            claim_type="module_effect",
            status="maybe" if has_layers and has_any_perturbation else "not_ready",
            missing=_missing(
                {
                    "expression layer for scoring": has_layers,
                    "module definition or gene set": False,
                    "perturbation/control assignment": has_any_perturbation and has_content_control,
                }
            ),
        ),
        "prediction_concordance": ReadinessEntry(
            claim_type="prediction_concordance",
            status="maybe" if has_prediction and (has_de or has_any_perturbation) else "not_ready",
            missing=_missing(
                {
                    "prediction artifact": has_prediction,
                    "compatible measured artifact": has_de,
                    "model provenance": False,
                }
            ),
        ),
        "replication": ReadinessEntry(
            claim_type="replication",
            status="maybe" if has_content_replicates else "not_ready",
            missing=_missing(
                {
                    "donor/sample replicate metadata": has_content_replicates,
                    "replication rule": False,
                    "multiple compatible measured artifacts": False,
                }
            ),
        ),
        "mechanism": ReadinessEntry(
            claim_type="mechanism",
            status="blocked",
            missing=["validated_mechanism policy is disabled", "orthogonal validation or rescue evidence"],
        ),
    }


def _missing(items: dict[str, bool]) -> list[str]:
    return [item for item, present in items.items() if not present]


def _global_readiness_notes(metadata: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if metadata.get("anndata_dependency_missing"):
        notes.append("anndata dependency is not installed; content preflight skipped")
    if metadata.get("anndata_inspection_errors"):
        notes.extend(str(item) for item in metadata["anndata_inspection_errors"])
    return notes
