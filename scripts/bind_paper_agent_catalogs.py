from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pertura_bench.metric_evaluators import validate_artifact_evaluator
from pertura_bench.paper_tasks import (
    load_paper_task_catalog,
    validate_paper_asset_catalog,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def resolve_previous_reference_index(
    *, previous_bound_path: Path, paper_root: Path
) -> Path:
    """Resolve the exact frozen reference index used by an earlier binding."""

    previous = _read(previous_bound_path)
    expected = str(
        (previous.get("source_hashes") or {}).get("reference_pack_index") or ""
    )
    if not expected.startswith("sha256:"):
        raise ValueError("previous task-reference binding lacks an index hash")
    matches: list[Path] = []
    for candidate in sorted(Path(paper_root).resolve().rglob("*.json")):
        try:
            if _sha256(candidate) != expected:
                continue
            payload = _read(candidate)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if payload.get("schema_version") == "pertura-paper-reference-pack-index-v1":
            matches.append(candidate.resolve())
    if len(matches) != 1:
        raise ValueError(
            "could not resolve exactly one frozen reference-pack index from "
            f"the previous binding: matches={[str(path) for path in matches]}"
        )
    return matches[0]


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"bound artifact is outside paper root: {path}") from exc


def bind_task_references(
    *,
    candidate_path: Path,
    reference_index_path: Path,
    task_reference_root: Path,
    paper_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    candidate = _read(candidate_path)
    reference_index = _read(reference_index_path)
    task_manifest_path = task_reference_root / "manifest.json"
    task_manifest = _read(task_manifest_path)
    problems: list[str] = []
    if candidate.get("schema_version") != "pertura-paper-task-reference-catalog-v1":
        problems.append("unsupported candidate task-reference schema")
    if (
        reference_index.get("schema_version")
        != "pertura-paper-reference-pack-index-v1"
    ):
        problems.append("unsupported reference-pack index schema")
    if reference_index.get("reference_pack_count") != 10 or reference_index.get(
        "passed"
    ) is not True:
        problems.append("REF-01 through REF-10 index is not complete")
    if (
        task_manifest.get("readiness") != "generated"
        or task_manifest.get("pending_jobs")
        or task_manifest.get("passed") is not True
        or task_manifest.get("problems")
    ):
        problems.append("Papalexi task-reference pack is not complete")
    for relative, expected in (task_manifest.get("output_files") or {}).items():
        path = task_reference_root / relative
        if not path.is_file() or _sha256(path) != expected:
            problems.append(f"task-reference output drift: {relative}")

    payload = copy.deepcopy(candidate)
    payload["schema_version"] = "pertura-paper-task-reference-catalog-bound-v1"
    payload["status"] = "bound" if not problems else "invalid"
    payload["passed"] = not problems
    payload["problems"] = problems
    payload["source_hashes"] = {
        "candidate_task_references": _sha256(candidate_path),
        "reference_pack_index": _sha256(reference_index_path),
        "task_reference_manifest": _sha256(task_manifest_path),
    }
    bindings = {
        str(item["task_id"]): item for item in payload.get("bindings") or ()
    }
    indexed_packs = {
        str(item.get("reference_pack_id") or ""): item
        for item in reference_index.get("reference_packs") or ()
    }
    for task_id, binding in bindings.items():
        bound_sources = []
        for source in binding.get("reference_sources") or ():
            source = str(source)
            if source.startswith("REF-"):
                indexed = indexed_packs.get(source)
                if indexed is None:
                    problems.append(
                        f"{task_id}: reference pack is absent from index: {source}"
                    )
                    continue
                manifest_hash = str(indexed.get("manifest_sha256") or "")
                tree_hash = str(indexed.get("pack_tree_sha256") or "")
                if not manifest_hash.startswith("sha256:") or not tree_hash.startswith(
                    "sha256:"
                ):
                    problems.append(f"{task_id}: invalid indexed hashes for {source}")
                    continue
                source_manifest = (
                    paper_root / "references" / source / "manifest.json"
                )
                if not source_manifest.is_file():
                    problems.append(
                        f"{task_id}: reference manifest is missing for {source}"
                    )
                    continue
                if _sha256(source_manifest) != manifest_hash:
                    problems.append(
                        f"{task_id}: reference manifest hash drift for {source}"
                    )
                    continue
                bound_sources.append(
                    {
                        "reference_id": source,
                        "manifest_sha256": manifest_hash,
                        "pack_tree_sha256": tree_hash,
                        "git_commit": indexed.get("git_commit"),
                    }
                )
            elif source.startswith("TASKREF-"):
                bound_sources.append(
                    {
                        "reference_id": source,
                        "manifest_sha256": _sha256(task_manifest_path),
                        "pack_tree_sha256": _path_sha256(task_reference_root),
                        "git_commit": task_manifest.get("git_commit"),
                    }
                )
            else:
                problems.append(f"{task_id}: unsupported reference source {source}")
        binding["bound_reference_sources"] = bound_sources
        if len(bound_sources) != len(binding.get("reference_sources") or ()):
            problems.append(f"{task_id}: reference sources are not fully bound")
        route = str(binding.get("scoring_route") or "")
        if route not in {
            "artifact_evaluator",
            "protocol_hard_gate",
            "hybrid",
            "custom_artifact_evaluator",
        }:
            problems.append(f"{task_id}: unsupported scoring route {route!r}")
        evaluators = []
        covered_metrics: set[str] = set()
        for index, template in enumerate(binding.get("evaluator_templates") or ()):
            spec = copy.deepcopy(template)
            source = str(spec.pop("reference_source", ""))
            output = str(spec.pop("reference_output", ""))
            metric_ids = {str(item) for item in spec.pop("metric_ids", ())}
            if source not in set(binding.get("reference_sources") or ()):
                problems.append(
                    f"{task_id}: evaluator source is not bound to the task: {source}"
                )
                continue
            pack_root = (paper_root / "references" / source).resolve()
            reference = (pack_root / output).resolve()
            if reference != pack_root and pack_root not in reference.parents:
                problems.append(f"{task_id}: evaluator reference escapes {source}")
                continue
            if not reference.is_file():
                problems.append(
                    f"{task_id}: evaluator reference is missing: {source}/{output}"
                )
                continue
            spec["reference_path"] = _relative(paper_root, reference)
            spec["reference_sha256"] = _sha256(reference)
            try:
                validate_artifact_evaluator(
                    spec, context=f"{task_id}.evaluator[{index}]"
                )
            except ValueError as exc:
                problems.append(str(exc))
                continue
            evaluators.append(spec)
            covered_metrics.update(metric_ids)
        if evaluators:
            binding["evaluators"] = evaluators
        protocol = dict(binding.get("protocol_evaluator") or {})
        if protocol:
            covered_metrics.update(
                str(item) for item in protocol.get("metric_ids") or ()
            )
            if not protocol.get("allowed_status") or not protocol.get(
                "allowed_analysis_units"
            ):
                problems.append(
                    f"{task_id}: protocol gate lacks status or analysis-unit bounds"
                )
            for pattern in (
                *(protocol.get("required_text_patterns") or ()),
                *(protocol.get("forbidden_text_patterns") or ()),
            ):
                try:
                    re.compile(str(pattern), flags=re.IGNORECASE)
                except re.error as exc:
                    problems.append(f"{task_id}: invalid protocol regex: {exc}")
            for relative in protocol.get("required_outputs") or ():
                path = Path(str(relative))
                if (
                    not str(relative)
                    or path.is_absolute()
                    or ".." in path.parts
                    or ":" in str(relative)
                    or "\\" in str(relative)
                ):
                    problems.append(
                        f"{task_id}: protocol output path is unsafe: {relative}"
                    )
            row_counts = protocol.get("required_table_row_counts") or {}
            if not isinstance(row_counts, dict):
                problems.append(f"{task_id}: protocol row counts are invalid")
            else:
                for relative, count in row_counts.items():
                    path = Path(str(relative))
                    if (
                        not str(relative)
                        or path.is_absolute()
                        or ".." in path.parts
                        or ":" in str(relative)
                        or "\\" in str(relative)
                    ):
                        problems.append(
                            f"{task_id}: protocol row-count path is unsafe: {relative}"
                        )
                    if (
                        not isinstance(count, int)
                        or isinstance(count, bool)
                        or count < 0
                    ):
                        problems.append(
                            f"{task_id}: protocol row count is invalid: {relative}"
                        )
            json_values = protocol.get("required_json_values") or {}
            if not isinstance(json_values, dict):
                problems.append(f"{task_id}: protocol JSON values are invalid")
            else:
                for relative, values in json_values.items():
                    path = Path(str(relative))
                    if (
                        not isinstance(values, dict)
                        or not values
                        or path.is_absolute()
                        or ".." in path.parts
                        or ":" in str(relative)
                        or "\\" in str(relative)
                    ):
                        problems.append(
                            f"{task_id}: protocol JSON value check is invalid: {relative}"
                        )
            balances = protocol.get("required_json_balances") or []
            if not isinstance(balances, list):
                problems.append(f"{task_id}: protocol JSON balances are invalid")
            else:
                for balance in balances:
                    if (
                        not isinstance(balance, dict)
                        or not str(balance.get("output") or "")
                        or not str(balance.get("total") or "")
                        or not isinstance(balance.get("parts"), list)
                        or not balance.get("parts")
                    ):
                        problems.append(
                            f"{task_id}: protocol JSON balance is invalid"
                        )
        declared_metrics = {str(item) for item in binding.get("metric_ids") or ()}
        if route == "custom_artifact_evaluator":
            covered_metrics.update(declared_metrics)
        if route in {"artifact_evaluator", "hybrid"} and not evaluators:
            problems.append(f"{task_id}: artifact evaluator route is unbound")
        if route in {"protocol_hard_gate", "hybrid"} and not protocol:
            problems.append(f"{task_id}: protocol hard-gate route is unbound")
        if covered_metrics != declared_metrics:
            problems.append(
                f"{task_id}: metric coverage mismatch: "
                f"missing={sorted(declared_metrics - covered_metrics)}, "
                f"extra={sorted(covered_metrics - declared_metrics)}"
            )
    p06_reference = task_reference_root / "PAPA-06" / "reference" / "trans_de_reference.tsv"
    p06_design_reference = task_reference_root / "PAPA-06" / "reference" / "design_matrices.tsv"
    p06_eligibility = task_reference_root / "PAPA-06" / "neutral_inputs" / "target_eligibility.tsv"
    p07_evidence = task_reference_root / "PAPA-07" / "global_effect_evidence.tsv"
    for path in (
        p06_reference,
        p06_design_reference,
        p06_eligibility,
        p07_evidence,
    ):
        if not path.is_file():
            problems.append(f"required task reference is missing: {path}")
    if not problems:
        bindings["PAPA-06"]["bound_evaluator"] = {
            "type": "macro_trans_de",
            "observed_output": "trans_de_results.tsv",
            "design_matrices_output": "trans_de_design_matrices.tsv",
            "design_manifest_output": "trans_de_design_manifest.json",
            "summary_output": "trans_de_summary.json",
            "reference_path": _relative(paper_root, p06_reference),
            "reference_sha256": _sha256(p06_reference),
            "design_reference_path": _relative(paper_root, p06_design_reference),
            "design_reference_sha256": _sha256(p06_design_reference),
            "eligibility_path": _relative(paper_root, p06_eligibility),
            "eligibility_sha256": _sha256(p06_eligibility),
            "top_k": 20,
        }
        bindings["PAPA-07"]["bound_evaluator"] = {
            "type": "claim_classification",
            "observed_output": "global_effect_claims.tsv",
            "limitations_output": "global_effect_limitations.json",
            "evidence_path": _relative(paper_root, p07_evidence),
            "evidence_sha256": _sha256(p07_evidence),
        }
    payload["status"] = "bound" if not problems else "invalid"
    payload["passed"] = not problems
    payload["problems"] = problems
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": "pertura-paper-task-reference-binding-validation-v1",
        "passed": not problems,
        "problems": problems,
        "binding_count": len(bindings),
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
    }


def bind_assets(
    *,
    template_path: Path,
    task_catalog_path: Path,
    cache: Path,
    paper_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    template = _read(template_path)
    if template.get("schema_version") != "pertura-paper-agent-assets-template-v1":
        raise ValueError("unsupported paper agent asset template")
    roots = {
        "cache": cache.resolve(),
        "paper_root": paper_root.resolve(),
        "benchmark_root": paper_root.resolve().parent,
    }
    workflows = copy.deepcopy(template.get("workflows") or {})
    problems: list[str] = []
    asset_count = 0
    for workflow_id, workflow in workflows.items():
        roles: set[str] = set()
        for asset in workflow.get("assets") or ():
            asset_count += 1
            role = str(asset.get("role") or "")
            root_name = str(asset.get("root") or "")
            relative = str(asset.get("relative_path") or "")
            if not role or role in roles:
                problems.append(f"{workflow_id}: duplicate or empty asset role {role}")
                continue
            roles.add(role)
            if root_name not in roots:
                problems.append(f"{workflow_id}/{role}: invalid root")
                continue
            base = roots[root_name]
            path = (base / relative).resolve()
            if path != base and base not in path.parents:
                problems.append(f"{workflow_id}/{role}: asset escapes root")
                continue
            if not path.exists():
                problems.append(f"{workflow_id}/{role}: asset is missing")
                continue
            asset["content_sha256"] = _path_sha256(path)
    payload = {
        "schema_version": "pertura-paper-agent-assets-v1",
        "status": "bound" if not problems else "invalid",
        "passed": not problems,
        "problems": problems,
        "source_template_sha256": _sha256(template_path),
        "workflows": workflows,
    }
    task_catalog = load_paper_task_catalog(task_catalog_path)
    problems.extend(validate_paper_asset_catalog(payload, task_catalog))
    payload["status"] = "bound" if not problems else "invalid"
    payload["passed"] = not problems
    payload["problems"] = problems
    payload["task_catalog_sha256"] = _sha256(task_catalog_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": "pertura-paper-agent-asset-binding-validation-v1",
        "passed": not problems,
        "problems": problems,
        "asset_count": asset_count,
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    references = sub.add_parser("references")
    references.add_argument("--candidate", type=Path, required=True)
    reference_source = references.add_mutually_exclusive_group(required=True)
    reference_source.add_argument("--reference-index", type=Path)
    reference_source.add_argument("--previous-bound", type=Path)
    references.add_argument("--task-reference-root", type=Path, required=True)
    references.add_argument("--paper-root", type=Path, required=True)
    references.add_argument("--output", type=Path, required=True)
    assets = sub.add_parser("assets")
    assets.add_argument("--template", type=Path, required=True)
    assets.add_argument("--task-catalog", type=Path, required=True)
    assets.add_argument("--cache", type=Path, required=True)
    assets.add_argument("--paper-root", type=Path, required=True)
    assets.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "references":
        reference_index = (
            args.reference_index.resolve()
            if args.reference_index is not None
            else resolve_previous_reference_index(
                previous_bound_path=args.previous_bound.resolve(),
                paper_root=args.paper_root.resolve(),
            )
        )
        result = bind_task_references(
            candidate_path=args.candidate.resolve(),
            reference_index_path=reference_index,
            task_reference_root=args.task_reference_root.resolve(),
            paper_root=args.paper_root.resolve(),
            output_path=args.output.resolve(),
        )
    else:
        result = bind_assets(
            template_path=args.template.resolve(),
            task_catalog_path=args.task_catalog.resolve(),
            cache=args.cache.resolve(),
            paper_root=args.paper_root.resolve(),
            output_path=args.output.resolve(),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
