# Smoke 08: Curated Enrichment Context Is Not Mechanism

Goal: verify that a curated enrichment result bound to a measured DE artifact can provide measured-context support, but cannot validate a mechanism.

Use synthetic files if needed; do not call external enrichment services.

Required steps:

1. Create `outputs/smoke08_measured_de.csv` with compact DE-like rows and adjusted p-values.
2. Register a perturbation design manifest for a KLF1 vs NegCtrl-style contrast.
3. Register the measured DE artifact with manifest-derived UID scope and structured eligibility.
4. Create `outputs/smoke08_enrichment.json` containing a curated pathway/gene-set enrichment result.
5. Register it with `mcp__pertura_evidence__register_curated_enrichment_artifact`, binding `input_measured_artifact_id` to the measured DE artifact and providing `input_gene_set_hash`, background universe, database/version, term id, method, and pvalue/padj.
6. Create a claim that asks for a validated mechanism from the enrichment.
7. After registering artifacts, copy the exact returned `artifact_id` values into the claims JSON. Every claim must set `evidence_refs` to the relevant registered artifact id, not a filename, basename, or prose reference.
8. Evaluate claims and render `reports/evidence_report.md`.

Expected runtime surface:

- max strength is measured association with curated context, or curated prior support if binding/metadata is missing.
- report states enrichment provides curated context only.
- report does not state that enrichment validates a mechanism.
