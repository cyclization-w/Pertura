from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from pertura_gate.identity.design_manifest import build_guide_label_manifest, build_treatment_condition_manifest, scope_for_raw_label
from pertura_gate.core.schema import ArtifactKind, ArtifactRole, EvidenceArtifact, EvidenceClass


class EvidenceRegistry:
    """Append-only run-local evidence artifact registry."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_run(cls, run_root: Path) -> "EvidenceRegistry":
        return cls(Path(run_root) / "artifacts" / "evidence_artifacts.jsonl")

    @property
    def run_root(self) -> Path:
        if self.path.parent.name.lower() == "artifacts":
            return self.path.parent.parent
        return self.path.parent

    def list(self) -> list[EvidenceArtifact]:
        if not self.path.exists():
            return []
        artifacts: list[EvidenceArtifact] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            artifact = EvidenceArtifact.from_dict(json.loads(line))
            artifacts.append(replace(artifact, scope=_canonicalize_scope_aliases(artifact.scope)))
        return artifacts

    def get(self, artifact_id: str) -> EvidenceArtifact | None:
        for artifact in reversed(self.list()):
            if artifact.artifact_id == artifact_id:
                return artifact
        return None

    def get_by_id_or_path(self, ref: str) -> EvidenceArtifact | None:
        needle = _normalize_ref(ref)
        basename_matches: list[EvidenceArtifact] = []
        for artifact in reversed(self.list()):
            if artifact.artifact_id == ref:
                return artifact
            artifact_path = _normalize_ref(artifact.path)
            if artifact_path == needle:
                return artifact
            if "/" not in needle and artifact_path.rsplit("/", 1)[-1].lower() == needle.lower():
                basename_matches.append(artifact)
        if len(basename_matches) == 1:
            return basename_matches[0]
        return None

    def register_perturbation_design_manifest(
        self,
        *,
        path: str | Path,
        adapter_name: str = "guide_label_v1",
        dataset_id: str | None = None,
        source_column: str | None = None,
        raw_labels: list[str] | None = None,
        conditions: list[dict | str] | None = None,
        guide_to_target_map: dict | None = None,
        provenance_level: str = "deterministic_rule",
        artifact_id: str | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        manifest_id = artifact_id or f"design_manifest_{uuid4().hex[:12]}"
        adapter = adapter_name or "guide_label_v1"
        if adapter == "treatment_condition_v1":
            manifest = build_treatment_condition_manifest(
                manifest_id=manifest_id,
                conditions=list(conditions or []),
                dataset_id=dataset_id,
                source_column=source_column or "condition",
                provenance_level=provenance_level,
            )
        else:
            manifest = build_guide_label_manifest(
                manifest_id=manifest_id,
                raw_labels=[str(item) for item in (raw_labels or [])],
                dataset_id=dataset_id,
                source_column=source_column or "guide_identity",
                guide_to_target_map={str(key): str(value) for key, value in (guide_to_target_map or {}).items()},
                provenance_level=provenance_level,
            )
        manifest_payload = manifest.to_dict()
        artifact = EvidenceArtifact(
            artifact_id=manifest_id,
            kind=ArtifactKind.perturbation_design_manifest,
            evidence_class=EvidenceClass.observed_metadata,
            artifact_roles=[ArtifactRole.scope_definition],
            path=path_text,
            notes=notes,
            scope={key: value for key, value in {"dataset_id": dataset_id, "design_manifest_id": manifest_id}.items() if value not in (None, "")},
            quality={
                "adapter_name": manifest.adapter_name,
                "adapter_version": manifest.adapter_version,
                "n_perturbation_identities": len(manifest.perturbations),
                "n_contrasts": len(manifest.contrasts),
                "provenance_level": provenance_level,
            },
            provenance={"created_by_tool": "register_perturbation_design_manifest"},
            source_sha256=self._hash_for_path(path_text),
            metadata={"manifest": manifest_payload, **dict(metadata or {})},
        )
        self.append(artifact)
        return artifact

    def get_design_manifest(self, manifest_id: str) -> dict | None:
        artifact = self.get(manifest_id)
        if artifact is None or artifact.kind != ArtifactKind.perturbation_design_manifest:
            return None
        manifest = artifact.metadata.get("manifest")
        return dict(manifest) if isinstance(manifest, dict) else None

    def resolve_manifest_scope(self, scope: dict | None) -> dict:
        data = _canonicalize_scope_aliases(dict(scope or {}))
        manifest_id = data.get("design_manifest_id")
        if not manifest_id:
            return data
        manifest = self.get_design_manifest(str(manifest_id))
        if not manifest:
            return data
        if data.get("perturbation_uid") or data.get("contrast_uid"):
            return data
        raw = data.get("raw_label") or data.get("raw_perturbation_label") or data.get("raw_condition_label")
        if raw is None:
            return data
        mapped = scope_for_raw_label(manifest, str(raw))
        return _canonicalize_scope_aliases({**data, **mapped})
    def register_experiment_design(
        self,
        *,
        path: str | Path,
        assay: str | None = None,
        perturbation_modality: str | None = None,
        guide_capture: str | None = None,
        moi: str | int | float | None = None,
        controls: dict | None = None,
        replication: dict | None = None,
        loading_doublet_policy: str | None = None,
        timepoint: str | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        controls_dict = dict(controls or {})
        eligibility = {
            "assay_modality": assay,
            "perturbation_modality": perturbation_modality,
            "moi": moi,
            "control_definition": controls_dict,
            "replicate_scope": dict(replication or {}),
            "perturbation_scope": {
                key: value for key, value in {
                    "guide_capture": guide_capture,
                    "timepoint": timepoint,
                    **dict(scope or {}),
                }.items() if value not in (None, "")
            },
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"experiment_design_{uuid4().hex[:12]}",
            kind=ArtifactKind.experiment_design,
            evidence_class=EvidenceClass.observed_metadata,
            artifact_roles=[ArtifactRole.scope_definition, ArtifactRole.analysis_eligibility],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            quality={
                key: value for key, value in {
                    "assay": assay,
                    "perturbation_modality": perturbation_modality,
                    "guide_capture": guide_capture,
                    "moi": moi,
                    "loading_doublet_policy": loading_doublet_policy,
                    "timepoint": timepoint,
                    **dict(quality or {}),
                }.items() if value not in (None, "")
            },
            eligibility=_clean_nested(eligibility),
            provenance={"created_by_tool": "register_experiment_design_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_guide_assignment(
        self,
        *,
        path: str | Path,
        assignment_method: str | None = None,
        assigned_count: int | None = None,
        unassigned_count: int | None = None,
        multi_guide_count: int | None = None,
        guide_distribution: dict | None = None,
        ambient_guide_handling: str | None = None,
        moi_inference: str | int | float | None = None,
        target_summary: dict | None = None,
        guide_to_target_map_hash: str | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        mapping = {
            "assignment_method": assignment_method,
            "assigned_count": assigned_count,
            "unassigned_count": unassigned_count,
            "multi_guide_count": multi_guide_count,
            "guide_distribution": dict(guide_distribution or {}),
            "ambient_guide_handling": ambient_guide_handling,
            "moi_inference": moi_inference,
            "target_summary": dict(target_summary or {}),
            "guide_to_target_map_hash": guide_to_target_map_hash,
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"guide_assignment_{uuid4().hex[:12]}",
            kind=ArtifactKind.guide_assignment,
            evidence_class=EvidenceClass.observed_metadata,
            artifact_roles=[ArtifactRole.analysis_eligibility],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            quality={key: value for key, value in {**mapping, **dict(quality or {})}.items() if value not in (None, "")},
            eligibility={"perturbation_cell_mapping": _clean_nested(mapping), "moi": moi_inference},
            provenance={"created_by_tool": "register_guide_assignment_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_target_qc(
        self,
        *,
        path: str | Path,
        target: str | None = None,
        control: str | None = None,
        n_target_cells: int | None = None,
        n_control_cells: int | None = None,
        guides_per_target: int | None = None,
        cells_per_guide: dict | None = None,
        guide_consistency: str | None = None,
        control_calibration: dict | None = None,
        min_cell_policy: str | None = None,
        batch_coverage: dict | None = None,
        donor_coverage: dict | None = None,
        estimand: str | None = None,
        model_covariates: list[str] | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        target_qc = {
            "target": target,
            "control": control,
            "n_target_cells": n_target_cells,
            "n_control_cells": n_control_cells,
            "guides_per_target": guides_per_target,
            "cells_per_guide": dict(cells_per_guide or {}),
            "guide_consistency": guide_consistency,
            "min_cell_policy": min_cell_policy,
            "batch_coverage": dict(batch_coverage or {}),
            "donor_coverage": dict(donor_coverage or {}),
            "model_covariates": list(model_covariates or []),
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"target_qc_{uuid4().hex[:12]}",
            kind=ArtifactKind.target_qc,
            evidence_class=EvidenceClass.observed_metadata,
            artifact_roles=[ArtifactRole.analysis_eligibility],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope({key: value for key, value in {"perturbation": target, "control": control, **dict(scope or {})}.items() if value not in (None, "")}),
            quality={key: value for key, value in {**target_qc, **dict(quality or {})}.items() if value not in (None, "")},
            eligibility={
                "target_qc": _clean_nested(target_qc),
                "control_definition": _clean_nested({"control_label": control}),
                "control_calibration": dict(control_calibration or {}),
                "estimand": estimand,
            },
            provenance={"created_by_tool": "register_target_qc_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_measured_de(
        self,
        *,
        path: str | Path,
        contrast_left: str | None,
        contrast_baseline: str | None,
        method: str | None,
        n_left: int | None,
        n_baseline: int | None,
        multiple_testing: str | None,
        has_padj: bool,
        columns: list[str] | None = None,
        source_data: str | None = None,
        notes: str | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        eligibility: dict | None = None,
        metadata: dict | None = None,
        code_sha256: str | None = None,
        execution_hash: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        merged_scope = {
            "dataset_id": source_data,
            "perturbation": contrast_left,
            "control": contrast_baseline,
            "contrast": _contrast_label(contrast_left, contrast_baseline),
            **_scope_identity_from_eligibility(eligibility),
            **dict(scope or {}),
        }
        merged_quality = {
            "n_treated": n_left,
            "n_control": n_baseline,
            "method": method,
            "correction": multiple_testing,
            "has_padj": has_padj,
            **dict(quality or {}),
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"measured_de_{uuid4().hex[:12]}",
            kind=ArtifactKind.measured_de,
            evidence_class=EvidenceClass.measured,
            artifact_roles=[ArtifactRole.effect_evidence],
            path=path_text,
            contrast_left=contrast_left,
            contrast_baseline=contrast_baseline,
            method=method,
            n_left=n_left,
            n_baseline=n_baseline,
            multiple_testing=multiple_testing,
            has_padj=has_padj,
            columns=list(columns or []),
            source_data=source_data,
            notes=notes,
            scope=self.resolve_manifest_scope({key: value for key, value in merged_scope.items() if value not in (None, "")}),
            predicate={"relation": "changes_expression", **dict(predicate or {})},
            quality={key: value for key, value in merged_quality.items() if value not in (None, "")},
            eligibility=dict(eligibility or {}),
            provenance={"created_by_tool": "register_measured_de_artifact"},
            source_sha256=self._hash_for_path(path_text),
            code_sha256=code_sha256,
            execution_hash=execution_hash,
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_predicted_effect(
        self,
        *,
        path: str | Path,
        model_name: str | None,
        model_version: str | None = None,
        prediction_method: str | None = None,
        perturbation: str | None = None,
        target_context: str | None = None,
        readout_type: str | None = None,
        target: str | None = None,
        notes: str | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"predicted_effect_{uuid4().hex[:12]}",
            kind=ArtifactKind.predicted_effect,
            evidence_class=EvidenceClass.predicted,
            artifact_roles=[ArtifactRole.prediction_evidence],
            path=path_text,
            notes=notes,
            scope={
                key: value for key, value in {
                    "perturbation": perturbation,
                    "cell_type": target_context,
                    **dict(scope or {}),
                }.items() if value not in (None, "")
            },
            predicate={
                key: value for key, value in {
                    "relation": "predicts_effect",
                    "target": target,
                    "readout_type": readout_type,
                    **dict(predicate or {}),
                }.items() if value not in (None, "")
            },
            quality={
                key: value for key, value in {
                    "model_name": model_name,
                    "model_version": model_version,
                    "prediction_method": prediction_method,
                    **dict(quality or {}),
                }.items() if value not in (None, "")
            },
            provenance={"created_by_tool": "register_predicted_effect_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_curated_prior(
        self,
        *,
        path: str | Path,
        database: str | None,
        database_version: str | None = None,
        term_id: str | None = None,
        term_name: str | None = None,
        target: str | None = None,
        notes: str | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"curated_prior_{uuid4().hex[:12]}",
            kind=ArtifactKind.curated_prior_lookup,
            evidence_class=EvidenceClass.curated_prior,
            artifact_roles=[ArtifactRole.prior_context],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            predicate={
                key: value for key, value in {
                    "relation": "curated_prior_support",
                    "target": target,
                    "term_id": term_id,
                    "term_name": term_name,
                    **dict(predicate or {}),
                }.items() if value not in (None, "")
            },
            quality={
                key: value for key, value in {
                    "database": database,
                    "database_version": database_version,
                    **dict(quality or {}),
                }.items() if value not in (None, "")
            },
            provenance={"created_by_tool": "register_curated_prior_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_replication(
        self,
        *,
        measured_artifact_ids: list[str],
        replication_type: str | None,
        notes: str | None = None,
        artifact_id: str | None = None,
        metadata: dict | None = None,
    ) -> EvidenceArtifact:
        resolved = [artifact for artifact_id in measured_artifact_ids if (artifact := self.get(artifact_id)) is not None]
        missing = [artifact_id for artifact_id in measured_artifact_ids if self.get(artifact_id) is None]
        artifact_id = artifact_id or f"replication_{uuid4().hex[:12]}"
        artifact = EvidenceArtifact(
            artifact_id=artifact_id,
            kind=ArtifactKind.replication_summary,
            evidence_class=EvidenceClass.composite_summary,
            artifact_roles=[ArtifactRole.effect_evidence],
            path=f"artifacts/{artifact_id}.json",
            notes=notes,
            scope=_shared_scope(resolved),
            predicate=_shared_predicate(resolved),
            quality={
                "replication_type": replication_type,
                "measured_artifact_ids": list(measured_artifact_ids),
                "resolved_artifact_ids": [artifact.artifact_id for artifact in resolved],
                "missing_artifact_ids": missing,
            },
            provenance={"created_by_tool": "register_replication_artifact"},
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_perturbation_efficiency(
        self,
        *,
        path: str | Path,
        perturbation: str | None = None,
        target_gene: str | None = None,
        modality: str | None = None,
        expected_direction: str | None = None,
        observed_direction: str | None = None,
        effect_size: float | None = None,
        pvalue: float | None = None,
        padj: float | None = None,
        method: str | None = None,
        n_target_cells: int | None = None,
        n_control_cells: int | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        merged_scope = {
            "perturbation": perturbation,
            "target": target_gene,
            **dict(scope or {}),
        }
        merged_quality = {
            "modality": modality,
            "expected_direction": expected_direction,
            "observed_direction": observed_direction,
            "effect_size": effect_size,
            "pvalue": pvalue,
            "padj": padj,
            "method": method,
            "n_target_cells": n_target_cells,
            "n_control_cells": n_control_cells,
            **dict(quality or {}),
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"perturbation_efficiency_{uuid4().hex[:12]}",
            kind=ArtifactKind.perturbation_efficiency,
            evidence_class=EvidenceClass.measured,
            artifact_roles=[ArtifactRole.effect_evidence, ArtifactRole.analysis_eligibility],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope({key: value for key, value in merged_scope.items() if value not in (None, "")}),
            predicate={
                key: value for key, value in {
                    "relation": "target_engagement",
                    "target": target_gene,
                    "direction": observed_direction,
                }.items() if value not in (None, "")
            },
            quality={key: value for key, value in merged_quality.items() if value not in (None, "")},
            provenance={"created_by_tool": "register_perturbation_efficiency_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_curated_enrichment(
        self,
        *,
        path: str | Path,
        input_measured_artifact_id: str | None = None,
        input_gene_set_hash: str | None = None,
        background_universe: str | None = None,
        database: str | None = None,
        database_version: str | None = None,
        term_id: str | None = None,
        term_name: str | None = None,
        method: str | None = None,
        pvalue: float | None = None,
        padj: float | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"curated_enrichment_{uuid4().hex[:12]}",
            kind=ArtifactKind.curated_enrichment_result,
            evidence_class=EvidenceClass.curated_prior,
            artifact_roles=[ArtifactRole.prior_context, ArtifactRole.effect_evidence],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            predicate={
                key: value for key, value in {
                    "relation": "curated_enrichment",
                    "term_id": term_id,
                    "term_name": term_name,
                }.items() if value not in (None, "")
            },
            quality={
                key: value for key, value in {
                    "input_measured_artifact_id": input_measured_artifact_id,
                    "input_gene_set_hash": input_gene_set_hash,
                    "background_universe": background_universe,
                    "database": database,
                    "database_version": database_version,
                    "method": method,
                    "pvalue": pvalue,
                    "padj": padj,
                    **dict(quality or {}),
                }.items() if value not in (None, "")
            },
            provenance={"created_by_tool": "register_curated_enrichment_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_module_effect(
        self,
        *,
        path: str | Path,
        module_id: str | None = None,
        module_name: str | None = None,
        module_source: str | None = None,
        module_gene_set_hash: str | None = None,
        scoring_method: str | None = None,
        effect_size: float | None = None,
        method: str | None = None,
        pvalue: float | None = None,
        padj: float | None = None,
        n_target_cells: int | None = None,
        n_control_cells: int | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        merged_predicate = {
            "relation": "module_score_effect",
            "module_id": module_id,
            "module_name": module_name,
            **dict(predicate or {}),
        }
        merged_quality = {
            "module_source": module_source,
            "module_gene_set_hash": module_gene_set_hash,
            "scoring_method": scoring_method,
            "effect_size": effect_size,
            "method": method,
            "pvalue": pvalue,
            "padj": padj,
            "n_target_cells": n_target_cells,
            "n_control_cells": n_control_cells,
            **dict(quality or {}),
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"module_effect_{uuid4().hex[:12]}",
            kind=ArtifactKind.module_effect,
            evidence_class=EvidenceClass.measured,
            artifact_roles=[ArtifactRole.effect_evidence],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            predicate={key: value for key, value in merged_predicate.items() if value not in (None, "")},
            quality={key: value for key, value in merged_quality.items() if value not in (None, "")},
            provenance={"created_by_tool": "register_module_effect_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact

    def register_global_effect(
        self,
        *,
        path: str | Path,
        metric: str | None = None,
        feature_space: str | None = None,
        embedding: str | None = None,
        comparison_method: str | None = None,
        effect_size: float | None = None,
        distance: float | None = None,
        null_model: str | None = None,
        permutation_or_test: str | None = None,
        pvalue: float | None = None,
        padj: float | None = None,
        n_target_cells: int | None = None,
        n_control_cells: int | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        merged_predicate = {
            "relation": "global_response_effect",
            "metric": metric,
            **dict(predicate or {}),
        }
        merged_quality = {
            "metric": metric,
            "feature_space": feature_space,
            "embedding": embedding,
            "comparison_method": comparison_method,
            "effect_size": effect_size,
            "distance": distance,
            "null_model": null_model,
            "permutation_or_test": permutation_or_test,
            "pvalue": pvalue,
            "padj": padj,
            "n_target_cells": n_target_cells,
            "n_control_cells": n_control_cells,
            **dict(quality or {}),
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"global_effect_{uuid4().hex[:12]}",
            kind=ArtifactKind.global_effect,
            evidence_class=EvidenceClass.measured,
            artifact_roles=[ArtifactRole.effect_evidence],
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            predicate={key: value for key, value in merged_predicate.items() if value not in (None, "")},
            quality={key: value for key, value in merged_quality.items() if value not in (None, "")},
            provenance={"created_by_tool": "register_global_effect_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact
    def register_cell_qc(
        self,
        *,
        path: str | Path,
        n_cells_after_qc: int | None = None,
        qc_policy: str | None = None,
        doublet_policy: str | None = None,
        ambient_policy: str | None = None,
        batch_qc: dict | None = None,
        passed: bool | None = None,
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        cell_qc = {
            "n_cells_after_qc": n_cells_after_qc,
            "qc_policy": qc_policy,
            "doublet_policy": doublet_policy,
            "ambient_policy": ambient_policy,
            "batch_qc": dict(batch_qc or {}),
            "cell_qc_passed": passed,
        }
        artifact = EvidenceArtifact(
            artifact_id=artifact_id or f"cell_qc_{uuid4().hex[:12]}",
            kind=ArtifactKind.cell_qc,
            evidence_class=EvidenceClass.observed_metadata,
            artifact_roles=[ArtifactRole.analysis_eligibility],
            path=path_text,
            notes=notes,
            scope=dict(scope or {}),
            quality={key: value for key, value in {**cell_qc, **dict(quality or {})}.items() if value not in (None, "")},
            eligibility={"cell_qc": _clean_nested(cell_qc), "target_qc": _clean_nested(cell_qc)},
            provenance={"created_by_tool": "register_cell_qc_artifact"},
            source_sha256=self._hash_for_path(path_text),
            metadata=dict(metadata or {}),
        )
        self.append(artifact)
        return artifact
    def register_scope_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "scope_artifact",
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        return self._register_family_artifact(
            artifact_id=artifact_id or f"scope_artifact_{uuid4().hex[:12]}",
            kind=ArtifactKind.scope_artifact,
            evidence_class=EvidenceClass.observed_metadata,
            roles=[ArtifactRole.scope_definition],
            created_by_tool="register_scope_artifact",
            path=path,
            artifact_subtype=artifact_subtype,
            scope=scope,
            predicate=predicate,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def register_eligibility_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "eligibility_artifact",
        artifact_id: str | None = None,
        scope: dict | None = None,
        eligibility: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        return self._register_family_artifact(
            artifact_id=artifact_id or f"eligibility_artifact_{uuid4().hex[:12]}",
            kind=ArtifactKind.qc_summary,
            evidence_class=EvidenceClass.observed_metadata,
            roles=[ArtifactRole.analysis_eligibility],
            created_by_tool="register_eligibility_artifact",
            path=path,
            artifact_subtype=artifact_subtype,
            scope=scope,
            quality=quality,
            eligibility=eligibility,
            metadata=metadata,
            notes=notes,
        )

    def register_measured_effect_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "measured_effect",
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
        **kwargs,
    ) -> EvidenceArtifact:
        subtype = str(artifact_subtype or "measured_effect")
        measured_de_required = {"contrast_left", "contrast_baseline", "method", "n_left", "n_baseline", "multiple_testing", "has_padj"}
        if subtype == "measured_de" and measured_de_required.issubset(kwargs):
            return self.register_measured_de(path=path, artifact_id=artifact_id, scope=scope, predicate=predicate, quality=quality, metadata=metadata, notes=notes, **kwargs)
        if subtype == "module_effect":
            return self.register_module_effect(path=path, artifact_id=artifact_id, scope=scope, predicate=predicate, quality=quality, metadata=metadata, notes=notes, **kwargs)
        if subtype == "global_effect":
            return self.register_global_effect(path=path, artifact_id=artifact_id, scope=scope, predicate=predicate, quality=quality, metadata=metadata, notes=notes, **kwargs)
        if subtype == "perturbation_efficiency":
            return self.register_perturbation_efficiency(path=path, artifact_id=artifact_id, scope=scope, quality=quality, metadata=metadata, notes=notes, **kwargs)
        return self._register_family_artifact(
            artifact_id=artifact_id or f"measured_effect_{uuid4().hex[:12]}",
            kind=ArtifactKind.measured_effect,
            evidence_class=EvidenceClass.measured,
            roles=[ArtifactRole.effect_evidence],
            created_by_tool="register_measured_effect_artifact",
            path=path,
            artifact_subtype=subtype,
            scope=scope,
            predicate=predicate,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def register_prior_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "curated_prior_lookup",
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
        **kwargs,
    ) -> EvidenceArtifact:
        subtype = str(artifact_subtype or "curated_prior_lookup")
        enrichment_required = {"input_measured_artifact_id", "database", "database_version", "term_id", "method"}
        if subtype == "curated_enrichment_result" and enrichment_required.issubset(kwargs):
            return self.register_curated_enrichment(path=path, artifact_id=artifact_id, scope=scope, predicate=predicate, quality=quality, metadata=metadata, notes=notes, **kwargs)
        if subtype == "curated_prior_lookup" and "database" in kwargs:
            return self.register_curated_prior(path=path, artifact_id=artifact_id, scope=scope, predicate=predicate, quality=quality, metadata=metadata, notes=notes, **kwargs)
        return self._register_family_artifact(
            artifact_id=artifact_id or f"prior_artifact_{uuid4().hex[:12]}",
            kind=ArtifactKind.curated_prior_lookup,
            evidence_class=EvidenceClass.curated_prior,
            roles=[ArtifactRole.prior_context],
            created_by_tool="register_prior_artifact",
            path=path,
            artifact_subtype=subtype,
            scope=scope,
            predicate=predicate,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def register_prediction_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "predicted_effect",
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
        **kwargs,
    ) -> EvidenceArtifact:
        subtype = str(artifact_subtype or "predicted_effect")
        if subtype == "predicted_effect" and "model_name" in kwargs:
            return self.register_predicted_effect(path=path, artifact_id=artifact_id, scope=scope, predicate=predicate, quality=quality, metadata=metadata, notes=notes, **kwargs)
        return self._register_family_artifact(
            artifact_id=artifact_id or f"prediction_artifact_{uuid4().hex[:12]}",
            kind=ArtifactKind.prediction_artifact,
            evidence_class=EvidenceClass.predicted,
            roles=[ArtifactRole.prediction_evidence],
            created_by_tool="register_prediction_artifact",
            path=path,
            artifact_subtype=subtype,
            scope=scope,
            predicate=predicate,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def register_inferred_structure_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "inferred_structure",
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        return self._register_family_artifact(
            artifact_id=artifact_id or f"inferred_structure_{uuid4().hex[:12]}",
            kind=ArtifactKind.inferred_structure,
            evidence_class=EvidenceClass.measured_inferred,
            roles=[ArtifactRole.effect_evidence],
            created_by_tool="register_inferred_structure_artifact",
            path=path,
            artifact_subtype=artifact_subtype,
            scope=scope,
            predicate=predicate,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def register_ranking_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "ranking_artifact",
        artifact_id: str | None = None,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        return self._register_family_artifact(
            artifact_id=artifact_id or f"ranking_artifact_{uuid4().hex[:12]}",
            kind=ArtifactKind.ranking_artifact,
            evidence_class=EvidenceClass.composite_summary,
            roles=[ArtifactRole.ranking_summary],
            created_by_tool="register_ranking_artifact",
            path=path,
            artifact_subtype=artifact_subtype,
            scope=scope,
            predicate=predicate,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def register_dataset_metadata_artifact(
        self,
        *,
        path: str | Path,
        artifact_subtype: str = "dataset_metadata",
        artifact_id: str | None = None,
        scope: dict | None = None,
        quality: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        return self._register_family_artifact(
            artifact_id=artifact_id or f"dataset_metadata_{uuid4().hex[:12]}",
            kind=ArtifactKind.scope_artifact,
            evidence_class=EvidenceClass.observed_metadata,
            roles=[ArtifactRole.scope_definition],
            created_by_tool="register_dataset_metadata_artifact",
            path=path,
            artifact_subtype=artifact_subtype,
            scope=scope,
            quality=quality,
            metadata=metadata,
            notes=notes,
        )

    def _register_family_artifact(
        self,
        *,
        artifact_id: str,
        kind: ArtifactKind,
        evidence_class: EvidenceClass,
        roles: list[ArtifactRole],
        created_by_tool: str,
        path: str | Path,
        artifact_subtype: str,
        scope: dict | None = None,
        predicate: dict | None = None,
        quality: dict | None = None,
        eligibility: dict | None = None,
        metadata: dict | None = None,
        notes: str | None = None,
    ) -> EvidenceArtifact:
        path_text = str(path)
        artifact = EvidenceArtifact(
            artifact_id=artifact_id,
            kind=kind,
            evidence_class=evidence_class,
            artifact_roles=roles,
            path=path_text,
            notes=notes,
            scope=self.resolve_manifest_scope(dict(scope or {})),
            predicate=dict(predicate or {}),
            quality={key: value for key, value in {"artifact_subtype": artifact_subtype, **dict(quality or {})}.items() if value not in (None, "")},
            eligibility=dict(eligibility or {}),
            provenance={"created_by_tool": created_by_tool},
            source_sha256=self._hash_for_path(path_text),
            metadata={"artifact_subtype": artifact_subtype, **dict(metadata or {})},
        )
        self.append(artifact)
        return artifact
    def source_hash_status(self, artifact: EvidenceArtifact) -> str:
        if not artifact.source_sha256:
            return "not_recorded"
        current = self._hash_for_path(artifact.path)
        if current is None:
            return "missing"
        return "match" if current == artifact.source_sha256 else "mismatch"

    def verify_source_hashes(self) -> dict[str, str]:
        return {artifact.artifact_id: self.source_hash_status(artifact) for artifact in self.list()}

    def append(self, artifact: EvidenceArtifact) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(artifact.to_dict(), ensure_ascii=False) + "\n")

    def _hash_for_path(self, path: str | Path) -> str | None:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.run_root / candidate
        if not candidate.exists() or not candidate.is_file():
            return None
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()



def _scope_identity_from_eligibility(eligibility: dict | None) -> dict:
    if not isinstance(eligibility, dict):
        return {}
    scope: dict = {}
    for key in ["design_manifest_id", "perturbation_uid", "control_uid", "contrast_uid", "estimand"]:
        value = eligibility.get(key)
        if value not in (None, ""):
            scope[key] = value
    perturbation_scope = eligibility.get("perturbation_scope")
    if isinstance(perturbation_scope, dict):
        for key in ["design_manifest_id", "perturbation_uid", "control_uid", "contrast_uid", "estimand"]:
            value = perturbation_scope.get(key)
            if value not in (None, "") and key not in scope:
                scope[key] = value
    return scope

def _canonicalize_scope_aliases(scope: dict) -> dict:
    data = dict(scope or {})
    aliases = {
        "focal_perturbation_uid": "perturbation_uid",
        "left_uid": "perturbation_uid",
        "focal_control_uid": "control_uid",
        "baseline_uid": "control_uid",
        "focal_contrast_uid": "contrast_uid",
    }
    for alias, canonical in aliases.items():
        if data.get(alias) and not data.get(canonical):
            data[canonical] = data[alias]
    return data

def _normalize_ref(value: str | Path) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _contrast_label(left: str | None, baseline: str | None) -> str | None:
    if not left or not baseline:
        return None
    return f"{left}_vs_{baseline}"


def _shared_scope(artifacts: list[EvidenceArtifact]) -> dict:
    if not artifacts:
        return {}
    shared = dict(artifacts[0].scope)
    for artifact in artifacts[1:]:
        for key in list(shared):
            if artifact.scope.get(key) != shared[key]:
                shared.pop(key, None)
    return shared


def _shared_predicate(artifacts: list[EvidenceArtifact]) -> dict:
    if not artifacts:
        return {}
    shared = dict(artifacts[0].predicate)
    for artifact in artifacts[1:]:
        for key in list(shared):
            if artifact.predicate.get(key) != shared[key]:
                shared.pop(key, None)
    return shared


def _clean_nested(value):
    if isinstance(value, dict):
        return {str(key): _clean_nested(item) for key, item in value.items() if item not in (None, "", {}, [])}
    if isinstance(value, list):
        return [_clean_nested(item) for item in value if item not in (None, "", {}, [])]
    return value





