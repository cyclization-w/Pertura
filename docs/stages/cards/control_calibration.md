# Control Calibration

Run exactly this Pertura stage in the current turn. Use normal CodeAct or the narrow Pertura runners to create compact structured outputs under outputs/.

## Stage Boundary
- This stage records negative-control calibration evidence only.
- It does not produce perturbation effect evidence, mechanism validation, driver validation, or biological conclusions.
- Use explicit UID-linked inputs. Do not infer controls, contrast identity, cell type, normalization, or confounders from filenames.
- Scratch outputs and candidates do not support claims until registered.
- Stop after the structured output and TurnFinal-ready summary are available.

## Expected Checks
- NTC-vs-NTC calibration: compare two explicit negative-control splits or explicit control UID groups.
- Label-permutation null: permute labels for an explicit registered contrast.
- Record alpha, tested feature count, significant feature count, pass/fail status, method, execution_hash, scope, and quality metadata.

## Handoff
Call `mcp__pertura_evidence__register_control_calibration_artifact` with the JSON output path and structured check dictionaries. This artifact is eligibility evidence only; do not use it as direct evidence for an effect claim.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.
