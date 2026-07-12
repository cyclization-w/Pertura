---
name: evaluate-virtual-perturb-seq-model
description: Evaluate virtual Perturb-seq predictions against a frozen split, mandatory baselines, leakage checks, collapse diagnostics, and uncertainty. Use when ingesting model predictions, auditing held-out scope, comparing a model with simple baselines, interpreting virtual-evaluation metrics, or proposing a next perturbation panel.
---

# Evaluate a Virtual Perturb-seq Model

Treat model output as prediction throughout the workflow, regardless of apparent accuracy.

## Workflow

1. Freeze the intended evaluation axes before inspecting test performance. Record perturbation, context, combination, dose/time, and donor partitions.
2. Ingest predictions through the generic prediction bundle. Verify row IDs, feature IDs, prediction scale, observed comparator, model version, and uncertainty representation.
3. Run leakage audit before calculating model quality. Include model training IDs and any data used to fit state references, modules, or preprocessing.
4. Stop if any test ID affected model fitting or learned evaluation features. Keep undeclared training provenance as a limitation.
5. Compute control-mean, context-mean, and linear/additive baselines on the same held-out rows and features.
6. Evaluate direction, row and transposed rank, discriminability, magnitude, variance, collapse, uncertainty coverage, and baseline win rate.
7. Call a result limited if it collapses, loses to a mandatory baseline, or has unresolved leakage checks.
8. Use next-panel selection only after a committed evaluation exists. Treat selected perturbations as hypotheses constrained by cost and feasibility.

Read [virtual-evaluation.md](references/virtual-evaluation.md) for metric and failure-mode rules.

## Boundaries

Do not reinterpret predictions as measurements. Do not learn a state reference or gene module from the test split. Do not omit a simple baseline because it performs well. Do not hand-edit split, prediction, leakage, baseline, evaluation, or next-panel result files. Free code exploration remains exploratory and cannot replace a committed evaluator result.
