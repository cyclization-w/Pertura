from __future__ import annotations

import os
import subprocess
import sys

from pertura_runtime.project.store import _process_alive


def test_current_process_is_alive_without_interrupting_it() -> None:
    assert _process_alive(os.getpid()) is True


def test_exited_subprocess_is_not_alive() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=30)

    assert _process_alive(process.pid) is False
