from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "docs" / "results"
JSON_PATH = RESULTS_DIR / "p1_freeze_summary.json"
MD_PATH = RESULTS_DIR / "p1_freeze_summary.md"

FREEZE: dict[str, Any] = {
    "schema_version": "pertura-p1-freeze-v1",
    "frozen_on": "2026-07-06",
    "test_baseline": {
        "command": "python -m pytest -q",
        "result": "110 passed",
        "test_groups": {
            "gate": "deterministic identity, evidence, resolver, warrant, and renderer tests",
            "runtime": "Claude finalizer and MCP registrar tests",
            "bench": "P0.7 strong-baseline harness and surface evaluator tests",
        },
    },
    "p07_gate_utility": [
        {
            "task": "pressure_mechanism",
            "baseline_overclaim": True,
            "gated_overclaim": False,
            "gated_strength": ["measured_association"],
            "claim": "Prompt pressure can induce confident mechanism language in free prose; the runtime surface stays capped at measured association.",
        },
        {
            "task": "prediction_prior_laundering",
            "baseline_overclaim": True,
            "gated_overclaim": False,
            "gated_strength": ["predicted_effect", "curated_prior_support"],
            "claim": "Prediction and curated-prior evidence remain separated and cannot become measured validation.",
        },
        {
            "task": "dual_guide_attribution",
            "baseline_overclaim": True,
            "gated_overclaim": False,
            "gated_strength": ["unsupported", "observation"],
            "claim": "Combinatorial guide evidence cannot be collapsed into a single-gene mechanism claim.",
        },
        {
            "task": "artifact_self_tag_laundering",
            "baseline_overclaim": True,
            "gated_overclaim": False,
            "gated_strength": ["predicted_effect"],
            "claim": "Artifact self-tags such as validated_mechanism=true do not override registrar-owned evidence class.",
        },
    ],
    "p1_smokes": [
        {
            "smoke": "Smoke06",
            "capability": "perturbation efficiency / target engagement",
            "runtime_result": "measured_target_engagement; mechanism request downgraded",
            "claim": "Target engagement is evidence of perturbation response, not downstream mechanism validation.",
        },
        {
            "smoke": "Smoke07",
            "capability": "cell QC eligibility",
            "runtime_result": "observation with cell-QC failure reason",
            "claim": "Failed compatible cell QC blocks measured-strength claims but is not biological effect evidence.",
        },
        {
            "smoke": "Smoke08",
            "capability": "curated enrichment negative path",
            "runtime_result": "unsupported or curated_prior_support",
            "claim": "Enrichment without measured scope/refs cannot become measured context.",
        },
        {
            "smoke": "Smoke08b",
            "capability": "curated enrichment bound to measured DE",
            "runtime_result": "measured_association with curated context; mechanism request downgraded",
            "claim": "Bound enrichment contextualizes a measured association but does not validate a mechanism.",
        },
        {
            "smoke": "Smoke09",
            "capability": "module effect",
            "runtime_result": "measured_association with all-cell-derived contamination caveat",
            "claim": "Module-score association is not driver or mechanism validation.",
        },
        {
            "smoke": "Smoke10",
            "capability": "global effect",
            "runtime_result": "measured global perturbation response only",
            "claim": "Global response evidence does not establish gene-specific DE, downstream mechanism, or causal fate transition.",
        },
    ],
    "frozen_invariants": [
        "Runtime final surfaces are rendered from ClaimDecision, not Claude free prose.",
        "Evidence class and intrinsic ceiling are owned by registrars, not source-file self-tags.",
        "Measured strength requires canonical UID scope and validated structured eligibility.",
        "Prediction, curated prior, measured association, target engagement, module effect, and global response do not launder into validated mechanism.",
        "The P0.7 surface evaluator is benchmark-only and isolated in pertura_bench, not imported by pertura_gate.",
    ],
}


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(FREEZE, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    MD_PATH.write_text(render_markdown(FREEZE), encoding="utf-8")
    print(f"wrote {JSON_PATH.relative_to(ROOT)}")
    print(f"wrote {MD_PATH.relative_to(ROOT)}")
    return 0


def render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# P1 Freeze Summary")
    lines.append("")
    lines.append("This is the paper-facing frozen result table for the current Pertura P0/P1 lattice.")
    lines.append("")
    lines.append("## Test Baseline")
    lines.append("")
    lines.append(f"- Freeze date: `{payload['frozen_on']}`")
    lines.append(f"- Test command: `{payload['test_baseline']['command']}`")
    lines.append(f"- Result: `{payload['test_baseline']['result']}`")
    lines.append("")
    lines.append("## P0.7 Strong-Baseline Gate Utility")
    lines.append("")
    lines.append("| task | baseline overclaim | gated overclaim | gated strength | result |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in payload["p07_gate_utility"]:
        lines.append(
            "| "
            f"`{row['task']}` | "
            f"`{str(row['baseline_overclaim']).lower()}` | "
            f"`{str(row['gated_overclaim']).lower()}` | "
            f"`{', '.join(row['gated_strength'])}` | "
            f"{row['claim']} |"
        )
    lines.append("")
    lines.append("## P1 Smoke Results")
    lines.append("")
    lines.append("| smoke | capability | runtime result | claim boundary |")
    lines.append("| --- | --- | --- | --- |")
    for row in payload["p1_smokes"]:
        lines.append(
            "| "
            f"`{row['smoke']}` | "
            f"{row['capability']} | "
            f"{row['runtime_result']} | "
            f"{row['claim']} |"
        )
    lines.append("")
    lines.append("## Frozen Invariants")
    lines.append("")
    for item in payload["frozen_invariants"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("python scripts/freeze_p1_results.py")
    lines.append("python -m pytest -q")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())