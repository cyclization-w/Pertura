from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pertura_bench.compatibility import freeze_contracts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Write or check the frozen Pertura v0.2 compatibility surface.")
    parser.add_argument("--check", action="store_true", help="Fail when the checked-in snapshots differ.")
    args = parser.parse_args()
    drift = freeze_contracts(ROOT, check=args.check)
    if drift:
        print("v0.2 compatibility drift: " + ", ".join(drift))
        return 1
    print("v0.2 compatibility surface is current" if args.check else "wrote v0.2 compatibility snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
