from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pertura_runtime.claude.agent import ClaudePerturaAgent
from pertura_runtime.claude.options import ClaudeRuntimeOptions
from pertura_runtime.claude.workspace import ClaudeRunWorkspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pertura-claude",
        description="Run the claim-free Pertura Claude Agent SDK runtime.",
    )
    parser.add_argument(
        "--input", type=Path, default=None, help="Input dataset directory or file."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".claude_runs"),
        help="Run root directory.",
    )
    parser.add_argument(
        "--run-id", type=str, default=None, help="Optional stable run directory name."
    )
    parser.add_argument("--model", type=str, default=None, help="Claude model name.")
    parser.add_argument(
        "--python-exe",
        type=str,
        default=None,
        help=(
            "Scientific Python executable for CodeAct analysis. Defaults to "
            "PERTURA_PYTHON or the current interpreter."
        ),
    )
    parser.add_argument(
        "--python-preflight-timeout-s",
        type=float,
        default=240.0,
        help="Timeout for scientific Python preflight imports.",
    )
    parser.add_argument(
        "--python-preflight-packages",
        type=str,
        default=None,
        help=(
            "Comma-separated Python packages to require during preflight. "
            "Defaults are stage-aware."
        ),
    )
    parser.add_argument(
        "--permission-mode",
        choices=["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"],
        default="default",
        help="Claude Code permission mode.",
    )
    parser.add_argument(
        "--max-turns", type=int, default=20, help="Maximum Claude agent turns."
    )
    parser.add_argument(
        "--interaction-mode",
        choices=["benchmark", "interactive"],
        default="benchmark",
        help=(
            "Benchmark mode never asks the user; interactive mode may collect "
            "metadata as user-supplied metadata only."
        ),
    )
    parser.add_argument(
        "--policy",
        dest="policy_profile",
        choices=["strict", "paper", "smoke"],
        default="strict",
        help=(
            "Run-level claim policy. Defaults to strict; smoke is fixture/debug only."
        ),
    )
    parser.add_argument("--max-budget-usd", type=float, default=None)
    parser.add_argument(
        "--task", type=str, default=None, help="Task prompt. Defaults to Pertura intake."
    )
    parser.add_argument(
        "--task-file", type=Path, default=None, help="Read task prompt from file."
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-hooks", action="store_true")
    parser.add_argument(
        "--allow-web",
        action="store_true",
        help="Do not disallow WebFetch/WebSearch.",
    )
    parser.add_argument(
        "--no-bundled-skills",
        action="store_true",
        help="Disable the four bundled Pertura analysis skills.",
    )
    parser.add_argument(
        "--skill-plugin",
        action="append",
        type=Path,
        default=[],
        help=(
            "Load an additional local plugin root containing skills/<name>/SKILL.md. "
            "May be repeated; user-global skills remain disabled."
        ),
    )
    args = parser.parse_args(argv)

    task = args.task
    if args.task_file is not None:
        task = args.task_file.read_text(encoding="utf-8")

    workspace = ClaudeRunWorkspace.create(
        root=args.root,
        input_source=args.input,
        run_id=args.run_id,
    )
    config = ClaudeRuntimeOptions(
        model=args.model,
        permission_mode=args.permission_mode,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        enable_audit_hooks=not args.no_hooks,
        disallowed_tools=[] if args.allow_web else ["WebFetch", "WebSearch"],
        enable_bundled_skills=not args.no_bundled_skills,
        additional_skill_plugins=tuple(args.skill_plugin),
        python_exe=args.python_exe,
        python_preflight_timeout_s=args.python_preflight_timeout_s,
        python_preflight_packages=(
            [
                item.strip()
                for item in args.python_preflight_packages.split(",")
                if item.strip()
            ]
            if args.python_preflight_packages
            else None
        ),
        interaction_mode=args.interaction_mode,
        policy_profile=args.policy_profile,
    )
    agent = ClaudePerturaAgent(
        workspace=workspace,
        config=config,
        verbose=not args.quiet,
    )
    result = asyncio.run(agent.run(task))
    print(f"\nrun: {result.workspace}")
    print(f"status: {result.status}")
    if result.error:
        print(f"error: {result.error}")
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
