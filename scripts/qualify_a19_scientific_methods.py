from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from pertura_bench.paper_task_evaluation import evaluate_paper_task
from pertura_bench.paper_tasks import load_paper_task_catalog
from pertura_core.hashing import canonical_hash, file_sha256, path_sha256


_BOUND_PARITY_TASKS = frozenset(
    {"PAPA-02", "PAPA-03", "PAPA-04", "PAPA-05", "KANG-02"}
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _task_map(path: Path) -> dict[str, Mapping[str, Any]]:
    catalog = load_paper_task_catalog(path)
    return {str(task["task_id"]): task for task in catalog.tasks()}


def _reference_map(path: Path) -> dict[str, Mapping[str, Any]]:
    payload = _read_json(path)
    return {
        str(binding["task_id"]): binding
        for binding in payload.get("bindings") or ()
    }


def _benchmark_result(task: Mapping[str, Any], *, analysis_unit: str) -> dict[str, Any]:
    return {
        "schema_version": "pertura-agent-benchmark-result-v1",
        "case_id": str(task["task_id"]),
        "dataset_id": str(task.get("dataset_id") or "qualification"),
        "result_type": "scientific_method_parity",
        "analysis_unit": analysis_unit,
        "status": "completed",
        "findings": [
            {
                "finding_id": "method-parity",
                "text": (
                    "Donor-paired pseudobulk and composition execution parity."
                    if str(task["task_id"]).startswith("KANG")
                    else "Replicate-paired pseudobulk execution parity."
                ),
                "metric_ids": [],
                "artifact_roles": list(task.get("required_artifact_roles") or ()),
            }
        ],
        "metrics": {},
        "limitations": [
            "Internal real-output method qualification; not a paper result."
        ],
        "artifact_roles": list(task.get("required_artifact_roles") or ()),
    }


def _asset_paths(asset_catalog: Path, workflow_id: str, paper_root: Path) -> dict[str, Path]:
    payload = _read_json(asset_catalog)
    workflow = dict((payload.get("workflows") or {}).get(workflow_id) or {})
    roots = {
        "cache": Path(os.environ.get("PERTURA_BENCH_CACHE") or paper_root.parent / "cache"),
        "paper_root": paper_root,
        "benchmark_root": paper_root.parent,
    }
    paths: dict[str, Path] = {}
    for asset in workflow.get("assets") or ():
        role = str(asset.get("role") or "")
        root_name = str(asset.get("root") or "")
        relative = str(asset.get("relative_path") or "")
        if not role or root_name not in roots or not relative:
            raise ValueError(f"{workflow_id}: malformed bound asset record")
        base = roots[root_name].resolve()
        path = (base / relative).resolve()
        if path != base and base not in path.parents:
            raise ValueError(f"{workflow_id}/{role}: asset escapes {root_name}")
        if not path.exists() or path_sha256(path) != str(asset.get("content_sha256") or ""):
            raise ValueError(f"{workflow_id}/{role}: bound asset is missing or drifted")
        paths[role] = path
    return paths


def _run_locked(
    command: list[str],
    *,
    repo: Path,
    timeout: int,
) -> None:
    environment = dict(os.environ)
    environment.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    completed = subprocess.run(
        command,
        cwd=repo,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"locked skill execution failed ({completed.returncode}): "
            + (completed.stderr or completed.stdout)[-4000:]
        )


def _evaluate(
    *,
    task: Mapping[str, Any],
    binding: Mapping[str, Any],
    output: Path,
    paper_root: Path,
    analysis_unit: str,
) -> dict[str, Any]:
    return evaluate_paper_task(
        task,
        benchmark_result=_benchmark_result(task, analysis_unit=analysis_unit),
        task_output_root=output,
        paper_root=paper_root,
        bindings=[binding],
    )


def _require_papa06_environment_parity(
    skill_manifest: Mapping[str, Any],
    reference_manifest: Mapping[str, Any],
) -> None:
    skill_versions = dict(skill_manifest.get("versions") or {})
    reference_versions = dict(reference_manifest.get("versions") or {})
    for name in ("R", "edgeR", "Matrix"):
        if not skill_versions.get(name) or not reference_versions.get(name):
            raise RuntimeError(
                f"PAPA-06 skill/reference {name} environment version is missing"
            )
        if str(skill_versions[name]) != str(reference_versions[name]):
            raise RuntimeError(
                f"PAPA-06 skill/reference {name} environment versions differ"
            )


def _run_papa06(
    *,
    repo: Path,
    task: Mapping[str, Any],
    binding: Mapping[str, Any],
    assets: Mapping[str, Path],
    paper_root: Path,
    work_root: Path,
) -> dict[str, Any]:
    output = work_root / "PAPA-06"
    output.mkdir(parents=True, exist_ok=True)
    config = {
        "mode": "per_target",
        "counts_mtx": str(assets["trans_de_pseudobulk_counts"]),
        "genes_tsv": str(assets["trans_de_genes"]),
        "samples_tsv": str(assets["trans_de_sample_manifest"]),
        "eligibility_tsv": str(assets["trans_de_eligibility"]),
        "output_dir": str(output),
        "unit_column": "replicate",
        "condition_column": "condition",
        "target_column": "target_uid",
        "baseline": "NTC",
        "control_label": "NTC",
        "full_gene_output": True,
        "robust": True,
    }
    config_path = output / "skill-config.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    runner = (
        repo
        / "src/pertura_runtime/agent_bundle/skills/"
        "run-replicate-aware-pseudobulk-de/scripts/run_locked.sh"
    )
    _run_locked(["bash", str(runner), "edger", str(config_path)], repo=repo, timeout=3600)
    manifest = _read_json(output / "trans_de_design_manifest.json")
    reference_manifest = _read_json(
        paper_root
        / "task_references/PAPA-06/reference/reference_design_manifest.json"
    )
    _require_papa06_environment_parity(manifest, reference_manifest)
    verdict = _evaluate(
        task=task,
        binding=binding,
        output=output,
        paper_root=paper_root,
        analysis_unit="target_by_replicate_pseudobulk",
    )
    return {
        "task_id": "PAPA-06",
        "route": "locked_skill_execution",
        "status": verdict.get("status"),
        "evaluation": verdict,
        "evaluation_hash": canonical_hash(verdict),
        "observed_artifact_hashes": {
            path.name: file_sha256(path)
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != config_path.name
        },
        "environment_versions": manifest.get("versions") or {},
        "reference_environment_versions": reference_manifest.get("versions") or {},
    }


def _run_kang01(
    *,
    repo: Path,
    task: Mapping[str, Any],
    binding: Mapping[str, Any],
    paper_root: Path,
    work_root: Path,
) -> dict[str, Any]:
    output = work_root / "KANG-01"
    output.mkdir(parents=True, exist_ok=True)
    inputs = paper_root / "references/REF-05/reference_inputs"
    evaluation_counts = inputs / "evaluation_pseudobulk_counts.tsv"
    evaluation_samples = inputs / "evaluation_sample_metadata.tsv"
    calibration_counts = inputs / "calibration_pseudobulk_counts.tsv"
    calibration_samples = inputs / "calibration_sample_metadata.tsv"
    for path in (
        evaluation_counts,
        evaluation_samples,
        calibration_counts,
        calibration_samples,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    shutil.copy2(evaluation_counts, output / "pseudobulk_counts.tsv")
    edge_config = {
        "mode": "single",
        "counts_tsv": str(evaluation_counts),
        "samples_tsv": str(evaluation_samples),
        "output_dir": str(output),
        "unit_column": "donor",
        "condition_column": "condition",
        "baseline": "ctrl",
        "target": "stim",
        "robust": False,
    }
    edge_config_path = output / "edger-skill-config.json"
    edge_config_path.write_text(
        json.dumps(edge_config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    edge_runner = (
        repo
        / "src/pertura_runtime/agent_bundle/skills/"
        "run-replicate-aware-pseudobulk-de/scripts/run_locked.sh"
    )
    _run_locked(
        ["bash", str(edge_runner), "edger", str(edge_config_path)],
        repo=repo,
        timeout=3600,
    )
    null_config = {
        "counts_tsv": str(calibration_counts),
        "samples_tsv": str(calibration_samples),
        "output_path": str(output / "null_calibration.tsv"),
        "details_path": str(output / "null_calibration_details.tsv"),
        "unit_column": "donor",
        "condition_column": "condition",
        "baseline": "ctrl",
        "target": "stim",
        "robust": False,
    }
    null_config_path = output / "null-skill-config.json"
    null_config_path.write_text(
        json.dumps(null_config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    null_runner = (
        repo
        / "src/pertura_runtime/agent_bundle/skills/"
        "run-design-preserving-null-calibration/scripts/run_locked.sh"
    )
    _run_locked(["bash", str(null_runner), str(null_config_path)], repo=repo, timeout=3600)
    manifest = _read_json(output / "design_manifest.json")
    reference_environment = _read_json(paper_root / "references/REF-05/r_environment.json")
    for name in ("R", "edgeR"):
        if str((manifest.get("versions") or {}).get(name)) != str(
            reference_environment.get(name)
        ):
            raise RuntimeError(f"KANG-01 skill/reference {name} versions differ")
    verdict = _evaluate(
        task=task,
        binding=binding,
        output=output,
        paper_root=paper_root,
        analysis_unit="donor_pseudobulk",
    )
    return {
        "task_id": "KANG-01",
        "route": "locked_skill_execution",
        "status": verdict.get("status"),
        "evaluation": verdict,
        "evaluation_hash": canonical_hash(verdict),
        "observed_artifact_hashes": {
            path.name: file_sha256(path)
            for path in sorted(output.iterdir())
            if path.is_file()
            and path.name not in {edge_config_path.name, null_config_path.name}
        },
        "environment_versions": manifest.get("versions") or {},
        "reference_environment_versions": reference_environment,
    }


def qualify(
    *,
    repo: Path,
    wheel: Path,
    task_catalog: Path,
    task_reference_catalog: Path,
    asset_catalog: Path,
    paper_root: Path,
    resource_lock: Path,
    binding_qualification: Path,
    work_root: Path,
) -> dict[str, Any]:
    binding_payload = _read_json(binding_qualification)
    if binding_payload.get("passed") is not True:
        raise RuntimeError("binding qualification did not pass")
    bound_records = {
        str(record["task_id"]): record
        for record in binding_payload.get("scientific_parity_records") or ()
    }
    if set(bound_records) != _BOUND_PARITY_TASKS:
        raise RuntimeError(
            "binding scientific parity coverage drifted: "
            f"observed={sorted(bound_records)}"
        )
    tasks = _task_map(task_catalog)
    references = _reference_map(task_reference_catalog)
    records = [dict(bound_records[task_id]) for task_id in sorted(bound_records)]
    records.append(
        _run_papa06(
            repo=repo,
            task=tasks["PAPA-06"],
            binding=references["PAPA-06"],
            assets=_asset_paths(asset_catalog, "WF-PAPA", paper_root),
            paper_root=paper_root,
            work_root=work_root,
        )
    )
    records.append(
        _run_kang01(
            repo=repo,
            task=tasks["KANG-01"],
            binding=references["KANG-01"],
            paper_root=paper_root,
            work_root=work_root,
        )
    )
    expected = _BOUND_PARITY_TASKS | {"PAPA-06", "KANG-01"}
    if {str(record["task_id"]) for record in records} != expected:
        raise RuntimeError("scientific method parity task coverage drifted")
    failed = [record for record in records if record.get("status") != "passed"]
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    payload = {
        "schema_version": "pertura-scientific-method-parity-v1",
        "status": "failed" if failed else "passed",
        "passed": not failed,
        "git_commit": commit,
        "wheel_sha256": file_sha256(wheel),
        "task_catalog_sha256": file_sha256(task_catalog),
        "task_reference_catalog_sha256": file_sha256(task_reference_catalog),
        "asset_catalog_sha256": file_sha256(asset_catalog),
        "resource_lock_sha256": file_sha256(resource_lock),
        "binding_qualification_sha256": file_sha256(binding_qualification),
        "task_count": len(records),
        "required_task_ids": sorted(expected),
        "failure_summary": [
            {
                "task_id": record["task_id"],
                "status": record.get("status"),
                "evaluation": record.get("evaluation"),
            }
            for record in failed
        ],
        "records": sorted(records, key=lambda item: str(item["task_id"])),
    }
    payload["canonical_hash"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--task-catalog", type=Path, required=True)
    parser.add_argument("--task-reference-catalog", type=Path, required=True)
    parser.add_argument("--asset-catalog", type=Path, required=True)
    parser.add_argument("--paper-root", type=Path, required=True)
    parser.add_argument("--resource-lock", type=Path, required=True)
    parser.add_argument("--binding-qualification", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = qualify(
        repo=args.repo.resolve(),
        wheel=args.wheel.resolve(),
        task_catalog=args.task_catalog.resolve(),
        task_reference_catalog=args.task_reference_catalog.resolve(),
        asset_catalog=args.asset_catalog.resolve(),
        paper_root=args.paper_root.resolve(),
        resource_lock=args.resource_lock.resolve(),
        binding_qualification=args.binding_qualification.resolve(),
        work_root=args.work_root.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
