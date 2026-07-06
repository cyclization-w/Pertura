from __future__ import annotations

from pathlib import Path
import tempfile

from pertura_bench.p21_classic_workflow import run_p21_suite, write_p21_summary


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "docs" / "results"


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pertura_p21_freeze_") as tmp:
        results = run_p21_suite(root=Path(tmp) / "cases")
        md_path, json_path = write_p21_summary(results, output_dir=RESULTS_DIR)
    print(f"wrote {json_path.relative_to(ROOT)}")
    print(f"wrote {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())