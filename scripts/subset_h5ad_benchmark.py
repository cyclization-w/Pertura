from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pertura_bench.models import BenchmarkSubsetSpec  # noqa: E402
from pertura_bench.operations import subset_h5ad  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic target/control H5AD benchmark subset.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--label-column", required=True)
    parser.add_argument("--labels", nargs="+", required=True)
    parser.add_argument("--max-cells-per-label", type=int, default=500)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--source-lock-hash", required=True)
    parser.add_argument("--split", choices=("calibration", "evaluation"), required=True)
    args = parser.parse_args()
    spec = BenchmarkSubsetSpec(
        dataset_id=args.dataset_id,
        source_lock_hash=args.source_lock_hash,
        split=args.split,
        label_column=args.label_column,
        labels=tuple(args.labels),
        max_cells_per_label=args.max_cells_per_label,
        seed=args.seed,
    )
    lock = subset_h5ad(args.input, args.output, spec)
    args.output.with_suffix(args.output.suffix + ".manifest.json").write_text(
        json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
