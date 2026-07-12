from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from pertura_core import AnalysisStatus, CapabilityRunRequest, CapabilitySpec, DatasetContract
from pertura_core.hashing import canonical_hash
from pertura_workflow.capabilities.candidate_common import (
    blocked, dependency_results, envelope, runtime_dependencies, write_json,
)
from pertura_workflow.capabilities.execution_context import execution_context
from pertura_workflow.environment import environment_prefix, micromamba_path


def run_effect_matrix_assemble(spec, request, contract, staging):
    tables = _effect_tables(staging)
    if not tables:
        return blocked(spec, request, contract, "no committed gene-effect table was resolved")
    identities = {
        (
            str(result.get("result_kind") or ""),
            str((result.get("scope") or {}).get("estimand") or ""),
            str(result.get("capability_id") or ""),
        )
        for result, _ in tables
    }
    result_kinds = {item[0] for item in identities}
    estimands = {item[1] for item in identities}
    capability_ids = {item[2] for item in identities}
    if len(result_kinds) != 1:
        return blocked(
            spec, request, contract,
            "effect matrix inputs mix incompatible scientific result kinds",
        )
    if len(estimands) != 1:
        return blocked(
            spec, request, contract,
            "effect matrix inputs mix incompatible or unresolved estimands",
        )
    if estimands == {""} and len(capability_ids) != 1:
        return blocked(
            spec, request, contract,
            "effect matrix inputs from different methods require an explicit shared estimand",
        )
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    sources: set[str] = set()
    for result, path in tables:
        try:
            rows = _read_csv(path)
        except (OSError, ValueError) as exc:
            return blocked(spec, request, contract, f"invalid effect table {path.name}: {exc}")
        generic_effect = any(
            row.get("effect") not in (None, "")
            and all(row.get(name) in (None, "") for name in (
                "logFC", "log2FC", "log_2_fold_change", "fold_change"
            ))
            for row in rows
        )
        effect_scale = str((result.get("metadata") or {}).get("effect_scale") or "")
        if generic_effect and effect_scale not in {
            "log2_fold_change", "logFC", "log2FC"
        }:
            return blocked(
                spec, request, contract,
                f"generic effect column in {path.name} lacks a compatible signed effect scale",
            )
        scoped = list((result.get("scope") or {}).get("perturbation_ids") or ())
        default = scoped[0] if len(scoped) == 1 else result["result_id"]
        for row in rows:
            gene = _first(row, "gene", "gene_id", "response_id", "feature_id")
            perturbation = _first(
                row, "perturbation", "perturbation_id", "grna_target", "target"
            ) or default
            raw = _value(row, "logFC", "log2FC", "log_2_fold_change", "effect")
            if raw is None and row.get("fold_change") not in (None, ""):
                fold = float(row["fold_change"])
                raw = math.log2(fold) if fold > 0 else None
            if gene and perturbation and raw is not None:
                effect = float(raw)
                if math.isfinite(effect):
                    values[(perturbation, gene)].append(effect)
                    sources.add(result["result_id"])
    perturbations = sorted({key[0] for key in values})
    features = sorted({key[1] for key in values})
    min_p = int(request.parameters.get("min_perturbations", 5))
    min_f = int(request.parameters.get("min_features", 200))
    if len(perturbations) < min_p or len(features) < min_f:
        return blocked(
            spec, request, contract,
            f"effect matrix requires at least {min_p} perturbations and {min_f} features; "
            f"observed {len(perturbations)} and {len(features)}",
        )
    matrix = np.full((len(perturbations), len(features)), np.nan)
    pi = {name: index for index, name in enumerate(perturbations)}
    fi = {name: index for index, name in enumerate(features)}
    conflicts = 0
    for key, observed_values in values.items():
        if len(observed_values) > 1 and max(observed_values) - min(observed_values) > 1e-8:
            conflicts += 1
        matrix[pi[key[0]], fi[key[1]]] = np.mean(observed_values)
    if conflicts:
        return blocked(
            spec, request, contract,
            f"committed inputs disagree for {conflicts} perturbation-feature entries",
        )
    observed = np.isfinite(matrix)
    bundle = staging / "effect_matrix.npz"
    np.savez_compressed(
        bundle, effects=np.nan_to_num(matrix), observed_mask=observed,
        perturbations=np.asarray(perturbations), features=np.asarray(features),
    )
    long_table = staging / "effect_matrix_long.csv"
    _write_csv(
        long_table, ("perturbation_id", "feature_id", "effect", "observed"),
        (
            {
                "perturbation_id": p, "feature_id": g,
                "effect": format(matrix[i, j], ".17g") if observed[i, j] else "",
                "observed": int(observed[i, j]),
            }
            for i, p in enumerate(perturbations)
            for j, g in enumerate(features)
        ),
    )
    missing = 1.0 - float(observed.mean())
    manifest_payload = {
        "schema_version": "pertura-effect-matrix-contract-v0",
        "effect_scale": "log2_fold_change",
        "shape": [len(perturbations), len(features)],
        "missing_values_are_zero": False,
        "missing_fraction": missing,
        "source_result_ids": sorted(sources),
        "source_result_kind": next(iter(result_kinds)),
        "estimand": next(iter(estimands)) or None,
        "source_capability_ids": sorted(capability_ids),
        "conflicting_cell_count": conflicts,
    }
    manifest_payload["scientific_hash"] = canonical_hash(manifest_payload)
    manifest = write_json(staging, "effect_matrix_manifest.json", manifest_payload)
    cautions = []
    if missing > float(request.parameters.get("missing_fraction_caution", 0.25)):
        cautions.append("effect matrix contains substantial structural missingness")
    return envelope(
        spec, request, contract,
        status=AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed,
        summary=f"Assembled a {len(perturbations)} by {len(features)} signed effect matrix.",
        cautions=cautions,
        metrics={"n_perturbations": len(perturbations), "n_features": len(features),
                 "missing_fraction": missing, "conflicting_cell_count": conflicts},
        outputs=(bundle, long_table, manifest),
        metadata={"effect_scale": "log2_fold_change", "derived_only": True},
    )


def run_response_signed_nmf(spec, request, contract, staging):
    loaded = _load_matrix(staging)
    if isinstance(loaded, str):
        return blocked(spec, request, contract, loaded)
    matrix, observed, perturbations, features, source = loaded
    coverage = observed.mean(axis=0)
    keep = coverage >= float(request.parameters.get("minimum_feature_coverage", 0.8))
    if not np.any(keep):
        return blocked(spec, request, contract, "no feature meets response-program coverage")
    matrix, observed, features = matrix[:, keep], observed[:, keep], features[keep]
    matrix = _impute(matrix, observed)
    ranks = sorted({
        int(value) for value in request.parameters.get("ranks", [5, 10, 15, 20])
        if 2 <= int(value) < min(matrix.shape)
    })
    if not ranks:
        return blocked(spec, request, contract, "effect matrix is too small for signed NMF")
    try:
        from sklearn.decomposition import NMF
    except ImportError:
        return blocked(spec, request, contract, "interpretation-v1 scikit-learn is missing")
    split = np.concatenate([np.maximum(matrix, 0), np.maximum(-matrix, 0)], axis=1)
    n_seeds = int(request.parameters.get("n_seeds", 5))
    seed = int(request.parameters.get("seed", 1729))
    threshold = float(request.parameters.get("stability_threshold", 0.80))
    if n_seeds < 2:
        return blocked(spec, request, contract, "response-program NMF requires at least two seeds")
    if not 0.0 <= threshold <= 1.0:
        return blocked(spec, request, contract, "stability_threshold must be in [0, 1]")
    fits, candidates = {}, []
    for rank in ranks:
        runs = []
        for offset in range(n_seeds):
            model = NMF(
                n_components=rank, init="nndsvda", random_state=seed + offset,
                max_iter=int(request.parameters.get("max_iter", 1000)), tol=1e-5,
            )
            activity = model.fit_transform(split)
            error = model.reconstruction_err_ / max(np.linalg.norm(split), 1e-12)
            runs.append((activity, model.components_, float(error)))
        fits[rank] = runs
        stability = _stability([item[1] for item in runs])
        candidates.append({
            "rank": rank, "stability": stability,
            "normalized_reconstruction_error": float(np.mean([item[2] for item in runs])),
            "stable": stability >= threshold,
        })
    stable = [item for item in candidates if item["stable"]]
    if stable:
        chosen_meta = min(stable, key=lambda x: (x["normalized_reconstruction_error"], x["rank"]))
        status, cautions = AnalysisStatus.completed, ()
    else:
        chosen_meta = max(candidates, key=lambda x: (x["stability"], -x["rank"]))
        status = AnalysisStatus.completed_with_caution
        cautions = ("no candidate rank reached consensus stability 0.80",)
    rank = int(chosen_meta["rank"])
    activity, split_loadings, _ = min(fits[rank], key=lambda x: x[2])
    loadings = split_loadings[:, :matrix.shape[1]] - split_loadings[:, matrix.shape[1]:]
    program_ids = np.asarray([f"response_program_{i + 1:03d}" for i in range(rank)])
    bundle = staging / "response_programs.npz"
    np.savez_compressed(
        bundle, activity=activity, signed_gene_loadings=loadings,
        perturbations=perturbations, features=features, program_ids=program_ids,
    )
    table = staging / "response_program_loadings.csv"
    _write_csv(
        table, ("program_id", "feature_id", "signed_loading"),
        ({"program_id": program_ids[i], "feature_id": gene,
          "signed_loading": format(loadings[i, j], ".17g")}
         for i in range(rank) for j, gene in enumerate(features)),
    )
    manifest = write_json(staging, "response_program_manifest.json", {
        "schema_version": "pertura-response-program-contract-v0",
        "kind": "response_program",
        "reference_module_kind": "reference_state_module",
        "selected_rank": rank, "rank_candidates": candidates,
        "source_effect_result_id": source.get("result_id"),
        "uses_perturbation_labels": True,
        "independent_confirmation_allowed": False,
        "seed_sequence": [seed + i for i in range(n_seeds)],
    })
    return envelope(
        spec, request, contract, status=status,
        summary=f"Learned {rank} signed response programs from committed effects.",
        cautions=cautions,
        metrics={"selected_rank": rank, "consensus_stability": chosen_meta["stability"],
                 "normalized_reconstruction_error": chosen_meta["normalized_reconstruction_error"]},
        outputs=(bundle, table, manifest),
        metadata={"module_kind": "response_program",
                  "independent_confirmation_allowed": False, "derived_only": True},
    )


def run_perturbation_cluster(spec, request, contract, staging):
    loaded = _load_matrix(staging)
    if isinstance(loaded, str):
        return blocked(spec, request, contract, loaded)
    matrix, observed, perturbations, _, source = loaded
    if len(perturbations) < 3:
        return blocked(spec, request, contract, "at least three perturbations are required")
    matrix = _impute(matrix, observed)
    try:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import pdist, squareform
        from sklearn.metrics import silhouette_score
    except ImportError:
        return blocked(spec, request, contract, "interpretation-v1 clustering dependencies are missing")
    distances = pdist(matrix, metric="correlation")
    if not np.all(np.isfinite(distances)):
        return blocked(spec, request, contract, "correlation distance is undefined")
    tree = linkage(distances, method="average")
    candidates = []
    for k in range(2, min(10, len(perturbations) - 1) + 1):
        labels = fcluster(tree, k, criterion="maxclust")
        if len(set(labels)) > 1:
            score = silhouette_score(squareform(distances), labels, metric="precomputed")
            candidates.append((float(score), k, labels))
    if not candidates:
        return blocked(spec, request, contract, "no non-degenerate clustering was found")
    silhouette, selected_k, labels = max(candidates, key=lambda x: (x[0], -x[1]))
    n_boot = int(request.parameters.get("bootstraps", 100))
    if n_boot < 10:
        return blocked(spec, request, contract, "cluster stability requires at least 10 bootstraps")
    rng = np.random.default_rng(int(request.parameters.get("seed", 1729)))
    same, valid = np.zeros((len(labels), len(labels))), 0
    for _ in range(n_boot):
        sampled = matrix[:, rng.integers(0, matrix.shape[1], matrix.shape[1])]
        distance = pdist(sampled, metric="correlation")
        if np.all(np.isfinite(distance)):
            current = fcluster(linkage(distance, method="average"), selected_k, criterion="maxclust")
            same += current[:, None] == current[None, :]
            valid += 1
    if valid:
        same /= valid
    within = [same[i, j] for i in range(len(labels)) for j in range(i + 1, len(labels))
              if labels[i] == labels[j]]
    stability = float(np.mean(within)) if within else 0.0
    table = staging / "perturbation_clusters.csv"
    _write_csv(
        table, ("perturbation_id", "cluster_id"),
        ({"perturbation_id": name, "cluster_id": f"cluster_{int(label):03d}"}
         for name, label in zip(perturbations, labels)),
    )
    manifest = write_json(staging, "perturbation_cluster_manifest.json", {
        "schema_version": "pertura-perturbation-cluster-v0",
        "distance": "correlation", "linkage": "average",
        "selected_k": selected_k, "silhouette": silhouette,
        "bootstrap_stability": stability, "valid_bootstraps": valid,
        "source_effect_result_id": source.get("result_id"),
    })
    cautions = () if stability >= 0.70 else (
        "perturbation clusters are unstable under feature bootstrap",
    )
    return envelope(
        spec, request, contract,
        status=AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed,
        summary=f"Clustered {len(perturbations)} perturbations into {selected_k} groups.",
        cautions=cautions,
        metrics={"n_clusters": selected_k, "silhouette": silhouette,
                 "bootstrap_stability": stability},
        outputs=(table, manifest), metadata={"derived_only": True},
    )


def run_enrichment_ora(spec, request, contract, staging):
    loaded = _load_matrix(staging)
    if isinstance(loaded, str):
        return blocked(spec, request, contract, loaded)
    matrix, observed, perturbations, features, _ = loaded
    gene_sets = _load_gene_sets(staging)
    if isinstance(gene_sets, str):
        return blocked(spec, request, contract, gene_sets)
    try:
        from scipy.stats import fisher_exact
    except ImportError:
        return blocked(spec, request, contract, "interpretation-v1 scipy is missing")
    minimum = int(request.parameters.get("min_gene_set_size", 10))
    maximum = int(request.parameters.get("max_gene_set_size", 500))
    threshold = float(request.parameters.get("effect_threshold", 0.5))
    rows = []
    for i, perturbation in enumerate(perturbations):
        universe = {features[j] for j in np.flatnonzero(observed[i])}
        for direction, selected in (
            ("up", {features[j] for j in np.flatnonzero(observed[i] & (matrix[i] >= threshold))}),
            ("down", {features[j] for j in np.flatnonzero(observed[i] & (matrix[i] <= -threshold))}),
        ):
            for name, genes in gene_sets.items():
                active = genes & universe
                overlap = len(selected & active)
                if not selected or not minimum <= len(active) <= maximum or not overlap:
                    continue
                background = universe - selected
                odds, pvalue = fisher_exact(
                    [[overlap, len(selected - active)],
                     [len(background & active), len(background - active)]],
                    alternative="greater",
                )
                rows.append({
                    "perturbation_id": perturbation, "direction": direction,
                    "gene_set": name, "overlap": overlap,
                    "selected_size": len(selected), "gene_set_size": len(active),
                    "odds_ratio": odds, "PValue": pvalue,
                    "tested_universe_size": len(universe),
                })
    _group_bh(rows, ("perturbation_id", "direction"), "PValue", "FDR")
    output = staging / "ora_results.csv"
    fields = ("perturbation_id", "direction", "gene_set", "overlap", "selected_size",
              "gene_set_size", "odds_ratio", "PValue", "FDR", "tested_universe_size")
    _write_csv(output, fields, rows)
    manifest = write_json(staging, "ora_manifest.json", {
        "schema_version": "pertura-ora-v0", "test": "fisher_exact_greater",
        "multiplicity": "BH within perturbation and direction",
        "universe_policy": "genes tested for the same perturbation",
        "effect_threshold": threshold, "gene_set_count": len(gene_sets),
    })
    caution = () if rows else ("no pathway passed overlap and size filters",)
    return envelope(
        spec, request, contract,
        status=AnalysisStatus.completed_with_caution if caution else AnalysisStatus.completed,
        summary=f"Computed direction-aware ORA for {len(perturbations)} perturbations.",
        cautions=caution, metrics={"n_tests": len(rows), "n_gene_sets": len(gene_sets)},
        outputs=(output, manifest),
        metadata={"derived_only": True, "knowledge_resource_bound": True},
    )


def run_enrichment_gsea_prerank(spec, request, contract, staging):
    matrix = _dependency_file(staging, "effect_matrix.npz")
    gene_sets = _gene_set_path(staging)
    if matrix is None:
        return blocked(spec, request, contract, "committed effect matrix is missing")
    if isinstance(gene_sets, str):
        return blocked(spec, request, contract, gene_sets)
    config = write_json(staging, "gsea_prerank_config.json", {
        "schema_version": "pertura-gsea-prerank-config-v1",
        "effect_matrix_path": str(matrix), "gene_sets_path": str(gene_sets),
        "output_path": str(staging / "gsea_prerank_results.csv"),
        "permutation_num": int(request.parameters.get("permutation_num", 10000)),
        "min_size": int(request.parameters.get("min_gene_set_size", 10)),
        "max_size": int(request.parameters.get("max_gene_set_size", 500)),
        "seed": int(request.parameters.get("seed", 1729)), "threads": 1,
    })
    completed = _run_profile("gsea_prerank_runner.py", config, spec.timeout_seconds)
    if isinstance(completed, str):
        return blocked(spec, request, contract, completed)
    if completed.returncode:
        return blocked(spec, request, contract, "GSEApy runner failed: " + completed.stderr[-2000:])
    output = staging / "gsea_prerank_results.csv"
    if not output.is_file():
        return blocked(spec, request, contract, "GSEApy runner returned no result table")
    rows = _read_csv(output)
    if not rows or not {"perturbation_id", "gene_set", "NES", "PValue", "FDR"}.issubset(rows[0]):
        return blocked(spec, request, contract, "GSEApy output schema is incomplete")
    seen = set()
    for row in rows:
        identity = (row["perturbation_id"], row["gene_set"])
        if identity in seen:
            return blocked(spec, request, contract, "GSEApy output contains duplicate tests")
        seen.add(identity)
        try:
            nes, pvalue, fdr = float(row["NES"]), float(row["PValue"]), float(row["FDR"])
        except (TypeError, ValueError):
            return blocked(spec, request, contract, "GSEApy output contains invalid numeric values")
        if not math.isfinite(nes) or not 0 <= pvalue <= 1 or not 0 <= fdr <= 1:
            return blocked(spec, request, contract, "GSEApy output contains non-finite or invalid values")
    return envelope(
        spec, request, contract, status=AnalysisStatus.completed_with_caution,
        summary=f"Computed {len(rows)} preranked GSEA tests.",
        cautions=("GSEA adapter is synthetic-only validated pending server benchmark",),
        metrics={"n_tests": len(rows)}, outputs=(config, output),
        metadata={"method": "gseapy_1.3.0", "derived_only": True},
    )


def run_regulator_activity_ulm(spec, request, contract, staging):
    matrix = _dependency_file(staging, "effect_matrix.npz")
    network = _resource_artifact(staging, "collectri_")
    if matrix is None or network is None:
        return blocked(spec, request, contract, "effect matrix or locked CollecTRI resource is missing")
    config = write_json(staging, "ulm_config.json", {
        "schema_version": "pertura-ulm-config-v1",
        "effect_matrix_path": str(matrix), "network_path": str(network),
        "output_path": str(staging / "regulator_activity.csv"),
        "minimum_targets": int(request.parameters.get("minimum_targets", 5)),
    })
    completed = _run_profile("ulm_runner.py", config, spec.timeout_seconds)
    if isinstance(completed, str):
        return blocked(spec, request, contract, completed)
    if completed.returncode:
        return blocked(spec, request, contract, "decoupler ULM runner failed: " + completed.stderr[-2000:])
    output = staging / "regulator_activity.csv"
    if not output.is_file():
        return blocked(spec, request, contract, "decoupler ULM returned no result table")
    rows = _read_csv(output)
    if not rows or not {"perturbation_id", "regulator", "activity", "FDR"}.issubset(rows[0]):
        return blocked(spec, request, contract, "decoupler ULM output schema is incomplete")
    seen = set()
    for row in rows:
        identity = (row["perturbation_id"], row["regulator"])
        if identity in seen:
            return blocked(spec, request, contract, "decoupler ULM output contains duplicate tests")
        seen.add(identity)
        try:
            activity, fdr = float(row["activity"]), float(row["FDR"])
        except (TypeError, ValueError):
            return blocked(spec, request, contract, "decoupler ULM output contains invalid values")
        if not math.isfinite(activity) or not 0 <= fdr <= 1:
            return blocked(spec, request, contract, "decoupler ULM output contains non-finite or invalid values")
    return envelope(
        spec, request, contract, status=AnalysisStatus.completed_with_caution,
        summary=f"Estimated {len(rows)} perturbation-regulator activities.",
        cautions=("regulator activity is derived and is not direct regulation",),
        metrics={"n_tests": len(rows)}, outputs=(config, output),
        metadata={"method": "decoupler_ulm_2.1.6", "derived_only": True},
    )


def run_perturbation_regulator_network(spec, request, contract, staging):
    activity = _dependency_file(staging, "regulator_activity.csv")
    if activity is None:
        return blocked(spec, request, contract, "regulator activity result is missing")
    alpha = float(request.parameters.get("fdr_threshold", 0.05))
    threshold = float(request.parameters.get("activity_threshold", 0.0))
    edges = []
    for row in _read_csv(activity):
        try:
            score, fdr = float(row["activity"]), float(row["FDR"])
        except (KeyError, ValueError):
            return blocked(spec, request, contract, "regulator activity has invalid values")
        if fdr <= alpha and abs(score) >= threshold:
            edges.append({
                "source_perturbation": row["perturbation_id"],
                "target_regulator": row["regulator"], "signed_activity": score,
                "FDR": fdr, "edge_role": "derived_hypothesis",
            })
    table = staging / "perturbation_regulator_network.csv"
    _write_csv(table, ("source_perturbation", "target_regulator", "signed_activity",
                       "FDR", "edge_role"), edges)
    manifest = write_json(staging, "perturbation_regulator_network.json", {
        "schema_version": "pertura-perturbation-regulator-network-v0",
        "edge_count": len(edges), "edge_role": "hypothesis",
        "causal_interpretation_allowed": False,
    })
    return envelope(
        spec, request, contract, status=AnalysisStatus.completed_with_caution,
        summary=f"Constructed {len(edges)} prior-grounded regulator hypotheses.",
        cautions=("network edges are hypotheses and are not causal measurements",),
        metrics={"n_edges": len(edges)}, outputs=(table, manifest),
        metadata={"hypothesis_only": True},
    )


def run_literature_europepmc(spec, request, contract, staging):
    policy = execution_context().get("network_policy") or {}
    if spec.capability_id not in set(policy.get("allowed_capabilities") or ()):
        return blocked(spec, request, contract, "literature network access was not authorized")
    if "www.ebi.ac.uk" not in set(policy.get("allowed_hosts") or ()):
        return blocked(spec, request, contract, "Europe PMC host is not allowlisted")
    query = str(request.parameters.get("query") or "").strip()
    if not query:
        return blocked(spec, request, contract, "Europe PMC query is required")
    page_size = min(100, max(1, int(request.parameters.get("max_records", 25))))
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
        {"query": query, "format": "json", "pageSize": page_size}
    )
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                     "User-Agent": "pertura-literature/0.2"})
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.load(response)
    except Exception as exc:
        return blocked(spec, request, contract, f"Europe PMC request failed: {exc}")
    records = [{
        "pmid": item.get("pmid"), "pmcid": item.get("pmcid"), "doi": item.get("doi"),
        "title": item.get("title"), "author_string": item.get("authorString"),
        "journal": item.get("journalTitle"), "publication_year": item.get("pubYear"),
        "cited_by_count": item.get("citedByCount"), "source": "europe_pmc",
    } for item in (payload.get("resultList") or {}).get("result") or ()]
    query_payload = {
        "schema_version": "pertura-literature-query-v0",
        "api_version": "Europe PMC REST 6.9", "query": query,
        "page_size": page_size, "host": "www.ebi.ac.uk",
    }
    query_payload["query_hash"] = canonical_hash(query_payload)
    query_path = write_json(staging, "literature_query.json", query_payload)
    record_payload = {
        "schema_version": "pertura-literature-record-set-v0",
        "query_hash": query_payload["query_hash"], "records": records,
        "record_count": len(records),
    }
    record_payload["content_hash"] = canonical_hash(record_payload)
    records_path = write_json(staging, "literature_records.json", record_payload)
    return envelope(
        spec, request, contract, status=AnalysisStatus.completed_with_caution,
        summary=f"Retrieved {len(records)} Europe PMC records for an opt-in query.",
        cautions=("literature records are curated priors and cannot strengthen measurements",),
        metrics={"n_records": len(records)}, outputs=(query_path, records_path),
        metadata={"network_access": "explicit_opt_in", "host": "www.ebi.ac.uk",
                  "api_version": "6.9", "source_role": "curated_prior"},
    )


def run_interpretation_evidence_map(spec, request, contract, staging):
    proposed = request.parameters.get("records")
    if not isinstance(proposed, list) or not proposed:
        return blocked(spec, request, contract, "structured interpretation records are required")
    available = {
        item["result_id"]: item for item in dependency_results(staging)
    }
    roles = {"measured", "derived", "prior", "contradiction", "hypothesis", "next_experiment"}
    accepted, rejected = [], []
    for index, record in enumerate(proposed):
        if not isinstance(record, dict):
            rejected.append({"index": index, "reasons": ["record_not_object"]})
            continue
        role, text = str(record.get("role") or ""), str(record.get("text") or "").strip()
        result_ids = tuple(str(item) for item in record.get("result_ids") or ())
        literature_ids = tuple(str(item) for item in record.get("literature_ids") or ())
        reasons = []
        if role not in roles:
            reasons.append("invalid_role")
        if not text:
            reasons.append("empty_text")
        if any(item not in available for item in result_ids):
            reasons.append("unknown_result_reference")
        referenced = [available[item] for item in result_ids if item in available]
        source_classes = {str(item.get("source_class") or "") for item in referenced}
        if role in {"measured", "derived", "contradiction"} and not result_ids:
            reasons.append("result_provenance_required")
        if role == "measured" and source_classes != {"measured_result"}:
            reasons.append("measured_role_requires_measured_results")
        if role == "derived" and source_classes & {"prediction", "hypothesis"}:
            reasons.append("derived_role_cannot_reclassify_prediction_or_hypothesis")
        if role == "prior" and not (
            literature_ids or "curated_prior" in source_classes
        ):
            reasons.append("prior_provenance_required")
        if role in {"hypothesis", "next_experiment"} and not (result_ids or literature_ids):
            reasons.append("antecedent_required")
        if reasons:
            rejected.append({"index": index, "reasons": reasons})
        else:
            accepted.append({
                "record_id": f"interpretation_{index:04d}", "role": role, "text": text,
                "result_ids": list(result_ids), "literature_ids": list(literature_ids),
                "limitations": list(record.get("limitations") or ()),
                "role_assignment": "explicit_structured_input", "promotion_effect": "none",
            })
    if not accepted:
        return blocked(spec, request, contract, "all interpretation records failed provenance validation")
    output = write_json(staging, "interpretation_evidence_map.json", {
        "schema_version": "pertura-interpretation-evidence-map-v0",
        "records": accepted, "rejected_records": rejected,
        "source_classes_unchanged": True, "promotion_effect": "none",
    })
    cautions = (f"{len(rejected)} interpretation records were rejected",) if rejected else ()
    return envelope(
        spec, request, contract,
        status=AnalysisStatus.completed_with_caution if cautions else AnalysisStatus.completed,
        summary=f"Grounded {len(accepted)} interpretation records to committed provenance.",
        cautions=cautions,
        metrics={"accepted_records": len(accepted), "rejected_records": len(rejected)},
        outputs=(output,), metadata={"promotion_effect": "none", "hypothesis_ceiling": True},
    )


def _effect_tables(staging):
    names = {"edger_results.csv", "sceptre_results.csv", "effect_table.csv", "gene_effects.csv"}
    found = []
    for result in dependency_results(staging):
        if result.get("result_kind") not in {
            "differential_expression", "conditional_association", "gene_effect",
            "guide_target_sensitivity",
        }:
            continue
        for value in result.get("local_output_paths") or ():
            path = Path(value)
            if path.is_file() and path.name in names:
                found.append((result, path))
    return found


def _load_matrix(staging):
    path = _dependency_file(staging, "effect_matrix.npz")
    source = next((item for item in dependency_results(staging)
                   if item.get("result_kind") == "effect_matrix"), {})
    if path is None:
        return "committed effect matrix dependency is missing"
    try:
        data = np.load(path, allow_pickle=False)
        matrix = np.asarray(data["effects"], float)
        observed = np.asarray(data["observed_mask"], bool)
        perturbations = np.asarray(data["perturbations"], str)
        features = np.asarray(data["features"], str)
    except (OSError, KeyError, ValueError) as exc:
        return f"effect matrix bundle is invalid: {exc}"
    if matrix.shape != observed.shape or matrix.shape != (len(perturbations), len(features)):
        return "effect matrix dimensions are inconsistent"
    if not np.all(np.isfinite(matrix)):
        return "effect matrix contains non-finite stored values"
    return matrix, observed, perturbations, features, source


def _dependency_file(staging, name):
    for result in dependency_results(staging):
        for value in result.get("local_output_paths") or ():
            path = Path(value)
            if path.is_file() and path.name == name:
                return path
    return None


def _resource_artifact(staging, prefix):
    for dependency in runtime_dependencies(staging):
        if dependency.get("kind") != "knowledge_resource":
            continue
        payload = dependency.get("payload") or {}
        directory = Path(str(payload.get("resource_dir") or ""))
        for artifact in payload.get("artifacts") or ():
            if str(artifact.get("artifact_id") or "").startswith(prefix):
                path = directory / str(artifact.get("relative_path") or "")
                if path.is_file():
                    return path
    return None


def _gene_set_path(staging):
    module_path = _dependency_file(staging, "gmt_modules.json")
    if module_path is not None:
        payload = json.loads(module_path.read_text(encoding="utf-8"))
        gmt = staging / "_derived_gene_sets.gmt"
        modules = payload.get("modules") or {}
        if isinstance(modules, dict):
            items = [(str(name), list(genes)) for name, genes in modules.items()]
        else:
            items = [
                (
                    str(module.get("name") or module.get("module_id")),
                    list(module.get("genes") or ()),
                )
                for module in modules
            ]
        lines = [
            "\t".join([name, "Pertura imported module", *[str(gene) for gene in genes]])
            for name, genes in items
        ]
        gmt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return gmt
    artifact = _resource_artifact(staging, "reactome_pathways")
    if artifact is not None and artifact.suffix.lower() == ".zip":
        with zipfile.ZipFile(artifact) as archive:
            members = sorted(name for name in archive.namelist() if name.lower().endswith(".gmt"))
            if not members:
                return "Reactome resource archive contains no GMT"
            target = staging / "_reactome.gmt"
            target.write_bytes(archive.read(members[0]))
            return target
    if artifact is not None:
        return artifact
    return "no committed module reference or locked pathway resource was resolved"


def _load_gene_sets(staging):
    path = _gene_set_path(staging)
    if isinstance(path, str):
        return path
    sets = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                sets[parts[0]] = set(parts[2:])
    return sets if sets else "gene-set resource contains no usable sets"


def _read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t" if path.suffix == ".tsv" else ",")
        if not reader.fieldnames:
            raise ValueError("missing header")
        return [dict(row) for row in reader]


def _write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _value(row, *names):
    return next((row[name] for name in names if row.get(name) not in (None, "")), None)


def _first(row, *names):
    value = _value(row, *names)
    return str(value).strip() if value not in (None, "") else ""


def _stability(loadings):
    if len(loadings) < 2:
        return 1.0
    from scipy.optimize import linear_sum_assignment

    reference, scores = loadings[0], []
    for current in loadings[1:]:
        ref = reference / np.maximum(np.linalg.norm(reference, axis=1, keepdims=True), 1e-12)
        cur = current / np.maximum(np.linalg.norm(current, axis=1, keepdims=True), 1e-12)
        similarity = np.abs(ref @ cur.T)
        rows, columns = linear_sum_assignment(-similarity)
        scores.extend(float(similarity[row, column]) for row, column in zip(rows, columns))
    return float(np.mean(scores)) if scores else 0.0


def _impute(matrix, observed):
    counts = observed.sum(axis=0)
    means = np.divide(np.where(observed, matrix, 0).sum(axis=0), counts,
                      out=np.zeros(matrix.shape[1]), where=counts > 0)
    return np.where(observed, matrix, means)


def _group_bh(rows, group_columns, p_column, output_column):
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[tuple(row[column] for column in group_columns)].append(index)
    for indices in groups.values():
        values = [float(rows[index][p_column]) for index in indices]
        order, adjusted, running = np.argsort(values), [1.0] * len(values), 1.0
        for rank in reversed(range(len(order))):
            original = int(order[rank])
            running = min(running, values[original] * len(values) / (rank + 1))
            adjusted[original] = min(1.0, running)
        for local, row_index in enumerate(indices):
            rows[row_index][output_column] = adjusted[local]


def _run_profile(runner_name, config, timeout):
    binary, prefix = micromamba_path(), environment_prefix("interpretation-v1")
    if not binary.is_file() or not prefix.is_dir():
        return "interpretation-v1 environment is missing"
    runner = resources.files("pertura_workflow.capabilities").joinpath("runners", runner_name)
    env = {key: os.environ[key] for key in (
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "PATH"
    ) if key in os.environ}
    return subprocess.run(
        [str(binary), "run", "--prefix", str(prefix), "python", str(runner), str(config)],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        timeout=timeout, check=False, env=env,
    )
