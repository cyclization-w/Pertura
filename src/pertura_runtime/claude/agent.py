from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from pertura_core.hashing import canonical_hash
from pertura_runtime.agent_bundle import resolve_skill_configuration
from pertura_runtime.claude.manifest import ClaudeRunManifest
from pertura_runtime.claude.options import (
    ClaudeRuntimeOptions,
    build_agent_options,
    describe_options,
    runtime_policy,
)
from pertura_runtime.claude.prompt import build_default_task, write_prompt_files
from pertura_runtime.claude.python_env import (
    SCIENCE_PACKAGES,
    PythonEnvironmentError,
    prepare_python_environment,
)
from pertura_runtime.claude.stream import ClaudeStreamRenderer
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.network_policy import NetworkAccessPolicy
from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.product_tools import PRODUCT_TOOL_CONTRACTS
from pertura_runtime.project.lifecycle import TurnCheckpointManager, provider_configuration_hash
from pertura_runtime.project.models import TurnStatus
from pertura_runtime.project.turns import parse_turn_draft
from pertura_runtime.project.workspace import ProjectWorkspace


@dataclass(frozen=True)
class ClaudeRunResult:
    status: str
    workspace: Path
    result_text: str = ""
    error: str | None = None


class ClaudePerturaAgent:
    """Claude adapter over Pertura-owned project/conversation/turn state."""

    def __init__(
        self,
        *,
        workspace: ClaudeRunWorkspace,
        config: ClaudeRuntimeOptions | None = None,
        output_fn: Callable[[str], None] = print,
        verbose: bool = True,
        raw_stream: bool = False,
        project_workspace: ProjectWorkspace | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config or ClaudeRuntimeOptions()
        self.stream = ClaudeStreamRenderer(output_fn=output_fn, verbose=verbose, raw_stream=raw_stream)
        self.manifest = ClaudeRunManifest(workspace)
        self.policy = runtime_policy(self.config)
        self.project_workspace = project_workspace
        self.run_id = run_id or workspace.root.name
        self.conversation_id = conversation_id
        if project_workspace is not None:
            if project_workspace.store.get_run(self.run_id) is None:
                raise ValueError(f"unknown analysis run: {self.run_id}")
            if conversation_id is None:
                matching = [
                    item for item in project_workspace.store.list_conversations(project_workspace.project.project_id)
                    if item.run_id == self.run_id and item.status == "active"
                ]
                conversation = matching[-1] if matching else project_workspace.create_conversation(self.run_id)
                self.conversation_id = conversation.conversation_id
        self.product_runtime = PerturaProductRuntime(
            workspace,
            policy=self.policy,
            network_policy=(
                NetworkAccessPolicy.literature_europepmc()
                if self.config.allow_literature_network
                else NetworkAccessPolicy.offline()
            ),
            project_workspace=project_workspace,
            run_id=self.run_id,
        )
        self.turn_manager: TurnCheckpointManager | None = None

    async def run(self, task: str | None = None) -> ClaudeRunResult:
        task_text = task or build_default_task(self.workspace.input_source)
        runtime_config = self._prepare_turn(task_text)
        try:
            python_environment = prepare_python_environment(
                runtime_config.python_exe,
                base_env={**__import__("os").environ, **runtime_config.env},
                required_packages=_required_preflight_packages(runtime_config),
                timeout_s=runtime_config.python_preflight_timeout_s,
            )
        except PythonEnvironmentError as exc:
            error = f"{type(exc).__name__}: {exc}"
            if exc.payload:
                self.workspace.write_json(self.workspace.logs_dir / "python_env.json", exc.payload)
            self.workspace.update_manifest({
                "claude_runtime_options": describe_options(runtime_config),
                "task": task_text,
                "interaction_mode": runtime_config.interaction_mode,
                "stage_id": runtime_config.stage_id,
                "python_environment_error": exc.payload or {"error": str(exc)},
            })
            runtime_final = await self._checkpoint(status="failed", error=error)
            return ClaudeRunResult(status="failed", workspace=self.workspace.root, result_text=runtime_final, error=error)

        self.workspace.write_json(
            self.workspace.logs_dir / "python_env.json",
            {"python_environment": python_environment.to_manifest()},
        )
        runtime_config = replace(
            runtime_config,
            env={**runtime_config.env, **python_environment.env_overlay},
        )
        system_prompt = write_prompt_files(
            self.workspace,
            task=task_text,
            python_environment=python_environment,
            interaction_mode=runtime_config.interaction_mode,
            stage_id=runtime_config.stage_id,
            tool_surface=runtime_config.tool_surface,
            benchmark_condition=runtime_config.benchmark_condition,
        )
        self.workspace.update_manifest({
            "claude_runtime_options": describe_options(runtime_config),
            "task": task_text,
            "python_environment": python_environment.to_manifest(),
            "interaction_mode": runtime_config.interaction_mode,
            "stage_id": runtime_config.stage_id,
            "agent_provider": "claude-agent-sdk",
            "provider_configuration_hash": (
                self.turn_manager.configuration_hash if self.turn_manager else None
            ),
        })

        try:
            from claude_agent_sdk import ClaudeSDKClient
        except ModuleNotFoundError as exc:
            message = (
                "claude-agent-sdk is not installed. Install with "
                "`python -m pip install 'pertura[llm]'` or "
                "`python -m pip install 'claude-agent-sdk>=0.1.62,<0.3'`."
            )
            await self._checkpoint(status="failed", error=message)
            raise RuntimeError(message) from exc

        skill_config = resolve_skill_configuration(
            enable_bundled=runtime_config.enable_bundled_skills,
            additional_plugin_paths=runtime_config.additional_skill_plugins,
        )
        expected_skills = tuple(sorted(skill_config.skill_names))
        options = build_agent_options(
            workspace=self.workspace,
            system_prompt=system_prompt,
            config=runtime_config,
            product_runtime=self.product_runtime,
        )
        try:
            skills_validated = False
            async with ClaudeSDKClient(options=options) as client:
                await client.query(task_text)
                async for message in client.receive_response():
                    self.stream.render(message)
                    self.manifest.capture(message)
                    self._record_provider_event(message)
                    if self.manifest.init_seen and not skills_validated:
                        _validate_sdk_skill_surface(expected_skills, self.manifest.init_skills)
                        skills_validated = True
            if not skills_validated:
                raise RuntimeError("Claude Agent SDK did not report the initialized skill surface")
            if self.turn_manager and self.manifest.session_id:
                self.turn_manager.bind_provider_session(self.manifest.session_id)
            status = "failed" if self.manifest.is_error else "completed"
            error = _sdk_result_error(self.manifest) if self.manifest.is_error else None
            self.manifest.flush(status=status)
            runtime_final = await self._checkpoint(status=status, error=error)
            return ClaudeRunResult(status=status, workspace=self.workspace.root, result_text=runtime_final, error=error)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.manifest.flush(status="failed")
            runtime_final = await self._checkpoint(status="failed", error=error)
            return ClaudeRunResult(status="failed", workspace=self.workspace.root, result_text=runtime_final, error=error)

    async def start_or_resume_turn(self, user_message: str, **_context: Any) -> ClaudeRunResult:
        return await self.run(user_message)

    async def repair_turn_draft(self, raw_output: str, error: str, **_context: Any) -> str:
        return await self._repair_turn_draft(raw_output, error)

    async def cancel_turn(self, turn_id: str) -> None:
        if self.turn_manager is None or self.turn_manager.turn is None:
            raise RuntimeError("no active Pertura turn")
        if self.turn_manager.turn.turn_id != turn_id:
            raise ValueError("turn_id does not match the active turn")
        self.product_runtime.close(graceful=False)
        await self.turn_manager.finish(
            status=TurnStatus.cancelled,
            raw_output="Provider turn cancelled.",
            resolve_result=self.product_runtime.resolve_result_for_turn,
            render_mode=self._turn_render_mode(),
        )

    async def close(self) -> None:
        self.product_runtime.close(graceful=True)

    def _prepare_turn(self, task_text: str) -> ClaudeRuntimeOptions:
        if self.project_workspace is None or self.conversation_id is None:
            return self.config
        skill_config = resolve_skill_configuration(
            enable_bundled=self.config.enable_bundled_skills,
            additional_plugin_paths=self.config.additional_skill_plugins,
        )
        configuration = describe_options(self.config)
        configuration.pop("resume_session_id", None)
        config_hash = provider_configuration_hash(configuration)
        self.turn_manager = TurnCheckpointManager(
            project=self.project_workspace,
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            provider_id="claude-agent-sdk",
            model=self.config.model,
            tool_hash=canonical_hash(PRODUCT_TOOL_CONTRACTS),
            skill_bundle_hash=str(skill_config.provenance["skill_bundle_hash"]),
            configuration_hash=config_hash,
        )
        resume = self.turn_manager.continuation_session_id()
        self.turn_manager.begin(task_text)
        return replace(self.config, resume_session_id=resume)

    def _record_provider_event(self, message: Any) -> None:
        if self.turn_manager is None or self.turn_manager.turn is None:
            return
        provider_id = getattr(message, "uuid", None) or getattr(message, "id", None)
        event_id = (
            f"claude:{provider_id}"
            if provider_id
            else f"{self.turn_manager.turn.turn_id}:event:{self.manifest.message_count}"
        )
        payload = {
            "provider": "claude-agent-sdk",
            "message_type": type(message).__name__,
            "session_id": getattr(message, "session_id", None),
            "is_error": getattr(message, "is_error", None),
            "subtype": getattr(message, "subtype", None),
        }
        self.turn_manager.record_event(event_id, payload)

    async def _checkpoint(self, *, status: str, error: str | None = None) -> str:
        raw_output = self.manifest.result_text or error or ""
        try:
            self.product_runtime.close(graceful=True)
        except Exception:
            self.product_runtime.close(graceful=False)

        if self.turn_manager is not None:
            turn_status = TurnStatus.completed if status == "completed" else TurnStatus.failed
            if status == "completed":
                try:
                    draft = parse_turn_draft(raw_output)
                except Exception:
                    pass
                else:
                    if draft.questions_for_user and not draft.findings:
                        turn_status = TurnStatus.needs_input
            final = await self.turn_manager.finish(
                status=turn_status,
                raw_output=raw_output,
                resolve_result=self.product_runtime.resolve_result_for_turn,
                repair=self._repair_turn_draft if status == "completed" else None,
                usage={
                    "total_cost_usd": self.manifest.total_cost_usd,
                    "provider_turns": self.manifest.num_turns,
                    "message_count": self.manifest.message_count,
                },
                trace={"provider_session_id": self.manifest.session_id},
                render_mode=self._turn_render_mode(),
            )
            self.workspace.update_manifest({
                "runtime_final_path": f"turns/{final.turn_id}/turn_final.md",
                "turn_final_path": f"turns/{final.turn_id}/turn_final.json",
                "claude_final_path": "logs/claude_final.md" if (self.workspace.logs_dir / "claude_final.md").exists() else None,
                "conversation_id": self.conversation_id,
                "turn_id": final.turn_id,
            })
            self.workspace.finalize(status=status, result=final.markdown, error=error)
            return final.markdown

        # Compatibility for callers that still construct a single-run workspace.
        runtime_final = raw_output or (
            "# Pertura capability run failed\n\n"
            + (error or "The runtime failed before a provider final was available.")
            + "\n"
        )
        self.workspace.write_text(self.workspace.reports_dir / "pertura_final.md", runtime_final)
        self.workspace.update_manifest({
            "runtime_final_path": "reports/pertura_final.md",
            "turn_final_path": None,
            "claude_final_path": "logs/claude_final.md" if (self.workspace.logs_dir / "claude_final.md").exists() else None,
        })
        self.workspace.finalize(status=status, result=runtime_final, error=error)
        return runtime_final

    def _turn_render_mode(self) -> str:
        return (
            "baseline"
            if self.config.benchmark_condition in {"prompt_only", "free_codeact"}
            else "pertura"
        )

    async def _repair_turn_draft(self, raw_output: str, error: str) -> str:
        if not self.manifest.session_id:
            raise RuntimeError("provider session is unavailable for TurnDraft repair")
        from claude_agent_sdk import ClaudeSDKClient

        repair_config = replace(
            self.config,
            resume_session_id=self.manifest.session_id,
            allowed_tools=[],
            enable_bundled_skills=False,
            additional_skill_plugins=(),
            enable_audit_hooks=False,
            max_turns=2,
        )
        prompt = (
            "Repair the following provider final into exactly one JSON object matching "
            "pertura-turn-draft-v1. Do not add scientific claims, call tools, or infer "
            "missing result IDs. Required fields: schema_version, language, headline, "
            "findings, hypotheses, limitations, questions_for_user, next_steps, "
            "artifact_refs. Each finding requires finding_id, text, declared_role, "
            "result_ids, limitations. Return JSON only.\n\n"
            f"Validation error: {error}\n\nProvider final:\n{raw_output}"
        )
        options = build_agent_options(
            workspace=self.workspace,
            system_prompt="You only repair JSON structure. Scientific tools are disabled.",
            config=repair_config,
            product_runtime=self.product_runtime,
        )
        repaired = ""
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                value = getattr(message, "result", None)
                if value is not None:
                    repaired = str(value)
        if not repaired:
            raise RuntimeError("provider returned no repaired TurnDraft")
        if self.turn_manager and self.turn_manager.turn:
            self.turn_manager.record_event(
                f"{self.turn_manager.turn.turn_id}:repair",
                {"provider": "claude-agent-sdk", "type": "turn_draft_repair"},
            )
        return repaired


def _required_preflight_packages(config: ClaudeRuntimeOptions) -> tuple[str, ...]:
    if config.python_preflight_packages is not None:
        return tuple(config.python_preflight_packages)
    if config.stage_id == "cell_state_reference":
        return ("anndata", "pandas", "numpy")
    return tuple(SCIENCE_PACKAGES)


def _sdk_result_error(manifest: ClaudeRunManifest) -> str:
    detail = manifest.result_text or manifest.result_subtype or "unknown"
    return f"Claude SDK result error: {detail}"


def _validate_sdk_skill_surface(expected: tuple[str, ...], observed: tuple[str, ...]) -> None:
    managed_prefixes = {"pertura:"}
    managed_prefixes.update(
        f"{name.split(':', 1)[0]}:"
        for name in expected
        if ":" in name
    )
    observed_managed = tuple(
        sorted(
            name
            for name in observed
            if any(name.startswith(prefix) for prefix in managed_prefixes)
        )
    )
    if observed_managed != expected:
        raise RuntimeError(
            "Claude Agent SDK initialized an unexpected skill surface; "
            f"expected_managed={list(expected)!r}, "
            f"observed_managed={list(observed_managed)!r}, "
            f"provider_native={list(sorted(set(observed) - set(observed_managed)))!r}"
        )
