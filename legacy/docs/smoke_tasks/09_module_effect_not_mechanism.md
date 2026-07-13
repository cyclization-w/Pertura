# Smoke 09: Module Effect Is Not Mechanism Or Driver

Goal: verify that a module/signature score effect supports only a measured module-score association and cannot become a mechanism, driver, or master-regulator claim.

Use synthetic module-score output; do not add a module-scoring runner.

Required steps:

1. Create `outputs/smoke09_module_effect.json` with a compact module-score contrast result.
2. Register a perturbation design manifest for the claim scope.
3. Register the module effect with `mcp__pertura_evidence__register_module_effect_artifact` using manifest-derived scope.
4. Include required metadata: module id/name, module source, module gene-set hash, scoring method, contrast/scope, effect size, method, pvalue/padj, n target cells, and n control cells.
5. Include structured eligibility, either inline in `quality.eligibility` or through separate eligibility artifacts.
6. Create a claim that says the module effect validates a downstream mechanism or confirms a driver.
7. After registering artifacts, copy the exact returned `artifact_id` values into the claims JSON. Every claim must set `evidence_refs` to the relevant registered artifact id, not a filename, basename, or prose reference.
8. Evaluate claims and render `reports/evidence_report.md`.

Expected runtime surface:

- max strength is measured association when metadata and eligibility pass.
- if `module_source=all_cell_derived`, the surface includes a perturbation-contamination caveat.
- report does not present the module effect as a validated mechanism, driver, or master regulator.
