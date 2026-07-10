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

Language and encoding discipline:

- Write runtime artifacts, registered metadata, reports, and stage summaries in English.
- Prefer ASCII punctuation in JSON and Markdown fields; avoid smart quotes, non-ASCII dashes, and decorative symbols.

For this evidence-gated v0 run, create these files when possible:

- `outputs/analysis_notes.md`: concise notes about what you inspected and found.
- `outputs/observed_files.json`: input files discovered, with format guesses.
- `outputs/design_notes.md`: guide/control/single-vs-dual perturbation observations.
- `outputs/guide_summary.csv` or `outputs/guide_summary.json`: guide counts when metadata is available.
- `outputs/plots/`: optional diagnostic plots.
- `reports/evidence_report.md`: runtime-rendered evidence report when measured DE or effect artifacts are produced.
- `artifacts/claim_decisions.json`: optional output from `mcp__pertura_evidence__evaluate_claims` when explicit claims are evaluated.

Runtime-owned trust files:

- Do not write `manifest.json`, the evidence registry, execution ledger, claim decisions, or files under `reports/` directly. They are written only by Pertura runtime/MCP tools.

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

- The claim policy is selected once by the runtime. Never request or attempt a weaker policy from an MCP call.
- Call `mcp__pertura_evidence__route_analysis_method` before choosing a statistical family when design facts are available.
- Prefer `run_target_reliability_audit`, `run_pseudobulk_de`, and the trusted control-calibration MCP tools when their input contracts fit. These runners write canonical execution-ledger records; register their outputs with the matching evidence registrar.

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
- If you produce a transcriptomic state reference, clustering, marker, or annotation summary,
  call `mcp__pertura_evidence__register_cell_state_reference_artifact`.
  Cell state references define scope/context and downstream stratification only; do not present them as perturbation effect evidence.
- If you produce a cell-level QC summary, call `mcp__pertura_evidence__register_cell_qc_artifact`.
  Cell QC is analysis-eligibility evidence only; do not present it as biological effect evidence.
- If you produce NTC-vs-NTC or label-permutation calibration summaries, call
  `mcp__pertura_evidence__register_control_calibration_artifact`. Control calibration is
  eligibility evidence only; it cannot support an effect claim by itself.
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
  If the MCP tool result is not visible, read `artifacts/claimable_artifacts.json`
  for effect evidence handoffs, or `artifacts/latest_registration.json` for the most recent registration,
  and copy `next_claim_template.scope` and `next_claim_template.evidence_refs` from there.
  Do not copy or invent claim strength from the template; choose `requested_strength`
  only from the scientific statement being tested. If the response says the artifact
  is scope/eligibility-only, do not put that artifact id in effect-claim `evidence_refs`.
- For explicit scientific conclusions, pass explicit `claims` into
  `mcp__pertura_evidence__render_evidence_report`; it writes both `reports/evidence_report.md`
  and the final `artifacts/claim_decisions.json`. Do not separately re-evaluate or re-render
  unless the claims or registry changed.
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
- If you produce cell-state or cluster composition evidence, register it with
  `mcp__pertura_evidence__register_composition_effect_artifact`. Composition effects support
  measured composition associations only; do not present them as causal fate conversion,
  target engagement, mechanisms, or driver validation.
- If you produce or receive prediction or curated-prior artifacts, register them
  with the corresponding Pertura evidence MCP tool. Do not label prediction or
  prior artifacts as measured evidence.
- If you harvest GEARS, scGPT, Geneformer, CPA/scGen, CellOracle, or custom virtual
  perturbation output, register it with `mcp__pertura_evidence__register_virtual_perturbation_prediction_artifact`
  or `mcp__pertura_evidence__register_virtual_cell_state_transition_artifact` before making claims.
  Virtual perturbation output is prediction evidence, not measured evidence.
- If you compare a virtual prediction with a registered measured artifact, register the metric with
  `mcp__pertura_evidence__register_prediction_measured_concordance_artifact`. Concordance is not
  mechanism validation and does not create measured strength. Any reported scope_match is diagnostic only;
  Pertura computes scope compatibility from registered manifest UID fields.
- The final response should point to the rendered report and remain a working note.
"""


CAPABILITY_OUTPUT_CONTRACT = """# Pertura capability output contract

Write exploratory code, notebooks, tables and figures under `outputs/` only.
Pertura writes contracts, signed receipts, promotion decisions and final reports;
never create or edit those trust objects yourself.

Use the five Pertura tools as the product control plane:

1. `inspect_dataset` creates a versioned DatasetContract.
2. `run_diagnostic` runs a registered QC capability.
3. `run_analysis` runs a registered scientific analysis capability.
4. `evaluate_virtual_model` evaluates predictions without relabeling them as measurements.
5. `finalize_report` seals receipts and renders committed results.

CodeAct remains available for Read/Glob/Grep/Bash/Write/Edit/NotebookEdit exploration.
Exploratory CodeAct output is untrusted until a bundled capability executes and the
independent verifier commits a signed result. Tool responses are compact; inspect
large Parquet/JSON/PNG/SVG outputs at the returned paths.

Never infer missing control, guide-target, replicate, donor, batch, dose, time or
state identity. Report unresolved fields and request a design confirmation.
Never write an effect through a design confirmation.
"""


def build_default_task(input_source: Path | None) -> str:
    source_text = str(input_source) if input_source else "the files under input/"
    return f"""Inspect and analyse this Perturb-seq project with the Pertura capability workflow.

Input source: {source_text}

Tasks:

0. Run the Python environment self-check shown in the system prompt.
1. Call `inspect_dataset`; review the DatasetContract and unresolved design fields.
2. Use CodeAct to inspect local files and resolve candidates, without inventing identity metadata.
3. Run appropriate registered diagnostics in phase order.
4. Run only analyses whose blockers are resolved; exploratory scripts remain exploratory.
5. Call `finalize_report` after the committed results needed for this task exist.

Use CodeAct freely: inspect files, write small Python scripts, print intermediate
results, and correct errors. Keep stdout compact and do not modify input data.
Use only local evidence; do not inject public dataset knowledge or memorized biology.
"""


def build_system_prompt(workspace: ClaudeRunWorkspace, *, python_environment: Any | None = None, interaction_mode: str = "benchmark", stage_id: str | None = None, tool_surface: str = "capability") -> str:
    python_section = ""
    if python_environment is not None:
        python_section = "\n" + python_environment.prompt_section()
    if stage_id and tool_surface == "capability":
        raise ValueError("stage prompts are not available on the production capability surface")
    stage_section = _legacy_stage_prompt_section(stage_id) if stage_id else ""
    return f"""You are Pertura, a Perturb-seq analysis coding agent.

This is the capability-first Pertura runtime. Scientific authority comes only
from committed verifier results; CodeAct itself remains fully available.

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
11. Use only the five high-level Pertura tools for scientific commits. Discover capabilities instead of inventing method names.
12. `inspect_dataset` and design confirmations establish identity only; they cannot create measured effects.
13. A result is trusted only when a bundled capability returns a valid signed receipt. Never hand-write, copy, or edit receipts.
14. Call `finalize_report` for user-visible scientific conclusions; free-form prose cannot promote a claim.
15. Use English for runtime artifacts, registered metadata, reports, and stage summaries. Prefer ASCII punctuation in JSON and Markdown fields.
16. The run-level claim policy is immutable and cannot be selected or weakened by a tool call.
17. Do not write runtime-owned contracts, receipts, commit-store projections, promotion decisions or reports directly.
18. `stage_id` is progress/help metadata only and never scientific authority.

{stage_section}
The output contract is also written at `task/PERTURA_OUTPUT_CONTRACT.md`.
"""


def write_prompt_files(workspace: ClaudeRunWorkspace, *, task: str, python_environment: Any | None = None, interaction_mode: str = "benchmark", stage_id: str | None = None, tool_surface: str = "capability") -> str:
    system_prompt = build_system_prompt(workspace, python_environment=python_environment, interaction_mode=interaction_mode, stage_id=stage_id, tool_surface=tool_surface)
    output_contract = CAPABILITY_OUTPUT_CONTRACT if tool_surface == "capability" else OUTPUT_CONTRACT
    workspace.write_task_files(task=task, system_prompt=system_prompt, output_contract=output_contract)
    if stage_id:
        workspace.write_text(workspace.task_dir / "PERTURA_STAGE_PROMPT.md", _legacy_stage_prompt_section(stage_id))
    _write_task_helpers(workspace)
    return system_prompt


def _legacy_stage_prompt_section(stage_id: str) -> str:
    """Load frozen stage help only for explicit legacy regression callers."""

    from pertura_runtime.stages import build_stage_prompt_section

    return build_stage_prompt_section(stage_id)

def _write_task_helpers(workspace: ClaudeRunWorkspace) -> None:
    """Stage deterministic task helpers into the isolated run bundle."""

    for filename, source in TASK_HELPERS.items():
        if source.exists():
            workspace.write_text(workspace.task_dir / "helpers" / filename, source.read_text(encoding="utf-8"))
