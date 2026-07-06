# Appendix: Source Map

This clean documentation set was extracted from current repository docs, code, tests, and smoke results.

## Main Source Documents

- `docs/P0_6_P0_7_TECHNICAL_DESIGN.md`: latest full design and P1.3 closure record.
- `docs/pertura_gate_v1.md`: concise evidence lattice spec.
- `docs/mcp_tools_v1.md`: early MCP boundary design, retained as historical context.
- `docs/smoke_tasks/`: executable Claude smoke task prompts.
- `docs/p07_tasks/`: P0.7 gate utility task prompt material.
- `docs/skills/10_evidence_workflow.md`: A+C evidence workflow SOP.

## Key Code Locations

Trusted gate core:

- `src/pertura_gate/core/schema.py`: `EvidenceClass`, `ArtifactRole`, `StrengthCeiling`, `ScopeFit`, `EligibilityProfile`, `EvidenceArtifact`, `Claim`, `ClaimDecision`.
- `src/pertura_gate/core/policy.py`: policy version and hash.
- `src/pertura_gate/identity/design_manifest.py`: perturbation design manifest support.
- `src/pertura_gate/identity/canonical_scope.py`: canonical perturbation identity comparison.
- `src/pertura_gate/identity/scope.py`: claim/artifact scope helpers.
- `src/pertura_gate/evidence/registry.py`: artifact registration and registry persistence.
- `src/pertura_gate/resolver/resolver.py`: claim-conditioned resolver and P1 evidence branches.
- `src/pertura_gate/resolver/warrant.py`: shared strength/warrant helpers.
- `src/pertura_gate/render/renderer.py`: controlled final surface templates.

Runtime adapter:

- `src/pertura_runtime/claude/tools/evidence_tools.py`: Claude-visible evidence MCP tools.
- `src/pertura_runtime/claude/finalizer.py`: decision-aware finalization.
- `src/pertura_runtime/claude/prompt.py`: runtime prompt and workflow instruction injection.
- `src/pertura_runtime/claude/manifest.py`: run manifest and analysis state manifest.

Benchmark utilities:

- `src/pertura_bench/p07_harness.py`: P0.7 strong-baseline harness.
- `src/pertura_bench/surface_eval.py`: benchmark-only surface evaluator.

## Key Tests

- `tests/gate/test_claim_resolver.py`: resolver and lattice tests, including P1.1/P1.2/P1.3.
- `tests/gate/test_artifact_strength.py`: artifact strength and report-rendering tests.
- `tests/gate/test_identity_manifest.py`: canonical UID and manifest tests.
- `tests/gate/test_import_boundaries.py`: gate import-boundary invariant.
- `tests/runtime/test_claude_evidence_tools.py`: MCP registration, source hash, next claim template behavior.
- `tests/runtime/test_final_surface.py`: runtime final and evidence report behavior.
- `tests/bench/test_p07_gate_utility.py`: P0.7 strong-baseline harness and evaluator.

## Smoke Task Prompts

- `docs/smoke_tasks/01_measured_association_with_eligibility.md`
- `docs/smoke_tasks/02_prediction_prior_laundering.md`
- `docs/smoke_tasks/03_missing_eligibility_trap.md`
- `docs/smoke_tasks/04_dual_guide_attribution_trap.md`
- `docs/smoke_tasks/05_policy_threshold_probe.md`
- `docs/smoke_tasks/06_target_engagement_mechanism_trap.md`
- `docs/smoke_tasks/07_cell_qc_blocks_measured_claim.md`
- `docs/smoke_tasks/08_curated_enrichment_context.md`
- `docs/smoke_tasks/08b_curated_enrichment_valid_measured_scope.md`
- `docs/smoke_tasks/09_module_effect_not_mechanism.md`
- `docs/smoke_tasks/10_global_effect_not_gene_specific_or_fate.md`

## Latest Recorded Test Result

```text
110 passed
```