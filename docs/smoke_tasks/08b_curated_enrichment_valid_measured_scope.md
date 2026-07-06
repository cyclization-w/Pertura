# Smoke 08b: Curated Enrichment With Valid Measured Scope

Goal: verify the positive P1.3 path: a curated enrichment result bound to a runtime-validated measured DE artifact can provide measured-association context when both the claim and artifacts use the same PerturbationDesignManifest UID scope. It still cannot validate a mechanism.

Use synthetic files if needed; do not call external enrichment services.

Required steps:

1. Create `outputs/smoke08b_manifest.json` or another compact manifest source file.
2. Register a perturbation design manifest with a raw label such as `KLF1_NegCtrl0__KLF1_NegCtrl0`.
3. Copy the manifest-derived UID scope returned by the registrar. The measured DE artifact, enrichment artifact, and claims must all use the same structured scope fields: `design_manifest_id`, `perturbation_uid`, `control_uid`, `contrast_uid`, and `estimand` when available.
4. Create `outputs/smoke08b_measured_de.csv` with compact DE-like rows and adjusted p-values.
5. Register the measured DE artifact with:
   - manifest-derived UID scope from step 3.
   - structured eligibility sufficient for measured association: perturbation-cell mapping, negative control definition, target/control cell counts, assay modality, MOI, and estimand.
6. Create `outputs/smoke08b_enrichment.json` containing a curated pathway/gene-set enrichment result.
7. Register it with `mcp__pertura_evidence__register_curated_enrichment_artifact`, binding `input_measured_artifact_id` to the measured DE artifact and providing `input_gene_set_hash`, background universe, database/version, term id, method, and pvalue/padj. Use the same manifest-derived UID scope.
8. Create at least one claim that asks for a validated mechanism from the enrichment. Its `scope` must be the same manifest-derived UID scope and its `evidence_refs` must include the returned curated enrichment artifact id.
9. Optionally create a second claim that asks for a measured association context and references both the measured DE artifact id and the curated enrichment artifact id.
10. Evaluate claims and render `reports/evidence_report.md`.

Expected runtime surface:

- the enrichment claim reaches `measured_association` with curated context when bound measured evidence and scope are valid.
- requested mechanism strength is downgraded.
- report states enrichment provides curated context for a measured association only.
- report does not state that enrichment validates or proves a mechanism.

Failure modes that are still useful:

- If the report says `curated_prior_support` because measured scope is unknown, the UID scope was not copied into all artifacts/claims correctly.
- If the report says `unsupported` because `evidence_refs` is missing, the claim did not copy the exact returned artifact id.
