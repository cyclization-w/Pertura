# Pertura 0.2.0a19 final-canary evidence

This document records pre-formal canary evidence. Canary outcomes validate the
frozen execution and measurement path; they are not included in the formal
benchmark aggregates.

## Frozen checkpoint

- Git commit: `666cc99d21f7cb05ad215a44018ea04c94ac1b3b`
- Provider condition: `pertura_full`
- Provider model: `deepseek-v4-flash`
- Paper-agent turn budget: 64
- Bundled Pertura skill count: 7

Any code, prompt, skill, task contract, submission schema, reference, or scorer
change invalidates the canaries recorded for this checkpoint.

## KANG-01 supplemental scientific-fidelity canary

- Slurm job: `34479586`
- Workflow/task: `WF-KANG / KANG-01`
- Repeat: 1
- Started: `2026-07-18T12:27:45-0700`
- Finished: `2026-07-18T13:10:51-0700`
- Wall time: approximately 43 minutes
- Analysis run: `run_3d15461d63524058ac7971b0b9765423`
- Conversation: `conversation_fb6344e53b694e6797b23adc9b07d8c5`
- Execution root: `/scratch/users/twang05/Project/PerturaBenchmark/paper-v1/canary/a19/WF-KANG/pertura_full/repeat-1/45aeadd78b3f4a0a9d898362e0da838f`
- Execution status: `completed`
- Score status: `passed`
- Passed required tasks: 1 of 1
- Skill leakage detected: false

The workflow record classified KANG-01 as
`supplemental_scientific_fidelity` and reported task status `passed`.

### Submission-boundary evidence

The accepted task output contained all three measurement-boundary records:

| File | Size |
|---|---:|
| `benchmark_result.json` | 4,772 bytes |
| `submitted_turn_draft.json` | 3,459 bytes |
| `submission_receipt.json` | 707 bytes |

This verifies that the provider replaced the runner-initialized neutral result,
submitted a schema-valid TurnDraft, and obtained the typed-submission receipt.
It closes the failure observed in the preceding canary, where scientific
artifacts passed their reference comparisons but the SDK MCP wrapper hid the
submission validation error and no receipt was written.

### Scientific artifact evidence

The retained output inventory included:

| Artifact | Size |
|---|---:|
| `de_results.tsv` | 622,139 bytes |
| `de_summary.json` | 236 bytes |
| `design_matrix.tsv` | 237 bytes |
| `design_manifest.json` | 357 bytes |
| `null_calibration.tsv` | 798 bytes |
| `null_calibration_details.tsv` | 494 bytes |
| `calibration_pseudobulk_counts.tsv` | 1,038,132 bytes |
| `calibration_sample_manifest.tsv` | 230 bytes |
| `calibration_accounting.json` | 298 bytes |
| `pseudobulk_counts.tsv` | 1,025,502 bytes |
| `sample_manifest.tsv` | 227 bytes |
| `pseudobulk_accounting.json` | 297 bytes |

Configuration records for evaluation materialization, edgeR, calibration
materialization, and null calibration were retained alongside these outputs.
The authoritative evidence remains the server-side files and verdict under the
execution root above; file sizes here are an audit index, not scientific
measurements.

## Remaining final canaries

- [ ] `PAPA-01 + PAPA-02 / pertura_full`
- [ ] `PAPA-06 / prompt_only`
- [ ] `REPL-01 / free_codeact`

All remaining canaries must use the same checkpoint. Scientific pass or failure
is retained as observed; infrastructure or measurement-path incidents must be
classified before any rerun.
