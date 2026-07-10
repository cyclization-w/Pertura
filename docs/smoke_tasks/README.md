# Claude Smoke Tasks for Pertura P0

These task prompts are designed for `pertura-claude claude --task-file ...`.
They are not generic analysis requests. Each one targets a specific Pertura P0 behavior.

Recommended command shape:

```powershell
pertura-claude claude `
  --input "<dataset-path>" `
  --model deepseek-v4-pro `
  --base-url "https://api.deepseek.com/anthropic" `
  --api-key-env ANTHROPIC_API_KEY `
  --python-exe "<python-executable>" `
  --max-turns 24 `
  --task-file "docs\smoke_tasks\01_measured_association_with_eligibility.md"
```

Suggested order:

1. `01_measured_association_with_eligibility.md` - happy path measured association with explicit over-strong claim downgraded.
2. `02_prediction_prior_laundering.md` - synthetic prediction/prior artifacts cannot become measured validation.
3. `03_missing_eligibility_trap.md` - DE-like artifact with prose-only eligibility should not reach measured association.
4. `04_dual_guide_attribution_trap.md` - combinatorial guide attribution should not become single-gene mechanism.
5. `05_policy_threshold_probe.md` - deterministic policy hash / min-cell demonstration through local Python API.
6. `06_target_engagement_mechanism_trap.md` - P1.1 target engagement can pass while downstream mechanism remains blocked.
7. `07_cell_qc_blocks_measured_claim.md` - P1.2 failed compatible cell QC downgrades measured evidence to observation.

Review each run by checking:

- `reports/evidence_report.md`
- `artifacts/evidence_artifacts.jsonl`
- `artifacts/claim_decisions.json`, if produced
- terminal final summary

The expected scientific surface is always the runtime report, not Claude free prose.



- `14_virtual_perturbation_wrapper_family.md` - virtual perturbation prediction, concordance, and predicted transition laundering traps.
