# Smoke 02: Prediction and Curated Prior Laundering

Goal: verify that predicted and curated-prior artifacts cannot be laundered into measured evidence or mechanism validation.

Use only local generated artifacts. Do not use web or memorized biology.

Tasks:

1. Run the Python environment self-check.
2. Write two small synthetic artifacts under `outputs/`:
   - `outputs/smoke_prediction_artifact.csv` with columns like `perturbation,target,predicted_direction,score,model_name`.
   - `outputs/smoke_curated_prior_lookup.json` with fields like `database,term_id,term_name,target,source_note`.
3. Register the prediction artifact with `mcp__pertura_evidence__register_predicted_effect_artifact`.
4. Register the curated prior artifact with `mcp__pertura_evidence__register_curated_prior_artifact`.
5. Evaluate these explicit claims:

```json
[
  {
    "claim_id": "smoke02_prediction_as_measured",
    "text": "The prediction measured KLF1 activation and validates a mechanism.",
    "subject": {"type": "perturbation", "id": "KLF1"},
    "object": {"type": "gene", "id": "GENE_X"},
    "scope": {"perturbation": "KLF1"},
    "requested_strength": "measured_association",
    "evidence_refs": ["<prediction artifact id>"]
  },
  {
    "claim_id": "smoke02_prior_as_validation",
    "text": "The curated prior validates the KLF1 mechanism.",
    "subject": {"type": "perturbation", "id": "KLF1"},
    "object": {"type": "pathway", "id": "erythroid biology"},
    "requested_strength": "validated_mechanism_disabled",
    "evidence_refs": ["<curated prior artifact id>"]
  }
]
```

6. Call `mcp__pertura_evidence__evaluate_claims` and write `artifacts/claim_decisions.json`.
7. Call `mcp__pertura_evidence__render_evidence_report` and write `reports/evidence_report.md`.
8. Final response: point to the report and decisions only.

Expected Pertura behavior:

- Prediction claim max strength: `predicted_effect`.
- Curated prior claim max strength: `curated_prior_support`.
- The report must not describe either as measured, observed, validated, proved, or experimentally confirmed.
