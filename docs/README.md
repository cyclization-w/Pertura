# Pertura Clean Documentation

This directory is the clean documentation set for the current Pertura implementation. It is intentionally separated from historical planning notes, local run outputs, and iterative design logs.

Pertura is a runtime evidence and claim-strength layer for scientific CodeAct agents. Claude remains free to explore data and write code, but user-visible scientific conclusions are rendered from runtime-registered evidence artifacts, explicit claims, canonical scope, eligibility profiles, and versioned policy decisions.

## File Tree

```text
docs/
  README.md
  01_system_overview.md
  02_architecture.md
  03_evidence_lattice.md
  04_scope_and_eligibility.md
  05_p1_evidence_paths.md
  06_mcp_tool_surface.md
  07_runtime_surfaces.md
  08_smoke_and_benchmark_results.md
  09_roadmap_and_boundaries.md
  10_p2_workflow_implementation_plan.md
  11_stage_skill_system.md
  12_predicate_warrant_closure.md
  13_product_pivot.md
  14_capability_first_product_architecture.md
  15_v020_implementation_status.md
  appendix/
    source_map.md
```

## Reading Order

1. Start with `01_system_overview.md` for the one-page claim.
2. Read `02_architecture.md` and `03_evidence_lattice.md` for the system model.
3. Read `04_scope_and_eligibility.md` before reviewing measured evidence decisions.
4. Read `05_p1_evidence_paths.md` for the completed P1 capability set.
5. Read `08_smoke_and_benchmark_results.md` for what has actually been validated.
6. Read `09_roadmap_and_boundaries.md` for the current stage boundaries.
7. Read `10_p2_workflow_implementation_plan.md` before implementing P2 workflow changes.
8. Read `11_stage_skill_system.md` for the fixed soft-stage / hard-gate design.
9. Read `12_predicate_warrant_closure.md` for the completed Smoke 13 predicate/warrant closure.
10. Read `results/p2_core_freeze_summary.md` for the current frozen architecture before adding external wrappers.
11. Read `results/p0_p1_experiment_summary.md` for the compact saved result table.
12. Read `results/p1_freeze_summary.md` for the frozen paper-facing P1 table.
13. Read `14_capability_first_product_architecture.md` for the capability-first hard invariants.
14. Read `15_v020_implementation_status.md` for the implemented alpha surface and non-bypassable release blockers.

## Current Status

P0.6, P0.7, P1.1, P1.2, and P1.3 are implemented for the current submission-oriented evidence lattice.

Latest full test result for the capability-first alpha implementation:

```text
316 passed at the 0.2.0a3 capability-completion checkpoint; run pytest for the current total
```

P1 should be treated as implementation-complete for the current lattice. P2.0 workflow substrate and P2.1 classic guide-based workflow are implemented and frozen with deterministic GateBench fixtures. The Smoke 13 predicate/warrant closure is implemented, and the current pre-wrapper baseline is frozen in `results/p2_core_freeze_summary.md`. Future P2 extensions and external wrappers should follow `docs/extensions/extension_interface.md`.
