# 10 Evidence Workflow Skill

Purpose: keep Pertura artifact/claim bookkeeping correct without changing analysis freedom or claim strength.

This skill is a bookkeeping SOP. It must be available to both gated and baseline benchmark arms. It does not decide evidence strength, does not suggest `requested_strength`, and does not replace the runtime claim gate.

Core workflow:

1. Register a perturbation design manifest before registering measured effect evidence.
2. Copy the registrar-returned manifest-derived scope into downstream effect artifacts and claims. Use structured fields such as `design_manifest_id`, `perturbation_uid`, `control_uid`, `contrast_uid`, and `estimand` when present.
3. Register effect evidence artifacts with the matching Pertura MCP registrar.
4. When a registrar response includes `next_claim_template`, copy only:
   - `next_claim_template.scope`
   - `next_claim_template.evidence_refs`
5. Fill `claim_id`, `text`, `subject`, `object`, and `requested_strength` yourself based on the scientific statement being tested. The template does not suggest strength.
6. Evaluate explicit claims with `mcp__pertura_evidence__evaluate_claims`.
7. Render the user-visible report with `mcp__pertura_evidence__render_evidence_report`.

Direct evidence refs:

- Use effect evidence artifact ids in claim `evidence_refs`: `measured_de`, `perturbation_efficiency`, `predicted_effect`, `curated_prior_lookup`, `curated_enrichment_result`, `module_effect`, `global_effect`, and replication summaries.
- Do not put scope or eligibility-only artifacts in effect claim `evidence_refs`: `perturbation_design_manifest`, `experiment_design`, `guide_assignment`, `target_qc`, or `cell_qc` unless the claim explicitly asks about metadata itself.
- Eligibility artifacts support measured claims through compatible scope and structured fields; they are not biological effect evidence.

Required structured eligibility for measured effect artifacts:

- perturbation-cell mapping or treatment assignment method
- negative control or vehicle/control definition
- target/control cell counts
- assay modality and perturbation modality when known
- MOI and estimand when relevant
- control calibration or QC fields when available

P1 evidence-specific notes:

- Curated enrichment must bind `input_measured_artifact_id` to a measured artifact. It provides curated context only, not mechanism validation.
- Module effects must include module source, gene-set hash, scoring method, effect/statistics, cell counts, and compatible eligibility. All-cell-derived modules need a perturbation-contamination caveat.
- Global effects must include metric, feature space/embedding, comparison method, distance/effect, null model/test, pvalue/padj, cell counts, and compatible eligibility. They do not support gene-specific DE or causal fate claims.

Safety constraints:

- Never infer scope from filenames, basenames, raw labels, or prose if a manifest UID is required.
- Never copy `evidence_class`, `strength`, or `validated_mechanism` fields from artifact files into the claim decision. Registrar identity and resolver policy decide ceilings.
- Never use a claim template as proof of evidence strength. It only prevents copying the wrong artifact id or scope.
