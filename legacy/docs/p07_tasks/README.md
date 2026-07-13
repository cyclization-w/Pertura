# P0.7 Gate Utility Tasks

P0.7 measures whether Pertura changes the user-visible scientific surface when the analysis artifacts and claims are held fixed.

Use strong baseline semantics:

- gated and baseline share the same registry, claims, and policy snapshot;
- gated uses the Pertura ClaimDecision renderer;
- baseline uses free-prose wording from the same artifact/claim summary;
- ask_user is disabled for both paths.

Recommended harness command for completed smoke runs:

```powershell
python scripts\p07_gate_utility.py `
  --case pressure_mechanism=".claude_runs\<smoke01_run>" `
  --case prediction_prior_laundering=".claude_runs\<smoke02_run>" `
  --case dual_guide_attribution=".claude_runs\<smoke04_run>" `
  --summary-root ".p07_runs\p07_latest"
```

Outputs:

- per case: `reports/p07_<task>_gated.md`, `reports/p07_<task>_baseline.md`, `artifacts/p07_<task>_surface_eval.json`
- suite: `reports/p07_gate_utility_summary.md`, `artifacts/p07_gate_utility_summary.json`
