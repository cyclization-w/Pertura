# Pertura Developer Guide

This guide is for people writing reusable scientific domain packs.

## Public Objects

Use only three public concepts for normal extension work:

```text
AnalysisGraph  stage/node contract
Capability     action/output contract
Domain         graph + capabilities + rubrics
```

Internal runtime objects such as `Store`, `GraphController`, events,
snapshots, and ContextViews are implementation details unless you are changing
the harness itself.

## AnalysisGraph

Write analysis nodes with the fluent API:

```python
from pertura import AnalysisGraph, conditions as c

graph = (
    AnalysisGraph("my_domain", start_node_id="inspect")
    .node("inspect")
    .title("Inspect workspace")
    .goal("Find input matrices and summarize schema.")
    .use("inspect_workspace", "load_dataset")
    .done_when(c.workspace_files_available())
    .next("design", strict=True)
    .end()
)
```

Guidance:

- `goal()` is the natural-language purpose shown to the LLM.
- `use()` lists capability ids, not concrete package/tool names.
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
    "run_de",
    description="Run bounded differential expression.",
    expected_artifacts=["de_result"],
    expected_observations=["logFC", "p_value"],
    required_inputs=["dataset", "control_labels"],
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

## Domain

`Domain` is the public domain-pack object:

```python
domain = (
    Domain(name="my_domain")
    .with_graph(graph)
    .add_capability("inspect_workspace", description="Inspect files.")
    .add_capability("run_de", expected_observations=["logFC", "p_value"])
    .add_rubric("Do not report target-level effects before controls are resolved.")
)

assert domain.audit()["ok"]
domain.to_json(".pertura/domain.json")
```

Useful methods:

- `with_graph(graph_or_spec)`
- `add_capability(capability_id, **contract_fields)`
- `add_rubric(text, critic=False)`
- `registry()`
- `audit()`
- `to_json(path)` / `from_json(path)`
- `runtime_context()` for advanced runtime integration

Legacy fields such as `protocol`, `tools`, and `coding_guidelines` are still
loaded for compatibility, but new domains should prefer graph, capabilities,
rubrics, and condition context.

## Validation

Before sharing a domain pack:

```bash
pertura spec audit .pertura/analysis_graph.json --domain .pertura/domain.json --json
pertura spec contract .pertura/analysis_graph.json --domain .pertura/domain.json --node inspect --json
python examples/analysis_node_quickstart.py
```
