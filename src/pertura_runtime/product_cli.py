from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, ResultEnvelope, ScopeKey
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.environment import SUPPORTED_PROFILES, doctor_environment, setup_environment



def main(argv: list[str] | None = None) -> int:
    arguments = list(argv or __import__("sys").argv[1:])

    parser = argparse.ArgumentParser(prog="pertura", description="Pertura capability-first Perturb-seq workflow.")
    commands = parser.add_subparsers(dest="command", required=True)

    environment = commands.add_parser("env", help="Set up or diagnose pinned scientific environments.")
    environment_commands = environment.add_subparsers(dest="env_command", required=True)
    env_setup = environment_commands.add_parser("setup")
    env_setup.add_argument("profile", choices=list(SUPPORTED_PROFILES))
    env_doctor = environment_commands.add_parser("doctor")
    env_doctor.add_argument("profile", choices=list(SUPPORTED_PROFILES))

    capabilities = commands.add_parser("capabilities", help="Discover registered capabilities.")
    capability_commands = capabilities.add_subparsers(dest="capabilities_command", required=True)
    capability_list = capability_commands.add_parser("list")
    capability_list.add_argument("--kind", choices=["diagnostic", "analysis", "virtual", "report"], default=None)
    capability_show = capability_commands.add_parser("show")
    capability_list.add_argument("--include-deprecated", action="store_true")
    capability_list.add_argument("--include-installed", action="store_true")
    capability_show.add_argument("capability_id")

    capability_show.add_argument("--include-installed", action="store_true")
    inspect = commands.add_parser("inspect", help="Create a deterministic DatasetContract.")
    _add_inspect_args(inspect)
    preflight = commands.add_parser("preflight", help="Deprecated alias that forwards to inspect.")
    _add_inspect_args(preflight)

    diagnostic = commands.add_parser("diagnostic", help="Run a registered diagnostic through the verifier.")
    diagnostic.add_argument("capability_id")
    diagnostic.add_argument("workspace", type=Path)
    _add_run_args(diagnostic)

    analyze = commands.add_parser("analyze", help="Run a registered analysis capability.")
    analyze.add_argument("objective")
    analyze.add_argument("workspace", type=Path)
    analyze.add_argument("--capability-id", default=None)
    _add_run_args(analyze)

    virtual = commands.add_parser("evaluate-virtual", help="Evaluate predictions under a fixed scope contract.")
    virtual.add_argument("workspace", type=Path)
    virtual.add_argument("--capability-id", default="virtual.evaluate.v1")
    _add_run_args(virtual)

    finalize = commands.add_parser("finalize", help="Seal receipts and render the committed report.")
    finalize.add_argument("run_id")
    finalize.add_argument("--workspace", type=Path, default=Path("."))
    finalize.add_argument("--out", type=Path, default=None)

    migrate = commands.add_parser("migrate-run", help="Import a legacy run as legacy_unverified.")
    migrate.add_argument("legacy_run", type=Path)
    migrate.add_argument("--workspace", type=Path, default=None)
    migrate.add_argument("--out", type=Path, default=None)

    dashboard = commands.add_parser("dashboard", help="Serve the local read-only dashboard.")
    dashboard.add_argument("workspace", type=Path, nargs="?", default=Path("."))
    dashboard.add_argument("--run", dest="run_id", default=None)
    dashboard.add_argument("--port", type=int, default=8765)

    release_check = commands.add_parser("release-check", help="Audit the v0.2.0 release gate without fabricating missing benchmarks.")
    release_check.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])

    args = parser.parse_args(arguments)
    if args.command == "env":
        payload = setup_environment(args.profile) if args.env_command == "setup" else doctor_environment(args.profile)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok", True) else 1
    if args.command == "capabilities":
        registry = CapabilityRegistry.load_default(include_external=bool(args.include_installed))
        payload = (
            {"capabilities": [item.to_dict() for item in registry.list(kind=args.kind, include_deprecated=args.include_deprecated)]}
            if args.capabilities_command == "list"
            else registry.get(args.capability_id).model_dump(mode="json")
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.command in {"inspect", "preflight"}:
        if args.command == "preflight":
            warnings.warn("preflight now forwards to inspect", DeprecationWarning, stacklevel=2)
        runtime = _product_runtime(args.workspace)
        try:
            payload = runtime.inspect_dataset(args.workspace, dataset_id=args.dataset_id, confirmations=_read_json(args.confirmations))
        finally:
            runtime.close()
        _write(payload, args.out)
        return 0
    if args.command in {"diagnostic", "analyze", "evaluate-virtual"}:
        runtime = _product_runtime(args.workspace)
        parameters = _read_json(args.parameters)
        scope = _read_json(args.scope)
        dependencies = _read_json(args.dependencies)
        try:
            if args.command == "diagnostic":
                payload = runtime.run_diagnostic(args.capability_id, contract_id=args.contract_id, scope=scope or None, parameters=parameters, dependencies=dependencies if isinstance(dependencies, list) else None)
            elif args.command == "analyze":
                payload = runtime.run_analysis(args.objective, capability_id=args.capability_id, contract_id=args.contract_id, scope=scope or None, parameters=parameters, dependencies=dependencies if isinstance(dependencies, list) else None)
            else:
                payload = runtime.evaluate_virtual_model(capability_id=args.capability_id, contract_id=args.contract_id, scope=scope or None, parameters=parameters)
        finally:
            runtime.close()
        _write(payload, args.out)
        return 0 if payload.get("status") != "failed" else 1
    if args.command == "finalize":
        runtime = _product_runtime(args.workspace, run_id=args.run_id)
        try:
            payload = runtime.finalize_report(args.run_id)
        finally:
            runtime.close()
        _write(payload, args.out)
        return 0
    if args.command == "migrate-run":
        payload = _migrate_legacy_run(args.legacy_run, args.workspace)
        _write(payload, args.out)
        return 0
    if args.command == "dashboard":
        from pertura_runtime.dashboard import run_dashboard

        run_dashboard(_product_runtime(args.workspace, run_id=args.run_id), port=args.port)
        return 0
    if args.command == "release-check":
        from pertura_bench.release_gate import audit_v020

        payload = audit_v020(args.repo)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ready"] else 1
    return 2


def _add_inspect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--confirmations", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contract-id", default=None)
    parser.add_argument("--scope", type=Path, default=None)
    parser.add_argument("--parameters", type=Path, default=None)
    parser.add_argument("--dependencies", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)


def _product_runtime(source: Path, *, run_id: str | None = None) -> PerturaProductRuntime:
    source = Path(source).expanduser().resolve()
    if source.is_dir() and (source / "manifest.json").is_file() and (source / "artifacts").is_dir():
        workspace = ClaudeRunWorkspace.open(source)
    else:
        project = source if source.is_dir() else source.parent
        selected = run_id or "current"
        run_root = project / ".pertura" / selected
        workspace = ClaudeRunWorkspace.open(run_root, input_source=source) if run_root.exists() else ClaudeRunWorkspace.create(root=run_root.parent, input_source=source, run_id=selected)
    return PerturaProductRuntime(workspace)


def _read_json(path: Path | None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path else {}


def _write(payload: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    if path is None:
        print(text)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _migrate_legacy_run(legacy_run: Path, workspace: Path | None) -> dict[str, Any]:
    legacy = legacy_run.expanduser().resolve()
    if not legacy.exists():
        raise FileNotFoundError(legacy)
    destination_root = (workspace.expanduser().resolve() if workspace else legacy.parent) / ".pertura" / f"migrated-{legacy.name}"
    destination = ClaudeRunWorkspace.open(destination_root, input_source=legacy)
    envelope = ResultEnvelope(
        run_id=destination.root.name,
        request_id=f"legacy_request_{legacy.name}",
        capability_id="legacy.import.v1",
        capability_version="1.0.0",
        capability_trust="exploratory",
        contract_id=f"legacy_contract_{legacy.name}",
        contract_hash="sha256:legacy-unverified",
        scope=ScopeKey(dataset_id=f"legacy:{legacy.name}", unresolved_fields=("scope", "control", "replicate")),
        status=AnalysisStatus.completed_with_caution,
        result_kind="legacy_unverified",
        source_class="observed_metadata",
        summary="Legacy run imported for read-only viewing; no trusted receipt was created.",
        cautions=("legacy_unverified", "scientific promotion is disabled"),
        output_paths=(str(legacy),),
        metadata={"legacy_run": str(legacy), "receipt_created": False},
    )
    path = destination.artifacts_dir / "legacy_unverified.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "legacy_unverified", "result_id": envelope.result_id, "receipt_id": None, "path": str(path)}


if __name__ == "__main__":
    raise SystemExit(main())
