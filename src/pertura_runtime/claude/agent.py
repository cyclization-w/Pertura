from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from pertura_runtime.claude.finalizer import build_runtime_final_summary
from pertura_runtime.claude.manifest import ClaudeRunManifest
from pertura_runtime.claude.options import (
    ClaudeRuntimeOptions,
    build_agent_options,
    describe_options,
)
from pertura_runtime.claude.prompt import build_default_task, write_prompt_files
from pertura_runtime.claude.python_env import PythonEnvironmentError, prepare_python_environment
from pertura_runtime.claude.stream import ClaudeStreamRenderer
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


@dataclass(frozen=True)
class ClaudeRunResult:
    status: str
    workspace: Path
    result_text: str = ""
    error: str | None = None


class ClaudePerturaAgent:
    """Thin Claude Agent SDK runner for Pertura CodeAct v0."""

    def __init__(
        self,
        *,
        workspace: ClaudeRunWorkspace,
        config: ClaudeRuntimeOptions | None = None,
        output_fn: Callable[[str], None] = print,
        verbose: bool = True,
        raw_stream: bool = False,
    ) -> None:
        self.workspace = workspace
        self.config = config or ClaudeRuntimeOptions()
        self.stream = ClaudeStreamRenderer(output_fn=output_fn, verbose=verbose, raw_stream=raw_stream)
        self.manifest = ClaudeRunManifest(workspace)

    async def run(self, task: str | None = None) -> ClaudeRunResult:
        task_text = task or build_default_task(self.workspace.input_source)
        try:
            python_environment = prepare_python_environment(
                self.config.python_exe,
                base_env={**__import__("os").environ, **self.config.env},
                timeout_s=self.config.python_preflight_timeout_s,
            )
        except PythonEnvironmentError as exc:
            error = f"{type(exc).__name__}: {exc}"
            if exc.payload:
                self.workspace.write_json(self.workspace.logs_dir / "python_env.json", exc.payload)
            self.workspace.update_manifest(
                {
                    "claude_runtime_options": describe_options(self.config),
                    "task": task_text,
                    "python_environment_error": exc.payload or {"error": str(exc)},
                }
            )
            self.workspace.finalize(status="failed", error=error)
            return ClaudeRunResult(status="failed", workspace=self.workspace.root, error=error)

        self.workspace.write_json(
            self.workspace.logs_dir / "python_env.json",
            {"python_environment": python_environment.to_manifest()},
        )
        runtime_config = replace(
            self.config,
            env={**self.config.env, **python_environment.env_overlay},
        )
        system_prompt = write_prompt_files(
            self.workspace,
            task=task_text,
            python_environment=python_environment,
            interaction_mode=self.config.interaction_mode,
            stage_id=self.config.stage_id,
        )
        self.workspace.update_manifest(
            {
                "claude_runtime_options": describe_options(runtime_config),
                "task": task_text,
                "python_environment": python_environment.to_manifest(),
                "interaction_mode": runtime_config.interaction_mode,
                "stage_id": runtime_config.stage_id,
            }
        )

        try:
            from claude_agent_sdk import ClaudeSDKClient
        except ModuleNotFoundError as exc:
            message = (
                "claude-agent-sdk is not installed. Install with "
                "`python -m pip install 'pertura-gate[llm]'` or "
                "`python -m pip install claude-agent-sdk`."
            )
            self.workspace.finalize(status="failed", error=message)
            raise RuntimeError(message) from exc

        options = build_agent_options(
            workspace=self.workspace,
            system_prompt=system_prompt,
            config=runtime_config,
        )
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(task_text)
                async for message in client.receive_response():
                    self.stream.render(message)
                    self.manifest.capture(message)
            status = "failed" if self.manifest.is_error else "completed"
            error = f"Claude SDK result error: {self.manifest.result_subtype or 'unknown'}" if self.manifest.is_error else None
            self.manifest.flush(status=status)
            runtime_final = build_runtime_final_summary(self.workspace, status=status, error=error)
            self.workspace.update_manifest(
                {
                    "runtime_final_path": "reports/pertura_final.md",
                    "claude_final_path": "logs/claude_final.md" if (self.workspace.logs_dir / "claude_final.md").exists() else None,
                }
            )
            self.workspace.finalize(status=status, result=runtime_final, error=error)
            return ClaudeRunResult(
                status=status,
                workspace=self.workspace.root,
                result_text=runtime_final,
                error=error,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.manifest.flush(status="failed")
            self.workspace.finalize(status="failed", error=error)
            return ClaudeRunResult(status="failed", workspace=self.workspace.root, error=error)





