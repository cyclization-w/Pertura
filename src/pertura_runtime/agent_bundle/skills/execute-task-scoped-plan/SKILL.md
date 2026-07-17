---
name: execute-task-scoped-plan
description: Execute a frozen Pertura task plan without rediscovering contracts. Use when a run provides PERTURA_CAPABILITY_PLAN.json and the agent must validate its route, registered assets, blockers, skill phases, output contract, and minimal legal next action.
---

# Execute a Task-Scoped Plan

Read `task/PERTURA_CAPABILITY_PLAN.json` once. Treat it as the task-local execution contract, not as scientific evidence.

## Start

1. Confirm `task_id`, `plan_id`, `plan_hash`, DatasetContract identity, route, registered assets, and required output paths.
2. Confirm that every required input has an asset ID or a declared upstream artifact. Do not substitute a similarly named file.
3. Create a conservative `benchmark_result.json` checkpoint before the first high-cost call. Use `blocked` status and describe missing work; never list artifacts that do not exist.
4. Follow the bound skill phases. Do not select methods by keyword matching.

## Select One Route

- `capability_or_codeact`: invoke only a ready capability with its rendered minimal call. If all candidates are blocked and a ready handoff exists, use the handoff.
- `codeact`: use the declared environment, invocation, inputs, outputs, and bound method skill. Keep outputs exploratory.
- `evidence_interpretation`: read the registered evidence and protocol; do not recompute or refit them.
- `blocked`: record the exact blockers and stop scientific execution.

## Boundaries

- Do not call `inspect_dataset` when the brief already contains a DatasetContract.
- Do not inspect repository source, capability YAML, tests, or environment installation directories to discover contracts.
- Do not repeat a blocked capability without new dependency evidence.
- Do not convert CodeAct output into a capability result or verifier-signed receipt.
- Write only to the current task output directory.

Read [route semantics](references/route-semantics.md) only when a route is ambiguous.
