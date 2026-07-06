# Smoke 05: Policy Threshold Probe With Manifest UID Scope

Goal: demonstrate that min-cell policy is versioned and deterministic: the same registry and claim can change decision only when policy changes, and policy hash changes with it.

This is a deterministic runtime probe, not a free-form analysis task. Do not inspect internal Pertura source files. Do not write a custom policy script from scratch. Use the provided helper script so the smoke tests policy behavior rather than Claude's ability to reverse-engineer the internal API.

Tasks:

1. Run the Python environment self-check from the system prompt.
2. Run the provided helper script from the run bundle with the preflighted Python executable:

```powershell
"<preflighted python>" task/helpers/policy_threshold_probe.py
```

3. Confirm that the helper wrote:
   - `outputs/policy_threshold_decisions.json`
   - `outputs/policy_threshold_notes.md`
   - `artifacts/claim_decisions.json`
   - `reports/evidence_report.md`
4. Read only `outputs/policy_threshold_notes.md` or compact JSON keys from `outputs/policy_threshold_decisions.json` if needed. Do not read large logs or internal source files.
5. Final response: point to the JSON, notes, and report. Do not write an independent scientific conclusion.

Expected Pertura behavior:

- Policy hash differs between min-cell 50 and min-cell 30.
- Strict policy caps the claim below measured association because target cells are below 50.
- Relaxed policy allows measured association because target cells are above 30 and manifest UID scope plus eligibility are valid.
- The result is deterministic and independent of prompt wording.

