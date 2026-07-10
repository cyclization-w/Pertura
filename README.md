# Pertura

Pertura is a capability-first, execution-grounded Perturb-seq runtime for scientific CodeAct agents.

Claude or another CodeAct agent remains free to inspect files, write Python, run scanpy/pertpy/custom analysis code, and produce intermediate artifacts. Pertura controls the formal scientific conclusion boundary:

```text
free CodeAct exploration
  -> versioned DatasetContract
  -> registered capability
  -> independent verifier process
  -> signed RunReceipt + broker-owned commit store
  -> structured promotion decision
  -> controlled scientific final surface
```

The system is designed to prevent prompt pressure, artifact self-tags, predicted/prior evidence, raw string-scope similarity, or prose-only eligibility from increasing user-visible claim strength.

## Current Status

The `0.2.0a3` capability kernel now implements:

- the neutral Pydantic v2 contracts and JSON Schema surface (`DatasetContract`, `ScopeKey`, capability request/result, receipt, statement, decision and confirmation);
- an independent verifier process with ephemeral in-memory Ed25519 receipts for trusted capabilities, replay protection, an authority SQLite store, explicit dependencies and stale propagation;
- exactly five default Pertura MCP tools while retaining normal Claude CodeAct file/Bash/notebook tools;
- deterministic CSV/TSV, H5AD, MuData and 10x intake inspection with versioned identity confirmations;
- guide barcode/orientation/map checks, two-component negative-binomial guide assignment, ambient/MOI/retained-cell outputs and separate doublet status;
- control-derived PCA/Leiden state-reference and GMT/NMF reference-module capabilities (blocked with an actionable dependency message when the scientific environment is missing);
- target reliability v2 with bootstrap intervals, guide effects, leave-one-guide-out, heterogeneity, signature efficacy and imported Mixscape/Mixscale responder labels;
- a verifier-only edgeR QL pseudobulk runner and explicit Micromamba environment setup/doctor commands;
- a local React/Vite dashboard that is read-only except for design/identity confirmation.
- a frozen v0.2 compatibility surface for schemas, tools, capability permissions, policy, scope and receipt signing;
- a portable benchmark protocol, maintainer CLI, published-proxy provenance boundary and expert annotation packet;
- an edgeR 4.8.2 environment lock plus an independent direct-R scientific golden with zero observed fixture error;
- isolated P4/P5 exploratory contracts and planted leakage tests that cannot enter trusted promotion;
- twenty granular P0-P3 candidate capabilities spanning intake/design, guide assignment/QC, state/module reference, target reliability, SCEPTRE, Propeller, sensitivity and method-null calibration;
- a capability benchmark matrix with six deterministic local protocol cases per candidate and a scheduler-neutral server benchmark plan.

The twenty additions are bundled exploratory candidates (version 0.1.0,
synthetic_only, no claim permissions). They are committed without a trusted
receipt so downstream candidate analyses can depend on them, but they cannot
support strong measured statements before real-data server benchmarks.

The former evidence lattice remains as a deprecated legacy/regression surface through the P3 migration window:

- P0.6 canonical perturbation scope and manifest UID binding
- P0.7 strong-baseline gate utility harness
- P1.1 perturbation efficiency / measured target engagement
- P1.2 cell QC as eligibility evidence
- P1.3 curated enrichment, module effect, and global effect evidence paths
- Evidence workflow closure with registrar-provided `next_claim_template`
- P2.0 workflow substrate: preflight, candidate harvest, next-evidence recommendation
- P2.1 core: internal family registrar API and classic recipe strict structured path
- Runtime trust spine: immutable run policy, protected registry/ledger/final surface, ledger-backed calibration
- Product vertical slice: target reliability audit and design-aware method/virtual-scope routing

Latest local full test baseline is recorded by CI and `python -m pytest -q`; do not use the historical count below as a release gate.

```text
See the current test run in the release handoff.
```

## Repository Layout

```text
src/pertura_gate/       Trusted deterministic gate core
src/pertura_core/       Runtime-neutral canonical contracts and receipt verification
src/pertura_workflow/   Bounded evidence-acquisition workflow substrate
src/pertura_runtime/    Claude/agent runtime adapter and MCP tool surface
src/pertura_bench/      Benchmark harness and surface evaluator

tests/gate/             Gate and identity tests
tests/runtime/          Claude runtime and MCP tests
tests/bench/            P0.7 benchmark/evaluator tests
tests/workflow/         Workflow substrate and CLI tests
scripts/                Smoke and benchmark helper scripts
docs/                   Architecture docs, smoke tasks, skill cards
```

Architecture invariant:

```text
pertura_workflow -> pertura_core + pertura_gate
pertura_runtime  -> pertura_core + pertura_gate + pertura_workflow
pertura_bench    -> pertura_core + pertura_gate + maintainer-only runtime/workflow adapters
pertura_gate     -> neither pertura_workflow, pertura_runtime, nor pertura_bench
```

`pertura_gate` does not import workflow orchestration, the Claude runtime, or the benchmark surface evaluator. This keeps the trusted gate separate from bounded evidence-acquisition workflows, untrusted agent execution, and benchmark-only lexical checks.

Start reading here:

- [docs/01_system_overview.md](docs/01_system_overview.md)
- [docs/02_architecture.md](docs/02_architecture.md)
- [docs/03_evidence_lattice.md](docs/03_evidence_lattice.md)
- [docs/08_smoke_and_benchmark_results.md](docs/08_smoke_and_benchmark_results.md)
- [docs/13_product_pivot.md](docs/13_product_pivot.md)
- [docs/results/p0_p1_experiment_summary.md](docs/results/p0_p1_experiment_summary.md)
- [docs/results/p1_freeze_summary.md](docs/results/p1_freeze_summary.md)

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

## Reproduce Current Results

Generate the frozen P1 result tables and run the deterministic test suite:

```bash
python scripts/freeze_p1_results.py
python -m pytest -q
```

The expected test baseline for this cleaned repo is:

```text
124 passed
```

## Runtime Command

Capability product CLI:

```bash
pertura env doctor edger-v1
pertura capabilities list
pertura inspect <workspace>
pertura diagnostic <capability_id> <workspace>
pertura analyze <objective> <workspace>
pertura evaluate-virtual <workspace>
pertura finalize <run_id> --workspace <project>
pertura dashboard <workspace> --run <run_id>
```

`preflight` forwards to `inspect` on the product CLI. Frozen commands such as
`recipe classic` require `--tool-surface legacy`.

Claude CodeAct runtime:

```bash
pertura-claude --help
```

Benchmark maintenance does not add MCP tools and never downloads data implicitly:

```bash
python -m pertura_bench validate --repo .
python -m pertura_bench status --repo .
python -m pertura_bench annotation-packet --modality crispri
python -m pertura_bench capabilities matrix
python -m pertura_bench validate-cases
python -m pertura_bench export-server-plan --output server-plan.json
```

A typical smoke command is documented in [docs/smoke_tasks/README.md](docs/smoke_tasks/README.md).

## Boundary

Pertura provides bounded evidence-acquisition workflows for Perturb-seq: it can run or harvest minimal analyses needed for claim calibration, while every user-visible scientific conclusion remains controlled by the evidence gate. It is not a replacement for full Scanpy, Seurat, Cell Ranger, SCEPTRE, Milo, CellOracle, or virtual-perturbation pipelines. It now ships exploratory adapters for Pertpy Mixscape, SCEPTRE 0.99.0 and speckle/Propeller 1.10.0, but these remain synthetic-only candidates until the server benchmark. It does not ship trusted runners for g:Profiler, Milo/scCODA, CellOracle, scGPT, GEARS, arbitrary Seurat RDS, or Cell Ranger execution. Agents may still use those tools in normal CodeAct. Pertura registers their structured outputs and controls what scientific claims those outputs can support.

`validated_mechanism` is intentionally disabled in the current policy unless future evidence types such as rescue assays, orthogonal validation, time-course causality, epistasis, protein validation, or reporter assays are added.

