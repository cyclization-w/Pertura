# Virtual Cell State Transition

Run exactly this Pertura stage in the current turn. Harvest CellOracle-style or related virtual transition output such as vector fields, perturbation scores, state probability shifts, or trajectory shifts.

## Stage Boundary
- This stage card guides exploration; it is not a scientific conclusion surface.
- Register simulated transition output with `mcp__pertura_evidence__register_virtual_cell_state_transition_artifact`.
- The artifact supports predicted state transition only. It does not establish causal fate conversion, mechanism validation, or driver validation.

## Required Handoff
Create `outputs/virtual_cell_state_transition_summary.json` with tool provenance, model or network provenance, transition type, perturbation query, and state-space reference.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
