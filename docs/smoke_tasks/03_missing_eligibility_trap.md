# Smoke 03: Missing Eligibility Trap

Goal: verify that a measured DE-like artifact with only prose eligibility cannot reach claim-level measured association.

Use only local generated artifacts and local input inspection.

Tasks:

1. Run the Python environment self-check.
2. Write a compact DE-like file under `outputs/prose_only_de.csv` with columns like `gene,logfoldchange,padj`.
3. Register it with `mcp__pertura_evidence__register_measured_de_artifact`, including normal DE execution metadata but only prose-style eligibility such as:

```json
{"eligibility_passed": true, "notes": "guide assignment passed"}
```

Do not include structured fields such as assignment method, control labels, guide-to-target map hash, n target/control cells, cells per guide, MOI, or estimand.

4. Evaluate this claim:

```json
{
  "claim_id": "smoke03_prose_eligibility",
  "text": "KLF1 has a measured target-vs-control association.",
  "subject": {"type": "perturbation", "id": "KLF1"},
  "scope": {"perturbation": "KLF1", "control": "NegCtrl"},
  "requested_strength": "measured_association",
  "evidence_refs": ["<registered DE artifact id>"]
}
```

5. Call `evaluate_claims` and `render_evidence_report`.
6. Final response: point to the report and decisions only.

Expected Pertura behavior:

- Artifact-intrinsic ceiling may be measured association, but claim-conditioned ceiling should downgrade to `observation` or unsupported because EligibilityProfile is not validated.
- The report must mention missing structured perturbation-cell mapping / control / QC eligibility.
