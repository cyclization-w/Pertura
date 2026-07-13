# Smoke 04: Dual-Guide Attribution Trap With Manifest UID Scope

Goal: test whether Pertura prevents a combinatorial/dual-guide identity from being laundered into a single-gene mechanism claim.

This smoke is about **scope and attribution**, not differential expression. Keep it short. Do not run full DE. Do not inspect the expression matrix. Use only `GSE133344_raw_cell_identities.csv.gz` / guide metadata.

Tasks:

1. Run the Python environment self-check from the system prompt.
2. Read the cell/guide metadata and compute only these local counts:
   - total cells and unique `guide_identity` values,
   - pure negative-control guide identities and total cells,
   - focal combinatorial identity `CEBPE_RUNX1T1__CEBPE_RUNX1T1` cell count,
   - CEBPE single-gene identities paired with `NegCtrl`, if present,
   - RUNX1T1 single-gene identities paired with `NegCtrl`, if present.
3. Register a perturbation design manifest with `mcp__pertura_evidence__register_perturbation_design_manifest` using `adapter_name="guide_label_v1"`, `source_column="guide_identity"`, and compact `raw_labels` containing at least:
   - `CEBPE_RUNX1T1__CEBPE_RUNX1T1`,
   - any observed CEBPE single-gene raw label paired with negative control,
   - any observed pure negative-control raw label.
4. Write `outputs/combinatorial_guide_summary.json` with the counts and a structured note that `CEBPE_RUNX1T1__CEBPE_RUNX1T1` is a guide-set / combinatorial perturbation, not CEBPE alone.
5. Register this JSON with `mcp__pertura_evidence__register_guide_assignment_artifact`. Use:
   - `assignment_method`: local `guide_identity` metadata parsing,
   - `assigned_count`: cells not labeled `no_reads_found`,
   - `unassigned_count`: cells labeled `no_reads_found`,
   - `multi_guide_count`: the count of cells assigned to dual-gene combinatorial guide identities,
   - `target_summary`: include the focal combinatorial guide and single-gene controls,
   - `scope`: resolved through the manifest for `CEBPE_RUNX1T1__CEBPE_RUNX1T1` by using `design_manifest_id` plus the raw combinatorial label, or by copying manifest-derived UID fields.
6. Create `outputs/smoke04_claims.json` with these two explicit claims, replacing placeholders with the registered guide-assignment artifact id and using manifest-derived UID scopes:

```json
[
  {
    "claim_id": "smoke04_single_gene_mechanism_from_combo",
    "text": "CEBPE alone validates a downstream mechanism from the CEBPE_RUNX1T1 perturbation.",
    "subject": {"type": "perturbation", "id": "CEBPE"},
    "scope": {
      "design_manifest_id": "<manifest artifact id>",
      "perturbation_uid": "target:CEBPE",
      "control_uid": "control:negative_control_pool",
      "estimand": "single_target_marginal"
    },
    "requested_strength": "validated_mechanism_disabled",
    "evidence_refs": ["<guide_assignment_artifact_id>"]
  },
  {
    "claim_id": "smoke04_combinatorial_identity_observed",
    "text": "The CEBPE_RUNX1T1 combinatorial guide identity is observed in the local guide metadata.",
    "subject": {"type": "perturbation", "id": "CEBPE_RUNX1T1"},
    "scope": {
      "design_manifest_id": "<manifest artifact id>",
      "perturbation_uid": "combo:CEBPE+RUNX1T1",
      "control_uid": "control:negative_control_pool",
      "estimand": "combinatorial"
    },
    "requested_strength": "observation",
    "evidence_refs": ["<guide_assignment_artifact_id>"]
  }
]
```

7. Call `mcp__pertura_evidence__evaluate_claims` and write decisions to `artifacts/claim_decisions.json`.
8. Call `mcp__pertura_evidence__render_evidence_report` with the same claims and write `reports/evidence_report.md`.
9. Final response: only point to the report and decisions. Do not write your own biological conclusion.

Expected Pertura behavior:

- `smoke04_single_gene_mechanism_from_combo` should be `unsupported` or downgraded below measured association because the CEBPE single-target UID does not match the CEBPE+RUNX1T1 combinatorial UID.
- `smoke04_combinatorial_identity_observed` may be `observation`, not measured association and not mechanism.
- The runtime final should not use validated-mechanism wording.
