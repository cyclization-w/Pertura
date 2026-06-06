"""Perturb-seq domain capability references.

These are not Pertura core concepts. They are the action vocabulary for the
built-in Perturb-seq domain pack and can be replaced or extended by another
domain pack.
"""

from __future__ import annotations

from pertura.capabilities import CapabilityRef, capability_ref
from pertura import core_caps


# Generic harness actions used by the Perturb-seq pack.
inspect_workspace = core_caps.inspect_workspace
query_observation_memory = core_caps.query_observation_memory
trace_upstream = core_caps.trace_upstream
compare_branches = core_caps.compare_branches
generate_report = core_caps.generate_report


# Perturb-seq / single-cell actions.
load_dataset = capability_ref("load_dataset", stage="workspace_inspection", kind="read")
inspect_schema = capability_ref("inspect_schema", stage="experimental_design", kind="review")
audit_controls = capability_ref("audit_controls", stage="experimental_design", kind="review")
audit_experimental_design = capability_ref("audit_experimental_design", stage="experimental_design", kind="review")
audit_guide_capture = capability_ref("audit_guide_capture", stage="experimental_design", kind="review")
audit_moi_loading = capability_ref("audit_moi_loading", stage="experimental_design", kind="review")

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
batch_condition_audit = capability_ref("batch_condition_audit", stage="effect_exploration", kind="review")
contrast_audit = capability_ref("contrast_audit", stage="effect_exploration", kind="review")
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
module_scoring = capability_ref("module_scoring", stage="effect_exploration")
composition_shift = capability_ref("composition_shift", stage="effect_exploration")

rank_targets = capability_ref("rank_targets", stage="target_discovery")
perturbation_profile_similarity = capability_ref("perturbation_profile_similarity", stage="target_discovery")
target_similarity = capability_ref("target_similarity", stage="target_discovery")
cluster_effect_profiles = capability_ref("cluster_effect_profiles", stage="target_discovery")
infer_network = capability_ref("infer_network", stage="target_discovery")
score_driver_targets = capability_ref("score_driver_targets", stage="target_discovery")

synthesize_story = capability_ref("synthesize_story", stage="biology_story", kind="report")
search_web = capability_ref("search_web", stage="biology_story", kind="external")
report_assembly = capability_ref("report_assembly", stage="report", kind="report")


ALL: tuple[CapabilityRef, ...] = (
    inspect_workspace,
    load_dataset,
    inspect_schema,
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
    batch_condition_audit,
    contrast_audit,
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
    module_scoring,
    composition_shift,
    rank_targets,
    perturbation_profile_similarity,
    target_similarity,
    cluster_effect_profiles,
    score_driver_targets,
    infer_network,
    compare_branches,
    synthesize_story,
    search_web,
    report_assembly,
    query_observation_memory,
    generate_report,
    trace_upstream,
)


def ids() -> list[str]:
    return [item.id for item in ALL]


def by_id(capability_id: str) -> CapabilityRef:
    for item in ALL:
        if item.id == capability_id:
            return item
    raise KeyError(capability_id)


__all__ = [
    "CapabilityRef",
    "ALL",
    "ids",
    "by_id",
    *(item.id for item in ALL),
]
