from __future__ import annotations

import argparse
import json
from pathlib import Path

from pertura_bench.capability_audit import audit_capabilities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit bundled capability safety invariants.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    payload = audit_capabilities(args.repo)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
