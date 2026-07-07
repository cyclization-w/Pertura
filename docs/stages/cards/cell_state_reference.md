# Cell State Reference

Run the classic single-cell state-reference stage for Perturb-seq analysis. This stage defines transcriptomic state space and annotation context; it does not produce perturbation effect evidence.

## What To Do
- Summarize transcriptome preprocessing inputs and any already-applied normalization/log transform.
- Compute or summarize HVGs, PCA, neighbors, UMAP, clustering, and cell grouping when the data supports it.
- Produce cluster/state marker summaries and annotation metadata when annotation is attempted.
- Record the exact state/cluster assignment column name that downstream stages should use.
- Keep plots and large tables under `outputs/`.

## Required Handoff
Create `outputs/state_reference_summary.json` with structured fields for:
- source h5ad/table path and source hash when available
- assignment column name
- embedding and clustering methods
- annotation method and confidence summary when available
- marker summary path or compact inline marker summary

Then call `mcp__pertura_evidence__register_cell_state_reference_artifact` with the summary path, assignment column, methods, marker path, source path/hash, and structured scope.

## Boundary
This stage can support scope definition, state context, and downstream stratification. It must not support measured perturbation effects, validated mechanism, causal fate decisions, driver validation, or gene-specific perturbation claims by itself.
## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.
