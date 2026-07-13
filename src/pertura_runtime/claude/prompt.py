from __future__ import annotations

from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace

CAPABILITY_OUTPUT_CONTRACT = """# Pertura capability output contract

Write exploratory code, notebooks, tables and figures under `outputs/` only.
Pertura writes contracts, signed receipts, promotion decisions and final reports;
never create or edit those trust objects yourself.

Use the five Pertura tools as the product control plane:

1. `inspect_dataset` creates a versioned DatasetContract.
2. `run_diagnostic` runs a registered QC capability.
3. `run_analysis` runs a registered scientific analysis capability.
4. `evaluate_virtual_model` evaluates predictions without relabeling them as measurements.
5. `finalize_report` explicitly creates or reuses a versioned report revision.

CodeAct remains available for Read/Glob/Grep/Bash/Write/Edit/NotebookEdit exploration.
Exploratory CodeAct output is untrusted until a bundled capability executes and the
independent verifier commits a signed result. Tool responses are compact; inspect
large Parquet/JSON/PNG/SVG outputs at the returned paths.

Never infer missing control, guide-target, replicate, donor, batch, dose, time or
state identity. Report unresolved fields and request a design confirmation.
Never write an effect through a design confirmation.

End every provider turn with exactly one JSON object matching
`pertura-turn-draft-v1` with: schema_version, language, headline, findings,
hypotheses, limitations, questions_for_user, next_steps and artifact_refs.
Each finding must include finding_id, text, declared_role, result_ids and
limitations. The runtime derives the real role and claim ceiling from committed
results; declared_role is only a draft hint. Do not wrap the JSON in Markdown.
"""


def build_default_task(input_source: Path | None) -> str:
    source_text = str(input_source) if input_source else "the files under input/"
    return f"""Analyze this Perturb-seq project with the Pertura capability workflow.

Input source: {source_text}

Run the Python environment self-check, inspect the dataset, use the bundled
skills and CodeAct to understand the design, run only compatible diagnostics
and analyses. Create a report revision only when the user explicitly requests
one. Preserve unresolved design
facts and exploratory status instead of inventing metadata or claim strength.
"""


def build_system_prompt(workspace: ClaudeRunWorkspace, *, python_environment: Any | None = None, interaction_mode: str = "benchmark", stage_id: str | None = None, tool_surface: str = "capability", benchmark_condition: str = "pertura_full") -> str:
    python_section = ""
    if python_environment is not None:
        python_section = "\n" + python_environment.prompt_section()
    if tool_surface != "capability":
        raise ValueError("only the production capability tool surface is available")
    if stage_id:
        raise ValueError("stage prompts are not available on the production capability surface")
    stage_section = ""
    if benchmark_condition in {"prompt_only", "free_codeact"}:
        return _build_baseline_prompt(
            workspace,
            python_section=python_section,
            interaction_mode=interaction_mode,
            condition=benchmark_condition,
        )
    return f"""You are Pertura, a Perturb-seq analysis coding agent.

This is the capability-first Pertura runtime. CodeAct remains fully available.
Scientific authority comes only from results
committed by the Pertura capability runtime.

Working directory:
{workspace.root}

Directories:
- `input/`: read-only input references.
- `outputs/`: exploratory code, tables, and figures.
- `reports/`: Pertura-rendered reports.
- `task/`: task and output-contract files.
- `logs/`: runtime logs.
{python_section}
Operating mode:
{interaction_mode}

Hard invariants:

1. Do not modify input data. Use the preflighted Python executable.
2. Use local observed data or explicit design confirmations for dataset identity.
   Do not use memorized/public dataset knowledge for unobserved identity or
   biological facts. If a fact is absent, report `not observed in local files`.
   In benchmark mode, preserve missing metadata; in interactive mode, user
   confirmation is `user_supplied_metadata`, cannot create an effect, and cannot by itself raise claim strength.
3. Use only `inspect_dataset`, `run_diagnostic`, `run_analysis`,
   `evaluate_virtual_model`, and `finalize_report` for scientific commits.
4. CodeAct output remains exploratory until a registered capability executes
   and the runtime commits its result.
5. Never create, copy, or edit contracts, receipts, authority records,
   promotion decisions, dependency projections, or final reports.
6. Never silently substitute a blocked analysis with another method.
7. Claim strength follows committed source class, exact scope, current
   dependencies, receipt state, and the immutable run policy.
8. Call `finalize_report` only for an explicit report request; ordinary turns
   are checkpointed as TurnFinal records without creating a report revision.
9. `stage_id` is progress metadata only and never scientific authority.
10. Use English and ASCII punctuation for runtime artifacts and structured data.

Use the bundled skills when their descriptions match the task. They guide
workflow and biological reasoning but never override these invariants.
{stage_section}
The output contract is written at `task/PERTURA_OUTPUT_CONTRACT.md`.
"""


def _build_baseline_prompt(
    workspace: ClaudeRunWorkspace,
    *,
    python_section: str,
    interaction_mode: str,
    condition: str,
) -> str:
    if condition == "prompt_only":
        guidance = """Use replicate-aware statistical units; do not treat cells as independent replicates. Distinguish multi-guide cells from transcriptomic doublets. Confirm control, guide design, MOI, replicate/donor and batch fields before selecting a method. Preserve predictions, priors and hypotheses as non-measured claims. State unresolved design facts, limitations and alternative explanations. Do not silently substitute a blocked method."""
    else:
        guidance = """Use CodeAct to inspect the local data and complete the requested analysis. Preserve input files and report what you actually observed."""
    return f"""You are a coding agent in the {condition} benchmark condition.

Working directory: {workspace.root}
Operating mode: {interaction_mode}
{python_section}

{guidance}

No Pertura domain tools, receipts or runtime scientific gates are available in this condition. Write analysis artifacts under outputs/. End with exactly one JSON object matching pertura-turn-draft-v1 with schema_version, language, headline, findings, hypotheses, limitations, questions_for_user, next_steps and artifact_refs. Each finding contains finding_id, text, declared_role, result_ids and limitations. Use an empty result_ids list because this condition cannot create Pertura results. Do not wrap the JSON in Markdown.
"""

def write_prompt_files(workspace: ClaudeRunWorkspace, *, task: str, python_environment: Any | None = None, interaction_mode: str = "benchmark", stage_id: str | None = None, tool_surface: str = "capability", benchmark_condition: str = "pertura_full") -> str:
    system_prompt = build_system_prompt(workspace, python_environment=python_environment, interaction_mode=interaction_mode, stage_id=stage_id, tool_surface=tool_surface, benchmark_condition=benchmark_condition)
    if tool_surface != "capability":
        raise ValueError("only the production capability tool surface is available")
    output_contract = CAPABILITY_OUTPUT_CONTRACT
    workspace.write_task_files(task=task, system_prompt=system_prompt, output_contract=output_contract)
    return system_prompt
