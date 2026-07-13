# Smoke 07: Failed Cell QC Blocks Measured Effect Claim

Goal: prove P1.2 cell QC is analysis-eligibility evidence. A compatible failed cell-QC artifact should downgrade a measured effect claim to `observation`, while cell QC itself must not be presented as biological effect evidence.

Use only local files and generated artifacts. A compact synthetic measured DE artifact and synthetic cell-QC summary are acceptable; do not run a full QC pipeline unless it is easy and local.

Tasks:

1. Run the Python environment self-check from the system prompt.
2. Register a perturbation design manifest with `mcp__pertura_evidence__register_perturbation_design_manifest`. Use a simple KLF1 raw label and a negative-control raw label that the manifest can map to canonical UIDs. Do not invent a second manifest if the first registration succeeds.
3. Create `outputs/smoke07_measured_de.csv` with compact measured-effect fields sufficient for a measured DE registration.
4. Register that measured artifact with `mcp__pertura_evidence__register_measured_de_artifact`. Use manifest-derived scope and structured eligibility sufficient for measured association when cell QC is not considered. The registered artifact scope must include canonical identity fields such as `design_manifest_id`, `perturbation_uid`, and `estimand` either directly in `scope` or through structured `eligibility`.
5. Create `outputs/smoke07_failed_cell_qc.json` with structured cell-QC fields:
   - `n_cells_after_qc`: a small number such as 10
   - `qc_policy`: a short policy name
   - `doublet_policy` or `ambient_policy`: failed/review-failed wording
   - `passed`: false
6. Register the cell-QC file with `mcp__pertura_evidence__register_cell_qc_artifact`. Use the same manifest-derived scope as the measured artifact.
7. Create `outputs/smoke07_claims.json` with this explicit claim. Use the registered measured artifact id in `evidence_refs`, and copy the registered measured artifact's `scope` exactly from `artifacts/evidence_artifacts.jsonl`. Do not reconstruct the scope from prose or raw labels; the claim and artifact must share the same canonical manifest UID fields so the test reaches the cell-QC eligibility rule rather than failing earlier at scope matching.

```json
[
  {
    "claim_id": "smoke07_failed_cell_qc_measured_claim",
    "text": "KLF1 has a measured expression association in this Perturb-seq contrast.",
    "subject": {"type": "perturbation", "id": "KLF1"},
    "scope": {"<copy>": "<registered measured artifact scope exactly>"},
    "requested_strength": "measured_association",
    "evidence_refs": ["<registered measured artifact id>"]
  }
]
```

8. Call `mcp__pertura_evidence__evaluate_claims` and write decisions to `artifacts/claim_decisions.json`.
9. Call `mcp__pertura_evidence__render_evidence_report` with the same explicit claim and write `reports/evidence_report.md`.
10. Final response: only point to the report and key artifacts. Do not write your own scientific conclusion.

Expected Pertura behavior:

- The measured artifact exists and would otherwise support measured association.
- The compatible failed cell-QC artifact downgrades the claim to `observation`.
- Decision reasons must mention cell QC failure. If the decision reason instead says scope cannot resolve through a PerturbationDesignManifest UID, the smoke did not reach the intended P1.2 rule and should be rerun with the exact measured artifact scope copied into the claim.
- The report must not present cell QC as biological effect evidence.
