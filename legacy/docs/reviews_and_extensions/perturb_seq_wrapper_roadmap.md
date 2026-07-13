# Perturb-seq Extension Roadmap: Stages, Skills, and Wrappers

This document is the implementation roadmap for extending Pertura from an evidence gate into a practical, evidence-aware Perturb-seq analysis agent.

The core architecture remains:

```text
free CodeAct
  -> stage skill / deterministic wrapper
  -> structured evidence artifact
  -> EvidencePredicate
  -> WarrantRule
  -> ClaimDecision
  -> controlled scientific surface
```

Pertura should help users analyze real Perturb-seq data, but it must not become an unconstrained pipeline runner. The contribution is not "Claude can run more tools"; the contribution is that every scientific conclusion is calibrated by registered execution evidence.

## 0. Organizing Principles

### Extension Types

Use the following labels when planning a new capability:

| Label | Meaning |
| --- | --- |
| `[field]` | Schema, policy, or `EligibilityProfile` field. |
| `[skill]` | Stage card or instruction that guides free CodeAct. |
| `[wrapper]` | Deterministic runner or output harvester with provenance. |
| `[gate]` | Resolver, warrant, policy, or controlled renderer behavior. |
| `[eval]` | Benchmark, smoke test, or evaluator. |
| `[UX]` | Product usability, report, or explanation layer. |

### Tool vs. Skill Decision Rule

Before adding a method, ask:

```text
If Claude hand-writes this analysis, can it easily produce an artifact that
looks plausible but is statistically invalid and then passes the gate?
```

If yes, implement it as a `[wrapper]` or deterministic harvester. Examples: pseudobulk DE, SCEPTRE, Milo/scCODA, Mixscape, and prediction-measured concordance.

If no, a `[skill]` is usually enough. Examples: exploratory inspection, choosing which analysis stage to run next, documenting caveats, or writing analysis notes.

The reason is simple: Pertura's strength comes from verified execution and structured evidence, not from LLM prose.

### Integration Pattern

All wrappers should follow the same pattern:

```text
runner or harvester
  -> structured output file
  -> existing register_* tool
  -> predicate-specific warrant rule
  -> controlled report
```

Most extensions should reuse existing registrars and predicates. New schema should be added only when an evidence type cannot be faithfully represented by the current predicate/warrant system.

## 1. Recommended Phase Order

The practical product roadmap and the paper-safety roadmap are slightly different. Product wrappers are useful, but real-data readiness and statistical safety must come first.

Recommended order:

```text
Phase 0: Real-data intake foundation
Phase 1a: Minimal statistical safety layer
Phase 2: Common biological wrappers, especially pertpy/scverse-adjacent tools
Phase 1b: Full rigorous DE and independence-aware measured claims
Phase 3: Virtual perturbation, network, GI, and trajectory extensions
Phase 4: Product UX and large benchmark freeze
```

## 2. Phase 0: Real-Data Intake Foundation

Goal: make real Perturb-seq workspaces readable before trying to make strong scientific claims.

| Item | Type | Insertion Point | Purpose |
| --- | --- | --- | --- |
| Content-based AnnData preflight | `[wrapper][field]` | `pertura_workflow/preflight.py` | Inspect `.obs`, `.var`, `.layers`, `.obsm`, and `.uns`; avoid filename-only guesses. |
| Guide/protospacer calling and MOI estimation | `[wrapper]` | `register_perturbation_design_manifest`, `register_guide_assignment` | Build manifest candidates from real data. |
| Perturbation modality expansion | `[field]` | policy and warrant modality tables | Add base editing, prime editing, ORF overexpression, shRNA, Cas13, etc. |
| Registration-time validation | `[gate]` | `registry.register_*` validation helpers | Reject or mark incomplete artifacts earlier, while leaving claim truth to resolver/warrant. |
| Replication summary branch repair | `[gate]` | resolver claim-artifact path | Ensure replication artifacts can participate in claim decisions when present. |
| Strict policy profile | `[gate]` | `policy.py` | Separate smoke-friendly behavior from paper/benchmark behavior. |

Do not call registration-time validation a "gate" in docs or code comments. The claim-conditioned gate remains the resolver/warrant layer.

## 3. Phase 1a: Minimal Statistical Safety Layer

Goal: prevent the system from over-trusting measured artifacts before wrappers expand.

| Item | Type | Insertion Point | Purpose |
| --- | --- | --- | --- |
| `replicate_scope` | `[field][gate]` | `EligibilityProfile` and warrant rules | Track donor/batch/lane/sample independence. |
| Pseudoreplication warning/downgrade | `[gate]` | measured predicates | Prevent cell-count-only evidence from supporting aggregate biological claims. |
| `control_calibration` | `[field]` | eligibility and policy | Record empirical NTC/null calibration. |
| `power` / detectable effect size | `[field]` | target QC and measured predicates | Distinguish "no effect" from "no power". |
| `viability_confound` | `[field][skill]` | target QC and stage cards | Surface survivorship or essential-gene bias. |
| Per-guide representation and efficiency | `[field]` | guide assignment / target QC | Avoid treating one weak guide as strong target evidence. |
| Trusted runner method whitelist | `[gate]` | policy + registry validation | Prevent user/LLM metadata strings from impersonating trusted methods. |

This layer should be small but mandatory before broad wrapper expansion.

## 4. Phase 2: Common Biological Wrappers

Goal: make Pertura feel useful on real Perturb-seq data by wrapping common methods while preserving predicate/warrant boundaries.

Prioritize methods that can reuse existing artifact kinds.

| Method Family | Type | Target Registrar | New Schema Needed? |
| --- | --- | --- | --- |
| Mixscape / Mixscale | `[wrapper][skill]` | `register_perturbation_efficiency` | No |
| scPerturb E-distance | `[wrapper]` | `register_global_effect` | No |
| Milo / scCODA | `[wrapper]` | `register_composition_effect` | No |
| Augur | `[wrapper][gate]` | `register_ranking_artifact` | Usually no, but needs ranking predicate handling |
| decoupler / GSEApy / g:Profiler | `[wrapper]` | `register_curated_enrichment` | No |
| cNMF / Hotspot | `[wrapper]` | `register_module_effect` | No |
| Scrublet / scDblFinder | `[wrapper][skill]` | `register_cell_qc` | No |
| CellBender / SoupX | `[wrapper][skill]` | `register_cell_qc` | No |
| CellTypist / Azimuth | `[wrapper]` | `register_cell_state_reference` | No |

Wrappers must never emit final scientific prose. They emit structured files and call registrars.

## 5. Phase 1b: Rigorous Measured Effect Methods

Goal: make `measured_association` defensible for review.

| Item | Type | Insertion Point | Purpose |
| --- | --- | --- | --- |
| Pseudobulk DE with edgeR / DESeq2 / dreamlet | `[wrapper]` | `register_measured_de` | Avoid pseudoreplication in DE. |
| SCEPTRE runner | `[wrapper]` | `register_measured_de` | CRISPR Perturb-seq calibrated DE path. |
| NTC empirical null metadata | `[field]` | `control_calibration` | Record number of NTCs and p-value calibration. |
| Replicate-axis gate | `[field][gate]` | `EligibilityProfile.replicate_scope` | Separate biological replicates from cells. |
| Power and guide consistency | `[field][gate]` | target QC / guide assignment | Distinguish weak data from absent effect. |

Phase 1b can be implemented after initial common wrappers, but Phase 1a should not be skipped.

## 6. Phase 3: Prediction, Network, GI, and Trajectory

Goal: support virtual perturbation and inferred mechanism-adjacent tools without turning them into validated mechanisms.

| Method Family | Type | Target Registrar | New Schema Needed? |
| --- | --- | --- | --- |
| GEARS / CPA / scGen / scGPT | `[wrapper]` | `register_virtual_perturbation_prediction` | No |
| CellOracle / pySCENIC | `[wrapper]` | `register_virtual_cell_state_transition` | No |
| Prediction-measured concordance | `[wrapper]` | `register_prediction_measured_concordance` | No |
| Genetic interaction / epistasis | `[field][gate]` | New `combinatorial_effect` kind | Yes |
| Trajectory / fate with CellRank/scVelo | `[wrapper][gate]` | New `fate_transition` kind | Yes |

Prediction-measured concordance is concordance only. It cannot validate a mechanism and cannot create measured strength unless the bound measured artifact independently supports the measured claim.

## 7. Phase 4: Product UX and Benchmarking

Goal: make the system usable and defensible.

| Item | Type | Purpose |
| --- | --- | --- |
| Unified `run_analysis(method, adata_ref, scope, params)` interface | `[wrapper]` | Avoid dozens of bespoke runner entrypoints. |
| Inline evidence-gap recommendations | `[UX]` | Attach actionable next steps to blocked/downgraded decisions. |
| `explain` and provenance graph | `[UX]` | Show why a claim was downgraded and what evidence is missing. |
| Single source of truth for prompt/contracts | `[UX]` | Generate prompt snippets from stage contracts where possible. |
| Decision-labeled benchmark | `[eval]` | Compare free CodeAct, prompt guardrails, and Pertura gate. |
| Second model provider | `[eval]` | Show the gate sits outside any one model. |

## 8. Unified Runner Signature

All deterministic runners should converge toward this internal shape:

```python
run_analysis(
    method: str,
    adata_ref: str,
    scope: dict,
    params: dict,
    registry: EvidenceRegistry,
) -> EvidenceArtifact
```

Where `scope` is UID/manifest based:

```json
{
  "design_manifest_id": "...",
  "perturbation_uid": "...",
  "control_uid": "...",
  "contrast_uid": "...",
  "estimand": "..."
}
```

General rules:

1. Supported registrars must backfill `source_sha256`, and wrappers should provide `code_sha256` / `execution_hash` when possible.
2. Missing warrant-bearing fields must downgrade to `observation`; audit-only fields should be recorded but must not raise claim strength.
3. Raw labels and reported scope intuition are diagnostics only. Runtime UID binding decides scope fit.

## 9. Wrapper I/O Contracts

### A. Identity and Intake

#### A1. Guide/protospacer calling and MOI

Recommended methods:

```text
guide_calling_cellranger
guide_calling_mixture
```

Inputs:

```text
guide_count_matrix
guide_to_target_csv
moi_method
assignment_threshold
```

Registrars:

```text
register_perturbation_design_manifest
register_guide_assignment
```

Warrant-bearing fields:

```text
assignment_method
guide_to_target_map_hash
moi_inference
multi_guide_count
scope
```

### B. Measured DE

#### B1. Pseudobulk DE

Recommended methods:

```text
pseudobulk_edger
pseudobulk_deseq2
dreamlet
```

Required inputs:

```text
contrast_uid
replicate_key
min_cells_per_replicate
counts_layer
gene_columns
```

Registrar:

```text
register_measured_de
```

Warrant-bearing fields:

```text
contrast_left
contrast_baseline
method
n_left
n_baseline
multiple_testing
has_padj
scope
eligibility.replicate_scope
```

#### B2. SCEPTRE

Recommended method:

```text
sceptre
```

Additional required inputs:

```text
ntc_guides
side
resampling_n
covariates
```

Additional warrant-bearing fields:

```text
eligibility.control_calibration.empirical_null
eligibility.control_calibration.n_ntc
eligibility.control_calibration.calibrated_pvalue
```

### C. Perturbation Efficiency / Target Engagement

#### C1. Mixscape / Mixscale

Recommended methods:

```text
mixscape
mixscale
```

Registrar:

```text
register_perturbation_efficiency
```

Warrant-bearing fields:

```text
perturbation
target_gene
modality
expected_direction
observed_direction
effect_size or pvalue/padj
method
n_target_cells
n_control_cells
quality.pct_perturbed
quality.pct_escaping
scope
```

Boundary:

```text
target engagement != downstream mechanism validation
```

### D. Global Effect

#### D1. scPerturb E-distance

Recommended method:

```text
edistance
```

Registrar:

```text
register_global_effect
```

Warrant-bearing fields:

```text
metric
feature_space or embedding
comparison_method
effect_size or distance
null_model or permutation_or_test
pvalue or padj
n_target_cells
n_control_cells
scope
```

Boundary:

```text
global transcriptomic shift != gene-specific DE
global transcriptomic shift != causal fate conversion
```

### E. Composition Effect

#### E1. Milo / scCODA

Recommended methods:

```text
milo
sccoda
```

Registrar:

```text
register_composition_effect
```

Warrant-bearing fields:

```text
state_source
state_assignment_column
comparison_method
state_counts_by_condition
state_level_deltas
effect_size or statistic
pvalue or padj
n_target_cells
n_control_cells
scope
```

Boundary:

```text
composition shift != gene-specific DE
composition shift != target engagement
composition shift != causal fate conversion
```

### F. Enrichment and Modules

#### F1. decoupler / GSEApy / g:Profiler

Registrar:

```text
register_curated_enrichment
```

Warrant-bearing fields:

```text
input_measured_artifact_id
input_gene_set_hash
background_universe
database
database_version
term_id
method
pvalue or padj
```

Boundary:

```text
curated enrichment provides measured context only when bound to a valid
measured artifact; it does not validate a mechanism
```

#### F2. cNMF / Hotspot

Registrar:

```text
register_module_effect
```

Warrant-bearing fields:

```text
module_id or module_name
module_source
module_gene_set_hash
scoring_method
effect_size
method
pvalue or padj
n_target_cells
n_control_cells
scope
```

Boundary:

```text
module score shift != driver validation
```

### G. QC and State Context

#### G1. Doublet and ambient correction

Recommended methods:

```text
scrublet
scDblFinder
cellbender
soupx
```

Registrar:

```text
register_cell_qc
```

Warrant-bearing fields:

```text
n_cells_after_qc
qc_policy
doublet_policy
ambient_policy
batch_qc
passed
scope
```

Boundary:

```text
cell QC is eligibility evidence, not biological effect evidence
```

#### G2. CellTypist / Azimuth

Registrar:

```text
register_cell_state_reference
```

Warrant-bearing fields:

```text
assignment_column
embedding_methods
clustering_method
annotation_method
marker_summary_path
source_data_path
source_data_sha256
```

Boundary:

```text
cell state reference defines context only
```

### H. Prediction and Mechanism-Adjacent Outputs

#### H1. GEARS / CPA / scGen / scGPT

Registrar:

```text
register_virtual_perturbation_prediction
```

Warrant-bearing fields:

```text
tool_name
tool_version
model_name
model_version or model_checkpoint_hash
prediction_method
prediction_type
perturbation_query
output_schema
n_predicted_genes or n_predicted_cells
scope
```

Boundary:

```text
virtual perturbation prediction != measured effect
```

#### H2. CellOracle / pySCENIC

Registrar:

```text
register_virtual_cell_state_transition
```

Warrant-bearing fields:

```text
tool_name
model_or_network_provenance
transition_type
perturbation_query
state_space_reference
scope
```

Boundary:

```text
predicted transition != causal fate conversion
```

#### H3. Prediction-measured concordance

Registrar:

```text
register_prediction_measured_concordance
```

Warrant-bearing fields:

```text
prediction_artifact_id
measured_artifact_id
metric
metric_value
denominator
comparison_method
runtime-computed scope binding
```

Diagnostic-only fields:

```text
reported_scope_match
```

Boundary:

```text
concordance != mechanism validation
concordance cannot create measured strength
```

## 10. Ranking Artifacts: Augur Example

Recommended method:

```text
augur
```

Registrar:

```text
register_ranking_artifact
```

Suggested predicate metadata:

```json
{
  "relation": "perturbation_responsiveness_rank",
  "metric": "AUC",
  "ranked_cell_types": ["..."],
  "per_type_scores": {"...": 0.0},
  "cv_method": "..."
}
```

Boundary:

```text
ranking != driver validation
ranking != mechanism
```

## 11. Content-Based Preflight to Eligibility Fields

Preflight detects candidates and risks. It does not produce claim strength.

| Detected Item | Source | Candidate Field | Risk / Gate Implication |
| --- | --- | --- | --- |
| Perturbation / guide column | `.obs` columns and values | manifest raw labels and source column | If unidentified, block manifest construction. |
| MOI distribution | guide counts per cell | `moi`, `moi_compatibility` | High-MOI without estimand blocks measured interpretation. |
| NTC/control candidates | `.obs` values matching control aliases | `control_definition.negative_controls` | No control means measured contrasts are blocked. |
| Cells per perturbation | UID-count table | `target_qc.n_target_cells`, `n_control_cells` | Low count downgrades measured predicates. |
| Replicate structure | donor/batch/lane/sample columns | `replicate_scope` | Cell-only independence downgrades aggregate claims. |
| Batch-perturbation confounding | batch x perturbation table | `replicate_scope.confound_flag` | Nested perturbation/batch can block measured claims. |
| Doublet signal | doublet score columns or missing policy | `cell_qc.doublet_policy` | Missing policy warns or downgrades by profile. |
| Ambient signal | raw/filtered data availability | `cell_qc.ambient_policy` | Missing policy warns or downgrades by profile. |
| QC metrics | n genes, total counts, pct mito | `cell_qc.n_cells_after_qc`, `qc_policy` | Missing or failed QC affects eligibility. |
| Modality inference | feature types, metadata, user manifest | `perturbation_modality`, `assay_modality` | Unknown modality disables direction-based claims. |
| Viability bias | essential gene list + low cell count | `target_qc.viability_confound` | Adds survivorship caveat. |
| Normalization state | `.layers`, `.X`, `.raw` | runner input readiness | Missing count layer blocks pseudobulk runner. |
| Guide capture availability | `.var.feature_types` | guide assignment readiness | Missing guide capture suggests guide map input needed. |

## 12. New Schema Candidates

Do not add these until the current predicate/warrant contract is stable.

| Method | New Kind / Predicate | Required Gate Behavior |
| --- | --- | --- |
| Genetic interaction / epistasis | `combinatorial_effect` / `genetic_interaction` | Requires single-A, single-B, and combo UID-linked measured artifacts plus interaction-model statistics. Does not validate mechanism. |
| Trajectory / fate | `fate_transition` measured predicate | Supports measured fate-proportion association only, not causal fate conversion. |

Before adding either, define:

```text
positive path:
  complete metadata + UID scope -> intended maximum strength

negative path:
  overclaim / wrong claim type / missing scope -> downgrade or unsupported
```

Each extension must include at least one overclaim regression test.

## 13. Cross-Cutting Requirements for Runners

1. **Unified eligibility injection**

   All measured runners should write relevant eligibility fields:

   ```text
   replicate_scope
   control_calibration
   power
   target_qc
   cell_qc
   guide_assignment
   ```

2. **Trusted method whitelist**

   `GatePolicy` should eventually include trusted method identifiers. A string field alone must not impersonate SCEPTRE, edgeR, DESeq2, Milo, scCODA, or any other trusted method.

3. **Preflight detection is not strength**

   Detected fields are candidates. They affect recommendation and readiness, but claim strength requires registered artifacts and warrant evaluation.

4. **Scope binding is runtime-owned**

   Claude may report scope intuition, but resolver-owned UID binding determines `scope_fit`.

5. **No final prose from wrappers**

   Wrapper outputs are files and structured metadata. User-visible scientific conclusions must come from `ClaimDecision` surfaces.

## 14. Tradeoffs and Priorities

The three main axes are:

1. **Real data can enter the system**

   Requires Phase 0: content preflight, guide/control detection, and manifest construction.

2. **Measured evidence is credible**

   Requires Phase 1a/1b: independence, pseudobulk/SCEPTRE paths, and control calibration.

3. **Users feel the system is useful**

   Requires Phase 2: common biological wrappers such as pertpy, enrichment, module, composition, global effect, and annotation tools.

Recommended immediate sequence:

```text
1. Strict policy profile
2. Content-based AnnData preflight
3. Minimal independence / pseudoreplication eligibility fields
4. Trusted runner method whitelist
5. pertpy/scverse wrapper batch
6. Rigorous pseudobulk/SCEPTRE measured DE
7. Virtual perturbation and network wrappers
8. Product UX and benchmark freeze
```
