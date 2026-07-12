from __future__ import annotations

import argparse
import json
from pathlib import Path

from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.workspace import ProjectWorkspace


PROJECT_COMMANDS = frozenset({"project", "runs", "conversations", "reports", "assets"})


def add_project_commands(commands: argparse._SubParsersAction) -> None:
    project = commands.add_parser("project", help="Create or inspect a persistent Pertura project.")
    sub = project.add_subparsers(dest="project_command", required=True)
    for name in ("init", "status"):
        item = sub.add_parser(name)
        item.add_argument("root", type=Path)

    runs = commands.add_parser("runs", help="Manage project analysis runs.")
    sub = runs.add_subparsers(dest="runs_command", required=True)
    for name in ("create", "list", "show"):
        item = sub.add_parser(name)
        item.add_argument("project", type=Path)
        if name == "create":
            item.add_argument("--name", default="analysis")
        elif name == "show":
            item.add_argument("run_id")

    conversations = commands.add_parser("conversations", help="Manage provider-neutral conversations.")
    sub = conversations.add_subparsers(dest="conversations_command", required=True)
    for name in ("start", "list", "show"):
        item = sub.add_parser(name)
        item.add_argument("project", type=Path)
        if name == "start":
            item.add_argument("--run", dest="run_id", default=None)
            item.add_argument("--title", default="Pertura analysis")
        elif name == "show":
            item.add_argument("conversation_id")

    reports = commands.add_parser("reports", help="Inspect immutable report revisions.")
    sub = reports.add_subparsers(dest="reports_command", required=True)
    for name in ("list", "show"):
        item = sub.add_parser(name)
        item.add_argument("project", type=Path)
        item.add_argument("--run", dest="run_id", default=None)
        if name == "show":
            item.add_argument("revision", type=int)

    assets = commands.add_parser("assets", help="Register and diagnose project data assets.")
    sub = assets.add_subparsers(dest="assets_command", required=True)
    add = sub.add_parser("add")
    add.add_argument("project", type=Path)
    add.add_argument("path", type=Path)
    add.add_argument("--role", required=True)
    add.add_argument("--kind", choices=["observed", "external_resource", "exploratory", "derived"], required=True)
    for name in ("list", "show", "doctor"):
        item = sub.add_parser(name)
        item.add_argument("project", type=Path)
        if name == "show":
            item.add_argument("asset_id")


def handle_project_command(args: argparse.Namespace) -> int:
    if args.command == "project":
        workspace = ProjectWorkspace.initialize(args.root) if args.project_command == "init" else ProjectWorkspace.open(args.root)
        return _print(project_status(workspace))
    workspace = ProjectWorkspace.open(args.project)
    if args.command == "runs":
        if args.runs_command == "create":
            payload = workspace.create_run(logical_name=args.name).model_dump(mode="json")
        elif args.runs_command == "show":
            payload = _required(workspace.store.get_run(args.run_id), "analysis run", args.run_id).model_dump(mode="json")
        else:
            payload = {"runs": [item.model_dump(mode="json") for item in workspace.store.list_runs(workspace.project.project_id)]}
        return _print(payload)
    if args.command == "conversations":
        if args.conversations_command == "start":
            payload = workspace.create_conversation(args.run_id or workspace.active_run().run_id, title=args.title).model_dump(mode="json")
        elif args.conversations_command == "show":
            record = _required(workspace.store.get_conversation(args.conversation_id), "conversation", args.conversation_id)
            payload = record.model_dump(mode="json") | {"turns": [item.model_dump(mode="json") for item in workspace.store.list_turns(record.conversation_id)]}
        else:
            payload = {"conversations": [item.model_dump(mode="json") for item in workspace.store.list_conversations(workspace.project.project_id)]}
        return _print(payload)
    if args.command == "reports":
        run_id = args.run_id or workspace.active_run().run_id
        revisions = workspace.store.list_report_revisions(run_id)
        if args.reports_command == "show":
            match = next((item for item in revisions if item.revision == args.revision), None)
            payload = _required(match, "report revision", str(args.revision)).model_dump(mode="json")
        else:
            payload = {"reports": [item.model_dump(mode="json") for item in revisions]}
        return _print(payload)
    registry = DataAssetRegistry(project_id=workspace.project.project_id, store=workspace.store, object_root=workspace.objects_dir)
    if args.assets_command == "add":
        payload = registry.register(args.path, role=args.role, kind=args.kind).model_dump(mode="json")
    elif args.assets_command == "show":
        asset = _required(workspace.store.get_asset(args.asset_id), "asset", args.asset_id)
        payload = asset.model_dump(mode="json") | {"locations": [item.model_dump(mode="json") for item in workspace.store.asset_locations(args.asset_id)]}
    elif args.assets_command == "doctor":
        payload = {"assets": [item.model_dump(mode="json") for item in registry.doctor_all()]}
    else:
        payload = {"assets": [item.model_dump(mode="json") for item in workspace.store.list_assets(workspace.project.project_id)]}
    return _print(payload)


def project_status(workspace: ProjectWorkspace) -> dict:
    project = workspace.store.get_project(workspace.project.project_id) or workspace.project
    runs = workspace.store.list_runs(project.project_id)
    conversations = workspace.store.list_conversations(project.project_id)
    assets = workspace.store.list_assets(project.project_id)
    return {
        "schema_version": "pertura-project-status-v1",
        "project": project.model_dump(mode="json"),
        "run_count": len(runs),
        "conversation_count": len(conversations),
        "asset_count": len(assets),
        "active_turn_ids": [item.active_turn_id for item in runs if item.active_turn_id],
    }


def _required(value, kind: str, identifier: str):
    if value is None:
        raise KeyError(f"unknown {kind}: {identifier}")
    return value


def _print(payload: dict) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0
