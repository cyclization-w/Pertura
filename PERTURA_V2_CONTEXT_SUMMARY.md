# Pertura v2 Context Summary

This document summarizes the current Pertura v2 direction, implementation state,
architecture decisions, and comparisons with related systems. It is intended as
a handoff note for continuing the project in a new conversation.

## 1. Core Positioning

Pertura is not primarily a single-cell analysis agent. It is an autonomous
scientific analysis harness. Perturb-seq is the first serious domain pack and
stress-test domain.

The central architecture claim is:

```text
free reasoning, gated commit, typed scientific memory
```

The LLM can freely explore, write code, inspect artifacts, branch, backtrack,
and propose next actions. However, durable scientific state is not owned by the
LLM. State changes must pass through the harness:

```text
LLM/tool proposal
        |
GraphController / gate / policy / contract validation
        |
append-only event store
        |
snapshot, graph, context, report projections
```

## 2. Current Architecture

```text
CLI / FastAPI / Workbench UI
        |
LLM Tool Loop
        |
GraphController + SQLite Event Store
        |
AnalysisGraph + Capability Contract
        |
Observation Memory + Trace/Rethink
        |
Domain Pack, first: Perturb-seq
```

Main public concepts:

| Concept | Role |
| --- | --- |
| `AnalysisGraph` | User-authored analysis nodes, transitions, and gates. |
| `Capability` | Domain action contract exposed to the LLM, such as `run_de`. |
| `Tool` | Core runtime primitive below capabilities, such as `execute_code`. |
| `Domain` | Public domain pack API: graph + capabilities + rubric + context. |
| `Design` | Run-level experimental facts with provenance/source. |
| `Condition` | Executable or rubric-only check over run state. |
| `Observation` | Variable-level scientific memory. |
| `GraphController` | Only official scientific state writer. |
| `ContextView` | Bounded LLM context; never full event log/full graph/full notebook. |
| `Trace/Rethink` | Derivation tracing and repair/branch/reinterpretation planning. |

## 3. Relation To Pertura v1 / Claim Gate

Pertura v1 focused on graph-enforced PI gating at phase boundaries. It was
valuable, but too rigid and too boundary-oriented.

Pertura v2 retains the important safety idea but changes the mechanism:

- Gates remain one mechanism, especially for PI-authority questions.
- The default safety loop is critic/audit/trace/rethink, not constant human
  interruption.
- Phase-internal analysis errors are now first-class:
  - wrong contrast
  - batch confounding
  - unsupported conclusion
  - weak or empty result
  - stale upstream design dependency
  - low coverage or guide discordance

HumanInterrupt is reserved for authority-bound issues, such as ambiguous
experimental design, web policy approval, or exhausted autonomous repair budget.

## 4. Relation To ActiveGraph

We borrow architecture primitives from ActiveGraph but do not depend on the
ActiveGraph runtime.

Borrowed ideas:

- event log as source of truth
- graph/context/report as projections
- behavior/patch/view/replay/fork/diff style runtime primitives
- LLM proposes changes rather than owning state

Pertura differs by being science-specific:

- The graph is not merely a workflow or file dependency DAG.
- It stores typed scientific memory:
  - target/gene
  - metric
  - value
  - contrast
  - method
  - parameters
  - artifact id
  - attempt id
  - branch id
- This supports scientific RAG through structured retrieval rather than text
  embedding alone.
- Observation-level memory enables contradiction detection, evidence coverage,
  stale dependency awareness, and trace-driven rethinking.

## 5. Relation To STAT And Other Single-Cell Agents

STAT and similar systems are domain agents. They are useful references for
workflow design and product experience, especially:

- clearer user-facing task workflow
- domain-specific GUI affordances
- orchestrator plus specialist/tool pattern
- practical run initialization and result presentation

Pertura should not copy STAT's spatial domain or viewer. Pertura's contribution
is the harness:

- event-sourced scientific state
- gated state mutation
- variable-level observation memory
- derivation trace
- replay/fork/diff/capsule
- public `AnalysisGraph` + `Capability` + `Domain` API
- user-authored domain packs

Perturb-seq remains the target domain for the first serious evaluation.

## 6. Current Implementation State

### Harness Core

Implemented:

- SQLite event store
- GraphController
- event schema validation
- append-only scientific state
- snapshot and graph projection
- replay, fork, diff
- run capsule and integrity hash
- audit, evidence review, trace, rethink
- persistent jobs and cooperative cancellation
- behavior lifecycle events
- structured mutation/replay errors

### AnalysisGraph / Gate

Implemented:

- public fluent API
- node transition
- active-node constraint
- `requires`, `completion`, `must_confirm`
- C-tier HumanInterrupt
- completion gate
- skip node
- branch support
- source-aware design fields
- hard completion failure blocks node completion
- `execute_code` must declare a selected capability when the active node has
  allowed capabilities

### Capability / Tool Surface

Implemented:

- core runtime tools separated from domain capabilities
- domain capabilities are the LLM's scientific action menu
- capability contract fields:
  - required inputs
  - expected artifacts
  - expected observations
  - permission tier
  - backend hint
  - runtime estimate
- readonly tool schema excludes `execute_code`, web, and VLM tools
- permission tiers:
  - `local_read`
  - `external_read`
  - `execute`
  - `state_change`
  - `privileged`

### LLM Context

Implemented:

- `ContextView` / context envelope
- no full event log, full graph, or full notebook in prompts
- active node and node contract
- reachable nodes
- allowed capabilities
- missing inputs
- recent attempts
- runtime symbols
- observation memory
- stale/conflict/audit preview
- trace-driven rethinking preview
- budget summary

### Perturb-seq Domain

Default analysis graph includes:

- workspace inspection
- experimental design
- scRNA QC
- guide assignment
- perturbation validation
- target QC
- state reference
- effect exploration
- target discovery
- biology story
- report

Initial Perturb-seq capabilities include:

- `experimental_design_audit`
- `scRNA_qc_summary`
- `guide_assignment_audit`
- `perturbation_validation`
- `target_coverage_check`
- `guide_concordance_check`
- `batch_condition_audit`
- `contrast_audit`
- `run_de`
- `module_scoring`
- `composition_shift`
- `target_similarity`
- `report_assembly`

## 7. Current CLI / GUI Usage

Local GUI:

```bash
pertura --GUI --domain perturbseq
```

Server GUI:

```bash
pertura --GUI --domain perturbseq --host 0.0.0.0
```

DeepSeek or another OpenAI-compatible endpoint:

```bash
export OPENAI_API_KEY="..."
pertura --GUI --domain perturbseq \
  --provider openai \
  --base-url https://api.deepseek.com \
  --model deepseek-v4-flash
```

PowerShell:

```powershell
$env:OPENAI_API_KEY="..."
pertura --GUI --domain perturbseq --provider openai --base-url https://api.deepseek.com --model deepseek-v4-flash
```

In this CLI, `provider=openai` means "use the OpenAI-compatible API adapter."
It is the correct setting for DeepSeek-style compatible endpoints.

## 8. Current Test State

Recently verified:

```text
python -m compileall pertura
python tests/test_harness.py    # 479/479 passed
python -m pytest -q             # 3 passed
```

## 9. Current GitHub State

Repository:

```text
https://github.com/cyclization-w/Pertura
```

Local path:

```text
C:\Users\25374\Documents\New project\pertura_v2
```

Current pending changes at the time this summary was written:

- `pertura/_cli.py`
- `README.md`
- `OPERATOR_GUIDE.md`
- `tests/test_harness.py`
- this file, `PERTURA_V2_CONTEXT_SUMMARY.md`

Recommended commit:

```bash
git add README.md OPERATOR_GUIDE.md pertura/_cli.py tests/test_harness.py PERTURA_V2_CONTEXT_SUMMARY.md
git commit -m "Support OpenAI-compatible GUI endpoints"
git push origin main
```

If remote has advanced:

```bash
git pull --rebase origin main
git push origin main
```

## 10. Remaining Priorities

### P0: Norman Dataset Server Test

Run a real Perturb-seq dataset through:

```text
workspace inspection
-> experimental design
-> scRNA QC
-> guide assignment
-> perturbation validation
-> effect exploration
-> report
```

Main thing to observe: whether the LLM naturally uses AnalysisGraph,
Capability, node contract, context review, and rethink tools without getting
lost.

### P1: Docker Sandbox / Long-Task Execution

Jupyter kernel is a convenience backend, not a security boundary.

Heavy jobs such as UMAP, Harmony, or `rank_genes_groups` should move toward:

```text
submit_job
-> write script under run directory
-> subprocess/docker backend
-> stream logs
-> register manifest/artifacts/observations
-> retry/cancel/extend
```

Needed:

- resource limits
- read-only workspace mount
- writable run directory
- network policy
- timeout/heartbeat/log handling
- manifest registration

### P2: Perturb-seq Capability Depth

Further work should mostly add domain capabilities and observation schemas, not
change the harness core.

Highest-value additions:

- stronger guide assignment audit
- contrast audit
- target coverage
- guide concordance
- batch-condition confounding checks
- DE result schema contract
- module score contract
- composition shift contract
- report assembly contract

### P3: Product UI

Current UI has built-in HTML plus React scaffold. The next product-oriented
improvements are:

- active node dashboard
- capability browser
- node contract panel
- runtime event cards
- job log panel
- artifact preview
- derivation lanes
- interrupt/resolution panel

## 11. Model / Tool Comparison

### Codex / Current Model

Best suited for:

- codebase-scale refactoring and consistency
- CLI/API/test changes together
- keeping architecture coherent over long implementation sessions
- turning conceptual architecture into maintained code

Main contribution in this phase:

- consolidated blackboard into Pertura v2
- clarified harness vs single-cell-agent distinction
- hardened GraphController, ContextView, Capability, and UI contracts
- kept changes covered by regression tests

### Claude Code

Best suited for:

- aggressive code review
- surfacing API usability problems
- finding mismatches in gate/capability/domain semantics
- challenging overly loose scaffold logic

Important critiques absorbed:

- capabilities, tools, and domain concepts were too mixed
- C-tier condition placement was inconsistent
- natural-language conditions should not rely on runtime keyword matching
- design fields need source/provenance
- stale upstream dependency must be visible
- completion gaps must be visible to the LLM before failed completion

### ActiveGraph

Best reference for:

- event-sourced graph runtime primitives
- behaviors
- patches
- views
- replay/fork/diff
- recorded fixture provider

Not directly adopted because:

- it is a general graph runtime
- Pertura needs scientific observation memory, domain capability contracts,
  PI/design source, and variable-level derivation tracing

### STAT

Best reference for:

- domain-oriented product flow
- practical GUI start and interaction patterns
- orchestrator plus specialist/tool product framing
- showing biological users a clearer task path

Not copied because:

- Pertura is not a spatial viewer
- Pertura's main claim is the reusable harness
- Perturb-seq is the first domain pack

## 12. Short Handoff Prompt

Use this to start a new conversation:

```text
We are working on Pertura v2, an autonomous scientific analysis harness. It is
not just a single-cell agent. The core architecture is free reasoning, gated
commit, and typed scientific memory: AnalysisGraph + Capability Contract +
GraphController + SQLite event store + Observation Memory + Trace/Rethink.
Perturb-seq is the first serious domain pack.

Current repo: C:\Users\25374\Documents\New project\pertura_v2
GitHub: https://github.com/cyclization-w/Pertura

Please preserve the architecture: LLMs can reason freely and call tools, but
durable scientific state must pass through GraphController/gates/contracts.
ContextView is the LLM context boundary. Observation Memory is variable-level
scientific memory. Domain-specific biological logic belongs in the Perturb-seq
Domain pack, not core.

Recent tests passed:
python tests/test_harness.py -> 479/479 passed
python -m pytest -q -> 3 passed

Next priorities: real Norman dataset server test, Docker/subprocess long-task
backend, and deeper Perturb-seq capabilities.
```
