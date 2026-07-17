---
name: run-replicate-aware-pseudobulk-de
description: Run replicate-aware pseudobulk differential expression with a paired edgeR quasi-likelihood design. Use when single-cell counts must be aggregated by an independent biological unit, or when registered pseudobulk inputs require a target-versus-control model without treating cells or guides as replicates.
---

# Run Replicate-Aware Pseudobulk DE

Use only the inputs, environment, analysis unit, baseline, design, and outputs declared by the task handoff.

## Validate

1. Confirm nonnegative integer raw counts and stable gene and sample identities.
2. Join cells to metadata and the declared selection by explicit cell ID.
3. Confirm the independent biological unit. Never use cells or guides as biological replicates.
4. Require paired target/control overlap and at least two independent units unless the task declares a stricter rule.
5. Build the declared design and stop if it is rank deficient.

## Execute

1. For cell-level input, parameterize and run `scripts/materialize_pseudobulk.py` to aggregate counts by independent unit and condition.
2. Parameterize and run `scripts/run_edger_ql.R` in the declared locked environment.
3. Use `filterByExpr`, TMM normalization, dispersion estimation, quasi-likelihood fitting, and the declared condition contrast.
4. Write the design and result tables before summaries.
5. For per-target mode, preserve the full registered gene universe and mark untested genes explicitly.
6. Record package versions, the actual analysis unit, pairing, design, and cautions.

Do not install packages, probe unrelated environments, read reference results, or reuse evaluator code. Read [configuration](references/configuration.md) before creating the template configuration.
