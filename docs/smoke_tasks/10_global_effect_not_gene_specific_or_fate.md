# Smoke 10: Global Effect Is Not Gene-Specific Or Causal Fate Evidence

Goal: verify that a global perturbation response or distribution-shift artifact supports only a measured global response association and cannot become gene-specific DE, downstream mechanism, or causal fate evidence.

Use synthetic global-effect output; do not add Milo, scCODA, trajectory, or embedding-distance runners. Keep this smoke minimal and stop once the runtime report is rendered.

Required steps:

1. Create `outputs/smoke10_global_effect.json` with a compact distance/distribution-shift result.
2. Register a perturbation design manifest for the claim scope.
3. Copy the manifest-derived UID scope returned by the registrar. The global-effect artifact and claims must all use the same structured scope fields: `design_manifest_id`, `perturbation_uid`, `control_uid`, `contrast_uid`, and `estimand` when available.
4. Register the global effect with `mcp__pertura_evidence__register_global_effect_artifact` using that manifest-derived scope.
5. Include required global-effect metadata: metric, feature space or embedding, comparison method, effect size or distance, null model/permutation/test, pvalue/padj, n target cells, and n control cells.
6. Include structured eligibility inline in `quality.eligibility` on the global-effect artifact. At minimum include perturbation-cell mapping, negative control definition, target/control cell counts, assay modality, MOI, and estimand. Do not rely on prose such as "QC passed".
7. If you also register separate experiment design / guide assignment / target QC / cell QC artifacts, they must use the same manifest-derived UID scope. However, this smoke should prefer inline `quality.eligibility` on the global-effect artifact to avoid scope mismatch.
8. Create one claim that asks for a measured global response and one claim that tries to turn the global effect into gene-specific DE or causal fate evidence.
9. After registering artifacts, copy the exact returned `artifact_id` values into the claims JSON. These claims should reference the `global_effect_...` artifact id only. Do not include `design_manifest_...`, `experiment_design_...`, `guide_assignment_...`, `target_qc_...`, or `cell_qc_...` in `evidence_refs` unless the task explicitly asks to evaluate metadata artifacts.
10. Evaluate claims and render `reports/evidence_report.md`.

Expected runtime surface:

- global response claim reaches `measured_association` when metadata and inline eligibility pass.
- gene-specific DE claim is downgraded to observation or unsupported.
- report states global response measured association only.
- report does not present global shift as gene-specific DE, causal fate decision, or downstream mechanism.

Failure modes that are still useful:

- If the global response claim stays at `observation` because EligibilityProfile is missing, the global artifact did not include structured inline `quality.eligibility` or the separate eligibility artifacts had mismatched UID scope.
- If metadata artifacts appear as mismatched in decision reasons, remove them from claim `evidence_refs`; they are eligibility inputs, not effect evidence for this claim.
- If the report says `unsupported` because `evidence_refs` is missing, the claim did not copy the exact returned `global_effect_...` artifact id.
