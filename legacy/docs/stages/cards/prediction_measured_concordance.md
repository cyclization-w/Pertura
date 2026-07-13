# Prediction Measured Concordance

Run exactly this Pertura stage in the current turn. Compare an already-registered virtual prediction artifact with an already-registered measured artifact using a bounded metric.

## Stage Boundary
- This stage card guides exploration; it is not a scientific conclusion surface.
- Concordance is contextual prediction evidence only.
- Concordance cannot create measured strength and cannot validate a mechanism.
- Register with `mcp__pertura_evidence__register_prediction_measured_concordance_artifact`.

## Required Handoff
Create `outputs/prediction_measured_concordance_summary.json` with prediction artifact id, measured artifact id, metric, metric value, denominator, optional reported_scope_match, and comparison method. Do not use reported_scope_match as evidence truth; Pertura computes scope compatibility from registered manifest UID fields.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
