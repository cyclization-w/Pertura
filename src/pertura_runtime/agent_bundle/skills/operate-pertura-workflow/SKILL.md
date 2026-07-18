---
name: operate-pertura-workflow
description: Operate Pertura's five-tool capability workflow while preserving free code exploration. Use when analyzing Perturb-seq data with Pertura, choosing the next diagnostic or analysis, resolving blockers, inspecting outputs, or finalizing a report.
---

# Operate the Pertura Workflow

Use Pertura as the scientific commit path while keeping CodeAct available for exploration.

## Workflow

1. If the run provides a registered DatasetContract and `task/capability_contracts/<task_id>.json`, consume them directly. The registered contract is the current design identity even when some named facts remain unresolved. Do not call `inspect_dataset` again and do not inspect source code or capability YAML to rediscover the contract. In ordinary interactive use without a registered contract, call `inspect_dataset` before scientific analysis and review unresolved design fields.
2. In registered-contract mode, use the task's bound capability or diagnostic first when it is applicable. Treat `blocked` and `unresolved` as information about missing design or data, not as permission to guess or to rerun broad discovery.
3. Use file inspection, shell commands, Python, R, or notebooks only for a specific unresolved fact or an explicitly CodeAct scientific method. Keep the read scope to the registered task assets and write exploratory outputs only under the run output directory. Do not rescan the complete primary matrix merely to restate facts already present in the contract.
4. Call `run_analysis` with the scientific objective and the registered asset IDs required by the static contract. Do not silently replace a blocked method.
5. Inspect returned JSON, Parquet, tables, and figures at their output paths. Keep large data out of chat.
6. Call `finalize_report` only when the user explicitly asks for a durable report revision. Ordinary turns are checkpointed automatically.

Use `run_analysis` to freeze a virtual split and ingest predictions, then use `evaluate_virtual_model` for leakage audit, mandatory baselines, and comprehensive evaluation. An out-of-scope response is not model support.

## Decision Rules

- Confirm identity fields only from observed metadata or explicit user confirmation.
- Keep exploratory calculations clearly labeled. They do not become committed results because their filenames resemble capability outputs.
- Follow result status, blockers, cautions, scope, and dependencies from the runtime.
- Ask for a design confirmation in interactive work when it would resolve a material ambiguity. In benchmark work, preserve the ambiguity and downgrade or block.
- Use runtime-rendered TurnFinal output for ordinary conversation and the versioned final report for an explicit reporting request.

## Boundaries

Never create or edit contracts, receipts, promotion decisions, authority records, or final reports. Never describe a candidate result as externally validated or scientifically certified. Skills guide behavior; registered capabilities and the runtime determine scientific authority.
