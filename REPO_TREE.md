# Repository Tree

This is the intended GitHub repository structure for Pertura after the repo-level cleanup.

```text
pertura/
  README.md
  REPO_MANIFEST.md
  REPO_TREE.md
  pyproject.toml
  .gitignore

  src/
    pertura_gate/                  # trusted gate core
      core/
        schema.py
        policy.py
      identity/
        canonical_scope.py
        design_manifest.py
        scope.py
      evidence/
        registry.py
      resolver/
        resolver.py
        warrant.py
      render/
        renderer.py

    pertura_runtime/               # untrusted agent/runtime adapter
      claude/
        cli.py
        agent.py
        prompt.py
        finalizer.py
        manifest.py
        options.py
        workspace.py
        mcp_server.py
        tools/
          evidence_tools.py

    pertura_bench/                 # benchmark-only utilities
      p07_harness.py
      surface_eval.py

  tests/
    gate/
      test_artifact_strength.py
      test_claim_resolver.py
      test_identity_manifest.py
      test_import_boundaries.py
    runtime/
      test_claude_evidence_tools.py
      test_final_surface.py
    bench/
      test_p07_gate_utility.py

  scripts/
    claude_pertura_harness.py
    p07_gate_utility.py
    policy_threshold_probe.py
    probe_claude_sdk.py
    run_smoke_suite.ps1

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
    smoke_tasks/
      01_measured_association_with_eligibility.md
      ...
      10_global_effect_not_gene_specific_or_fate.md
    p07_tasks/
      README.md
      04_artifact_self_tag_laundering.md
    skills/
      00_experiment_design.md
      03_guide_assignment.md
      05_target_qc_and_replication.md
      10_evidence_workflow.md
```

## Deliberately Excluded From Git

```text
.claude_runs/
.p07_runs/
.smoke_runs/
.pertura_workspace/
.tmp/
outputs/
reports/
artifacts/
.pytest_cache*/
__pycache__/
generated_by_gpt.txt
local datasets, h5ad files, API keys, and environment files
```

## Verification

The clean export was tested from its own root with:

```bash
python -m pytest -q
```

Result:

```text
110 passed
```