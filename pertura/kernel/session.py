"""Persistent Jupyter kernel — like CellVoyager and legacy paper_v1.

Variables, imports, and loaded data carry across cells.
Each cell sees stdout/stderr/errors captured in full.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _emit_output(callback: Callable[[str, str], None] | None, stream: str, text: str) -> None:
    if callback is None or not text:
        return
    try:
        callback(stream, text)
    except Exception:
        pass


class KernelSession:
    """Synchronous wrapper around a persistent Jupyter kernel."""

    def __init__(self, workspace: str, artifacts_dir: str, domain_imports: list[str] | None = None):
        self.workspace = workspace
        self.artifacts_dir = artifacts_dir
        self.domain_imports = domain_imports or []
        self.km = None
        self.kc = None
        self._started = False

    def start(self):
        if self._started:
            return
        try:
            import jupyter_core.paths
            from jupyter_client.manager import KernelManager
        except ImportError:
            raise RuntimeError("Persistent kernel requires: pip install jupyter_client ipykernel")

        runtime_root = Path(self.artifacts_dir).parent / ".jupyter"
        runtime_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("IPYTHONDIR", str(runtime_root / "ipython"))
        os.environ.setdefault("JUPYTER_RUNTIME_DIR", str(runtime_root / "runtime"))
        os.environ.setdefault("JUPYTER_DATA_DIR", str(runtime_root / "data"))
        os.environ.setdefault("JUPYTER_ALLOW_INSECURE_WRITES", "1")
        jupyter_core.paths.allow_insecure_writes = True

        self.km = KernelManager(kernel_name="python3")
        self.km.start_kernel()
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=30)

        # Bootstrap: inject paths via safe repr() to prevent code injection
        import shlex
        ws_repr = repr(str(self.workspace))
        ad_repr = repr(str(self.artifacts_dir))
        bootstrap = (
            f"import sys, os, json, atexit\n"
            f"from pathlib import Path\n"
            f"workspace = Path({ws_repr})\n"
            f"artifacts_dir = Path({ad_repr})\n"
            f"artifacts_dir.mkdir(parents=True, exist_ok=True)\n"
            f"os.chdir({ws_repr})\n"
            f"_manifest = {{'manifest_id': 'kernel', 'attempt_id': '', 'artifacts': [], 'observations': []}}\n"
            f"def register_artifact(path, kind, summary='', metadata=None):\n"
            f"    _manifest['artifacts'].append({{'path': str(path), 'kind': str(kind), 'summary': str(summary), 'metadata': dict(metadata or {{}})}})\n"
            f"def register_observation(type, target='', metric='', value=None, contrast='', method='', parameters=None, uncertainty=None, artifact_id='', variable_key='', input_ids=None, design_fields_used=None, parameter_hash='', method_version=''):\n"
            f"    _manifest['observations'].append({{'type': str(type), 'target': str(target), 'metric': str(metric), 'value': value, 'contrast': str(contrast), 'method': str(method), 'parameters': dict(parameters or {{}}), 'uncertainty': dict(uncertainty or {{}}), 'artifact_id': str(artifact_id), 'variable_key': str(variable_key), 'input_ids': list(input_ids or []), 'design_fields_used': list(design_fields_used or []), 'parameter_hash': str(parameter_hash), 'method_version': str(method_version)}})\n"
            f"def _flush_manifest():\n"
            f"    p = artifacts_dir / (_manifest.get('attempt_id', 'kernel') + '_manifest.json')\n"
            f"    p.write_text(json.dumps(_manifest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')\n"
            f"    print(f'MANIFEST: {{p}}', file=sys.stderr)\n"
            f"def _get_kernel_state():\n"
            f"    import types, gc\n"
            f"    vars_sniff = {{}}\n"
            f"    for name, val in list(globals().items()):\n"
            f"        if name.startswith('_') or isinstance(val, (types.ModuleType, types.FunctionType)):\n"
            f"            continue\n"
            f"        try:\n"
            f"            t = type(val).__name__\n"
            f"            if hasattr(val, 'shape'):\n"
            f"                vars_sniff[name] = f'{{t}}({{val.shape}})'\n"
            f"            elif hasattr(val, '__len__') and not isinstance(val, str):\n"
            f"                vars_sniff[name] = f'{{t}}(len={{len(val)}})'\n"
            f"            else:\n"
            f"                vars_sniff[name] = f'{{t}}'\n"
            f"        except Exception:\n"
            f"            vars_sniff[name] = type(val).__name__\n"
            f"    imports = sorted(k for k, v in sys.modules.items() if k in globals() and not k.startswith('_'))\n"
            f"    return json.dumps({{'variables': vars_sniff, 'imports': imports}}, default=str)\n"
            f"atexit.register(_flush_manifest)\n"
            f"print('Kernel ready. workspace:', workspace, file=sys.stderr)\n"
        )
        self._execute_sync(bootstrap)
        self._started = True

    def execute(
        self,
        attempt_id: str,
        code: str,
        timeout: float | None = None,
        soft_timeout: float | None = None,
        hard_timeout: float | None = None,
        heartbeat_timeout: float | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> dict:
        """Execute code in the kernel. Manifest reset + flushed per attempt."""
        self.start()
        # Reset manifest for this attempt — clear previous observations
        self._execute_sync(
            "_manifest.clear()\n"
            "_manifest['manifest_id'] = 'kernel'\n"
            f"_manifest['attempt_id'] = '{attempt_id}'\n"
            "_manifest['artifacts'] = []\n"
            "_manifest['observations'] = []"
        )
        if soft_timeout is None:
            soft_timeout = timeout if timeout is not None else 120
        if hard_timeout is None:
            hard_timeout = max(float(soft_timeout) * 3, 600)
        if heartbeat_timeout is None:
            heartbeat_timeout = 240
        result = self._execute_sync(
            code,
            soft_timeout=float(soft_timeout),
            hard_timeout=float(hard_timeout),
            heartbeat_timeout=float(heartbeat_timeout),
            on_output=on_output,
        )
        # Force flush — observations written immediately
        self._execute_sync("_flush_manifest()")
        # Snapshot kernel variables for LLM context
        try:
            state_json = self._execute_sync(
                "print(_get_kernel_state())",
                soft_timeout=3,
                hard_timeout=6,
                heartbeat_timeout=6,
            )
            result["kernel_state"] = json.loads(
                state_json.get("stdout", "{}") or "{}")
        except Exception as exc:
            result["kernel_state"] = {}
            result["kernel_state_warning"] = str(exc)
        return result

    def _execute_sync(
        self,
        code: str,
        timeout: float | None = None,
        soft_timeout: float | None = None,
        hard_timeout: float | None = None,
        heartbeat_timeout: float | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> dict:
        """Execute synchronously using the Jupyter kernel."""
        if self.kc is None:
            return {"returncode": 1, "stdout": "", "stderr": "Kernel not started."}

        msg_id = self.kc.execute(code)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        rich_outputs: list[str] = []
        traceback: list[str] = []
        status = "success"
        timed_out_at = ""
        soft_timeout_hit = False

        import time
        start = time.time()
        soft_timeout = float(soft_timeout if soft_timeout is not None else (timeout if timeout is not None else 30))
        hard_timeout = float(hard_timeout if hard_timeout is not None else max(soft_timeout, 30))
        heartbeat_timeout = float(heartbeat_timeout if heartbeat_timeout is not None else max(hard_timeout, 30))
        soft_deadline = start + soft_timeout
        hard_deadline = start + hard_timeout
        last_heartbeat = start
        try:
            while True:
                now = time.time()
                if now > hard_deadline:
                    status = "timeout"
                    timed_out_at = "hard"
                    text = f"Hard timeout after {hard_timeout}s; interrupting kernel.\n"
                    stderr_parts.append(text)
                    _emit_output(on_output, "stderr", text)
                    try:
                        self.km.interrupt_kernel()
                    except Exception as exc:
                        text = f"Kernel interrupt failed: {exc}\n"
                        stderr_parts.append(text)
                        _emit_output(on_output, "stderr", text)
                    break
                if not soft_timeout_hit and now > soft_deadline:
                    soft_timeout_hit = True
                    text = f"Soft timeout after {soft_timeout}s; kernel is still responsive, continuing until hard timeout.\n"
                    stderr_parts.append(text)
                    _emit_output(on_output, "stderr", text)
                if self._heartbeat_alive():
                    last_heartbeat = now
                elif now - last_heartbeat > heartbeat_timeout:
                    status = "timeout"
                    timed_out_at = "heartbeat"
                    text = f"Kernel heartbeat lost for {heartbeat_timeout}s; interrupting kernel.\n"
                    stderr_parts.append(text)
                    _emit_output(on_output, "stderr", text)
                    try:
                        self.km.interrupt_kernel()
                    except Exception as exc:
                        text = f"Kernel interrupt failed: {exc}\n"
                        stderr_parts.append(text)
                        _emit_output(on_output, "stderr", text)
                    break
                try:
                    msg = self.kc.get_iopub_msg(timeout=1)
                except Exception:
                    continue

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                msg_type = msg["header"]["msg_type"]
                content = msg["content"]

                if msg_type == "stream":
                    last_heartbeat = time.time()
                    text = content.get("text", "")
                    if content.get("name") == "stderr":
                        stderr_parts.append(text)
                        _emit_output(on_output, "stderr", text)
                    else:
                        stdout_parts.append(text)
                        _emit_output(on_output, "stdout", text)

                elif msg_type == "error":
                    last_heartbeat = time.time()
                    status = "error"
                    traceback = content.get("traceback", [])
                    if traceback:
                        _emit_output(on_output, "stderr", "\n".join(str(item) for item in traceback))

                elif msg_type in {"execute_result", "display_data"}:
                    last_heartbeat = time.time()
                    data = content.get("data") or {}
                    if "text/plain" in data:
                        rich_outputs.append(str(data.get("text/plain", "")))

                elif msg_type == "status" and content.get("execution_state") == "idle":
                    break

        except Exception as exc:
            status = "error"
            text = f"Kernel error: {exc}\n"
            stderr_parts.insert(0, text)
            _emit_output(on_output, "stderr", text)

        return {
            "returncode": 0 if status == "success" else 1,
            "stdout": "".join(stdout_parts),
            "stderr": "".join(stderr_parts + traceback),
            "rich_outputs": rich_outputs,
            "timed_out": status == "timeout",
            "timed_out_at": timed_out_at,
            "soft_timeout_hit": soft_timeout_hit,
            "execution_time": round(time.time() - start, 3),
            "partial_stdout": "".join(stdout_parts)[-4000:],
            "partial_stderr": "".join(stderr_parts + traceback)[-4000:],
        }

    def _heartbeat_alive(self) -> bool:
        hb = getattr(self.kc, "hb_channel", None)
        if hb is None:
            return True
        probe = getattr(hb, "is_beating", None)
        try:
            if callable(probe):
                return bool(probe())
            if probe is not None:
                return bool(probe)
        except Exception:
            return False
        return True

    def restart(self):
        """Restart the kernel, preserving bootstrap."""
        if self.kc:
            self.kc.stop_channels()
        if self.km:
            if self.km.is_alive():
                self.km.shutdown_kernel(now=True)
        self._started = False
        self.start()

    def shutdown(self):
        if self.kc:
            self.kc.stop_channels()
        if self.km and self.km.is_alive():
            self.km.shutdown_kernel(now=True)
        self._started = False

    def alive(self) -> bool:
        return self.km is not None and self.km.is_alive()
