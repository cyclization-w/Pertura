# Composition Effect

Run exactly this Pertura stage in the current turn. Use normal CodeAct to inspect files and create compact structured outputs under outputs/.

## Stage Boundary
- This stage card guides exploration; it is not a scientific conclusion surface.
- Scratch outputs and candidate artifacts do not support claims.
- Register the stage artifact when the required structured output exists.
- Do not use free-text notes, filenames, or raw-label overlap to raise claim strength.
- Stop after the stage output and TurnFinal-ready summary are available.

## Task
Estimate whether a perturbation is associated with changes in cell-state, cluster, or annotated-group composition relative to a compatible control.

Use an existing `cell_state_reference` output or a clearly documented state/cluster assignment column. Produce `outputs/composition_effect_summary.json` with:
- state source or cell-state-reference artifact id when available
- state assignment column
- perturbation/control scope copied from a DesignManifest-derived scope when available. If you register a design manifest in this turn, use the returned `artifact_id` as `scope.design_manifest_id` and pass the target raw label as `scope.raw_label`; do not invent `target_uid`, `control_uid`, or `contrast_uid`.
- counts or proportions by state and condition
- comparison method and statistic metadata
- effect size, state-level deltas, and pvalue/padj when available
- n target cells and n control cells

Then call `mcp__pertura_evidence__register_composition_effect_artifact` with the summary path and structured metadata. Pass count tables explicitly as `state_counts_by_condition` or `counts_by_state` in the tool call, not only inside the JSON file. Pass state-level deltas as `state_level_deltas` when available.

## Scientific Boundary
- Composition effects can support measured cell-state composition association only after ClaimDecision evaluation.
- Do not present composition evidence as causal fate conversion, target engagement, gene-specific DE, mechanism validation, driver validation, or lineage commitment proof.

## Handoff
Use the stage contract to decide whether to register evidence. If a registrar returns no `next_claim_template`, do not use that artifact as direct effect evidence.

## Language and Encoding
- Write stage outputs, registered metadata, reports, and summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields.
- Avoid smart quotes, non-ASCII dashes, and decorative symbols.

