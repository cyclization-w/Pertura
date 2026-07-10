from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pertura_bench.operations import fetch_benchmark, load_source_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicitly fetch and verify one Pertura benchmark source.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    lock, _ = fetch_benchmark(load_source_manifest(args.manifest), args.cache)
    print(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
