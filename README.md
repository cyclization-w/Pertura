# Pertura

Pertura is a Perturb-seq native analysis agent and workbench for LLM-driven
notebook analysis.

It is designed for real Perturb-seq analysis sessions: users provide a
workspace and a scientific goal, then watch the agent resolve design facts,
run audited code, surface plots/artifacts, ask targeted questions, and trace
each claim back to the code, parameters, observations, and evidence that
produced it.

Under that product surface, Pertura still uses an event-sourced runtime with
gated commits, replayable state, branchable analysis, observation memory, and
auditable repair. The runtime is the safety layer; the default user experience
is the Perturb-seq analysis console.

> Status: alpha research software. The core harness and reviewer checks run
> locally. Real Perturb-seq deployments should validate server dependencies,
> API keys, Docker policy, and small real-data smoke tests first.

## What It Provides

- A Perturb-seq Analysis Console with a live agent run, Design Ledger,
  Perturb-seq Flow, Evidence Board, plots, artifacts, and report preview.
- Candidate actions and structured questions compiled from the current run
  state, instead of a free-form chat transcript as durable state.
- Editable Perturb-seq analysis graphs instead of a fixed five-step pipeline.
- Typed capability contracts instead of exposing raw tools as the scientific
  API; capability cards carry required design fields, prechecks, expected
  observations/artifacts, common errors, and repair hints.
- Event-sourced run state for replay, fork, diff, and audit.
- Observation memory for variable-level scientific provenance.
- Trace-driven rethinking for failed, stale, weak, negative, or suspicious
  results.
- Audited auto-repair for low-risk code failures, with higher-risk repairs
  routed back to user confirmation.

Pertura is not a general chat app and not a hidden fixed pipeline. The LLM can
still reason and choose actions, but the workbench makes those actions
scientific: every run step is tied to a Perturb-seq stage, capability contract,
gate, artifact, observation, or interrupt.

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
pertura --GUI --domain perturbseq
```

Use an OpenAI-compatible endpoint such as DeepSeek by setting the API key in
the environment and passing the endpoint/model on the GUI command:

```bash
export OPENAI_API_KEY="..."
pertura --GUI --domain perturbseq \
  --provider openai \
  --base-url https://api.deepseek.com \
  --model deepseek-v4-flash
```

In this CLI, `provider=openai` means “use the OpenAI-compatible API adapter.”
It is the correct setting for DeepSeek-style compatible endpoints. Keep API
keys in environment variables rather than command-line flags.

The GUI shell reads a compact UI contract from:

```text
GET /api/workbench-view
```

Use this as the stable first-screen payload for custom frontends. The primary
field is `perturbseq`, which contains the Design Ledger, active stage,
Perturb-seq Flow, ready/blocked capability cards, quality flags, evidence
board, branch board, and product timeline. Runtime/debug projections remain
available under `execution_state`, `analysis`, `review`, and `agent_context`,
but they are no longer the default first-screen model.

The repository also includes an experimental React/Vite frontend in
`frontend/`. Run it against the FastAPI backend:

```bash
# terminal A
pertura --GUI --domain perturbseq --ui auto

# terminal B
cd frontend
npm install
npm run dev
```

Vite proxies `/api/*` to `http://127.0.0.1:8765`.

The built-in Perturb-seq pack includes nodes for workspace inspection,
experimental design, scRNA-seq QC, guide assignment, perturbation validation,
target QC, state reference, effect exploration, target discovery, biology
story, and reporting.

## Core Concepts

| Concept | Meaning |
| --- | --- |
| `PerturbSeqView` | Product projection for the GUI and LLM turn card: design ledger, flow, evidence, capability cards, timeline. |
| `AnalysisGraph` | User-editable analysis nodes, transitions, and gates. |
| `Capability` | Domain action contract exposed to the LLM, such as `run_de`. |
| `Tool` | Runtime primitive below capabilities, such as `execute_code`. |
| `Design` | Run-level experimental facts with source/provenance. |
| `Condition` | Executable or rubric-only check over state, design, artifacts, or observations. |
| `Observation` | Variable-level scientific memory, such as a target logFC under a contrast. |

## What Is Fixed vs User-Authored

Pertura separates the runtime kernel from the Perturb-seq product surface:

| Layer | Who defines it? | Examples | Notes |
| --- | --- | --- | --- |
| Core tools | Pertura core | `execute_code`, `get_context_review`, `trace_upstream`, `audit_run` | Runtime primitives. Domain authors usually do not create these. |
| Perturb-seq product projection | Pertura product layer | Design Ledger, Flow, Evidence Board, product timeline | The default GUI/API/LLM surface. It is compiled from the event-sourced snapshot. |
| Capabilities | Domain pack / user | `run_de`, `audit_controls`, `assign_guides` | The action menu shown to the LLM. A capability can use one or more core tools. |
| Conditions | Core + domain/user | `control_labels_defined`, `workspace_files_available` | Evaluators are provided by core/domain code; users attach them to nodes. Natural-language conditions can remain rubric-only or be compiled. |
| Design fields | Domain/user + run state | `control_labels`, `guide_column`, `perturbation_modality` | Experimental facts for one run. They should carry a source such as `pi_confirmed`, `api_confirmed`, `data_observed`, or `llm_inferred`. |
| Analysis nodes | Domain pack / user | `experimental_design`, `guide_assignment`, `effect_exploration` | Stages that constrain which capabilities are currently allowed and what must be true before moving on. |

In short: users write `AnalysisGraph`, `Domain`, and `Capability` contracts.
Pertura supplies the core runtime tools and event-sourced state machinery.
`Design` is not a static schema alone; it is the confirmed or inferred
experimental context accumulated during a run.

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
from pertura.domain import perturbseq as ps

graph = (
    pt.AnalysisGraph("my_singlecell", start_node_id="inspect")
    .node("inspect")
    .title("Inspect workspace")
    .goal("Find matrix inputs and summarize schema.")
    .use(ps.caps.inspect_workspace, ps.caps.load_dataset)
    .done_when(c.workspace_files_available())
    .next("design", strict=True)
    .end()
)

graph.node("design").title("Resolve design").goal(
    "Resolve controls before interpretation."
).enter_if(
    c.workspace_files_available()
).use(
    ps.caps.inspect_schema, ps.caps.audit_controls
).done_when(
    c.design_confirmed("control_labels")
).next("effect")

graph.node("effect").title("Effect exploration").goal(
    "Run bounded differential expression."
).enter_if(
    c.design_confirmed("control_labels")
).use(
    ps.caps.run_de
).done_when(
    c.observation_metric("logFC")
)

domain = (
    pt.Domain(name="my_singlecell")
    .with_graph(graph)
    .add_capability(
        ps.caps.run_de,
        description="Run bounded differential expression.",
        expected_artifacts=["de_result"],
        expected_observations=["logFC", "p_value"],
        required_inputs=["adata", "control_labels", "target_column"],
    )
    .add_rubric("Do not interpret target effects before controls are confirmed.")
)

assert domain.audit()["ok"]
domain.to_json(".pertura/domain.json")
```

`ps.caps.*` entries are typed Perturb-seq references for autocomplete and early
auditing. Pertura core also exposes a small `pt.caps` module for generic
harness actions, but scientific actions such as `run_de` live in domain packs.
Serialized domain files still store plain capability ids such as `"run_de"`,
so domain packs remain portable and editable.

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
