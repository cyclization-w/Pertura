from __future__ import annotations

import argparse
import json
from pathlib import Path

from pertura_bench.operations import (
    fetch_benchmark,
    run_conversion,
    source_manifests,
    subset_h5ad,
    validate_repository,
    write_annotation_packet,
)
from pertura_bench.models import BenchmarkSubsetSpec
from pertura_bench.release_gate import audit_v020


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pertura_bench")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--repo", type=Path, default=Path.cwd())
    status = sub.add_parser("status")
    status.add_argument("--repo", type=Path, default=Path.cwd())
    fetch = sub.add_parser("fetch")
    fetch.add_argument("dataset_id")
    fetch.add_argument("--cache", type=Path, required=True)
    fetch.add_argument("--repo", type=Path, default=Path.cwd())
    annotation = sub.add_parser("annotation-packet")
    annotation.add_argument("--modality", choices=("crispri", "crispra"), required=True)
    annotation.add_argument("--output", type=Path, default=Path("benchmarks/annotations"))
    convert = sub.add_parser("convert")
    convert.add_argument("dataset_id")
    convert.add_argument("--cache", type=Path, required=True)
    convert.add_argument("--repo", type=Path, default=Path.cwd())
    convert.add_argument("--rscript", default="Rscript")
    subset = sub.add_parser("subset")
    subset.add_argument("dataset_id")
    subset.add_argument("--split", choices=("calibration", "evaluation"), required=True)
    subset.add_argument("--cache", type=Path, required=True)
    subset.add_argument("--input", type=Path, required=True)
    subset.add_argument("--output", type=Path, required=True)
    subset.add_argument("--source-lock-hash", required=True)
    subset.add_argument("--label-column", required=True)
    subset.add_argument("--labels", nargs="+", required=True)
    sub.add_parser("edger-golden").add_argument("--environment", default="edger-v1")
    capabilities = sub.add_parser("capabilities")
    capability_sub = capabilities.add_subparsers(dest="capability_command", required=True)
    capability_sub.add_parser("list")
    capability_sub.add_parser("matrix")
    sub.add_parser("validate-cases")
    run = sub.add_parser("run")
    run.add_argument("capability_id")
    run.add_argument("--tier", choices=("unit", "synthetic_ci", "frozen_subset", "full_dataset"), default="synthetic_ci")
    run.add_argument("--dataset")
    run_matrix = sub.add_parser("run-matrix")
    run_matrix.add_argument("--tier", choices=("unit", "synthetic_ci", "frozen_subset", "full_dataset"), default="synthetic_ci")
    server = sub.add_parser("export-server-plan")
    server.add_argument("--output", type=Path, required=True)
    args, extra = parser.parse_known_args(argv)
    if args.command == "validate":
        payload = validate_repository(args.repo)
    elif args.command == "status":
        payload = audit_v020(args.repo)
    elif args.command == "fetch":
        manifests = source_manifests(args.repo)
        if args.dataset_id not in manifests:
            parser.error(f"unknown benchmark dataset: {args.dataset_id}")
        lock, path = fetch_benchmark(manifests[args.dataset_id][1], args.cache)
        payload = {"lock": lock.model_dump(mode="json"), "local_path": str(path)}
    elif args.command == "annotation-packet":
        payload = write_annotation_packet(args.modality, args.output)
    elif args.command == "convert":
        manifests = source_manifests(args.repo)
        if args.dataset_id not in manifests:
            parser.error(f"unknown benchmark dataset: {args.dataset_id}")
        lock, path = run_conversion(
            manifests[args.dataset_id][1], repo_root=args.repo, cache=args.cache, rscript=args.rscript
        )
        payload = {"lock": lock.model_dump(mode="json"), "local_path": str(path)}
    elif args.command == "subset":
        spec = BenchmarkSubsetSpec(
            dataset_id=args.dataset_id,
            source_lock_hash=args.source_lock_hash,
            split=args.split,
            label_column=args.label_column,
            labels=tuple(args.labels),
        )
        lock = subset_h5ad(args.input, args.output, spec)
        payload = lock.model_dump(mode="json")
    elif args.command == "capabilities":
        from pertura_bench.capability_bench import CANDIDATE_CAPABILITIES, coverage_matrix

        payload = (
            {"capabilities": list(CANDIDATE_CAPABILITIES)}
            if args.capability_command == "list"
            else coverage_matrix().model_dump(mode="json")
        )
    elif args.command == "validate-cases":
        from pertura_bench.capability_bench import validate_cases

        payload = validate_cases()
    elif args.command == "run":
        from pertura_bench.capability_bench import run_protocol_cases

        payload = {
            "capability_id": args.capability_id,
            "tier": args.tier,
            "dataset_id": args.dataset,
            "verdicts": run_protocol_cases(args.capability_id, tier=args.tier),
        }
    elif args.command == "run-matrix":
        from pertura_bench.capability_bench import CANDIDATE_CAPABILITIES, run_protocol_cases

        payload = {
            capability_id: run_protocol_cases(capability_id, tier=args.tier)
            for capability_id in CANDIDATE_CAPABILITIES
        }
    elif args.command == "export-server-plan":
        from pertura_bench.capability_bench import write_server_plan

        payload = write_server_plan(args.output)
    else:
        from pertura_bench.scientific import dispatch_maintainer_command

        payload = dispatch_maintainer_command(args.command, args, extra)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
