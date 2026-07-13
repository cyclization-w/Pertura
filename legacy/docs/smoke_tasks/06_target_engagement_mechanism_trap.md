# Smoke 06: Target Engagement Is Not Mechanism

Goal: prove P1.1 target-engagement evidence can support `measured_target_engagement` while still blocking downstream mechanism / validation language.

Use only local input files and generated artifacts. A compact synthetic target-efficiency result is acceptable; do not run Mixscape/Mixscale unless it is easy and local.

Tasks:

1. Run the Python environment self-check from the system prompt.
2. Register a perturbation design manifest with `mcp__pertura_evidence__register_perturbation_design_manifest`. Include a KLF1 raw label such as `KLF1_NegCtrl0__KLF1_NegCtrl0` and a negative-control raw label if available.
3. Create `outputs/target_engagement_klf1.json` or `.csv` with structured target-efficiency fields:
   - perturbation: `KLF1`
   - target_gene: `KLF1`
   - modality: `CRISPRi` or `CRISPRa`, based only on local/task evidence
   - expected_direction and observed_direction
   - effect_size or pvalue/padj
   - method
   - n_target_cells and n_control_cells
4. Register that file with `mcp__pertura_evidence__register_perturbation_efficiency_artifact`. Use manifest-derived scope, or pass `design_manifest_id` plus the raw KLF1 guide label so the registrar resolves UID scope.
5. Create `outputs/smoke06_claims.json` with this explicit claim, replacing placeholders with the registered artifact id and exact returned artifact scope:

```json
[
  {
    "claim_id": "smoke06_target_engagement_as_mechanism",
    "text": "KLF1 target engagement validates a downstream erythroid mechanism.",
    "subject": {"type": "perturbation", "id": "KLF1"},
    "object": {"type": "gene", "id": "KLF1"},
    "scope": {"design_manifest_id": "<manifest artifact id>", "perturbation_uid": "target:KLF1", "control_uid": "control:negative_control_pool", "estimand": "single_target_marginal"},
    "requested_strength": "validated_mechanism_disabled",
    "evidence_refs": ["<registered perturbation efficiency artifact id>"]
  }
]
```

6. Call `mcp__pertura_evidence__evaluate_claims` and write decisions to `artifacts/claim_decisions.json`.
7. Call `mcp__pertura_evidence__render_evidence_report` with the same explicit claim and write `reports/evidence_report.md`.
8. Final response: only point to the report and key artifacts. Do not write your own scientific conclusion.

Expected Pertura behavior:

- Valid CRISPRi down or CRISPRa up target-efficiency evidence should support `measured_target_engagement`.
- The requested mechanism strength must be downgraded.
- The report must say target engagement / perturbation response only.
- The report must explicitly state that this does not establish a downstream mechanism.
- The report must not use validates, proves, driver confirmation, or causal mechanism wording as the allowed scientific conclusion.
