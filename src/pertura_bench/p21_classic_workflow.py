from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pertura_gate.core.policy import DEFAULT_POLICY, GatePolicy
from pertura_workflow.recipes import run_classic_perturbseq


@dataclass(frozen=True)
class P21CaseResult:
    case_id: str
    workspace: str
    completion: bool
    decision_strengths: list[str]
    decision_ids: list[str]
    linked_claims: int
    unlinked_claims: int
    runner_steps: list[str]
    report_path: str | None
    policy_hash: str
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "workspace": self.workspace,
            "completion": self.completion,
            "decision_strengths": list(self.decision_strengths),
            "decision_ids": list(self.decision_ids),
            "linked_claims": self.linked_claims,
            "unlinked_claims": self.unlinked_claims,
            "runner_steps": list(self.runner_steps),
            "report_path": self.report_path,
            "policy_hash": self.policy_hash,
            "notes": list(self.notes),
        }


def run_p21_case(case_id: str, *, root: str | Path, policy: GatePolicy = DEFAULT_POLICY) -> P21CaseResult:
    workspace = Path(root).expanduser().resolve() / _safe_case_id(case_id)
    workspace.mkdir(parents=True, exist_ok=True)
    _write_case_workspace(case_id, workspace)
    result = run_classic_perturbseq(workspace, policy=policy)
    decisions_payload = _read_decisions_payload(workspace)
    decisions = decisions_payload.get("decisions", []) if isinstance(decisions_payload, dict) else []
    links = decisions_payload.get("candidate_claim_links", []) if isinstance(decisions_payload, dict) else []
    runner_steps = [step.name for step in (result.workflow_run_manifest.steps if result.workflow_run_manifest else []) if step.name.startswith("run_basic_")]
    report_path = workspace / "reports" / "evidence_report.md"
    notes = _case_notes(case_id, result.report_markdown)
    return P21CaseResult(
        case_id=case_id,
        workspace=str(workspace),
        completion=_case_completion(case_id, result.report_markdown, decisions, links),
        decision_strengths=[str(item.get("max_strength")) for item in decisions if isinstance(item, dict)],
        decision_ids=[str(item.get("decision_id")) for item in decisions if isinstance(item, dict) and item.get("decision_id")],
        linked_claims=sum(1 for item in links if isinstance(item, dict) and item.get("status") == "linked"),
        unlinked_claims=sum(1 for item in links if isinstance(item, dict) and item.get("status") == "unlinked"),
        runner_steps=runner_steps,
        report_path=str(report_path) if report_path.exists() else None,
        policy_hash=policy.policy_hash,
        notes=notes,
    )


def run_p21_suite(*, root: str | Path | None = None, policy: GatePolicy = DEFAULT_POLICY) -> list[P21CaseResult]:
    case_ids = [
        "strict_measured_association",
        "basic_runners_measured_association",
        "candidate_claim_gap",
        "partial_success_missing_manifest",
    ]
    if root is None:
        with tempfile.TemporaryDirectory(prefix="pertura_p21_") as tmp:
            return [run_p21_case(case_id, root=tmp, policy=policy) for case_id in case_ids]
    root_path = Path(root).expanduser().resolve()
    return [run_p21_case(case_id, root=root_path, policy=policy) for case_id in case_ids]


def write_p21_summary(results: list[P21CaseResult], *, output_dir: str | Path) -> tuple[Path, Path]:
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "p21_freeze_summary.json"
    md_path = out / "p21_freeze_summary.md"
    payload = {
        "schema_version": "pertura-p21-classic-workflow-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "results": [result.to_dict() for result in results],
        "invariants": [
            "classic workflow reports are rendered through ClaimDecision when linked claims exist",
            "candidate claims without DesignManifest UID scope remain gaps, not scientific findings",
            "basic runners produce structured tables/summaries only and do not write biological conclusions",
            "partial-success workspaces report missing evidence instead of upgrading claim strength",
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_render_summary_markdown(payload), encoding="utf-8")
    return md_path, json_path


def _write_case_workspace(case_id: str, root: Path) -> None:
    if case_id == "partial_success_missing_manifest":
        (root / "klf1_vs_negctrl_de.csv").write_text("gene,logfc,padj\nKLF1,-1,0.01\n", encoding="utf-8")
        return
    if case_id == "basic_runners_measured_association":
        _write_expression_metadata(root)
        _write_config(root, measured_de=False, basic_runners=True, candidate_gap=False)
        return
    if case_id == "candidate_claim_gap":
        _write_measured_de(root)
        _write_config(root, measured_de=True, basic_runners=False, candidate_gap=True)
        return
    if case_id == "strict_measured_association":
        _write_measured_de(root)
        _write_config(root, measured_de=True, basic_runners=False, candidate_gap=False)
        return
    raise ValueError(f"unknown P2.1 case: {case_id}")


def _write_measured_de(root: Path) -> None:
    (root / "klf1_de.csv").write_text("gene,logfc,padj\nKLF1,-1.2,0.01\nGYPA,-0.7,0.03\n", encoding="utf-8")


def _write_expression_metadata(root: Path) -> None:
    (root / "expression.csv").write_text(
        "cell_id,KLF1,GYPA\n"
        "c1,10,6\n"
        "c2,12,5\n"
        "c3,2,1\n"
        "c4,1,2\n",
        encoding="utf-8",
    )
    (root / "metadata.csv").write_text(
        "cell_id,perturbation_uid,guide\n"
        "c1,target:KLF1,sgKLF1_1\n"
        "c2,target:KLF1,sgKLF1_2\n"
        "c3,control:negative_control_pool,NegCtrl0\n"
        "c4,control:negative_control_pool,NegCtrl1\n",
        encoding="utf-8",
    )
    (root / "guide_map.csv").write_text(
        "guide,target\nsgKLF1_1,KLF1\nsgKLF1_2,KLF1\nNegCtrl0,negative_control\nNegCtrl1,negative_control\n",
        encoding="utf-8",
    )


def _write_config(root: Path, *, measured_de: bool, basic_runners: bool, candidate_gap: bool) -> None:
    config: dict[str, Any] = {
        "dataset_id": "synthetic_norman_p21",
        "raw_labels": ["KLF1_NegCtrl0__KLF1_NegCtrl0", "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0"],
        "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
        "controls": {"negative_controls": ["NegCtrl0", "NegCtrl1"]},
        "experiment_design": {
            "assay": "Perturb-seq",
            "perturbation_modality": "guide_based_perturb_seq",
            "moi": "low",
            "controls": {"negative_controls": ["NegCtrl0", "NegCtrl1"]},
        },
        "guide_assignment": {
            "assignment_method": "synthetic_guide_calling",
            "assigned_count": 4 if basic_runners else 40,
            "unassigned_count": 0,
            "multi_guide_count": 0,
            "target_summary": {"KLF1": 2 if basic_runners else 20, "NegCtrl": 2 if basic_runners else 20},
        },
        "claim": {
            "claim_id": "p21_strict_mechanism_overclaim",
            "text": "KLF1 validates an erythroid mechanism in this Perturb-seq experiment.",
            "requested_strength": "validated_mechanism_disabled",
        },
    }
    if measured_de:
        config["target_qc"] = {
            "target": "KLF1",
            "control": "NegCtrl0",
            "n_target_cells": 20,
            "n_control_cells": 20,
            "guides_per_target": 1,
            "guide_consistency": "single_guide_synthetic",
        }
        config["measured_de"] = {
            "path": "klf1_de.csv",
            "contrast_left": "KLF1_NegCtrl0__KLF1_NegCtrl0",
            "contrast_baseline": "NegCtrl0_NegCtrl0__NegCtrl0_NegCtrl0",
            "method": "synthetic_wilcoxon",
            "n_left": 20,
            "n_baseline": 20,
            "multiple_testing": "BH",
            "has_padj": True,
        }
    if basic_runners:
        config["basic_target_qc"] = {
            "metadata_csv": "metadata.csv",
            "guide_column": "guide",
            "guide_to_target_csv": "guide_map.csv",
            "target": "KLF1",
            "control": "NegCtrl pool",
            "minimum_cells": 2,
        }
        config["basic_de"] = {
            "expression_csv": "expression.csv",
            "metadata_csv": "metadata.csv",
            "layer": "normalized_counts",
            "condition_column": "perturbation_uid",
        }
    if candidate_gap:
        config.pop("claim", None)
        config["candidate_claims"] = [
            {
                "claim_id": "linked_klf1_claim",
                "text": "KLF1 validates an erythroid mechanism.",
                "perturbation_raw_label": "KLF1_NegCtrl0__KLF1_NegCtrl0",
                "requested_strength": "validated_mechanism_disabled",
            },
            {
                "claim_id": "unlinked_dusp9_claim",
                "text": "DUSP9 validates a mechanism.",
                "perturbation_raw_label": "DUSP9_NegCtrl0__DUSP9_NegCtrl0",
                "requested_strength": "validated_mechanism_disabled",
            },
        ]
    (root / "classic_recipe_config.json").write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


def _read_decisions_payload(workspace: Path) -> dict[str, Any]:
    path = workspace / "artifacts" / "claim_decisions.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _case_completion(case_id: str, report: str, decisions: list[Any], links: list[Any]) -> bool:
    if case_id == "partial_success_missing_manifest":
        return "partial_success" in report and not decisions
    if case_id == "candidate_claim_gap":
        return bool(decisions) and any(isinstance(item, dict) and item.get("status") == "unlinked" for item in links) and "Candidate Claim Gaps" in report
    if case_id == "basic_runners_measured_association":
        return bool(decisions) and "Claim strength ceiling: `measured_association`" in report
    if case_id == "strict_measured_association":
        return bool(decisions) and "Claim strength ceiling: `measured_association`" in report
    return False


def _case_notes(case_id: str, report: str) -> list[str]:
    if case_id == "partial_success_missing_manifest":
        return ["incomplete workspace produced partial-success evidence-gap report"]
    if case_id == "candidate_claim_gap":
        return ["linked candidate evaluated; unlinked candidate remained a gap"]
    if case_id == "basic_runners_measured_association":
        return ["basic target QC and basic DE runners produced structured outputs before gate evaluation"]
    return ["strict structured classic recipe produced measured association and downgraded mechanism request"]


def _render_summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P2.1 Classic Workflow Freeze Summary",
        "",
        "This table freezes deterministic P2.1 classic guide-based Perturb-seq workflow behavior.",
        "",
        "| case | completion | strengths | linked claims | unlinked claims | runner steps | notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in payload["results"]:
        lines.append(
            "| "
            f"`{row['case_id']}` | "
            f"`{str(row['completion']).lower()}` | "
            f"`{', '.join(row['decision_strengths']) or 'none'}` | "
            f"`{row['linked_claims']}` | "
            f"`{row['unlinked_claims']}` | "
            f"`{', '.join(row['runner_steps']) or 'none'}` | "
            f"{'; '.join(row['notes'])} |"
        )
    lines.extend(["", "## Frozen Invariants", ""])
    for item in payload["invariants"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _safe_case_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value).strip("_") or "case"