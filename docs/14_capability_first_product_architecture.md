# 14. Capability-First Perturb-seq Product Architecture

> Status: target architecture and implementation reference  
> Scope: Pertura v2; refines `13_product_pivot.md`  
> Audience: runtime, workflow, gate, capability, benchmark, and product developers

## 1. Decision

Pertura will remain a free CodeAct scientific agent with a small deterministic
scientific commit boundary. Analytical coverage grows through capability plugins,
not through new gate taxonomies, one registrar per scientific noun, or a second
workflow engine parallel to Claude CodeAct.

The product must answer four connected questions:

1. What data and experimental design are actually present?
2. Is a perturbation, target response, or predicted response trustworthy enough
   for the requested interpretation?
3. Which analysis is appropriate for this design and what does it support?
4. What is the smallest additional check or experiment that could change the
   answer?

The stable product claim is:

> Claude may explore freely, but only a verifier-signed, scope-bound,
> dependency-complete result may be promoted into a reportable scientific
> statement.

Individual methods, models, databases, and visualizations are replaceable
capabilities around that boundary.

## 2. Normative language and concepts

**MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative.

- A **hard boundary** is enforced by code and cannot be weakened by the model.
- A **capability** is a discoverable analysis or diagnostic unit with declared
  inputs, preconditions, outputs, validator, scope rules, and claim permissions.
- A **scientific validator** is deterministic or version-pinned code that checks
  a capability-specific result.
- A **commit** moves untrusted workspace output into an immutable verified result.
- A **promotion** moves verified results into an allowed user-visible statement.

## 3. Ownership and non-goals

### 3.1 Pertura owns

1. canonical data, perturbation, contrast, context, and replicate identity;
2. explicit design contracts and unresolved-field handling;
3. capability discovery, compatibility filtering, and method routing;
4. isolated execution receipts and method-specific validation;
5. measured, predicted, prior, and hypothesis separation;
6. result dependencies and stale propagation;
7. deterministic statement promotion and final scientific rendering.

### 3.2 Claude CodeAct remains free

Claude MAY:

- inspect files with `Read`, `Glob`, `Grep`, and shell commands;
- write and execute Python, R, shell scripts, and notebooks;
- use installed scientific packages;
- visualize and inspect intermediate results;
- debug failures and revise the analysis plan;
- prototype methods for which no trusted capability exists;
- produce working notes and explicitly exploratory outputs.

Free CodeAct output is not automatically trusted. CodeAct decides what to try;
Pertura decides what may be scientifically committed and promoted.

### 3.3 Pertura does not claim

Pertura does not replace analytical libraries or biological review. It MUST NOT
claim that successful code execution proves method validity, a prediction
validates a mechanism, prior knowledge is measurement, user confirmation creates
an effect, or an unreplicated cell-level test is population-level confirmation.

## 4. Dual-plane tool model

```text
Claude CodeAct
  |
  +-- Free exploration plane
  |     Read / Glob / Grep / Bash / Write / Edit / Notebook
  |     Python and R packages
  |     exploratory files, figures, scripts, and notebooks
  |
  +-- Pertura scientific operation plane
        inspect_dataset
        run_diagnostic
        run_analysis
        evaluate_virtual_model
        finalize_report
```

The high-level tools do not disable or replace CodeAct. They are the narrow path
through which results receive verified receipts and become eligible for claim
promotion.

### 4.1 Trust zones

```text
input/                  read-only source data
outputs/, notebooks/    agent-writable and untrusted
verifier staging        isolated, ephemeral execution area
receipts/, commits/     runtime-owned and agent-inaccessible
reports/                runtime-rendered scientific surface
```

The agent MUST NOT be able to mint a trusted receipt by importing a helper or
writing a JSONL record. Signing material and the authoritative receipt store MUST
be outside the agent process and agent-writable paths.

## 5. Five high-level tools

### 5.1 `inspect_dataset`

Creates or updates a versioned `DatasetContract`.

Responsibilities:

- discover supported formats and file relationships;
- align cell and guide barcodes;
- identify matrices and data layers;
- detect candidate condition, guide, target, control, batch, donor, replicate,
  dose, time, and state columns;
- separate observed, inferred, confirmed, conflicting, and unresolved fields;
- compute source hashes and canonical scope candidates.

It MUST NOT silently convert a guide-like token into a confirmed gene target.

### 5.2 `run_diagnostic`

Executes one diagnostic capability and returns a `ResultEnvelope`. Examples
include assignment QC, ambient guide detection, MOI, target detectability, guide
heterogeneity, escape/responder fraction, state-reference stability, replicate
overlap, confounding, and negative-control calibration.

### 5.3 `run_analysis`

Routes to and executes an analysis capability compatible with the confirmed
design contract. A route MUST expose the design facts, exclusions, and missing
preconditions used. Routing never upgrades a claim.

### 5.4 `evaluate_virtual_model`

Evaluates prediction output under a fixed scope and split contract. It checks
leakage, required baselines, direction, ranking, discriminability, magnitude,
mode collapse, and uncertainty or conformal coverage.

### 5.5 `finalize_report`

Renders the controlled surface from committed results. It rejects stale or
dependency-incomplete results, distinguishes findings/predictions/priors/
hypotheses, exposes design and method limitations, and links promoted statements
to receipts. Exploratory CodeAct output may appear only in a labeled appendix.

## 6. Core contracts

The target kernel uses a small stable object set. Analytical details live inside
capability payloads rather than new global enums.

### 6.1 `DatasetContract`

```yaml
schema_version: pertura-dataset-contract-v2
contract_id: dataset_contract:abc123
source_files:
  - path: input/screen.h5ad
    sha256: sha256:...
assays:
  rna: {container: anndata, matrix: X, scale: counts}
  guide_capture: {container: feature_barcode_matrix}
identity:
  cell_barcode_column: cell_id
  perturbation_column: perturbation_uid
  guide_column: guide_id
design:
  perturbation_modality: crispri
  moi: low
  replicate_unit: donor
  donor_column: donor
  batch_column: batch
confirmed_fields: [assays.rna.scale, design.replicate_unit]
inferred_fields:
  - path: identity.control_labels
    value: [NTC]
    confidence: 0.93
    reason: label_pattern
unresolved_fields: [guide_to_target_map]
conflicts: []
```

Only observed or confirmed fields MAY satisfy hard preconditions. Inference may
guide exploration and questions but MUST NOT raise claim strength.

### 6.2 `ScopeKey`

`ScopeKey` is the only authoritative scope representation.

```yaml
dataset_uid: dataset:screen_001
assay_uid: assay:rna
perturbation_uid: target:STAT1
control_uid: control:negative_control_pool
contrast_uid: contrast:stat1_crispri_vs_ntc
cell_context_uid: state:myeloid_01
replicate_unit: donor
dose: null
time: null
```

Comparison returns `exact`, `compatible_by_declared_rule`, `broader`, `narrower`,
`mismatch`, or `unresolved`. `Unresolved` cannot satisfy an effect dependency.
Compatibility rules belong to capability specs, not raw-string overlap.

### 6.3 `CapabilitySpec`

```yaml
schema_version: pertura-capability-v1
capability_id: de.pseudobulk.edger.v1
version: 1.0.0
phase: effect_estimation
kind: analysis
accepts:
  assays: [rna]
  scales: [counts]
  perturbation_modalities: [crispri, crispra, knockout]
preconditions:
  - controls.confirmed
  - assignment.screen_passed
  - replicate_unit.confirmed
  - replicate_overlap.minimum_two
executor:
  backend: isolated_r
  entrypoint: pertura_edger_runner.R
  environment_lock: environments/edger-v1.lock
validator: {id: validate.edger_pseudobulk.v1}
outputs: {result_type: measured_result, metrics_schema: de_result_v1}
claim_permissions:
  families: [differential_expression, measured_association]
  maximum_strength: measured_association
scope_rules:
  output_inherits: [dataset_uid, contrast_uid, cell_context_uid]
```

A capability MUST declare a validator. Installation or process exit zero is not
sufficient for trust.

### 6.4 `ResultEnvelope`

```yaml
schema_version: pertura-result-envelope-v1
result_id: result:...
capability_id: target_reliability.v2
result_type: diagnostic
status: screen_passed
scope: {...ScopeKey}
metrics: {}
findings:
  - code: target_gene_low_detectability
    severity: caution
    message: Control detection is below the profile threshold.
blockers: []
cautions: [target_gene_low_detectability]
recommended_actions: [Run signature-level efficacy analysis.]
dependencies: [dataset_contract:abc123]
files:
  - role: target_reliability_table
    path: outputs/target_reliability.parquet
    sha256: sha256:...
profile:
  id: crispri_screen_v1
  version: 1.0.0
  benchmark_reference: benchmark:target_reliability_v1
receipt_id: receipt:...
```

Status vocabulary:

- diagnostics: `screen_passed`, `caution`, `blocked`, `unresolved`, `failed`;
- analyses: `completed`, `completed_with_caution`, `blocked`, `failed`;
- virtual evaluation: `supported`, `limited`, `out_of_scope`, `failed`.

`Eligible` is not used as a universal scientific certification.

### 6.5 `RunReceipt`

```yaml
schema_version: pertura-run-receipt-v1
receipt_id: receipt:...
capability_id: de.pseudobulk.edger.v1
runner_digest: sha256:...
environment_digest: sha256:...
dataset_contract_hash: sha256:...
input_hashes: {}
parameter_hash: sha256:...
output_hashes: {}
validator_id: validate.edger_pseudobulk.v1
validator_version: 1.0.0
validator_status: passed
created_at_utc: ...
signature: ...
```

The receipt is issued by an isolated authority and verified again at promotion.

### 6.6 `ScientificStatement` and `PromotionDecision`

```yaml
statement_id: statement:...
family: differential_expression
subject: target:STAT1
relation: associated_with_change_in
object: gene_module:interferon_response
scope: {...ScopeKey}
requested_strength: measured_association
result_refs: [result:...]
```

```yaml
decision_id: decision:...
state: allowed_with_downgrade
allowed_strength: exploratory_measured_result
reason_codes: [insufficient_independent_replicates]
dependency_status: current
surface_text: ...
```

The model may propose statements. Only the promotion engine selects strength.

## 7. Capability lifecycle

```text
discover
  -> filter by DatasetContract
  -> check hard preconditions
  -> report or resolve missing fields
  -> execute in verifier environment
  -> validate result semantics
  -> issue RunReceipt
  -> commit ResultEnvelope
  -> make eligible for statement promotion
```

Claude initially sees only ID, summary, compatible objectives, and key blockers.
Full instructions and schemas are loaded after selection.

Adding a capability SHOULD require one spec, executor, validator, metrics schema,
scientific golden tests, failure fixtures, and benchmark provenance. It SHOULD NOT
require a global artifact kind, predicate, MCP registrar, warrant branch, or
stage contract.

## 8. Seven Perturb-seq product phases

The phases organize capabilities and product progress; they are not a fixed
workflow graph. Claude may revisit them as diagnostics change the plan.

```text
1. Data and design intake
2. Guide assignment and screen QC
3. Cell-state reference construction
4. Target efficacy and response reliability
5. Effect estimation
6. Biological interpretation
7. Virtual experiments and next-round design
```

### 8.1 Phase 1: data and design intake

**Goal:** turn heterogeneous files into a confirmed `DatasetContract` without
inventing experimental design.

Input support:

- 10x CRISPR feature-barcode matrices and Cell Ranger outputs;
- H5AD/AnnData and MuData;
- Seurat-converted H5AD or explicitly exported matrices/metadata;
- expression tables, metadata, guide libraries, and sample sheets.

Initial capabilities:

- `intake.discover_files.v1`
- `intake.anndata_contract.v1`
- `intake.10x_crispr_contract.v1`
- `intake.mudata_contract.v1`
- `intake.barcode_alignment.v1`
- `intake.layer_semantics.v1`
- `intake.design_candidate_detection.v1`

Required diagnostics include dimensions, sparsity, barcode uniqueness/overlap,
layer semantics, duplicated/missing metadata, candidate design fields, and
cross-file contradictions.

Blocking conditions include unalignable cells/metadata, absent assignment source,
unresolved layer semantics for count-required methods, and ambiguous control or
contrast identity required by the requested analysis.

**MVP acceptance:** deterministic contracts on public fixtures; every unresolved
authority field is reported; name-based guesses never become confirmed mappings.

### 8.2 Phase 2: guide assignment and screen QC

**Goal:** determine whether perturbation-to-cell assignment and screen technical
quality support downstream target or effect analysis.

Initial capabilities:

- `assignment.guide_umi_mixture.v1`
- `assignment.ambient_guide.v1`
- `assignment.barcode_overlap.v1`
- `assignment.reverse_complement_check.v1`
- `assignment.multi_guide_doublet.v1`
- `assignment.moi_profile.v1`
- `assignment.guide_target_integrity.v1`
- `qc.cell_basic.v1`
- `qc.sample_balance.v1`

Required outputs include per-cell assignment/confidence, ambiguous/unassigned
fractions, signal/background distributions, ambient estimates, singlet/multi-guide
fractions, MOI uncertainty, guide/target coverage, sample balance, and an explicit
retained-cell manifest.

Scientific boundaries:

- assignment confidence is not perturbation efficacy;
- multiple guides are not automatically doublets in high-MOI/combinatorial designs;
- filtering decisions are versioned downstream dependencies;
- changing retained cells makes downstream results stale.

**MVP acceptance:** planted ambient, barcode, reverse-complement, multi-guide,
coverage, and guide-map failures are evaluated with sensitivity and false alarms.

### 8.3 Phase 3: cell-state reference construction

**Goal:** create a versioned state space and gene-module vocabulary for stratified
effects, composition, response programs, state transitions, and virtual-model
evaluation. A state reference is a scientific dependency, not a plotting side
effect.

#### 8.3.1 Define the reference state space

The state space may be fitted from:

1. control cells in the current dataset;
2. all cells with perturbation-aware safeguards;
3. an external matched reference or atlas;
4. a hybrid mapping from internal cells to an external reference.

Initial capabilities:

- `state.preprocess_reference.v1`
- `state.embedding_pca.v1`
- `state.neighbor_graph.v1`
- `state.cluster_leiden.v1`
- `state.annotation_marker_assisted.v1`
- `state.annotation_reference_mapping.v1`
- `state.reference_stability.v1`
- `state.perturbation_leakage_check.v1`

A committed `StateReference` records source population, inclusion criteria,
whether perturbed cells were used for fitting, preprocessing and algorithms,
random seeds, stable state IDs, human labels separately, annotation evidence,
uncertain labels, stability across seed/resolution/batch/held-out samples, mapping
method for new cells, and hashes.

LLM, marker, or atlas annotation is contextual evidence and cannot silently
become ground truth. Uncertain states retain technical IDs such as
`state:cluster_07`.

#### 8.3.2 Define state-describing gene modules

Modules may come from curated databases, external matched data, published
signatures, control cells, the current Perturb-seq data, or aligned consensus.

Initial capabilities:

- `module.import_curated.v1`
- `module.import_external.v1`
- `module.learn_cnmf.v1`
- `module.learn_nmf.v1`
- `module.learn_coexpression.v1`
- `module.score.v1`
- `module.stability.v1`
- `module.align_and_deduplicate.v1`

A `GeneModuleReference` records source class (`curated`, `external_measured`, or
`internally_learned`), species and identifiers, genes/weights, fitting population,
preprocessing, stability/source version, use of perturbation labels or test data,
semantic-label provenance, and permitted downstream uses.

Leakage and circularity rules:

- label-trained modules cannot independently confirm the same perturbation effect;
- a reference fitted on a virtual-model test set cannot support an unqualified
  held-out-generalization claim;
- frozen control/external references are preferred for confirmatory composition
  and state-transition claims;
- data-derived modules are measured structures, while biological names assigned
  by enrichment or LLMs remain prior-supported hypotheses.

**Required outputs:** versioned state and module references, per-cell assignments
with uncertainty, module scores, stability report, and reuse/leakage policy.

**MVP acceptance:** construct a control-derived PCA/neighbor/Leiden reference,
map perturbed cells without refitting, learn or import modules, quantify stability,
and block circular independent-validation claims.

### 8.4 Phase 4: target efficacy and response reliability

**Goal:** determine whether a nominal assignment produced a detectable consistent
response and whether target-level pooling is defensible.

Initial capabilities:

- `reliability.target_expression.v2`
- `reliability.signature_efficacy.v1`
- `reliability.guide_heterogeneity.v1`
- `reliability.escape_responder.v1`
- `reliability.mixscape_adapter.v1`
- `reliability.mixscale_adapter.v1`
- `reliability.batch_overlap.v1`
- `reliability.replicate_overlap.v1`

Routing rules:

- direct target direction is one signal only when control detectability is adequate;
- low detectability means zeros cannot be interpreted as knockdown; use signatures,
  state displacement, or orthogonal evidence;
- retain guide-level effects before target pooling;
- expose escape/responder assumptions and uncertainty;
- Mixscape/Mixscale adapters are diagnostics, not ground truth.

Thresholds live in a versioned profile with benchmark provenance. The successful
status is `screen_passed`, not universal `eligible`.

**MVP acceptance:** expert usable/caution/exclude labels plus planted detectability,
guide-disagreement, escape, and confounding cases.

### 8.5 Phase 5: effect estimation

**Goal:** estimate effects with a method appropriate to experimental unit, MOI,
contrast, outcome, state reference, and estimand.

Capability families:

- replicated low-MOI: `de.pseudobulk.edger.v1`, `deseq2.v1`, `muscat.v1`;
- high-MOI/combinatorial: `association.sceptre.v1`, `conditional_glm.v1`;
- composition: `composition.milo.v1`, `sccoda.v1`, `replicate_level.v1`;
- state/module/global: module score, state transition, program activity, global shift;
- sensitivity: guide/replicate leave-one-out;
- calibration: NTC-vs-NTC, label permutation, negative-control genes.

Every route states experimental and replicate unit, estimand, contrast, formula,
covariates, layer requirements, overlap/calibration requirements, and excluded
methods. The current normal-approximation pseudobulk prototype remains exploratory
until replaced by or validated against a replicate-aware count model.

A confirmatory measured association normally requires confirmed assignment and
controls, exact/declared-compatible scope, independent replicates, passed method
validator, effect and uncertainty, multiplicity handling, no blocking confounding,
required calibration, and current cell/state/module dependencies.

**MVP acceptance:** trusted wrappers reproduce authoritative package outputs and
fail safely for invalid layers, missing replicates, confounding, or scope mismatch.

### 8.6 Phase 6: biological interpretation

**Goal:** connect committed effects to response programs, pathways, regulatory
hypotheses, literature, contradictory evidence, and discriminating next checks
without laundering interpretation into measurement.

Initial capabilities:

- perturbation clustering and response programs;
- ORA/GSEA;
- regulator and GRN hypothesis inference;
- source-backed literature support;
- contradiction search;
- explicit hypothesis synthesis.

Evidence rules:

- enrichment from measured rankings is derived interpretation, not mechanism;
- curated pathways and literature are prior evidence;
- same-dataset GRNs are measured-inferred structures with limitations;
- LLM causal explanations are hypotheses;
- multi-agent agreement increases consensus, not evidence class.

Reports separate measured observations, derived programs, supporting priors,
contradictions, hypotheses, and experiments that distinguish hypotheses.

**MVP acceptance:** every mechanistic sentence is traceable to measurement, prior,
or an explicit hypothesis; removing priors cannot increase strength.

### 8.7 Phase 7: virtual experiments and next-round design

**Goal:** evaluate virtual perturbation models under an explicit generalization
contract and use verified results plus uncertainty to recommend the next experiment.

Virtual capabilities cover scope contract, split audit, mean/context/linear/additive
baselines, direction, rank, discriminability, collapse, uncertainty, conformal
coverage, and measured concordance.

Prediction scope includes seen/unseen perturbation and context, combination,
dose/time interpolation or extrapolation, donor generalization, train/validation/
test identities, and whether state/modules used evaluation data.

No single aggregate correlation is sufficient. At minimum report baseline
performance, direction recovery, perturbation/transposed ranking, discriminability,
prediction variance, baseline win rate, magnitude error, and uncertainty coverage.

Next-round capabilities cover uncertainty sampling, information gain, program
coverage, biological diversity, cost-constrained panels, and minimum
disambiguating experiments. Recommendations consume committed evidence,
uncertainty, feasibility, cost, and diversity. LLM knowledge may rerank or explain
but cannot replace quantitative acquisition evidence without a cold-start label.

**MVP acceptance:** detect planted leakage and collapse, prevent models losing to
simple baselines from receiving a strong verdict, and reproduce fixed panel
recommendations under fixed profiles.

## 9. Dependencies and stale propagation

```text
DatasetContract
  +-- AssignmentResult -> retained-cell manifest, guide/target mapping
  +-- StateReference -> GeneModuleReference, state assignments
  +-- TargetReliabilityResult -> assignment, optional state/modules
  +-- MeasuredEffectResult -> assignment, reliability, design,
  |                           calibration, optional state/modules
  +-- InterpretationResult -> committed measured/predicted results
  +-- VirtualEvaluationResult -> scope, splits, baselines, references
```

Changing a dependency hash marks downstream results stale. Stale records remain
auditable but cannot support a current promoted statement.

## 10. Scientific commit and promotion

### 10.1 Stable source classes

| Source class | Meaning | Default maximum |
| --- | --- | --- |
| `observed_metadata` | directly observed design/file facts | observation |
| `measured_result` | verified analysis of measured data | capability ceiling |
| `prediction` | model output | predicted effect |
| `curated_prior` | database, atlas, literature | prior support |
| `hypothesis` | LLM/GRN/consensus explanation | hypothesis only |

Clusters, modules, and inferred GRNs carry a source class plus derivation
descriptor; they do not need new global gate types.

Promotion evaluates structured statement family, scope, results/receipts,
capability permissions, dependencies, policy, validator status, staleness,
conflicts, and requested strength. It SHOULD NOT infer claim type by free-text
keyword matching.

Hard non-laundering rules:

The stable mechanism claim is: **Pertura makes scientific authority conditional
on resolved, provenance-backed design identity and verified, scope-compatible,
dependency-complete evidence. Unresolved design facts trigger a checkpointed
clarification or fail closed; user confirmation may resolve identity but cannot
create scientific evidence.**

- prediction plus prior cannot become measurement;
- hypothesis consensus cannot become measurement;
- user confirmation can resolve identity but cannot create effect;
- execution without a method validator is not trusted;
- unresolved scope cannot support an effect statement;
- guide agreement is not independent biological replication;
- prediction-measurement concordance does not validate mechanism;
- a label-trained module cannot independently confirm the same effect.

## 11. Runner and verifier protocol

Free-code path: Claude executes arbitrary analysis in its workspace. Files and
logs are captured as exploratory provenance but receive no signed receipt.

Trusted-capability path:

1. resolve capability and frozen contract;
2. mount/copy inputs read-only into isolation;
3. execute with pinned environment and resource limits;
4. stage outputs outside agent-owned trust storage;
5. run capability-specific validation;
6. hash inputs, parameters, code, environment, and outputs;
7. sign a `RunReceipt`;
8. commit envelope and dependency edges;
9. expose committed result to promotion.

Novel CodeAct analysis can later be packaged through review, a spec, pinned
environment, validator, and golden tests. The agent cannot self-certify arbitrary
code in the same run.

## 12. Current package structure

The monorepo and Claude Agent SDK remain in place. Scientific authority is split by responsibility, not duplicated across frameworks.

```text
src/
  pertura_core/
    contracts.py
    scope.py
    promotion.py
    compatibility/v0.2/

  pertura_workflow/
    capabilities/
      registry.py
      specs/
      runners/
    planner.py

  pertura_runtime/
    product.py
    product_tools/
    verifier/
    project/
    adapters/
    agent_bundle/

  pertura_bench/
    capability_models.py
    real_execution.py
    agent_models.py
    agent_server_execution.py
    server_plan.py
```

Required authority direction:

```text
capability spec + runtime-resolved dependencies
-> controlled executor + validator
-> ResultEnvelope
-> authority commit + optional receipt/session seal
-> pertura_core.promotion
-> TurnFinal / versioned report
```

`phase` is presentation metadata only. Scientific topology comes from `depends_on`, cycle validation, and explicit dependency policies.

## 13. Migration and legacy boundary

The retired evidence lattice, registrar, stage harness, classic recipe, evidence MCP, and old finalizer are physically isolated under `legacy/`. They are excluded from wheel/sdist and from the active runtime import graph.

Historical workspaces enter through a read-only importer and become `legacy_unverified`. They are never converted into current receipts or promotion decisions. New capabilities must use `ResultEnvelope` and must not extend or bridge the legacy taxonomy.
## 14. Implementation sequence

### P0. Integrity freeze

- freeze artifact kinds, registrars, predicates, and stage contracts;
- remove strict trust from simplified pseudobulk;
- implement agent-inaccessible receipt authority;
- require explicit result dependencies;
- make unresolved scope fail closed;
- pass one immutable policy through every finalizer path;
- add ledger-forgery and input-mutation adversarial tests.

Exit: agent code cannot mint trust; method names alone do not confer trust;
unknown scope cannot upgrade; all tests pass.

### P1. Capability kernel and tool consolidation

- implement the core contracts and capability registry;
- add progressive disclosure and five high-level tools;
- auto-commit verified output and add legacy adapters;
- reduce stages to product progress metadata.

Exit: one vertical slice needs no model-selected registrar; adding a diagnostic
needs no gate enum; free CodeAct remains available.

### P2. Intake, assignment, and state reference

- direct H5AD, MuData, and 10x CRISPR intake;
- barcode, guide-UMI, ambient, MOI, and guide-map diagnostics;
- control-derived reference and frozen mapping;
- curated module import plus one learned module method;
- leakage/stability validators.

Exit: public data produces reproducible contracts, assignment reports, state and
module references; planted failures are detected.

### P3. Reliability and effect estimation

- profile-backed target reliability v2;
- signature efficacy and escape/responder;
- validated edgeR or DESeq2 pseudobulk first;
- SCEPTRE/conditional association and one composition method;
- calibration and sensitivity capabilities.

Exit: expert reliability agreement; trusted wrappers reproduce reference output;
invalid designs fail or remain exploratory.

### P4. Interpretation

- response programs, clustering, and enrichment;
- source-backed literature and contradiction retrieval;
- explicit hypothesis synthesis;
- separate rendering of measured, derived, prior, and hypothesis content.

Exit: every sentence is source-class traceable; interpretation never increases
its input's measured ceiling.

### P5. Virtual evaluation and experimental design

- prediction scope/split contracts;
- mandatory baselines and anti-collapse checks;
- uncertainty/conformal adapters;
- cost/diversity-aware next-panel selection;
- leakage and fixed-panel benchmarks.

Exit: collapsed/leaking predictors fail; recommendations expose objective,
uncertainty, cost, and dependencies.

## 15. Verification strategy

Contract tests cover schemas, scope truth tables, stale propagation, compatibility,
receipt verification, and non-laundering.

Scientific golden tests compare trusted methods with authoritative package output,
including coefficients, direction, significance fields, sample unit, formula, and
diagnostics within declared tolerance.

Planted-failure tests cover guide/barcode mismatch, reverse complement, ambient
guides, high-MOI misclassification, detectability, guide disagreement, escape,
confounding, missing replicate overlap, state instability, module circularity,
prediction leakage, collapse, and forged trust records.

End-to-end benchmarks use real workspaces, full trajectories, deterministic
graders, and expert rubrics. Measure task completion, diagnostic sensitivity and
specificity, method-route accuracy, scientific agreement, unsupported claims,
time to defensible answer, unnecessary questions, reproducibility, and expert
agreement on verdict/next action. LLM judges may score communication but not
replace deterministic or expert scientific grading.

## 16. Product report contract

Default report sections:

1. Answer and current verdict.
2. Reasons, blockers, cautions, and conflicts.
3. Design, scope, replicate unit, and estimand.
4. Assignment QC and retained population.
5. State reference, modules, stability, and mapping.
6. Target efficacy, guide heterogeneity, escape, and dropout.
7. Effects, formula, uncertainty, calibration, and sensitivity.
8. Programs, priors, contradictions, and hypotheses.
9. Virtual scope, baselines, collapse, uncertainty, and concordance when relevant.
10. Smallest next analysis or experiment likely to change the verdict.
11. Capability versions, receipts, files, and hashes.
12. Optional clearly labeled exploratory appendix.

Internal terms such as registrar, evidence lattice, warrant branch, and MCP tool
SHOULD NOT appear in the normal user report.

## 17. Definition of done

The architecture is realized when:

- Claude can freely read, code, use Bash/Python/R, and edit notebooks;
- it normally sees five scientific tools plus generic CodeAct tools;
- analytical coverage grows by capabilities, not gate types;
- trust authority is inaccessible to agent-authored code;
- trusted methods have validators and golden tests;
- one contract and scope representation replace competing authority layers;
- state references/modules are versioned dependencies with leakage controls;
- every promoted statement has current dependencies and a verified receipt;
- prediction, prior, and hypothesis cannot become measured evidence;
- a run can cover intake, assignment, state reference, reliability, effects,
  interpretation, and optional virtual/next-design work;
- real-data benchmarks show faster defensible answers and fewer unsupported
  conclusions than a notebook-only baseline.

## 18. Immediate implementation rule

Until P1 is complete:

> Do not add a new `ArtifactKind`, evidence predicate, registrar, MCP registration
> tool, or stage contract for a new analysis. Define a provisional
> `CapabilitySpec`, `ResultEnvelope`, validator, and migration adapter instead.

This keeps the scientific boundary small while Perturb-seq coverage expands.
