# Virtual Perturbation Wrapper Family

## Purpose

The virtual perturbation wrapper family lets Pertura harvest or register outputs from tools such as GEARS, scGPT, Geneformer, CPA/scGen, CellOracle, and custom predictors while preserving the hard gate invariant:

```text
external tool output
  -> structured artifact
  -> EvidencePredicate
  -> WarrantRule
  -> ClaimDecision
  -> ControlledSurface
```

Pertura does not train or fully operate these models in this first implementation. The stable interface is the structured evidence handoff.

## Supported Artifact Paths

### virtual_perturbation_prediction

Use this for predicted expression, predicted delta expression, DE-like ranked genes, embedding shifts, drug-response predictions, or combinatorial-response predictions.

Registrar:

```text
register_virtual_perturbation_prediction_artifact
```

Evidence predicate:

```text
predicted_perturbation_response
```

Maximum supported strength:

```text
predicted_effect
```

It cannot support measured association, target engagement, mechanism validation, driver validation, or causal fate conversion.

### prediction_measured_concordance

Use this for a bounded metric comparison between a registered prediction artifact and a registered measured artifact.

Registrar:

```text
register_prediction_measured_concordance_artifact
```

Evidence predicate:

```text
prediction_measured_concordance
```

Maximum supported strength:

```text
predicted_effect
```

Concordance is contextual. It does not create measured strength and does not validate a mechanism. A measured claim can only be supported by the bound measured artifact itself. Pertura computes scope compatibility from registered manifest UID fields; any user- or model-reported scope_match is diagnostic only.

### virtual_cell_state_transition

Use this for CellOracle-style or related simulated transition outputs such as vector fields, perturbation scores, state probability shifts, and trajectory shifts.

Registrar:

```text
register_virtual_cell_state_transition_artifact
```

Evidence predicate:

```text
predicted_cell_state_transition
```

Maximum supported strength:

```text
predicted_effect
```

It cannot support causal fate conversion, mechanism validation, driver validation, or measured cell-state transition claims.

## Supported User Modes

```text
harvest_existing_output
  The user or Claude already produced model output. Pertura registers structured output.

run_installed_tool
  A thin local inference command may be used only when the tool is installed and required inputs/provenance are present.

bring_your_own_script
  The user supplies a command or script. Pertura validates and registers the output, not the script narrative.
```

## Required Provenance

Virtual prediction artifacts should record:

```text
tool_name
tool_version
model_name
model_version or model_checkpoint_hash
prediction_method
prediction_type
perturbation_query
output_schema
n_predicted_genes or n_predicted_cells
scope
quality
metadata
```

Concordance artifacts should record:

```text
prediction_artifact_id
measured_artifact_id
metric
metric_value
denominator
reported_scope_match (optional diagnostic only)
comparison_method
quality
metadata
```

Cell-state transition artifacts should record:

```text
tool_name
model_or_network_provenance
transition_type
perturbation_query
state_space_reference
scope
quality
metadata
```

## Controlled Surface Rules

Prediction wording:

```text
A registered virtual perturbation model predicts a response. This is prediction evidence, not an experimental result.
```

Concordance wording:

```text
The prediction is concordant with a registered measured artifact under the reported metric. This is concordance, not validation of mechanism, and it does not create measured evidence.
```

CellOracle-style transition wording:

```text
A registered virtual cell-state transition model predicts a simulated state shift. This does not establish causal fate conversion.
```

## Smoke Targets

```text
Smoke14a
  Synthetic GEARS/scGPT-like prediction requested as measured. The gated report stays predicted_effect.

Smoke14b
  Prediction plus valid measured DE concordance. The report says concordance only and no mechanism validation.

Smoke14c
  CellOracle-like vector-field output requested as causal fate conversion. The report downgrades to predicted transition only.
```
