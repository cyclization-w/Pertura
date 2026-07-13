# <Stage Title>

Run exactly this Pertura stage in the current turn. Use normal CodeAct to inspect files and create compact structured outputs under outputs/.

## What To Do

- Inspect only the data and metadata needed for this stage.
- Write the required stage summary to `outputs/<stage_summary>.json`.
- Register the structured artifact only if the stage contract allows a specific MCP registrar.
- If the registrar returns a `next_claim_template`, preserve its `scope` and `evidence_refs` exactly for a later claim_report stage.
- Stop after the stage output and TurnFinal-ready summary are available.

## Stage Boundary

- This stage card guides exploration; it is not a scientific conclusion surface.
- Scratch outputs and EvidenceCandidate-like files do not support claims.
- Registered artifacts support only the ceiling declared by the evidence gate.
- Do not use free-text notes, filenames, raw labels, or string overlap to raise claim strength.
- Do not claim validated mechanism, driver validation, or causal regulation unless a future policy explicitly enables those strengths.

## Handoff

Use the stage contract to decide whether to register evidence. Candidate claims are handoff material only. They become scientific surface only after claim_report calls evaluate_claims and render_evidence_report.

## Language and Encoding

- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.