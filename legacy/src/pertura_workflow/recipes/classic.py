from __future__ import annotations

import csv
import json
from pathlib import Path
from uuid import uuid4

from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_gate.core.schema import Claim
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.identity.design_manifest import scope_for_raw_label
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_artifact_strength, resolve_claims
from pertura_workflow.claims import link_candidate_claims, normalize_candidate_claims
from pertura_workflow.harvest import harvest_artifacts_from_workspace
from pertura_workflow.models import HarvestMode, HarvestReport, RecipeRunResult, WorkflowRunManifest, WorkflowRunStep
from pertura_workflow.preflight import preflight_workspace
from pertura_workflow.recommend import recommend_next_evidence
from pertura_workflow.runners import (
    run_basic_de_for_registered_contrast,
    run_basic_target_qc,
    run_label_permutation_null,
    run_ntc_vs_ntc_calibration,
)


RECIPE_NAME = "classic_perturbseq"
_CONFIG_NAMES = ("classic_recipe_config.json", "pertura_classic_recipe.json")


def run_classic_perturbseq(
    workspace: str | Path,
    *,
    mode: str = "benchmark",
    harvest_mode: HarvestMode | str = HarvestMode.candidate_only,
    policy: GatePolicy = DEFAULT_POLICY,
) -> RecipeRunResult:
    """Run the P2.1 classic guide-based Perturb-seq workflow.

    Without a structured recipe config this returns a partial-success report.
    With `classic_recipe_config.json`, it can register validator-backed classic
    Perturb-seq artifacts and render ClaimDecision output. It still does not run
    a full Scanpy/Seurat pipeline or infer scientific scope from file names.
    """

    root = Path(workspace).resolve()
    config_path = _find_config(root)
    if config_path is not None:
        return _run_configured_classic_recipe(root, config_path=config_path, mode=mode, policy=policy)
    return _run_partial_classic_recipe(root, mode=mode, harvest_mode=harvest_mode, policy=policy)


def _run_partial_classic_recipe(
    root: Path,
    *,
    mode: str,
    harvest_mode: HarvestMode | str,
    policy: GatePolicy,
) -> RecipeRunResult:
    preflight = preflight_workspace(root, mode=mode)
    harvest = harvest_artifacts_from_workspace(root, mode=harvest_mode)
    goals = recommend_next_evidence(preflight)
    candidate_claims = _candidate_claims_from_preflight(preflight)
    steps = [
        WorkflowRunStep("preflight", "passed"),
        WorkflowRunStep("harvest_candidates", "passed", notes=harvest.reasons),
        WorkflowRunStep(
            "candidate_claim_linking",
            "blocked" if candidate_claims else "skipped",
            notes=["candidate claims require DesignManifest UID linking before evaluation"] if candidate_claims else [],
        ),
        WorkflowRunStep("recommend_next_evidence", "passed"),
    ]
    manifest = WorkflowRunManifest(
        workflow_run_id=f"workflow_run_{uuid4().hex[:12]}",
        command="recipe classic",
        workspace=str(root),
        mode=mode,
        policy_hash=policy.policy_hash,
        inputs={"recipe_name": RECIPE_NAME, "harvest_mode": harvest.mode.value},
        steps=steps,
    )
    report = _render_classic_recipe_report(preflight, harvest, goals, candidate_claims)
    return RecipeRunResult(
        recipe_name=RECIPE_NAME,
        workspace=str(root),
        mode=mode,
        preflight=preflight,
        harvest=harvest,
        evidence_goals=goals,
        candidate_claims=candidate_claims,
        decision_ids=[],
        report_markdown=report,
        workflow_run_manifest=manifest,
    )


def _run_configured_classic_recipe(root: Path, *, config_path: Path, mode: str, policy: GatePolicy) -> RecipeRunResult:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    preflight = preflight_workspace(root, mode=mode)
    registry = EvidenceRegistry.for_run(root)
    artifacts_dir = root / "artifacts"
    reports_dir = root / "reports"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    manifest_artifact = registry.register_perturbation_design_manifest(
        path=config_path,
        adapter_name=str(config.get("adapter_name") or "guide_label_v1"),
        dataset_id=config.get("dataset_id"),
        source_column=str(config.get("source_column") or "guide_identity"),
        raw_labels=[str(item) for item in config.get("raw_labels") or []],
        guide_to_target_map=dict(config.get("guide_to_target_map") or {}),
        provenance_level=str(config.get("provenance_level") or "deterministic_rule"),
    )
    raw_label = str(config.get("perturbation_raw_label") or (config.get("raw_labels") or [""])[0])
    scope = scope_for_raw_label(manifest_artifact.metadata["manifest"], raw_label)
    if not scope:
        raise ValueError("classic_recipe_config.json must provide perturbation_raw_label present in raw_labels")

    experiment = dict(config.get("experiment_design") or {})
    experiment_artifact = registry.register_experiment_design(
        path=config_path,
        assay=experiment.get("assay") or config.get("assay") or "Perturb-seq",
        perturbation_modality=experiment.get("perturbation_modality") or config.get("perturbation_modality") or "guide_based_perturb_seq",
        guide_capture=experiment.get("guide_capture") or "present",
        moi=experiment.get("moi") or config.get("moi") or "low",
        controls=dict(experiment.get("controls") or config.get("controls") or {}),
        replication=dict(experiment.get("replication") or {}),
        timepoint=experiment.get("timepoint") or config.get("timepoint"),
        scope=scope,
    )

    guide = dict(config.get("guide_assignment") or {})
    guide_artifact = registry.register_guide_assignment(
        path=config_path,
        assignment_method=guide.get("assignment_method") or "structured_recipe_metadata",
        assigned_count=_optional_int(guide.get("assigned_count")),
        unassigned_count=_optional_int(guide.get("unassigned_count")),
        multi_guide_count=_optional_int(guide.get("multi_guide_count")),
        guide_distribution=dict(guide.get("guide_distribution") or {}),
        ambient_guide_handling=guide.get("ambient_guide_handling"),
        moi_inference=guide.get("moi_inference") or config.get("moi") or "low",
        target_summary=dict(guide.get("target_summary") or {}),
        guide_to_target_map_hash=guide.get("guide_to_target_map_hash") or _config_hash(config.get("guide_to_target_map") or {}),
        scope=scope,
    )

    target_qc = dict(config.get("target_qc") or {})
    basic_target_qc = dict(config.get("basic_target_qc") or {})
    basic_target_qc_result: dict[str, object] | None = None
    target_qc_path = config_path
    if not target_qc and basic_target_qc:
        metadata_csv = basic_target_qc.get("metadata_csv") or basic_target_qc.get("cell_metadata")
        if not metadata_csv:
            raise ValueError("basic_target_qc requires metadata_csv/cell_metadata")
        basic_target_qc_result = run_basic_target_qc(
            root,
            metadata_csv=metadata_csv,
            target_uid=str(basic_target_qc.get("target_uid") or scope.get("perturbation_uid") or ""),
            control_uid=str(basic_target_qc.get("control_uid") or scope.get("control_uid") or ""),
            target=str(basic_target_qc.get("target") or config.get("target") or _target_from_scope(scope)),
            control=str(basic_target_qc.get("control") or config.get("control") or "negative_control_pool"),
            output_path=basic_target_qc.get("output_path"),
            cell_id_column=str(basic_target_qc.get("cell_id_column") or "cell_id"),
            condition_column=str(basic_target_qc.get("condition_column") or "perturbation_uid"),
            guide_column=basic_target_qc.get("guide_column"),
            guide_to_target_csv=basic_target_qc.get("guide_to_target_csv") or basic_target_qc.get("guide_to_target_map"),
            guide_column_in_map=str(basic_target_qc.get("guide_column_in_map") or "guide"),
            target_column_in_map=str(basic_target_qc.get("target_column_in_map") or "target"),
            minimum_cells=_optional_int(basic_target_qc.get("minimum_cells")),
        )
        target_qc = {**basic_target_qc_result, **target_qc}
        target_qc_path = Path(str(basic_target_qc_result["path"]))
    target_artifact = registry.register_target_qc(
        path=target_qc_path,
        target=target_qc.get("target") or config.get("target") or _target_from_scope(scope),
        control=target_qc.get("control") or config.get("control") or "negative_control_pool",
        n_target_cells=_optional_int(target_qc.get("n_target_cells") or config.get("n_left")),
        n_control_cells=_optional_int(target_qc.get("n_control_cells") or config.get("n_baseline")),
        guides_per_target=_optional_int(target_qc.get("guides_per_target")),
        cells_per_guide=dict(target_qc.get("cells_per_guide") or {}),
        guide_consistency=target_qc.get("guide_consistency"),
        control_calibration=dict(target_qc.get("control_calibration") or {}),
        estimand=target_qc.get("estimand") or scope.get("estimand"),
        model_covariates=list(target_qc.get("model_covariates") or []),
        scope=scope,
    )

    optional_artifact_ids: list[str] = []
    calibration_runner_steps: list[WorkflowRunStep] = []
    if isinstance(config.get("cell_qc"), dict):
        cell_qc = dict(config["cell_qc"])
        cell_artifact = registry.register_cell_qc(
            path=config_path,
            n_cells_after_qc=_optional_int(cell_qc.get("n_cells_after_qc")),
            qc_policy=cell_qc.get("qc_policy"),
            doublet_policy=cell_qc.get("doublet_policy"),
            ambient_policy=cell_qc.get("ambient_policy"),
            batch_qc=dict(cell_qc.get("batch_qc") or {}),
            passed=cell_qc.get("passed"),
            scope=scope,
        )
        optional_artifact_ids.append(cell_artifact.artifact_id)

    calibration_config = dict(config.get("control_calibration") or {})
    for calibration_payload in _run_or_load_control_calibrations(root, calibration_config, scope):
        calibration_path = _required_workspace_file(root, calibration_payload.get("path"))
        calibration_artifact = registry.register_control_calibration(
            path=calibration_path,
            calibration_type=calibration_payload.get("calibration_type"),
            scope=scope,
            negative_control_status=calibration_payload.get("negative_control_status"),
            ntc_vs_ntc_check=dict(calibration_payload.get("ntc_vs_ntc_check") or {}),
            label_permutation_check=dict(calibration_payload.get("label_permutation_check") or {}),
            alpha=_optional_float(calibration_payload.get("alpha")),
            n_features_tested=_optional_int(calibration_payload.get("n_features_tested")),
            n_significant=_optional_int(calibration_payload.get("n_significant")),
            method=calibration_payload.get("method"),
            execution_hash=calibration_payload.get("execution_hash"),
            quality=dict(calibration_payload.get("quality") or {}),
            metadata={"source": "classic_recipe_control_calibration"},
        )
        optional_artifact_ids.append(calibration_artifact.artifact_id)
        calibration_runner_steps.append(
            WorkflowRunStep(
                f"register_control_calibration:{calibration_payload.get('calibration_type') or 'control_calibration'}",
                "registered",
                artifact_ids=[calibration_artifact.artifact_id],
                output_paths=[str(calibration_path)],
                notes=["control calibration is eligibility evidence only; scientific interpretation remains gated"],
            )
        )

    de = dict(config.get("measured_de") or {})
    basic_de = dict(config.get("basic_de") or {})
    basic_de_result: dict[str, object] | None = None
    if not (de.get("path") or config.get("de_table")) and basic_de:
        expression_csv = basic_de.get("expression_csv") or basic_de.get("expression_matrix")
        metadata_csv = basic_de.get("metadata_csv") or basic_de.get("cell_metadata")
        if not expression_csv or not metadata_csv:
            raise ValueError("basic_de requires expression_csv/expression_matrix and metadata_csv/cell_metadata")
        gene_columns = basic_de.get("gene_columns")
        basic_de_result = run_basic_de_for_registered_contrast(
            root,
            expression_csv=expression_csv,
            metadata_csv=metadata_csv,
            contrast_uid=str(basic_de.get("contrast_uid") or scope.get("contrast_uid") or ""),
            left_uid=str(basic_de.get("left_uid") or scope.get("perturbation_uid") or ""),
            baseline_uid=str(basic_de.get("baseline_uid") or scope.get("control_uid") or ""),
            layer=str(basic_de.get("layer") or config.get("layer") or ""),
            output_path=basic_de.get("output_path"),
            cell_id_column=str(basic_de.get("cell_id_column") or "cell_id"),
            condition_column=str(basic_de.get("condition_column") or "perturbation_uid"),
            gene_columns=[str(item) for item in gene_columns] if isinstance(gene_columns, list) else None,
        )
        de = {**basic_de_result, **de}

    de_path = _required_workspace_file(root, de.get("path") or config.get("de_table"))
    columns = de.get("columns") or _table_columns(de_path)
    measured = registry.register_measured_de(
        path=de_path,
        contrast_left=de.get("contrast_left") or raw_label,
        contrast_baseline=de.get("contrast_baseline") or config.get("control_label") or "negative_control_pool",
        method=de.get("method") or "registered_table",
        n_left=_optional_int(de.get("n_left") or target_qc.get("n_target_cells") or config.get("n_left")),
        n_baseline=_optional_int(de.get("n_baseline") or target_qc.get("n_control_cells") or config.get("n_baseline")),
        multiple_testing=de.get("multiple_testing") or "BH",
        has_padj=bool(de.get("has_padj", "padj" in {str(column).lower() for column in columns})),
        columns=[str(column) for column in columns],
        source_data=config.get("dataset_id"),
        scope=scope,
    )

    candidate_links = link_candidate_claims(
        normalize_candidate_claims(config),
        manifest=manifest_artifact.metadata["manifest"],
        default_scope=scope,
        default_evidence_refs=[measured.artifact_id],
        default_subject_id=_target_from_scope(scope),
    )
    linked_claims = [link.claim for link in candidate_links if link.claim is not None]
    decisions = resolve_claims(linked_claims, registry, policy=policy) if linked_claims else []
    decisions_path = artifacts_dir / "claim_decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "decisions": [decision.to_dict() for decision in decisions],
                "candidate_claim_links": [link.to_dict() for link in candidate_links],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    report_path = reports_dir / "evidence_report.md"
    if linked_claims:
        rendered = render_evidence_report(registry=registry, claims=linked_claims, write_path=report_path, title="Pertura Classic Perturb-seq Evidence Report", policy=policy)
        report_markdown = _append_candidate_claim_gap_section(rendered.markdown, candidate_links)
    else:
        report_markdown = _render_configured_gap_report(registry, candidate_links, policy=policy)
    report_path.write_text(report_markdown, encoding="utf-8")

    registered_ids = [
        manifest_artifact.artifact_id,
        experiment_artifact.artifact_id,
        guide_artifact.artifact_id,
        target_artifact.artifact_id,
        *optional_artifact_ids,
        measured.artifact_id,
    ]
    harvest = HarvestReport(
        workspace=str(root),
        mode=HarvestMode.auto_register_strict,
        candidates=preflight.candidate_artifacts,
        registered_artifact_ids=registered_ids,
        registry_path=str(registry.path),
        reasons=["classic recipe registered validator-backed artifacts from structured classic_recipe_config.json"],
    )
    runner_steps = []
    if basic_target_qc_result:
        runner_steps.append(
            WorkflowRunStep(
                "run_basic_target_qc",
                "passed",
                output_paths=[str(basic_target_qc_result["path"])],
                notes=["narrow runner produced target/control QC summary only; scientific interpretation remains gated"],
            )
        )
    if basic_de_result:
        runner_steps.append(
            WorkflowRunStep(
                "run_basic_de_for_registered_contrast",
                "passed",
                output_paths=[str(basic_de_result["path"])],
                notes=["narrow runner produced DE table only; scientific interpretation remains gated"],
            )
        )
    runner_steps.extend(calibration_runner_steps)
    steps = [
        WorkflowRunStep("preflight", "passed"),
        WorkflowRunStep("register_design_manifest", "registered", artifact_ids=[manifest_artifact.artifact_id]),
        WorkflowRunStep("register_eligibility", "registered", artifact_ids=[experiment_artifact.artifact_id, guide_artifact.artifact_id, target_artifact.artifact_id, *optional_artifact_ids]),
        *runner_steps,
        WorkflowRunStep("register_measured_de", "registered", artifact_ids=[measured.artifact_id]),
        WorkflowRunStep("link_candidate_claims", "passed" if linked_claims else "blocked", output_paths=[str(decisions_path)]),
        WorkflowRunStep("evaluate_claims", "passed" if linked_claims else "skipped", output_paths=[str(decisions_path)]),
        WorkflowRunStep("render_report", "passed", output_paths=[str(report_path)]),
    ]
    manifest = WorkflowRunManifest(
        workflow_run_id=f"workflow_run_{uuid4().hex[:12]}",
        command="recipe classic",
        workspace=str(root),
        mode=mode,
        policy_hash=policy.policy_hash,
        inputs={"recipe_name": RECIPE_NAME, "config_path": str(config_path), "harvest_mode": harvest.mode.value, "basic_de_ran": bool(basic_de_result), "basic_target_qc_ran": bool(basic_target_qc_result)},
        steps=steps,
        output_paths=[str(decisions_path), str(report_path)],
    )
    candidate_claim_records = [link.to_dict() for link in candidate_links]
    return RecipeRunResult(
        recipe_name=RECIPE_NAME,
        workspace=str(root),
        mode=mode,
        preflight=preflight,
        harvest=harvest,
        evidence_goals=[],
        candidate_claims=candidate_claim_records,
        decision_ids=[decision.decision_id for decision in decisions],
        report_markdown=report_markdown,
        workflow_run_manifest=manifest,
    )


def _run_or_load_control_calibrations(root: Path, calibration_config: dict, scope: dict | None = None) -> list[dict[str, object]]:
    if not calibration_config:
        return []
    payloads: list[dict[str, object]] = []
    existing_path = calibration_config.get("path")
    if existing_path:
        path = _required_workspace_file(root, existing_path)
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("path", str(path))
            payloads.append(payload)
    ntc_config = dict(calibration_config.get("ntc_vs_ntc") or {})
    if ntc_config:
        result = run_ntc_vs_ntc_calibration(
            root,
            expression_csv=ntc_config.get("expression_csv") or ntc_config.get("expression_matrix"),
            metadata_csv=ntc_config.get("metadata_csv") or ntc_config.get("cell_metadata"),
            control_uid=str(ntc_config.get("control_uid") or ""),
            layer=str(ntc_config.get("layer") or calibration_config.get("layer") or ""),
            output_path=ntc_config.get("output_path"),
            cell_id_column=str(ntc_config.get("cell_id_column") or "cell_id"),
            condition_column=str(ntc_config.get("condition_column") or "perturbation_uid"),
            gene_columns=[str(item) for item in ntc_config.get("gene_columns")] if isinstance(ntc_config.get("gene_columns"), list) else None,
            alpha=float(ntc_config.get("alpha") or calibration_config.get("alpha") or 0.05),
            seed=int(ntc_config.get("seed") or calibration_config.get("seed") or 0),
            max_features=_optional_int(ntc_config.get("max_features")),
        )
        payloads.append(result)
    permutation_config = dict(calibration_config.get("label_permutation") or {})
    if permutation_config:
        result = run_label_permutation_null(
            root,
            expression_csv=permutation_config.get("expression_csv") or permutation_config.get("expression_matrix"),
            metadata_csv=permutation_config.get("metadata_csv") or permutation_config.get("cell_metadata"),
            contrast_uid=str(permutation_config.get("contrast_uid") or ""),
            left_uid=str(permutation_config.get("left_uid") or ""),
            baseline_uid=str(permutation_config.get("baseline_uid") or ""),
            layer=str(permutation_config.get("layer") or calibration_config.get("layer") or ""),
            output_path=permutation_config.get("output_path"),
            cell_id_column=str(permutation_config.get("cell_id_column") or "cell_id"),
            condition_column=str(permutation_config.get("condition_column") or "perturbation_uid"),
            gene_columns=[str(item) for item in permutation_config.get("gene_columns")] if isinstance(permutation_config.get("gene_columns"), list) else None,
            alpha=float(permutation_config.get("alpha") or calibration_config.get("alpha") or 0.05),
            seed=int(permutation_config.get("seed") or calibration_config.get("seed") or 0),
            max_features=_optional_int(permutation_config.get("max_features")),
        )
        payloads.append(result)
    return payloads


def _append_candidate_claim_gap_section(markdown: str, candidate_links) -> str:
    unlinked = [link for link in candidate_links if link.claim is None]
    if not unlinked:
        return markdown
    lines = [markdown.rstrip(), "", "## Candidate Claim Gaps", ""]
    lines.append("These candidate claims were not evaluated as scientific findings because they could not be linked to registered evidence and canonical UID scope.")
    lines.append("")
    for link in unlinked:
        reasons = "; ".join(link.reasons) if link.reasons else "unlinked candidate claim"
        lines.append(f"- `{link.candidate_claim_id}`: `{link.status}`; {reasons}")
    return "\n".join(lines) + "\n"


def _render_configured_gap_report(registry: EvidenceRegistry, candidate_links, *, policy: GatePolicy) -> str:
    lines = [
        "# Pertura Classic Perturb-seq Evidence Gap Report",
        "",
        f"- Policy version: `{policy.version}`",
        f"- Policy hash: `{policy.policy_hash}`",
        "- Recipe status: `partial_success`",
        "- Scientific surface: no effect-level conclusion was rendered because no candidate claim linked to both canonical UID scope and registered evidence.",
        "",
        "## Candidate Claim Gaps",
        "",
    ]
    if not candidate_links:
        lines.append("No candidate claims were provided.")
    for link in candidate_links:
        reasons = "; ".join(link.reasons) if link.reasons else "unlinked candidate claim"
        lines.append(f"- `{link.candidate_claim_id}`: `{link.status}`; {reasons}")
    lines.extend(["", "## Registered Evidence Artifacts", ""])
    artifacts = registry.list()
    if not artifacts:
        lines.append("No evidence artifacts were registered.")
    else:
        lines.append("| artifact | kind | evidence_class | intrinsic_ceiling |")
        lines.append("| --- | --- | --- | --- |")
        for artifact in artifacts:
            lines.append(
                f"| `{artifact.artifact_id}` | `{artifact.kind.value}` | `{artifact.effective_evidence_class.value}` | `{resolve_artifact_strength(artifact, policy=policy).ceiling.value}` |"
            )
    lines.extend(
        [
            "",
            "## Gate Boundary",
            "",
            "Candidate claims are not scientific conclusions. They must be linked to DesignManifest UIDs, evaluated by the claim resolver, and rendered through ClaimDecision before user-facing scientific use.",
        ]
    )
    return "\n".join(lines) + "\n"

def _find_config(root: Path) -> Path | None:
    for name in _CONFIG_NAMES:
        path = root / name
        if path.exists() and path.is_file():
            return path
    return None


def _candidate_claims_from_preflight(preflight) -> list[dict]:
    claims: list[dict] = []
    for candidate in preflight.candidate_artifacts:
        if candidate.candidate_kind != "measured_de_table":
            continue
        claims.append(
            {
                "claim_id": f"candidate_claim_{candidate.candidate_id}",
                "text": "Candidate measured differential-expression claim; requires UID-linked evidence before evaluation.",
                "scope": {},
                "evidence_refs": [],
                "source_candidate_id": candidate.candidate_id,
                "status": "candidate_only_unlinked",
            }
        )
    return claims


def _render_classic_recipe_report(preflight, harvest, goals, candidate_claims: list[dict]) -> str:
    lines = [
        "# Pertura Classic Perturb-seq Recipe Report",
        "",
        f"- Workspace: `{preflight.workspace}`",
        f"- Mode: `{preflight.mode}`",
        "- Recipe status: `partial_success`",
        "- Scientific surface: no effect-level conclusion is rendered by the recipe skeleton.",
        "",
        "## Candidate Artifacts",
        "",
    ]
    if not harvest.candidates:
        lines.append("No candidate artifacts detected.")
    for candidate in harvest.candidates:
        unresolved = ", ".join(candidate.unresolved_fields) if candidate.unresolved_fields else "none"
        lines.append(
            f"- `{candidate.candidate_id}` `{candidate.candidate_kind}` / `{candidate.artifact_subtype}` "
            f"from `{candidate.relative_path}`; unresolved: {unresolved}"
        )
    lines.extend(["", "## Candidate Claims", ""])
    if not candidate_claims:
        lines.append("No candidate claims were generated.")
    for claim in candidate_claims:
        lines.append(f"- `{claim['claim_id']}`: {claim['status']}")
    lines.extend(["", "## Recommended Next Evidence", ""])
    if not goals:
        lines.append("No next-evidence goals were identified.")
    for goal in goals:
        lines.append(f"- `{goal.claim_type}` missing `{goal.missing}` ({goal.priority}): {goal.recommendation}")
    lines.extend(
        [
            "",
            "## Gate Boundary",
            "",
            "Candidate claims are not scientific conclusions. They must be linked to DesignManifest UIDs, evaluated by the claim resolver, and rendered through ClaimDecision before user-facing scientific use.",
        ]
    )
    return "\n".join(lines) + "\n"


def _required_workspace_file(root: Path, value) -> Path:
    if not value:
        raise ValueError("classic_recipe_config.json must include measured_de.path or de_table")
    path = Path(str(value))
    candidate = path if path.is_absolute() else root / path
    if not candidate.exists() or not candidate.is_file():
        raise ValueError(f"configured workspace file does not exist: {value}")
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"configured workspace file escapes workspace: {value}") from exc
    return candidate


def _table_columns(path: Path) -> list[str]:
    if path.suffix.lower() not in {".csv", ".tsv"}:
        return []
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            return [str(item) for item in next(reader)]
        except StopIteration:
            return []


def _optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _config_hash(value) -> str | None:
    if not value:
        return None
    import hashlib

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _target_from_scope(scope: dict) -> str:
    uid = str(scope.get("perturbation_uid") or "target:unknown")
    if uid.startswith("target:"):
        return uid.split(":", 1)[1]
    return uid
