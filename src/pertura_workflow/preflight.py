from __future__ import annotations

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


def preflight_workspace(workspace: str | Path, *, mode: str = "benchmark", max_files: int = _MAX_FILES) -> PreflightReport:
    root = Path(workspace).resolve()
    detected = _detect_files(root, max_files=max_files)
    metadata = _metadata_from_files(detected)
    candidates = [_candidate_from_file(file) for file in detected if _candidate_from_file(file) is not None]
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
    return {
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
        uid_linked=file.file_kind == "design_manifest",
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
    return ["structured_validator"]


def _readiness(metadata: dict[str, Any]) -> dict[str, ReadinessEntry]:
    has_files = bool(metadata.get("n_files"))
    has_manifest = bool(metadata.get("has_design_manifest"))
    has_guide = bool(metadata.get("has_guide_metadata"))
    has_de = bool(metadata.get("has_de_table"))
    has_qc = bool(metadata.get("has_qc_summary"))
    has_prediction = bool(metadata.get("has_prediction_table"))
    return {
        "observation": ReadinessEntry(
            claim_type="observation",
            status="ready" if has_files else "not_ready",
            missing=[] if has_files else ["input files"],
        ),
        "measured_de": ReadinessEntry(
            claim_type="measured_de",
            status="maybe" if has_de else "not_ready",
            missing=[
                item
                for item, present in {
                    "DesignManifest UID scope": has_manifest,
                    "guide metadata or assignment": has_guide,
                    "target/cell QC eligibility": has_qc,
                    "measured DE table": has_de,
                }.items()
                if not present
            ],
            notes=["measured strength requires validator-pass evidence, not file-name detection"],
        ),
        "target_engagement": ReadinessEntry(
            claim_type="target_engagement",
            status="maybe" if has_guide and has_qc else "not_ready",
            missing=[
                item
                for item, present in {
                    "perturbation modality": has_manifest,
                    "guide or treatment assignment": has_guide,
                    "target QC or effect metadata": has_qc,
                }.items()
                if not present
            ],
        ),
        "replication": ReadinessEntry(
            claim_type="replication",
            status="not_ready",
            missing=["independent replicate axis", "replication rule", "multiple compatible measured artifacts"],
        ),
        "mechanism": ReadinessEntry(
            claim_type="mechanism",
            status="not_ready",
            missing=["validated_mechanism policy is disabled", "orthogonal validation or rescue evidence"],
        ),
        "prediction_concordance": ReadinessEntry(
            claim_type="prediction_concordance",
            status="maybe" if has_prediction and has_de else "not_ready",
            missing=[
                item
                for item, present in {
                    "prediction artifact": has_prediction,
                    "compatible measured artifact": has_de,
                    "model provenance": False,
                }.items()
                if not present
            ],
        ),
    }
