from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "pertura-paper-ref09-v1"
SNAPSHOT_DATE = "2026-07-15"

EUROPE_PMC_RECORDS = (
    {
        "pmid": "35688146",
        "pmcid": "PMC9380471",
        "doi": "10.1016/j.cell.2022.05.013",
        "title": "Mapping information-rich genotype-phenotype landscapes with genome-scale Perturb-seq.",
        "author_string": "Replogle JM, Saunders RA, Pogson AN, Hussmann JA, Lenail A, Guna A, Mascibroda L, Wagner EJ, Adelman K, Lithwick-Yanai G, Iremadze N, Oberstrass F, Lipson D, Bonnar JL, Jost M, Norman TM, Weissman JS.",
        "journal": "Cell",
        "publication_year": "2022",
    },
    {
        "pmid": "33649593",
        "pmcid": "PMC8011839",
        "doi": "10.1038/s41588-021-00778-2",
        "title": "Characterizing the molecular regulation of inhibitory immune checkpoints with multimodal single-cell screens.",
        "author_string": "Papalexi E, Mimitou EP, Butler AW, Foster S, Bracken B, Mauck WM, Wessels HH, Hao Y, Yeung BZ, Smibert P, Satija R.",
        "journal": "Nat Genet",
        "publication_year": "2021",
    },
    {
        "pmid": "31395745",
        "pmcid": "PMC6746554",
        "doi": "10.1126/science.aax4438",
        "title": "Exploring genetic interaction manifolds constructed from rich single-cell phenotypes.",
        "author_string": "Norman TM, Horlbeck MA, Replogle JM, Ge AY, Xu A, Jost M, Gilbert LA, Weissman JS.",
        "journal": "Science",
        "publication_year": "2019",
    },
    {
        "pmid": "27984732",
        "pmcid": "PMC5181115",
        "doi": "10.1016/j.cell.2016.11.038",
        "title": "Perturb-Seq: Dissecting Molecular Circuits with Scalable Single-Cell RNA Profiling of Pooled Genetic Screens.",
        "author_string": "Dixit A, Parnas O, Li B, Chen J, Fulco CP, Jerby-Arnon L, Marjanovic ND, Dionne D, Burks T, Raychowdhury R, Adamson B, Norman TM, Lander ES, Weissman JS, Friedman N, Regev A.",
        "journal": "Cell",
        "publication_year": "2016",
    },
    {
        "pmid": "28099430",
        "pmcid": "PMC5334791",
        "doi": "10.1038/nmeth.4177",
        "title": "Pooled CRISPR screening with single-cell transcriptome readout.",
        "author_string": "Datlinger P, Rendeiro AF, Schmidl C, Krausgruber T, Traxler P, Klughammer J, Schuster LC, Kuchler A, Alpar D, Bock C.",
        "journal": "Nat Methods",
        "publication_year": "2017",
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_tsv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _literature_reference(output: Path) -> dict[str, Any]:
    records = []
    queries = []
    for index, raw in enumerate(EUROPE_PMC_RECORDS, start=1):
        query_id = f"query_{index:02d}"
        record = dict(raw) | {
            "record_id": f"MED:{raw['pmid']}",
            "source": "europe_pmc",
            "source_class": "curated_prior",
            "query_id": query_id,
            "citation_complete": True,
        }
        records.append(record)
        queries.append(
            {
                "query_id": query_id,
                "query": f"DOI:{raw['doi']}",
                "expected_record_id": record["record_id"],
                "expected_result_count_minimum": 1,
            }
        )
    snapshot = {
        "schema_version": "pertura-europepmc-frozen-snapshot-v1",
        "source": "Europe PMC REST API",
        "api_version": "6.9",
        "host": "www.ebi.ac.uk",
        "snapshot_date": SNAPSHOT_DATE,
        "offline_replay_only": True,
        "coverage_claim": "identity fixture only; not a systematic-review recall benchmark",
        "queries": queries,
        "records": records,
    }
    snapshot_path = output / "europepmc_snapshot.json"
    _write_json(snapshot_path, snapshot)
    table_path = output / "literature_record_reference.tsv"
    _write_tsv(
        table_path,
        [
            "record_id",
            "pmid",
            "pmcid",
            "doi",
            "title",
            "author_string",
            "journal",
            "publication_year",
            "source",
            "source_class",
            "query_id",
            "citation_complete",
        ],
        records,
    )
    return {
        "snapshot_path": snapshot_path,
        "table_path": table_path,
        "record_count": len(records),
        "query_count": len(queries),
    }


def _evidence_reference(output: Path, ref08_hash: str) -> dict[str, Any]:
    results = [
        {
            "result_id": "result_measured_target_a_current",
            "target_uid": "target_A",
            "source_class": "measured_result",
            "current": True,
            "status": "completed",
            "artifact_hash": "sha256:" + "a" * 64,
        },
        {
            "result_id": "result_derived_target_a_current",
            "target_uid": "target_A",
            "source_class": "derived",
            "current": True,
            "status": "completed_with_caution",
            "artifact_hash": ref08_hash,
        },
        {
            "result_id": "result_prediction_target_a_current",
            "target_uid": "target_A",
            "source_class": "prediction",
            "current": True,
            "status": "supported",
            "artifact_hash": "sha256:" + "b" * 64,
        },
        {
            "result_id": "result_measured_target_a_stale",
            "target_uid": "target_A",
            "source_class": "measured_result",
            "current": False,
            "status": "completed",
            "artifact_hash": "sha256:" + "c" * 64,
        },
        {
            "result_id": "result_measured_target_b_current",
            "target_uid": "target_B",
            "source_class": "measured_result",
            "current": True,
            "status": "completed",
            "artifact_hash": "sha256:" + "d" * 64,
        },
    ]
    records = [
        {
            "case_id": "evidence_01",
            "role": "measured",
            "text": "Target A has a measured response in the current evaluation result.",
            "target_uid": "target_A",
            "result_ids": ["result_measured_target_a_current"],
            "literature_ids": [],
            "expected": "accepted",
            "reasons": [],
            "strong_claim_allowed": True,
        },
        {
            "case_id": "evidence_02",
            "role": "derived",
            "text": "Target A is associated with a derived response program.",
            "target_uid": "target_A",
            "result_ids": ["result_derived_target_a_current"],
            "literature_ids": [],
            "expected": "accepted",
            "reasons": [],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_03",
            "role": "prior",
            "text": "Published Perturb-seq studies provide contextual prior support.",
            "target_uid": "target_A",
            "result_ids": [],
            "literature_ids": ["MED:35688146", "MED:27984732"],
            "expected": "accepted",
            "reasons": [],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_04",
            "role": "contradiction",
            "text": "The current measured direction conflicts with the derived program direction.",
            "target_uid": "target_A",
            "result_ids": [
                "result_measured_target_a_current",
                "result_derived_target_a_current",
            ],
            "literature_ids": [],
            "expected": "accepted",
            "reasons": [],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_05",
            "role": "hypothesis",
            "text": "A follow-up experiment should test whether the derived regulator mediates the measured response.",
            "target_uid": "target_A",
            "result_ids": [
                "result_measured_target_a_current",
                "result_derived_target_a_current",
            ],
            "literature_ids": ["MED:35688146"],
            "expected": "accepted",
            "reasons": [],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_06",
            "role": "measured",
            "text": "A prediction proves a measured mechanism for Target A.",
            "target_uid": "target_A",
            "result_ids": ["result_prediction_target_a_current"],
            "literature_ids": [],
            "expected": "rejected",
            "reasons": ["prediction_reclassified_as_measurement"],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_07",
            "role": "measured",
            "text": "A stale result establishes a current Target A measurement.",
            "target_uid": "target_A",
            "result_ids": ["result_measured_target_a_stale"],
            "literature_ids": [],
            "expected": "rejected",
            "reasons": ["stale_result"],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_08",
            "role": "measured",
            "text": "Target B evidence establishes a Target A measurement.",
            "target_uid": "target_A",
            "result_ids": ["result_measured_target_b_current"],
            "literature_ids": [],
            "expected": "rejected",
            "reasons": ["cross_target_evidence"],
            "strong_claim_allowed": False,
        },
        {
            "case_id": "evidence_09",
            "role": "prior",
            "text": "An uncited prior supports the mechanism.",
            "target_uid": "target_A",
            "result_ids": [],
            "literature_ids": [],
            "expected": "rejected",
            "reasons": ["prior_provenance_required"],
            "strong_claim_allowed": False,
        },
    ]
    accepted = [row["case_id"] for row in records if row["expected"] == "accepted"]
    rejected = [row["case_id"] for row in records if row["expected"] == "rejected"]
    payload = {
        "schema_version": "pertura-evidence-map-truth-v1",
        "results": results,
        "records": records,
        "expected_accepted_case_ids": accepted,
        "expected_rejected_case_ids": rejected,
        "contradiction_case_ids": ["evidence_04"],
        "source_classes_unchanged": True,
        "promotion_effect": "none",
        "strong_overclaim_count": 0,
    }
    path = output / "evidence_map_truth.json"
    _write_json(path, payload)
    return {
        "path": path,
        "record_count": len(records),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "contradiction_count": 1,
    }


def _panel_reference(output: Path) -> dict[str, Any]:
    weights = {
        "uncertainty": 0.25,
        "information_gain": 0.25,
        "program_coverage": 0.20,
        "biological_diversity": 0.15,
        "feasibility": 0.15,
    }
    candidates = [
        {"candidate_id": "panel_A", "cost": 2.0, "program": "P1", "effect_size": 0.95, "uncertainty": 0.90, "information_gain": 0.92, "program_coverage": 0.80, "biological_diversity": 0.75, "feasibility": 0.85},
        {"candidate_id": "panel_B", "cost": 1.0, "program": "P2", "effect_size": 0.80, "uncertainty": 0.78, "information_gain": 0.84, "program_coverage": 0.88, "biological_diversity": 0.70, "feasibility": 0.95},
        {"candidate_id": "panel_C", "cost": 1.5, "program": "P3", "effect_size": 0.72, "uncertainty": 0.88, "information_gain": 0.76, "program_coverage": 0.86, "biological_diversity": 0.90, "feasibility": 0.80},
        {"candidate_id": "panel_D", "cost": 2.5, "program": "P1", "effect_size": 1.00, "uncertainty": 0.25, "information_gain": 0.45, "program_coverage": 0.50, "biological_diversity": 0.35, "feasibility": 0.90},
        {"candidate_id": "panel_E", "cost": 1.0, "program": "P4", "effect_size": 0.45, "uncertainty": 0.95, "information_gain": 0.90, "program_coverage": 0.95, "biological_diversity": 0.92, "feasibility": 0.70},
        {"candidate_id": "panel_F", "cost": 2.0, "program": "P2", "effect_size": 0.68, "uncertainty": 0.65, "information_gain": 0.72, "program_coverage": 0.60, "biological_diversity": 0.82, "feasibility": 0.88},
        {"candidate_id": "panel_G", "cost": 0.5, "program": "P3", "effect_size": 0.30, "uncertainty": 0.70, "information_gain": 0.55, "program_coverage": 0.52, "biological_diversity": 0.60, "feasibility": 0.98},
        {"candidate_id": "panel_H", "cost": 1.5, "program": "P4", "effect_size": 0.62, "uncertainty": 0.82, "information_gain": 0.78, "program_coverage": 0.75, "biological_diversity": 0.86, "feasibility": 0.76},
    ]
    budget = 6.0
    for candidate in candidates:
        candidate["utility"] = sum(
            weights[key] * float(candidate[key]) for key in weights
        )
        candidate["utility_per_cost"] = candidate["utility"] / candidate["cost"]
    feasible = []
    for size in range(1, len(candidates) + 1):
        for subset in itertools.combinations(candidates, size):
            cost = sum(item["cost"] for item in subset)
            if cost > budget + 1e-12:
                continue
            programs = sorted({item["program"] for item in subset})
            feasible.append(
                {
                    "selected_ids": sorted(item["candidate_id"] for item in subset),
                    "cost": cost,
                    "utility": sum(item["utility"] for item in subset),
                    "coverage": len(programs) / 4,
                    "maximum_uncertainty": max(item["uncertainty"] for item in subset),
                }
            )
    optimal_utility = max(row["utility"] for row in feasible)
    optima = [
        row for row in feasible if abs(row["utility"] - optimal_utility) <= 1e-12
    ]
    ordered = sorted(
        candidates,
        key=lambda row: (-row["utility_per_cost"], -row["utility"], row["candidate_id"]),
    )
    greedy, used = [], 0.0
    for candidate in ordered:
        if used + candidate["cost"] <= budget + 1e-12:
            greedy.append(candidate)
            used += candidate["cost"]
    effect_only, used_effect = [], 0.0
    for candidate in sorted(candidates, key=lambda row: (-row["effect_size"], row["candidate_id"])):
        if used_effect + candidate["cost"] <= budget + 1e-12:
            effect_only.append(candidate)
            used_effect += candidate["cost"]
    selected = optima[0]
    payload = {
        "schema_version": "pertura-next-panel-reference-v1",
        "source_class": "hypothesis",
        "budget": budget,
        "weights": weights,
        "candidates": candidates,
        "constraints": {
            "unique_candidate_ids": True,
            "positive_finite_cost": True,
            "utility_components_in_unit_interval": True,
            "total_cost_at_most_budget": True,
        },
        "feasible_subset_count": len(feasible),
        "optimal_subsets": optima,
        "selected_oracle": selected,
        "greedy_reference": {
            "selected_ids": sorted(item["candidate_id"] for item in greedy),
            "cost": used,
            "utility": sum(item["utility"] for item in greedy),
        },
        "effect_size_only_baseline": {
            "selected_ids": sorted(item["candidate_id"] for item in effect_only),
            "cost": used_effect,
            "coverage": len({item["program"] for item in effect_only}) / 4,
            "maximum_uncertainty": max(item["uncertainty"] for item in effect_only),
        },
        "oracle_metrics": {
            "constraint_satisfaction": 1.0,
            "coverage": selected["coverage"],
            "uncertainty_capture": selected["maximum_uncertainty"],
            "unsupported_selection_count": 0,
        },
        "claim_boundary": "selected entries are next-experiment hypotheses, not measurements",
    }
    path = output / "next_panel_reference.json"
    _write_json(path, payload)
    return {
        "path": path,
        "candidate_count": len(candidates),
        "feasible_subset_count": len(feasible),
        **payload["oracle_metrics"],
    }


def generate(ref08: Path, output: Path) -> dict[str, Any]:
    ref08_manifest_path = ref08 / "manifest.json"
    if not ref08_manifest_path.is_file():
        raise FileNotFoundError("REF-08 manifest is missing")
    ref08_manifest = json.loads(ref08_manifest_path.read_text(encoding="utf-8"))
    if (
        ref08_manifest.get("reference_pack_id") != "REF-08"
        or ref08_manifest.get("readiness") != "generated"
        or ref08_manifest.get("pending_jobs")
    ):
        raise ValueError("REF-08 must be frozen and complete before REF-09")
    output.mkdir(parents=True, exist_ok=True)
    print("REF-09-A: writing offline Europe PMC identity snapshot", flush=True)
    literature = _literature_reference(output)
    print("REF-09-B: generating planted mixed-source evidence truth", flush=True)
    evidence = _evidence_reference(output, _sha256(ref08_manifest_path))
    print("REF-09-C: enumerating constrained next-panel oracle", flush=True)
    panel = _panel_reference(output)
    output_paths = {
        "europepmc_snapshot.json": literature["snapshot_path"],
        "literature_record_reference.tsv": literature["table_path"],
        "evidence_map_truth.json": evidence["path"],
        "next_panel_reference.json": panel["path"],
    }
    metrics = {
        "record_identity_match": 1.0,
        "citation_completeness": 1.0,
        "source_class_accuracy": 1.0,
        "contradiction_recall": 1.0,
        "strong_overclaim_count": 0,
        "constraint_satisfaction": panel["constraint_satisfaction"],
        "coverage": panel["coverage"],
        "uncertainty_capture": panel["uncertainty_capture"],
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-09",
        "completed_jobs": ["REF-09-A", "REF-09-B", "REF-09-C"],
        "pending_jobs": [],
        "readiness": "generated",
        "independent_of_pertura_results": True,
        "input_files": {"ref08_manifest": _sha256(ref08_manifest_path)},
        "generator_script_sha256": _sha256(Path(__file__).resolve()),
        "output_files": {name: _sha256(path) for name, path in output_paths.items()},
        "counts": {
            "literature_queries": literature["query_count"],
            "literature_records": literature["record_count"],
            "evidence_records": evidence["record_count"],
            "accepted_evidence_records": evidence["accepted_count"],
            "rejected_evidence_records": evidence["rejected_count"],
            "contradiction_records": evidence["contradiction_count"],
            "panel_candidates": panel["candidate_count"],
            "feasible_panel_subsets": panel["feasible_subset_count"],
        },
        "metrics": metrics,
        "parameters": {
            "literature_source": "Europe PMC",
            "snapshot_date": SNAPSHOT_DATE,
            "literature_mode": "offline_identity_fixture",
            "panel_selection": "exhaustive_subset_oracle",
        },
        "limitations": [
            "The Europe PMC snapshot tests stable retrieval identities and citation fields, not systematic-review recall.",
            "Literature records remain curated priors and cannot strengthen measured claims.",
            "Evidence records are planted to test source-class, stale-result, contradiction, and target-scope boundaries.",
            "Panel selections are hypotheses for follow-up experiments and are not measured findings.",
            "A direct PubMed adapter is intentionally deferred to a later capability expansion.",
        ],
    }
    manifest_path = output / "manifest.json"
    _write_json(manifest_path, manifest)
    print("REF-09: manifest written", flush=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_pack_id": "REF-09",
        "readiness": "generated",
        "completed_jobs": manifest["completed_jobs"],
        "pending_jobs": [],
        "passed": True,
        "problems": [],
        "manifest_sha256": _sha256(manifest_path),
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate offline Europe PMC, evidence-map, and panel references."
    )
    parser.add_argument("--ref08", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.ref08.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
