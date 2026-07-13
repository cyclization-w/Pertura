# 09. Roadmap and Boundaries

## Current Completion State

P1 is implementation-complete for the current submission-oriented evidence lattice.

Completed:

- P0.6 canonical scope / manifest UID resolver discipline.
- P0.7 strong-baseline gate utility harness.
- P1.1 target engagement / perturbation efficiency.
- P1.2 cell QC eligibility tightening.
- P1.3 curated enrichment, module effect, and global effect.
- A+C evidence workflow closure.
- P2.0 workflow substrate: preflight, candidate harvest, next-evidence recommendation, and workflow run manifests.
- P2.1 classic workflow: internal family registrar API, strict structured recipe path, narrow basic DE / target QC runners, candidate-claim UID linking, evidence-gap reporting, and deterministic workflow freeze.

## P2 Direction

P2 upgrades Pertura into an evidence-aware Perturb-seq workflow agent without turning it into a full replacement for Scanpy, Seurat, Cell Ranger, SCEPTRE, Milo, CellOracle, scGPT, GEARS, or CPA.

Core framing:

```text
Pertura provides bounded evidence-acquisition workflows for Perturb-seq: it can run or harvest minimal analyses needed for claim calibration, while every user-visible scientific conclusion remains controlled by the evidence gate.
```

P2 is split into staged milestones:

```text
P2.0: workflow substrate
P2.1: classic guide-based Perturb-seq workflow
P2.2: modality expansion: genome-scale / chemical / virtual KO
```

The detailed implementation plan is maintained in `10_p2_workflow_implementation_plan.md`.

## P2.0 Workflow Substrate

P2.0 adds `src/pertura_workflow/` and a public `pertura` CLI.

Initial commands:

```text
pertura preflight <workspace>
pertura harvest <workspace>
pertura recommend-next <workspace>
pertura explain <decision_id>
```

P2.0 objects:

- `WorkflowStateManifest`
- `WorkflowRunManifest`
- `EvidenceCandidate`
- `HarvestReport`
- `EvidenceGoal`
- `PreflightReport`

Harvest modes:

```text
candidate_only
  never writes evidence registry, only candidates

auto_register_strict
  only fully validated, UID-linked, non-ambiguous candidates can register

interactive_confirm
  can ask users only for identity/design metadata; user answers cannot create measured effect
```

## P2.1 Classic Guide-Based Perturb-seq

P2.1 should implement a usable `classic_perturbseq` workflow:

1. Preflight workspace.
2. Harvest or build DesignManifest.
3. Harvest guide metadata / guide-to-target map.
4. Harvest or run target QC / cell QC.
5. Harvest or run basic DE for a UID-linked contrast.
6. Optionally harvest enrichment / prediction / module / global outputs.
7. Generate candidate claims from user question and registered artifacts.
8. Link candidate claims to DesignManifest UIDs.
9. Evaluate linked claims.
10. Render ClaimDecision report.

Generated claims are candidate claims, not scientific surface. Unlinked or ambiguous claims cannot receive effect-level strength.

P2.1 currently implemented scope: internal family registrar API, P1-compatible family subtype delegation, partial-success classic recipe behavior, a strict structured classic recipe path that can register DesignManifest, eligibility artifacts, measured DE, evaluate candidate claims, and render ClaimDecision reports from `classic_recipe_config.json`, plus `run_basic_de_for_registered_contrast` and `run_basic_target_qc` for explicit UID-linked CSV inputs. It still does not infer confounders/cell types or register ambiguous candidates.

## P2 Core Refactor Completed: Predicate / Warrant Layer

Smoke 13 showed that later measured evidence types can inherit DE-shaped assumptions if the gate only branches on `StrengthCeiling.measured_association`. That issue is now closed.

The gate core now uses:

```text
EvidenceArtifact -> EvidencePredicate -> WarrantRule -> ClaimDecision -> ControlledSurface
```

`measured_association` is a strength ceiling, not a synonym for differential expression. New extension stages must define predicate-specific warrant and surface rules before they can support claims. See `12_predicate_warrant_closure.md`.


## Next External Wrapper Direction

Before adding CellOracle, scGPT, GEARS, CPA, or similar wrappers, use the frozen P2 core baseline in `results/p2_core_freeze_summary.md`.

External wrappers should follow this path:

```text
external output
  -> structured artifact
  -> EvidencePredicate
  -> WarrantRule
  -> ClaimDecision
  -> ControlledSurface
```

The first wrapper family is now prediction / virtual perturbation plus prediction-measured concordance. It extends the P0.7 laundering boundary without weakening measured-vs-predicted separation; the implementation remains output-harvesting first, not full model training or GPU-heavy inference.

## P2.2 Modality Expansion

P2.2 should add:

- `genome_scale_guide_based_perturbseq`, not a general bulk CRISPR screen workflow.
- `TreatmentManifest` and chemical/treatment workflows.
- `virtual_perturbation` with prediction provenance, prediction-measured concordance, and CellOracle-style predicted state transition artifacts.

`prediction_measured_concordance` is concordance only. It cannot validate a mechanism and cannot create measured strength unless the measured artifact alone supports it.

## Hard Invariants

```text
pertura_gate must not import pertura_workflow, pertura_runtime, or pertura_bench.
Every scientific surface must pass through ClaimDecision.
Harvesters produce EvidenceCandidate objects, not evidence truth.
Validator pass is required before registry writes.
Raw-label overlap is diagnostic only.
Raw-label overlap cannot raise scope_fit to exact or compatible.
Raw-label overlap cannot increase max_strength.
User confirmation can resolve identity/design metadata but cannot create measured effect.
validated_mechanism remains disabled.
```

## What Remains Deferred

Deferred beyond P2.0/P2.1 substrate work:

- full Cell Ranger execution;
- full SCEPTRE pipeline;
- full Milo/scCODA modeling pipeline;
- full CellOracle / scGPT / GEARS / CPA execution or training;
- full trajectory inference;
- full GRN inference;
- PubMed RAG;
- dashboard UI.

## Paper Framing Boundary

Strong claim:

```text
Pertura prevents prompt pressure, self-tag laundering, predicted/prior evidence, string-scope ambiguity, and prose-only eligibility from increasing user-visible claim strength.
```

P2 product claim:

```text
Pertura can run or harvest bounded Perturb-seq evidence-acquisition workflows and then calibrate scientific claims through a deterministic evidence gate.
```

Avoid claiming:

```text
Pertura runs every Perturb-seq analysis method.
Pertura replaces Scanpy, Seurat, Cell Ranger, SCEPTRE, Milo, CellOracle, scGPT, GEARS, or CPA.
Pertura prevents users from reading local audit files.
Pertura validates mechanisms.
Pertura replaces biological review.
```
