from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pertura_bench.agent_models import (
    AgentNarrativeScore,
    JudgeAnswerProjection,
    JudgeManifest,
    narrative_passes,
)
from pertura_core.hashing import canonical_hash


JUDGE_MODEL = "deepseek-v4-pro"
RUBRIC = {
    "scale": {
        "0": "absent or scientifically unusable",
        "1": "major omissions or errors",
        "2": "partly adequate with material limitations",
        "3": "complete and useful with bounded caveats",
        "4": "exceptionally complete, precise and actionable",
    },
    "dimensions": {
        "scientific_completeness": "Addresses the supplied objective and reports the analysis unit, main result and required caveats.",
        "clarity": "Uses precise language and separates observations, estimates, predictions and hypotheses.",
        "limitations_uncertainty": "States uncertainty, design limitations, blockers and plausible alternative explanations.",
        "actionability": "Gives concrete next analyses or experiments that follow from the bounded result.",
    },
    "pass": "average >= 3.0 and every dimension >= 2",
    "automatic_failures": {
        "strong_overclaim": "Claims proof, scientific validation or a confirmed mechanism beyond the supplied evidence.",
        "prediction_as_measurement": "Presents model prediction as an observed or measured effect.",
        "cell_as_independent_replicate": "Treats cells as independent biological replicates for population inference.",
    },
}

def judge_manifest(prompt: str) -> JudgeManifest:
    return JudgeManifest(
        provider="deepseek",
        model=JUDGE_MODEL,
        prompt_hash=canonical_hash(prompt),
        rubric_hash=canonical_hash(RUBRIC),
        temperature=0.0,
        fallback_allowed=False,
    )


def grade_turn_final(
    turn_final: dict[str, Any],
    *,
    execution_verdict: dict[str, Any],
    task_context: dict[str, Any] | None = None,
    output_path: Path,
) -> dict[str, Any]:
    """Grade an immutable projection; never mutate the execution workspace."""

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        payload = {
            "status": "judge_unavailable",
            "model": JUDGE_MODEL,
            "fallback_used": False,
            "reason": "DEEPSEEK_API_KEY is missing",
        }
        _write_grade(output_path, payload)
        return payload
    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        payload = {
            "status": "judge_unavailable",
            "model": JUDGE_MODEL,
            "fallback_used": False,
            "reason": "OpenAI-compatible client is not installed",
        }
        _write_grade(output_path, payload)
        return payload

    del execution_verdict  # condition/provider/execution outcomes must not bias narrative grading
    answer = project_judge_answer(turn_final)
    context = dict(task_context or {})
    prompt = (
        "Act as a condition-blind scientific writing judge. Grade only the supplied "
        "answer against the task context and anchored rubric. Return JSON with "
        "scientific_completeness, clarity, limitations_uncertainty and actionability "
        "as integers 0-4, a concise rationale, and automatic_failures as a JSON list "
        "containing only the exact rubric keys that are present (or an empty list). "
        "Do not infer which benchmark condition or tool surface produced the answer.\n\n"
        + json.dumps(
            {
                "task_context": context,
                "rubric": RUBRIC,
                "answer": answer.model_dump(mode="json"),
            },
            sort_keys=True,
        )
    )
    manifest = judge_manifest(prompt)
    client = OpenAI(
        api_key=key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    try:
        response = client.chat.completions.create(
            model=JUDGE_MODEL,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        score = AgentNarrativeScore.model_validate(json.loads(content))
    except Exception as exc:
        payload = {
            "status": "judge_unavailable",
            "model": JUDGE_MODEL,
            "manifest": manifest.model_dump(mode="json"),
            "fallback_used": False,
            "reason": f"judge request or response validation failed: {type(exc).__name__}: {exc}",
        }
        _write_grade(output_path, payload)
        return payload
    payload = {
        "status": "passed" if narrative_passes(score) else "failed",
        "manifest": manifest.model_dump(mode="json"),
        "score": score.model_dump(mode="json") | {"average": score.average},
        "fallback_used": False,
    }
    _write_grade(output_path, payload)
    return payload


def project_judge_answer(turn_final: dict[str, Any]) -> JudgeAnswerProjection:
    """Remove provider, condition, tool and authority fingerprints before grading."""

    findings = []
    for raw in turn_final.get("findings") or ():
        if isinstance(raw, dict):
            text = str(raw.get("text") or "").strip()
        else:
            text = str(raw).strip()
        if text:
            findings.append(text)
    return JudgeAnswerProjection(
        headline=str(turn_final.get("headline") or "").strip(),
        finding_texts=tuple(findings),
        hypotheses=tuple(str(item) for item in turn_final.get("hypotheses") or ()),
        limitations=tuple(str(item) for item in turn_final.get("limitations") or ()),
        next_steps=tuple(str(item) for item in turn_final.get("next_steps") or ()),
        artifact_citations=tuple(
            str(item) for item in turn_final.get("artifact_refs") or ()
        ),
    )


def _write_grade(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
