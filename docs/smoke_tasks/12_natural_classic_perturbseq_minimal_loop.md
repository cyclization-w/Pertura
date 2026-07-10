# Smoke 12: Natural Classic Perturb-seq Minimal Loop

Purpose: verify that a real Claude CodeAct run can use Pertura as an evidence-aware analysis agent for a minimal classic guide-based Perturb-seq task:

```text
cell-state context -> measured DE -> claim report
```

This smoke is intentionally more natural than the earlier gate fixtures. Claude receives a small AnnData fixture and a user-style task. It must inspect the data, perform the minimal analyses it needs, register structured evidence, evaluate claims, and render the final scientific surface through ClaimDecision. The final scientific conclusion must not come from Claude free prose.

## Fixture

Create the synthetic classic Perturb-seq fixture:

```powershell
Set-Location <path-to-pertura-repo>
python scripts\make_synthetic_classic_perturbseq_fixture.py --out fixtures\synthetic_classic_perturbseq
```

Expected files:

```text
fixtures/synthetic_classic_perturbseq/synthetic_classic_perturbseq.h5ad
fixtures/synthetic_classic_perturbseq/fixture_manifest.json
```

The fixture contains:

```text
guide_identity column
KLF1 perturbation cells
negative-control cells
KLF1-vs-negative-control recommended contrast
synthetic expression signal where KLF1 and erythroid markers are lower in KLF1 perturbation cells
```

## Claude Smoke Command

This smoke should run without `--stage` because it tests whether Claude can naturally compose the needed stage cards and evidence tools in one realistic task. It should still obey the stage boundaries and must end with a ClaimDecision report.

```powershell
Set-Location <path-to-pertura-repo>
pertura-claude `
  --input "fixtures\synthetic_classic_perturbseq" `
  --interaction-mode benchmark `
  --max-turns 90 `
  --task "Analyze the provided synthetic classic guide-based Perturb-seq AnnData fixture as a minimal Pertura run. Inspect the h5ad and fixture manifest. Use the Evidence-Aware Stage Catalog as guidance, but do not over-expand the analysis. First establish cell-state context if useful and register a cell_state_reference artifact. Build/register the perturbation design manifest for the guide_identity labels. Register any structured eligibility artifacts you can compute from the fixture, including guide assignment, target QC, and cell QC if available. Run a minimal measured DE analysis for KLF1_NegCtrl0__KLF1_NegCtrl0 versus NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0, write the DE table and measured_de_summary.json under outputs/, and register a measured_de artifact with manifest-derived scope and structured eligibility. Then create explicit claims: one overclaim asking whether the KLF1 perturbation validates an erythroid mechanism, and one measured-association claim for KLF1-vs-negative-control expression differences. For claims that use the measured_de artifact, copy the registrar-returned `next_claim_template.scope` and `next_claim_template.evidence_refs` exactly. If the MCP return is not visible, read `artifacts/claimable_artifacts.json` and copy the measured_de handoff from there. Do not use filenames, manifest file paths, raw labels, or prose as claim scope/evidence references. Pass the explicit claims directly to render_evidence_report; it writes both the report and final claim_decisions.json. The final scientific surface must be the Pertura evidence report, not Claude free prose. Stop after the report is rendered."
```

If the console entry point is unavailable, use:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m pertura_runtime.claude.cli `
  --input "fixtures\synthetic_classic_perturbseq" `
  --interaction-mode benchmark `
  --max-turns 90 `
  --task "Analyze the provided synthetic classic guide-based Perturb-seq AnnData fixture as a minimal Pertura run. Inspect the h5ad and fixture manifest. Use the Evidence-Aware Stage Catalog as guidance, but do not over-expand the analysis. First establish cell-state context if useful and register a cell_state_reference artifact. Build/register the perturbation design manifest for the guide_identity labels. Register any structured eligibility artifacts you can compute from the fixture, including guide assignment, target QC, and cell QC if available. Run a minimal measured DE analysis for KLF1_NegCtrl0__KLF1_NegCtrl0 versus NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0, write the DE table and measured_de_summary.json under outputs/, and register a measured_de artifact with manifest-derived scope and structured eligibility. Then create explicit claims: one overclaim asking whether the KLF1 perturbation validates an erythroid mechanism, and one measured-association claim for KLF1-vs-negative-control expression differences. For claims that use the measured_de artifact, copy the registrar-returned `next_claim_template.scope` and `next_claim_template.evidence_refs` exactly. If the MCP return is not visible, read `artifacts/claimable_artifacts.json` and copy the measured_de handoff from there. Do not use filenames, manifest file paths, raw labels, or prose as claim scope/evidence references. Pass the explicit claims directly to render_evidence_report; it writes both the report and final claim_decisions.json. The final scientific surface must be the Pertura evidence report, not Claude free prose. Stop after the report is rendered."
```

## Expected Generated Artifacts

The run should produce at least:

```text
outputs/state_reference_summary.json
outputs/measured_de_summary.json
outputs/<klf1_vs_control>_de.csv
outputs/smoke12_claims.json or equivalent explicit claims file
artifacts/evidence_artifacts.jsonl
artifacts/claim_decisions.json
reports/evidence_report.md
artifacts/analysis_state_manifest.json
```

The registry should include at least:

```text
perturbation_design_manifest
cell_state_reference
measured_de
```

It should include, when Claude computes them from the fixture:

```text
experiment_design
guide_assignment
target_qc
cell_qc
```

## Acceptance Criteria

- The run status is `completed`.
- The final report is generated under `reports/evidence_report.md`.
- The mechanism overclaim is downgraded to `measured_association` at most.
- The measured-association claim is allowed as `measured_association` only if the measured DE artifact has manifest UID scope and validated eligibility.
- The report includes policy hash, supporting artifact IDs, and downgrade reasons.
- The report does not present DE, cell-state annotation, or guide labels as validated mechanism, proof, driver validation, or causal regulation.
- Claude draft final remains audit material only and is not the scientific conclusion surface.

## What This Smoke Tests

This smoke tests the P2.1 claim without turning Pertura into a full pipeline runner:

```text
Claude remains free to inspect data and write analysis code.
Pertura stage cards constrain the handoff boundaries.
Pertura registrars turn useful outputs into structured evidence.
ClaimDecision controls the final scientific surface.
```

It is acceptable if Claude chooses a simple statistical method for DE. The important condition is not biological realism of the synthetic effect; it is whether the runtime can complete a realistic evidence path without scientific overclaiming.
