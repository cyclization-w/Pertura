"""Minimal public-API quickstart for Pertura analysis nodes.

Run from the repository root:

    python examples/analysis_node_quickstart.py

This example does not call an LLM and does not need data files. It shows how a
third-party user can author an analysis graph, inspect its contracts, and audit
whether the graph is usable as a scientific agent harness.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pertura import (  # noqa: E402
    AnalysisGraph,
    Domain,
    conditions as c,
    graph_contract,
    node_contract,
)


def build_domain() -> Domain:
    graph = AnalysisGraph("quickstart_perturbseq", start_node_id="inspect")
    (
        graph.node("inspect")
        .title("Inspect workspace")
        .goal("Find candidate matrices and summarize schema.")
        .use("inspect_workspace", "load_dataset")
        .done_when(c.workspace_files_available())
        .next("design", strict=True)
    )
    (
        graph.node("design")
        .title("Resolve design")
        .goal("Confirm controls and perturbation columns before interpretation.")
        .enter_if(c.workspace_files_available())
        .use("inspect_schema", "audit_controls")
        .done_when(c.design_confirmed("control_labels"))
        .next("effect")
    )
    (
        graph.node("effect")
        .title("Effect exploration")
        .goal("Run bounded differential expression and register evidence.")
        .enter_if(c.design_confirmed("control_labels"))
        .use("run_de")
        .done_when(c.observation_metric("logFC"))
    )
    return (
        Domain(name="quickstart")
        .with_graph(graph)
        .add_capability("inspect_workspace", description="Inspect workspace files.")
        .add_capability("load_dataset", description="Load matrix-level dataset.")
        .add_capability(
            "run_de",
            description="Run bounded differential expression.",
            expected_artifacts=["de_result"],
            expected_observations=["logFC", "p_value"],
            required_inputs=["control_labels"],
        )
        .add_rubric("Do not interpret target effects before controls are confirmed.")
    )


def main() -> None:
    domain = build_domain()
    spec = domain.analysis_graph
    registry = domain.registry()

    audit = domain.audit()
    effect = node_contract(spec, "effect", capabilities=registry)
    graph = graph_contract(spec, capabilities=registry)

    print("audit ok:", audit["ok"])
    print("nodes:", graph["node_count"])
    print("effect inputs:", ", ".join(effect["inputs"]["required"]) or "none")
    print("effect observations:", ", ".join(effect["outputs"]["expected_observations"]) or "none")
    print("effect template call:")
    print(json.dumps(effect["actions"]["template_calls"][0], indent=2))


if __name__ == "__main__":
    main()
