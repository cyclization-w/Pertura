from __future__ import annotations

from pathlib import Path

from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_gate.evidence.registry import EvidenceRegistry
from pertura_gate.resolver.resolver import resolve_artifact_strength, resolve_claims
from pertura_gate.resolver.warrant import surface_for_artifact
from pertura_gate.core.schema import Claim, ClaimDecision, EvidenceArtifact, RenderedReport, StrengthCeiling


def render_evidence_report(
    *,
    registry: EvidenceRegistry,
    artifact_ids: list[str] | None = None,
    claims: list[dict | Claim] | None = None,
    title: str = "Pertura Evidence Report",
    write_path: Path | None = None,
    policy: GatePolicy = DEFAULT_POLICY,
) -> RenderedReport:
    artifacts, unresolved_refs = _select_artifacts(registry, artifact_ids)
    resolutions = [resolve_artifact_strength(artifact, policy=policy) for artifact in artifacts]
    decisions: list[ClaimDecision] = []
    if claims:
        decisions = resolve_claims(claims, registry, policy=policy)

    lines = [f"# {title}", ""]
    lines.append(f"- Policy version: `{policy.version}`")
    lines.append(f"- Policy hash: `{policy.policy_hash}`")
    lines.append("")

    if decisions:
        lines.extend(_render_decisions(decisions))
        lines.append("")
        lines.extend(_render_decision_table(decisions))
        lines.append("")
    elif not artifacts:
        missing = resolve_artifact_strength(None, policy=policy)
        lines.extend(
            [
                "No registered measured evidence artifacts support a scientific conclusion in this run.",
                "",
                f"- Evidence tier: `{missing.tier.value}`",
                f"- Strength ceiling: `{missing.ceiling.value}`",
                f"- Reason: {missing.reasons[0]}",
                "",
            ]
        )
        if unresolved_refs:
            lines.append("Unresolved artifact references:")
            for ref in unresolved_refs:
                lines.append(f"- `{ref}`")
            lines.append("")
        resolutions = [missing]
    else:
        for artifact, resolution in zip(artifacts, resolutions):
            lines.extend(_render_artifact_section(artifact, resolution.ceiling))
            if resolution.reasons:
                lines.append("")
                lines.append("Execution checks:")
                for reason in resolution.reasons:
                    lines.append(f"- {reason}")
            lines.append("")
        if unresolved_refs:
            lines.append("Unresolved artifact references:")
            for ref in unresolved_refs:
                lines.append(f"- `{ref}`")
            lines.append("")

    if artifacts:
        lines.extend(_render_evidence_table(artifacts, resolutions))
        lines.append("")

    markdown = "\n".join(lines).rstrip() + "\n"
    if write_path is not None:
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_text(markdown, encoding="utf-8")
    return RenderedReport(
        markdown=markdown,
        artifacts=artifacts,
        resolutions=resolutions,
        decisions=decisions,
        report_path=write_path,
    )


def _select_artifacts(registry: EvidenceRegistry, artifact_ids: list[str] | None) -> tuple[list[EvidenceArtifact], list[str]]:
    if not artifact_ids:
        return registry.list(), []
    artifacts: list[EvidenceArtifact] = []
    unresolved: list[str] = []
    for artifact_ref in artifact_ids:
        artifact = registry.get_by_id_or_path(artifact_ref)
        if artifact is None:
            unresolved.append(artifact_ref)
        else:
            artifacts.append(artifact)
    return artifacts, unresolved


def _render_decisions(decisions: list[ClaimDecision]) -> list[str]:
    lines = ["## Runtime-calibrated findings", ""]
    for decision in decisions:
        lines.append(f"### Claim `{decision.claim_id}`")
        lines.append("")
        lines.append(decision.allowed_surface)
        lines.append("")
        lines.append(f"- Decision: `{decision.decision.value}`")
        lines.append(f"- Claim strength ceiling: `{decision.max_strength.value}`")
        lines.append(f"- Scope fit: `{decision.scope_fit.value}`")
        if decision.supporting_artifacts:
            lines.append(f"- Supporting artifacts: {', '.join(f'`{item}`' for item in decision.supporting_artifacts)}")
        if decision.missing_artifacts:
            lines.append(f"- Missing artifacts: {', '.join(f'`{item}`' for item in decision.missing_artifacts)}")
        if decision.blocked_requested_strength:
            blocked = decision.blocked_requested_strength.value if hasattr(decision.blocked_requested_strength, "value") else decision.blocked_requested_strength
            lines.append(f"- Blocked requested strength: `{blocked}`")
        if decision.reasons:
            lines.append("- Decision reasons: " + "; ".join(decision.reasons))
        lines.append("")
    return lines


def _render_decision_table(decisions: list[ClaimDecision]) -> list[str]:
    lines = ["## Evidence / decision table", ""]
    lines.append("| claim | max_strength | evidence_class | scope_fit | supporting_artifacts | downgrade_reason |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for decision in decisions:
        evidence_classes = ", ".join(item.value for item in decision.evidence_classes) or "none"
        supporting = ", ".join(decision.supporting_artifacts) or "none"
        reason = "; ".join(decision.reasons) if decision.reasons else "none"
        lines.append(
            f"| `{decision.claim_id}` | `{decision.max_strength.value}` | `{evidence_classes}` | "
            f"`{decision.scope_fit.value}` | `{supporting}` | {reason} |"
        )
    return lines


def _render_artifact_section(artifact: EvidenceArtifact, ceiling: StrengthCeiling) -> list[str]:
    lines = [
        f"## Evidence `{artifact.artifact_id}`",
        "",
        f"- Artifact kind: `{artifact.kind.value}`",
        f"- Evidence class: `{artifact.effective_evidence_class.value}`",
        f"- Artifact path: `{artifact.path}`",
        f"- Artifact intrinsic ceiling: `{ceiling.value}`",
        "",
        surface_for_artifact(artifact, ceiling),
    ]
    return lines

def _render_evidence_table(artifacts: list[EvidenceArtifact], resolutions) -> list[str]:
    lines = ["## Registered evidence artifacts", ""]
    lines.append("| artifact | kind | evidence_class | intrinsic_ceiling | source_hash |")
    lines.append("| --- | --- | --- | --- | --- |")
    for artifact, resolution in zip(artifacts, resolutions):
        lines.append(
            f"| `{artifact.artifact_id}` | `{artifact.kind.value}` | `{artifact.effective_evidence_class.value}` | "
            f"`{resolution.ceiling.value}` | `{artifact.source_sha256 or 'not recorded'}` |"
        )
    return lines


