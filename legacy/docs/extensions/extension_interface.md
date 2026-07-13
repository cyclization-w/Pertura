# Pertura Extension Interface

This document defines the minimum interface for extending Pertura without weakening the evidence gate. It is intentionally smaller than a plugin system. An extension is a coordinated addition across stage guidance, evidence registration, runtime handoff, and benchmark coverage.

## Core Boundary

Pertura extensions must preserve these dependencies:

```text
pertura_gate      <- trusted evidence, identity, resolver, renderer core
pertura_runtime   -> may call pertura_gate, never trusted as evidence by itself
pertura_workflow  -> may call pertura_gate, produces candidates or registered artifacts
pertura_bench     -> may call pertura_gate, evaluates surfaces after the gate
```

The gate must not import runtime, workflow, benchmark, stage cards, or benchmark word lists. Every user-visible scientific conclusion must pass through ClaimDecision and the controlled renderer.

## Extension Planes

### 1. Stage Extension

A stage extension adds one bounded analysis stage to the Evidence-Aware Stage Catalog.

Required files:

```text
docs/stages/contracts/<stage_id>.yaml
docs/stages/cards/<stage_id>.md
```

Required index entry:

```text
docs/stages/index.yaml
```

A stage contract is runtime and benchmark readable. It declares:

- `stage_id`
- `stage_role`
- `evidence_role`
- `evidence_producing`
- `turn_final_surface_type`
- `allowed_mcp_tools`
- `required_outputs`
- `optional_outputs`
- `can_support`
- `must_not_support`
- `failure_modes`
- `next_stage_recommendations`
- `benchmark_expectations`

A stage card is Claude-readable guidance. It tells Claude what to inspect, what to write under `outputs/`, what to register, and where to stop. It must not be the scientific conclusion surface.

### 2. Evidence Extension

An evidence extension adds a structured artifact path and its predicate-specific warrant behavior.

Required design decisions:

- artifact kind
- `EvidencePredicate`
- evidence class
- artifact role
- intrinsic strength ceiling
- required structured metadata for strength
- scope requirements
- quality predicates
- warrant behavior
- controlled surface wording
- forbidden wording
- claim types the artifact can support
- claim types the artifact must never support
- whether the artifact can be referenced by claims
- whether a Claude-facing MCP registrar is needed

Rules:

- `StrengthCeiling.measured_association` is not an evidence predicate.
- Artifact self-tags cannot define evidence class, predicate, or strength.
- Raw labels and filenames cannot upgrade scope.
- Scope upgrades require manifest UID match or manifest-declared typed compatibility.
- Renderer wording must be predicate-specific, not strength-only.
- Eligibility requirements must be predicate-specific, not inherited from DE.
- A new evidence kind must have at least one negative overclaim test.

### 3. Runtime Extension

A runtime extension changes how Claude runs a bounded stage, not what the gate believes.

Allowed runtime changes:

- add one stage card to the selected-stage prompt
- add a specific MCP registrar for a concrete artifact kind
- add stage-aware Python preflight package requirements
- add TurnFinal fields or formatting derived from runtime-owned artifacts
- add safe handoff files such as candidate claims or registration handoffs

Disallowed runtime changes:

- recipes writing scientific prose directly
- Claude free text raising claim strength
- user answers creating measured effect evidence
- stage cards bypassing ClaimDecision

### 4. Benchmark Extension

A benchmark extension freezes the new boundary.

Each new stage or evidence kind should add tests for:

- stage completion
- artifact registration correctness
- metadata completeness
- stage boundary respected
- exploration not surfaced as conclusion
- next-stage recommendation correctness
- at least one overclaim or laundering trap when applicable

Benchmarks may use word lists to evaluate surfaces, but word lists stay in `pertura_bench`. They must not enter `pertura_gate`.

## Predicate / Warrant Gate

No extension is accepted until its predicate and warrant have an explicit testable contract. The minimum gate for a new predicate is:

```text
EvidenceArtifact(kind, evidence_predicate, structured metadata)
  -> intrinsic_warrant
  -> claim-conditioned warrant
  -> predicate-specific controlled surface
```

The extension must prove two paths:

```text
positive path:
  complete structured metadata + UID scope + passing quality predicates
  -> intended max strength

negative path:
  overclaim / wrong claim type / missing scope / missing quality metadata
  -> downgrade or unsupported with safe wording
```

Do not add a new stage or MCP tool if the predicate can already be represented by an existing artifact kind. Prefer mapping external software outputs to existing evidence predicates.

## Family Registrars

Family registrars are internal Python APIs for workflow code:

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

They are not Claude-facing MCP tools in P2.1. Claude-facing tools remain specific registrars such as `register_measured_de_artifact` or `register_cell_state_reference_artifact`.

## Extension Checklist

Before adding a new extension, answer these questions:

1. What stage does it belong to?
2. What files must Claude write under `outputs/`?
3. What structured artifact, if any, gets registered?
4. What `EvidencePredicate` does that artifact represent?
5. What structured fields are required for intrinsic strength?
6. What scope evidence is required?
7. What quality predicates can downgrade it?
8. What is the maximum strength ceiling?
9. What claim types can it support?
10. What claim types must it never support?
11. What exact controlled surface wording should be used?
12. Does it need a specific MCP registrar, or can workflow code use an internal family registrar?
13. What TurnFinal surface type should it use?
14. What next stages should it recommend?
15. What deterministic benchmark proves the boundary?
16. What smoke test proves the real runtime handoff?

## Minimal Implementation Order

1. Add the stage contract and card.
2. Add or reuse the evidence registrar.
3. Add resolver and renderer behavior only if a new artifact kind can support claims.
4. Add runtime prompt/tool exposure only if Claude needs a specific handoff.
5. Add deterministic tests.
6. Add a real Claude smoke only after deterministic tests pass.

## Do Not

- Do not expose abstract family registrars as MCP tools.
- Do not let stage completion imply scientific support.
- Do not let harvester confidence become evidence strength.
- Do not let prediction-measured concordance become mechanism validation.
- Do not let ranking become driver validation.
- Do not enable validated mechanism without a future validation policy.