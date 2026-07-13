from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pertura_gate.core.schema import Claim, ScopeFit, StrengthCeiling
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_artifact_strength, resolve_claim
from pertura_runtime.stages import load_stage_contract

STAGE_BENCH_CASE_IDS = [
    "guide_assignment_eligibility_only",
    "cell_state_reference_context_only",
    "composition_effect_association_only",
    "measured_de_association_only",
    "claim_report_decision_surface",
]

STAGE_BENCH_METRICS = [
    "stage_completion",
    "artifact_registration_correct",
    "metadata_completeness",
    "stage_boundary_respected",
    "exploration_not_surfaced",
    "next_stage_recommendation_correct",
]


@dataclass(frozen=True)
class StageBenchmarkResult:
    case_id: str
    stage_id: str
    completion: bool
    metrics: dict[str, bool]
    artifact_kinds: list[str]
    decision_strengths: list[str]
    scope_fits: list[str]
    report_path: str | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "stage_id": self.stage_id,
            "completion": self.completion,
            "metrics": dict(self.metrics),
            "artifact_kinds": list(self.artifact_kinds),
            "decision_strengths": list(self.decision_strengths),
            "scope_fits": list(self.scope_fits),
            "report_path": self.report_path,
            "notes": list(self.notes),
        }


def run_stage_benchmark_case(case_id: str, *, root: str | Path) -> StageBenchmarkResult:
    workspace = Path(root).expanduser().resolve() / _safe_case_id(case_id)
    (workspace / "outputs").mkdir(parents=True, exist_ok=True)
    registry = EvidenceRegistry(workspace / "artifacts" / "evidence_artifacts.jsonl")
    if case_id == "guide_assignment_eligibility_only":
        return _case_guide_assignment(workspace, registry)
    if case_id == "cell_state_reference_context_only":
        return _case_cell_state_reference(workspace, registry)
    if case_id == "composition_effect_association_only":
        return _case_composition_effect(workspace, registry)
    if case_id == "measured_de_association_only":
        return _case_measured_de(workspace, registry, render_report=False)
    if case_id == "claim_report_decision_surface":
        return _case_measured_de(workspace, registry, render_report=True)
    raise ValueError(f"unknown stage benchmark case: {case_id}")


def run_stage_benchmark_suite(*, root: str | Path | None = None) -> list[StageBenchmarkResult]:
    if root is None:
        with tempfile.TemporaryDirectory(prefix="pertura_stage_bench_") as tmp:
            return [run_stage_benchmark_case(case_id, root=tmp) for case_id in STAGE_BENCH_CASE_IDS]
    root_path = Path(root).expanduser().resolve()
    return [run_stage_benchmark_case(case_id, root=root_path) for case_id in STAGE_BENCH_CASE_IDS]


def write_stage_benchmark_summary(results: list[StageBenchmarkResult], *, output_dir: str | Path) -> tuple[Path, Path]:
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "pertura-stage-benchmark-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "metrics": list(STAGE_BENCH_METRICS),
        "results": [result.to_dict() for result in results],
        "invariants": [
            "guide_assignment is eligibility metadata only and cannot support measured claims by itself",
            "cell_state_reference is state context only and cannot support perturbation effect claims by itself",
            "measured_de supports measured_association at most and cannot establish mechanism",
            "claim_report surfaces scientific conclusions only through ClaimDecision rendering",
        ],
    }
    json_path = out / "stage_benchmark_summary.json"
    md_path = out / "stage_benchmark_summary.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_render_summary_markdown(payload), encoding="utf-8")
    return md_path, json_path


def _case_guide_assignment(workspace: Path, registry: EvidenceRegistry) -> StageBenchmarkResult:
    path = _write(workspace, "guide_assignment_summary.json", {"assignment_method": "synthetic_threshold", "assigned_count": 12})
    artifact = registry.register_guide_assignment(
        path=path,
        assignment_method="synthetic_threshold",
        assigned_count=12,
        unassigned_count=0,
        multi_guide_count=0,
        guide_distribution={"sgKLF1": 6, "NegCtrl0": 6},
        target_summary={"KLF1": 6, "negative_control": 6},
        scope={"dataset_id": "stage_bench"},
    )
    resolved = resolve_artifact_strength(artifact)
    metrics = _base_metrics(
        stage_id="guide_assignment",
        artifact_ok=artifact.kind.value == "guide_assignment" and resolved.ceiling == StrengthCeiling.observation,
        metadata_ok=artifact.quality.get("assignment_method") == "synthetic_threshold",
        boundary_ok=resolved.ceiling == StrengthCeiling.observation,
        surfaced_ok=True,
    )
    return StageBenchmarkResult(
        case_id="guide_assignment_eligibility_only",
        stage_id="guide_assignment",
        completion=all(metrics.values()),
        metrics=metrics,
        artifact_kinds=[artifact.kind.value],
        decision_strengths=[],
        scope_fits=[],
        report_path=None,
        notes=["guide assignment registered as analysis eligibility only"],
    )


def _case_cell_state_reference(workspace: Path, registry: EvidenceRegistry) -> StageBenchmarkResult:
    path = _write(workspace, "state_reference_summary.json", {"assignment_column": "leiden", "n_clusters": 3})
    artifact = registry.register_cell_state_reference(
        path=path,
        assignment_column="leiden",
        embedding_methods=["PCA", "UMAP"],
        clustering_method="Leiden",
        annotation_method="marker_summary",
        marker_summary_path="outputs/state_markers.csv",
        source_data_path="input/synthetic.h5ad",
        source_data_sha256="sha256:synthetic",
        scope={"dataset_id": "stage_bench"},
    )
    claim = Claim(
        claim_id="state_context_as_effect",
        text="The cell-state reference validates a KLF1 perturbation mechanism.",
        subject={"id": "KLF1"},
        object={"id": "cell_state"},
        scope={"dataset_id": "stage_bench"},
        requested_strength=StrengthCeiling.measured_association,
        evidence_refs=[artifact.artifact_id],
    )
    decision = resolve_claim(claim, registry)
    metrics = _base_metrics(
        stage_id="cell_state_reference",
        artifact_ok=artifact.kind.value == "cell_state_reference" and artifact.effective_evidence_class.value == "observed_metadata",
        metadata_ok=artifact.quality.get("assignment_column") == "leiden" and artifact.quality.get("clustering_method") == "Leiden",
        boundary_ok=decision.max_strength == StrengthCeiling.observation,
        surfaced_ok="validates" not in decision.allowed_surface.lower(),
    )
    return StageBenchmarkResult(
        case_id="cell_state_reference_context_only",
        stage_id="cell_state_reference",
        completion=all(metrics.values()),
        metrics=metrics,
        artifact_kinds=[artifact.kind.value],
        decision_strengths=[decision.max_strength.value],
        scope_fits=[decision.scope_fit.value],
        report_path=None,
        notes=["cell state reference remained state context and did not support effect claim"],
    )



def _case_composition_effect(workspace: Path, registry: EvidenceRegistry) -> StageBenchmarkResult:
    raw_left = "KLF1_NegCtrl0__KLF1_NegCtrl0"
    raw_control = "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"
    manifest_source = _write(workspace, "design_manifest_source.json", {"raw_labels": [raw_left, raw_control]})
    manifest = registry.register_perturbation_design_manifest(
        path=manifest_source,
        dataset_id="stage_bench",
        raw_labels=[raw_left, raw_control],
    )
    scope = scope_for_raw_label(manifest.metadata["manifest"], raw_left)
    comp_path = _write(
        workspace,
        "composition_effect_summary.json",
        {"state_counts_by_condition": {"state_a": {"target": 80, "control": 40}}},
    )
    artifact = registry.register_composition_effect(
        path=comp_path,
        state_source="cell_state_reference_synthetic",
        state_assignment_column="state_label",
        comparison_method="fisher_exact",
        effect_size=0.25,
        padj=0.01,
        n_target_cells=120,
        n_control_cells=150,
        scope=scope,
        quality={
            "state_counts_by_condition": {"state_a": {"target": 80, "control": 40}, "other": {"target": 40, "control": 110}},
            "eligibility": {
                "perturbation_cell_mapping": {"assignment_method": "synthetic_threshold", "guide_to_target_map_hash": "sha256:guide-map"},
                "control_definition": {"negative_controls": ["NegCtrl0"], "control_label": "NegCtrl0"},
                "target_qc": {"n_target_cells": 120, "n_control_cells": 150, "guides_per_target": 1},
                "assay_modality": "guide_based_perturb_seq",
                "perturbation_modality": "CRISPRi",
                "moi": "low",
                "estimand": "single_target_marginal",
                "control_calibration": {"negative_control_status": "available"},
            },
        },
    )
    claim = Claim(
        claim_id="composition_as_fate_mechanism",
        text="KLF1 causes cell fate conversion through a validated mechanism.",
        subject={"id": "KLF1"},
        object={"type": "cell_state", "id": "state_a"},
        scope=scope,
        requested_strength="causal_fate_conversion",
        evidence_refs=[artifact.artifact_id],
    )
    decision = resolve_claim(claim, registry)
    metrics = _base_metrics(
        stage_id="composition_effect",
        artifact_ok=artifact.kind.value == "composition_effect" and artifact.effective_evidence_class.value == "measured",
        metadata_ok=artifact.quality.get("state_assignment_column") == "state_label" and artifact.quality.get("comparison_method") == "fisher_exact",
        boundary_ok=decision.max_strength == StrengthCeiling.measured_association and decision.scope_fit == ScopeFit.exact,
        surfaced_ok="composition association" in decision.allowed_surface.lower() and "does not establish" in decision.allowed_surface.lower(),
    )
    return StageBenchmarkResult(
        case_id="composition_effect_association_only",
        stage_id="composition_effect",
        completion=all(metrics.values()),
        metrics=metrics,
        artifact_kinds=[manifest.kind.value, artifact.kind.value],
        decision_strengths=[decision.max_strength.value],
        scope_fits=[decision.scope_fit.value],
        report_path=None,
        notes=["composition effect downgraded causal fate request to measured composition association"],
    )
def _case_measured_de(workspace: Path, registry: EvidenceRegistry, *, render_report: bool) -> StageBenchmarkResult:
    raw_left = "KLF1_NegCtrl0__KLF1_NegCtrl0"
    raw_control = "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"
    manifest_source = _write(workspace, "design_manifest_source.json", {"raw_labels": [raw_left, raw_control]})
    manifest = registry.register_perturbation_design_manifest(
        path=manifest_source,
        dataset_id="stage_bench",
        raw_labels=[raw_left, raw_control],
    )
    scope = scope_for_raw_label(manifest.metadata["manifest"], raw_left)
    de_path = _write_text(workspace, "klf1_de.csv", "gene,logfc,padj\nKLF1,-1.2,0.01\nGYPA,-0.7,0.03\n")
    artifact = registry.register_measured_de(
        path=de_path,
        contrast_left=raw_left,
        contrast_baseline=raw_control,
        method="synthetic_wilcoxon",
        n_left=20,
        n_baseline=20,
        multiple_testing="BH",
        has_padj=True,
        columns=["gene", "logfc", "padj"],
        source_data="synthetic_stage_bench",
        scope=scope,
        eligibility={
            "perturbation_cell_mapping": {"assignment_method": "synthetic_threshold", "guide_to_target_map_hash": "sha256:guide-map"},
            "control_definition": {"negative_controls": ["NegCtrl0"], "control_label": "NegCtrl0"},
            "target_qc": {"n_target_cells": 20, "n_control_cells": 20, "guides_per_target": 1},
            "assay_modality": "guide_based_perturb_seq",
            "perturbation_modality": "CRISPRi",
            "moi": "low",
            "estimand": "single_target_marginal",
            "control_calibration": {"negative_control_status": "available"},
        },
    )
    claim = Claim(
        claim_id="measured_de_as_mechanism",
        text="KLF1 validates a downstream mechanism.",
        subject={"id": "KLF1"},
        object={"id": "downstream_program"},
        scope=scope,
        requested_strength=StrengthCeiling.validated_mechanism_disabled,
        evidence_refs=[artifact.artifact_id],
    )
    decision = resolve_claim(claim, registry)
    report_path = None
    if render_report:
        report_path = workspace / "reports" / "evidence_report.md"
        render_evidence_report(registry=registry, claims=[claim], write_path=report_path, title="Stage Benchmark Claim Report")
    metrics = _base_metrics(
        stage_id="claim_report" if render_report else "measured_de",
        artifact_ok=artifact.kind.value == "measured_de",
        metadata_ok=artifact.method == "synthetic_wilcoxon" and artifact.has_padj,
        boundary_ok=decision.max_strength == StrengthCeiling.measured_association and decision.scope_fit == ScopeFit.exact,
        surfaced_ok="measured association only" in decision.allowed_surface.lower() and "proves" not in decision.allowed_surface.lower(),
    )
    if render_report:
        metrics["stage_completion"] = report_path.exists()
        metrics["exploration_not_surfaced"] = report_path.exists() and "Claim strength ceiling: `measured_association`" in report_path.read_text(encoding="utf-8")
    return StageBenchmarkResult(
        case_id="claim_report_decision_surface" if render_report else "measured_de_association_only",
        stage_id="claim_report" if render_report else "measured_de",
        completion=all(metrics.values()),
        metrics=metrics,
        artifact_kinds=[manifest.kind.value, artifact.kind.value],
        decision_strengths=[decision.max_strength.value],
        scope_fits=[decision.scope_fit.value],
        report_path=str(report_path) if report_path and report_path.exists() else None,
        notes=["claim report rendered through ClaimDecision" if render_report else "measured DE downgraded mechanism request to measured association"],
    )


def _base_metrics(*, stage_id: str, artifact_ok: bool, metadata_ok: bool, boundary_ok: bool, surfaced_ok: bool) -> dict[str, bool]:
    contract = load_stage_contract(stage_id)
    next_ok = bool(contract.get("next_stage_recommendations") or stage_id == "claim_report")
    return {
        "stage_completion": True,
        "artifact_registration_correct": bool(artifact_ok),
        "metadata_completeness": bool(metadata_ok),
        "stage_boundary_respected": bool(boundary_ok),
        "exploration_not_surfaced": bool(surfaced_ok),
        "next_stage_recommendation_correct": next_ok,
    }


def _write(workspace: Path, filename: str, payload: dict[str, Any]) -> str:
    path = workspace / "outputs" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"outputs/{filename}"


def _write_text(workspace: Path, filename: str, text: str) -> str:
    path = workspace / "outputs" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"outputs/{filename}"


def _render_summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pertura Stage Benchmark Summary",
        "",
        "This deterministic benchmark freezes the first Evidence-Aware Stage Catalog boundaries.",
        "",
        "| case | stage | completion | strengths | metrics | notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["results"]:
        passed = sum(1 for value in row["metrics"].values() if value)
        total = len(row["metrics"])
        lines.append(
            "| "
            f"`{row['case_id']}` | "
            f"`{row['stage_id']}` | "
            f"`{str(row['completion']).lower()}` | "
            f"`{', '.join(row['decision_strengths']) or 'none'}` | "
            f"`{passed}/{total}` | "
            f"{'; '.join(row['notes'])} |"
        )
    lines.extend(["", "## Invariants", ""])
    lines.extend(f"- {item}" for item in payload["invariants"])
    lines.append("")
    return "\n".join(lines)


def _safe_case_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value).strip("_") or "case"
