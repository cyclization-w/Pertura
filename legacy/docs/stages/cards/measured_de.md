# Measured DE

Run exactly this Pertura stage in the current turn. Use normal CodeAct to inspect files and create compact structured outputs under outputs/.

## What To Do
- Use an already resolved DesignManifest scope and contrast when available.
- Run or harvest a differential-expression table for the selected contrast.
- Write a compact measured DE summary to outputs/measured_de_summary.json.
- Register the DE table with `mcp__pertura_evidence__register_measured_de_artifact`.
- When the registrar returns `next_claim_template`, copy its `scope` and `evidence_refs` exactly into any candidate claim that uses this DE artifact. Do not reconstruct scope from filenames, manifest paths, raw labels, or prose.
- If useful, write candidate claims to outputs/candidate_claims.json for a later claim_report stage.

## Stage Boundary
- This stage card guides exploration; it is not a scientific conclusion surface.
- Scratch outputs and candidate artifacts do not support claims.
- A registered measured_de artifact can support measured_association at most, and only with manifest UID scope plus validated eligibility.
- Do not claim mechanism, driver validation, or causal regulation from DE.
- Do not use free-text notes, filenames, or raw-label overlap to raise claim strength.
- Stop after the stage output and TurnFinal-ready summary are available.

## Handoff
Use the stage contract to decide whether to register evidence. If a registrar returns `next_claim_template`, treat it as the only safe claim handoff for this artifact: copy `scope` and `evidence_refs`, then fill claim text and requested strength separately. If a registrar returns no `next_claim_template`, do not use that artifact as direct effect evidence.

Candidate claims are handoff material only. They become scientific surface only after claim_report calls evaluate_claims and render_evidence_report.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.
