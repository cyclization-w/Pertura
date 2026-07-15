from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_paper_task_references import validate_task_reference_pack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    result = validate_task_reference_pack(args.root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
