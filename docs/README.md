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
  appendix/
    source_map.md
```

## Reading Order

1. Start with `01_system_overview.md` for the one-page claim.
2. Read `02_architecture.md` and `03_evidence_lattice.md` for the system model.
3. Read `04_scope_and_eligibility.md` before reviewing measured evidence decisions.
4. Read `05_p1_evidence_paths.md` for the completed P1 capability set.
5. Read `08_smoke_and_benchmark_results.md` for what has actually been validated.`r`n6. Read `results/p0_p1_experiment_summary.md` for the compact saved result table.

## Current Status

P0.6, P0.7, P1.1, P1.2, and P1.3 are implemented for the current submission-oriented evidence lattice.

Latest full test result recorded after the repo cleanup:

```text
110 passed
```

P1 should be treated as implementation-complete for the current lattice. The next phase is benchmark/evaluation consolidation and paper-ready result tables, not adding another evidence kind.