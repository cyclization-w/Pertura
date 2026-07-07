# Claim Report

Render the controlled scientific surface for registered evidence and explicit candidate claims.

## What To Do
- Collect explicit claims from the task or candidate claim file.
- Ensure claims reference registered evidence artifact IDs and manifest-derived UID scope when effect strength is requested.
- Call `mcp__pertura_evidence__evaluate_claims`.
- Call `mcp__pertura_evidence__render_evidence_report`.
- Treat blocked and downgraded claims as first-class results, not as failures.

## Boundary
The user-visible scientific conclusion must come from ClaimDecision rendering. Do not use Claude free prose, scratch notes, stage progress summaries, or candidate claims as the scientific surface.

## Handoff
The report should include decision strength, policy hash, supporting artifact IDs, and downgrade or block reasons. If claims cannot be linked to registered evidence, render the evidence gap instead of inventing a conclusion.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.
