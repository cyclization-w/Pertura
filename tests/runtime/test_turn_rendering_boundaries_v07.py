from __future__ import annotations

from pertura_runtime.project.models import TurnDraft, TurnFindingDraft, TurnStatus
from pertura_runtime.project.turns import render_turn_draft


def test_turn_rendering_reads_stale_state_from_result_metadata() -> None:
    draft = TurnDraft(
        headline="Virtual model review",
        findings=(
            TurnFindingDraft(
                finding_id="prediction-1",
                text="The model predicts a response.",
                declared_role="measured",
                result_ids=("result-prediction",),
            ),
        ),
    )
    result = {
        "result_id": "result-prediction",
        "source_class": "prediction",
        "verification_state": "validated_untrusted",
        "status": "supported",
        "scope": {"scope_id": "scope-1"},
        "metadata": {"stale": True},
    }

    final = render_turn_draft(
        turn_id="turn-1",
        status=TurnStatus.completed,
        draft=draft,
        resolve_result=lambda result_id: result if result_id == result["result_id"] else None,
    )

    assert final.claim_authority is False
    assert final.findings[0]["role"] == "prediction"
    assert final.findings[0]["ceiling"] == "prediction"
    assert "stale_result" in final.findings[0]["limitations"]


def test_out_of_scope_result_is_non_supporting() -> None:
    draft = TurnDraft(
        headline="Virtual model review",
        findings=(
            TurnFindingDraft(
                finding_id="prediction-1",
                text="The evaluator could not assess this scope.",
                result_ids=("result-prediction",),
            ),
        ),
    )
    result = {
        "result_id": "result-prediction",
        "source_class": "prediction",
        "verification_state": "validated_untrusted",
        "status": "out_of_scope",
        "scope": {"scope_id": "scope-1"},
        "metadata": {},
    }

    final = render_turn_draft(
        turn_id="turn-2",
        status=TurnStatus.completed,
        draft=draft,
        resolve_result=lambda result_id: result if result_id == result["result_id"] else None,
    )

    assert "non_supporting_status" in final.findings[0]["limitations"]
