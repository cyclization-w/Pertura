# 10. P2 Workflow Implementation Plan

## Purpose

P2 upgrades Pertura from a claim-conditioned evidence gate into an evidence-aware Perturb-seq workflow agent.

The core framing is:

```text
Pertura provides bounded evidence-acquisition workflows for Perturb-seq: it can run or harvest minimal analyses needed for claim calibration, while every user-visible scientific conclusion remains controlled by the evidence gate.
```

Pertura may run narrow helper analyses, but it must not become a full replacement for Scanpy, Seurat, Cell Ranger, SCEPTRE, Milo, CellOracle, scGPT, GEARS, CPA, or other domain tools.

## Dependency Boundaries

The repo is intentionally split into separate trust domains:

```text
src/pertura_gate/
  trusted identity, evidence, policy, resolver, and renderer core

src/pertura_runtime/
  Claude / CodeAct runtime and MCP-facing tool surface

src/pertura_bench/
  benchmark harnesses and surface evaluators

src/pertura_workflow/
  bounded evidence-acquisition workflow layer
```

Hard import invariants:

```text
pertura_gate must not import pertura_workflow.
pertura_gate must not import pertura_runtime.
pertura_gate must not import pertura_bench.

pertura_workflow may import pertura_gate.
pertura_runtime may import pertura_gate and pertura_workflow.
pertura_bench may import pertura_gate and pertura_workflow.
```

Benchmark evaluators and over-claim word lists stay outside `pertura_gate`.

## P2 Milestones

P2 is split into three implementation milestones:

```text
P2.0: workflow substrate
P2.1: classic guide-based Perturb-seq workflow
P2.2: modality expansion: genome-scale / chemical / virtual perturbation
```

P2.0 and P2.1 are implemented. P2.1 includes the strict classic recipe path, narrow basic DE / target QC runners, candidate claim UID linking, evidence-gap reporting, deterministic GateBench fixtures, and freeze docs. The next work is P2.2 modality expansion.

## P2.0: Workflow Substrate

Implemented scope:

- `src/pertura_workflow/`
- public `pertura` CLI
- workflow models:
  - `WorkflowStateManifest`
  - `WorkflowRunManifest`
  - `WorkflowRunStep`
  - `EvidenceCandidate`
  - `HarvestReport`
  - `EvidenceGoal`
  - `PreflightReport`
- CLI commands:
  - `pertura preflight <workspace>`
  - `pertura harvest <workspace>`
  - `pertura recommend-next <workspace>`
  - `pertura explain <decision_id>`
- candidate-only harvest path
- next-evidence recommendations
- workflow run manifest writing
- import-boundary tests

Harvest modes:

```text
candidate_only
  Only produces EvidenceCandidate records.
  Never writes the evidence registry.

auto_register_strict
  May register only fully validated, UID-linked, non-ambiguous candidates.

interactive_confirm
  May ask the user only for identity/design metadata.
  User answers can change provenance but cannot create measured effect evidence.
```

P2.0 readiness output must keep these concepts separate:

```text
detected_files
detected_metadata
candidate_artifacts
readiness_by_claim_type
```

Readiness examples:

```text
ready_for_observation: yes
ready_for_measured_de: maybe, missing target_qc
ready_for_target_engagement: no, modality unknown
ready_for_replication: no, replicate axis missing
ready_for_mechanism: no, policy disabled / validation missing
```

## P2.1: Classic Guide-Based Perturb-seq

P2.1 is the first user-facing workflow milestone. It targets Norman-style / guide-based Perturb-seq, not chemical perturbation or general bulk CRISPR screens.

Target CLI:

```text
pertura recipe classic <workspace>
```

Workflow shape:

1. Preflight workspace.
2. Harvest or build `PerturbationDesignManifest`.
3. Harvest guide metadata / guide-to-target map.
4. Harvest or run target QC / cell QC.
5. Harvest or run basic DE for a UID-linked contrast.
6. Optionally harvest enrichment / prediction / module / global outputs.
7. Generate candidate claims from the user question and registered artifacts.
8. Link candidate claims to DesignManifest UIDs.
9. Evaluate linked claims.
10. Render a `ClaimDecision` report.

Candidate-claim rule:

```text
Generated claims are candidate claims, not scientific surface.
Candidate claims must be linked to canonical UID scope before they can receive effect-level strength.
Unlinked or ambiguous claims are downgraded or blocked.
```

### Current P2.1 Implemented Scope

Implemented:

- internal family registrar API in `EvidenceRegistry`
- P1-compatible subtype delegation for family registrars
- partial-success `classic_perturbseq` recipe path when no structured config exists
- strict structured `classic_recipe_config.json` path that can:
  - register a `PerturbationDesignManifest`
  - register experiment design
  - register guide assignment
  - register target QC
  - optionally register cell QC
  - register measured DE
  - build an explicit candidate claim
  - call `resolve_claims`
  - write `artifacts/claim_decisions.json`
  - render `reports/evidence_report.md`
- `run_basic_de_for_registered_contrast` for explicit UID-linked expression/metadata CSV inputs
- `run_basic_target_qc` for explicit UID-linked metadata/guide-map CSV inputs
- candidate claim UID linking with gap reporting for unlinked claims

Not yet implemented:

- candidate claim generation from natural-language user questions
- UID linking for externally generated candidate claims
- strict auto-registration from harvested ambiguous candidates
- P2.2 modality expansion

### Family Registrar API
P2 introduces family registrars so future workflow code does not need dozens of MCP tools:

```text
register_scope_artifact
register_eligibility_artifact
register_measured_effect_artifact
register_prior_artifact
register_prediction_artifact
register_inferred_structure_artifact
register_ranking_artifact
register_dataset_metadata_artifact
```

Implementation order:

1. internal Python family registrars
2. P1 wrappers call or match family behavior
3. equivalence tests: old P1 path == family path
4. CLI uses family API
5. MCP exposure later, behind explicit design review

P1 registrars remain public compatibility paths.

### Thin Runners

Allowed narrow runners:

```text
run_basic_de_for_registered_contrast
run_basic_target_qc
run_basic_module_score
run_basic_global_distance
run_basic_enrichment
```

`run_basic_de_for_registered_contrast` constraints:

- input must be a `contrast_uid`
- counts or normalized layer must be explicitly declared
- cell type must not be auto-inferred
- confounders must not be auto-inferred
- biological interpretation must not be emitted
- output is only a DE table plus structured metadata

### Harvesters

P2.1 target harvesters:

```text
AnnData metadata harvester
generic DE table harvester
generic enrichment table harvester
generic prediction table harvester
Cell Ranger CRISPR / guide metadata harvester
guide-to-target map harvester
```

Harvester rule:

```text
Harvesters produce EvidenceCandidate objects.
They do not create evidence truth.
Only validator-passed, UID-linked, non-ambiguous candidates may be registered.
```

## P2.2: Modality Expansion

P2.2 is deferred until P2.1 is stable.

### Genome-Scale Guide-Based Perturb-seq

Use the name:

```text
genome_scale_guide_based_perturbseq
```

Do not call this a general `whole_genome_screen`; Pertura is not yet modeling bulk CRISPR-screen readouts.

Scope:

```text
requires single-cell readout or registered perturbation-level transcriptomic association
not a general bulk CRISPR screen workflow
```

Likely artifact subtypes:

```text
guide_level_effect
target_level_aggregate_effect
screen_hit_ranking
guide_consistency_summary
screen_qc_summary
```

Gate rules:

- ranking is candidate/ranking evidence, not driver validation
- guide consistency is a quality predicate, not replication by default
- hit ranking is not a validated driver claim
- screen-level assay validation requires positive and negative control behavior

### Chemical / Treatment Perturbation

Add:

```text
TreatmentManifest
treatment_condition_v1 adapter
```

Identity fields:

```text
treatment_uid
compound_uid
dose_uid
timepoint_uid
vehicle_uid
contrast_uid
```

Rules:

- missing vehicle/control means observation only
- dose/time mismatch blocks measured treatment association
- treatment response does not imply direct molecular target mechanism
- chemical workflow does not require guide assignment

### Virtual Perturbation

Add recipe:

```text
virtual_perturbation
```

Supported inputs:

```text
scGPT-style prediction table
GEARS-style prediction table
CPA-style prediction table
CellOracle-style prediction/network output
custom prediction CSV/JSON
```

Add artifact:

```text
prediction_measured_concordance
```

Minimum fields:

```text
prediction_artifact_id
measured_artifact_id
scope_match
metric
value
n_targets
policy_interpretation: concordance_only_not_validation
```

Gate rules:

- virtual KO supports predicted effect only
- prediction-measured concordance is concordance only
- concordance cannot validate a mechanism
- concordance cannot create a measured claim unless the measured artifact alone supports it

## Hard Resolver Invariants

These are not implementation preferences; they are design constraints:

```text
resolver scope upgrades require UID match or manifest-declared typed compatibility
raw-label overlap is diagnostic only
raw-label overlap cannot raise scope_fit to exact or compatible
raw-label overlap cannot increase max_strength
prediction, prior, measured, inferred, and ranking evidence cannot launder into each other
validated_mechanism remains disabled
```

Every scientific surface must pass through `ClaimDecision`.

Recipes, harvesters, runners, and Claude runtime outputs are not scientific surfaces.

## Partial-Success Acceptance

Pertura workflows must still produce useful output when a workspace is incomplete.

If required inputs are missing, Pertura should still produce:

- preflight report
- candidate artifacts where possible
- blocked or downgraded `ClaimDecision`
- `recommend_next_evidence` suggestions
- no unsupported scientific overclaim

This is required for real Perturb-seq workspaces, where guide maps, controls, dose/time metadata, or target QC are often incomplete.

## P2.1 Next Implementation Steps

Next steps after the current strict structured recipe path and basic DE runner:

1. Start P2.2 genome-scale guide-based Perturb-seq planning and fixtures.
2. Start P2.2 TreatmentManifest / chemical perturbation planning.
3. Start P2.2 virtual perturbation / prediction-measured concordance planning.
4. Keep natural-language claim generation deferred until deterministic workflow behavior remains stable across P2.2.

## Do / Don't

Do:

- keep `pertura_gate` pure and workflow-agnostic
- make every scientific surface pass through `ClaimDecision`
- treat harvesters as candidate producers, not evidence producers
- require validator pass before registry write
- use UID/manifest-based scope matching in resolver
- render missing evidence and repair suggestions even when a recipe is incomplete
- preserve all P1 registrar behavior through compatibility wrappers

Do not:

- let recipes write scientific prose directly
- let harvester confidence become evidence strength
- let raw string matching upgrade scope
- let user confirmation create measured effect
- let prediction-measured concordance become validation
- let ranking become driver validation
- let network edges become mechanism
- enable `validated_mechanism` in P2