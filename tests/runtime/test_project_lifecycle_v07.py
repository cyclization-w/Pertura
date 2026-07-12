from __future__ import annotations

import json
from pathlib import Path

import pytest

from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import TurnStatus
from pertura_runtime.project.turns import parse_turn_draft, render_turn_draft, working_note_final
from pertura_runtime.project.workspace import ProjectWorkspace


def test_project_run_conversation_and_single_active_turn_survive_restart(tmp_path: Path) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "study")
    run = project.create_run(logical_name="screen")
    conversation = project.create_conversation(run.run_id)
    turn = project.store.begin_turn(conversation.conversation_id, "inspect the screen")

    with pytest.raises(RuntimeError, match="active turn"):
        project.store.begin_turn(conversation.conversation_id, "race")

    project.store.append_event(turn.turn_id, "provider-event-1", {"type": "assistant"})
    assert project.store.append_event(turn.turn_id, "provider-event-1", {"duplicate": True}) is False
    project.store.complete_turn(
        turn.turn_id,
        status=TurnStatus.needs_input,
        provider_final="need replicate identity",
    )
    reopened = ProjectWorkspace.open(project.root)
    assert reopened.store.get_turn(turn.turn_id).status == TurnStatus.needs_input
    assert reopened.store.get_run(run.run_id).active_turn_id is None


def test_asset_identity_excludes_path_and_detects_drift(tmp_path: Path) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "study")
    first = tmp_path / "one" / "modules.gmt"
    second = tmp_path / "two" / "renamed.gmt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("M\tdesc\tA\tB\n", encoding="utf-8")
    second.write_bytes(first.read_bytes())
    registry = DataAssetRegistry(project_id=project.project.project_id, store=project.store, object_root=project.objects_dir)

    a = registry.register(first, role="gene_modules", kind="external_resource")
    b = registry.register(second, role="gene_modules", kind="external_resource")

    assert a.content_sha256 == b.content_sha256
    assert a.asset_id == b.asset_id
    assert a.identity_hash == b.identity_hash
    location = project.store.asset_locations(a.asset_id)[0]
    Path(location.absolute_path).write_text("changed", encoding="utf-8")
    assert registry.doctor(a.asset_id).status == "drifted"


def test_turn_draft_downgrades_unbound_and_candidate_findings() -> None:
    draft = parse_turn_draft(json.dumps({
        "schema_version": "pertura-turn-draft-v1",
        "language": "en",
        "headline": "Screen review",
        "findings": [
            {"finding_id": "a", "text": "candidate effect", "declared_role": "measured", "result_ids": ["candidate"]},
            {"finding_id": "b", "text": "unbound fact", "declared_role": "measured", "result_ids": []},
        ],
    }))
    result = {
        "result_id": "candidate",
        "source_class": "measured_result",
        "verification_state": "validated_untrusted",
        "status": "completed",
        "scope": {"scope_id": "scope-1"},
    }
    final = render_turn_draft(
        turn_id="turn-1",
        status=TurnStatus.completed,
        draft=draft,
        resolve_result=lambda result_id: result if result_id == "candidate" else None,
    )
    assert final.claim_authority is False
    assert final.findings[0]["ceiling"] == "exploratory_measured"
    assert "unbound fact" in final.hypotheses

    fallback = working_note_final(turn_id="turn-2", status=TurnStatus.failed, raw_output="plain text", error="invalid JSON")
    assert fallback.structured is False
    assert fallback.claim_authority is False
