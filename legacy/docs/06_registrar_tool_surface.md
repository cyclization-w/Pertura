# 06. MCP Tool Surface

## Design Rule

MCP tools are evidence boundaries, not a full Perturb-seq analysis menu. Claude remains free to run external analysis code, but runtime-owned tools register evidence, evaluate claims, and render controlled reports.

This prevents tool-surface explosion while keeping the evidence lattice auditable.

## Current Evidence Tools

Implemented in:

```text
src/pertura_runtime/claude/tools/evidence_tools.py
```

Current registrar tools:

- `register_perturbation_design_manifest`
- `register_experiment_design_artifact`
- `register_guide_assignment_artifact`
- `register_target_qc_artifact`
- `register_measured_de_artifact`
- `register_predicted_effect_artifact`
- `register_virtual_perturbation_prediction_artifact`
- `register_prediction_measured_concordance_artifact`
- `register_virtual_cell_state_transition_artifact`
- `register_curated_prior_artifact`
- `register_perturbation_efficiency_artifact`
- `register_curated_enrichment_artifact`
- `register_module_effect_artifact`
- `register_global_effect_artifact`
- `register_composition_effect_artifact`
- `register_cell_qc_artifact`
- `register_control_calibration_artifact`
- `register_replication_artifact`

Decision/report tools:

- `evaluate_claims`
- `render_report`

## Registrar-Owned Truth

Registrars own evidence class and intrinsic ceiling. Source files cannot upgrade themselves by including fields like:

```json
{
  "evidence_class": "measured",
  "strength": "validated_mechanism",
  "validated_mechanism": true
}
```

A prediction registered through `register_predicted_effect_artifact` or the virtual perturbation registrars remains prediction evidence regardless of self-tags.

## Path Boundaries

Registration tools should read evidence sources only from workspace evidence roots such as:

```text
outputs/
artifacts/
```

Reports are final surfaces and should not become evidence sources.

## next_claim_template

A+C closure added `next_claim_template` to claimable registrar responses.

Claimable registrars return:

```json
{
  "next_claim_template": {
    "scope": { "...": "artifact canonical scope" },
    "evidence_refs": ["artifact_id"]
  }
}
```

The template intentionally does not contain:

- requested strength;
- suggested conclusion;
- evidence class upgrade;
- multi-artifact decision logic.

Metadata/eligibility artifacts do not return direct claim templates. This prevents design manifest, target QC, guide assignment, cell QC, or control calibration artifacts from being mistakenly used as effect evidence refs.

Control calibration artifacts are eligibility evidence only. NTC-vs-NTC and label-permutation checks can downgrade measured-strength eligibility under strict/paper profiles, but they never create effect evidence or biological conclusions.

## Virtual Perturbation Wrapper Tools

The first external-wrapper family adds specific Claude-facing registrars:

- `register_virtual_perturbation_prediction_artifact`
- `register_prediction_measured_concordance_artifact`
- `register_virtual_cell_state_transition_artifact`

These tools harvest structured outputs from GEARS, scGPT, Geneformer, CPA/scGen, CellOracle, or custom scripts. They do not train models, run heavy GPU inference by default, or allow prediction/concordance to become measured evidence.

## Future P2 Tool Surface

P2 should avoid one MCP tool per method. It should move toward artifact-family APIs:

- `register_scope_artifact`
- `register_measured_effect_artifact`
- `register_inferred_structure_artifact`
- `register_prediction_artifact`
- `register_ranking_artifact`

Method-specific differences should live in subtype validators, provenance, and policy rules.
