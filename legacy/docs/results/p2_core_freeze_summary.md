# P2 Core Freeze Summary

## Status

Frozen on 2026-07-08.

This is the baseline before adding external wrappers such as CellOracle, scGPT, GEARS, or CPA. The purpose of this freeze is to lock the hard gate architecture after the stage catalog, composition extension, and predicate/warrant closure.

Latest verification:

```text
python -m pytest -q
184 passed in 10.72s
```

## Frozen Scope

This freeze includes:

- P0.6 manifest UID scope discipline.
- P0.7 strong-baseline gate utility harness.
- P1.1 target engagement / perturbation efficiency.
- P1.2 cell QC eligibility tightening.
- P1.3 curated enrichment, module effect, and global effect.
- P2.0 workflow substrate.
- P2.1 classic guide-based Perturb-seq substrate.
- Evidence-Aware Stage Catalog.
- `cell_state_reference` stage.
- `composition_effect` extension.
- `EvidencePredicate -> WarrantRule -> ClaimDecision -> ControlledSurface` closure.

## Current Architecture

```text
Claude CodeAct / external tools
  -> structured registrar call
  -> EvidenceArtifact
  -> EvidencePredicate
  -> WarrantRule
  -> ClaimDecision
  -> ControlledSurface
```

Package boundaries:

```text
pertura_gate
  trusted identity, evidence, resolver, warrant, and renderer core

pertura_runtime
  Claude runtime and MCP tool surface; untrusted CodeAct carrier

pertura_bench
  benchmark harness and surface evaluator; not part of gate truth

pertura_workflow
  workflow substrate, preflight, candidate harvesting, and next-evidence recommendation
```

`pertura_gate` must not import `pertura_runtime`, `pertura_bench`, or `pertura_workflow`.

## Hard Invariants

- Every scientific conclusion surface must pass through `ClaimDecision`.
- Raw labels and filenames cannot upgrade `scope_fit` or `max_strength`.
- Manifest UID or manifest-declared typed compatibility is required for effect-level scope upgrades.
- Artifact self-tags cannot upgrade `evidence_class`, `evidence_predicate`, or strength ceiling.
- Prediction, prior, measured, inferred, ranking, and metadata evidence cannot launder into each other.
- `validated_mechanism` remains disabled by policy.
- Stage outputs and `EvidenceCandidate` objects are not evidence until registered and validated.
- `StrengthCeiling.measured_association` is not synonymous with differential expression.
- Renderer wording is predicate-specific, not strength-only.

## Implemented Evidence Predicates

```text
metadata_observation
scope_definition
analysis_eligibility
state_context
differential_expression
target_engagement
module_score_shift
global_transcriptomic_shift
cell_state_composition_shift
curated_prior_context
curated_enrichment_context
predicted_effect
replication_summary
```

## Current Claude-Facing Tool Surface

Scope and design:

```text
register_perturbation_design_manifest
register_experiment_design_artifact
```

Eligibility:

```text
register_guide_assignment_artifact
register_target_qc_artifact
register_cell_qc_artifact
```

State context:

```text
register_cell_state_reference_artifact
```

Measured effects:

```text
register_measured_de_artifact
register_perturbation_efficiency_artifact
register_module_effect_artifact
register_global_effect_artifact
register_composition_effect_artifact
```

Context and prediction:

```text
register_curated_prior_artifact
register_curated_enrichment_artifact
register_predicted_effect_artifact
```

Synthesis:

```text
evaluate_claims
render_evidence_report
```

## Frozen Smoke Result

Smoke 13b is the latest architecture-closing smoke.

Saved artifacts:

```text
docs/results/smoke13b_predicate_warrant/
```

Result:

```text
composition_effect
  -> EvidencePredicate.cell_state_composition_shift
  -> measured cell-state composition association
  -> not DE
  -> not target engagement
  -> not causal fate conversion
  -> not downstream mechanism
  -> not driver validation
```

The causal fate/mechanism overclaim is downgraded to `measured_association`; the normal composition association claim is allowed.

## Wrapper Policy After This Freeze

CellOracle, scGPT, GEARS, CPA, and related tools should enter through the same hard path:

```text
external output
  -> structured artifact
  -> EvidencePredicate
  -> WarrantRule
  -> ClaimDecision
  -> ControlledSurface
```

Do not let wrapper prose become scientific surface. Do not let model provenance, confidence, or generated explanations become measured evidence. Do not let prediction-measured concordance become mechanism validation.

A new wrapper should not be added unless it defines:

- evidence predicate;
- required structured fields;
- scope requirements;
- quality predicates;
- maximum strength ceiling;
- claim types it can support;
- claim types it must never support;
- controlled surface wording;
- benchmark fixture or smoke test.

Recommended first wrapper family:

```text
prediction / virtual perturbation with prediction-measured concordance
```

Reason: it directly extends the P0.7 laundering boundary and keeps prediction-vs-measured separation central.
