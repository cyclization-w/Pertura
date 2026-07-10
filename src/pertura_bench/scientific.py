from __future__ import annotations

from typing import Any


def dispatch_maintainer_command(command: str, args: Any, extra: list[str]) -> dict[str, Any]:
    if command == "edger-golden":
        from pertura_bench.edger_golden import run_edger_golden

        return run_edger_golden(environment=args.environment)
    raise ValueError(f"unknown maintainer command: {command}")
