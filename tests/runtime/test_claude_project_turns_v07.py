from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions
from pertura_runtime.project.models import TurnStatus
from pertura_runtime.project.workspace import ProjectWorkspace


def _draft(headline: str = "Checkpoint") -> str:
    return json.dumps({
        "schema_version": "pertura-turn-draft-v1",
        "language": "en",
        "headline": headline,
        "findings": [],
        "hypotheses": [],
        "limitations": [],
        "questions_for_user": [],
        "next_steps": [],
        "artifact_refs": [],
    })


def test_claude_continuation_uses_provider_session_without_history_replay(tmp_path: Path) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "study")
    run = project.create_run()
    conversation = project.create_conversation(run.run_id)
    workspace = project.run_workspace(run.run_id)

    first = ClaudePerturaAgent(
        workspace=workspace,
        config=ClaudeRuntimeOptions(enable_bundled_skills=False),
        project_workspace=project,
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
    )
    first_config = first._prepare_turn("first user message")
    assert first_config.resume_session_id is None
    first.turn_manager.bind_provider_session("claude-session-1")
    asyncio.run(first.turn_manager.finish(
        status=TurnStatus.completed,
        raw_output=_draft(),
        resolve_result=lambda _: None,
    ))

    second = ClaudePerturaAgent(
        workspace=workspace,
        config=ClaudeRuntimeOptions(enable_bundled_skills=False),
        project_workspace=project,
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
    )
    second_config = second._prepare_turn("second user message")
    assert second_config.resume_session_id == "claude-session-1"
    turns = project.store.list_turns(conversation.conversation_id)
    assert [item.user_input for item in turns] == ["first user message", "second user message"]
    project.store.complete_turn(second.turn_manager.turn.turn_id, status=TurnStatus.cancelled, provider_final="cancelled")


def test_project_turn_checkpoint_does_not_implicitly_finalize_report(tmp_path: Path) -> None:
    project = ProjectWorkspace.initialize(tmp_path / "study")
    run = project.create_run()
    conversation = project.create_conversation(run.run_id)
    workspace = project.run_workspace(run.run_id)
    agent = ClaudePerturaAgent(
        workspace=workspace,
        config=ClaudeRuntimeOptions(enable_bundled_skills=False),
        project_workspace=project,
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
    )
    agent._prepare_turn("ordinary turn")
    agent.manifest.result_text = _draft("Ordinary checkpoint")

    rendered = asyncio.run(agent._checkpoint(status="completed"))

    assert "Ordinary checkpoint" in rendered
    assert project.store.list_report_revisions(run.run_id) == ()
    turn = project.store.list_turns(conversation.conversation_id)[0]
    assert project.store.get_turn_final(turn.turn_id).structured is True
