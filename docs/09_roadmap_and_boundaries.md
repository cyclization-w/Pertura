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

## Next Immediate Work

The next phase should not add another evidence kind. It should consolidate benchmark and paper-ready evidence:

1. Freeze a P1 smoke table covering Smoke06, Smoke07, Smoke08, Smoke08b, Smoke09, Smoke10.
2. Ensure A+C evidence workflow is available symmetrically to gated and baseline arms in P0.7.
3. Update the benchmark spec to include P1 evidence kinds and surface evaluator checks.
4. Produce a result table separating:
   - resolver unit tests;
   - MCP registrar tests;
   - Claude smoke tests;
   - P0.7 strong-baseline utility tests.
5. Rerun core P0.7 tasks under the finalized workflow contract.

## What Is Deferred

### Real External Runners

Pertura currently does not include runners for:

- Mixscape / Mixscale;
- g:Profiler / Enrichr / MSigDB;
- Milo / scCODA;
- trajectory analysis;
- CellOracle / scGPT / GEARS;
- Cell Ranger guide assignment.

These remain Claude CodeAct or future adapter responsibilities. Pertura registers their structured outputs.

### P2 Artifact-Family APIs

P2 should cover broader external capability families:

- scope artifacts: cell annotation, state reference, dataset metadata;
- measured effects: composition, trajectory, module, global effects;
- inferred structures: cofunctional targets, regulatory networks;
- prediction artifacts: predicted response, predicted network;
- ranking artifacts: driver or target prioritization.

The P2 design should avoid one MCP tool per algorithm.

### Validated Mechanism Positive Path

`validated_mechanism` remains disabled. Enabling it would require future artifacts such as rescue assays, orthogonal validation, time-course causality, epistasis, protein validation, reporter assays, or equivalent evidence.

## Paper Framing Boundary

The contribution should be framed as an execution-grounded evidence lattice and controlled scientific surface for CodeAct agents, not as a more complete Perturb-seq analysis platform.

Strong claim:

```text
Pertura prevents prompt pressure, self-tag laundering, predicted/prior evidence, string-scope ambiguity, and prose-only eligibility from increasing user-visible claim strength.
```

Avoid claiming:

```text
Pertura runs every Perturb-seq analysis method.
Pertura prevents users from reading local audit files.
Pertura validates mechanisms.
Pertura replaces biological review.
```