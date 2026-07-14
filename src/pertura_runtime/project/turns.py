from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pertura_runtime.project.models import TurnDraft, TurnFinal, TurnStatus

ResultResolver = Callable[[str], Mapping[str, Any] | None]


def parse_turn_draft(raw_output: str) -> TurnDraft:
    """Parse a provider draft without inferring scientific role from prose."""

    text = raw_output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip("\r\n")
    return TurnDraft.model_validate(json.loads(text))


def render_turn_draft(*, turn_id: str, status: TurnStatus, draft: TurnDraft, resolve_result: ResultResolver) -> TurnFinal:
    rendered_findings: list[dict[str, Any]] = []
    hypotheses = list(draft.hypotheses)
    limitations = list(draft.limitations)
    accepted_result_ids: set[str] = set()
    for finding in draft.findings:
        resolved = [resolve_result(result_id) for result_id in finding.result_ids]
        resolved = [item for item in resolved if item is not None]
        if not resolved or len(resolved) != len(finding.result_ids):
            hypotheses.append(finding.text)
            limitations.append(f"{finding.finding_id}: no complete committed-result provenance; treated as a working hypothesis.")
            continue
        role, ceiling, reasons = _derive_role_and_ceiling(resolved)
        accepted_result_ids.update(finding.result_ids)
        rendered_findings.append({
            "finding_id": finding.finding_id,
            "text": finding.text,
            "role": role,
            "ceiling": ceiling,
            "result_ids": list(finding.result_ids),
            "limitations": list(finding.limitations) + reasons,
        })
    markdown = _render_markdown(
        headline=draft.headline,
        findings=rendered_findings,
        hypotheses=hypotheses,
        limitations=limitations,
        questions=list(draft.questions_for_user),
        next_steps=list(draft.next_steps),
        artifact_refs=list(draft.artifact_refs),
    )
    return TurnFinal(
        turn_id=turn_id,
        status=status,
        language=draft.language,
        headline=draft.headline,
        markdown=markdown,
        findings=tuple(rendered_findings),
        hypotheses=tuple(dict.fromkeys(hypotheses)),
        limitations=tuple(dict.fromkeys(limitations)),
        questions_for_user=draft.questions_for_user,
        next_steps=draft.next_steps,
        artifact_refs=draft.artifact_refs,
        result_ids=tuple(sorted(accepted_result_ids)),
        claim_authority=any(item["ceiling"] == "strong_measured" for item in rendered_findings),
    )


def render_baseline_turn_draft(
    *, turn_id: str, status: TurnStatus, draft: TurnDraft
) -> TurnFinal:
    """Preserve provider claims for controlled scoring without granting authority.

    Baseline conditions intentionally do not use Pertura result provenance or
    claim-ceiling enforcement. Their declared roles and prose must remain
    visible to the benchmark so overclaim can be measured rather than silently
    corrected by the product renderer.
    """

    findings = tuple(
        {
            "finding_id": finding.finding_id,
            "text": finding.text,
            "role": finding.declared_role,
            "ceiling": "unscored_provider_claim",
            "result_ids": [],
            "limitations": list(finding.limitations),
        }
        for finding in draft.findings
    )
    markdown = _render_markdown(
        headline=draft.headline,
        findings=list(findings),
        hypotheses=list(draft.hypotheses),
        limitations=list(draft.limitations),
        questions=list(draft.questions_for_user),
        next_steps=list(draft.next_steps),
        artifact_refs=list(draft.artifact_refs),
    )
    return TurnFinal(
        turn_id=turn_id,
        status=status,
        language=draft.language,
        headline=draft.headline,
        markdown=markdown,
        findings=findings,
        hypotheses=draft.hypotheses,
        limitations=draft.limitations,
        questions_for_user=draft.questions_for_user,
        next_steps=draft.next_steps,
        artifact_refs=draft.artifact_refs,
        result_ids=(),
        claim_authority=False,
    )


def working_note_final(*, turn_id: str, status: TurnStatus, raw_output: str, error: str) -> TurnFinal:
    markdown = (
        "# Working note\n\n"
        "The provider output did not match the TurnDraft schema after one repair attempt. "
        "This note has no claim authority.\n\n"
        f"> Format error: {error}\n\n{raw_output.strip()}\n"
    )
    return TurnFinal(
        turn_id=turn_id,
        status=status,
        headline="Unstructured working note",
        markdown=markdown,
        structured=False,
        claim_authority=False,
        format_error=error,
    )


def write_turn_final(root: Path, final: TurnFinal) -> tuple[Path, Path]:
    destination = Path(root) / "turns" / final.turn_id
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "turn_final.json"
    markdown_path = destination / "turn_final.md"
    json_path.write_text(json.dumps(final.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(final.markdown.rstrip() + "\n", encoding="utf-8")
    return json_path, markdown_path


def _derive_role_and_ceiling(results: list[Mapping[str, Any]]) -> tuple[str, str, list[str]]:
    classes = {str(item.get("source_class") or "hypothesis") for item in results}
    states = {str(item.get("verification_state") or "unverified") for item in results}
    statuses = {str(item.get("status") or "failed") for item in results}
    stale = any(
        bool(item.get("stale"))
        or item.get("dependency_state") == "stale"
        or bool((item.get("metadata") or {}).get("stale"))
        or (item.get("metadata") or {}).get("dependency_state") == "stale"
        for item in results
    )
    scopes = {str((item.get("scope") or {}).get("scope_id") or "") for item in results}
    reasons: list[str] = []
    if len(scopes) != 1:
        reasons.append("mixed_scope")
    if stale:
        reasons.append("stale_result")
    if statuses.intersection({"blocked", "failed", "unresolved", "out_of_scope"}):
        reasons.append("non_supporting_status")
    if classes == {"measured_result"}:
        policy_ceilings = {str(item.get("rendering_ceiling") or "exploratory_measured") for item in results}
        strong = policy_ceilings == {"strong_measured"} and not reasons
        if not strong:
            reasons.append("candidate_or_policy_downgraded_measured_result")
        reasons.extend(
            str(reason)
            for item in results
            for reason in item.get("promotion_reasons") or ()
        )
        return "measured", "strong_measured" if strong else "exploratory_measured", list(dict.fromkeys(reasons))
    if classes == {"prediction"}:
        return "prediction", "prediction", reasons
    if classes == {"curated_prior"}:
        return "prior", "prior", reasons
    if classes == {"observed_metadata"}:
        return "derived", "observed_metadata", reasons
    if classes == {"hypothesis"}:
        return "hypothesis", "hypothesis", reasons
    reasons.append("mixed_source_class")
    return "hypothesis", "hypothesis", reasons


def _render_markdown(*, headline: str, findings: list[dict[str, Any]], hypotheses: list[str], limitations: list[str], questions: list[str], next_steps: list[str], artifact_refs: list[str]) -> str:
    lines = [f"# {headline}", ""]
    sections = (
        ("Findings", [f"{item['text']}  \n  _Role: {item['role']}; ceiling: {item['ceiling']}; results: {', '.join(item['result_ids'])}_" for item in findings]),
        ("Working hypotheses", hypotheses),
        ("Limitations", limitations + [value for item in findings for value in item["limitations"]]),
        ("Questions for you", questions),
        ("Next steps", next_steps),
        ("Artifacts", artifact_refs),
    )
    for title, items in sections:
        if items:
            lines.extend([f"## {title}", "", *(f"- {item}" for item in items), ""])
    return "\n".join(lines).rstrip() + "\n"
