from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from pertura_runtime.claude.manifest import ClaudeRunManifest
from pertura_runtime.claude.options import (
    ClaudeRuntimeOptions,
    build_agent_options,
    describe_options,
    runtime_policy,
)
from pertura_runtime.claude.prompt import build_default_task, write_prompt_files
from pertura_runtime.claude.python_env import SCIENCE_PACKAGES, PythonEnvironmentError, prepare_python_environment
from pertura_runtime.claude.stream import ClaudeStreamRenderer
from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime


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
        self.policy = runtime_policy(self.config)
        self.product_runtime = PerturaProductRuntime(workspace, policy=self.policy)

    async def run(self, task: str | None = None) -> ClaudeRunResult:
        task_text = task or build_default_task(self.workspace.input_source)
        try:
            python_environment = prepare_python_environment(
                self.config.python_exe,
                base_env={**__import__("os").environ, **self.config.env},
                required_packages=_required_preflight_packages(self.config),
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
                    "interaction_mode": self.config.interaction_mode,
                    "stage_id": self.config.stage_id,
                    "python_environment_error": exc.payload or {"error": str(exc)},
                }
            )
            runtime_final = self._finalize_with_runtime_summary(status="failed", error=error)
            return ClaudeRunResult(status="failed", workspace=self.workspace.root, result_text=runtime_final, error=error)

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
            tool_surface=self.config.tool_surface,
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
            self._finalize_with_runtime_summary(status="failed", error=message)
            raise RuntimeError(message) from exc

        options = build_agent_options(
            workspace=self.workspace,
            system_prompt=system_prompt,
            config=runtime_config,
            product_runtime=self.product_runtime,
        )
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(task_text)
                async for message in client.receive_response():
                    self.stream.render(message)
                    self.manifest.capture(message)
            status = "failed" if self.manifest.is_error else "completed"
            error = _sdk_result_error(self.manifest) if self.manifest.is_error else None
            self.manifest.flush(status=status)
            runtime_final = self._finalize_with_runtime_summary(status=status, error=error)
            return ClaudeRunResult(
                status=status,
                workspace=self.workspace.root,
                result_text=runtime_final,
                error=error,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.manifest.flush(status="failed")
            runtime_final = self._finalize_with_runtime_summary(status="failed", error=error)
            return ClaudeRunResult(status="failed", workspace=self.workspace.root, result_text=runtime_final, error=error)

    def _finalize_with_runtime_summary(self, *, status: str, error: str | None = None) -> str:
        if status == "completed":
            self.product_runtime.finalize_report(self.workspace.root.name)
            runtime_final = (self.workspace.reports_dir / "pertura_final.md").read_text(encoding="utf-8")
            self.product_runtime.close(graceful=True)
        else:
            self.product_runtime.close(graceful=False)
            runtime_final = "# Pertura capability run failed\n\n" + (error or "The runtime failed before a verified result was finalized.") + "\n"
            self.workspace.write_text(self.workspace.reports_dir / "pertura_final.md", runtime_final)
        self.workspace.update_manifest(
            {
                "runtime_final_path": "reports/pertura_final.md",
                "turn_final_path": None,
                "claude_final_path": "logs/claude_final.md" if (self.workspace.logs_dir / "claude_final.md").exists() else None,
            }
        )
        self.workspace.finalize(status=status, result=runtime_final, error=error)
        return runtime_final
def _required_preflight_packages(config: ClaudeRuntimeOptions) -> tuple[str, ...]:
    if config.python_preflight_packages is not None:
        return tuple(config.python_preflight_packages)
    if config.stage_id == "cell_state_reference":
        return ("anndata", "pandas", "numpy")
    return tuple(SCIENCE_PACKAGES)

def _sdk_result_error(manifest: ClaudeRunManifest) -> str:
    detail = manifest.result_text or manifest.result_subtype or "unknown"
    return f"Claude SDK result error: {detail}"
