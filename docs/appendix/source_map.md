# Appendix: source map

## Active product authority

- `src/pertura_core/contracts.py`: frozen core result, receipt, scope, and statement objects.
- `src/pertura_core/promotion.py`: the only active promotion policy and decision engine.
- `src/pertura_workflow/capabilities/specs/`: capability specifications and explicit dependency policies.
- `src/pertura_workflow/capabilities/registry.py`: spec, dependency-policy, and cycle validation.
- `src/pertura_workflow/planner.py`: routing from confirmed design facts and committed diagnostics.
- `src/pertura_runtime/verifier/`: controlled execution, receipts, committed results, and authority-session seals.
- `src/pertura_runtime/product.py`: five-tool product runtime and report projection.
- `src/pertura_runtime/project/`: projects, assets, runs, conversations, turns, and report revisions.

## Active benchmarks

- `src/pertura_bench/capability_models.py`: scientific capability case and verdict schemas.
- `src/pertura_bench/real_execution.py`: locked real-data execution and scientific metrics.
- `src/pertura_bench/server_plan.py`: checkpoint-bound server plan.
- `src/pertura_bench/agent_models.py`: provider-neutral agent workflow cases and verdicts.
- `src/pertura_bench/agent_server_execution.py`: three-condition server agent runs.
- `src/pertura_bench/release_gate.py`: repository/runtime/local/real readiness audit.
- `src/pertura_bench/cases/`: versioned cases and external catalog templates.

## Active tests

- `tests/workflow/test_a11_dependency_and_sparse.py`: phase neutrality, dependency policies, design routing, and sparse guide behavior.
- `tests/workflow/test_scientific_dependency_grounding.py`: runtime-owned dependency hashes and actual consumption.
- `tests/bench/test_a11_scientific_verdicts.py`: v3 hard-gate/reference/metrics behavior and three-condition parity.
- `tests/runtime/test_product_import_fence.py`: active import isolation.
- `tests/core/test_version_and_packaging.py`: distribution contents and legacy exclusion.

## Legacy archive

The retired evidence lattice, registrar, stage harness, evidence MCP, old finalizer, classic recipes, prompts, and tests are under `legacy/`. They run only through `legacy/pytest.ini` with `legacy/src` explicitly added to `PYTHONPATH`. They are excluded from product distributions and cannot create v0.2 results, receipts, or promotion decisions.