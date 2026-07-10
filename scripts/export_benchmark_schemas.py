from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pertura_bench.schema_export import export_benchmark_schemas  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift = export_benchmark_schemas(ROOT / "src" / "pertura_bench" / "schemas", check=args.check)
    if drift:
        print("benchmark schema drift: " + ", ".join(drift))
        return 1
    print("benchmark schemas are current" if args.check else "wrote benchmark schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
