# Virtual Perturbation Prediction

Run exactly this Pertura stage in the current turn. Use normal CodeAct to inspect or run lightweight local scripts, but register only structured virtual perturbation output.

## Stage Boundary
- This stage card guides exploration; it is not a scientific conclusion surface.
- Harvest existing GEARS, scGPT, Geneformer, CPA/scGen, or custom prediction output when available.
- You may run an installed thin inference command only if inputs and model provenance are already available.
- Register with `mcp__pertura_evidence__register_virtual_perturbation_prediction_artifact`.
- Virtual perturbation predictions are prediction evidence only, not measured evidence or mechanism validation.

## Required Handoff
Create `outputs/virtual_perturbation_prediction_summary.json` with tool/model provenance, prediction type, perturbation query, output schema, and predicted gene/cell counts.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
