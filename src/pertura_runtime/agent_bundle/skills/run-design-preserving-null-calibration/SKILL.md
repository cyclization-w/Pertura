---
name: run-design-preserving-null-calibration
description: Run deterministic null calibration for paired pseudobulk designs by swapping condition labels within independent units. Use when a task provides a calibration split and requires type-I error or null-effect checks without cell-label permutation.
---

# Run Design-Preserving Null Calibration

Use only the registered calibration split. Never derive calibration labels from evaluation outcomes.

## Validate

1. Confirm that calibration and evaluation selections are distinct registered inputs.
2. Require at least three paired independent units with one baseline and one target pseudobulk per unit.
3. Confirm the same count preprocessing, design, contrast, and filtering used by the primary fit.
4. Stop on missing pairs, rank deficiency, nonexchangeable labels, or ambiguous unit identity.

## Calibrate

1. Sort independent unit IDs deterministically.
2. Enumerate masks `1` through `2^n - 2`, excluding identity and complete inversion.
3. Swap baseline and target labels only within the units selected by each mask.
4. Refit the full edgeR quasi-likelihood pipeline for each permutation.
5. Write one row per permutation with `permutation_id`, `type1_rate`, `null_effect_bias`, and `exchangeability_violation_count`.
6. Keep optional swapped-unit details separate from the required table.

Never permute cells, copy reference values, or inspect evaluator files. Parameterize `scripts/run_paired_label_null.R` using [configuration](references/configuration.md).
