from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pertura_bench.agent_models import AgentNarrativeScore, JudgeManifest, narrative_passes
from pertura_core.hashing import canonical_hash


JUDGE_MODEL = "deepseek-v4-pro"
RUBRIC = {
    "scale": "0-4",
    "dimensions": [
        "scientific_completeness",
        "clarity",
        "limitations_uncertainty",
        "actionability",
    ],
    "pass": "average >= 3.0 and every dimension >= 2",
    "automatic_failures": [
        "strong overclaim",
        "prediction relabeled as measurement",
        "cell treated as independent replicate",
    ],
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

    prompt = (
        "Grade this Pertura TurnFinal using the attached rubric. Return JSON with "
        "scientific_completeness, clarity, limitations_uncertainty, actionability "
        "as integers 0-4 and a rationale. Do not change the execution verdict.\n\n"
        + json.dumps({"rubric": RUBRIC, "turn_final": turn_final, "execution_verdict": execution_verdict}, sort_keys=True)
    )
    manifest = judge_manifest(prompt)
    client = OpenAI(
        api_key=key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    score = AgentNarrativeScore.model_validate(json.loads(content))
    payload = {
        "status": "passed" if narrative_passes(score) else "failed",
        "manifest": manifest.model_dump(mode="json"),
        "score": score.model_dump(mode="json") | {"average": score.average},
        "fallback_used": False,
    }
    _write_grade(output_path, payload)
    return payload


def _write_grade(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
