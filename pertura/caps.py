"""Typed built-in capability references.

Use this module for authoring analysis graphs without bare string ids:

    import pertura as pt

    graph.node("effect").use(pt.caps.run_de)

The runtime still serializes capability ids as strings so domain JSON remains
portable and easy to edit.
"""

from __future__ import annotations

from pertura.capabilities import CapabilityRef, capability_ref


# Generic harness capabilities.
inspect_workspace = capability_ref(
    "inspect_workspace",
    title="Inspect workspace",
    description="List files and identify likely matrix, metadata, script, and notebook inputs.",
    stage="workspace_inspection",
    kind="read",
)
load_dataset = capability_ref(
    "load_dataset",
    title="Load dataset",
    description="Load a matrix-level single-cell dataset into the execution runtime.",
    stage="workspace_inspection",
    kind="read",
)
inspect_schema = capability_ref(
    "inspect_schema",
    title="Inspect schema",
    description="Inspect AnnData/table columns, dimensions, dtypes, and candidate design fields.",
    stage="experimental_design",
    kind="read",
)
query_observation_memory = capability_ref(
    "query_observation_memory",
    title="Query observation memory",
    description="Retrieve compact variable-level scientific observations from prior attempts.",
    kind="read",
)
trace_upstream = capability_ref(
    "trace_upstream",
    title="Trace upstream",
    description="Trace an observation, artifact, conclusion, or attempt to upstream dependencies.",
    kind="read",
)
compare_branches = capability_ref(
    "compare_branches",
    title="Compare branches",
    description="Compare observations, artifacts, conclusions, or graph changes across branches.",
    kind="read",
)
search_web = capability_ref(
    "search_web",
    title="Search web",
    description="Use approved web research for biology-story context or follow-up hypotheses.",
    kind="external",
)
generate_report = capability_ref(
    "generate_report",
    title="Generate report",
    description="Assemble a report from registered conclusions, evidence paths, artifacts, and limitations.",
    stage="report",
    kind="report",
)


# Perturb-seq reference capabilities.
audit_controls = capability_ref("audit_controls", stage="experimental_design", kind="read")
audit_experimental_design = capability_ref("audit_experimental_design", stage="experimental_design", kind="read")
audit_guide_capture = capability_ref("audit_guide_capture", stage="experimental_design", kind="read")
audit_moi_loading = capability_ref("audit_moi_loading", stage="experimental_design", kind="read")

run_qc = capability_ref("run_qc", stage="scrna_qc")
plot_qc = capability_ref("plot_qc", stage="scrna_qc")
filter_cells = capability_ref("filter_cells", stage="scrna_qc")
empty_droplet_filter = capability_ref("empty_droplet_filter", stage="scrna_qc")
overloading_strategy = capability_ref("overloading_strategy", stage="scrna_qc")
normalize = capability_ref("normalize", stage="scrna_qc")

assign_guides = capability_ref("assign_guides", stage="guide_assignment")
audit_guide_counts = capability_ref("audit_guide_counts", stage="guide_assignment", kind="review")
compare_thresholds = capability_ref("compare_thresholds", stage="guide_assignment")
audit_guide_mapping = capability_ref("audit_guide_mapping", stage="guide_assignment", kind="review")

validate_perturbation = capability_ref("validate_perturbation", stage="perturbation_validation")
run_de = capability_ref(
    "run_de",
    title="Run differential expression",
    description="Run bounded differential expression or effect-size analysis.",
    stage="effect_exploration",
)
score_signature = capability_ref("score_signature", stage="perturbation_validation")

check_target_coverage = capability_ref("check_target_coverage", stage="target_qc", kind="review")
check_guide_concordance = capability_ref("check_guide_concordance", stage="target_qc", kind="review")
aggregate_target = capability_ref("aggregate_target", stage="target_qc")

build_embedding = capability_ref("build_embedding", stage="state_reference")
cluster_cells = capability_ref("cluster_cells", stage="state_reference")
annotate_states = capability_ref("annotate_states", stage="state_reference")
score_modules = capability_ref("score_modules", stage="state_reference")
learn_gene_modules = capability_ref("learn_gene_modules", stage="state_reference")

global_effect = capability_ref("global_effect", stage="effect_exploration")
composition_test = capability_ref("composition_test", stage="effect_exploration")
trajectory_analysis = capability_ref("trajectory_analysis", stage="effect_exploration")
co_regulated_modules = capability_ref("co_regulated_modules", stage="effect_exploration")
compare_methods = capability_ref("compare_methods", stage="effect_exploration")

rank_targets = capability_ref("rank_targets", stage="target_discovery")
perturbation_profile_similarity = capability_ref("perturbation_profile_similarity", stage="target_discovery")
cluster_effect_profiles = capability_ref("cluster_effect_profiles", stage="target_discovery")
infer_network = capability_ref("infer_network", stage="target_discovery")
score_driver_targets = capability_ref("score_driver_targets", stage="target_discovery")

synthesize_story = capability_ref("synthesize_story", stage="biology_story", kind="report")


CORE: tuple[CapabilityRef, ...] = (
    inspect_workspace,
    load_dataset,
    inspect_schema,
    query_observation_memory,
    trace_upstream,
    compare_branches,
    search_web,
    generate_report,
)


PERTURBSEQ: tuple[CapabilityRef, ...] = (
    audit_controls,
    audit_experimental_design,
    audit_guide_capture,
    audit_moi_loading,
    run_qc,
    plot_qc,
    filter_cells,
    empty_droplet_filter,
    overloading_strategy,
    normalize,
    assign_guides,
    audit_guide_counts,
    compare_thresholds,
    audit_guide_mapping,
    validate_perturbation,
    run_de,
    score_signature,
    check_target_coverage,
    check_guide_concordance,
    aggregate_target,
    build_embedding,
    cluster_cells,
    annotate_states,
    score_modules,
    learn_gene_modules,
    global_effect,
    composition_test,
    trajectory_analysis,
    co_regulated_modules,
    compare_methods,
    rank_targets,
    perturbation_profile_similarity,
    cluster_effect_profiles,
    infer_network,
    score_driver_targets,
    synthesize_story,
)


ALL: tuple[CapabilityRef, ...] = (*CORE, *PERTURBSEQ)


def ids() -> list[str]:
    """Return all built-in capability ids."""
    return [item.id for item in ALL]


def by_id(capability_id: str) -> CapabilityRef:
    """Return a built-in capability reference by id."""
    for item in ALL:
        if item.id == capability_id:
            return item
    raise KeyError(capability_id)


__all__ = [
    "CapabilityRef",
    "CORE",
    "PERTURBSEQ",
    "ALL",
    "ids",
    "by_id",
    *(item.id for item in ALL),
]
