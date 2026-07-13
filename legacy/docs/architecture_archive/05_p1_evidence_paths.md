# 05. P1 Evidence Paths

P1 extends the evidence lattice without turning Pertura into a pipeline runner. Claude still computes results with normal code. Pertura registers structured outputs and controls what they can support.

## P1.1 Perturbation Efficiency / Target Engagement

Artifact kind:

```text
perturbation_efficiency
```

Maximum supported strength:

```text
measured_target_engagement
```

Supported claims:

- CRISPRi target down -> target engagement.
- CRISPRa target up -> target engagement.
- CRISPR-KO target altered/down can support target engagement with caveats.

Downgrades:

- CRISPRi target up or CRISPRa target down -> observation / conflict.
- KO target mRNA unchanged is not automatic perturbation failure; it is an observation with caveat.
- Missing method/stat/effect fields -> observation.
- Low target/control cell counts under policy -> observation.

Boundary:

```text
target engagement != downstream mechanism
```

## P1.2 Cell QC as Eligibility Evidence

Artifact kind:

```text
cell_qc
```

Evidence class:

```text
observed_metadata
```

Role:

```text
analysis_eligibility
```

Cell QC alone never supports biological effect claims. It can only support or block eligibility for measured artifacts.

Default policy:

- missing cell QC does not block measured claims;
- explicitly failed compatible cell QC downgrades measured strength;
- low post-QC cell count under policy downgrades;
- boolean-only `qc_passed=true` without structured fields does not raise strength.

## P1.3 Curated Enrichment

Artifact kind:

```text
curated_enrichment_result
```

Purpose:

Curated pathway/gene-set context for a measured DE result.

Required boundary:

- must bind to `input_measured_artifact_id` to become measured-context evidence;
- bound measured artifact must support the same claim under resolver;
- otherwise it remains `curated_prior_support`.

Maximum user-facing meaning:

```text
measured association with curated context
not validation
not mechanism
```

## P1.3 Module Effect

Artifact kind:

```text
module_effect
```

Purpose:

Measured association of perturbation with a module/signature score.

Required fields include module ID/name, module source, gene-set hash, scoring method, contrast/scope, effect/stat metadata, and cell counts.

Module source is important:

- `curated_gene_set`
- `external_reference`
- `control_derived`
- `all_cell_derived`
- `prediction_derived`

All-cell-derived modules carry a perturbation-contamination caveat.

Maximum meaning:

```text
measured module-score association
not mechanism
not driver confirmation
not master regulator validation
```

## P1.3 Global Effect

Artifact kind:

```text
global_effect
```

Purpose:

Measured global perturbation response, embedding shift, distance metric, or distribution shift.

Required fields include metric, feature space, comparison method, effect/distance, null model/test, p-value/padj, cell counts, scope, and eligibility.

Maximum meaning:

```text
measured global perturbation response
not gene-specific DE
not downstream mechanism
not causal cell-state transition
```

## P1 Completion Judgment

P1 is complete for the current submission-oriented lattice:

- P1.1 target engagement: complete.
- P1.2 cell QC eligibility: complete.
- P1.3 curated enrichment/module/global effect: complete.
- Evidence workflow A+C closure: complete.

Next work should focus on benchmark/evaluation consolidation rather than adding new evidence kinds.