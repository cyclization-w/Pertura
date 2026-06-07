"""Perturb-seq capability template provider."""

from __future__ import annotations


def template_code_for(
    capability_id: str,
    *,
    mode: str = "",
    target: str = "",
    columns: dict | None = None,
    control_labels: list | None = None,
    parameters: dict | None = None,
) -> str:
    columns = columns or {}
    control_labels = control_labels or []
    parameters = parameters or {}
    target_expr = repr(target or "<target>")
    perturb_col_expr = repr(columns.get("perturbation") or columns.get("target") or "<target_or_perturbation_column>")
    guide_col_expr = repr(columns.get("guide") or "<guide_column>")
    target_col_expr = repr(columns.get("target") or "<target_column>")
    state_col_expr = repr(columns.get("state") or "<state_column>")
    control_expr = repr(control_labels)
    if capability_id == "run_de":
        return f"""# Differential expression / effect-size skeleton.
# Assumes `adata` is loaded and control_labels / target labels are confirmed.
import pandas as pd
import scanpy as sc

target = {target_expr}
perturb_col = {perturb_col_expr}
control_labels = {control_expr} or (list(design.get("control_labels", [])) if "design" in globals() else [])
groupby = perturb_col

if not control_labels:
    raise ValueError("control_labels must be confirmed before run_de")
if groupby not in adata.obs:
    raise ValueError(f"Missing perturbation column: {{groupby}}")

sc.tl.rank_genes_groups(
    adata,
    groupby=groupby,
    groups=[target],
    reference=control_labels[0],
    method="wilcoxon",
)
de = sc.get.rank_genes_groups_df(adata, group=target)
de_path = artifacts_dir / f"de_{{target}}.csv"
de.to_csv(de_path, index=False)

top = de.iloc[0].to_dict() if len(de) else {{}}
register_observation("de_effect", target=target, metric="logFC", value=float(top.get("logfoldchanges", 0.0)), method="scanpy.rank_genes_groups")
register_observation("de_effect", target=target, metric="p_value", value=float(top.get("pvals_adj", top.get("pvals", 1.0))), method="scanpy.rank_genes_groups")
register_artifact(str(de_path), kind="table", summary=f"DE results for {{target}}")"""
    if capability_id == "check_target_coverage":
        return f"""# Target coverage skeleton.
import pandas as pd

target_col = {target_col_expr}
guide_col = {guide_col_expr}
if target_col not in adata.obs:
    raise ValueError(f"Missing target column: {{target_col}}")

coverage = adata.obs.groupby(target_col).size().reset_index(name="n_cells")
coverage_path = artifacts_dir / "target_coverage.csv"
coverage.to_csv(coverage_path, index=False)
register_observation("target_coverage", target="all_targets", metric="n_targets", value=int(coverage.shape[0]), method="groupby_count")
register_artifact(str(coverage_path), kind="table", summary="Cells per perturbation target")"""
    if capability_id in {"run_qc", "plot_qc", "filter_cells"}:
        return """# scRNA-seq QC skeleton.
import scanpy as sc

sc.pp.calculate_qc_metrics(adata, inplace=True)
register_observation("qc_metric", target="cells", metric="n_cells", value=int(adata.n_obs), method="scanpy.calculate_qc_metrics")
register_observation("qc_metric", target="genes", metric="n_genes", value=int(adata.n_vars), method="scanpy.calculate_qc_metrics")
qc_path = artifacts_dir / "qc_obs_metrics.csv"
adata.obs.to_csv(qc_path)
register_artifact(str(qc_path), kind="table", summary="Cell-level QC metrics")"""
    if capability_id in {"assign_guides", "audit_guide_counts", "audit_guide_mapping"}:
        return f"""# Guide assignment / audit skeleton.
import pandas as pd

guide_col = {guide_col_expr}
if guide_col not in adata.obs:
    raise ValueError(f"Missing guide column: {{guide_col}}")

guide_counts = adata.obs[guide_col].value_counts(dropna=False).reset_index()
guide_counts.columns = [guide_col, "n_cells"]
guide_path = artifacts_dir / "guide_counts.csv"
guide_counts.to_csv(guide_path, index=False)
register_observation("guide_count", target="all_guides", metric="n_guides", value=int(guide_counts.shape[0]), method="value_counts")
register_artifact(str(guide_path), kind="table", summary="Guide count distribution")"""
    if capability_id in {"state_reference", "build_embedding", "cluster_cells", "annotate_states", "score_modules", "learn_gene_modules"}:
        return f"""# State-reference skeleton.
import scanpy as sc
import pandas as pd

if 'adata' not in globals():
    raise ValueError("adata must be loaded before state_reference")

sc.pp.pca(adata)
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.tl.leiden(adata)

embedding_path = artifacts_dir / "state_embedding.csv"
cluster_path = artifacts_dir / "cluster_assignments.csv"
adata.obsm["X_umap"][:].tolist() if "X_umap" in adata.obsm else None
pd.DataFrame(adata.obsm["X_umap"], columns=["UMAP1", "UMAP2"]).to_csv(embedding_path, index=False)
pd.DataFrame({{"cluster": adata.obs["leiden"]}}).to_csv(cluster_path, index=False)
register_observation("embedding_summary", target="all_cells", metric="embedding", value="umap", method="scanpy.tl.umap")
register_observation("cluster_summary", target="all_cells", metric="clusters", value=int(adata.obs["leiden"].nunique()), method="scanpy.tl.leiden")
register_artifact(str(embedding_path), kind="table", summary="UMAP embedding coordinates")
register_artifact(str(cluster_path), kind="table", summary="Cluster assignments")"""
    if capability_id == "trajectory_analysis":
        return f"""# Trajectory / fate-bias skeleton.
import scanpy as sc
import pandas as pd

state_col = {state_col_expr}
if 'adata' not in globals():
    raise ValueError("adata must be loaded before trajectory_analysis")
if state_col not in adata.obs:
    raise ValueError(f"Missing state column: {{state_col}}")

sc.tl.diffmap(adata)
sc.tl.dpt(adata)
traj_path = artifacts_dir / "trajectory_scores.csv"
pd.DataFrame({{"dpt_pseudotime": adata.obs.get("dpt_pseudotime", pd.Series(index=adata.obs_names))}}).to_csv(traj_path, index=False)
register_observation("trajectory_effect", target="all_cells", metric="trajectory", value="dpt", method="scanpy.tl.dpt")
register_artifact(str(traj_path), kind="table", summary="Trajectory or fate-bias scores")"""
    if capability_id in {"compare_methods", "composition_test", "co_regulated_modules"}:
        methods = parameters.get("methods") or ["wilcoxon", "t-test"]
        contrast = parameters.get("contrast") or "<contrast>"
        return f"""# Effect exploration / method comparison skeleton.
import pandas as pd

target = {target_expr}
state_col = {state_col_expr}
mode = {repr(mode or '<analysis_mode>')}
methods = {repr(methods)}
contrast = {repr(contrast)}

plan_path = artifacts_dir / f"{{target}}_{{mode}}_plan.txt"
plan_path.write_text(f"target={{target}}\\nmode={{mode}}\\nstate_col={{state_col}}\\n", encoding="utf-8")
register_observation("effect_plan", target=target, metric="mode", value=mode, method="template")
for method in methods:
    register_observation("method_sensitivity", target=target, metric="method", value=method, contrast=contrast, method="template")
register_artifact(str(plan_path), kind="text", summary="Method comparison or effect exploration plan")"""
    if capability_id == "generate_report":
        conclusion_ids = parameters.get("conclusions", [])
        artifact_ids = parameters.get("artifacts", [])
        return f"""# Report skeleton.
import pandas as pd

report_path = artifacts_dir / "perturbseq_report.md"
rows = []
for conclusion_id in {repr(conclusion_ids)}:
    rows.append(f"- conclusion: {{conclusion_id}}")
for artifact_id in {repr(artifact_ids)}:
    rows.append(f"- artifact: {{artifact_id}}")
report_path.write_text("\\n".join([
    "# Perturb-seq report",
    "",
    "## Observations",
    *rows,
]), encoding="utf-8")
register_artifact(str(report_path), kind="report", summary="Perturb-seq report")
register_observation("report_summary", target="run", metric="sections", value="observations/conclusions", method="template")"""
    return generic_template_code()


def generic_template_code() -> str:
    return """# Capability skeleton.
# Inspect active_contract and runtime_symbols, then implement one bounded step.
register_observation("analysis_step", target="<target>", metric="<metric>", value="<value>", method="template")"""
