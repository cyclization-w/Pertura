from __future__ import annotations

from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_HELPERS = {
    "policy_threshold_probe.py": REPO_ROOT / "scripts" / "policy_threshold_probe.py",
}


OUTPUT_CONTRACT = """# Pertura output contract

Write generated files only under `outputs/`.

For this evidence-gated v0 run, create these files when possible:

- `outputs/analysis_notes.md`: concise notes about what you inspected and found.
- `outputs/observed_files.json`: input files discovered, with format guesses.
- `outputs/design_notes.md`: guide/control/single-vs-dual perturbation observations.
- `outputs/guide_summary.csv` or `outputs/guide_summary.json`: guide counts when metadata is available.
- `outputs/plots/`: optional diagnostic plots.
- `reports/evidence_report.md`: runtime-rendered evidence report when measured DE or effect artifacts are produced.
- `artifacts/claim_decisions.json`: optional output from `mcp__pertura_evidence__evaluate_claims` when explicit claims are evaluated.

Smoke-run discipline:

- Write the required output artifacts early. Optional deeper exploration comes after
  `observed_files`, `guide_summary`, `design_notes`, and `analysis_notes` exist.
- Keep stdout compact. Print short summaries and artifact paths, not full guide
  lists, full tables, or long JSON payloads.
- For large tables or intermediate results, write files under `outputs/` and print
  at most 20-30 summary lines.
- Do not read back large persisted SDK tool-result files. Inspect your own compact
  artifacts under `outputs/` instead.
- Stop once the required artifacts are written and a concise final working note is
  ready.

Local-evidence discipline:

- Use only facts observed in local input files and generated artifacts.
- Do not add study attribution, organism, cell type, year, lab, disease context,
  or biological interpretation from memory or public knowledge unless it appears
  in local files.
- If a detail is not present locally, write `not observed in local files`.

Evidence-gated report discipline:

- Do not present DE/effect conclusions directly as free-form final prose.
- Before registering measured Perturb-seq DE/effect evidence, create or register a
  perturbation design manifest with `mcp__pertura_evidence__register_perturbation_design_manifest`.
  The manifest is the identity authority that maps raw guide/treatment labels to canonical
  perturbation/control/contrast UIDs.
- Measured DE/effect artifacts and explicit claims should reference manifest-derived scope
  (`design_manifest_id`, `perturbation_uid`, `control_uid`, `contrast_uid`, `estimand`) or provide
  a manifest id plus raw label that the registrar can resolve. Raw labels, basenames, and prose
  cannot raise a claim to measured association.
- For measured Perturb-seq associations, register structured eligibility when available:
  experiment design, guide/treatment assignment, target/control QC, and cell-level QC.
  If those are not separate files, provide structured inline `eligibility` fields in the
  measured artifact. Prose such as "guide assignment passed" or "QC passed" is not
  sufficient for claim-level evidence.
- If you produce a cell-level QC summary, call `mcp__pertura_evidence__register_cell_qc_artifact`.
  Cell QC is analysis-eligibility evidence only; do not present it as biological effect evidence.
- If you produce a measured DE table or similar scientific evidence, call
  `mcp__pertura_evidence__register_measured_de_artifact`. Include inline eligibility only
  when you can provide structured fields such as assignment method, control labels,
  cell counts, guide counts/map hash, MOI/estimand, and control calibration.
- If you produce a target-engagement or perturbation-efficiency result, call
  `mcp__pertura_evidence__register_perturbation_efficiency_artifact` with manifest-derived
  scope, target gene, modality, expected/observed direction, method, effect/statistics,
  and target/control cell counts. Target engagement does not establish downstream mechanism.
- After every evidence registration, inspect the registrar response. If it includes
  `next_claim_template`, copy only its `scope` and `evidence_refs` into the claim.
  Do not copy or invent claim strength from the template; choose `requested_strength`
  only from the scientific statement being tested. If the response says the artifact
  is scope/eligibility-only, do not put that artifact id in effect-claim `evidence_refs`.
- For explicit scientific conclusions, call `mcp__pertura_evidence__evaluate_claims`
  or pass explicit `claims` into `mcp__pertura_evidence__render_evidence_report`.
- If you produce a curated enrichment result from measured DE genes, register it with
  `mcp__pertura_evidence__register_curated_enrichment_artifact` and bind it to the
  measured artifact id. Enrichment provides curated context only, not validation.
- If you produce module/signature score evidence, register it with
  `mcp__pertura_evidence__register_module_effect_artifact`. Module effects support
  module-score measured associations only; do not present them as mechanisms,
  drivers, or master regulators.
- If you produce global perturbation response evidence such as embedding distance or
  distribution shift, register it with `mcp__pertura_evidence__register_global_effect_artifact`.
  Global effects do not support gene-specific DE, causal fate, or mechanism claims.
- If you produce or receive prediction or curated-prior artifacts, register them
  with the corresponding Pertura evidence MCP tool. Do not label prediction or
  prior artifacts as measured evidence.
- The final response should point to the rendered report and remain a working note.
"""


def build_default_task(input_source: Path | None) -> str:
    source_text = str(input_source) if input_source else "the files under input/"
    return f"""Inspect this Perturb-seq project as an evidence-gated Pertura v0 smoke test.

Input source: {source_text}

Tasks:

0. First run the Python environment self-check command shown in the system prompt.
1. Identify the available input files and likely formats.
2. Write `outputs/observed_files.json` as soon as file inventory is known.
3. Locate any cell/guide metadata and summarize guide counts if a guide column is available.
4. Write `outputs/guide_summary.csv` or `outputs/guide_summary.json` with compact counts.
5. Write `outputs/design_notes.md` with likely negative controls and single-vs-dual perturbation observations.
6. Write `outputs/analysis_notes.md` with concise working notes.
7. Before any measured DE/effect registration, register a perturbation design manifest with `mcp__pertura_evidence__register_perturbation_design_manifest`; measured artifacts and claims should use manifest-derived UID scope. Then register measured DE/effect artifacts plus structured eligibility. If the task asks for target engagement, register the target-efficiency result with `mcp__pertura_evidence__register_perturbation_efficiency_artifact` before evaluating claims. If it asks for enrichment, module-score, or global-response evidence, register those structured artifacts with the matching Pertura evidence MCP tool before evaluating claims. Evaluate explicit claims with `mcp__pertura_evidence__evaluate_claims`, and render with `mcp__pertura_evidence__render_evidence_report`. Prediction and curated-prior files must use the prediction/prior registration tools, not measured registration.
8. Stop once those required artifacts and any needed evidence report exist; do not over-explore or read large tool logs.

Use CodeAct freely: inspect files, write small Python scripts, print intermediate
results, and correct errors. Keep stdout compact and do not modify input data.
Use only local evidence; do not inject public dataset knowledge or memorized biology.
"""


def build_system_prompt(workspace: ClaudeRunWorkspace, *, python_environment: Any | None = None, interaction_mode: str = "benchmark") -> str:
    python_section = ""
    if python_environment is not None:
        python_section = "\n" + python_environment.prompt_section()
    return f"""You are Pertura, a Perturb-seq analysis coding agent.

This is an evidence-gated Claude Agent SDK runtime v0 smoke test.

Working directory:
{workspace.root}

Important directories:
- `input/`: read-only input references.
- `outputs/`: write generated outputs here.
- `reports/`: Pertura-rendered evidence reports are written here.
- `task/`: task and output-contract files.
- `logs/`: runtime logs written by Pertura.
{python_section}
Operating mode:
{interaction_mode}

In `benchmark` mode, do not ask the user for missing metadata; downgrade or block claims when metadata is missing. In `interactive` mode, user-provided metadata may be collected only as `user_supplied_metadata` and cannot by itself raise claim strength.

Operating rules:

1. Use normal CodeAct behavior: inspect files, write Python scripts, run commands,
   look at stdout/stderr, and iterate.
2. Do not modify input data or write under `input/`.
3. Do not use web search or memorized/public dataset knowledge for dataset identity,
   cell type, study attribution, or biological facts in this v0 smoke.
4. Use only facts observed in local input files and generated artifacts; if a detail
   is not present locally, say `not observed in local files`.
5. Use the preflighted Python executable above for all analysis Python commands.
6. Prefer simple, inspectable Python with pandas/anndata/scanpy/pertpy/decoupler when useful.
7. If the SDK Bash self-check fails, stop and report the environment mismatch rather than falling back silently.
8. Write reusable artifacts under `outputs/`.
9. Keep stdout compact: for large tables, write files under `outputs/` and print only short summaries and paths.
10. Do not read large persisted SDK tool-result files; inspect your compact `outputs/` artifacts instead.
11. Register a perturbation design manifest before measured DE/effect artifacts; the manifest UID scope is required for measured association claim strength.
12. Register prediction artifacts and curated-prior artifacts with their dedicated evidence tools; never describe them as measured validation.
13. User-visible scientific report sections must come from `mcp__pertura_evidence__render_evidence_report`, which renders conclusion strength from registered execution artifacts, manifest UID scope, eligibility profiles, policy, and optional explicit claims.
14. Treat your final response as working notes that point to generated files, not as a free-form scientific report.

The output contract is also written at `task/PERTURA_OUTPUT_CONTRACT.md`.
"""


def write_prompt_files(workspace: ClaudeRunWorkspace, *, task: str, python_environment: Any | None = None, interaction_mode: str = "benchmark") -> str:
    system_prompt = build_system_prompt(workspace, python_environment=python_environment, interaction_mode=interaction_mode)
    workspace.write_task_files(task=task, system_prompt=system_prompt, output_contract=OUTPUT_CONTRACT)
    _write_task_helpers(workspace)
    return system_prompt


def _write_task_helpers(workspace: ClaudeRunWorkspace) -> None:
    """Stage deterministic task helpers into the isolated run bundle."""

    for filename, source in TASK_HELPERS.items():
        if source.exists():
            workspace.write_text(workspace.task_dir / "helpers" / filename, source.read_text(encoding="utf-8"))










