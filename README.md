# Pertura

Pertura is an event-sourced scientific harness for LLM-driven notebook
analysis.

It is designed for autonomous scientific analysis agents that need to explore
data, run code, keep track of attempts, review weak or suspicious results, and
trace conclusions back to the code, parameters, artifacts, and observations
that produced them.

> Status: alpha research software. The core harness and reviewer checks run
> locally. Real Perturb-seq deployments should validate server dependencies,
> API keys, Docker policy, and small real-data smoke tests first.

## What It Provides

- Editable analysis graphs instead of a hard-coded pipeline.
- Typed capability contracts instead of exposing raw tools as the scientific
  API.
- Event-sourced run state for replay, fork, diff, and audit.
- Observation memory for variable-level scientific provenance.
- Trace-driven rethinking for failed, stale, weak, negative, or suspicious
  results.
- A Perturb-seq reference domain pack that starts from matrix-level data.

Pertura is not an agent framework or a workflow engine. It sits below an LLM
agent and gives it a durable scientific workbench: what stage it is in, what it
is allowed to do, what evidence it has produced, what remains unresolved, and
where to trace when something looks wrong.

## Install

```bash
pip install -e ".[review]"
pertura doctor
```

Optional extras:

```bash
pip install -e ".[server]"      # FastAPI GUI/API
pip install -e ".[perturbseq]"  # scanpy/anndata scientific stack
pip install -e ".[all]"         # all optional integrations
```

For clean wheel/server smoke tests, see [INSTALL.md](INSTALL.md).

## Quickstart: Perturb-seq

Initialize a project:

```bash
pertura init .
```

Inspect the built-in Perturb-seq domain:

```bash
pertura domain inspect --domain perturbseq
pertura domain capabilities --domain perturbseq --node effect_exploration
pertura spec audit --domain perturbseq --json
```

Run an analysis:

```bash
pertura run ./data --goal "Analyze this perturb-seq dataset"
```

Start the local workbench:

```bash
pertura serve
```

The built-in Perturb-seq pack includes nodes for workspace inspection,
experimental design, scRNA-seq QC, guide assignment, perturbation validation,
target QC, state reference, effect exploration, target discovery, biology
story, and reporting.

## Core Concepts

| Concept | Meaning |
| --- | --- |
| `AnalysisGraph` | User-editable analysis nodes, transitions, and gates. |
| `Capability` | Domain action contract exposed to the LLM, such as `run_de`. |
| `Tool` | Runtime primitive below capabilities, such as `execute_code`. |
| `Design` | Run-level experimental facts with source/provenance. |
| `Condition` | Executable or rubric-only check over state, design, artifacts, or observations. |
| `Observation` | Variable-level scientific memory, such as a target logFC under a contrast. |

The public authoring API is intentionally small:

```text
AnalysisGraph  -> what stages exist and what must be true
Capability     -> what actions the LLM may take and what outputs they owe
Domain         -> reusable pack of graph + capabilities + rubrics
```

## Author A Domain

```python
import pertura as pt
from pertura import conditions as c

graph = (
    pt.AnalysisGraph("my_singlecell", start_node_id="inspect")
    .node("inspect")
    .title("Inspect workspace")
    .goal("Find matrix inputs and summarize schema.")
    .use(pt.caps.inspect_workspace, pt.caps.load_dataset)
    .done_when(c.workspace_files_available())
    .next("design", strict=True)
    .end()
)

graph.node("design").title("Resolve design").goal(
    "Resolve controls before interpretation."
).enter_if(
    c.workspace_files_available()
).use(
    pt.caps.inspect_schema, pt.caps.audit_controls
).done_when(
    c.design_confirmed("control_labels")
).next("effect")

graph.node("effect").title("Effect exploration").goal(
    "Run bounded differential expression."
).enter_if(
    c.design_confirmed("control_labels")
).use(
    pt.caps.run_de
).done_when(
    c.observation_metric("logFC")
)

domain = (
    pt.Domain(name="my_singlecell")
    .with_graph(graph)
    .add_capability(
        pt.caps.run_de,
        description="Run bounded differential expression.",
        expected_artifacts=["de_result"],
        expected_observations=["logFC", "p_value"],
        required_inputs=["dataset", "control_labels"],
    )
    .add_rubric("Do not interpret target effects before controls are confirmed.")
)

assert domain.audit()["ok"]
domain.to_json(".pertura/domain.json")
```

`pt.caps.*` entries are typed public references for autocomplete and early
auditing. Serialized domain files still store plain capability ids such as
`"run_de"`, so domain packs remain portable and editable.

For a complete authoring guide, see [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).

## Inspect A Run

```bash
pertura context runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura audit runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura trace runs/run_YYYYMMDD_HHMMSS_xxxxxx obs_123 --json
pertura rethink runs/run_YYYYMMDD_HHMMSS_xxxxxx con_123 --issue "stale support" --json
pertura capsule runs/run_YYYYMMDD_HHMMSS_xxxxxx --verify --json
```

For replay, trace, evidence, capsule, and GUI/API details, see
[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Repository Layout

```text
pertura/              runtime, public APIs, domain packs, tools
examples/             minimal public API examples
tests/                script harness, pytest wrapper, claim tests
DEVELOPER_GUIDE.md    domain/capability authoring
OPERATOR_GUIDE.md     audit, replay, trace, capsule commands
INSTALL.md            clean install and server smoke
CLAIMS.md             paper-claim verification
```

## Verification

```bash
python -m pytest
python tests/test_harness.py
pertura claims --json
python -m pertura.claim_tests --json
```

The current harness-level test suite covers event replay, graph derivation,
context views, analysis-node gates, capability contracts, observation memory,
audit/rethinking tools, capsule integrity, CLI helpers, and public API examples.

## Current Limitations

- Alpha software; APIs may still change.
- Docker sandbox policy is available as an option, but the final bio image is
  not pinned.
- Large real-data Perturb-seq performance depends on the server environment and
  installed scientific stack.
- The Perturb-seq pack is a reference domain, not a complete replacement for
  expert review.
- Web research and external-read tools should be enabled through explicit run
  policy.

## License

MIT. See [LICENSE](LICENSE).
