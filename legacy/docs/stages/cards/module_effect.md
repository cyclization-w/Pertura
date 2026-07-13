# Module Effect

Run exactly this Pertura stage in the current turn. Use normal CodeAct to inspect files and create compact structured outputs under outputs/.

## Stage Boundary
- This stage card guides exploration; it is not a scientific conclusion surface.
- Scratch outputs and candidate artifacts do not support claims.
- Register the stage artifact when the required structured output exists.
- Do not use free-text notes, filenames, or raw-label overlap to raise claim strength.
- Stop after the stage output and TurnFinal-ready summary are available.

## Handoff
Use the stage contract to decide whether to register evidence. If a registrar returns no 
ext_claim_template, do not use that artifact as direct effect evidence.
## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.
