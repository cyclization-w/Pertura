---
name: operate-pertura-workflow
description: Operate Pertura's five-tool capability workflow while preserving free code exploration. Use when analyzing Perturb-seq data with Pertura, choosing the next diagnostic or analysis, resolving blockers, inspecting outputs, or finalizing a report.
---

# Operate the Pertura Workflow

Use Pertura as the scientific commit path while keeping CodeAct available for exploration.

## Workflow

1. Call `inspect_dataset` before scientific analysis. Review unresolved design fields and the recommended next capabilities.
2. Use file inspection, shell commands, Python, R, or notebooks to understand the data. Write exploratory outputs only under the run output directory.
3. Run the relevant diagnostics with `run_diagnostic`. Treat `blocked` and `unresolved` as information about missing design or data, not as permission to guess.
4. Call `run_analysis` with the scientific objective. Let the runtime select or validate the capability; do not silently replace a blocked method.
5. Inspect returned JSON, Parquet, tables, and figures at their output paths. Keep large data out of chat.
6. Call `finalize_report` after the needed committed results exist.

Use `evaluate_virtual_model` only when a supported evaluator exists. An out-of-scope response is not a model result.

## Decision Rules

- Confirm identity fields only from observed metadata or explicit user confirmation.
- Keep exploratory calculations clearly labeled. They do not become committed results because their filenames resemble capability outputs.
- Follow result status, blockers, cautions, scope, and dependencies from the runtime.
- Ask for a design confirmation in interactive work when it would resolve a material ambiguity. In benchmark work, preserve the ambiguity and downgrade or block.
- Use the final report as the user-visible claim surface.

## Boundaries

Never create or edit contracts, receipts, promotion decisions, authority records, or final reports. Never describe a candidate result as externally validated or scientifically certified. Skills guide behavior; registered capabilities and the runtime determine scientific authority.
