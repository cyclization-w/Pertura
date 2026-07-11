# Pertura

Pertura is a capability-first Perturb-seq runtime for scientific CodeAct agents. Claude remains free to inspect data, write code, and use Bash, Python, R, or notebooks; Pertura controls which executed results may enter a user-visible scientific claim.

```text
DatasetContract
  -> capability planner and dependency resolver
  -> controlled capability execution
  -> committed ResultEnvelope
  -> receipt/session verification
  -> promotion decision
  -> report
```

## Current checkpoint

`0.2.0a5` is the provider-neutral agent-skills checkpoint, not the final `0.2.0` scientific release.

The current product surface provides:

- exactly five Pertura MCP tools: `inspect_dataset`, `run_diagnostic`, `run_analysis`, `evaluate_virtual_model`, and `finalize_report`;
- frozen v0.2 contracts for dataset identity, scope, results, receipts, statements, and promotion decisions;
- a single capability registry and planner spanning P0-P3 intake, guide assignment/QC, state and module reference, target reliability, effect estimation, and calibration;
- persistent authority-session records for validating results across separate CLI processes while keeping signing keys ephemeral;
- trusted receipt-gated promotion and a separate exploratory-results report section;
- twenty granular exploratory P0-P3 candidate capabilities with synthetic-only validation and no claim permissions;
- PerturaBench case specifications, synthetic execution, portable artifact locks, and scheduler-neutral server plans;
- four provider-neutral Perturb-seq skills bundled once and loaded by the Claude adapter without reducing CodeAct access;
- an import-safe OpenAI Agents SDK adapter contract that reuses the same five tool schemas but is deliberately not runnable yet;
- a read-mostly React/Vite dashboard whose only write operation confirms dataset design or identity.

Real-data benchmark locks, verdicts, and expert-adjudicated CRISPRi/CRISPRa profiles remain required before `release_ready` can become true.

## Install

Python 3.10 or later is required.

```bash
python -m venv .venv
pip install -e ".[dev]"
```

Install optional surfaces only when needed:

```bash
pip install -e ".[llm,omics,perturbseq,dashboard]"
```

Scientific R environments are supplied explicitly and are never installed during analysis:

```bash
pertura env setup edger-v1
pertura env doctor edger-v1
```

## Product commands

```bash
pertura capabilities list
pertura inspect <workspace>
pertura diagnostic <capability_id> <workspace>
pertura analyze <objective> <workspace>
pertura evaluate-virtual <workspace>
pertura finalize <run_id> --workspace <workspace>
pertura dashboard <workspace> --run <run_id>
```

Claude CodeAct uses the same product runtime. The four bundled Pertura skills are enabled by default; additional local skill plugins require an explicit path:

```bash
pertura-claude --help
pertura-claude --no-bundled-skills
pertura-claude --skill-plugin <plugin-root>
```

Skills guide tool selection and scientific reasoning only. They cannot create contracts, receipts, promotion decisions, or measured results. The OpenAI adapter currently provides schema and instruction projection for future Agents SDK integration; it does not expose a CLI or make API requests.

Legacy registrars, stages, and classic recipes are regression-only internals and are not part of the production CLI or MCP surface.

## Benchmark maintenance

Benchmark commands never download data implicitly:

```bash
python -m pertura_bench validate --repo .
python -m pertura_bench validate-cases
python -m pertura_bench skills validate --repo .
python -m pertura_bench run-matrix --tier synthetic_ci
python -m pertura_bench export-server-plan --output server-plan.json
```

Large datasets, converted subsets, local environments, and cache paths remain outside Git. Server verdicts must bind the Git commit, wheel hash, case-spec hash, environment lock, and dataset artifact locks.

## Repository boundaries

```text
pertura_core       frozen contracts, scope, policy, receipt verification
pertura_workflow   capabilities, scientific runners, planner
pertura_runtime    five-tool product runtime, Claude adapter, dashboard
pertura_bench      maintainer-only fixtures, locks, metrics, server plans
pertura_gate       deprecated read-only compatibility and regression surface
```

The default product import path must not load the legacy orchestration spine.

## Validate a checkout

```bash
python -m pytest -q
python scripts/check_version_sync.py
python scripts/freeze_v020_contracts.py --check
python -m pertura_bench validate-cases
python -m pertura_bench skills validate --repo .
```

The repository does not encode a historical test count as a release gate. CI and the release audit are the source of truth for the current checkout.

Start with [the documentation index](docs/README.md), then read the [capability-first architecture](docs/14_capability_first_product_architecture.md), [implementation status](docs/15_v020_implementation_status.md), and [benchmark protocol](docs/benchmark_design.md).
