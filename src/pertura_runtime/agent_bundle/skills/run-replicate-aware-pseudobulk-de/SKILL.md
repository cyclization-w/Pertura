---
name: run-replicate-aware-pseudobulk-de
description: Run replicate-aware pseudobulk differential expression with a paired edgeR quasi-likelihood design. Use when single-cell counts must be aggregated by an independent biological unit, or when registered pseudobulk inputs require a target-versus-control model without treating cells or guides as replicates.
---

# Run Replicate-Aware Pseudobulk DE

Use only the inputs, environment, analysis unit, baseline, design, column bindings, and outputs declared by the task's frozen `codeact_protocol` and output contract. The protocol is a precommitted curator/user-confirmed design contract, not an evaluator answer.

## Validate

1. Confirm nonnegative integer raw counts and stable gene and sample identities.
2. Join cells to metadata and the declared selection by explicit cell ID.
3. Confirm the independent biological unit. Never use cells or guides as biological replicates.
4. Require paired target/control overlap and at least two independent units unless the task declares a stricter rule.
5. Build the declared design and stop if it is rank deficient.

## Execute

1. Set `SKILL_DIR` to the base directory reported by the Skill tool for this skill.
2. Write only task-local JSON configuration files described in [configuration](references/configuration.md). Do not write a replacement Python or R analysis script.
3. For cell-level input, aggregate counts with:

   ```bash
   bash "$SKILL_DIR/scripts/run_locked.sh" materialize CONFIG.json
   ```

4. Run the registered pseudobulk input with:

   ```bash
   bash "$SKILL_DIR/scripts/run_locked.sh" edger CONFIG.json
   ```

5. Use `filterByExpr`, TMM normalization, dispersion estimation, quasi-likelihood fitting, and the declared condition contrast.
6. Write the design and result tables before summaries.
7. For per-target mode, preserve the full registered gene universe and mark untested genes explicitly.
8. Record package versions, the actual analysis unit, pairing, design, and cautions.

Use only the two commands above. Do not use `module load`, a PATH-resolved `python` or `Rscript`, `pip`, `conda`, `install.packages`, or `BiocManager::install`. Do not rename, suffix, or deduplicate gene identities. If the frozen gene identity is not unique, stop rather than inventing identifiers. Do not require an execution brief or CodeAct handoff. Do not probe unrelated environments, read reference results, or reuse evaluator code.
