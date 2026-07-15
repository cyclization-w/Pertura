from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DATASETS = {
    "WF-REPL": "replogle_k562_essential_2022",
    "WF-PAPA": "papalexi_thp1_eccite",
    "WF-NORM": "norman_k562_crispra_2019",
    "WF-KANG": "kang18_8vs8_pbmc",
}


def _asset(path: Path, *, role: str, cache: Path, paper_root: Path, kind: str = "external_resource") -> dict[str, Any]:
    resolved = path.resolve()
    roots = {
        "cache": cache.resolve(),
        "paper_root": paper_root.resolve(),
        "benchmark_root": paper_root.resolve().parent,
    }
    for root_name, root in roots.items():
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        return {
            "role": role,
            "root": root_name,
            "relative_path": relative,
            "kind": kind,
        }
    raise ValueError(f"asset is outside benchmark roots: {resolved}")


def build(
    *,
    datasets_path: Path,
    splits_path: Path,
    cache: Path,
    paper_root: Path,
    papalexi_guide_assets: Path,
    papalexi_table_root: Path,
    kang_table_root: Path,
    edger_environment_lock: Path,
    edger_rscript: Path,
    output: Path,
) -> dict[str, Any]:
    datasets = json.loads(datasets_path.read_text(encoding="utf-8"))
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    workflows: dict[str, Any] = {}
    for workflow_id, dataset_id in DATASETS.items():
        artifact = Path(datasets["datasets"][dataset_id]["artifact_path"])
        assets = [
            _asset(artifact, role="primary_h5ad", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(splits_path, role="split_catalog", cache=cache, paper_root=paper_root),
        ]
        for split in ("calibration", "evaluation"):
            record = splits["datasets"][dataset_id][split]
            selection = splits_path.resolve().parent.parent / record["cell_selection_path"]
            assets.append(
                _asset(selection, role=f"{split}_split", cache=cache, paper_root=paper_root)
            )
        workflows[workflow_id] = {"dataset_id": dataset_id, "assets": assets}

    guide = workflows["WF-PAPA"]["assets"]
    guide.extend(
        [
            _asset(papalexi_guide_assets / "guide_matrix", role="guide_matrix", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(papalexi_guide_assets / "guide_map.tsv", role="guide_map", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(papalexi_guide_assets / "cell_metadata.tsv", role="cell_metadata", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(papalexi_table_root / "target_expression.tsv", role="target_expression", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(paper_root / "task_references" / "PAPA-06" / "neutral_inputs" / "pseudobulk_counts.mtx", role="trans_de_pseudobulk_counts", cache=cache, paper_root=paper_root, kind="derived"),
            _asset(paper_root / "task_references" / "PAPA-06" / "neutral_inputs" / "genes.tsv", role="trans_de_genes", cache=cache, paper_root=paper_root, kind="derived"),
            _asset(paper_root / "task_references" / "PAPA-06" / "neutral_inputs" / "sample_manifest.tsv", role="trans_de_sample_manifest", cache=cache, paper_root=paper_root, kind="derived"),
            _asset(paper_root / "task_references" / "PAPA-06" / "neutral_inputs" / "target_eligibility.tsv", role="trans_de_eligibility", cache=cache, paper_root=paper_root, kind="derived"),
            _asset(edger_environment_lock, role="edgeR_environment_lock", cache=cache, paper_root=paper_root, kind="environment_lock"),
            _asset(edger_rscript, role="edgeR_rscript", cache=cache, paper_root=paper_root, kind="executable"),
            _asset(paper_root / "task_references" / "PAPA-07" / "global_effect_evidence.tsv", role="global_effect_evidence", cache=cache, paper_root=paper_root, kind="derived"),
            _asset(paper_root / "task_references" / "PAPA-07" / "global_effect_protocol.json", role="global_effect_protocol", cache=cache, paper_root=paper_root, kind="protocol"),
            _asset(paper_root / "task_references" / "manifest.json", role="global_effect_reference_lock", cache=cache, paper_root=paper_root, kind="reference_lock"),
            _asset(paper_root / "references" / "REF-07" / "gmt_reference.json", role="frozen_gene_sets", cache=cache, paper_root=paper_root, kind="prior"),
            _asset(paper_root / "references" / "REF-09" / "europepmc_snapshot.json", role="literature_snapshot", cache=cache, paper_root=paper_root, kind="prior"),
        ]
    )
    workflows["WF-REPL"]["assets"].extend(
        [
            _asset(Path(datasets["datasets"][DATASETS["WF-REPL"]]["artifact_path"]), role="cell_metadata", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(paper_root / "references" / "REF-07" / "gmt_reference.json", role="frozen_gene_sets", cache=cache, paper_root=paper_root, kind="prior"),
            _asset(paper_root / "references" / "REF-09" / "europepmc_snapshot.json", role="literature_snapshot", cache=cache, paper_root=paper_root, kind="prior"),
        ]
    )
    workflows["WF-NORM"]["assets"].extend(
        [
            _asset(Path(datasets["datasets"][DATASETS["WF-NORM"]]["artifact_path"]), role="construct_metadata", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(paper_root / "references" / "REF-07" / "gmt_reference.json", role="frozen_gene_sets", cache=cache, paper_root=paper_root, kind="prior"),
            _asset(paper_root / "references" / "REF-09" / "europepmc_snapshot.json", role="literature_snapshot", cache=cache, paper_root=paper_root, kind="prior"),
        ]
    )
    workflows["WF-KANG"]["assets"].extend(
        [
            _asset(kang_table_root / "cell_metadata.tsv", role="donor_metadata", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(kang_table_root / "cell_metadata.tsv", role="cell_state_metadata", cache=cache, paper_root=paper_root, kind="observed"),
            _asset(Path(datasets["datasets"][DATASETS["WF-KANG"]]["artifact_path"]), role="raw_counts", cache=cache, paper_root=paper_root, kind="observed"),
        ]
    )
    payload = {
        "schema_version": "pertura-paper-agent-assets-template-v1",
        "source_catalogs": {
            "datasets": str(datasets_path),
            "splits": str(splits_path),
        },
        "workflows": workflows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": "pertura-paper-agent-asset-template-validation-v1",
        "passed": True,
        "workflow_count": len(workflows),
        "asset_count": sum(len(item["assets"]) for item in workflows.values()),
        "output": str(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--papalexi-guide-assets", type=Path, required=True)
    parser.add_argument("--papalexi-table-root", type=Path, required=True)
    parser.add_argument("--kang-table-root", type=Path, required=True)
    parser.add_argument("--edger-environment-lock", type=Path, required=True)
    parser.add_argument("--edger-rscript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build(
        datasets_path=args.datasets.resolve(), splits_path=args.splits.resolve(),
        cache=args.cache.resolve(), paper_root=args.paper_root.resolve(),
        papalexi_guide_assets=args.papalexi_guide_assets.resolve(),
        papalexi_table_root=args.papalexi_table_root.resolve(),
        kang_table_root=args.kang_table_root.resolve(),
        edger_environment_lock=args.edger_environment_lock.resolve(),
        edger_rscript=args.edger_rscript.resolve(), output=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
