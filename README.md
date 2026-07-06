# Pertura

Pertura is an execution-grounded evidence and claim-strength runtime for scientific CodeAct agents working on Perturb-seq analysis.

Claude or another CodeAct agent remains free to inspect files, write Python, run scanpy/pertpy/custom analysis code, and produce intermediate artifacts. Pertura controls the formal scientific conclusion boundary:

```text
free CodeAct analysis
  -> runtime-registered evidence artifacts
  -> explicit claims
  -> claim-conditioned resolver
  -> controlled scientific final surface
```

The system is designed to prevent prompt pressure, artifact self-tags, predicted/prior evidence, raw string-scope similarity, or prose-only eligibility from increasing user-visible claim strength.

## Current Status

Implemented for the current submission-oriented evidence lattice:

- P0.6 canonical perturbation scope and manifest UID binding
- P0.7 strong-baseline gate utility harness
- P1.1 perturbation efficiency / measured target engagement
- P1.2 cell QC as eligibility evidence
- P1.3 curated enrichment, module effect, and global effect evidence paths
- Evidence workflow closure with registrar-provided `next_claim_template`

Latest recorded full test result from this cleaned repo:

```text
110 passed
```

## Repository Layout

```text
src/pertura_gate/       Trusted deterministic gate core
src/pertura_runtime/    Claude/agent runtime adapter and MCP tool surface
src/pertura_bench/      Benchmark harness and surface evaluator

tests/gate/             Gate and identity tests
tests/runtime/          Claude runtime and MCP tests
tests/bench/            P0.7 benchmark/evaluator tests
scripts/                Smoke and benchmark helper scripts
docs/                   Architecture docs, smoke tasks, skill cards
```

Architecture invariant:

```text
pertura_runtime -> pertura_gate
pertura_bench   -> pertura_gate
pertura_gate    -> neither pertura_runtime nor pertura_bench
```

`pertura_gate` does not import the Claude runtime, and it does not import the benchmark surface evaluator. This keeps the trusted gate separate from untrusted agent execution and from benchmark-only lexical checks.

Start reading here:

- [docs/01_system_overview.md](docs/01_system_overview.md)
- [docs/02_architecture.md](docs/02_architecture.md)
- [docs/03_evidence_lattice.md](docs/03_evidence_lattice.md)
- [docs/08_smoke_and_benchmark_results.md](docs/08_smoke_and_benchmark_results.md)`r`n- [docs/results/p0_p1_experiment_summary.md](docs/results/p0_p1_experiment_summary.md)

## Install

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev,llm,omics,perturbseq]"
```

For the minimal core package:

```bash
pip install -e ".[dev]"
```

## Run Tests

```bash
python -m pytest -q
```

## Runtime Command

Claude CodeAct runtime:

```bash
pertura-claude --help
```

A typical smoke command is documented in [docs/smoke_tasks/README.md](docs/smoke_tasks/README.md).

## Boundary

Pertura is not a full Perturb-seq pipeline runner. It does not currently ship real runners for Mixscape/Mixscale, g:Profiler, Milo/scCODA, CellOracle, scGPT, GEARS, or Cell Ranger. Agents may use those tools in normal CodeAct. Pertura registers their structured outputs and controls what scientific claims those outputs can support.

`validated_mechanism` is intentionally disabled in the current policy unless future evidence types such as rescue assays, orthogonal validation, time-course causality, epistasis, protein validation, or reporter assays are added.