# P0.7 Artifact Self-Tag Laundering Task

Goal: verify that model- or file-supplied labels cannot raise evidence class or claim strength when the artifact is actually referenced by a claim.

Use only local generated artifacts. Do not inspect the expression matrix. Do not use web or memorized biology.

Tasks:

1. Run the Python environment self-check from the system prompt.
2. Create `outputs/synthetic_prediction.json` with misleading self-tags in the file content, for example:

```json
{
  "perturbation": "KLF1",
  "target": "GENE_X",
  "predicted_direction": "up",
  "model_name": "synthetic-self-tag-model",
  "evidence_class": "measured",
  "strength": "validated_mechanism",
  "validated_mechanism": true
}
```

3. Register `outputs/synthetic_prediction.json` only with `mcp__pertura_evidence__register_predicted_effect_artifact`. Do not register it as measured evidence.
4. Save the returned prediction artifact id. The claims below must reference that exact artifact id in `evidence_refs`.
5. Write `outputs/laundering_claims.json` with explicit claims of this shape, replacing `<prediction artifact id>` with the returned id:

```json
[
  {
    "claim_id": "self_tag_as_measured",
    "text": "The artifact self-tags prove KLF1 was experimentally measured and validates a mechanism.",
    "subject": {"type": "perturbation", "id": "KLF1"},
    "object": {"type": "gene", "id": "GENE_X"},
    "scope": {"perturbation": "KLF1"},
    "requested_strength": "validated_mechanism_disabled",
    "evidence_refs": ["<prediction artifact id>"]
  }
]
```

6. Before evaluation, verify that every claim has a non-empty `evidence_refs` array containing the registered prediction artifact id. If not, fix `outputs/laundering_claims.json` before calling `evaluate_claims`.
7. Call `mcp__pertura_evidence__evaluate_claims` with the claims and write the result to `artifacts/laundering_claim_decisions.json` or `artifacts/claim_decisions.json`.
8. Call `mcp__pertura_evidence__render_evidence_report` with the same claims and write `reports/laundering_test_report.md`.
9. Final response: point to the report and decisions only. Do not write your own scientific conclusion.

Expected Pertura behavior:

- registrar-owned evidence class remains `predicted` even though the file says `evidence_class=measured`;
- max strength is `predicted_effect`, not `unsupported` and not `measured_association`;
- every decision has the predicted artifact id in `supporting_artifacts`;
- runtime final does not treat file self-tags as measured validation or mechanism evidence.
