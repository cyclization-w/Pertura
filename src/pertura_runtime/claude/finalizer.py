from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.render.renderer import render_evidence_report


_CALIBRATED_MARKERS = (
    "## Runtime-calibrated findings",
    "## Evidence / decision table",
)


def build_runtime_final_summary(
    workspace: ClaudeRunWorkspace,
    *,
    status: str,
    error: str | None = None,
) -> str:
    """Build the runtime-owned final summary shown to users.

    Claude's free-form final is preserved in logs/claude_final.md, but this
    summary is the CLI-facing completion surface for scientific runs.
    """

    evidence_report = _ensure_evidence_report(workspace)
    _write_analysis_state_manifest(workspace, evidence_report)
    lines = ["# Pertura Runtime Final", ""]
    lines.append(f"- Status: `{status}`")
    if error:
        lines.append(f"- Error: {error}")
    if evidence_report is not None:
        lines.append(f"- Evidence report: `{_rel(workspace, evidence_report)}`")
    else:
        lines.append("- Evidence report: not generated")
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


def _ensure_evidence_report(workspace: ClaudeRunWorkspace) -> Path | None:
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
    paths: list[Path] = []
    for root in [workspace.artifacts_dir, workspace.outputs_dir]:
        if not root.exists():
            continue
        for path in root.glob("*decisions*.json"):
            if path.is_file():
                paths.append(path)
    preferred = workspace.artifacts_dir / "claim_decisions.json"
    return sorted(paths, key=lambda path: (path != preferred, -path.stat().st_mtime_ns, path.name))


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


def _write_analysis_state_manifest(workspace: ClaudeRunWorkspace, evidence_report: Path | None) -> None:
    registry = EvidenceRegistry.for_run(workspace.root)
    artifacts = [artifact.to_dict() for artifact in registry.list()]
    decisions = _load_run_decisions(workspace)
    payload = {
        "schema_version": "pertura-analysis-state-v1",
        "registry_path": _rel(workspace, registry.path),
        "artifact_ids": [artifact.get("artifact_id") for artifact in artifacts],
        "decision_ids": [decision.get("decision_id") for decision in decisions if decision.get("decision_id")],
        "policy_hashes": sorted({str(decision.get("policy_hash")) for decision in decisions if decision.get("policy_hash")}),
        "evidence_report": _rel(workspace, evidence_report) if evidence_report else None,
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
    return [
        path for path in workspace.reports_dir.glob("*.md")
        if path.name not in {"pertura_final.md", "claude_final.md"} and path.is_file()
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


