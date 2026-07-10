from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

SCIENCE_PACKAGES: tuple[str, ...] = (
    "scanpy",
    "anndata",
    "pandas",
    "pertpy",
    "decoupler",
)

_PROBE_CODE = r'''
import importlib
import json
import os
import sys
import traceback

packages = [p for p in os.environ.get("PERTURA_PREFLIGHT_PACKAGES", "").split(",") if p]
result = {
    "python_executable": sys.executable,
    "sys_prefix": sys.prefix,
    "sys_base_prefix": getattr(sys, "base_prefix", sys.prefix),
    "version": sys.version,
    "platform": sys.platform,
    "packages": {},
}
for name in packages:
    try:
        module = importlib.import_module(name)
        result["packages"][name] = {
            "status": "ok",
            "version": getattr(module, "__version__", "unknown"),
        }
    except Exception as exc:
        result["packages"][name] = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-8:],
        }
print(json.dumps(result))
'''


class PythonEnvironmentError(RuntimeError):
    """Raised when the configured scientific Python environment is unusable."""

    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


@dataclass(frozen=True)
class PythonPackageStatus:
    name: str
    status: str
    version: str | None = None
    error_type: str | None = None
    error: str | None = None
    traceback_tail: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, name: str, payload: Mapping[str, Any]) -> "PythonPackageStatus":
        return cls(
            name=name,
            status=str(payload.get("status", "error")),
            version=str(payload.get("version")) if payload.get("version") is not None else None,
            error_type=str(payload.get("error_type")) if payload.get("error_type") else None,
            error=str(payload.get("error")) if payload.get("error") else None,
            traceback_tail=[str(item) for item in payload.get("traceback_tail", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        if self.version is not None:
            payload["version"] = self.version
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        if self.error is not None:
            payload["error"] = self.error
        if self.traceback_tail:
            payload["traceback_tail"] = list(self.traceback_tail)
        return payload


@dataclass(frozen=True)
class PythonEnvironment:
    requested_python: str
    python_executable: Path
    sys_prefix: Path
    sys_base_prefix: Path
    version: str
    platform: str
    env_overlay: dict[str, str]
    packages: dict[str, PythonPackageStatus]

    @property
    def shell_python(self) -> str:
        return self.python_executable.as_posix()

    @property
    def failed_packages(self) -> list[PythonPackageStatus]:
        return [pkg for pkg in self.packages.values() if pkg.status != "ok"]

    def self_check_command(self) -> str:
        packages = ",".join(self.packages) or ",".join(SCIENCE_PACKAGES)
        imports = "; ".join(f"import {name}" for name in packages.split(",") if name)
        return (
            f'"{self.shell_python}" -c "import sys; {imports}; '
            "print(sys.executable); print('pertura_python_self_check_ok')\""
        )

    def prompt_section(self) -> str:
        return f"""## Python Environment

Pertura has already resolved and preflighted the scientific Python executable for this run.
Use this exact Python executable for all analysis code:

`{self.shell_python}`

Do not use bare `python`, `python3`, or `py`; those may resolve to a base environment
without the preflighted scientific packages. As your first Bash action, verify the SDK tool
subprocess sees the same environment by running:

```bash
{self.self_check_command()}
```
"""

    def to_manifest(self) -> dict[str, Any]:
        return {
            "requested_python": self.requested_python,
            "python_executable": str(self.python_executable),
            "python_executable_posix": self.shell_python,
            "sys_prefix": str(self.sys_prefix),
            "sys_base_prefix": str(self.sys_base_prefix),
            "version": self.version,
            "platform": self.platform,
            "env_overlay_keys": sorted(self.env_overlay),
            "packages": {name: status.to_dict() for name, status in self.packages.items()},
            "self_check_command": self.self_check_command(),
        }


def prepare_python_environment(
    python_exe: str | os.PathLike[str] | None = None,
    *,
    base_env: Mapping[str, str] | None = None,
    required_packages: Sequence[str] = SCIENCE_PACKAGES,
    timeout_s: float = 60.0,
) -> PythonEnvironment:
    """Resolve and preflight the Python executable used by Claude CodeAct."""

    base = dict(os.environ if base_env is None else base_env)
    requested = _resolve_requested_python(python_exe, base)
    initial = _run_probe(requested, env_overlay={}, base_env=base, required_packages=(), timeout_s=timeout_s)

    executable = Path(str(initial["python_executable"])).expanduser().resolve()
    prefix = Path(str(initial["sys_prefix"])).expanduser().resolve()
    base_prefix = Path(str(initial.get("sys_base_prefix", initial["sys_prefix"]))).expanduser().resolve()
    platform = str(initial.get("platform", sys.platform))
    env_overlay = _build_env_overlay(
        python_executable=executable,
        sys_prefix=prefix,
        platform=platform,
        base_env=base,
    )
    final = _run_probe(
        str(executable),
        env_overlay=env_overlay,
        base_env=base,
        required_packages=required_packages,
        timeout_s=timeout_s,
    )
    packages = {
        name: PythonPackageStatus.from_payload(name, payload)
        for name, payload in dict(final.get("packages", {})).items()
    }
    environment = PythonEnvironment(
        requested_python=requested,
        python_executable=Path(str(final["python_executable"])).expanduser().resolve(),
        sys_prefix=Path(str(final["sys_prefix"])).expanduser().resolve(),
        sys_base_prefix=Path(str(final.get("sys_base_prefix", final["sys_prefix"]))).expanduser().resolve(),
        version=str(final.get("version", "")),
        platform=str(final.get("platform", platform)),
        env_overlay=env_overlay,
        packages=packages,
    )
    if environment.failed_packages:
        failed = ", ".join(
            f"{pkg.name} ({pkg.error_type or 'error'}: {pkg.error or 'unknown'})"
            for pkg in environment.failed_packages
        )
        raise PythonEnvironmentError(
            f"Scientific Python preflight failed: {failed}",
            payload={"python_environment": environment.to_manifest()},
        )
    return environment


def _resolve_requested_python(python_exe: str | os.PathLike[str] | None, env: Mapping[str, str]) -> str:
    if python_exe:
        return str(Path(python_exe).expanduser())
    if env.get("PERTURA_PYTHON"):
        return str(Path(str(env["PERTURA_PYTHON"])).expanduser())
    if sys.executable:
        return sys.executable
    found = shutil.which("python")
    if found:
        return found
    return "python"


def _run_probe(
    python_exe: str,
    *,
    env_overlay: Mapping[str, str],
    base_env: Mapping[str, str],
    required_packages: Sequence[str],
    timeout_s: float,
) -> dict[str, Any]:
    env = dict(base_env)
    env.update(env_overlay)
    env["PERTURA_PREFLIGHT_PACKAGES"] = ",".join(required_packages)
    try:
        completed = subprocess.run(
            [python_exe, "-c", _PROBE_CODE],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_s,
            check=False,
        )
    except Exception as exc:
        raise PythonEnvironmentError(
            f"Could not execute Python preflight with {python_exe!r}: {type(exc).__name__}: {exc}",
            payload={"requested_python": python_exe},
        ) from exc
    if completed.returncode != 0:
        raise PythonEnvironmentError(
            f"Python preflight command failed for {python_exe!r} with exit code {completed.returncode}",
            payload={
                "requested_python": python_exe,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "returncode": completed.returncode,
            },
        )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise PythonEnvironmentError(
            f"Python preflight produced no JSON output for {python_exe!r}",
            payload={"requested_python": python_exe, "stderr": completed.stderr[-4000:]},
        )
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise PythonEnvironmentError(
            f"Python preflight produced invalid JSON for {python_exe!r}",
            payload={
                "requested_python": python_exe,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        ) from exc


def _build_env_overlay(
    *,
    python_executable: Path,
    sys_prefix: Path,
    platform: str,
    base_env: Mapping[str, str],
) -> dict[str, str]:
    entries = _python_path_entries(python_executable=python_executable, sys_prefix=sys_prefix, platform=platform)
    existing_path = str(base_env.get("PATH", ""))
    path_parts = [str(path) for path in entries]
    if existing_path:
        path_parts.append(existing_path)
    overlay = {
        "PATH": os.pathsep.join(path_parts),
        "PERTURA_PYTHON_EXE": str(python_executable),
        "PERTURA_PYTHON_PREFIX": str(sys_prefix),
    }
    if (sys_prefix / "conda-meta").exists() or base_env.get("CONDA_PREFIX"):
        overlay["CONDA_PREFIX"] = str(sys_prefix)
        overlay["CONDA_DEFAULT_ENV"] = sys_prefix.name
    return overlay


def _python_path_entries(*, python_executable: Path, sys_prefix: Path, platform: str) -> list[Path]:
    raw: list[Path]
    if platform.startswith("win") or python_executable.suffix.lower() == ".exe":
        raw = [
            python_executable.parent,
            sys_prefix,
            sys_prefix / "Scripts",
            sys_prefix / "Library" / "bin",
        ]
    else:
        raw = [python_executable.parent, sys_prefix / "bin"]
    seen: set[str] = set()
    entries: list[Path] = []
    for path in raw:
        key = str(path).lower() if os.name == "nt" else str(path)
        if key in seen:
            continue
        seen.add(key)
        entries.append(path)
    return entries
