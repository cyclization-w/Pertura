# 03. Evidence Lattice

## Core Objects

### EvidenceArtifact

Runtime-validated metadata for a generated evidence file. The registrar owns:

- `artifact_id`
- `kind`
- `evidence_class`
- `artifact_roles`
- `artifact_intrinsic_ceiling`
- `scope`
- `quality`
- `provenance`
- `source_hash`

Model-supplied tags inside source files are ignored for class/strength decisions.

### Claim

A structured proposed scientific statement. Minimal fields:

- `claim_id`
- `text`
- `subject`
- `relation`
- `object`
- `scope`
- `requested_strength`
- `evidence_refs`

### ClaimDecision

The deterministic runtime decision for one claim under one policy:

- `decision`
- `max_strength`
- `scope_fit`
- `supporting_artifacts`
- `missing_artifacts`
- `blocked_requested_strength`
- `decision_reasons`
- `allowed_surface`
- `policy_version`
- `policy_hash`

## Three Axes

Pertura does not use one scalar confidence score.

```text
EvidenceClass:
  observed_metadata
  measured
  predicted
  curated_prior
  measured_inferred
  composite_summary

ArtifactRole:
  scope_definition
  analysis_eligibility
  effect_evidence
  prior_context
  prediction_evidence
  ranking_summary

StrengthCeiling:
  unsupported
  observation
  curated_prior_support
  predicted_effect
  measured_target_engagement
  measured_association
  replicated_measured_association
  validated_mechanism_disabled
```

`unsupported` is a claim decision state, not an artifact evidence class.

## Evidence Class Invariants

- Prediction artifacts support `predicted_effect` only.
- Curated prior lookup supports `curated_prior_support` only.
- Measured artifacts may support measured strengths only if scope and eligibility pass.
- Metadata artifacts define scope or eligibility but do not support biological effect claims by themselves.
- Measured association plus curated prior does not become validated mechanism.
- Predicted plus curated prior does not become measured.
- `validated_mechanism` remains disabled in the current policy.

## Artifact Intrinsic Ceiling vs Claim Strength Ceiling

An artifact can have an intrinsic ceiling, but the final strength is claim-conditioned.

Example:

```text
measured DE artifact intrinsic ceiling: measured_association
claim asks: KLF1 validates erythroid mechanism
claim decision max strength: measured_association, with mechanism request blocked
```

The same artifact may be unsupported for a wrong perturbation, wrong contrast, wrong cell type, or wrong evidence target.

## Controlled Surfaces

Renderer templates are part of the gate. They convert decisions into safe language such as:

- prediction evidence, not measured result;
- curated prior context, not validation;
- target engagement, not downstream mechanism;
- measured module association, not driver confirmation;
- global perturbation response, not gene-specific or causal fate effect.