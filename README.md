# Pertura v2

Pertura is a scientific analysis harness for LLM-driven notebooks.

> Status: alpha research artifact. The core harness and reviewer checks run
> locally; real Perturb-seq deployments should validate server dependencies,
> API keys, Docker policy, and small real-data smoke tests first.

It lets an LLM reason freely, but keeps durable scientific state behind a
small set of explicit contracts:

```text
AnalysisGraph  -> what stages exist and what must be true
Capability     -> what actions the LLM may take and what outputs they owe
Domain         -> a reusable pack of graph + capabilities + rubrics
```

The runtime records attempts, artifacts, observations, branches, findings, and
human interventions in an event-sourced graph. Compact ContextViews, evidence
audit, observation memory, and trace-driven rethinking keep the LLM oriented
without loading the full run history into the prompt.

## Why Pertura

- User-editable analysis graph instead of a hard-coded pipeline.
- Capability contracts instead of exposing raw tools as the scientific API.
- Observation memory for variable-level scientific provenance.
- Evidence-chain audit and trace-driven rethinking for failed, stale, weak, or
  suspicious results.
- Perturb-seq reference domain pack, with the core harness kept reusable for
  other single-cell domains.

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

## Install

```bash
pip install -e ".[review]"
pertura doctor
```

Useful extras:

```bash
pip install -e ".[server]"      # FastAPI GUI/API
pip install -e ".[perturbseq]"  # scanpy/anndata scientific stack
pip install -e ".[all]"         # all optional integrations
```

For clean wheel/server smoke tests, see [INSTALL.md](INSTALL.md).

## Perturb-seq Quickstart

```bash
pertura init .
pertura spec audit --domain perturbseq --json
pertura spec contract --domain perturbseq --node effect_exploration --json
pertura run ./data --goal "Analyze this perturb-seq dataset"
pertura serve
```

The built-in Perturb-seq pack starts from matrix-level data and includes nodes
for workspace inspection, experimental design, scRNA-seq QC, guide assignment,
perturbation validation, target QC, state reference, effect exploration, target
discovery, biology story, and reporting.

## Author A Domain

```python
import pertura as pt
from pertura import conditions as c

graph = (
    pt.AnalysisGraph("my_singlecell", start_node_id="inspect")
    .node("inspect")
    .title("Inspect workspace")
    .goal("Find matrix inputs and summarize schema.")
    .use("inspect_workspace", "load_dataset")
    .done_when(c.workspace_files_available())
    .next("design", strict=True)
    .end()
)

graph.node("design").title("Resolve design").goal(
    "Resolve controls before interpretation."
).enter_if(
    c.workspace_files_available()
).use(
    "inspect_schema", "audit_controls"
).done_when(
    c.design_confirmed("control_labels")
).next("effect")

graph.node("effect").title("Effect exploration").goal(
    "Run bounded differential expression."
).enter_if(
    c.design_confirmed("control_labels")
).use(
    "run_de"
).done_when(
    c.observation_metric("logFC")
)

domain = (
    pt.Domain(name="my_singlecell")
    .with_graph(graph)
    .add_capability(
        "run_de",
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

For a complete authoring guide, see [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).

## Minimal Operator Commands

```bash
pertura context runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura audit runs/run_YYYYMMDD_HHMMSS_xxxxxx --json
pertura rethink runs/run_YYYYMMDD_HHMMSS_xxxxxx con_123 --issue "stale support" --json
pertura capsule runs/run_YYYYMMDD_HHMMSS_xxxxxx --verify --json
```

For replay, trace, evidence, capsule, and GUI/API details, see
[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Reviewer Checks

```bash
python -m pytest
python tests/test_harness.py
pertura claims --json
python -m pertura.claim_tests --json
```

For paper-claim verification and capsule checks, see [CLAIMS.md](CLAIMS.md).

## License

MIT. See [LICENSE](LICENSE).
