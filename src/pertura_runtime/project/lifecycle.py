from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pertura_core.hashing import canonical_hash
from pertura_runtime.project.models import (
    ProviderSessionBinding,
    TurnFinal,
    TurnRecord,
    TurnStatus,
)
from pertura_runtime.project.store import ProjectStore
from pertura_runtime.project.turns import (
    parse_turn_draft,
    render_baseline_turn_draft,
    render_turn_draft,
    working_note_final,
    write_turn_final,
)
from pertura_runtime.project.workspace import ProjectWorkspace


class TurnCheckpointManager:
    """Own one provider turn and guarantee terminal checkpoint/lock cleanup."""

    def __init__(
        self,
        *,
        project: ProjectWorkspace,
        run_id: str,
        conversation_id: str,
        provider_id: str,
        model: str | None,
        tool_hash: str,
        skill_bundle_hash: str,
        configuration_hash: str,
    ) -> None:
        conversation = project.store.get_conversation(conversation_id)
        if conversation is None or conversation.run_id != run_id:
            raise ValueError("conversation is not bound to the selected analysis run")
        self.project = project
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.provider_id = provider_id
        self.model = model
        self.tool_hash = tool_hash
        self.skill_bundle_hash = skill_bundle_hash
        self.configuration_hash = configuration_hash
        self.turn: TurnRecord | None = None

    def continuation_session_id(self) -> str | None:
        binding = self.project.store.active_provider_binding(self.conversation_id)
        if binding is None:
            return None
        if (
            binding.provider_id == self.provider_id
            and binding.configuration_hash == self.configuration_hash
            and binding.tool_hash == self.tool_hash
            and binding.skill_bundle_hash == self.skill_bundle_hash
        ):
            return binding.provider_session_id
        return None

    def begin(self, user_input: str) -> TurnRecord:
        binding = self.project.store.active_provider_binding(self.conversation_id)
        self.turn = self.project.store.begin_turn(
            self.conversation_id,
            user_input,
            provider_binding_id=binding.binding_id if binding else None,
        )
        return self.turn

    def record_event(self, event_id: str, payload: dict[str, Any]) -> bool:
        if self.turn is None:
            raise RuntimeError("turn has not started")
        return self.project.store.append_event(self.turn.turn_id, event_id, payload)

    def bind_provider_session(self, provider_session_id: str) -> ProviderSessionBinding:
        current = self.project.store.active_provider_binding(self.conversation_id)
        if current and current.provider_session_id == provider_session_id and current.configuration_hash == self.configuration_hash:
            return current
        reason = None
        if current is not None:
            reason = "provider/tool/skill/policy configuration changed; continuation boundary created"
        binding = ProviderSessionBinding(
            conversation_id=self.conversation_id,
            provider_id=self.provider_id,
            provider_session_id=provider_session_id,
            model=self.model,
            tool_hash=self.tool_hash,
            skill_bundle_hash=self.skill_bundle_hash,
            configuration_hash=self.configuration_hash,
            continuity_reason=reason,
        )
        self.project.store.put_provider_binding(binding)
        if self.turn is not None:
            self.project.store.assign_turn_binding(self.turn.turn_id, binding.binding_id)
        return binding

    async def finish(
        self,
        *,
        status: TurnStatus,
        raw_output: str,
        resolve_result: Callable[[str], Mapping[str, Any] | None],
        repair: Callable[[str, str], Awaitable[str]] | None = None,
        result_ids: tuple[str, ...] = (),
        artifact_ids: tuple[str, ...] = (),
        usage: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
        render_mode: str = "pertura",
    ) -> TurnFinal:
        if self.turn is None:
            raise RuntimeError("turn has not started")
        if render_mode not in {"pertura", "baseline"}:
            raise ValueError(f"unsupported turn render mode: {render_mode}")

        def render(draft: Any) -> TurnFinal:
            if render_mode == "baseline":
                return render_baseline_turn_draft(
                    turn_id=self.turn.turn_id,
                    status=status,
                    draft=draft,
                )
            return render_turn_draft(
                turn_id=self.turn.turn_id,
                status=status,
                draft=draft,
                resolve_result=resolve_result,
            )

        repaired = raw_output
        first_error: Exception | None = None
        try:
            draft = parse_turn_draft(raw_output)
        except Exception as exc:
            first_error = exc
            if repair is not None:
                try:
                    repaired = await repair(raw_output, str(exc))
                    draft = parse_turn_draft(repaired)
                except Exception as repair_exc:
                    final = working_note_final(
                        turn_id=self.turn.turn_id,
                        status=status,
                        raw_output=raw_output,
                        error=f"{type(first_error).__name__}: {first_error}; repair failed: {type(repair_exc).__name__}: {repair_exc}",
                    )
                else:
                    final = render(draft)
            else:
                final = working_note_final(
                    turn_id=self.turn.turn_id,
                    status=status,
                    raw_output=raw_output,
                    error=f"{type(exc).__name__}: {exc}",
                )
        else:
            final = render(draft)
        write_turn_final(self.project.run_workspace(self.run_id).root, final)
        self.project.store.complete_turn(
            self.turn.turn_id,
            status=status,
            provider_final=raw_output,
            result_ids=result_ids or final.result_ids,
            artifact_ids=artifact_ids or final.artifact_ids,
            usage=usage,
            trace=trace,
            final=final,
        )
        return final


def provider_configuration_hash(payload: Mapping[str, Any]) -> str:
    """Hash continuation-affecting public configuration, never provider secrets."""

    return canonical_hash(dict(payload))
