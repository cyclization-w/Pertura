# 12. Predicate and Warrant Closure

## Status

Implemented and verified.

This document records the Smoke 13 closure that made `EvidencePredicate` and predicate-specific warrant rules part of the gate core. It is no longer a future implementation plan. It is the current architecture boundary for adding new evidence extensions.

Latest verification:

```text
Smoke 13b: completed
Full suite: 184 passed
```

Saved Smoke 13b artifacts:

```text
docs/results/smoke13b_predicate_warrant/smoke13b_summary.md
docs/results/smoke13b_predicate_warrant/smoke13b_summary.json
docs/results/smoke13b_predicate_warrant/reports/evidence_report.md
```

## Why This Document Exists

Smoke 13 exposed a real architectural issue: Pertura's early gate path grew from measured differential expression, and later measured evidence paths could accidentally inherit DE-shaped assumptions.

The failure mode was:

```text
measured_association strength
  was sometimes treated as if it meant differential_expression evidence
```

That assumption is wrong. `measured_association` is a strength ceiling. It is not an evidence predicate.

## What Smoke 13 Showed

Smoke 13a tested the `composition_effect` stage.

Expected behavior:

```text
composition_effect artifact
  -> measured cell-state composition association
  -> not gene-specific DE
  -> not target engagement
  -> not causal fate conversion
  -> not downstream mechanism
  -> not driver validation
```

Observed issues before closure:

1. The first 13a run registered counts in the JSON output, but the registrar/resolver did not recognize the count-table aliases used by the stage output.
2. The first 13a run allowed Claude to invent plausible UID-like values instead of copying manifest-derived UID scope.
3. Artifact-only report rendering described a composition artifact as a differential-expression artifact because renderer logic keyed only on `StrengthCeiling.measured_association`.
4. The first 13b claim report downgraded a valid composition claim to `observation` because the resolver applied guide-based DE eligibility requirements to a composition artifact.

These were variants of one root cause:

```text
artifact kind / predicate semantics were not separated strongly enough from strength ceiling.
```

## Implemented Architecture

The current core data flow is:

```text
EvidenceArtifact
  -> EvidencePredicate
  -> WarrantRule
  -> ClaimDecision
  -> ControlledSurface
```

`EvidencePredicate` names what the artifact actually represents:

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

`StrengthCeiling` remains only the maximum claim strength allowed by the predicate, scope, quality metadata, eligibility, and policy:

```text
unsupported
observation
curated_prior_support
predicted_effect
measured_target_engagement
measured_association
replicated_measured_association
validated_mechanism_disabled
```

## Implementation Points

The closure added or hardened these implementation points:

- `EvidenceArtifact.evidence_predicate` is serialized to registry JSONL.
- Legacy registry rows without `evidence_predicate` are still readable; predicate is inferred from `ArtifactKind`.
- Registrars keep their current MCP names and public parameters.
- MCP registration handoffs and `claimable_artifacts.json` include `evidence_predicate`.
- `resolver/warrant.py` owns predicate-specific intrinsic warrant and controlled surface text.
- `resolver/resolver.py` handles orchestration, scope comparison, decision assembly, and claim-conditioned checks.
- `render/renderer.py` uses predicate-specific artifact-only wording instead of strength-only wording.
- `composition_effect`, `module_effect`, and `global_effect` do not inherit DE-only eligibility wording.

## Current Predicate Mapping

```text
measured_de                    -> differential_expression
perturbation_efficiency         -> target_engagement
module_effect                  -> module_score_shift
global_effect                  -> global_transcriptomic_shift
composition_effect             -> cell_state_composition_shift
curated_enrichment_result      -> curated_enrichment_context
curated_prior_lookup           -> curated_prior_context
predicted_effect               -> predicted_effect
replication_summary            -> replication_summary
perturbation_design_manifest   -> scope_definition
cell_state_reference           -> state_context
experiment_design              -> analysis_eligibility
guide_assignment               -> analysis_eligibility
target_qc                      -> analysis_eligibility
cell_qc                        -> analysis_eligibility
```

## Required Invariants

The predicate/warrant layer enforces these invariants:

```text
StrengthCeiling.measured_association does not imply differential expression.
Renderer wording is predicate-specific, not strength-only.
Eligibility requirements are predicate-specific, not inherited from DE.
Composition shift cannot become gene-specific DE.
Composition shift cannot become target engagement.
Composition shift cannot become causal fate conversion.
Global shift cannot become gene-specific DE.
Module score association cannot become driver validation.
Target engagement cannot become downstream mechanism.
Prediction evidence cannot become measured evidence.
Curated prior/context cannot become validation.
Raw labels cannot upgrade scope or strength.
Artifact self-tags cannot upgrade evidence class or strength.
```

## Smoke 13b Frozen Result

Smoke 13b registers a `composition_effect` artifact with exact manifest UID scope and evaluates two claims:

1. A causal fate/mechanism overclaim.
2. A normal measured composition association claim.

Result:

```text
composition evidence predicate: cell_state_composition_shift
composition intrinsic ceiling: measured_association

smoke13b_composition_as_fate_mechanism:
  decision: allowed_with_downgrade
  max_strength: measured_association
  blocked_requested_strength: causal_fate_conversion
  scope_fit: exact

smoke13b_composition_association:
  decision: allowed
  max_strength: measured_association
  scope_fit: exact
```

Controlled surface:

```text
Registered composition evidence supports a measured cell-state composition association for KLF1. This does not establish a gene-specific effect, target engagement, causal fate conversion, downstream mechanism, or driver validation.
```

The frozen checks all pass:

```text
fate_mechanism_claim_downgraded_to_measured_association: true
composition_association_claim_allowed: true
surface_mentions_composition_association: true
surface_blocks_fate_mechanism: true
surface_not_de: true
```

## Extension Rule After Closure

Do not add a new extension by copying a DE-shaped resolver path.

Every new evidence extension must define:

```text
EvidencePredicate
required structured fields
scope requirements
quality predicates
max strength ceiling
claim types it can support
claim types it must never support
predicate-specific controlled surface text
benchmark fixture
smoke or handoff test
```

## Paper-Facing Interpretation

This closure strengthens the paper claim because it makes heterogeneous evidence first-class.

Pertura's contribution is not that every Perturb-seq artifact is a DE result. The contribution is that heterogeneous Perturb-seq evidence can be registered as structured predicates, and each predicate receives a deterministic warrant that caps what the final scientific surface may say.

Correct framing:

```text
Pertura separates evidence predicate, scope, eligibility, policy, and claim strength.
```

Incorrect framing:

```text
Pertura renders DE results and adds more artifact types around them.
```
