from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from pertura_gate.core.policy import DEFAULT_POLICY
from pertura_workflow.harvest import harvest_artifacts_from_workspace
from pertura_workflow.models import HarvestMode, WorkflowRunManifest, WorkflowRunStep
from pertura_workflow.preflight import preflight_workspace
from pertura_workflow.recommend import recommend_next_evidence
from pertura_workflow.recipes import run_classic_perturbseq


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pertura", description="Pertura bounded evidence-acquisition workflow CLI.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    preflight = subcommands.add_parser("preflight", help="Inspect a workspace and report evidence readiness.")
    _add_workspace_args(preflight)

    harvest = subcommands.add_parser("harvest", help="Harvest candidate artifacts from a workspace.")
    _add_workspace_args(harvest)
    harvest.add_argument(
        "--mode",
        choices=[item.value for item in HarvestMode],
        default=HarvestMode.candidate_only.value,
        help="Harvest registration mode.",
    )
    harvest.add_argument("--registry", type=Path, default=None, help="Optional evidence registry path for future strict registration.")

    recommend = subcommands.add_parser("recommend-next", help="Recommend missing evidence from preflight readiness.")
    _add_workspace_args(recommend)

    recipe = subcommands.add_parser("recipe", help="Run a bounded Pertura evidence-acquisition recipe.")
    recipe_subcommands = recipe.add_subparsers(dest="recipe_name", required=True)
    classic = recipe_subcommands.add_parser("classic", help="Run the classic guide-based Perturb-seq recipe skeleton.")
    _add_workspace_args(classic)
    classic.add_argument("--harvest-mode", choices=[item.value for item in HarvestMode], default=HarvestMode.candidate_only.value)

    explain = subcommands.add_parser("explain", help="Explain a ClaimDecision from a decisions JSON file.")
    explain.add_argument("decision_id", type=str)
    explain.add_argument("--decisions", type=Path, required=True, help="Path to a decisions JSON file.")
    explain.add_argument("--format", choices=["json", "markdown"], default="markdown")
    explain.add_argument("--out", type=Path, default=None)
    explain.add_argument("--run-manifest", type=Path, default=None)

    args = parser.parse_args(argv)
    if args.command == "preflight":
        report = preflight_workspace(args.workspace, mode=args.interaction_mode)
        payload = report.to_dict()
        output_text = json.dumps(payload, indent=2, sort_keys=True) if args.format == "json" else report.to_markdown()
        _write_or_print(output_text, args.out)
        _maybe_write_run_manifest(args, [WorkflowRunStep("preflight", "passed", output_paths=_paths(args.out))])
        return 0
    if args.command == "harvest":
        report = harvest_artifacts_from_workspace(args.workspace, mode=args.mode, registry_path=args.registry)
        payload = report.to_dict()
        output_text = json.dumps(payload, indent=2, sort_keys=True) if args.format == "json" else report.to_markdown()
        _write_or_print(output_text, args.out)
        _maybe_write_run_manifest(args, [WorkflowRunStep("harvest", "passed", output_paths=_paths(args.out), notes=report.reasons)])
        return 0
    if args.command == "recommend-next":
        preflight_report = preflight_workspace(args.workspace, mode=args.interaction_mode)
        goals = recommend_next_evidence(preflight_report)
        payload = {"workspace": preflight_report.workspace, "evidence_goals": [goal.to_dict() for goal in goals]}
        if args.format == "json":
            output_text = json.dumps(payload, indent=2, sort_keys=True)
        else:
            output_text = _goals_markdown(preflight_report.workspace, goals)
        _write_or_print(output_text, args.out)
        _maybe_write_run_manifest(args, [WorkflowRunStep("recommend_next", "passed", output_paths=_paths(args.out))])
        return 0
    if args.command == "recipe" and args.recipe_name == "classic":
        result = run_classic_perturbseq(args.workspace, mode=args.interaction_mode, harvest_mode=args.harvest_mode)
        payload = result.to_dict()
        output_text = json.dumps(payload, indent=2, sort_keys=True) if args.format == "json" else result.report_markdown
        _write_or_print(output_text, args.out)
        if args.run_manifest is not None and result.workflow_run_manifest is not None:
            args.run_manifest.parent.mkdir(parents=True, exist_ok=True)
            args.run_manifest.write_text(json.dumps(result.workflow_run_manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return 0
    if args.command == "explain":
        text = _explain_decision(args.decision_id, args.decisions, args.format)
        _write_or_print(text, args.out)
        _maybe_write_run_manifest(args, [WorkflowRunStep("explain", "passed", output_paths=_paths(args.out))])
        return 0
    return 2


def _add_workspace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--interaction-mode", choices=["benchmark", "interactive"], default="benchmark")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--run-manifest", type=Path, default=None)


def _write_or_print(text: str, path: Path | None) -> None:
    if path is None:
        print(text)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _paths(path: Path | None) -> list[str]:
    return [str(path)] if path is not None else []


def _maybe_write_run_manifest(args: argparse.Namespace, steps: list[WorkflowRunStep]) -> None:
    path = getattr(args, "run_manifest", None)
    if path is None:
        return
    workspace = str(getattr(args, "workspace", ""))
    mode = str(getattr(args, "interaction_mode", "benchmark"))
    manifest = WorkflowRunManifest(
        workflow_run_id=f"workflow_run_{uuid4().hex[:12]}",
        command=str(args.command),
        workspace=workspace,
        mode=mode,
        policy_hash=DEFAULT_POLICY.policy_hash,
        inputs={key: str(value) for key, value in vars(args).items() if key not in {"out", "run_manifest"} and value is not None},
        steps=steps,
        output_paths=[path for step in steps for path in step.output_paths],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def _goals_markdown(workspace: str, goals) -> str:
    lines = ["# Pertura Recommended Next Evidence", "", f"- Workspace: `{workspace}`", ""]
    if not goals:
        lines.append("No missing evidence goals were identified.")
    for goal in goals:
        lines.append(f"- `{goal.claim_type}` missing `{goal.missing}` ({goal.priority}): {goal.recommendation}")
    return "\n".join(lines) + "\n"


def _explain_decision(decision_id: str, decisions_path: Path, output_format: str) -> str:
    payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions = payload.get("decisions") if isinstance(payload, dict) else payload
    for decision in decisions or []:
        if str(decision.get("decision_id") or decision.get("claim_id")) == decision_id:
            if output_format == "json":
                return json.dumps(decision, indent=2, sort_keys=True)
            reasons = decision.get("reasons") or []
            lines = [
                f"# ClaimDecision `{decision_id}`",
                "",
                f"- Claim: `{decision.get('claim_id')}`",
                f"- Decision: `{decision.get('decision')}`",
                f"- Max strength: `{decision.get('max_strength')}`",
                f"- Scope fit: `{decision.get('scope_fit')}`",
                "",
                "## Allowed Surface",
                "",
                str(decision.get("allowed_surface") or ""),
                "",
                "## Reasons",
                "",
            ]
            lines.extend(f"- {reason}" for reason in reasons)
            return "\n".join(lines) + "\n"
    raise SystemExit(f"decision not found: {decision_id}")


if __name__ == "__main__":
    raise SystemExit(main())
