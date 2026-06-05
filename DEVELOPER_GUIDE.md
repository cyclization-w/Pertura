# Pertura Developer Guide

This guide is for people writing reusable scientific domain packs.

## Public Objects

Use only three public concepts for normal extension work:

```text
AnalysisGraph  stage/node contract
Capability     action/output contract
Domain         graph + capabilities + rubrics
```

Core runtime tools such as `execute_code`, `get_context_review`, and
`trace_upstream` exist below this API. They are implementation primitives. A
domain author should normally expose a `Capability` such as `run_de`, not a raw
tool name.

## Fixed Kernel vs Domain Surface

Pertura has two authoring layers:

```text
Core runtime
  fixed by Pertura
  tools, event store, graph projection, context views, audit/replay/rethink

Domain surface
  written by a domain author or user
  analysis graph, capabilities, rubrics, condition placement, design vocabulary
```

Use this rule of thumb:

| Object | Can domain authors write it? | Where it belongs |
| --- | --- | --- |
| `Tool` | Usually no | Pertura core runtime. |
| `Capability` | Yes | Domain pack. |
| `AnalysisNode` | Yes | `AnalysisGraph`. |
| `Condition` placement | Yes | Node `enter_if`, `confirm`, or `done_when`. |
| `Condition` evaluator implementation | Sometimes | Core or domain extension code. |
| `Design` field vocabulary | Yes | Domain pack and run schema. |
| `Design` field value | During a run | PI/user/API/data/LLM-sourced run state. |

For example, `execute_code` is a core tool. `run_de` is a capability. A
Perturb-seq node can allow `run_de`; the runtime may implement that capability
through `execute_code`, templates, validators, and audit hooks.

Internal runtime objects such as `Store`, `GraphController`, events,
snapshots, and ContextViews are implementation details unless you are changing
the harness itself.

## AnalysisGraph

Write analysis nodes with the fluent API:

```python
from pertura import AnalysisGraph, conditions as c
from pertura.domain import perturbseq as ps

graph = (
    AnalysisGraph("my_domain", start_node_id="inspect")
    .node("inspect")
    .title("Inspect workspace")
    .goal("Find input matrices and summarize schema.")
    .use(ps.caps.inspect_workspace, ps.caps.load_dataset)
    .done_when(c.workspace_files_available())
    .next("design", strict=True)
    .end()
)
```

Guidance:

- `goal()` is the natural-language purpose shown to the LLM.
- `use()` lists capabilities, not concrete package/tool names. Prefer
  domain refs such as `pertura.domain.perturbseq.caps.run_de`; serialized specs
  still store stable ids such as `"run_de"`.
- `enter_if()` is for node entry prerequisites.
- `confirm()` is for C-tier user/PI authority checks.
- `done_when()` is for completion checks.
- `next(..., strict=True)` restricts transitions; otherwise the LLM may move
  among reachable nodes when gates pass.

Natural-language conditions are allowed, but executable helpers from
`pertura.conditions` are preferred. Build-time condition compilation can map
some prose conditions into executable checks and reports rubric-only leftovers.

## Capability

Capabilities are the LLM's action menu. They sit above concrete tools and
templates.

```python
domain.add_capability(
    ps.caps.run_de,
    description="Run bounded differential expression.",
    expected_artifacts=["de_result"],
    expected_observations=["logFC", "p_value"],
    required_inputs=["adata", "control_labels", "target_column"],
)
```

Minimum useful fields:

- `description`
- `required_inputs`
- `expected_artifacts`
- `expected_observations`
- optional `packages`, `functions`, `analysis_modes`, `risk`, `backend`

The harness blocks execution when a node allows capabilities but the LLM does
not declare the selected capability.

Browse capability contracts before running:

```bash
pertura domain capabilities --domain perturbseq
pertura domain capabilities --domain perturbseq --node effect_exploration
```

Inspect core runtime tools separately:

```bash
pertura domain tools
```

## Domain

`Domain` is the public domain-pack object:

```python
domain = (
    Domain(name="my_domain")
    .with_graph(graph)
    .add_capability(ps.caps.inspect_workspace, description="Inspect files.")
    .add_capability(ps.caps.run_de, expected_observations=["logFC", "p_value"])
    .add_rubric("Do not report target-level effects before controls are resolved.")
)

assert domain.audit()["ok"]
domain.to_json(".pertura/domain.json")
```

Useful methods:

- `with_graph(graph_or_spec)`
- `add_capability(capability_ref_or_id, **contract_fields)`
- `add_rubric(text, critic=False)`
- `registry()`
- `audit()`
- `describe()` for CLI/GUI-ready node, capability, design, condition, and tool
  browser payloads
- `to_json(path)` / `from_json(path)`
- `runtime_context()` for advanced runtime integration

Legacy fields such as `protocol`, `tools`, and `coding_guidelines` are still
loaded for compatibility, but new domains should prefer graph, capabilities,
rubrics, and condition context.

## Validation

Before sharing a domain pack:

```bash
pertura domain inspect --domain perturbseq
pertura spec audit .pertura/analysis_graph.json --domain .pertura/domain.json --json
pertura spec contract .pertura/analysis_graph.json --domain .pertura/domain.json --node inspect --json
python examples/analysis_node_quickstart.py
```
