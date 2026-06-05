"""Perturb-seq reference domain."""

from pertura.domain.base import Domain
from pertura.capabilities import capability
from pertura.spec.models import AnalysisGraph, condition
from . import perturbseq_caps as caps


def _design_required(condition_id: str, field: str, message: str):
    return condition(
        condition_id,
        evaluator_id="design_field_known",
        tier="C",
        failure_mode="human_interrupt",
        inputs={"field": field},
        message=message,
    )


def build_perturbseq_analysis_graph():
    graph = AnalysisGraph("perturbseq_v2", start_node_id="workspace_inspection")
    graph.add_node(
        "workspace_inspection",
        title="Workspace inspection",
        purpose="Discover matrix-level inputs, metadata, scripts, and prior notebooks.",
        allowed_capabilities=["inspect_workspace", "load_dataset", "inspect_schema"],
        requires=["workspace files are available"],
        completion=[
            condition(
                "dataset_summarized",
                evaluator_id="has_dataset_loaded_observation",
                message="Dataset or relevant input files are summarized.",
            )
        ],
        recommended_actions=["list files", "inspect candidate AnnData or tables"],
        expected_outputs=["workspace_file observations", "dataset schema observations"],
        next_nodes=["experimental_design", "scrna_qc"],
    )
    graph.add_node(
        "experimental_design",
        title="Experimental design audit",
        purpose="Clarify perturbation modality, guide capture, controls, MOI, and loading strategy.",
        allowed_capabilities=[
            "inspect_schema", "audit_controls", "audit_experimental_design",
            "audit_guide_capture", "audit_moi_loading",
        ],
        requires=["Dataset is loaded."],
        completion=[
            condition(
                "control_labels_defined",
                evaluator_id="design_field_known",
                tier="C",
                failure_mode="human_interrupt",
                inputs={"field": "control_labels"},
                message="Control labels should be resolved before target-level interpretation.",
            ),
            _design_required(
                "perturbation_design_known",
                "perturbation_modality",
                "Perturbation modality should be resolved before perturbation-specific interpretation.",
            ),
            "Perturbation modality is recorded.",
            "Control labels are recorded or unresolved status is documented.",
            "Guide capture and MOI/loading assumptions are recorded.",
        ],
        recommended_actions=["audit obs columns", "inspect values for controls", "ask user if design authority is needed"],
        expected_outputs=["design observations", "control audit artifact"],
        next_nodes=["scrna_qc", "guide_assignment"],
    )
    graph.add_node(
        "scrna_qc",
        title="scRNA-seq QC",
        purpose="Assess matrix-level cell and gene quality before perturbation-specific interpretation.",
        allowed_capabilities=[
            "run_qc", "plot_qc", "filter_cells", "empty_droplet_filter",
            "overloading_strategy", "normalize",
        ],
        requires=["Dataset is loaded."],
        completion=[
            condition(
                "qc_metrics_registered",
                evaluator_id="has_observation_metric",
                inputs={"metric": "n_cells"},
                message="QC metrics are registered.",
            ),
            condition(
                "filtering_decision_recorded",
                evaluator_id="has_observation",
                inputs={"metric": "filtering_decision"},
                hard=False,
                message="Filtering decisions are recorded when filtering is applied.",
            ),
        ],
        recommended_actions=["calculate QC metrics", "plot distributions", "record before/after counts"],
        expected_outputs=["qc observations", "qc figures", "filtered dataset checkpoint"],
        next_nodes=["guide_assignment", "state_reference", "experimental_design"],
    )
    graph.add_node(
        "guide_assignment",
        title="Guide assignment",
        purpose="Infer or validate guide assignment, guide counts, target mapping, and assignment thresholds.",
        allowed_capabilities=["assign_guides", "audit_guide_counts", "compare_thresholds", "audit_guide_mapping"],
        requires=[
            "Dataset is loaded.",
            _design_required(
                "control_labels_defined",
                "control_labels",
                "Control labels should be resolved before guide assignment interpretation.",
            ),
        ],
        must_confirm=[],
        completion=[
            condition(
                "guide_assignment_recorded",
                evaluator_id="has_observation",
                inputs={"metric": "guide_assignment"},
                message="Guide assignment method is recorded.",
            ),
            condition(
                "guide_count_distribution_summarized",
                evaluator_id="has_observation",
                inputs={"metric": "n_guides"},
                message="Guide count distribution is summarized.",
            ),
            condition(
                "target_mapping_registered",
                evaluator_id="has_artifact_kind",
                inputs={"kind": "mapping_table"},
                hard=False,
                message="Target mapping is registered or explicitly marked unavailable.",
            ),
        ],
        recommended_actions=["compare thresholds", "audit low-MOI/high-MOI assumptions", "register guide count observations"],
        expected_outputs=["guide assignment observations", "target mapping artifact"],
        next_nodes=["perturbation_validation", "target_qc", "experimental_design"],
    )
    graph.add_node(
        "perturbation_validation",
        title="Perturbation validation",
        purpose="Check whether perturbations show expected target expression or signature direction.",
        allowed_capabilities=["validate_perturbation", "run_de", "score_signature"],
        requires=[
            "Dataset is loaded.",
            _design_required(
                "control_labels_defined",
                "control_labels",
                "Control labels should be resolved before perturbation validation.",
            ),
            _design_required(
                "guide_column_known",
                "guide_column",
                "Guide column should be resolved before perturbation validation.",
            ),
        ],
        completion=[
            condition(
                "perturbation_validation_registered",
                evaluator_id="has_observation",
                inputs={"metric": "perturbation_validation"},
                message="Direction checks or signature checks are registered.",
            )
        ],
        recommended_actions=["validate target expression direction", "register logFC/p_value/signature score"],
        expected_outputs=["perturbation validation observations"],
        next_nodes=["target_qc", "effect_exploration", "guide_assignment"],
    )
    graph.add_node(
        "target_qc",
        title="Target-level QC",
        purpose="Check per-target coverage, cells per target, guide concordance, and aggregation safety.",
        allowed_capabilities=["check_target_coverage", "check_guide_concordance", "aggregate_target"],
        requires=[
            "Dataset is loaded.",
            _design_required(
                "control_labels_defined",
                "control_labels",
                "Control labels should be resolved before target-level QC.",
            ),
        ],
        completion=[
            condition(
                "target_coverage_registered",
                evaluator_id="has_observation",
                inputs={"metric": "target_coverage"},
                message="Coverage is registered before target-level interpretation.",
            ),
            condition(
                "guide_concordance_registered",
                evaluator_id="has_observation",
                inputs={"metric": "guide_concordance"},
                hard=False,
                message="Guide concordance is registered or limitations are recorded.",
            ),
        ],
        recommended_actions=["compute cells per target", "check single-guide effects", "save coverage table"],
        expected_outputs=["target coverage table", "guide concordance observations"],
        next_nodes=["effect_exploration", "target_discovery", "guide_assignment"],
    )
    graph.add_node(
        "state_reference",
        title="State reference",
        purpose="Build or audit cell-state reference, clustering, annotations, and gene modules.",
        allowed_capabilities=[
            "build_embedding", "cluster_cells", "annotate_states",
            "score_modules", "learn_gene_modules",
        ],
        requires=["Dataset is loaded."],
        completion=[
            condition(
                "state_reference_registered",
                evaluator_id="has_artifact_kind",
                inputs={"kind": "embedding"},
                message="State reference artifact is registered.",
            ),
            condition(
                "module_scores_registered",
                evaluator_id="has_observation",
                inputs={"metric": "module_score"},
                hard=False,
                message="Module scores are registered when used.",
            ),
        ],
        recommended_actions=["PCA/UMAP/Leiden", "audit batch/state separation", "register cluster/module observations"],
        expected_outputs=["embedding figures", "cluster observations", "module score observations"],
        next_nodes=["effect_exploration", "target_discovery", "scrna_qc"],
    )
    graph.add_node(
        "effect_exploration",
        title="Perturbation effect exploration",
        purpose="Explore global effects, state composition shifts, module changes, and trajectory/fate biases.",
        allowed_capabilities=[
            "run_de", "compare_methods", "composition_test", "score_modules",
            "trajectory_analysis", "co_regulated_modules", "trace_upstream",
        ],
        requires=[
            "Dataset is loaded.",
            _design_required(
                "control_labels_defined",
                "control_labels",
                "Control labels should be resolved before effect exploration.",
            ),
        ],
        completion=["At least one perturbation effect observation or limitation is registered."],
        recommended_actions=["run exploratory DE", "check batch confounding", "branch for negative or suspicious results"],
        expected_outputs=["effect observations", "DE tables", "effect figures"],
        next_nodes=["target_discovery", "biology_story", "target_qc", "state_reference"],
    )
    graph.add_node(
        "target_discovery",
        title="Target discovery",
        purpose="Identify co-functional targets, drivers, and candidate regulatory relationships.",
        allowed_capabilities=[
            "rank_targets", "perturbation_profile_similarity",
            "cluster_effect_profiles", "score_driver_targets",
            "infer_network", "compare_branches",
        ],
        requires=[
            "Dataset is loaded.",
            _design_required(
                "control_labels_defined",
                "control_labels",
                "Control labels should be resolved before target discovery.",
            ),
        ],
        completion=["Target ranking or co-functional target observations are registered."],
        recommended_actions=["rank by effect strength", "cluster profiles", "record uncertainty and coverage"],
        expected_outputs=["target ranking table", "network observations"],
        next_nodes=["biology_story", "effect_exploration", "report"],
    )
    graph.add_node(
        "biology_story",
        title="Biology story",
        purpose="Draft cautious biological hypotheses from supported observations and limitations.",
        allowed_capabilities=["synthesize_story", "search_web", "query_observation_memory"],
        requires=["Dataset is loaded."],
        completion=["Story claims cite observations, artifacts, and limitations."],
        recommended_actions=["prefer data/method explanations before novel biology", "use web research only as supporting context"],
        expected_outputs=["story conclusions", "limitations"],
        next_nodes=["report", "target_discovery", "effect_exploration"],
    )
    graph.add_node(
        "report",
        title="Report",
        purpose="Assemble key findings, limitations, paths, branches, artifacts, and next steps.",
        allowed_capabilities=["generate_report", "trace_upstream", "compare_branches"],
        requires=["Dataset is loaded."],
        completion=[
            condition(
                "report_artifact_registered",
                evaluator_id="has_artifact_kind",
                inputs={"kind": "report"},
                message="Report artifact is registered.",
            )
        ],
        recommended_actions=["include derivation paths", "grade conclusions", "surface unresolved blockers"],
        expected_outputs=["report markdown/html", "conclusion graph"],
        next_nodes=[],
    )
    return graph.to_spec()


def default_graph():
    """Return the default perturb-seq AnalysisGraphSpec."""
    return build_perturbseq_analysis_graph()


def default_domain():
    """Return a fresh perturb-seq Domain object for a new workbench run."""
    return DOMAIN.model_copy(deep=True)


def _cap(capability_id: str, stage: str, description: str, *,
         kind: str = "execute", tools: list[str] | None = None,
         artifacts: list[str] | None = None,
         observations: list[str] | None = None,
         required: list[str] | None = None,
         packages: list[str] | None = None,
         functions: list[str] | None = None,
         modes: list[str] | None = None,
         risk: str = "low", backend: str = "kernel") -> dict:
    return capability(
        capability_id,
        stage=stage,
        description=description,
        kind=kind,
        tool_names=tools or (["execute_code"] if kind in {"execute", "review"} else []),
        packages=packages or [],
        functions=functions or [],
        analysis_modes=modes or [],
        expected_artifacts=artifacts or [],
        expected_observations=observations or [],
        required_inputs=required or [],
        risk=risk,
        backend=backend,
    ).model_dump(mode="json")


PERTURBSEQ_CAPABILITIES = [
    _cap("inspect_workspace", "workspace_inspection", "Inspect files and candidate matrix inputs.", kind="read"),
    _cap("load_dataset", "workspace_inspection", "Detect and prepare matrix-level dataset loading code.", kind="read", tools=["load_dataset"], observations=["schema"]),
    _cap("inspect_schema", "experimental_design", "Inspect obs/var/layer schema and candidate design columns.", kind="review", observations=["schema", "design"]),
    _cap("audit_controls", "experimental_design", "Audit control labels and control-related columns.", kind="review", observations=["control_audit"], required=["control_candidates"]),
    _cap("audit_experimental_design", "experimental_design", "Audit modality, guide capture, MOI, loading, and controls.", kind="review", observations=["design"]),
    _cap("audit_guide_capture", "experimental_design", "Record guide capture source and whether guide counts are directly observed or inferred.", kind="review", observations=["guide_capture"]),
    _cap("audit_moi_loading", "experimental_design", "Record low/high MOI and normal/overloaded droplet assumptions.", kind="review", observations=["moi_loading"]),
    _cap("run_qc", "scrna_qc", "Compute scRNA-seq QC metrics.", observations=["qc_metric"], artifacts=["qc_table"]),
    _cap("plot_qc", "scrna_qc", "Generate QC diagnostic plots.", observations=["qc_metric"], artifacts=["figure"]),
    _cap("filter_cells", "scrna_qc", "Apply and record cell/gene filtering decisions.", observations=["filtering_decision"], artifacts=["checkpoint"]),
    _cap("empty_droplet_filter", "scrna_qc", "Assess empty droplets or low-quality droplets before filtering.", observations=["empty_droplet"], artifacts=["qc_table"]),
    _cap("overloading_strategy", "scrna_qc", "Handle droplet overloading without blindly applying doublet removal.", observations=["overloading_strategy"]),
    _cap("normalize", "scrna_qc", "Normalize/log-transform or prepare analysis matrix.", observations=["normalization_decision"], artifacts=["checkpoint"]),
    _cap("assign_guides", "guide_assignment", "Assign guides or audit existing guide assignments.", observations=["guide_assignment"], artifacts=["guide_assignment_table"]),
    _cap("audit_guide_counts", "guide_assignment", "Summarize guide count distribution and MOI behavior.", observations=["guide_count"]),
    _cap("compare_thresholds", "guide_assignment", "Compare guide assignment thresholds or methods.", observations=["threshold_sensitivity"], artifacts=["threshold_table"]),
    _cap("audit_guide_mapping", "guide_assignment", "Audit guide-to-target mapping availability and ambiguity.", observations=["guide_mapping"], artifacts=["mapping_table"]),
    _cap("validate_perturbation", "perturbation_validation", "Validate target expression or signature direction.", observations=["perturbation_validation"]),
    _cap(
        "run_de",
        "effect_exploration",
        "Run bounded differential expression or effect-size analysis.",
        observations=["logFC", "p_value"],
        artifacts=["de_result"],
        required=["adata", "control_labels", "target_column"],
        packages=["scanpy"],
        functions=["scanpy.tl.rank_genes_groups", "scanpy.get.rank_genes_groups_df"],
        modes=["differential_expression", "effect_size"],
    ),
    _cap("score_signature", "perturbation_validation", "Score gene signatures or modules.", observations=["signature_score"], artifacts=["signature_table"]),
    _cap("check_target_coverage", "target_qc", "Check cells, guides, batches, and samples per target.", observations=["target_coverage"], artifacts=["coverage_table"]),
    _cap("check_guide_concordance", "target_qc", "Check direction consistency across guides for a target.", observations=["guide_concordance"], artifacts=["concordance_table"]),
    _cap("aggregate_target", "target_qc", "Aggregate guide-level information to target level after checks.", observations=["target_aggregate"], artifacts=["target_table"]),
    _cap("build_embedding", "state_reference", "Build PCA/neighbors/UMAP or equivalent state space.", observations=["embedding_summary"], artifacts=["embedding"]),
    _cap("cluster_cells", "state_reference", "Cluster cells or audit existing clusters.", observations=["cluster_summary"], artifacts=["cluster_table"]),
    _cap("annotate_states", "state_reference", "Annotate cell states using markers or provided labels.", observations=["state_annotation"], artifacts=["annotation_table"]),
    _cap("score_modules", "state_reference", "Compute module or program scores.", observations=["module_score"], artifacts=["module_score_table"]),
    _cap("learn_gene_modules", "state_reference", "Learn gene modules from the dataset when external modules are unavailable.", observations=["gene_module"], artifacts=["module_table"]),
    _cap("compare_methods", "effect_exploration", "Compare methods for a target/effect result.", tools=["compare_methods", "execute_code"], observations=["method_sensitivity"]),
    _cap("composition_test", "effect_exploration", "Test perturbation effects on state composition.", observations=["composition_shift"], artifacts=["composition_table"]),
    _cap("trajectory_analysis", "effect_exploration", "Explore pseudotime, fate bias, or trajectory shifts when state structure supports it.", observations=["trajectory_effect"], artifacts=["trajectory_table"]),
    _cap("co_regulated_modules", "effect_exploration", "Identify co-regulated gene modules affected by perturbations.", observations=["co_regulated_module"], artifacts=["module_table"]),
    _cap("trace_upstream", "effect_exploration", "Trace upstream dependencies for an observation or conclusion.", kind="read", tools=["trace_upstream"]),
    _cap("rank_targets", "target_discovery", "Rank targets by effect strength, coverage, and limitations.", observations=["target_rank"], artifacts=["target_ranking"]),
    _cap("perturbation_profile_similarity", "target_discovery", "Compare perturbation-level response profiles to find co-functional targets.", observations=["profile_similarity"], artifacts=["similarity_table"]),
    _cap("cluster_effect_profiles", "target_discovery", "Cluster perturbation effect profiles.", observations=["effect_profile_cluster"], artifacts=["cluster_table"]),
    _cap("score_driver_targets", "target_discovery", "Score candidate driver targets by effect size, coverage, and program impact.", observations=["driver_score"], artifacts=["target_ranking"]),
    _cap("infer_network", "target_discovery", "Draft regulatory relationships from observed effects.", observations=["network_edge"], artifacts=["network_table"]),
    _cap("compare_branches", "target_discovery", "Compare branch-specific observations and conclusions.", kind="read", tools=["compare_branches"]),
    _cap("synthesize_story", "biology_story", "Synthesize cautious biological interpretation from observations.", kind="report", tools=["finish"], observations=["story_conclusion"]),
    _cap("search_web", "biology_story", "Use web context for biological story support only.", kind="external", tools=["search_web"], risk="high"),
    _cap("query_observation_memory", "biology_story", "Retrieve variable-level observation memory.", kind="read", tools=["query_observation_memory"]),
    _cap("generate_report", "report", "Generate report with conclusions, limitations, and paths.", kind="report", tools=["finish"], artifacts=["report"]),
]

DOMAIN = Domain(
    name="perturbseq",
    metadata={
        "design_fields": [
            "control_labels",
            "control_column",
            "guide_column",
            "target_column",
            "perturbation_column",
            "perturbation_modality",
            "batch_column",
            "sample_column",
            "state_column",
            "moi",
            "loading_strategy",
        ],
    },
    agenda=[
        "experimental_design", "scrna_qc", "guide_assignment",
        "perturbation_validation", "target_qc", "state_reference",
        "effect_exploration", "target_discovery", "biology_story", "report",
    ],
    capabilities=PERTURBSEQ_CAPABILITIES,
    tools="""# Perturb-seq Analysis Tooling Guideline

## 0. General Rules
Use the simplest reliable method first. Do not start with heavy modeling unless needed.
Always inspect data schema before assuming column names. Never assume guide/target/control/batch/condition columns exist by default.
Every result: register_observation(). Every output file: register_artifact(). Branch variables: use prefixed names (adata_br_..., de_results_br_...).
A negative result is not a biological negative until coverage/controls/method are checked.
Record for every DE: target, control group, cell subset, batch/replicate, method, n_perturbed, n_control, effect size, p-value, limitations.
Data persists in the kernel across cells — DO NOT reload. Imports persist — DO NOT re-import.

## 1. Data Loading & Assembly
Inspect workspace files first. Choose loader based on what you find:
- .h5ad → sc.read_h5ad() / ad.read_h5ad()
- 10x .h5 → sc.read_10x_h5()
- CellRanger mtx dir → sc.read_10x_mtx()
- .csv/.tsv → pd.read_csv() / pd.read_table()
- Raw matrices → ad.AnnData(X, obs=..., var=...) to assemble
- Multiple AnnDatas → ad.concat()
- Metadata tables → pd.merge() into adata.obs
After loading: print shape, .obs.columns, .var.columns, .layers.keys(), .obsm.keys().
Register schema: register_observation("schema", target="anndata", metric="shape", value=f"{n_obs}x{n_vars}")
Register obs columns: register_observation("schema", target="obs_columns", metric="columns", value="...")
If required columns cannot be inferred, ask user rather than guessing.

## 2. Perturb-seq Schema Inference
Identify or ask for: guide column, target column, control labels, sample/replicate column, batch column, cell type column, guide count matrix/key, perturbation modality (KO/CRISPRi/CRISPRa/base editor), MOI regime (low/high/droplet overloading/combinatorial).
Candidate column name patterns: guide → guide,gRNA,sgRNA,assigned_guide,guide_id. target → target,gene,perturbation,gene_target. control → control,non_targeting,nontargeting,NTC,NT,safe_targeting. batch → batch,sample,donor,replicate,lane.
Never do target-level DE until control definition and target annotation are audited.

## 3. Standard scRNA-seq QC (scanpy)
sc.pp.calculate_qc_metrics(adata, ...) then visualize distributions BEFORE filtering.
Mitochondrial: adata.var_names.str.startswith("MT-") (human) or "mt-" (mouse).
sc.pp.filter_cells(adata, min_genes=...), sc.pp.filter_genes(adata, min_cells=...) — thresholds from data, not defaults.
For droplet overloading: document loading strategy. DO NOT apply standard doublet filtering.
Register: n_cells before/after, median_UMI, median_genes, median_pct_mt.
sc.pp.normalize_total(adata, target_sum=1e4), sc.pp.log1p(adata).
Preserve raw counts: adata.layers["counts"] = adata.X.copy() before overwriting.

## 4. Normalization & Feature Selection
Default: normalize → log1p → HVG (2000) → PCA (50) → neighbors (15) → UMAP → Leiden.
For pseudobulk/replicate-aware DE: use raw counts, not log-normalized.

## 5. Dimensionality Reduction & Clustering (scanpy)
sc.pp.pca(adata, n_comps=50), sc.pp.neighbors(adata, n_neighbors=15), sc.tl.umap(adata), sc.tl.leiden(adata, resolution=...).
UMAP colored by: target, guide, control, batch, QC metrics, cell type. Use for exploration, not as primary proof.

## 6. Guide Assignment & QC (pertpy)
Inspect guide count distribution before thresholding.
Low MOI: strict threshold. High MOI / combinatorial: prefer mixture or probabilistic.
pertpy.pp.GuideAssignment(adata, guide_count_key=..., output_column='assigned_guide', method='threshold'|'mixture', threshold=5).
pertpy.pp.GuideQC(adata, guide_column=..., target_column=...).
Register: n_cells no_guide/one_guide/multi_guide, n_guides_total, n_targets_total, median cells per guide, median cells per target, control cell count.
Multi-guide targets: check guide concordance before collapsing to target-level.

## 7. Target Aggregation
Allowed only after: assignment done or labels reliable, guide-to-target mapping exists, controls audited, coverage sufficient.
Per target: n_guides, n_cells, n_samples, n_batches, n_control cells, guide direction consistency.
Save target_coverage.csv: register_artifact(kind="table", path=..., description="Per-target coverage").

## 8. Differential Expression

### 8.1 Exploratory DE (scanpy)
sc.tl.rank_genes_groups(adata, groupby='...', method='wilcoxon', reference='control'). Good for screening/visualization. NOT sufficient for strong biological claims — may inflate significance.

### 8.2 Pseudobulk / Replicate-aware DE
For claims: aggregate counts by sample+perturbation group. Use count-based methods.
Recommended: pertpy.tools.PyDESeq2, pertpy.tools.EdgeR, pydeseq2, statsmodels GLM.
Each row = one biological replicate, not one cell.
Required: sample column, group column, control group, raw counts, min cells per pseudobulk.
Register: logFC, adjusted p-value, n_perturbed, n_control, n_replicates, method, limitations.

### 8.3 Cell-type-specific DE
Run DE within relevant cell types if labels available. Register cell type, target, effect, p-value, n_cells.

## 9. Perturbation Response & Mixscape (pertpy)
Use when guide labels are noisy or expected effects absent.
pertpy.tools.Mixscape — identify true responders among guide-positive cells.
scvi.external.ContrastiveVI — separate perturbation-driven variation from background (advanced, optional).
Register: fraction responders, responder/non-responder counts, target response rate, whether DE changes after filtering.

## 10. Perturbation Distance & Effect Profiles
Compare perturbations via correlation/cosine between effect profiles. Cluster perturbation signatures.
pertpy.tools.Distance, pertpy.tools.DistanceTest, numpy.corrcoef, scipy.stats.spearmanr, sklearn.metrics.pairwise.cosine_similarity, scipy.cluster.hierarchy.
Register: metric, features used, top similar perturbations, clusters.

## 11. Pathway, TF & Gene Set Activity (decoupler)
decoupler: ORA, GSEA, GSVA, AUCell, VIPER. Resources: PROGENy, DoRothEA, CollecTRI, Hallmark.
gseapy / Enrichr as optional external support.
sc.tl.score_genes(adata, gene_list) for predefined modules.
Register: resource, method, input genes, top pathways/TFs, adjusted p-values, target, contrast.

## 12. Cell Type Annotation
Marker-based via sc.tl.rank_genes_groups + manual inspection. sc.tl.score_genes for known markers. celltypist if relevant reference exists.
Automated annotation is not ground truth. Register marker evidence and reference used.

## 13. Differential Abundance & Composition (pertpy)
Use when perturbations shift cell states rather than gene expression.
pertpy.tools.Milo — neighborhood-level differential abundance.
pertpy.tools.Sccoda / Tasccoda — compositional analysis.
Simple crosstabs + Fisher/chi-square for quick checks.
Register: cell state, target, effect direction, method, p-value, batch structure.

## 14. Batch, Replicate & Confounding Checks
Before strong claims: target×batch crosstab, target×sample crosstab, control×batch crosstab, cell type×target crosstab, guide×batch crosstab.
Target in only one batch/sample → downgrade or note limitation.
Save crosstab tables as artifacts. Register confounding findings.

## 15. Negative Result Trace
Do NOT conclude true negative immediately. Open a focused branch checking: coverage, controls, guide stringency, method sensitivity, cell-type specificity, batch confounding, plot/artifact correctness, biological expectation.
Minimum before supported negative: coverage adequate, controls audited, batch checked, ≥1 alternative method/parameter tried, guide concordance checked (multi-guide).
Close branch with: summary, evidence_ids, conclusion, implication for parent.

## 16. Plotting & Artifact Rules
EVERY important figure: save to artifacts_dir AND register_artifact(). Inline display alone is insufficient.
Pattern: fig_path = artifacts_dir / "name.png"; fig.savefig(fig_path, dpi=200, bbox_inches="tight"); plt.close(fig); register_artifact(kind="figure", path=fig_path, description="...").
sc.pl.* for standard plots. matplotlib/seaborn for custom.

## 17. Observation & Artifact Minimum
Each meaningful cell must produce ≥1 of: observation, artifact, finding, branch summary, or user question. A cell with no registered evidence is incomplete (except setup/import).

## 18. Method Selection Heuristics
Exploratory → scanpy QC, UMAP, rank_genes_groups, crosstabs, simple counts.
Claims → pseudobulk DE, replicate-aware models, guide concordance, coverage validators, branch comparison, pathway/TF validation.
Advanced (only when warranted) → Mixscape, scVI/ContrastiveVI, Milo/scCODA, scGen.

## 19. Claims & Conclusion Rules
No robust conclusion without: support_ids present, controls audited, coverage adequate, batch checked, branches closed or marked unresolved, validators pass.
Grades: robust (multi-evidence, validators pass), supported (sufficient evidence, no blockers), tentative (thin/limited evidence), inconclusive (insufficient), supported_negative (negative after full trace), blocked (missing controls/schema/confounding).
Every conclusion must cite observation, artifact, validator, and branch IDs.""",
    rubric=[
        "Check Perturb-seq experimental design before interpretation: modality, guide capture, controls, loading, MOI.",
        "For droplet overloading designs, avoid ordinary doublet filtering without a deconvolution plan.",
        "For guide assignment, treat low-MOI and high-MOI designs differently.",
        "Validate perturbation effects through target expression direction or gene signatures before target-level interpretation.",
        "For target-level claims, inspect target coverage and guide concordance before aggregation.",
        "Build state reference and gene modules before interpreting state composition or trajectory shifts.",
        "For target discovery, distinguish co-functional similarity, driver ranking, and regulatory-network hypotheses.",
        "Prefer autonomous validator checks for batch-condition confounding before asking the user.",
        "Treat low target coverage, guide discordance, empty DE, and bad plots as recoverable analysis issues first.",
        "Use web research only for biology story or follow-up hypotheses.",
    ],
    validators=[
        "control_label_audit", "batch_condition_crosstab", "guide_target_mapping_check",
        "target_coverage_check", "guide_concordance_check", "plot_artifact_check",
        "perturbation_modality_audit", "guide_capture_audit", "moi_loading_audit",
    ],
    analysis_graph=build_perturbseq_analysis_graph().model_dump(mode="json"),
    critic_rubric=[
        "Prefer data/method diagnosis before novel biological explanation.",
        "Treat empty or weak results as reasons to trace controls, coverage, contrast, and method sensitivity.",
        "Flag target effects that depend on a single guide, batch, or unsupported contrast.",
        "Do not upgrade a biology story when evidence paths are stale, unsupported, or missing artifacts.",
    ],
    condition_context="""Perturb-seq domain conditions:
- C-tier user/PI authority fields: control labels, perturbation modality, guide column when needed for target interpretation.
- Computable fields should be resolved through observations/artifacts before interrupting the user.
- Completion checks should prefer registered observations/artifacts over prose summaries.
- Rubric-only conditions are advisory and should appear in LLM context rather than hard gates.""",
    report_template="Report key results, evidence grade, unresolved limitations, artifacts, and derivation paths.",
    protocol="""0. EXPERIMENTAL DESIGN AUDIT
   - Perturbation modality: KO, CRISPRi, CRISPRa
   - Guide capture method
   - Control design: non-targeting, positive control
   - Droplet overloading / normal loading
   - Low MOI / High MOI

1. PERTURB-SEQ DATA QC
   1.1 Standard scRNA-seq QC
       - Remove low UMI, low feature, high mito, empty droplets
       - For droplet overloading: preserve multi-cell droplets, use regression/deconvolution
       - Register: n_cells, n_genes, median_UMI, pct_mito
   1.2 Guide assignment
       - Compare assignment thresholds/methods
       - Count gRNAs per cell, filter
       - Low-MOI vs high-MOI: different strategies
       - Register: n_cells_with_guide, n_guides_per_cell, assignment_rate
   1.3 Perturbation validation
       - Target expression direction (KO=down, CRISPRa=up)
       - Gene signature validation if available
       - Register: logFC, p_value per target
   1.4 Target-level QC
       - Cells/droplets per target
       - Guide concordance within target
       - Register: cells_per_target, guide_concordance

2. STATE REFERENCE
   2.1 Define reference state space
       - Dimensionality reduction, clustering, annotation
   2.2 Define gene modules
       - External knowledge base / external data / learned from data
   2.3 Register: n_clusters, cluster_labels, module_genes

3. PERTURBATION EFFECT EXPLORATION
   3.1 Global effect: distribution shift vs NTC
   3.2 Composition/state changes: cluster abundance, module score shifts
   3.3 Trajectory/fate: pseudotime, lineage bias
   3.4 Co-regulated modules: similar perturbation response patterns
   3.5 Register: effect_size, composition_shift, module_score_change

4. TARGET DISCOVERY
   4.1 Co-functional targets: similar transcriptional response profiles
   4.2 Driver targets: effect strength + key program impact
   4.3 Regulatory network: TF modules to gene programs (e.g., Zhou et al.)
   4.4 Register: target_similarity, driver_score, network_edge

5. REPORT — Summarize findings, evidence quality, limitations""",
    coding_guidelines="""## Behavioral Rules
- Run ONE small step per cell. Inspect the result. Then decide the next step.
- Split expensive processing: normalize, HVG, PCA, neighbors, UMAP as separate cells.
- Only pivot direction on significant evidence. Don't change course for every small result.
- After broad exploration, narrow focus to the most promising cell types, targets, or signals.
- If you need user input, use ask_user action. The harness records real interrupts — never write "[Asked PI]" yourself.

## Claim Calibration
- USE: "QC audit", "coverage summary", "sanity check", "direction check", "exploratory DE", "evidence suggests".
- AVOID: "validated biology", "proved mechanism", "confirmed target", "full Perturb-seq analysis".
- A negative result is not biological until coverage, controls, and method are checked.

## Data & Schema
- First cell: list workspace files. Discover and assemble. Never assume file format.
- Pre-annotated cell identity tables (cell_type, cluster) are downstream annotations — NOT ground truth for cell calling or QC thresholds.
- Distinguish: annotation audit (obs columns only) vs de novo assignment (raw guide counts used).
- Distinguish: matrix-level QC vs FASTQ-level preprocessing.
- DO NOT reload data in subsequent cells — kernel persists.

## Registration
- register_observation() for EVERY quantitative finding.
- register_artifact() for EVERY important figure and table. Save to artifacts_dir. Never rely on inline display alone.
- A cell with no registered evidence is incomplete (except setup/import).

## Per-stage Focus
  [experimental_design] Print observed columns. Identify perturb/guide/target/batch/control by inspecting values. DO NOT assume names.
  [scrna_qc] Visualize distributions BEFORE choosing thresholds. Print before/after. Check mito pct.
  [guide_assignment] Count guides per cell. Compare thresholds (3,5,10). Low-MOI vs high-MOI strategies differ.
  [perturbation_validation] Check direction matches modality (KO=down, CRISPRa=up). DE per target vs control.
  [target_qc] Per-target cells, guide concordance. Flag low-coverage targets.
  [state_reference] PCA, UMAP, Leiden. Gene modules from external knowledge or data-driven.
  [effect_exploration] DE per contrast. Module scores. Composition shifts. Check batch confounding.
  [target_discovery] Rank by effect size. Cluster profiles. Co-functional targets. Driver targets.
  [biology_story] Cite specific observations. Note limitations. Downgrade unsupported interpretations.
  [report] Summarize findings. Grade evidence. List open questions and unresolved branches.""",
)


__all__ = [
    "DOMAIN",
    "PERTURBSEQ_CAPABILITIES",
    "caps",
    "build_perturbseq_analysis_graph",
    "default_graph",
    "default_domain",
]
