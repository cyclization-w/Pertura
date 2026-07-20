---
name: operate-pertura-workflow
description: Operate Pertura's five-tool capability workflow while preserving free code exploration. Use when analyzing Perturb-seq data with Pertura, choosing the next diagnostic or analysis, resolving blockers, inspecting outputs, or finalizing a report.
---

# Operate the Pertura Workflow

Use Pertura as the scientific commit path while keeping CodeAct available for exploration.

## Workflow

1. If the run provides a registered DatasetContract and `task/capability_contracts/<task_id>.json`, consume them directly. The registered contract is the current design identity even when some named facts remain unresolved. Do not call `inspect_dataset` again and do not inspect source code or capability YAML to rediscover the contract. In ordinary interactive use without a registered contract, call `inspect_dataset` before scientific analysis and review unresolved design fields.
2. In registered-contract mode, use only the capability or diagnostic advertised by the task's static contract. Attempt an advertised capability at most once. Treat a genuine scientific `blocked` or `unresolved` response about missing observed data, unresolved design identity, incompatible scope, or missing independent replicates as information to preserve, not as permission to guess.
3. When the static contract advertises no executable capability, proceed directly with the task's audited CodeAct fallback. When an advertised capability fails only at an integration or access boundary, including an unavailable ancestor capability receipt, stop retrying and use that same fallback. A fallback may produce independently scored files, but it does not create a capability receipt or measured authority and must never be described as capability execution.
4. Use file inspection, shell commands, Python, R, or notebooks only for the frozen CodeAct method or a specific unresolved fact. Keep the read scope to the registered task assets and write outputs only under the canonical task output directory. Do not rescan the complete primary matrix merely to restate facts already present in the contract, and do not use CodeAct to bypass a genuine scientific applicability block.
5. Call `run_analysis` with the scientific objective and the registered asset IDs required by the static contract. Do not call an unadvertised capability or inspect source code or capability YAML to discover one.
6. Inspect returned JSON, Parquet, tables, and figures at their output paths. Keep large data out of chat.
7. Call `finalize_report` only when the user explicitly asks for a durable report revision. Ordinary turns are checkpointed automatically.

Use `run_analysis` to freeze a virtual split and ingest predictions, then use `evaluate_virtual_model` for leakage audit, mandatory baselines, and comprehensive evaluation. An out-of-scope response is not model support.

## Decision Rules

- Confirm identity fields only from observed metadata or explicit user confirmation.
- Keep exploratory calculations clearly labeled. They do not become committed results because their filenames resemble capability outputs.
- Follow result status, blockers, cautions, scope, and dependencies from the runtime.
- Ask for a design confirmation in interactive work when it would resolve a material ambiguity. In benchmark work, preserve the ambiguity and downgrade or block.
- Use runtime-rendered TurnFinal output for ordinary conversation and the versioned final report for an explicit reporting request.

## Boundaries

Never create or edit contracts, receipts, promotion decisions, authority records, or final reports. Never describe a candidate result as externally validated or scientifically certified. Skills guide behavior; registered capabilities and the runtime determine scientific authority.
