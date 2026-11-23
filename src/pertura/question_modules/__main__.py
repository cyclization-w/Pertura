"""Run one module on a screen object, as a job writes it (result.tsv, result.json, figure.png and further tables in
the output folder), or list the modules with their contracts.

    python -m pertura.question_modules run B.efficacy --screen <screen object> --design <design.yaml> --out <folder>
        [--choices '{"threshold": 0.5}'] [--inputs '{"gene_lists": {...}}'] [--cells <a kept cell set>]
        [--by <a cell column>] [--targets '["A", "B"]']
    python -m pertura.question_modules list
"""
import argparse
import contextlib
import json
import os
import sys

from . import REGISTRY, queue_of
from .data import Screen

PROGRAMS_QUEUE = "programs_found_in_the_cells.lock"  # in the folder of a workspace's jobs: cNMF and the like


def lock_of(queue):
    """The lock file of a queue (queue_of), beside the workspace's jobs: one per heavy module."""
    return PROGRAMS_QUEUE if queue == "programs" else f"heavy_module_runs_{queue}.lock"
BY_VALUES_AT_MOST = 8  # a run by a cell column runs the module once per value


def queued(name, choices=None):
    """In a job, a module of ONE_AT_A_TIME waits for the workspace's other one in its queue to end (a lock beside the
    jobs' folders, queue_of); anything else, or outside a job, goes at once."""
    job = os.environ.get("PERTURA_JOB_DIR")
    queue = queue_of(name, choices)
    if queue is None or not job:
        return contextlib.nullcontext()
    from ..finding_checks import _slots
    return _slots(os.path.join(os.path.dirname(os.path.abspath(job)), lock_of(queue)), 1)


def run(name, screen_dir, design_path, out_dir, choices=None, inputs=None, cells=None, by=None, targets=None):
    if name not in REGISTRY:
        raise SystemExit(f"no module {name}; the modules are {sorted(REGISTRY)}")
    with queued(name, choices):  # before the screen is loaded
        return _run(name, screen_dir, design_path, out_dir, choices, inputs, cells, by, targets)


def _run(name, screen_dir, design_path, out_dir, choices=None, inputs=None, cells=None, by=None, targets=None):
    from ..screen_object import load_design
    design = load_design(design_path) if design_path else {}
    screen = Screen(screen_dir, design)
    if cells:  # the module sees only the set's cells, with its labels (docs/adr/0024, section 3)
        from ..cell_sets import apply
        screen = apply(screen, screen_dir, cells)
    if targets:  # the targets read, as the blocks read them: the other targets' cells left out, the reference kept
        from ..blocks import _focus
        screen = _focus(screen, targets, (choices or {}).get("reference", "control_guides"))
    from .. import blocks
    blocks.SLICE.update(cells=cells, targets=targets)  # the notebook's lines of the run name its slice
    try:
        if by:
            return run_by(name, screen, design, out_dir, by, choices, inputs)
        return REGISTRY[name].run(screen, design, out_dir, **(choices or {}), **(inputs or {}))
    except blocks.NoCells as e:  # no target the cells in hand can test: the run says so, it has not failed
        import numpy as np
        import pandas as pd
        from . import figures
        from .contract import Result
        path = os.path.join(out_dir, "figure.png")
        os.makedirs(out_dir, exist_ok=True)
        r = Result("not_enough_data", pd.DataFrame(), {}, path,
                   figures.bars(np.zeros(1), ["none"], path, "No target with enough cells", "cells"), str(e))
        r.write(out_dir)
        return r
    finally:
        blocks.SLICE.clear()


def run_by(name, screen, design, out_dir, by, choices=None, inputs=None):
    """The module once per value of a cell column (each condition, tissue or cell type apart), each in its own
    folder (by_<value>), gathered into one result: its tables with the value as a column, each value's biology lines,
    each value's figure (the first value with a finding leads), and the summaries side by side."""
    import re

    import pandas as pd

    from .c import BIOLOGY_LINES
    from .contract import Result
    obs = screen.adata.obs
    if by not in obs.columns:
        raise ValueError(f"by: no cell column {by!r}; the columns are {sorted(map(str, obs.columns))}")
    values = sorted({str(v) for v in obs[by].astype(str)} - {"", "nan"})
    if not 2 <= len(values) <= BY_VALUES_AT_MOST:
        raise ValueError(f"by {by!r} holds {len(values)} values ({', '.join(values[:12])}): a run by a column takes 2 "
                         f"to {BY_VALUES_AT_MOST}, one module run each")
    parts, untestable = {}, {}
    for v in values:  # a value whose cells hold no testable target is said, the others still run (GSE263761 on
        # 0adebaa: one condition of three had two targets with ten cells, and the whole run failed)
        try:
            parts[v] = REGISTRY[name].run(screen.subset(**{by: v}), design,
                                          os.path.join(out_dir, "by_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", v)),
                                          **(choices or {}), **(inputs or {}))
        except ValueError as e:  # blocks.NoCells among them
            untestable[v] = str(e)
    if not parts:
        raise ValueError(f"by {by!r}: no value could be read: " + "; ".join(f"{v}: {w}" for v, w in untestable.items()))
    values = list(parts)

    def gathered(frames):
        frames = [f.assign(**{by: v}) for v, f in frames if isinstance(f, pd.DataFrame) and len(f)]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    statuses = [r.status for r in parts.values()]
    status = next(s for s in ("detected", "not_detected", "measured", "not_enough_data") if s in statuses)
    lead = next((v for v in values if parts[v].status == "detected"), values[0])
    each = max(1, BIOLOGY_LINES // len(values))
    r = Result(status, gathered((v, p.table) for v, p in parts.items()),
               {"by": by, "values": {**{v: {"status": p.status, "figure": os.path.relpath(p.figure, out_dir)}
                                        for v, p in parts.items()},
                                     **{v: {"status": "not_enough_data", "why": w} for v, w in untestable.items()}}},
               parts[lead].figure, parts[lead].figure_checks,
               f"by {by}, each value apart: " + "; ".join(f"{v}: {p.summary[:400]}" for v, p in parts.items())
               + "".join(f"; {v}: not read ({w[:300]})" for v, w in untestable.items()),
               tables={k: gathered((v, p.tables.get(k)) for v, p in parts.items())
                       for k in sorted({k for p in parts.values() for k in p.tables})},
               biology=[f"{v}: {line}" for v, p in parts.items() for line in p.biology[:each]])
    r.write(out_dir)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pertura.question_modules")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--screen", required=True)
    r.add_argument("--design", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--choices", default="{}")
    r.add_argument("--inputs", default="{}")
    r.add_argument("--cells", default=None)
    r.add_argument("--by", default=None)
    r.add_argument("--targets", default=None)
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.command == "list":
        print(json.dumps([m.describe() for m in REGISTRY.values()], indent=1))
        return 0
    res = run(a.name, a.screen, a.design, a.out, json.loads(a.choices), json.loads(a.inputs), a.cells, a.by,
              json.loads(a.targets) if a.targets else None)
    print(f"{a.name}: {res.status}. {res.summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
