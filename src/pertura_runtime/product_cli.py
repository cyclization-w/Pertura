from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Any

from pertura_core import AnalysisStatus, ResultEnvelope, ScopeKey
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.network_policy import NetworkAccessPolicy
from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.project.cli import PROJECT_COMMANDS, add_project_commands, handle_project_command
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import TurnStatus
from pertura_workflow.capabilities import CapabilityRegistry
from pertura_workflow.environment import SUPPORTED_PROFILES, doctor_environment, setup_environment
from pertura_workflow.knowledge_resources import (
    RESOURCE_PROFILES, doctor_resource, freeze_local_resource, setup_resource,
)



def main(argv: list[str] | None = None) -> int:
    arguments = list(argv or __import__("sys").argv[1:])

    parser = argparse.ArgumentParser(prog="pertura", description="Pertura capability-first Perturb-seq workflow.")
    commands = parser.add_subparsers(dest="command", required=True)
    add_project_commands(commands)

    environment = commands.add_parser("env", help="Set up or diagnose pinned scientific environments.")
    environment_commands = environment.add_subparsers(dest="env_command", required=True)
    env_setup = environment_commands.add_parser("setup")
    env_setup.add_argument("profile", choices=list(SUPPORTED_PROFILES))
    env_doctor = environment_commands.add_parser("doctor")
    env_doctor.add_argument("profile", choices=list(SUPPORTED_PROFILES))

    resource_parser = commands.add_parser(
        "resources", help="Set up, freeze or diagnose versioned knowledge resources."
    )
    resource_commands = resource_parser.add_subparsers(
        dest="resources_command", required=True
    )
    resource_setup = resource_commands.add_parser("setup")
    resource_setup.add_argument("profile", choices=list(RESOURCE_PROFILES))
    resource_doctor = resource_commands.add_parser("doctor")
    resource_doctor.add_argument("profile", choices=list(RESOURCE_PROFILES))
    resource_freeze = resource_commands.add_parser("freeze")
    resource_freeze.add_argument("profile", choices=list(RESOURCE_PROFILES))
    resource_freeze.add_argument(
        "--artifact", action="append", default=[],
        help="Generated artifact binding in artifact_id=path form.",
    )

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
    analyze.add_argument(
        "--allow-literature-network", action="store_true",
        help="Allow only Europe PMC access for the literature capability in this process.",
    )

    virtual = commands.add_parser("evaluate-virtual", help="Evaluate predictions under a fixed scope contract.")
    virtual.add_argument("workspace", type=Path)
    virtual.add_argument("--capability-id", default="virtual.evaluate.comprehensive.v1")
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
    if args.command in PROJECT_COMMANDS:
        return handle_project_command(args)
    if args.command == "env":
        payload = setup_environment(args.profile) if args.env_command == "setup" else doctor_environment(args.profile)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok", True) else 1
    if args.command == "resources":
        if args.resources_command == "setup":
            payload = setup_resource(args.profile)
        elif args.resources_command == "doctor":
            payload = doctor_resource(args.profile)
        else:
            bindings = {}
            for item in args.artifact:
                if "=" not in item:
                    parser.error("--artifact requires artifact_id=path")
                artifact_id, path = item.split("=", 1)
                bindings[artifact_id] = Path(path)
            payload = freeze_local_resource(args.profile, bindings)
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
        runtime = _product_runtime(
            args.workspace,
            allow_literature_network=bool(
                getattr(args, "allow_literature_network", False)
            ),
        )
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


def _product_runtime(
    source: Path,
    *,
    run_id: str | None = None,
    allow_literature_network: bool = False,
) -> PerturaProductRuntime:
    source = Path(source).expanduser().resolve()
    network_policy = (
        NetworkAccessPolicy.literature_europepmc()
        if allow_literature_network
        else NetworkAccessPolicy.offline()
    )
    if source.is_dir() and (source / "manifest.json").is_file() and (source / "artifacts").is_dir():
        return PerturaProductRuntime(
            ClaudeRunWorkspace.open(source),
            network_policy=network_policy,
        )

    project_root = source if source.is_dir() else source.parent
    project = (
        ProjectWorkspace.open(project_root)
        if (project_root / ".pertura" / "project.sqlite").is_file()
        else ProjectWorkspace.initialize(project_root)
    )
    if run_id:
        selected_run = project.store.get_run(run_id)
        if selected_run is None:
            selected_run = project.create_run(logical_name=run_id, run_id=run_id)
    else:
        selected_run = project.active_run()
    input_source = None if source == project.root else source
    workspace = project.run_workspace(selected_run.run_id, input_source=input_source)
    return PerturaProductRuntime(
        workspace,
        network_policy=network_policy,
        project_workspace=project,
        run_id=selected_run.run_id,
    )


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
    project_root = (workspace.expanduser().resolve() if workspace else legacy.parent)
    project = (
        ProjectWorkspace.open(project_root)
        if (project_root / ".pertura" / "project.sqlite").is_file()
        else ProjectWorkspace.initialize(project_root)
    )
    run = project.create_run(logical_name=f"legacy:{legacy.name}")
    run = run.model_copy(update={"status": "legacy_unverified"})
    project.store.put_run(run)
    destination = project.run_workspace(run.run_id, input_source=legacy)
    registry = DataAssetRegistry(project_id=project.project.project_id, store=project.store, object_root=project.objects_dir)
    asset_ids = []
    files = [legacy] if legacy.is_file() else sorted(item for item in legacy.rglob("*") if item.is_file())
    for path in files:
        relative = path.name if legacy.is_file() else path.relative_to(legacy).as_posix()
        role = "legacy_input" if relative.startswith(("input/", "data/")) else "legacy_output"
        kind = "observed" if role == "legacy_input" else "derived"
        asset = registry.register(path, role=role, kind=kind)
        asset_ids.append(asset.asset_id)
    envelope = ResultEnvelope(
        run_id=run.run_id,
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
        output_paths=(),
        metadata={"legacy_logical_name": legacy.name, "receipt_created": False, "asset_ids": asset_ids},
    )
    path = destination.artifacts_dir / "legacy_unverified.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    conversation = project.create_conversation(run.run_id, title="Legacy imported projection")
    turn = project.store.begin_turn(conversation.conversation_id, "Legacy run imported for read-only viewing.")
    project.store.complete_turn(
        turn.turn_id,
        status=TurnStatus.completed,
        provider_final=None,
        result_ids=(envelope.result_id,),
        artifact_ids=tuple(asset_ids),
        trace={"projection": "legacy_imported", "history_reconstructed": False},
    )
    return {
        "status": "legacy_unverified",
        "project_id": project.project.project_id,
        "run_id": run.run_id,
        "conversation_id": conversation.conversation_id,
        "turn_id": turn.turn_id,
        "result_id": envelope.result_id,
        "receipt_id": None,
        "asset_ids": asset_ids,
        "path": str(path),
    }


if __name__ == "__main__":
    raise SystemExit(main())
