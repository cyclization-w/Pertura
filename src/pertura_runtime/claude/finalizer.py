from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.stages.catalog import StageCatalogError, load_stage_contract
from pertura_runtime.stages.turn_final import STAGE_RESULT_STATUSES, TURN_FINAL_SURFACE_TYPES, TurnFinal
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.core.policy import GatePolicy, policy_for_profile
from pertura_gate.render.renderer import render_evidence_report
from pertura_gate.resolver.resolver import resolve_artifact_strength


_CALIBRATED_MARKERS = (
    "## Runtime-calibrated findings",
    "## Evidence / decision table",
)


def build_runtime_final_summary(
    workspace: ClaudeRunWorkspace,
    *,
    status: str,
    error: str | None = None,
    policy: GatePolicy | None = None,
) -> str:
    """Build the runtime-owned final summary shown to users.

    Claude's free-form final is preserved in logs/claude_final.md, but this
    summary is the CLI-facing completion surface for scientific runs.
    """

    bound_policy = policy or _policy_from_manifest(workspace)
    evidence_report = _ensure_evidence_report(workspace, policy=bound_policy)
    turn_final = _write_turn_final(
        workspace,
        status=status,
        evidence_report=evidence_report,
        error=error,
        policy=bound_policy,
    )
    turn_final_report = workspace.reports_dir / "turn_final.md"
    _write_analysis_state_manifest(workspace, evidence_report, turn_final_report, policy=bound_policy)
    workspace.update_manifest({"turn_final_path": "reports/turn_final.md"})

    lines = ["# Pertura Runtime Final", ""]
    lines.append(f"- Status: `{status}`")
    lines.append(f"- Claim policy: `{bound_policy.profile}` (`{bound_policy.policy_hash}`)")
    if error:
        lines.append(f"- Error: {error}")
    if evidence_report is not None:
        lines.append(f"- Evidence report: `{_rel(workspace, evidence_report)}`")
    else:
        lines.append("- Evidence report: not generated")
    lines.append("- Turn final: `reports/turn_final.md`")
    claude_final = workspace.logs_dir / "claude_final.md"
    if claude_final.exists():
        lines.append("- Claude draft final retained in internal audit logs; not the scientific conclusion surface")
    lines.append("")

    artifact_lines = _artifact_lines(workspace)
    if artifact_lines:
        lines.append("## Generated Artifacts")
        lines.append("")
        lines.extend(artifact_lines)
        lines.append("")

    if turn_final_report.exists():
        lines.append("## Runtime Turn Final")
        lines.append("")
        lines.append(turn_final_report.read_text(encoding="utf-8").strip())
        lines.append("")
    else:
        lines.append("## Runtime Turn Final")
        lines.append("")
        lines.append(f"No runtime-owned turn final is available for stage `{turn_final.stage_id}`.")
        lines.append("")

    if evidence_report is not None and evidence_report.exists():
        lines.append("## Runtime Evidence Report")
        lines.append("")
        lines.append(evidence_report.read_text(encoding="utf-8").strip())
        lines.append("")
    else:
        lines.append("## Runtime Evidence Report")
        lines.append("")
        lines.append("No runtime-rendered evidence report is available for this run.")
        lines.append("")

    summary = "\n".join(lines).rstrip() + "\n"
    workspace.write_text(workspace.reports_dir / "pertura_final.md", summary)
    return summary


def _write_turn_final(
    workspace: ClaudeRunWorkspace,
    *,
    status: str,
    evidence_report: Path | None,
    error: str | None = None,
    policy: GatePolicy,
) -> TurnFinal:
    registry = EvidenceRegistry.for_run(workspace.root)
    artifact_objects = registry.list()
    artifacts = []
    for artifact in artifact_objects:
        payload = artifact.to_dict()
        payload["artifact_intrinsic_ceiling"] = resolve_artifact_strength(artifact, policy=policy).ceiling.value
        artifacts.append(payload)
    decisions = _load_run_decisions(workspace)
    stage_id, contract = _stage_context(workspace)
    surface_type = _turn_surface_type(stage_id=stage_id, contract=contract, decisions=decisions, artifacts=artifacts)
    turn_status = _turn_status(status=status, artifacts=artifacts, decisions=decisions, evidence_report=evidence_report)
    generated_files = _generated_files(workspace)
    registered_artifacts = [str(artifact.get("artifact_id")) for artifact in artifacts if artifact.get("artifact_id")]
    claim_decisions = [_decision_claim_id(decision) for decision in decisions]
    blocked_reasons = _blocked_or_downgraded_reasons(decisions)
    artifact_kinds = Counter(str(artifact.get("kind") or artifact.get("artifact_type") or "unknown") for artifact in artifacts)
    policy_hashes = sorted({str(decision.get("policy_hash")) for decision in decisions if decision.get("policy_hash")})
    recommended_next = _recommended_next_stages(contract)
    what_was_done = _what_was_done(
        artifacts=artifacts,
        decisions=decisions,
        evidence_report=evidence_report,
        error=error,
    )
    turn_final = TurnFinal(
        stage_id=stage_id,
        status=turn_status,
        surface_type=surface_type,
        what_was_done=what_was_done,
        generated_files=generated_files,
        registered_artifacts=registered_artifacts,
        claim_decisions=claim_decisions,
        blocked_or_downgraded_reasons=blocked_reasons,
        recommended_next_stages=recommended_next,
        report_path=_rel(workspace, evidence_report) if evidence_report else None,
        metadata={
            "schema_version": "pertura-turn-final-v1",
            "artifact_kind_counts": dict(sorted(artifact_kinds.items())),
            "policy_hashes": policy_hashes,
            "runtime_policy_profile": policy.profile,
            "runtime_policy_hash": policy.policy_hash,
            "error": error,
            "stage_contract_loaded": contract is not None,
        },
    )
    workspace.write_json(workspace.artifacts_dir / "turn_final.json", turn_final.to_dict())
    workspace.write_text(workspace.reports_dir / "turn_final.md", _render_turn_final_markdown(turn_final, artifacts, decisions))
    return turn_final


def _stage_context(workspace: ClaudeRunWorkspace) -> tuple[str, dict[str, Any] | None]:
    manifest = _load_manifest(workspace)
    stage_id = str(manifest.get("stage_id") or manifest.get("stage") or "unstaged")
    if stage_id == "unstaged":
        return stage_id, None
    try:
        return stage_id, load_stage_contract(stage_id)
    except (StageCatalogError, OSError, ValueError):
        return stage_id, None


def _load_manifest(workspace: ClaudeRunWorkspace) -> dict[str, Any]:
    path = workspace.root / "manifest.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _policy_from_manifest(workspace: ClaudeRunWorkspace) -> GatePolicy:
    manifest = _load_manifest(workspace)
    trust_policy = manifest.get("trust_policy") if isinstance(manifest, dict) else None
    profile = trust_policy.get("profile") if isinstance(trust_policy, dict) else None
    # Direct finalizer callers retain the historical smoke default. Production
    # Claude runs always pass the immutable policy object explicitly.
    return policy_for_profile(str(profile or "smoke"))


def _turn_surface_type(
    *,
    stage_id: str,
    contract: dict[str, Any] | None,
    decisions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> str:
    if contract is not None:
        surface_type = str(contract.get("turn_final_surface_type") or "progress_only")
        if surface_type not in TURN_FINAL_SURFACE_TYPES:
            surface_type = "progress_only"
        if stage_id != "claim_report" and surface_type == "claim_decision_surface":
            return "evidence_summary"
        return surface_type
    if decisions:
        return "claim_decision_surface"
    if artifacts:
        return "evidence_summary"
    return "progress_only"


def _turn_status(
    *,
    status: str,
    artifacts: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    evidence_report: Path | None,
) -> str:
    if status == "completed" and not artifacts and not decisions and evidence_report is None:
        return "no_evidence_registered"
    if status in STAGE_RESULT_STATUSES:
        return status
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    return "partial"


def _generated_files(workspace: ClaudeRunWorkspace) -> list[str]:
    files = workspace.summarize_outputs().get("files") or []
    result: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        if not path or path.startswith("logs/"):
            continue
        if path in {"reports/turn_final.md", "artifacts/turn_final.json"}:
            continue
        result.append(path)
    return sorted(result)


def _recommended_next_stages(contract: dict[str, Any] | None) -> list[str]:
    if contract is None:
        return []
    values = contract.get("next_stage_recommendations") or []
    if isinstance(values, list):
        return [str(value) for value in values]
    return []


def _what_was_done(
    *,
    artifacts: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    evidence_report: Path | None,
    error: str | None,
) -> list[str]:
    items: list[str] = []
    if artifacts:
        kinds = Counter(str(artifact.get("kind") or artifact.get("artifact_type") or "unknown") for artifact in artifacts)
        kind_text = ", ".join(f"{kind}={count}" for kind, count in sorted(kinds.items()))
        items.append(f"Registered {len(artifacts)} evidence artifact(s): {kind_text}.")
    else:
        items.append("No evidence artifacts were registered.")
    if decisions:
        items.append(f"Evaluated {len(decisions)} claim(s) through ClaimDecision.")
    if evidence_report is not None:
        items.append("Rendered a runtime-controlled evidence report.")
    if error:
        items.append(f"Run ended with error: {error}")
    return items


def _decision_claim_id(decision: dict[str, Any]) -> str:
    return str(decision.get("claim_id") or decision.get("id") or decision.get("decision_id") or "claim")


def _decision_strength(decision: dict[str, Any]) -> str:
    return str(decision.get("max_strength") or decision.get("claim_strength_ceiling") or "unknown")


def _blocked_or_downgraded_reasons(decisions: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for decision in decisions:
        decision_status = str(decision.get("decision") or "")
        blocked = decision.get("blocked_requested_strength")
        if decision_status not in {"allowed_with_downgrade", "unsupported", "blocked"} and not blocked:
            continue
        reasons = decision.get("reasons") or decision.get("decision_reasons") or []
        if isinstance(reasons, str):
            reason_text = reasons
        else:
            reason_text = "; ".join(str(reason) for reason in reasons)
        pieces = []
        if blocked:
            pieces.append(f"blocked requested strength {blocked}")
        if reason_text:
            pieces.append(reason_text)
        if not pieces:
            pieces.append(decision_status or "downgraded")
        result.append(f"{_decision_claim_id(decision)}: " + "; ".join(pieces))
    return result


def _render_turn_final_markdown(
    turn_final: TurnFinal,
    artifacts: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> str:
    lines = ["# Pertura Turn Final", ""]
    lines.append(f"- Stage: `{turn_final.stage_id}`")
    lines.append(f"- Status: `{turn_final.status}`")
    lines.append(f"- Surface type: `{turn_final.surface_type}`")
    if turn_final.report_path:
        lines.append(f"- Report: `{turn_final.report_path}`")
    lines.append("")
    lines.append("## What Was Done")
    lines.append("")
    for item in turn_final.what_was_done:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Registered Artifacts")
    lines.append("")
    if artifacts:
        lines.append("| artifact | kind | evidence_class | intrinsic_ceiling |")
        lines.append("| --- | --- | --- | --- |")
        for artifact in artifacts:
            artifact_id = str(artifact.get("artifact_id") or "unknown")
            kind = str(artifact.get("kind") or artifact.get("artifact_type") or "unknown")
            evidence_class = str(artifact.get("evidence_class") or "unknown")
            ceiling = str(artifact.get("intrinsic_ceiling") or artifact.get("artifact_intrinsic_ceiling") or "unknown")
            lines.append(f"| `{artifact_id}` | `{kind}` | `{evidence_class}` | `{ceiling}` |")
    else:
        lines.append("No registered evidence artifacts.")
    lines.append("")
    lines.append("## Claim Decisions")
    lines.append("")
    if decisions:
        lines.append("| claim | decision | max_strength | scope_fit | blocked_requested_strength |")
        lines.append("| --- | --- | --- | --- | --- |")
        for decision in decisions:
            claim_id = _decision_claim_id(decision)
            decision_status = str(decision.get("decision") or "unknown")
            strength = _decision_strength(decision)
            scope_fit = str(decision.get("scope_fit") or "unknown")
            blocked = str(decision.get("blocked_requested_strength") or "none")
            lines.append(f"| `{claim_id}` | `{decision_status}` | `{strength}` | `{scope_fit}` | `{blocked}` |")
    else:
        lines.append("No claim decisions were produced in this turn.")
    lines.append("")
    lines.append("## Blocked Or Downgraded")
    lines.append("")
    if turn_final.blocked_or_downgraded_reasons:
        for reason in turn_final.blocked_or_downgraded_reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("No blocked or downgraded claim decisions were recorded.")
    lines.append("")
    lines.append("## Recommended Next Stages")
    lines.append("")
    if turn_final.recommended_next_stages:
        for stage in turn_final.recommended_next_stages:
            lines.append(f"- `{stage}`")
    else:
        lines.append("No next-stage recommendation was recorded by the active stage contract.")
    lines.append("")
    if turn_final.generated_files:
        lines.append("## Generated Files")
        lines.append("")
        for path in turn_final.generated_files:
            lines.append(f"- `{path}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _ensure_evidence_report(workspace: ClaudeRunWorkspace, *, policy: GatePolicy) -> Path | None:
    existing_calibrated = _select_existing_calibrated_report(workspace)
    if existing_calibrated is not None:
        return existing_calibrated

    registry = EvidenceRegistry.for_run(workspace.root)
    artifacts = registry.list()
    if artifacts:
        claims = _load_run_claims(workspace)
        if claims:
            report_path = workspace.reports_dir / "evidence_report.md"
            report = render_evidence_report(
                registry=registry,
                claims=claims,
                title="Pertura Evidence Report",
                write_path=report_path,
                policy=policy,
            )
            _write_claim_decisions(workspace, report)
            return report_path

    decision_report = _render_existing_decision_report(workspace)
    if decision_report is not None:
        return decision_report

    existing = _select_existing_report(workspace)
    if existing is not None:
        return existing
    if not artifacts:
        return None

    report_path = workspace.reports_dir / "evidence_report.md"
    render_evidence_report(
        registry=registry,
        title="Pertura Evidence Report",
        write_path=report_path,
        policy=policy,
    )
    return report_path


def _render_existing_decision_report(workspace: ClaudeRunWorkspace) -> Path | None:
    decisions = _load_run_decisions(workspace)
    if not decisions:
        return None
    report_path = workspace.reports_dir / "evidence_report.md"
    lines = ["# Pertura Decision Report", ""]
    policy_hashes = sorted({str(item.get("policy_hash")) for item in decisions if item.get("policy_hash")})
    if policy_hashes:
        lines.append("- Policy hash: " + ", ".join(f"`{item}`" for item in policy_hashes))
        lines.append("")
    lines.append("## Runtime-calibrated findings")
    lines.append("")
    for item in decisions:
        claim_id = str(item.get("claim_id") or item.get("id") or "claim")
        lines.append(f"### Claim `{claim_id}`")
        lines.append("")
        surface = str(item.get("allowed_surface") or "Runtime decision recorded for this claim.")
        lines.append(surface)
        lines.append("")
        if item.get("decision"):
            lines.append(f"- Decision: `{item.get('decision')}`")
        if item.get("max_strength") or item.get("claim_strength_ceiling"):
            lines.append(f"- Claim strength ceiling: `{item.get('max_strength') or item.get('claim_strength_ceiling')}`")
        if item.get("scope_fit"):
            lines.append(f"- Scope fit: `{item.get('scope_fit')}`")
        if item.get("supporting_artifacts"):
            lines.append("- Supporting artifacts: " + ", ".join(f"`{artifact}`" for artifact in item.get("supporting_artifacts") or []))
        if item.get("blocked_requested_strength"):
            lines.append(f"- Blocked requested strength: `{item.get('blocked_requested_strength')}`")
        reasons = item.get("reasons") or item.get("decision_reasons") or []
        if reasons:
            lines.append("- Decision reasons: " + "; ".join(str(reason) for reason in reasons))
        lines.append("")
    lines.extend(_render_decisions_table_from_payload(decisions))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def _render_decisions_table_from_payload(decisions: list[dict[str, Any]]) -> list[str]:
    lines = ["## Evidence / decision table", ""]
    lines.append("| claim | max_strength | evidence_class | scope_fit | supporting_artifacts | downgrade_reason |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in decisions:
        claim_id = str(item.get("claim_id") or item.get("id") or "claim")
        strength = str(item.get("max_strength") or item.get("claim_strength_ceiling") or "unknown")
        evidence_classes = item.get("evidence_classes") or item.get("evidence_class") or []
        if isinstance(evidence_classes, str):
            evidence_text = evidence_classes
        else:
            evidence_text = ", ".join(str(value) for value in evidence_classes) or "none"
        scope_fit = str(item.get("scope_fit") or "unknown")
        supporting = item.get("supporting_artifacts") or []
        supporting_text = ", ".join(str(value) for value in supporting) or "none"
        reasons = item.get("reasons") or item.get("decision_reasons") or []
        reason_text = "; ".join(str(reason) for reason in reasons) if reasons else "none"
        lines.append(f"| `{claim_id}` | `{strength}` | `{evidence_text}` | `{scope_fit}` | `{supporting_text}` | {reason_text} |")
    return lines


def _load_run_decisions(workspace: ClaudeRunWorkspace) -> list[dict[str, Any]]:
    for path in _decision_files(workspace):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        decisions = _extract_decisions(payload)
        if decisions:
            return decisions
    return []


def _decision_files(workspace: ClaudeRunWorkspace) -> list[Path]:
    canonical = workspace.artifacts_dir / "claim_decisions.json"
    return [canonical] if canonical.is_file() else []


def _extract_decisions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        direct = payload.get("decisions")
        if isinstance(direct, list):
            return [dict(item) for item in direct if isinstance(item, dict)]
        if {"claim_id", "max_strength"}.issubset(payload):
            return [dict(payload)]
        found: list[dict[str, Any]] = []
        for value in payload.values():
            found.extend(_extract_decisions(value))
        return found
    if isinstance(payload, list):
        result: list[dict[str, Any]] = []
        for item in payload:
            result.extend(_extract_decisions(item))
        return result
    return []


def _write_analysis_state_manifest(
    workspace: ClaudeRunWorkspace,
    evidence_report: Path | None,
    turn_final_report: Path | None = None,
    *,
    policy: GatePolicy,
) -> None:
    registry = EvidenceRegistry.for_run(workspace.root)
    artifact_objects = registry.list()
    artifacts = []
    for artifact in artifact_objects:
        payload = artifact.to_dict()
        payload["artifact_intrinsic_ceiling"] = resolve_artifact_strength(artifact, policy=policy).ceiling.value
        artifacts.append(payload)
    decisions = _load_run_decisions(workspace)
    payload = {
        "schema_version": "pertura-analysis-state-v1",
        "registry_path": _rel(workspace, registry.path),
        "artifact_ids": [artifact.get("artifact_id") for artifact in artifacts],
        "decision_ids": [decision.get("decision_id") for decision in decisions if decision.get("decision_id")],
        "policy_hashes": sorted({str(decision.get("policy_hash")) for decision in decisions if decision.get("policy_hash")}),
        "runtime_policy_profile": policy.profile,
        "runtime_policy_hash": policy.policy_hash,
        "evidence_report": _rel(workspace, evidence_report) if evidence_report else None,
        "turn_final": _rel(workspace, turn_final_report) if turn_final_report else None,
        "outputs": workspace.summarize_outputs(),
    }
    workspace.write_json(workspace.artifacts_dir / "analysis_state_manifest.json", payload)


def _select_existing_calibrated_report(workspace: ClaudeRunWorkspace) -> Path | None:
    reports = _report_files(workspace)
    calibrated = [path for path in reports if _is_claim_calibrated_report(path)]
    return _newest(calibrated) if calibrated else None


def _select_existing_report(workspace: ClaudeRunWorkspace) -> Path | None:
    reports = _report_files(workspace)
    default_report = workspace.reports_dir / "evidence_report.md"
    if default_report.exists():
        return default_report
    return _newest(reports) if reports else None


def _report_files(workspace: ClaudeRunWorkspace) -> list[Path]:
    if not workspace.reports_dir.exists():
        return []
    excluded = {"pertura_final.md", "claude_final.md", "turn_final.md"}
    return [
        path for path in workspace.reports_dir.glob("*.md")
        if path.name not in excluded and path.is_file()
    ]


def _is_claim_calibrated_report(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in text for marker in _CALIBRATED_MARKERS)


def _load_run_claims(workspace: ClaudeRunWorkspace) -> list[dict[str, Any]]:
    for path in _claim_files(workspace):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        claims = payload.get("claims") if isinstance(payload, dict) else payload
        if isinstance(claims, list):
            valid_claims = [claim for claim in claims if isinstance(claim, dict) and claim.get("claim_id")]
            if valid_claims:
                return _dedupe_claims(valid_claims)
    return []


def _claim_files(workspace: ClaudeRunWorkspace) -> list[Path]:
    roots = [workspace.outputs_dir, workspace.artifacts_dir]
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*claims*.json"):
            lower_name = path.name.lower()
            if "decision" in lower_name or not path.is_file():
                continue
            paths.append(path)
    return sorted(paths, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in claims:
        key = str(claim.get("claim_id") or json.dumps(claim, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(claim)
    return deduped


def _write_claim_decisions(workspace: ClaudeRunWorkspace, report) -> None:
    if not report.decisions:
        return
    output_path = workspace.artifacts_dir / "claim_decisions.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"decisions": [decision.to_dict() for decision in report.decisions]}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _newest(paths: list[Path]) -> Path:
    return max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _artifact_lines(workspace: ClaudeRunWorkspace) -> list[str]:
    rows: list[str] = []
    for base in [workspace.outputs_dir, workspace.reports_dir, workspace.artifacts_dir]:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                rows.append(f"- `{_rel(workspace, path)}`")
    return rows


def _rel(workspace: ClaudeRunWorkspace, path: Path) -> str:
    try:
        return str(path.relative_to(workspace.root)).replace("\\", "/")
    except ValueError:
        return str(path)
