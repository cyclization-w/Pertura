# 11. Evidence-Aware Stage Catalog

## Purpose

This document fixes the design position for Pertura's soft execution layer.

Pertura is:

```text
Perturb-seq analysis agent with gated scientific conclusions.
```

The system has two deliberately different layers:

```text
soft stage skill layer
  Helps Claude perform native Perturb-seq analysis stages.
  Preserves CodeAct freedom inside each stage.

hard evidence gate layer
  Controls what the stage outputs can support as scientific claims.
  Produces the user-visible scientific surface from ClaimDecision.
```

The Evidence-Aware Stage Catalog is not a replacement for the evidence gate. It is also not a full pipeline runner or a heavy workflow planner. It is a stage-native contract system that gives Claude domain structure without taking away exploratory freedom.

## Relationship To SP-Mind

SP-Mind uses a soft orchestration pattern:

```text
task text
-> keyword-selected skill markdown
-> tool documentation in the system prompt
-> Claude CodeAct
-> Claude final prose
```

Pertura adopts the useful part of this pattern:

```text
task or selected stage
-> stage skill card
-> tool documentation
-> Claude CodeAct
```

But Pertura adds a hard boundary that SP-Mind does not have:

```text
Claude stage output
-> structured artifact registration
-> validator-owned evidence class
-> UID / scope checking
-> claim-conditioned resolver
-> ClaimDecision
-> controlled scientific report
```

Therefore:

```text
Pertura stage skill layer ~= SP-Mind skill/tool layer
Pertura evidence gate layer != SP-Mind
```

The novelty is not that Pertura can inject domain skill cards into a Claude session. The novelty is that free CodeAct analysis is followed by an execution-grounded evidence gate that controls scientific claim strength.

## Core Design Decision

Pertura should not implement an ever-growing set of user-scenario workflows such as:

```text
quick KLF1 workflow
QC-only workflow
harvest-only workflow
prediction workflow
ranking workflow
...
```

It should instead implement a finite catalog of native Perturb-seq stages. The implemented catalog lives at `legacy/docs/stages/index.yaml`, with Claude-readable cards under `legacy/docs/stages/cards/` and runtime/benchmark contracts under `legacy/docs/stages/contracts/`:

```text
preflight
experiment_design
perturbation_design_manifest
guide_assignment
cell_qc
target_qc
cell_state_reference
measured_de
target_engagement
curated_enrichment
module_effect
global_effect
prediction_artifact
virtual_perturbation_prediction
prediction_measured_concordance
virtual_cell_state_transition
claim_report
```

Future P2 stages may include:

```text
composition_effect
trajectory_effect
cofunctional_targets
driver_ranking
regulatory_network
genome_scale_screening
chemical_treatment_effect
replication_or_robustness
```

A full workflow is just an optional sequence of stages. The system core is the stage contract, not a global workflow planner.

## Stage Contract

Each stage skill card must define a contract. It should constrain the stage boundary, not the internal analysis path.

Minimal stage card fields:

```yaml
stage_id: measured_de
purpose: Estimate expression association for a registered perturbation contrast.

when_to_use:
  - The user needs measured perturbation-response evidence for a contrast.
  - A downstream claim requires measured association support.

required_inputs:
  - expression matrix or AnnData-like object
  - perturbation identity or DesignManifest mapping
  - perturbation group
  - control group

optional_inputs:
  - donor
  - batch
  - cell type
  - replicate axis
  - covariates

recommended_checks:
  - verify control definition
  - verify perturbation-cell mapping
  - verify n_target_cells and n_control_cells
  - verify multiple-testing metadata

allowed_methods:
  - scanpy rank_genes_groups
  - pseudobulk DE
  - custom regression
  - existing external DE output

required_outputs:
  - source file path
  - source hash
  - contrast_uid or structured scope
  - method
  - effect/statistical fields
  - n_target_cells
  - n_control_cells
  - multiple-testing metadata

registrar_handoff:
  - register_measured_de_artifact

max_supported_strength:
  - measured_association

cannot_support:
  - validated_mechanism
  - driver validation
  - causal regulatory mechanism
```

The stage card may recommend tools and checks, but it must not force one analysis command or one statistical method unless the stage is explicitly a narrow runner.

## Freedom Inside A Stage

Claude keeps freedom inside the stage:

```text
Claude may inspect data.
Claude may choose method variants.
Claude may write and debug code.
Claude may create plots and intermediate files.
Claude may generate exploratory notes.
Claude may decide that the stage is blocked.
Claude may recommend missing evidence.
```

The hard boundary is at the stage exit:

```text
Exploration notes are not evidence.
Plots are not evidence unless registered as an artifact.
Temporary tables are not evidence unless registered as an artifact.
Claude prose is not the scientific final surface.
```

Only registered artifacts enter the evidence gate.

## Stage Outputs

A stage may end in one of three valid states:

```text
completed
  The stage registered one or more evidence artifacts.

partial
  The stage produced candidate artifacts, diagnostics, or notes, but could not register effect-level evidence.

blocked
  The stage could not proceed because required identity, design, control, QC, or source metadata is missing.
```

Blocked or partial stages are valid outcomes. The system should render a useful TurnFinal rather than forcing Claude to invent a result.

## Claim Report As A Stage

`claim_report` is a first-class stage.

Evidence-producing stages should not directly write the scientific conclusion. They should produce artifacts and optional candidate claims. The `claim_report` stage performs:

```text
candidate claim collection
claim UID / scope linking
evaluate_claims
render_evidence_report
runtime finalization
```

This keeps scientific conclusion rendering centralized in the hard gate.

## Stage Selection

Stage selection should not be fully delegated to the user. Users often do not know which Perturb-seq stages are required for a valid claim.

Stage selection should also not be unconstrained Claude free choice. Claude may skip inconvenient prerequisites or move too quickly to narrative conclusions.

The preferred rule is:

```text
runtime selects or validates the stage
Claude may propose a stage
user may provide constraints
gate evaluates the resulting artifacts and claims
```

In practice:

```text
preflight
  discovers files, metadata, candidates, and missing fields

stage proposal
  may come from Claude or runtime defaults

stage validation
  checks whether prerequisites and execution bounds are satisfied

stage execution
  Claude runs CodeAct inside the selected stage contract

stage handoff
  artifacts are registered and decisions rendered by the gate
```

The user should not need to manually choose stages, but the system should expose the selected stage and the reason for selection.

## User Constraints

User input should be interpreted as constraints, not as a manual pipeline script.

Examples:

```text
only look at KLF1
do not run trajectory analysis
use existing DE table only
do not ask me questions
stop after QC
generate a calibrated report
```

These constraints bound execution. They do not change evidence strength.

## Ask-User Policy

`ask_user` is useful for interactive use but must not become a way for Claude to bypass evidence rules.

Rules:

```text
benchmark mode
  ask_user disabled
  unresolved metadata leads to downgrade/block

interactive mode
  ask_user may be used for design/intake ambiguity
  user answers are recorded as user_confirmed metadata
  user answers cannot create measured effect evidence

runtime ownership
  Claude may propose a question
  runtime decides whether to ask
  Claude cannot fill in a fake user answer
```

The best default trigger point is early design/intake, not claim-time. If control, modality, guide label parsing, or perturbation identity is unresolved, asking early prevents Claude from running an analysis on an unconfirmed assumption.

If no answer is available, the gate downgrades or blocks the affected claim.

## Turn Finalization

Every agent turn should produce a clear runtime-owned summary. This is separate from the scientific report.

The TurnFinal should include:

```text
status
selected stage(s)
what was done
generated files
registered evidence artifacts
claim decisions, if any
blocked or downgraded reasons
recommended next evidence or next stage
report path, if any
```

Scientific claims inside this summary must still come from ClaimDecision or clearly be labeled as operational status. Claude free prose may describe what happened procedurally, but it must not become the final scientific surface.

## Existing Hard Layers

The stage system is compatible with the implemented hard layers.

### Identity / Scope Layer

Objects:

```text
PerturbationDesignManifest
perturbation_uid
control_uid
contrast_uid
estimand
```

Function:

```text
Verify that the claim and artifact refer to the same structured perturbation identity.
Prevent raw label, basename, or token overlap from upgrading scope.
Prevent combinatorial perturbations from satisfying single-target claims.
```

Tests focus on UID match, mismatch, combinatorial mismatch, and raw-string fallback not increasing scope or strength.

### Evidence Registry / Artifact Validators

Objects:

```text
EvidenceArtifact
EvidenceRegistry
register_*_artifact
source_hash
artifact_intrinsic_ceiling
evidence_class
artifact_role
```

Function:

```text
Only runtime validators assign evidence class and intrinsic ceiling.
Input files cannot self-tag themselves into higher evidence.
Source hashes and provenance are recorded.
Reports cannot be registered as evidence sources.
```

Tests focus on prediction/prior/measured laundering, source path restrictions, self-tag rejection, and registrar-owned evidence class.

### EligibilityProfile

Objects:

```text
experiment_design
guide_assignment
target_qc
cell_qc
control_calibration
MOI
estimand
```

Function:

```text
Measured artifact existence is not enough.
Measured claim strength requires runtime-validated eligibility.
Structured inline eligibility can be accepted.
Prose or boolean-only eligibility cannot raise strength.
```

Tests focus on structured eligibility passing, prose-only downgrading, negative control missing, failed cell QC, and policy-configurable thresholds.

### Claim / ClaimDecision

Objects:

```text
Claim
ClaimDecision
requested_strength
max_strength
scope_fit
supporting_artifacts
blocked_requested_strength
allowed_surface
policy_hash
```

Function:

```text
Gate the claim, not the artifact.
The same artifact can support one claim while failing another.
The final decision is claim-conditioned.
```

Tests focus on mechanism downgrade, prediction/prior laundering, missing evidence, wrong scope, stable decisions, and controlled allowed surfaces.

### Resolver / Strength Lattice

Objects:

```text
EvidenceClass
ArtifactRole
StrengthCeiling
ScopeFit
Policy
```

Function:

```text
Combine evidence type, scope fit, eligibility, and policy into max_strength.
Prevent predicted, curated prior, measured, inferred, and ranking evidence from laundering into each other.
Keep validated_mechanism disabled unless a future policy explicitly enables it.
```

Tests focus on non-upgrade invariants: prediction plus prior does not become measured, measured plus enrichment does not become mechanism, target engagement does not become downstream mechanism, module/global effects do not become driver or causal fate decisions.

### Policy / Hash

Objects:

```text
policy_version
policy_hash
resolver_version
min cell thresholds
cell_qc policy
validated_mechanism disabled
```

Function:

```text
Make decisions reproducible.
Changing thresholds or policy changes the policy hash.
Same registry, claims, and policy produce identical decisions.
```

Tests focus on stable canonical policy hashes, threshold changes, and decision reproducibility.

### Controlled Renderer / Finalizer

Objects:

```text
render_evidence_report
allowed_surface
decision table
analysis_state_manifest
runtime final
```

Function:

```text
User-visible scientific surface comes from ClaimDecision, not Claude prose.
Reports show policy hash, decision table, supporting artifacts, and downgrade reasons.
Runtime finalizer prefers claim decisions over artifact-only fallback.
```

Tests focus on prediction wording, target engagement wording, enrichment/module/global caveats, decision tables, and hiding Claude draft prose from the scientific final.

### Benchmark / Surface Evaluator

Objects:

```text
P0.7 strong baseline
gated surface
baseline surface
surface_eval
```

Function:

```text
Evaluate whether the gate improves real agent surfaces.
This is benchmark-only and must not enter pertura_gate.
```

Tests focus on same registry snapshots, baseline overclaim true, gated overclaim false, and evaluator import boundaries.

## Testing Philosophy

The hard-layer tests do not primarily ask:

```text
Can Claude write the best Scanpy code?
Is the DE method statistically optimal for every dataset?
Is the biological conclusion true in the world?
```

They ask:

```text
Can Claude raise evidence strength through prose?
Can a file self-tag into a higher class?
Can raw labels or filenames bind evidence?
Can prediction, prior, enrichment, module, global, or ranking evidence become mechanism?
Can missing eligibility still support measured claims?
Can policy changes be reproduced and audited?
Can the final scientific surface bypass ClaimDecision?
```

The stage skill tests should therefore focus on:

```text
stage card loaded for the selected stage
Claude receives the correct contract and tool guidance
stage outputs are registered or blocked with reasons
exploration notes do not become evidence
TurnFinal reports registered artifacts and next evidence
scientific report still comes from ClaimDecision
```

## Implementation Implications

The next implementation step should be additive:

```text
docs/skills or docs/stage_cards
  stage cards for core P0/P1 paths

runtime stage option
  --stage measured_de
  --stage target_engagement
  --stage claim_report

StageRunManifest or WorkflowRunManifest reuse
  record selected stage, inputs, outputs, artifact IDs, decisions

TurnFinal renderer
  operational summary from stage/run manifests and decisions
```

Do not replace the current hard gate. Do not move benchmark word lists into the gate. Do not let stage cards define scientific strength independently of the resolver.

## Fixed Invariants

These design invariants are now fixed:

```text
1. Pertura is a Perturb-seq analysis agent with gated scientific conclusions.
2. Stage skills are soft contracts, not hard pipelines.
3. Claude remains free inside a stage.
4. Stage outputs become scientific evidence only through registration.
5. Evidence class is owned by the registrar/validator, not by files or Claude prose.
6. Scope upgrades require UID match or manifest-declared typed compatibility.
7. Claim strength is claim-conditioned.
8. Final scientific surface is rendered from ClaimDecision.
9. ask_user is runtime-owned and disabled in benchmark mode.
10. Workflow substrate records stage/run state but does not replace the evidence gate.
```
## Language And Encoding

Runtime artifacts, registered metadata, reports, stage summaries, and benchmark fixtures should be written in English. JSON and Markdown fields should prefer ASCII punctuation. Avoid smart quotes, non-ASCII dashes, and decorative symbols in registered metadata because those strings may pass through SDK logs, JSONL registries, Markdown reports, and shell output on different platforms.

This is a portability rule, not a scientific constraint. Claude remains free to analyze local data and write code, but the artifacts handed to Pertura should be English and ASCII-safe.
