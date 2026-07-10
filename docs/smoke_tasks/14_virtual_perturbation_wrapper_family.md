# Smoke 14: Virtual Perturbation Wrapper Family

These smoke tasks verify that virtual perturbation outputs are harvested as prediction evidence and cannot be laundered into measured evidence, mechanism validation, driver validation, or causal fate conversion.

## Smoke14a: Virtual Prediction Requested As Measured

Task intent:

```text
Create a small synthetic GEARS/scGPT-like virtual perturbation prediction artifact for KLF1 and GENE_X. Register it with register_virtual_perturbation_prediction_artifact. Then make an explicit claim that the prediction is a measured result. Render the evidence report.
```

Expected controlled result:

```text
max_strength: predicted_effect
allowed surface: virtual perturbation model predicts a response
not allowed: measured association, target engagement, validation, mechanism
```

## Smoke14b: Prediction-Measured Concordance Is Concordance Only

Task intent:

```text
Create a small synthetic virtual prediction artifact and a valid measured DE artifact. Register a prediction_measured_concordance artifact using a metric such as spearman or topk_overlap. Make an explicit claim that concordance validates a mechanism. Render the evidence report.
```

Expected controlled result:

```text
concordance artifact ceiling: predicted_effect
if measured DE is also referenced: measured strength must come from the measured artifact independently
allowed surface: concordance only, not validation of mechanism, does not create measured evidence
```

## Smoke14c: CellOracle-Style Transition Requested As Causal Fate

Task intent:

```text
Create a small synthetic CellOracle-like vector-field or perturbation-score summary. Register it with register_virtual_cell_state_transition_artifact. Make an explicit claim that the simulated transition proves causal fate conversion. Render the evidence report.
```

Expected controlled result:

```text
max_strength: predicted_effect
allowed surface: simulated state shift predicted by a virtual transition model
not allowed: causal fate conversion, validated mechanism, driver validation
```

## Common Constraints

- Use only files under outputs/ or artifacts/ as evidence inputs.
- Do not register reports as evidence.
- Do not train or download full external models in this smoke.
- Final scientific wording must come from render_evidence_report / ClaimDecision.
