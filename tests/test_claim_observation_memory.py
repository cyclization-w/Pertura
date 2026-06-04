"""Standalone claim check: scientific observation memory."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pertura.core import claim_id_for_script
from test_claim_segments import run_claims


def main() -> int:
    payload = run_claims([claim_id_for_script(Path(__file__).name)], json_output=True)
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
