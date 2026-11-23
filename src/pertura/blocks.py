"""Blocks (docs/adr/0024, sections 2 and 8): the analyses' parts the agent calls in its notebook as `pt.<name>`.

A block is one comparison of cells against a reference by one method: the cells (a kept cell set's name, a selection
of the screen, or every cell), the targets (all with enough cells, or those named), the reference (the control
cells by default) and the method's choices. It returns a Result into the notebook: a table in one shape per block,
the n of each side, the package calls. Every call is logged in the notebook's blocks folder, and the tool that ran
the cell enters it in the record, so the robustness engine can rerun it and the report can state its method.

The checks rerun a result's own call: `calibrate` with groups of control guides in place of the targets (what the
same analysis gives with no effect), `by_guide` with each guide's cells in place of its target's, `match_depth` with
the deeper side's counts thinned to the other's depth. A gene result also gives `shared()` (genes changed in many
targets) and `similar()` (targets whose effects correlate).

Blocks that need R or start processes (edgeR-QL, MAST, the energy-distance test) run as Pertura jobs, outside the
notebook's confinement: the call returns a Handle at once, its job starts when the cell ends, and `handle.result()`
gives the Result once the job is done.

Every call's table is kept under its call ID, and `save_table` names a table the agent computed, so any tool takes
either by its ID (docs/adr/0027); `load` gives any object by its ID.

    import pertura.blocks as pt     (done by every notebook's start-up)
    pt.help(); pt.help("genes"); pt.describe("strict")
    res = pt.genes("strict", targets=["T1"]); pt.calibrate(res); res.by_guide()
    pt.save_table("pair_fit", fit); pt.load("table:pair_fit")
"""
import contextlib
import hashlib
import uuid
import json
import os
import time
import warnings

import numpy as np
import pandas as pd

from .gene_sets import NAMED as COLLECTION_NAMES
from .question_modules.contract import Contract, Module
from .question_modules.data import REFERENCES, Screen

CONTEXT = {}   # set by the notebook's start-up: screen_dir, design_path, notebook_dir, jobs_dir; or by inside()
BLOCKS = {}    # name -> Module (its contract; a call's choices and inputs are checked as a module's are)
# a block's methods run as a job in the notebook (None: all of it); auto, the design's test per target, may need R
JOBS = {"genes": ("edger_ql", "mast", "seurat_lr", "auto", "limma_voom", "deseq2", "glmgampoi", "sceptre"),
        "any_effect": None,
        "programs": ("cnmf", "genenmf", "genenmf_meta"), "cell_cycle": ("seurat",),
        "composition": ("propeller", "sccoda", "milo", "auto"), "enrich": None, "scores": ("seurat",),
        "cell_type_response": None}
MIN_CELLS = 10  # pertura.policy's default; the blocks read the number in effect (_least_cells)
EXCLUDED = "excluded"  # a cell that is neither a chosen target nor the reference
ADJUSTED = 0.05  # pertura.policy's default q; the blocks read the q in effect (_q)
# a module run's slice (cells, targets), set by the module runner: recipe_as_run gives it to each block call that
# takes it, so the notebook's lines give the run's result
SLICE = {}


def _q(q=None):
    """A changed row's q: the one given, else Pertura's in effect (pertura.policy)."""
    from . import policy
    return policy.get("q") if q is None else q


def _least_cells():
    from . import policy
    return policy.get("min_cells")


def _least_profile_cells():
    from . import policy
    return policy.get("min_cells_profile")
LOG = "calls.jsonl"
FAST_GZIP = {"method": "gzip", "compresslevel": 1}
_SCREEN = {}
_BUILD = {}  # the build and the cells' rule each cached screen object was read with
_RULES = {}  # a design file's cells' rule, by its path, with the file's time it was read at


def context(**kw):
    CONTEXT.update(kw)


@contextlib.contextmanager
def inside(screen_obj, out_dir):
    """Blocks run in this process on the given screen, their calls logged in out_dir/blocks: how a module's recipe
    calls them (docs/adr/0031, D2). The context before comes back afterwards."""
    saved = dict(CONTEXT)
    CONTEXT.clear()
    CONTEXT.update({"screen": screen_obj, "screen_dir": getattr(screen_obj, "dir", None), "notebook_dir": out_dir,
                    "work_dir": os.path.join(out_dir, "blocks", "work"), "in_process": True})
    try:
        yield
    finally:
        CONTEXT.clear()
        CONTEXT.update(saved)


def calls_logged(notebook_dir=None):
    """The block calls logged in a notebook's folder, or a module run's, in order."""
    path = os.path.join(notebook_dir or CONTEXT["notebook_dir"], "blocks", LOG)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def recipe_as_run(fn, **args):
    """A module's recipe with the arguments one run used: its block calls as code to paste into run_code and change
    one step (docs/adr/0031, D3). A block that runs as a job in the notebook returns a Handle there: its result()
    comes in the next cell. A parameter the run did not give is bound to the recipe's own default, so the code shown
    names no variable the notebook lacks; the run's slice (SLICE) goes to each block call that takes it."""
    import ast
    import inspect
    import textwrap
    args = {**{k: p.default for k, p in inspect.signature(fn).parameters.items()
               if p.default is not inspect.Parameter.empty}, **args}
    f = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    body = f.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]  # the docstring

    # names the conditions may read: the arguments, and the recipe module's plain constants (PROGRAM_METHODS)
    known = {k: v for k, v in fn.__globals__.items() if isinstance(v, (str, int, float, bool, tuple, frozenset))}
    known.update(args)

    def decided(test):
        """A condition's value where it reads only the arguments and constants, else None."""
        try:
            return bool(eval(compile(ast.Expression(test), "recipe", "eval"), {"__builtins__": {}}, dict(known)))
        except Exception:
            return None

    class Bind(ast.NodeTransformer):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and node.id in args:
                return ast.copy_location(ast.Constant(args[node.id]), node)
            return node

        def visit_IfExp(self, node):
            value = decided(node.test)
            return self.visit(node.body if value else node.orelse) if value is not None else self.generic_visit(node)

        def visit_Call(self, node):  # a keyword bound to None is the block's own default: left out
            self.generic_visit(node)
            node.keywords = [kw for kw in node.keywords
                             if not (isinstance(kw.value, ast.Constant) and kw.value.value is None)]
            f = node.func
            pt_call = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "pt"
            if pt_call and callable(globals().get(f.attr)):
                takes, have = inspect.signature(globals()[f.attr]).parameters, {kw.arg for kw in node.keywords}
                node.keywords += [ast.keyword(k, ast.parse(repr(v), mode="eval").body) for k, v in SLICE.items()
                                  if v is not None and k in takes and k not in have]
            return node

    def taken(nodes):
        """The statements this run took: an if whose condition the arguments decide becomes its branch."""
        out = []
        for n in nodes:
            if isinstance(n, ast.If):
                value = decided(n.test)
                if value is not None:
                    out += taken(n.body if value else n.orelse)
                    continue
            out.append(n)
        return out
    lines = [ast.unparse(Bind().visit(n)) for n in taken(body)
             if not isinstance(n, (ast.Import, ast.ImportFrom, ast.Return))]
    jobs = [m for m in (JOBS.get("genes") or ()) if args.get("method") == m]
    head = ([f"# pt.genes with method {jobs[0]!r} runs as a job here: run this first line, then res = res.result() "
             "in the next cell, then the rest"] if jobs else [])
    return "\n".join(head + lines)


def _cells_rule(path):
    """The cells' quality rule a design file holds (data.qc's value, as Screen applies it), as JSON; None without
    the file. Read again only when the file changes."""
    if not path or not os.path.exists(path):
        return None
    t = os.path.getmtime(path)
    if _RULES.get(path, (None,))[0] != t:
        from .question_modules.data import value
        from .screen_object import load_design
        _RULES[path] = (t, json.dumps(value(load_design(path), "data.qc"), sort_keys=True, default=str))
    return _RULES[path][1]


def screen():
    """The screen object, with the confirmed design (loaded once per notebook, and again after a rebuild or when the
    cells' rule changes; inside a module, the module's)."""
    if CONTEXT.get("screen") is not None:
        return CONTEXT["screen"]
    key = CONTEXT["screen_dir"]
    from .screen_object import current_build, load_design
    build = current_build(key)
    if build is None:  # before the build the notebook serves the design (development session 9, problem 12)
        raise ValueError("no screen object yet: it is built once the design is confirmed (build_screen_object). "
                         "Until then the deposit's files are at `deposit`, and a deposit Pertura cannot read goes into "
                         "the cell tables at `cell_tables`")
    path = CONTEXT.get("design_path")
    seen = (build, _cells_rule(path))
    # a rebuild (item 2), or a cells' rule confirmed after the notebook first read the object: read it again, or an
    # open kernel keeps the cells of the rule before (the open points of 2026-11-19)
    if key in _SCREEN and _BUILD.setdefault(key, seen) != seen:
        del _SCREEN[key]
    if key not in _SCREEN:
        design = load_design(path) if path and os.path.exists(path) else {}
        _SCREEN[key], _BUILD[key] = Screen(key, design), seen
    return _SCREEN[key]


def save_cells(name, barcodes, labels=None, reason="", calls=False, base=None):
    """Keep a named selection of the screen object's cells for every analysis (blocks, runners, checks), with each
    cell's label where it changes guide calls; each label must follow from the cell's called guides (a target's
    guide, each member's for a pair 'a|b', control guides for 'control'). reason: why; calls=True when it decides
    guide calls (it relabels cells, or leaves cells out by their guide evidence): usable at once, and findings on it
    say so; the user confirms one only to make it the screen's default calling rule; base: the kept set
    it is cut from (a population such as one tissue)."""
    from .cell_sets import write
    return write(os.path.join(CONTEXT["notebook_dir"], "cell_sets"), name, barcodes, labels, reason, calls, base)


def load_cells(name):
    """A kept cell set's table (barcode, and label where the set gives them)."""
    from .cell_sets import load
    return load(CONTEXT["screen_dir"], name)[0]


def save_table(name, table, note=""):
    """Name a table this notebook computed, so any tool takes it by its ID, table:<name> (a module's inputs, pt.load,
    a finding). What it was made from is known from the cell: the files and the earlier results it used. Saving
    under the same name again makes a new version. note: what the table holds, in a few words."""
    from .flow import NAME
    if not NAME.match(str(name)):
        raise ValueError("a table's name is letters, digits, _ and - (up to 80)")
    if isinstance(table, dict) and table and all(np.isscalar(v) or v is None for v in table.values()):
        table = [table]  # one row of measures, as a branch's worker saves it (docs/adr/0032, D9)
    df = table if isinstance(table, pd.DataFrame) else pd.DataFrame(table)
    if not isinstance(df.index, pd.RangeIndex):  # the index is data (a pivot's rows): kept as a column
        df = df.reset_index()
    folder = os.path.join(CONTEXT["notebook_dir"], "tables")
    os.makedirs(folder, exist_ok=True)
    df.to_csv(os.path.join(folder, f"{name}.tsv.gz"), sep="\t", index=False, compression=FAST_GZIP)
    with open(os.path.join(folder, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "columns": list(map(str, df.columns)), "rows": int(len(df)), "note": str(note)}, fh)
    return f"table:{name}"


def load(object_id):
    """Any object by its ID, as a table: table:<name> (pt.save_table, in any notebook), <run>:<table> (a module
    run's table), cells:<name> (a kept cell set), result:<name> (a block result kept with result.save: a Result), a
    block call's ID (its table), or file:<path> (a file a cell wrote in this notebook)."""
    oid = str(object_id)
    root = os.path.dirname(CONTEXT["notebook_dir"])
    kind, _, rest = oid.partition(":")
    if kind == "table":
        found = sorted((p for nb in os.listdir(root) for p in [os.path.join(root, nb, "tables", f"{rest}.tsv.gz")]
                        if os.path.exists(p)), key=os.path.getmtime)
        if not found:
            raise ValueError(f"no saved table {rest!r} (pt.save_table keeps one)")
        return pd.read_csv(found[-1], sep="\t")
    if kind == "cells":
        return load_cells(rest)
    if kind == "result":
        return results.load(rest)
    if kind == "file":
        path = os.path.join(CONTEXT["notebook_dir"], *rest.split("/"))
        if not os.path.exists(path):
            raise ValueError(f"no file {rest!r} in this notebook")
        if path.endswith((".tsv", ".tsv.gz")):
            return pd.read_csv(path, sep="\t")
        return pd.read_csv(path) if path.endswith((".csv", ".csv.gz")) else path
    if rest:  # <run>:<table>
        folder = os.path.join(CONTEXT["jobs_dir"], kind)
        for ext in (".tsv", ".tsv.gz"):
            if os.path.exists(os.path.join(folder, rest + ext)):
                return pd.read_csv(os.path.join(folder, rest + ext), sep="\t")
        have = sorted(f.split(".")[0] for f in os.listdir(folder) if ".tsv" in f) if os.path.isdir(folder) else []
        raise ValueError(f"run {kind} has no table {rest!r}" + (f"; it has {have}" if have else ""))
    for nb in sorted(os.listdir(root)):  # a block call's ID
        path = os.path.join(root, nb, "blocks", "outputs", oid, "table.tsv.gz")
        if os.path.exists(path):
            return pd.read_csv(path, sep="\t")
    raise ValueError(f"no object {oid!r}; read_record(what='objects') lists them")


def _resolve(cells):
    """The Screen a block runs on, the name of its cells, and whether they are a kept set (or every cell)."""
    if cells is None:
        return screen(), "every cell", True
    if isinstance(cells, str):
        from .cell_sets import apply, pending
        try:
            return apply(screen(), CONTEXT["screen_dir"], cells), cells, True
        except ValueError as e:
            if CONTEXT.get("notebook_dir") and cells in pending(os.path.join(CONTEXT["notebook_dir"], "cell_sets")):
                raise ValueError(f"the cell set {cells!r} was saved in this cell: a set is checked and kept when the "
                                 "cell that saved it ends, so use it from the next cell on") from e
            raise
    if isinstance(cells, Screen):
        info = getattr(cells, "cell_set", None)
        if info:
            return cells, info["name"], True
        return cells, f"a selection of {len(cells.label)} cells (not kept)", False
    raise ValueError("cells is a kept cell set's name, a selection (pt.screen().subset(...)), or None for every cell")


class NoCells(ValueError):
    """The cells in hand hold no target the call can test."""


def _targets(s, targets, min_cells=None):
    """The call's targets: those with Pertura's default cells (pertura.policy) where the call names none; those it
    names, each with as few cells as can be computed (a note says which are below the default)."""
    from . import policy
    least = policy.get("min_cells") if min_cells is None else min_cells
    have = s.targets(least)
    if targets is None:
        if not have:
            raise NoCells(f"no target with {least} or more cells here")
        return have
    targets = [targets] if isinstance(targets, str) else [str(t) for t in targets]
    runs, missing = policy.named_targets(s.targets, [t for t in targets if "|" not in t], least)
    missing += [t for t in targets if "|" in t]
    pairs = [t for t in missing if "|" in t]
    if pairs:
        raise ValueError(f"{pairs[:3]}{' and others' if len(pairs) > 3 else ''}: a pair's label is not a target of "
                         "this block, which compares single perturbations. A pair is tested with the genes tool "
                         "(C.genes), method seurat3_lr, with groups holding the pair and each of its singles ({name: "
                         "[labels]}); combinations (E.combinations) with model regulatory_modes then reads that run's "
                         "de table, the pair and its singles")
    if missing:
        raise NoCells(f"no target {missing} with {policy.COMPUTABLE['min_cells']} or more cells here, the least a "
                      f"test can be computed on; such as {have[:8]}")
    return runs


# blocks that read an earlier result's table: their targets are the table's, cut to those asked, whatever the cells
# of the screen in hand (bug register, BR09)
TABLE_BLOCKS = {"correlate_effects", "rank_on", "cluster_targets"}


def _table_targets(name, given, targets):
    """The targets of a table-driven block's call: the table's own, or those asked, each of them in the table."""
    table = _table_of(given.get("of"), name)
    if {"group", "other", "correlation"} <= set(table.columns):  # a similarity result: its groups, not its controls
        t = table[table["kind"] != "control guides"] if "kind" in table else table
        have = sorted(set(t["group"].astype(str)) | set(t["other"].astype(str)))
    else:
        have = list(dict.fromkeys(table["target"].astype(str)))
    if targets is None:
        return have
    asked = [targets] if isinstance(targets, str) else [str(t) for t in targets]
    missing = [t for t in asked if t not in have]
    if missing:
        raise ValueError(f"{name}: no target {missing} in the table it reads; it holds such as {have[:8]}")
    return asked


def _focus(s, targets, reference):
    """The screen with the chosen targets' cells and the reference's; the other targets' cells labelled 'excluded'
    (with other targets as the reference they all stay, and the rows are kept for the chosen ones)."""
    if reference not in REFERENCES:
        raise ValueError(f"reference is one of {list(REFERENCES)}, not {reference!r}")
    if reference == "other_targets":
        return s
    other = np.isin(s.label, s.targets(1)) & ~np.isin(s.label, targets)
    return s.with_labels(np.where(other, EXCLUDED, s.label)) if other.any() else s


def _n(s, targets, reference):
    """What a call compared (item 24.4): each target's cells, and the reference cells its test used (its own arm's
    where the design matches references, bug register BR37), the samples and whether they are independent."""
    out = {"targets": {t: int((s.label == t).sum()) for t in targets},
           "reference_cells": int(s.reference(reference).sum()), "samples": len(s.units()),
           "independent_units": bool(s.independent_units())}
    if getattr(s, "reference_matches", None) and reference in ("control_guides", "other_targets"):
        each = {}
        target_of = dict(zip(s.guides["guide"].astype(str), s.guides["target"].astype(str)))
        for t in targets:
            lab = t if (s.label == t).any() else target_of.get(str(t), t)  # a guide's: its target's
            try:
                each[t] = int((s.own_reference(lab, reference) & ~(s.label == lab)).sum())
            except ValueError as e:  # a target with no one arm: said, not counted
                each[t] = str(e)
        out["reference_cells_by_target"] = each
    return out


# blocks that read the whole screen (a PCA, programs, phases, the samples' agreement): every cell is theirs, the
# rows cut to the targets named, so a block call gives what its module gives
SCREEN_WIDE = {"any_effect", "programs", "cell_cycle", "replicate_agreement", "response", "similarity",
               "correlate_effects", "rank_on", "cluster_targets", "pair_fit", "enrich", "cell_type_response"}
_TABLES = {}  # results' tables by call ID, in this process, for the blocks that read an earlier result


def _is_job(name, given):
    if CONTEXT.get("in_process"):  # a module's recipe: already outside the notebook's confinement
        return False
    methods = JOBS.get(name, ())
    return name in JOBS and (methods is None or given.get("method") in methods)


# ---- the result ----------------------------------------------------------------------------------------------------
class Result:
    """A block's output: table, n, calls (the package calls), cells (their name), block, args, and extra (per-cell
    values such as states or scores). A result on a selection that is not kept cannot be rerun by the robustness
    engine: keep the selection first (pt.save_cells)."""

    def __init__(self, block, args, table, n, calls, cells, extra=None, screen=None, call_id=None):
        self.block, self.args, self.table, self.n, self.calls = block, dict(args), table, n, list(calls or [])
        self.cells, self.extra, self._screen, self.call_id = cells, dict(extra or {}), screen, call_id
        self.name = None

    def __repr__(self):
        head = self.table.head(8).to_string() if len(self.table) else "(no rows)"
        skipped = (f"; skipped by {self.args.get('by')!r}: {self.extra['skipped']} (no target with enough cells)"
                   if self.extra.get("skipped") else "")
        return f"pt.{self.block} on {self.cells}: {len(self.table)} rows; n {self.n}{skipped}\n{head}"

    def calibrate(self, **kw):
        return calibrate(self, **kw)

    def by_guide(self):
        return by_guide(self)

    def match_depth(self, **kw):
        return match_depth(self, **kw)

    def shared(self, q=None):
        """Genes changed in many targets: per gene, the targets changing it at adjusted p < q, the median effect among
        them and the share with the commoner sign (descriptive; D.common_response tests whether targets differ beyond
        what they share)."""
        if self.block != "genes":
            raise ValueError("shared() is a gene result's")
        t = self.table[self.table["q"] < _q(q)]
        if t.empty:
            return pd.DataFrame(columns=["gene", "targets", "median_effect", "same_sign"])
        g = t.groupby("gene")["effect"]
        out = pd.DataFrame({"targets": g.size(), "median_effect": g.median(),
                            "same_sign": g.apply(lambda e: max((e > 0).mean(), (e < 0).mean()))})
        return out.sort_values("targets", ascending=False).reset_index()

    def own_genes(self, q=None):
        """Each CRISPRi or CRISPRa target's own gene in this gene result: changed in the expected direction at q (a
        power check; a knockout's own gene is listed but not counted, since its transcript can stay)."""
        from .question_modules.c import positive_controls
        if self.block != "genes":
            raise ValueError("own_genes() is a gene result's")
        s = self._screen if self._screen is not None else screen()
        return pd.DataFrame(positive_controls(s, s.design, self.table, _q(q)))

    def similar(self, q=None):
        """Pearson correlations of the targets' effects over the genes any of them changes at adjusted p < q
        (descriptive; E.similar_targets tests them against control-guide groups)."""
        if self.block != "genes":
            raise ValueError("similar() is a gene result's")
        from .steps.effects_across_targets import one_row_per_key
        genes = sorted(set(self.table.loc[self.table["q"] < _q(q), "gene"]))
        sub = one_row_per_key(self.table[self.table["gene"].isin(genes)], ("target", "gene"), "similar()")
        M = sub.pivot(index="gene", columns="target", values="effect")
        return M.corr()

    def draw(self, **kw):
        return draw(self, **kw)

    def save(self, name):
        """Keep the result under a name every notebook can read (pt.results.load, or pt.load('result:<name>'))."""
        folder = os.path.join(CONTEXT["notebook_dir"], "results", name)
        os.makedirs(folder, exist_ok=True)
        self.table.to_csv(os.path.join(folder, "table.tsv.gz"), sep="\t", index=False)
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"block": self.block, "args": self.args, "n": self.n, "cells": self.cells, "calls": self.calls,
                       "call_id": self.call_id}, fh, indent=1, default=str)
        self.name = name
        return name


class Handle:
    """A block running as a Pertura job; it starts when this cell ends. result() gives the Result once done."""

    def __init__(self, folder, block, args, cells, n, screen=None):
        self.folder, self.block, self.args, self.cells, self.n = folder, block, dict(args), cells, n
        self._screen = screen

    def _run(self):
        p = os.path.join(self.folder, "run.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def state(self):
        run = self._run()
        if run is None:
            return "starts when this cell ends"
        out = os.path.join(CONTEXT["jobs_dir"], run["run_id"])
        if os.path.exists(os.path.join(out, "result.tsv.gz")):
            return "done"
        if os.path.exists(os.path.join(out, "exit_code.json")):
            return "failed"
        return "running"

    def result(self, wait=0):
        """The Result once the job is done; waits up to `wait` seconds."""
        end = time.time() + wait
        st = self.state()
        while st not in ("done", "failed") and time.time() < end:
            time.sleep(min(5.0, max(0.1, end - time.time())))
            st = self.state()
        if st != "done":
            run = self._run()
            raise RuntimeError(f"pt.{self.block}: {st}" + (f" (run {run['run_id']})" if run else "")
                               + ("; take its result in a later cell: handle.result(wait=...)"
                                  if run is None else ""))
        out = os.path.join(CONTEXT["jobs_dir"], self._run()["run_id"])
        table = pd.read_csv(os.path.join(out, "result.tsv.gz"), sep="\t")
        with open(os.path.join(out, "block.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        r = Result(self.block, self.args, table, meta["n"], meta["calls"], self.cells, extra=_read_extras(out),
                   screen=self._screen, call_id=meta.get("call_id"))
        _remember(r)
        return r

    def __repr__(self):
        run = self._run()
        return (f"pt.{self.block} job on {self.cells} ({self.state()}" + (f", run {run['run_id']}" if run else "")
                + f"); n {self.n}. handle.result() when it is done")


# ---- running a call ------------------------------------------------------------------------------------------------
def block(name, contract):
    def deco(fn):
        BLOCKS[name] = Module(name, contract, fn)
        return fn
    return deco


def _log(entry):
    folder = os.path.join(CONTEXT["notebook_dir"], "blocks")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, LOG), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def _call_id(name, args, cells):
    raw = json.dumps([name, args, cells, time.time()], default=str, sort_keys=True)
    return f"{name}-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


def _execute(name, f, targets, reference, given, cells, kept, check_of=None, check=None):
    """One call on a focused screen: run here, or hand to a job (a Handle); logged either way. The call's own
    choices (its targets where named) are its own, under Pertura's numbers (pertura.policy)."""
    from . import policy
    named = _own_choices(name, given)
    named |= {"targets"} if targets is not None else set()
    with policy.using(None, explicit_choices=named):
        return _execute_under_policy(name, f, targets, reference, given, cells, kept, check_of, check, named)


def _execute_under_policy(name, f, targets, reference, given, cells, kept, check_of, check, named):
    from . import policy
    n = _n(f, targets, reference)
    args = {**given, "targets": targets, "reference": reference}
    cid = _call_id(name, {**args, "check": check}, cells)
    entry = {"call_id": cid, "block": name, "choices": given, "cells": cells, "targets": targets,
             "reference": reference, "n": n, "kept_cells": kept, "check_of": check_of, "check": check,
             "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if _is_job(name, given):
        folder = os.path.join(CONTEXT["notebook_dir"], "blocks", "requests", cid)
        os.makedirs(folder, exist_ok=True)
        pd.DataFrame({"barcode": np.asarray(f.adata.obs_names).astype(str), "label": f.label}).to_csv(
            os.path.join(folder, "cells.tsv.gz"), sep="\t", index=False)
        with open(os.path.join(folder, "request.json"), "w", encoding="utf-8") as fh:
            # the notebook's folders, so the job reads earlier results by their IDs as the notebook does
            json.dump({**entry, "args": args, "policy": policy.changed(), "named": sorted(named),
                       "context": {k: CONTEXT.get(k) for k in ("notebook_dir", "jobs_dir", "screen_dir")}}, fh,
                      indent=1, default=str)
        _log({**entry, "job": True})
        return Handle(folder, name, args, cells, n, screen=f)
    CONTEXT["call_dir"] = os.path.join(CONTEXT.get("work_dir") or os.getcwd(), cid)  # the call's own files, if any
    table, calls, extra = BLOCKS[name].fn(f, targets, reference, **given)
    extra = {**(extra or {}), "policy": policy.used()}
    folder = os.path.join(CONTEXT["notebook_dir"], "blocks", "outputs", cid)
    os.makedirs(folder, exist_ok=True)
    # the table, so any tool can take the call by its ID (docs/adr/0027, D2)
    outputs = {"table": os.path.join(folder, "table.tsv.gz")}
    table.to_csv(outputs["table"], sep="\t", index=False, compression=FAST_GZIP)
    outputs.update(_write_extras(folder, extra, len(f.label)))
    _log({**entry, "outputs": outputs})
    r = Result(name, args, table, n, calls, cells, extra=extra, screen=f, call_id=cid)
    _remember(r)
    return r


def _write_extras(folder, extra, n_cells):
    """A result's per-cell values (states, scores, program usages: read by a finding's checks and by later calls)
    as <key>.tsv.gz, and its other plain values in extra.json. Returns {key: path}."""
    out, plain = {}, {}
    for key, value in (extra or {}).items():
        if isinstance(value, (pd.Series, pd.DataFrame)) and len(value) == n_cells:
            path = os.path.join(folder, f"{key}.tsv.gz")
            (value.rename_axis("barcode").to_frame(key) if isinstance(value, pd.Series)
             else value.rename_axis("barcode")).to_csv(path, sep="\t", compression=FAST_GZIP)
            out[key] = path
        elif isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            plain[key] = value
    if plain:
        with open(os.path.join(folder, "extra.json"), "w", encoding="utf-8") as fh:
            json.dump(plain, fh, default=str)
    return out


def _read_extras(folder):
    """What _write_extras wrote: {key: per-cell table indexed by barcode, or plain value}."""
    extra = {}
    for name in sorted(os.listdir(folder)):
        if name.endswith(".tsv.gz") and name not in ("result.tsv.gz", "table.tsv.gz"):
            extra[name[:-len(".tsv.gz")]] = pd.read_csv(os.path.join(folder, name), sep="\t", index_col=0)
    if os.path.exists(os.path.join(folder, "extra.json")):
        with open(os.path.join(folder, "extra.json"), encoding="utf-8") as fh:
            extra.update(json.load(fh))
    return extra


_PER_CELL = {}  # call ID -> the result's per-cell values, for a later call that takes the result (pt.scores)


def _remember(result):
    if result.call_id:
        _TABLES[result.call_id] = result.table
    if result.call_id and result.extra:
        _PER_CELL[result.call_id] = {k: v for k, v in result.extra.items() if isinstance(v, pd.DataFrame)}


def per_cell(call_id, key):
    """A result's per-cell values by its call ID: from this process, else from the notebook's or the module run's
    blocks folder."""
    if call_id in _PER_CELL and key in _PER_CELL[call_id]:
        return _PER_CELL[call_id][key]
    base = CONTEXT.get("notebook_dir")
    roots = [os.path.join(base, "blocks", "outputs", call_id)] if base else []
    if base and os.path.isdir(os.path.dirname(base)):
        roots += [os.path.join(os.path.dirname(base), nb, "blocks", "outputs", call_id)
                  for nb in sorted(os.listdir(os.path.dirname(base)))]
    for folder in roots:
        path = os.path.join(folder, f"{key}.tsv.gz")
        if os.path.exists(path):
            return pd.read_csv(path, sep="\t", index_col=0)
    raise ValueError(f"no {key} for the call {call_id!r}: give the pt.programs result itself, or its call ID from this "
                     "notebook")


def _own_choices(name, given):
    """The choices a call names itself: given, not "auto" and not at their default (a table or a list is its own)."""
    from .question_modules.contract import _at_default
    return {k for k, v in given.items()
            if not (isinstance(v, str) and v == "auto") and not _at_default(v, BLOCKS[name].default(k))}


def _run(name, cells, targets, reference, by, given):
    """Check the call, resolve its cells, focus them on the targets and the reference, and run it (once per value of
    the cell column `by`, where given), under Pertura's numbers (pertura.policy)."""
    from . import policy
    own = _own_choices(name, given)
    with policy.using(None, explicit_choices=own | ({"targets"} if targets is not None else set())):
        return _run_under_policy(name, cells, targets, reference, by, given)


def _run_under_policy(name, cells, targets, reference, by, given):
    BLOCKS[name].check(given)
    s, cname, kept = _resolve(cells)
    if by is not None:
        if by not in s.adata.obs:
            raise ValueError(f"by: no cell column {by!r}; the columns are {sorted(map(str, s.adata.obs.columns))}")
        parts, skipped = {}, []
        for v in sorted(set(map(str, s.adata.obs[by]))):
            try:
                parts[v] = _run(name, s.subset(**{by: v}), targets, reference, None, given)
            except NoCells:  # a value holding no cells to test, such as the cells of no unit: the others still run
                skipped.append(v)
        if not parts:
            raise NoCells(f"by {by!r}: no value holds a target with enough cells to test")
        if skipped:
            warnings.warn(f"pt.{name} by {by!r}: skipped {skipped}, which hold no target with enough cells to test")
        if any(isinstance(p, Handle) for p in parts.values()):
            return parts
        table = pd.concat([p.table.assign(**{by: v}) for v, p in parts.items()], ignore_index=True)
        return Result(name, {**given, "targets": targets, "reference": reference, "by": by}, table,
                      {v: p.n for v, p in parts.items()}, next(iter(parts.values())).calls, cname,
                      extra={"parts": parts, **({"skipped": skipped} if skipped else {})})
    ts = _table_targets(name, given, targets) if name in TABLE_BLOCKS else _targets(s, targets)
    return _execute(name, s if name in SCREEN_WIDE else _focus(s, ts, reference), ts, reference, given, cname, kept)


def _rerun(result, s, targets, check, **override):
    """A result's own call on another screen (its targets replaced; a choice overridden, such as the one test of a
    result by several), logged as a check of it."""
    given = {k: v for k, v in result.args.items() if k not in ("targets", "reference", "by", "check")}
    given.update(override)
    reference = result.args["reference"]
    # a screen-wide block sees every cell, as its first call did (pt.any_effect's PCA is fitted on all of them)
    return _execute(result.block, s if result.block in SCREEN_WIDE else _focus(s, targets, reference), targets,
                    reference, given, result.cells, False, check_of=result.call_id, check=check)


def _live(result):
    if not isinstance(result, Result) or result._screen is None:
        raise ValueError("a check reruns a result of this notebook (pt.<block>(...) or handle.result()); a loaded "
                         "result, or one split by a cell column, has no single set of cells to rerun")
    return result._screen


# ---- the analyses --------------------------------------------------------------------------------------------------
@block("genes", Contract(
    serves="C.genes", applies_when="always",
    needs="raw counts, labels, the reference cells; edgeR-QL needs 3 or more samples or lanes holding the target and "
          "the reference",
    returns="per target and gene: the effect (log2 fold change), p and adjusted p (within the target), the cells on "
            "each side",
    fails_when=[("cells as the observations can give excess false positives (Barry et al., Genome Biology 2024)",
                 "pt.calibrate: control-guide groups through the same test; edgeR-QL pseudobulk as the alternative"),
                ("a target whose cells sit in other samples than the control cells' reads the samples' differences "
                 "as its genes (Barry et al., Genome Biology 2024)",
                 "wilcoxon_same_samples (the default): each target against the control cells of its own samples, "
                 "in its mix")],
    tested_against="the synthetic screen: target B's planted genes found, few genes in control-guide groups",
    choices={"method": ["wilcoxon_same_samples", "wilcoxon", "auto", "seurat_lr", "seurat3_lr", "edger_ql",
                        "pydeseq2", "mast", "ttest", "limma_voom", "deseq2", "glmgampoi", "sceptre"]},
    inputs={"params": "{setting: value}: the method's package settings changed from their defaults (pt.help('genes') "
                      "lists each method's, with its default)"}))
def _genes(s, targets, reference, method="wilcoxon_same_samples", params=None):
    """The genes step (pertura.steps.genes), the one plumbing C.genes and the finding checks use: each target against
    its own arm's reference where the design matches references to attributes, its own cells left out. The default,
    wilcoxon_same_samples: Wilcoxon against the control cells of the target's own samples, in its mix (every control
    cell where all are in one sample); wilcoxon: against every control cell. auto: each target by the design's test
    for it (C.genes' default). In the notebook, the Wilcoxon tests and the Python LR run in its one process (its
    confinement refuses to start processes); the R tests and auto run as a job."""
    from .question_modules import c
    from .steps import genes
    leads = c.leads_of(s, targets, reference, method)
    table, calls = genes.compare(s, leads, reference, CONTEXT.get("call_dir") or os.getcwd(),
                                 workers=None if CONTEXT.get("in_process") else 1, params=params)
    cols = ["target", "gene", "effect", "p", "q", "test"]
    table = table[cols + [x for x in genes.EXTRA if x in table.columns]]
    # a test against the control cells of the same samples names the cells it kept and the control cells it matched
    matched = {t: m for c in calls for t, m in (c.get("matched") or {}).items()}
    cells = {t: matched[t]["cells"] if t in matched else int((s.label == t).sum()) for t in targets}
    ref = {t: matched[t]["reference_cells"] if t in matched else
           int((s.reference(reference, target=t) & ~s.cells_of(t)).sum()) for t in targets}
    table = table.assign(cells=table["target"].map(cells), reference_cells=table["target"].map(ref))
    extra = {"method": method, "leads": {t: {"method": ld["method"], "reason": ld["reason"]} for t, ld in leads.items()}}
    if matched:
        extra["matched"] = matched
    return table.reset_index(drop=True), calls, extra


@block("any_effect", Contract(
    serves="C.any_effect", applies_when="always",
    needs="log-normalised expression, labels and the reference cells",
    returns="per target: the energy distance to the reference cells of its own samples, its permutation p and "
            "adjusted p (pertpy's DistanceTest), the cells of each; by default each side drawn to at most 1,000 "
            "cells, as the overview's distances are (extra['drawn'])",
    fails_when=[("cell numbers or depth move the whole profile",
                 "pt.calibrate: control-guide groups through the same test, drawn as the targets are"),
                ("the energy distance grows as the cells fall, so targets of different sizes do not compare",
                 "the overview's line for each target: control-guide groups drawn to its own number of cells")],
    tested_against="C.any_effect's tests: the synthetic screen's strongest target ranked first",
    choices={"control_cells": ["same_samples", "all"], "n_pcs": [50, 20],
             "metric": ["edistance", "mmd", "mean_pairwise", "euclidean", "pearson_distance", "cosine_distance",
                        "spearman_distance", "mahalanobis"]},
    inputs={"permutations": "permutations of the test (1000)", "seed": "the permutations' and the draws' seed (0)",
            "draw": "each side drawn, seeded, to at most this many cells (1,000, the overview's; steps.any_effect."
                    "CAP), or none: every cell"}))
def _any_effect(s, targets, reference, control_cells="same_samples", n_pcs=50, permutations=1000, seed=0,
                metric="edistance", draw=1000):
    from .steps import any_effect
    per, calls, drawn = any_effect.test(s, reference, n_pcs, permutations, seed=seed, control_cells=control_cells,
                                        only=targets, metric=metric, draw=draw)
    return per.reset_index(drop=True), calls, {"drawn": drawn}


@block("programs", Contract(
    serves="C.programs", applies_when="always",
    needs="raw counts; for fit_on controls, 50 or more control cells; for genenmf_meta, samples",
    returns="each program's top genes (program, rank, gene, weight) and, in extra['usage'], each cell's usage of "
            "each program (pertura.steps.programs: cNMF, scikit-learn's NMF, GeneNMF's runNMF or meta-programs); "
            "pt.scores(programs=...) then tests each target's usage against its reference",
    fails_when=[("a program found on every cell can be shaped by the perturbations it is then used to test",
                 "fit_on controls: the programs found in the control cells, every cell read on them"),
                ("a program of one sample or capture (a batch) found as biology",
                 "genenmf_meta: programs that recur across samples; each program's top genes read against the "
                 "samples")],
    tested_against="the synthetic screen's planted program found by each method",
    choices={"method": ["cnmf", "sklearn_nmf", "genenmf", "genenmf_meta"], "fit_on": ["all", "controls"]},
    inputs={"k": "the number of programs (cnmf: else the most stable of 5 to 15; sklearn_nmf and genenmf: else 10; "
                 "genenmf_meta: the number of meta-programs, else 10)",
            "seed": "the random seed (0)",
            "params": "{setting: value}: the method's package settings changed from their defaults "
                      "(pt.help('programs') lists each method's, with its default)"}))
def _programs(s, targets, reference, method="cnmf", fit_on="all", k=None, seed=0, params=None):
    from .steps import programs
    usage, genes, about, calls = programs.discover(s, method, CONTEXT.get("call_dir") or os.getcwd(), k=k,
                                                   fit_on=fit_on, seed=seed, **(params or {}))
    return genes.reset_index(drop=True), calls, {"usage": usage, "about": about}


MAX_SETS = 200  # the sets one call scores: published screens score a few chosen sets, not a whole collection


def _chosen_sets(sets, select, collection):
    """A collection's sets to score: those select names, or those whose names hold its word; all of them where they
    are at most MAX_SETS."""
    if select is None:
        if len(sets) > MAX_SETS:
            raise ValueError(f"{collection} holds {len(sets)} sets, more than the {MAX_SETS} one call scores: give "
                             "select, the sets' names or a word their names hold (the gene_sets tool lists the sets "
                             "whose names hold a word, or that hold a gene)")
        return sets
    if isinstance(select, str):
        chosen = {k: v for k, v in sets.items() if select.lower() in k.lower()}
        if not chosen:
            raise ValueError(f"no set of {collection} has {select!r} in its name")
    else:
        missing = [n for n in select if n not in sets]
        if missing:
            raise ValueError(f"no set {missing} in {collection}")
        chosen = {n: sets[n] for n in select}
    if len(chosen) > MAX_SETS:
        raise ValueError(f"{len(chosen)} sets of {collection} have {select!r} in their names, more than the "
                         f"{MAX_SETS} one call scores: a longer word, or their names")
    return chosen


def _contrast_sets(contrast):
    """The control cells' signature of a contrast between two levels of a cell column ({column, levels: [a, b], top}),
    read on the whole screen whatever cells the call scores: scanpy's Wilcoxon of a's control cells against b's on the
    log-normalised counts, the genes at adjusted p < 0.05 ranked by its statistic, at most `top` (50) up and as many
    down, as published screens build an activation score from the control cells' own contrast."""
    from .question_modules.c import _wilcoxon_part
    whole = screen()
    column, levels, top = contrast.get("column"), list(contrast.get("levels") or []), int(contrast.get("top", 50))
    if column not in whole.adata.obs.columns or len(levels) != 2:
        raise ValueError(f"contrast: {{column, levels: [a, b]}} with a cell column; the columns are "
                         f"{sorted(map(str, whole.adata.obs.columns))}")
    values = whole.adata.obs[column].astype(str).to_numpy()
    control = whole.reference("control_guides")
    side = {str(v): control & (values == str(v)) for v in levels}
    few = {v: int(m.sum()) for v, m in side.items() if m.sum() < _least_cells()}
    if few:
        raise ValueError(f"contrast: too few control cells at {few} (at least {_least_cells()} each); the values of "
                         f"{column} are {sorted(set(values))[:20]}")
    a, b = map(str, levels)
    rows = np.flatnonzero(side[a] | side[b])
    t = _wilcoxon_part(whole.lognorm[rows], np.where(side[a][rows], "a", "b"), whole.genes, ["a"], "b")
    t = t[t["pvals_adj"] < 0.05]
    sets = {f"{a} vs {b}, up": t[t["scores"] > 0].sort_values("scores", ascending=False)["names"].head(top),
            f"{a} vs {b}, down": t[t["scores"] < 0].sort_values("scores")["names"].head(top)}
    call = {"package": "scanpy", "function": "tl.rank_genes_groups", "layer": "log-normalised counts",
            "params": {"method": "wilcoxon", "tie_correct": True, "cells": f"control cells, {column} {a} against {b}",
                       "tested": "genes detected in at least 10% of the cells on one side (Seurat's min.pct)",
                       "kept": f"adjusted p < 0.05, at most {top} each way by the statistic"}}
    return {k: list(map(str, v)) for k, v in sets.items() if len(v)}, call


def _protein_values(s, proteins):
    """{protein: each cell's normalised value}: the antibody counts the screen object keeps (obsm antibody_counts),
    normalised as Seurat's multimodal vignette does (NormalizeData, CLR, margin 2): each cell across all its proteins,
    log1p(x / exp(sum(log1p(x)) / the number of proteins)). proteins: their names, or all."""
    counts = s.adata.obsm.get("antibody_counts")
    if counts is None or not counts.shape[1]:
        raise ValueError("the screen object holds no antibody counts: its deposit has no protein features")
    M = np.asarray(counts, dtype=float)
    clr = np.log1p(M / np.exp(np.log1p(M).sum(axis=1) / M.shape[1])[:, None])
    names = [str(c) for c in counts.columns]
    chosen = names if proteins in (True, "all") else [str(p) for p in ([proteins] if isinstance(proteins, str)
                                                                        else proteins)]
    missing = [p for p in chosen if p not in names]
    if missing:
        raise ValueError(f"no protein {missing}; the proteins are {names[:60]}")
    return {p: clr[:, names.index(p)] for p in chosen}


def _cell_values(s, cell_values):
    """{name: each cell's value, in the screen's order}: a numeric cell column, or a table (a saved table's ID, or its
    file in a module run) with a barcode column and one numeric column or more."""
    obs = s.adata.obs
    if cell_values in obs.columns:
        col = pd.to_numeric(obs[cell_values], errors="coerce")
        if col.isna().all():
            raise ValueError(f"the cell column {cell_values!r} holds no numbers")
        return {str(cell_values): col.to_numpy(dtype=float)}
    if os.path.isfile(str(cell_values)):
        table = pd.read_csv(cell_values, sep="\t")
    elif ":" in str(cell_values):
        table = load(cell_values)
    else:
        raise ValueError(f"cell_values: {cell_values!r} is no cell column (the columns are "
                         f"{sorted(map(str, obs.columns))}) and no table: give a saved table's ID (pt.save_table, "
                         "with a barcode column and the values)")
    if "barcode" not in table.columns:
        raise ValueError(f"the table {cell_values!r} has no barcode column; its columns are {list(table.columns)}")
    table = table.set_index(table["barcode"].astype(str))
    numeric = [c for c in table.columns if c != "barcode" and pd.api.types.is_numeric_dtype(table[c])]
    if not numeric:
        raise ValueError(f"the table {cell_values!r} has no numeric column beside its barcodes")
    names = pd.Index(np.asarray(s.adata.obs_names).astype(str))
    missing = int((~names.isin(table.index)).sum())
    if missing:
        raise ValueError(f"{missing} of the {len(names)} cells read have no value in {cell_values!r}")
    return {str(c): table[c].reindex(names).to_numpy(dtype=float) for c in numeric}


@block("scores", Contract(
    serves="C.programs", applies_when="always",
    needs="gene sets ({name: [genes]}, or an MSigDB collection's name, its sets chosen with select where it holds "
          f"more than {MAX_SETS}) and log-normalised expression; or a pt.programs result; or per-cell values (a cell "
          "column, or a table with a barcode column)",
    returns="per target and set, program or value: the difference from the reference cells (scanpy's score_genes "
            "for a set, the usage for a program, the value itself for a per-cell value), tested by test (auto: a "
            "linear model with the sample where there are several, else Wilcoxon; or either named), p and adjusted "
            "p, the cells of each",
    fails_when=[("a set's genes move with depth or with the state mix",
                 "pt.match_depth; the same call within each state")],
    tested_against="C.programs' tests: the planted shift of genes s0-s9 found in the synthetic screen's targets",
    inputs={"sets": "{name: [genes]}: the gene sets to score; or an MSigDB collection's name (hallmark, immunologic, "
                    "cell_types and the others the gene_sets tool names)",
            "select": "a collection's sets to score: their names, or a word their names hold",
            "programs": "a pt.programs result (or its call ID): each cell's usage of each program",
            "cell_values": "per-cell values to test as they are: a numeric cell column, or a table's ID with a "
                           "barcode column and numeric columns (pt.save_table)",
            "contrast": "{column, levels: [a, b], top}: the control cells' genes up and down between two levels of a "
                        "cell column, read on the whole screen, scored as two sets (an activation score, for example)",
            "proteins": "the proteins to test (their names, or all): the screen object's antibody counts, CLR per "
                        "cell as Seurat's multimodal vignette normalises them",
            "min_genes": "a set's genes detected in 1% of the cells needed to score it (5)"},
    choices={"method": ["scanpy", "seurat"], "test": ["auto", "linear_model", "wilcoxon"]}))
def _scores(s, targets, reference, sets=None, programs=None, cell_values=None, select=None, min_genes=5,
            method="scanpy", test="auto", contrast=None, proteins=None):
    from .question_modules import c
    calls = []
    if programs is not None:
        usage = per_cell(programs, "usage")
        names = np.asarray(s.adata.obs_names).astype(str)
        usage.index = usage.index.astype(str)
        missing = ~pd.Index(names).isin(usage.index)
        if missing.any():
            raise ValueError(f"{int(missing.sum())} cells have no usage in the programs result {programs!r}: run "
                             "pt.programs on these cells")
        values = usage.reindex(names)
        per_cell_values = {str(p): values[p].to_numpy(dtype=float) for p in values.columns}
        used = {"programs": programs}
    elif cell_values is not None:
        per_cell_values, used = _cell_values(s, cell_values), {"cell_values": cell_values}
    elif proteins is not None:
        per_cell_values, used = _protein_values(s, proteins), {"proteins": proteins}
        calls.append({"package": "pertura", "function": "Seurat's CLR normalisation (margin 2), in numpy",
                      "layer": "antibody counts", "params": {"across": "each cell's proteins"}})
    else:
        if contrast is not None:
            sets, call = _contrast_sets(contrast)
            calls.append(call)
        elif isinstance(sets, str):
            from . import gene_sets as gs
            from .question_modules.data import value
            organism = value(s.design, "experiment.organism")
            if organism not in ("human", "mouse"):
                raise ValueError(f"the {sets} sets need experiment.organism, human or mouse (here {organism!r})")
            sets = _chosen_sets(gs.msigdb(sets, organism), select, sets)
        elif select is not None:
            raise ValueError("select chooses among a collection's sets: give sets a collection's name")
        if not sets:
            raise ValueError("scores needs sets ({name: [genes]} or hallmark) or programs (a pt.programs result)")
        scorer = c.scanpy_scores if method == "scanpy" else c.seurat_scores
        per_cell_values, used, call = scorer(s, sets, min_genes=min_genes)
        if not per_cell_values:
            raise ValueError(f"no set has {min_genes} or more genes detected in 1% of the cells")
        calls.append(call)
    by_unit, per, test_calls = c.compare_scores_field(s, per_cell_values, reference, test=test)
    per = per[per["target"].isin(targets)].reset_index(drop=True)
    return per, calls + test_calls, {"scores": pd.DataFrame(per_cell_values,
                                                            index=np.asarray(s.adata.obs_names).astype(str)),
                                     "genes_used": used, "by_unit": by_unit[by_unit["target"].isin(targets)]}


@block("replicate_agreement", Contract(
    serves="F.replicates_agree", applies_when="2 or more samples or lanes",
    needs="labels and samples, with control cells in each",
    returns="per pair of samples: the correlation (Pearson) of every target's profile against its control cells "
            "together (the screen's r), over well-expressed genes (Replogle et al. 2022), with the targets and genes "
            "both hold and the median of the targets' own correlations; in extra['per_target'], each target's r",
    fails_when=[("targets without an effect pull the screen's r down, and one strong target can carry it",
                 "each target's r beside the screen's; the targets C.genes finds read apart")],
    tested_against="the synthetic screen: an effect planted in three samples gives a high r, noise alone none",
    choices={"genes": ["well_expressed", "detected", "all"]}))
def _replicate_agreement(s, targets, reference, genes="well_expressed"):
    from .steps import profiles
    pairs, per_target = profiles.replicate_agreement(s, labels=targets, genes=genes, reference=reference)
    return pairs, [{"package": "numpy", "function": "corrcoef (Pearson) of profiles, replicate against replicate",
                    "layer": "lognorm", "params": {"genes": genes, "min_cells": _least_profile_cells()}}], \
        {"per_target": per_target}


@block("cell_cycle", Contract(
    serves="C.cell_cycle", applies_when="human or mouse (experiment.organism)",
    needs="log-normalised expression (scanpy) or raw counts (seurat); the organism",
    returns="per label and phase: its cells and share; in extra['phases'], each cell's S and G2/M scores and phase "
            "(G1, S, G2M) by the method (steps.cell_cycle: scanpy's score_genes_cell_cycle, or Seurat's "
            "CellCycleScoring; Seurat's cc.genes.updated.2019, Tirosh et al. 2016); pt.composition(of=res) tests "
            "each target's phase shares",
    fails_when=[("a target that stops cells cycling moves every proliferation-linked gene and program",
                 "the cell-cycle scores as a covariate in the gene test (pt.genes params cell_cycle)")],
    tested_against="the synthetic screen: cells with the S genes raised scored S",
    choices={"method": ["scanpy", "seurat"]}, inputs={"seed": "scanpy's random seed (0)"}))
def _cell_cycle(s, targets, reference, method="scanpy", seed=0):
    from .steps import cell_cycle
    cells, calls = cell_cycle.phases(s, method=method, seed=seed, workdir=CONTEXT.get("call_dir") or os.getcwd())
    t = pd.DataFrame({"label": s.label, "phase": cells["phase"].to_numpy()})
    counts = t.groupby(["label", "phase"]).size().rename("cells").reset_index()
    counts["share"] = counts["cells"] / counts.groupby("label")["cells"].transform("sum")
    keep = counts["label"].isin(list(targets) + ["control"])
    return counts[keep].reset_index(drop=True), calls, {"phases": cells}


@block("composition", Contract(
    serves="C.states_and_composition", applies_when="always",
    needs="a labelling of the cells: a pt.states or pt.cell_cycle result (or its call ID), or a kept cell set's labels",
    returns="per target and state (or phase): its share of cells against its reference by the method "
            "(pertura.steps.composition): Fisher's exact test on the cells against the control cells of its own "
            "samples, in its mix (same_samples), or every control cell (all); the Cochran-Mantel-Haenszel test over "
            "the samples; speckle's propeller on the samples' proportions; scCODA's credible effects; Milo's "
            "neighbourhoods (a row per neighbourhood, with its most common state); auto, the design's lead per target "
            "(propeller where 3 biological replicates hold both sides, else Fisher). Benjamini-Hochberg over targets "
            "and states (a target needs 10 cells and 10 reference cells, a row 10 kept target cells), changed in "
            "the column of that name; per target, state and sample, the share's difference from the reference's "
            "(extra['by_unit'])",
    fails_when=[("one of a target's guides can carry its shift", "the shares with each guide alone "
                 "(pt.by_guide); B.clonality"),
                ("the target's cells and the control cells come from different samples, so the shares measure the "
                 "samples", "control_cells same_samples (the default)")],
    tested_against="the synthetic screen: a target with twice the controls' share of a state found",
    choices={"method": ["fisher", "auto", "cmh", "propeller", "sccoda", "milo"],
             "control_cells": ["same_samples", "all"]},
    inputs={"of": "a pt.states or pt.cell_cycle result, or its call ID: the cells' labelling",
            "column": "the labelling's column (state for pt.states, phase for pt.cell_cycle)",
            "seed": "the seed of the same-sample control cells' draw, and of a backend's own draws (0)",
            "params": "{setting: value}: the method's package settings changed from their defaults (pt.help("
                      "'composition') lists each method's, with its default)"}))
def _composition(s, targets, reference, of=None, column=None, control_cells="same_samples", seed=0, method="fisher",
                 params=None):
    from . import design_fit as fit
    from .steps import composition as step
    if of is None:
        raise ValueError("composition needs of: a pt.states or pt.cell_cycle result (or its call ID)")
    table = None
    for key in ("phases", "states"):
        try:
            table = per_cell(of, key)
            break
        except ValueError:
            continue
    if table is None:
        raise ValueError(f"the call {of!r} holds no labelling of cells (pt.states or pt.cell_cycle)")
    column = column or ("phase" if "phase" in table.columns else "state" if "state" in table.columns
                        else table.columns[0])
    names = np.asarray(s.adata.obs_names).astype(str)
    table.index = table.index.astype(str)
    lab = table.reindex(names)[column].astype(str).to_numpy()
    work = os.path.join(CONTEXT.get("call_dir") or os.getcwd(), "composition")
    leads = None
    if method == "auto":
        leads = {t: fit.state_lead(s, t, reference) for t in targets}
        per, by_unit, calls = step.compare_by_leads(s, leads, reference, lab, work, control_cells, seed, params)
    else:
        per, by_unit, calls = step.compare(s, targets, reference, lab, method, work, control_cells, seed, params)
    for c in calls:
        c.setdefault("params", {})["column"] = column
    extra = {"column": column, "by_unit": by_unit, "method": method}
    if leads:
        extra["leads"] = {t: {"method": step.BACKEND_OF.get(ld["method"], ld["method"]), "reason": ld["reason"]}
                          for t, ld in leads.items()}
    return per, calls, extra


@block("states", Contract(
    serves="C.states_and_composition", applies_when="always",
    needs="raw counts and labels",
    returns="the cells' states (scanpy: normalise, highly variable genes, PCA, neighbours, Leiden) with each state's "
            "markers (extra['markers']) and each cell's state and UMAP coordinates (extra['states']); per target "
            "and state: its share of cells against the control cells of its own samples (Fisher's exact test, as "
            "pt.composition), adjusted p. With root, each cell's diffusion pseudotime from it (scanpy's tl.diffmap "
            "and tl.dpt on the same graph) in extra['states']",
    fails_when=[("states found on these cells differ with the resolution",
                 "the same call at another resolution; the states' markers")],
    tested_against="C.states_and_composition's tests on the synthetic screen",
    choices={"resolution": [1.0, 0.5]}, inputs={"n_pcs": "principal components (30)", "seed": "random seed (0)",
                                                "root": "a state (S01, ...) or {column: level}: the cells a diffusion "
                                                        "pseudotime starts from"}))
def _states(s, targets, reference, resolution=1.0, n_pcs=30, seed=0, root=None):
    import scanpy as sc
    from . import quality, states as st
    from .question_modules import c, packages
    # the states are fitted on the cells the quality rule keeps (with a guide or not); a cell it leaves out has none
    fit = np.asarray(s.label).astype(str) != quality.FAILED
    a, h = st.embed(s.X[np.flatnonzero(fit)] if not fit.all() else s.X, s.genes, n_pcs=n_pcs, seed=seed)
    fitted = st.leiden_states(h, resolution, seed)
    sc.tl.umap(h, random_state=seed)  # the map the states are drawn on
    lab = np.full(len(fit), "", dtype=object)
    lab[fit] = fitted
    lab = lab.astype(str)
    xy = np.full((len(fit), 2), np.nan)
    xy[fit] = h.obsm["X_umap"]
    table, _ = c.composition_rows(s, targets, reference, lab, "same_samples", seed)
    calls = [packages.call("scanpy", "pp.normalize_total, pp.log1p, pp.highly_variable_genes, pp.scale, tl.pca, "
                           "pp.neighbors, tl.leiden, tl.umap, tl.rank_genes_groups", data=s.X, layer="raw_counts",
                           resolution=resolution, flavor="seurat", n_top_genes=2000, n_pcs=n_pcs, random_state=seed),
             packages.call("scipy", "stats.fisher_exact", contrast="each target's share in a state against the "
                           "control cells of its own samples, in its mix"),
             packages.call("statsmodels", "stats.multitest.multipletests", method="fdr_bh")]
    cells = pd.DataFrame({"state": lab, "umap_1": xy[:, 0], "umap_2": xy[:, 1]},
                         index=np.asarray(s.adata.obs_names).astype(str))
    if root is not None:  # a state, or {column: level}
        if isinstance(root, dict):
            (column, level), = root.items()
            if column not in s.adata.obs:
                raise ValueError(f"no cell column {column!r} for the root; the columns are {list(s.adata.obs)}")
            within = s.adata.obs[column].astype(str).to_numpy()[fit] == str(level)
        else:
            within = fitted == str(root)
        if not within.any():
            raise ValueError(f"no cell in the root {root!r}; the states are {sorted(set(fitted))}")
        cells["pseudotime"] = np.nan
        cells.loc[fit, "pseudotime"] = st.pseudotime(h, within, seed)
        calls.append(packages.call("scanpy", "tl.diffmap, tl.dpt", root=root, random_state=seed,
                                   root_cell="the root's cell nearest its mean profile"))
    return table, calls, {"states": cells, "markers": st.state_markers(a, fitted, 10),
                          "receptor_segments_left_out": a.uns.get("receptor_segments_left_out", [])}


@block("on_target", Contract(
    serves="B.efficacy", applies_when="a target gene in the matrix",
    needs="raw counts and each cell's top guide",
    returns="per guide and sample: its target gene in the guide's cells against the reference cells: log2 fold "
            "change of counts per million, the shares detecting it, whether the change would be visible",
    fails_when=[("a knockout's transcript can stay", "read with B.efficacy's line: at least 30% down is past it; "
                 "no drop is 'transcript unchanged' for a knockout")],
    tested_against="B.efficacy's tests: guide A_1 knocks its gene down, A_2 does not, on the synthetic screen",
    choices={"method": ["pooled_cpm", "mean_cp10k", "detection"]}))
def _on_target(s, targets, reference, method="pooled_cpm"):
    """steps.on_target's formulas: pooled_cpm (B.efficacy's, the log2fc column always), mean_cp10k, detection (in
    the change column)."""
    from .question_modules.b import own_gene_effects
    t = own_gene_effects(s, _least_cells(), reference, method=method)
    t = t[t["target"].isin(targets)].reset_index(drop=True) if len(t) else t
    return t, [{"package": "pertura", "function": "B.efficacy's own-gene effect", "layer": "raw_counts"}], {}


@block("similarity", Contract(
    serves="E.similar_targets", applies_when="2 or more targets, or guides, with enough cells",
    needs="log-normalised expression, labels, each cell's strongest guide, samples and the control cells",
    returns="every two targets' (level target) or guides' (level guide) profiles, each cell against its sample's "
            "control cells, over the genes well expressed there (0.25 UMI per cell, Replogle et al. 2022) with the "
            "targeted genes left out: pooled over the samples of one kind of capture and compared once per kind (the "
            "default), or (profile by_sample) compared within each sample and averaged over the samples holding both; "
            "pairs of control guides of one kind, each against its sample's other control cells, as the reference, "
            "and each pair's share of them as high (pertura.steps.similarity); with more than one kind of capture "
            "(the delivery, the batch, the design's other grouping factor), each kind's mean and share beside them "
            "and where the kinds disagree (kinds_disagree); extra['matrix'] the groups' mean similarity, "
            "extra['control_pairs'] the reference values",
    fails_when=[("a guide's change of its own gene makes a target's guides alike",
                 "the targeted genes are left out (mask_targets)"),
                ("profiles of few cells are noisy and agree with nothing",
                 "the control-guide pairs, of guides with as few cells, are the reference")],
    tested_against="the synthetic screens: the guides of a target with a planted effect correlate above every pair "
                   "of control guides, those of an inert target do not; two targets sharing an effect correlate",
    choices={"level": ["target", "guide"], "metric": ["pearson", "spearman", "cosine"],
             "profile": ["pooled", "by_sample"]},
    inputs={"min_cells": "a target's or guide's cells for its profile: in all, pooled (30); in one sample, by_sample",
            "mask_targets": "leave the targeted genes out (True)",
            "genes": "well_expressed (the default), detected, or all"}))
def _similarity(s, targets, reference, level="target", metric="pearson", min_cells=30, mask_targets=True,
                genes="well_expressed", profile="pooled"):
    from .steps import similarity
    if reference != "control_guides":
        raise ValueError("similarity reads each profile against its sample's control cells")
    table, matrix, control = similarity.pairs(s, level, targets, metric, min_cells, mask_targets, genes,
                                              profile=profile)
    calls = [{"package": "numpy", "function": f"profiles compared ({metric}), each cell against its sample's "
              "control cells", "layer": "lognorm", "params": {"level": level, "genes": genes, "min_cells": min_cells,
                                                            "mask_targets": mask_targets, "profile": profile}}]
    return table, calls, {"matrix": matrix, "control_pairs": [round(float(x), 6) for x in control]}


def _table_of(of, what):
    """An earlier result's table by the result or its call ID (this process's, else its saved table)."""
    if isinstance(of, Result):
        return of.table
    if of in _TABLES:
        return _TABLES[of]
    try:
        return load(of)
    except (ValueError, KeyError, OSError) as e:
        raise ValueError(f"{what}: no table for {of!r}; give the result itself or its call ID") from e


@block("correlate_effects", Contract(
    serves="E.program_axis", applies_when="each target's effect on several programs (a pt.scores result)",
    needs="a pt.scores result (target, score, effect): of",
    returns="for every two programs, the correlation (Pearson or Spearman) of the targets' effects on the one with "
            "their effects on the other, each mode apart where the screen has two, Benjamini-Hochberg over every "
            "pair (pertura.steps.effects_across_targets)",
    fails_when=[("a few strong targets make a correlation alone", "the targets counted, and the scatter drawn"),
                ("programs sharing genes correlate by construction", "E.program_axis counts their shared top genes")],
    tested_against="synthetic: targets planted to move P1 up and P2 down give r below -0.8; independent programs "
                   "none at q < 0.05",
    choices={"method": ["pearson", "spearman"]},
    inputs={"of": "a pt.scores result, or its call ID",
            "top_genes": "{program: [genes]} (C.programs' diagnostics), to count the genes two programs share",
            "null": "pt.calibrate on the same scores, or its call ID: the same correlation over the control-guide "
                    "groups beside each pair (r_groups), a reference to read r against, not a test"}))
def _correlate_effects(s, targets, reference, of=None, method="pearson", top_genes=None, null=None):
    from .question_modules.e import _modes_of
    from .steps import effects_across_targets as eat
    table = _table_of(of, "correlate_effects")
    table = table[table["target"].isin(targets)]
    modes = _modes_of(s, s.design, sorted(set(table["target"])))
    out = (eat.correlate_against_groups(table, _table_of(null, "correlate_effects null"), modes, method, top_genes)
           if null is not None else eat.correlate(table, modes, method, top_genes))
    return out, [{"package": "scipy", "function": f"stats.{method}r over the targets", "layer": "effects",
                  "params": {"method": method}}, {"package": "statsmodels", "function": "multipletests",
                                                  "params": {"method": "fdr_bh"}}], {}


@block("rank_on", Contract(
    serves="E.program_axis", applies_when="each target's effect on the two programs of an axis (a pt.scores result)",
    needs="a pt.scores result (target, score, effect): of; the programs up and down",
    returns="the targets ranked on two programs at once: the effect on up minus the effect on down, and, given the "
            "same scores' calibration (pt.calibrate on them), whether each goes past the control-guide groups' "
            "95th percentile of the same axis (pertura.steps.effects_across_targets)",
    fails_when=[("the axis can be moved by a target's effect on one program alone", "its two effects, in the table"),
                ("an axis read without its null ranks noise too", "null: the scores' calibration")],
    tested_against="synthetic: a target planted up on one program and down on the other ranks first and past the "
                   "control-guide groups",
    inputs={"of": "a pt.scores result, or its call ID", "up": "the program the axis wants up",
            "down": "the program the axis wants down", "null": "pt.calibrate on the same scores, or its call ID"}))
def _rank_on(s, targets, reference, of=None, up=None, down=None, null=None):
    from .steps import effects_across_targets as eat
    if not up or not down:
        raise ValueError("rank_on needs up and down: the two programs of the axis")
    table = _table_of(of, "rank_on")
    table = table[table["target"].isin(targets)]
    per, summary = eat.rank_on(table, up, down, _table_of(null, "rank_on null") if null is not None else None)
    return per, [{"package": "pertura", "function": "the axis: effect on up minus effect on down",
                  "layer": "effects", "params": {"up": up, "down": down}}], {"summary": summary}


@block("cluster_targets", Contract(
    serves="E.co_functional_sets", applies_when="4 or more targets with effects on 2 or more features",
    needs="a pt.scores result (target, score, effect) or a pt.similarity result: of",
    returns="sets of targets: hierarchical (average linkage on the correlation distance, the cut of 2 to 10 sets "
            "with the best silhouette), HDBSCAN (scikit-learn, min_cluster_size 4, as Replogle et al. 2022; -1 is "
            "unassigned) or Leiden on the targets' neighbour graph; each set's stability over resampled features "
            "(the share of its pairs together again, Zhu et al. 2026) (pertura.steps.effects_across_targets)",
    fails_when=[("the number of sets is a cut of the tree", "the silhouette of the cut, and each set's stability"),
                ("a set of a few weak targets can reflect noise",
                 "each set's stability over resampled features")],
    tested_against="synthetic: two groups of targets moving different programs fall into two stable sets",
    choices={"method": ["hierarchical", "hdbscan", "leiden"]},
    inputs={"of": "a pt.scores or pt.similarity result, or its call ID",
            "draws": "resamples for each set's stability (50)", "seed": "random seed (0)",
            "leave_out": "a column of a scores result split by it (pt.scores with by=, such as the unit): each set's "
                         "members kept together with one of its values left out, beside the same share by chance "
                         "(kept_without_one, kept_by_chance), a description, not a test"}))
def _cluster_targets(s, targets, reference, of=None, method="hierarchical", draws=50, seed=0, leave_out=None):
    from .steps import effects_across_targets as eat
    table = _table_of(of, "cluster_targets")
    if {"group", "other", "correlation"} <= set(table.columns):  # a similarity result: 1 - r as the distance
        t = table[table["kind"] != "control guides"] if "kind" in table else table
        t = t[t["group"].astype(str).isin(targets) & t["other"].astype(str).isin(targets)]
        pair = [tuple(sorted(x)) for x in zip(t["group"].astype(str), t["other"].astype(str))]
        eat.one_row_per_key(pd.DataFrame(pair, columns=["a", "b"]), ("a", "b"), "cluster_targets' similarity table")
        names = sorted(set(t["group"].astype(str)) | set(t["other"].astype(str)))
        R = pd.DataFrame(np.nan, index=names, columns=names)
        for (a, b), r in zip(pair, t["correlation"]):
            R.at[a, b] = R.at[b, a] = r
        np.fill_diagonal(R.values, 1.0)
        R, left_out = eat.complete_pairs(R)
        names, X = list(R.index), R.to_numpy()
        got, best = eat.clusters(X, names, method, seed=seed, distance=1 - X) if len(names) else (None, None)
    else:
        scoped = table[table["target"].astype(str).isin(targets)]
        if leave_out is not None:  # a table split by a column: the sets on the mean over its values
            if leave_out not in scoped:
                raise ValueError(f"cluster_targets: leave_out {leave_out!r} is not a column of the table")
            scoped = scoped.groupby(["target", "score"], as_index=False)["effect"].mean()
        M, left_out = eat.complete_rows(eat.effects_matrix(scoped))
        names, X = list(M.index), M.to_numpy()
        got, best = eat.clusters(X, names, method, seed=seed)
    if got is None:
        raise ValueError("cluster_targets: too few targets to cut into 2 or more sets"
                         + (f" once {left_out} are left out (a missing effect or an unobserved pair is not 0)"
                            if left_out else ""))
    stable = eat.stability(X, names, got, method, draws=draws, seed=seed)
    out = pd.DataFrame({"target": names, "set": [got[n] for n in names]})
    out["stability"] = out["set"].map(stable)
    if leave_out is not None:
        kept = eat.kept_without_one(table[table["target"].astype(str).isin(names)], leave_out, got, method, seed)
        out["kept_without_one"] = out["set"].map(lambda k: (kept.get(k) or {}).get("kept"))
        out["kept_by_chance"] = out["set"].map(lambda k: (kept.get(k) or {}).get("by_chance"))
    return out, [{"package": "scipy, scikit-learn, leidenalg", "function": f"{method} clustering of the targets",
                  "layer": "effects", "params": {"method": method, "draws": draws}}], \
        {"summary": {"sets": int(len(set(out["set"]) - {-1})), "silhouette": best, "stability": stable,
                     "left_out": left_out}}


@block("pair_fit", Contract(
    serves="E.combinations", applies_when="pairs of perturbations (labels with | or +) beside their singles",
    needs="log-normalised expression, labels with the pairs and their singles, samples and the control cells",
    returns="per pair with both singles (min_cells cells each, pooled over the samples that hold the pair, each "
            "cell against its own sample's control cells): Norman et al. 2019's fit of its profile on its singles' "
            "(fitted_weights: the weights and R2) or the plain sum (plain_sum), the additive R2 and slope, the "
            "departure's size, the cells, whether each member moves its gene in the pair's cells; where 2 or more "
            "samples hold the pair and both singles, the departure's agreement between them and the share of "
            "control pseudo-pairs (control cells drawn at the pair's cell numbers) whose departure agrees as well; "
            "per program set the observed and additive means (pertura.steps.pairs); extra['profiles'] each pair's "
            "observed, additive and fitted profile",
    fails_when=[("a pair's departure can be noise that agrees by chance between two samples",
                 "control pseudo-pairs at its cell numbers, through the same fit, where 2 or more samples hold it"),
                ("a member that does not act makes a pair look like its other single", "members_act, per pair")],
    tested_against="E.combinations' tests: CRISPRai's pairs, against an earlier hand analysis",
    choices={"model": ["fitted_weights", "plain_sum"]},
    inputs={"min_cells": "a label's cells for its profile, pooled over the samples (20; in each sample, for the "
                         "agreement check)",
            "gene_sets": "hallmark (the default) or given", "gene_lists": "{set: [genes]} for gene_sets given",
            "pairs": "the pairs to keep (all by default)"}))
def _pair_fit(s, targets, reference, model="fitted_weights", min_cells=20, gene_sets="hallmark", gene_lists=None,
              pairs=None):
    from .steps import pairs as step
    if reference != "control_guides":
        raise ValueError("pair_fit reads each profile against its sample's control cells")
    df, kept, report = step.fit_pairs(s, s.design, min_cells, model, gene_sets, gene_lists)
    if pairs is not None and len(df):
        df = df[df["pair"].isin(list(pairs))].reset_index(drop=True)
    profiles = (pd.concat(kept, ignore_index=True) if kept else
                pd.DataFrame(columns=["pair", "gene", "observed", "additive", "fitted"]))
    calls = [{"package": "numpy", "function": "linalg.lstsq (Norman et al. 2019's fit of a pair on its singles)"
              if model == "fitted_weights" else "the plain sum of the singles' profiles", "layer": "lognorm",
              "params": {"model": model, "min_cells": min_cells, "pseudo_pairs": "50 draws of control cells"}}]
    return df, calls, {"profiles": profiles, "report": report}


def genes_table_file(run_dir):
    """The file of a module run's own pt.genes call: its test of every gene, not only the changed ones; None when the
    run made no such call (a method that does not run as blocks)."""
    log = os.path.join(run_dir, "blocks", LOG)
    if not (os.path.isdir(run_dir) and os.path.exists(log)):
        return None
    with open(log, encoding="utf-8") as fh:
        calls = [json.loads(line) for line in fh if line.strip()]
    for c in calls:
        if c.get("block") == "genes" and not c.get("check_of"):
            path = os.path.join(run_dir, "blocks", "outputs", c["call_id"], "table.tsv.gz")
            if os.path.exists(path):
                return path
    return None


def genes_calibration_tables(genes_table):
    """The calibration of the pt.genes call whose table this is, from its run's block log: each test's run on groups of
    control guides (the groups as targets), [] where the call has none or the table is not a run's."""
    parts = os.path.normpath(str(genes_table)).split(os.sep)  # <run>/blocks/outputs/<call ID>/table.tsv.gz
    if len(parts) < 4 or parts[-3] != "outputs" or parts[-4] != "blocks":
        return []
    blocks_dir, call_id = os.sep.join(parts[:-3]), parts[-2]
    log = os.path.join(blocks_dir, LOG)
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as fh:
        calls = [json.loads(line) for line in fh if line.strip()]
    paths = [os.path.join(blocks_dir, "outputs", c["call_id"], "table.tsv.gz") for c in calls
             if c.get("block") == "genes" and c.get("check") == "calibration" and c.get("check_of") == call_id]
    return [pd.read_csv(p, sep="\t") for p in paths if os.path.exists(p)]


def _genes_table(of):
    """A gene result's full table for enrichment: a pt.genes result or call ID, or a module run of genes (its ID, or
    one of its tables' IDs such as <run>:changed_genes), read as the run's own pt.genes call, so that the universe is
    every gene tested, not only the changed ones (development session 3, problem 23)."""
    oid = None if isinstance(of, Result) else str(of)
    if oid and not oid.startswith(("table:", "cells:", "result:", "file:")):
        path = genes_table_file(os.path.join(CONTEXT.get("jobs_dir") or "", oid.split(":")[0]))
        if path:
            return pd.read_csv(path, sep="\t")
    return _table_of(of, "enrich")


@block("enrich", Contract(
    serves="G.meaning", applies_when="a gene result (pt.genes) with changed genes, and the organism",
    needs="a pt.genes result: of; experiment.organism for the MSigDB collections, CollecTRI and PROGENy",
    returns="per target: the gene sets over-represented among its genes up and among its genes down (method ora: "
            "each against the genes its test tested, the universe), or enriched at the ends of its ranking by effect "
            "(method gsea), by GSEApy or clusterProfiler with their defaults (pertura.steps.enrichment); set, "
            "overlap, set_size, gene_ratio, p, q (the package's BH), the genes; or (method ulm) each source's "
            "activity from the target's signed test statistics over its tested genes, decoupler's univariate linear "
            "model as its tutorials read factors (collectri) and pathways (progeny), q its BH over the sources within "
            "the target",
    fails_when=[("a whole-genome universe inflates enrichment", "each target's tested genes as its universe"),
                ("up and down genes pooled hide sets that go one way", "up and down apart (direction both)"),
                ("enrichment names a pathway, not a mechanism in these cells", "the result is prior knowledge")],
    tested_against="each backend's known case under its own rule, and a planted set found first",
    choices={"method": ["ora", "gsea", "ulm"], "backend": ["gseapy", "clusterprofiler"],
             "collection": [*COLLECTION_NAMES, "collectri", "progeny"], "direction": ["both", "up", "down"]},
    inputs={"of": "a pt.genes result or its call ID, or a C.genes run (its ID, or a table's, such as "
                  "<run>:changed_genes): the run's own test of every gene", "gene_lists": "{set: [genes]} in place of "
                  "a collection",
            "q": "the gene result's adjusted p for a changed gene (0.05)", "seed": "GSEA's seed (0)"}))
def _enrich(s, targets, reference, of=None, method="ora", backend="gseapy", collection="hallmark", direction="both",
            gene_lists=None, q=None, seed=0):
    from .question_modules.data import value
    from .steps import enrichment
    q = _q(q)
    table = _genes_table(of)
    organism = value(s.design, "experiment.organism")
    if not gene_lists and organism not in ("human", "mouse"):
        raise ValueError(f"enrich needs experiment.organism, human or mouse, for {collection} (here {organism!r}), "
                         "or gene_lists")
    if method == "ulm":
        net = enrichment.network(collection, organism, gene_lists)
        out = enrichment.activities(table[table["target"].astype(str).isin(set(map(str, targets)))], net)
        return out, [{"package": "decoupler", "function": "mt.ulm", "layer": "a gene result",
                      "params": {"net": collection if not gene_lists else "given", "tmin": 5,
                                 "data": "each target's signed test statistics over its tested genes"}}], {}
    if collection in enrichment.RESOURCES:
        raise ValueError(f"{collection} is a weighted network: read it with method ulm")
    work = os.path.join(CONTEXT.get("call_dir") or os.getcwd(), "enrich")
    os.makedirs(work, exist_ok=True)
    if method == "ora":
        enrichment.checked(backend, work)
    parts = []
    for t in [x for x in dict.fromkeys(table["target"]) if x in set(targets)]:
        part = table[table["target"] == t]
        universe = set(part["gene"].astype(str))
        sets = enrichment.sets_for(collection, organism, universe, gene_lists)
        if method == "ora":
            ways = {"up": part["effect"] > 0, "down": part["effect"] < 0}
            for way in (["up", "down"] if direction == "both" else [direction]):
                genes = part.loc[(part["q"] < q) & ways[way], "gene"].astype(str)
                d = enrichment.ora(genes, universe, sets, backend, work)
                parts.append(d.assign(target=t, direction=way, list_size=len(set(genes)),
                                      universe=len(universe)))
        else:
            rank = part.set_index(part["gene"].astype(str))["effect"]
            parts.append(enrichment.gsea(rank, sets, backend, work, seed=seed).assign(target=t, direction="ranking",
                                                                                       universe=len(universe)))
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["target", "direction", "set"])
    if method == "ora" and len(out):
        out["gene_ratio"] = out["overlap"] / out["list_size"].clip(lower=1)
    lead = ["target", "direction", "set"]
    out = out[lead + [c for c in out.columns if c not in lead]]
    package = "gseapy" if backend == "gseapy" else "clusterProfiler"
    function = {("gseapy", "ora"): "enrich", ("gseapy", "gsea"): "prerank", ("clusterprofiler", "ora"): "enricher",
                ("clusterprofiler", "gsea"): "GSEA (fgsea)"}[(backend, method)]
    calls = [{"package": package, "function": function, "layer": "a gene result",
              "params": {"collection": collection if not gene_lists else "given", "direction": direction, "q": q,
                         "universe": "each target's tested genes"}}]
    return out.reset_index(drop=True), calls, {}


@block("cell_type_response", Contract(
    serves="C.any_effect", applies_when="a screen of several cell types (a cell column naming them)",
    needs="log-normalised expression, labels, the control cells and a cell column of cell types: column",
    returns="per target and cell type: pertpy's Augur, a random forest telling the target's cells from the control "
            "cells within the cell type over subsamples of 20 cells a side, its mean AUC and the type's rank within "
            "the target (pertura.steps.cell_type_response); a related measure, not a test",
    fails_when=[("a cell type with more cells or more variable genes separates more easily",
                 "the same subsample size on each side, the cells counted"),
                ("an AUC is not a p-value", "the types read against each other; 0.5 is no separation")],
    tested_against="synthetic: a target that moves one cell type's genes ranks that type first",
    inputs={"column": "the cell column naming each cell's type", "n_subsamples": "Augur's subsamples (50)",
            "subsample_size": "cells a side in each subsample (20)", "seed": "random seed (0)"}))
def _cell_type_response(s, targets, reference, column=None, n_subsamples=50, subsample_size=20, seed=0):
    from .steps import cell_type_response
    if not column:
        raise ValueError("cell_type_response needs column, the cell column naming each cell's type")
    per, calls = cell_type_response.respond(s, column, targets, reference, n_subsamples, subsample_size, seed)
    return per, calls, {}


@block("response", Contract(
    serves="B.escape", applies_when="a perturbation that removes or adds a gene's activity (CRISPRi, CRISPRa, knockout)",
    needs="log-normalised expression, labels, samples and the control cells",
    returns="each cell's response to its perturbation (pertura.steps.response): mixscape, each target's cells called "
            "perturbed or not perturbed (pertpy's Mixscape: the perturbation signature within each sample, then a "
            "mixture model), per target the share perturbed; mixscale, each cell's score against the control cells "
            "(pertpy's Mixscale), per target its median and mean; each cell's class or score in extra['cells'], "
            "a cell set for the other blocks (pt.save_cells)",
    fails_when=[("a target with too few DE genes is not split (mixscape) or not scored (mixscale)",
                 "its cells count as not perturbed, or score 1; the table names the targets scored"),
                ("cells far from their nearest control cells are called perturbed, which other differences do too",
                 "pt.calibrate: control-guide groups through the same call")],
    tested_against="the synthetic screen: B's cells raise b0-b4, and Mixscape calls most of them perturbed",
    choices={"method": ["mixscape", "mixscale"]},
    inputs={"n_pcs": "principal components of the embedding (40)", "seed": "random seed (0)",
            "params": "{setting: value}: the method's settings changed from pertpy's defaults (pt.help('response'))"}))
def _response(s, targets, reference, method="mixscape", n_pcs=40, seed=0, params=None):
    from .steps import response
    if reference != "control_guides":
        raise ValueError("response reads each target's cells against the control cells")
    cells, per, calls = response.respond(s, method, n_pcs, seed, params)
    per = per[per["target"].isin(targets)].reset_index(drop=True)
    return per, calls, {"cells": cells.set_index("barcode"), "method": method}


# ---- the public calls ----------------------------------------------------------------------------------------------
def genes(cells=None, targets=None, reference="control_guides", method="wilcoxon_same_samples", by=None, params=None):
    """Genes changed in each target's cells against the reference. method: wilcoxon_same_samples (each target against
    the reference cells of its own samples, in its mix), wilcoxon (against every reference cell) or seurat3_lr (here);
    auto (the design's test per target), edger_ql, mast or seurat_lr (a job: a Handle). params: package settings
    changed from their defaults, such as {"logfc_threshold": 0.25} for Seurat's tests (pt.help("genes") lists them).
    by: a cell column, one call per value."""
    given = {"method": method, **({"params": dict(params)} if params else {})}
    return _run("genes", cells, targets, reference, by, given)


def any_effect(cells=None, targets=None, reference="control_guides", control_cells="same_samples", n_pcs=50,
               permutations=1000, by=None, seed=0, metric="edistance", draw=1000):
    """Whether each target's cells differ from the reference at all: the energy distance (or another of pertpy's
    distances, metric) and its permutation p (a job: a Handle). draw: each side drawn, seeded, to at most this many
    cells (the overview's 1,000), or none: every cell. pt.calibrate(res) puts groups of control guides through the
    same test, drawn the same way."""
    given = {"control_cells": control_cells, "n_pcs": n_pcs, "permutations": permutations}
    if seed:
        given["seed"] = seed
    if metric != "edistance":
        given["metric"] = metric
    if draw != 1000:
        given["draw"] = draw
    return _run("any_effect", cells, targets, reference, by, given)


def programs(cells=None, method="cnmf", fit_on="all", k=None, seed=0, params=None):
    """Gene programs found in the cells: each program's top genes, and each cell's usage (extra['usage']).
    method: cnmf (default; K the most stable of 5 to 15 unless given), sklearn_nmf, genenmf (GeneNMF's runNMF),
    genenmf_meta (GeneNMF's meta-programs across samples). fit_on: all, or controls (found in the control cells, every
    cell read on them). params: package settings changed from their defaults (pt.help("programs") lists them). Then
    pt.scores(programs=res) tests each target's usage. cNMF and GeneNMF run as a job."""
    given = {"method": method, "fit_on": fit_on, "k": k, "seed": seed, "params": dict(params) if params else None}
    return _run("programs", cells, None, "control_guides", None, {x: v for x, v in given.items() if v is not None})


def scores(sets=None, cells=None, targets=None, reference="control_guides", by=None, programs=None, min_genes=5,
           method="scanpy", test="auto", select=None, cell_values=None, contrast=None, proteins=None):
    """Each target's cells against the reference on gene-set scores (sets: {name: [genes]}, or an MSigDB collection's
    name such as hallmark, immunologic or cell_types, with select: its sets' names or a word their names hold; or
    contrast: {column, levels: [a, b], top}, the control cells' own genes up and down between two levels, read on the
    whole screen; method scanpy, scanpy's score_genes, or seurat, Seurat's AddModuleScore), on programs' usage
    (programs: a pt.programs result or its call ID), or on per-cell values as they are (cell_values: a numeric cell
    column, or a saved table's ID with a barcode column); test: auto (the linear model with the sample where there are
    several samples, else Wilcoxon), linear_model or wilcoxon."""
    named = {"test": test} if test != "auto" else {}
    if programs is not None:
        if isinstance(programs, Result):
            _remember(programs)
            programs = programs.call_id
        return _run("scores", cells, targets, reference, by, {"programs": programs, **named})
    if cell_values is not None:
        return _run("scores", cells, targets, reference, by, {"cell_values": cell_values, **named})
    if proteins is not None:
        return _run("scores", cells, targets, reference, by,
                    {"proteins": "all" if proteins is True else proteins, **named})
    given = {"sets": sets, "min_genes": min_genes, **named}
    if select is not None:
        given["select"] = select
    if contrast is not None:
        given["contrast"] = contrast
    if method != "scanpy":
        given["method"] = method
    return _run("scores", cells, targets, reference, by, given)


def replicate_agreement(cells=None, targets=None, reference="control_guides", genes="well_expressed"):
    """The screen's reproducibility, run once before pooling: each pair of samples' profiles of the same targets
    correlated over the genes (every target together, and each target in extra['per_target'])."""
    return _run("replicate_agreement", cells, targets, reference, None, {"genes": genes})


def cell_cycle(cells=None, method="scanpy", seed=0):
    """Each cell's S and G2/M scores and phase (extra['phases']) and each label's phase shares: scanpy's
    score_genes_cell_cycle, or method seurat for Seurat's CellCycleScoring (a job). Then
    pt.composition(of=res) tests each target's phase shares."""
    return _run("cell_cycle", cells, None, "control_guides", None,
                {"method": method, **({"seed": seed} if seed else {})})


def composition(of, cells=None, targets=None, reference="control_guides", column=None, control_cells="same_samples",
                seed=0, method="fisher", params=None):
    """Each target's share of cells in each state (or phase) of a labelling against its reference. method: fisher
    (Fisher's exact test on cells, against the control cells of its own samples, in its mix; control_cells all: every
    control cell), cmh (Cochran-Mantel-Haenszel over the samples), propeller (speckle, the samples' proportions),
    sccoda (pertpy's scCODA, credible effects), milo (pertpy's Milo, neighbourhoods), or auto (the design's lead per
    target). of: a pt.states or pt.cell_cycle result, or its call ID. params: package settings changed from their
    defaults. pt.calibrate(res) puts groups of control guides through the same test."""
    if isinstance(of, Result):
        _remember(of)
        of = of.call_id
    given = {"of": of, **({"column": column} if column else {})}
    if control_cells != "same_samples":
        given["control_cells"] = control_cells
    if seed:
        given["seed"] = seed
    if method != "fisher":
        given["method"] = method
    if params:
        given["params"] = dict(params)
    return _run("composition", cells, targets, reference, None, given)


def states(cells=None, targets=None, reference="control_guides", resolution=1.0, n_pcs=30, seed=0, by=None,
           root=None):
    """The cells' states, and each target's share in each state against the control cells of its own samples; root
    (a state, or {column: level}): each cell's diffusion pseudotime from it."""
    given = {"resolution": resolution, "n_pcs": n_pcs, "seed": seed}
    if root is not None:
        given["root"] = root
    return _run("states", cells, targets, reference, by, given)


def similarity(cells=None, targets=None, level="target", metric="pearson", min_cells=30, mask_targets=True,
               genes="well_expressed", profile="pooled"):
    """Every two targets' (level target) or guides' (level guide) profiles compared (Pearson, Spearman or cosine),
    each cell against its sample's control cells, over the genes well expressed there with the targeted genes left
    out: pooled over the samples of one kind of capture (the default), or by_sample, compared within each sample and
    averaged over the samples holding both; pairs of control guides of one kind are the reference (each pair's
    control_pairs_as_high). extra['matrix'] holds the groups' mean similarity."""
    given = {} if level == "target" else {"level": level}
    for k, v, d in (("metric", metric, "pearson"), ("min_cells", min_cells, 30), ("mask_targets", mask_targets, True),
                    ("genes", genes, "well_expressed"), ("profile", profile, "pooled")):
        if v != d:
            given[k] = v
    return _run("similarity", cells, targets, "control_guides", None, given)


def _given_of(of):
    if isinstance(of, Result):
        if not of.call_id:  # a result split by a column has no call of its own: it is kept under an ID of its own
            of.call_id = f"{of.block}-split-{uuid.uuid4().hex[:12]}"  # this process's: not kept for a later one
        _remember(of)
        return of.call_id
    return of


def correlate_effects(of, cells=None, targets=None, method="pearson", top_genes=None, null=None):
    """For every two programs of a pt.scores result, the correlation of the targets' effects on the one with their
    effects on the other (each mode apart), Benjamini-Hochberg over the pairs: an axis of two programs. top_genes
    ({program: [genes]}) counts the genes two programs share, as E.program_axis does."""
    given = {"of": _given_of(of), **({"method": method} if method != "pearson" else {}),
             **({"top_genes": top_genes} if top_genes else {}),
             **({"null": _given_of(null)} if null is not None else {})}
    return _run("correlate_effects", cells, targets, "control_guides", None, given)


def rank_on(of, up, down, null=None, cells=None, targets=None):
    """The targets of a pt.scores result ranked on two programs at once (effect on up minus effect on down); with
    null, the same scores' calibration (pt.calibrate), each read against the control-guide groups' 95th
    percentile of the same axis."""
    given = {"of": _given_of(of), "up": up, "down": down}
    if null is not None:
        given["null"] = _given_of(null)
    return _run("rank_on", cells, targets, "control_guides", None, given)


def cluster_targets(of, cells=None, targets=None, method="hierarchical", draws=50, seed=0, leave_out=None):
    """Sets of targets from a pt.scores result (their effects) or a pt.similarity result (1 - r): hierarchical,
    hdbscan or leiden, each set's stability over resampled features."""
    given = {"of": _given_of(of), **({"method": method} if method != "hierarchical" else {})}
    if draws != 50:
        given["draws"] = draws
    if seed:
        given["seed"] = seed
    if leave_out is not None:
        given["leave_out"] = leave_out
    return _run("cluster_targets", cells, targets, "control_guides", None, given)


def pair_fit(cells=None, model="fitted_weights", min_cells=20, gene_sets="hallmark", gene_lists=None, pairs=None):
    """Each pair of perturbations against its singles: Norman et al. 2019's fit (fitted_weights) or the plain sum,
    the departure's agreement between samples, and control pseudo-pairs at the pair's cell numbers as the
    reference."""
    given = {k: v for k, v, d in (("model", model, "fitted_weights"), ("min_cells", min_cells, 20),
                                  ("gene_sets", gene_sets, "hallmark"), ("gene_lists", gene_lists, None),
                                  ("pairs", pairs, None)) if v != d}
    return _run("pair_fit", cells, None, "control_guides", None, given)


def enrich(of, cells=None, targets=None, method="ora", backend="gseapy", collection="hallmark", direction="both",
           gene_lists=None, q=None, seed=0):
    """Each target's gene sets from a pt.genes result, or from a C.genes run (of: its ID, or one of its tables'):
    over-represented among its genes up and its genes down (ora; the genes its test tested as the universe), or
    enriched at the ends of its ranking by effect (gsea); by GSEApy or clusterProfiler, with their defaults, on the
    gene sets kept with Pertura (no network needed); or each factor's (collection collectri) or pathway's (progeny)
    activity from the target's changes (ulm, decoupler). Prior knowledge, kept apart from the evidence in the data."""
    given = {"of": _given_of(of)}
    for k, v, d in (("method", method, "ora"), ("backend", backend, "gseapy"), ("collection", collection, "hallmark"),
                    ("direction", direction, "both"), ("gene_lists", gene_lists, None), ("q", q, None),
                    ("seed", seed, 0)):
        if v != d:
            given[k] = v
    return _run("enrich", cells, targets, "control_guides", None, given)


def cell_type_response(column, cells=None, targets=None, reference="control_guides", n_subsamples=50,
                       subsample_size=20, seed=0):
    """Which cell types each target moves most, for a screen of several cell types: pertpy's Augur, the mean AUC of a
    random forest telling the target's cells from the control cells within each type (column: the cell column of
    types)."""
    given = {"column": column}
    for k, v, d in (("n_subsamples", n_subsamples, 50), ("subsample_size", subsample_size, 20), ("seed", seed, 0)):
        if v != d:
            given[k] = v
    return _run("cell_type_response", cells, targets, reference, None, given)


def response(cells=None, targets=None, method="mixscape", n_pcs=40, seed=0, params=None):
    """Each cell's response to its perturbation, against the control cells: mixscape (pertpy's Mixscape: perturbed
    or not perturbed, per target the share perturbed) or mixscale (pertpy's Mixscale: a score per cell, per target
    its median). extra['cells'] holds each cell's class or score. pt.calibrate(res) puts groups of control guides
    through the same call."""
    given = {} if method == "mixscape" else {"method": method}
    if n_pcs != 40:
        given["n_pcs"] = n_pcs
    if seed:
        given["seed"] = seed
    if params:
        given["params"] = dict(params)
    return _run("response", cells, targets, "control_guides", None, given)


def on_target(cells=None, targets=None, reference="control_guides", by=None, method="pooled_cpm"):
    """Each guide's effect on its own target gene, per sample: the log2 fold change of pooled counts per million
    (B.efficacy's); method mean_cp10k (mean normalised expression, as Replogle et al. 2022) or detection (the share
    of cells detecting it) adds that formula's change."""
    return _run("on_target", cells, targets, reference, by, {} if method == "pooled_cpm" else {"method": method})


# ---- the checks ----------------------------------------------------------------------------------------------------
def calibrate(result, seed=0):
    """The result's own call with groups of control guides in place of the targets, the rest of the control cells
    as their reference: what the same analysis gives with no effect. A gene result's calibration also gives, in its
    extra['summary'], each group's discoveries, their 95th percentile and the targets beyond it. A gene result by the
    design's tests (method auto) is calibrated once for each test it used, each target read against its own test's
    groups."""
    from .question_modules.c import calibration_groups
    if result.block == "on_target":
        raise ValueError("calibrate does not apply to pt.on_target: a group of control guides has no target gene of "
                         "its own; a guide's knockdown is read against the control cells directly (B.efficacy's 30% "
                         "rule)")
    if result.block == "similarity":
        raise ValueError("calibrate does not apply to pt.similarity: its pairs are read against pairs of control "
                         "guides already (control_pairs_as_high)")
    if result.block in ("correlate_effects", "rank_on", "cluster_targets", "enrich"):
        raise ValueError(f"calibrate does not apply to pt.{result.block}: it reads a table of effects; calibrate the "
                         "scores it reads (pt.calibrate on the pt.scores result), and give rank_on that as its null")
    if result.args.get("reference") != "control_guides":
        raise ValueError("calibrate needs a result against the control cells")
    s = _live(result)
    # the targets' cells leave the test; a screen-wide block keeps them where its first call had them (in the PCA
    # that pt.any_effect fits on every cell), testing the groups only
    lab = s.label if result.block in SCREEN_WIDE else np.where(np.isin(s.label, s.targets(1)), EXCLUDED, s.label)
    guides = {}
    for g, gl in calibration_groups(s, seed).items():
        m = (s.label == "control") & np.isin(s.top_guide, gl)
        if m.sum() >= _least_cells():
            lab = np.where(m, g, lab)
            guides[g] = gl
    if not guides:
        raise ValueError(f"too few control guides with {_least_cells()} cells for control-guide groups")
    null_screen, groups = s.with_labels(lab), list(guides)
    if result.block == "genes" and result.args.get("method") == "auto":
        used = sorted(set(result.table["test"])) if len(result.table) else []
        parts = {m: _rerun(result, null_screen, groups, "calibration", method=m) for m in used}
        if any(isinstance(p, Handle) for p in parts.values()):
            return parts  # each test's calibration as a job: {test: Handle}
        table = pd.concat([p.table for p in parts.values()], ignore_index=True) if parts else pd.DataFrame()
        out = Result("genes", {**result.args, "targets": groups, "check": "calibration"}, table,
                     {m: p.n for m, p in parts.items()}, [c for p in parts.values() for c in p.calls], result.cells,
                     screen=null_screen)
        out.extra["summary"] = calibration_summary(result, out, guides, by_method=parts)
        return out
    out = _rerun(result, null_screen, groups, "calibration")
    if isinstance(out, Result) and result.block == "genes":
        out.extra["summary"] = calibration_summary(result, out, guides)
    elif isinstance(out, Result) and result.block == "scores":
        out.extra["summary"] = scores_calibration_summary(result, out)
    elif isinstance(out, Result) and result.block == "any_effect":
        col = "distance" if "distance" in out.table.columns else "e_distance"  # the call's metric
        d = out.table[col].to_numpy(dtype=float) if len(out.table) else np.array([])
        d = d[np.isfinite(d)]
        p95 = float(np.percentile(d, 95)) if len(d) else None
        t = result.table
        out.extra["summary"] = {"groups": int(len(d)), "group_distances": [round(float(x), 6) for x in d],
                                "groups_95th_percentile": p95,
                                "targets_beyond": sorted(t.loc[t[col] > p95, "target"].astype(str))
                                if p95 is not None else []}
    elif isinstance(out, Result) and result.block == "response":
        col = "share_perturbed" if result.args.get("method", "mixscape") == "mixscape" else "median_score"
        v = out.table[col].to_numpy(dtype=float) if len(out.table) else np.array([])
        v = v[np.isfinite(v)]
        p95 = float(np.percentile(v, 95)) if len(v) else None
        t = result.table
        out.extra["summary"] = {"groups": int(len(v)), "measure": col,
                                "group_values": [round(float(x), 6) for x in v], "groups_95th_percentile": p95,
                                "targets_beyond": sorted(t.loc[t[col] > p95, "target"].astype(str))
                                if p95 is not None else []}
    elif isinstance(out, Result) and result.block == "composition":
        out.extra["summary"] = scores_calibration_summary(
            result.__class__(result.block, result.args, result.table.rename(
                columns={"state": "score", "mean_difference": "effect"}), result.n, result.calls, result.cells),
            out.__class__(out.block, out.args, out.table.rename(columns={"state": "score", "mean_difference": "effect"}),
                          out.n, out.calls, out.cells))
    return out


def scores_calibration_summary(result, null):
    """A score (or composition) result against its calibration: the group x score pairs that the same test calls
    changed in groups of control guides (group_pairs_changed; Schnitzler et al. 2024 counted the significant
    regulators among control cells sampled as targets); and per set, program or state, the groups' effects, the 95th
    percentile of their size, and the targets whose effect goes past it."""
    t, g = result.table, null.table
    by_score, pairs = {}, 0
    for name, part in g.groupby("score"):
        size = part["effect"].abs().to_numpy(dtype=float)
        size = size[np.isfinite(size)]
        if not len(size):
            continue
        p95 = float(np.percentile(size, 95))
        mine = t[t["score"] == name]
        past = sorted(mine.loc[mine["effect"].abs() > p95, "target"].astype(str))
        by_score[str(name)] = {"groups": int(len(size)), "groups_95th_percentile_effect": p95,
                               "group_effects": [round(float(x), 6) for x in part["effect"]],
                               "targets_beyond": past}
        pairs += len(past)
    return {"by_score": by_score, "pairs_beyond": pairs, "pairs": int(len(t)),
            "groups": int(g["target"].nunique()) if len(g) else 0, "group_pairs": int(len(g)),
            "group_pairs_changed": (int(g["changed"].astype(bool).sum()) if "changed" in g.columns else
                                    int((g["q"] < _q()).sum())) if len(g) else 0}


def calibration_summary(result, null, guides=None, by_method=None):
    """A gene result against its calibration: each control-guide group's discoveries (adjusted p < 0.05), their 95th
    percentile, and the targets beyond it; each fitted group with its guides, cells and genes tested; the share of the
    groups' p-values below 0.05. A group the test could not fit (no gene tested, as edgeR-QL without 3 samples
    holding it) is not a group with no discovery: it is left out of the counts and named, as C.genes' calibration
    does. by_method: one summary per test of a result by the design's tests, each target against its own test's
    groups."""
    from .steps.genes import fitted as fitted_by
    t = result.table[result.table["q"] < _q()].groupby("target").size().reindex(result.args["targets"],
                                                                                    fill_value=0)
    target_discoveries = {k: int(v) for k, v in t.items()}
    if by_method:
        each = {m: calibration_summary(result, part, guides) for m, part in by_method.items()}
        test_of = result.table.drop_duplicates("target").set_index("target")["test"].to_dict()
        line = {x: each.get(test_of.get(x), {}).get("groups_95th_percentile") for x in target_discoveries}
        return {"by_method": each, "target_tests": {x: test_of.get(x) for x in target_discoveries},
                "targets_beyond": sorted(x for x, n in target_discoveries.items() if line[x] is not None and n > line[x]),
                "target_discoveries": target_discoveries}
    fitted = fitted_by(null.args.get("method"), null.table, null.args["targets"], null.calls)
    unfitted = [g for g in null.args["targets"] if g not in fitted]
    if not fitted:
        return {"not_run": f"the test could fit none of the {len(unfitted)} control-guide groups",
                "groups_not_fitted": unfitted, "target_discoveries": target_discoveries}
    d = null.table[null.table["q"] < _q()].groupby("target").size().reindex(fitted, fill_value=0)
    p95 = float(np.percentile(d.to_numpy(), 95))
    cells = null._screen.label if null._screen is not None else None
    each_group = [{"group": g, "guides": ";".join((guides or {}).get(g, [])),
                   "cells": int((cells == g).sum()) if cells is not None else None,
                   "genes_tested": int((null.table["target"] == g).sum()), "discoveries": int(d[g])} for g in fitted]
    pv = null.table.loc[null.table["target"].isin(fitted), "p"].to_numpy() if "p" in null.table else np.array([])
    return {"group_discoveries": {k: int(v) for k, v in d.items()}, "groups_95th_percentile": p95,
            "groups_not_fitted": unfitted, "targets_beyond": sorted(t[t > p95].index.tolist()),
            "target_discoveries": target_discoveries, "groups": each_group,
            "share_p_below_0.05": round(float((pv < 0.05).mean()), 4) if len(pv) else None, "p_values": int(len(pv))}


def by_guide(result):
    """The result's own call on each guide's cells in place of its target's (the cells' top guide). A gene result's
    also gives, in its extra['agreement'], each guide's share of its target's changed genes with the same sign."""
    if result.block == "on_target":
        raise ValueError("pt.on_target is already per guide: its table has a row for each guide (and sample)")
    s = _live(result)
    lab, guides = s.label.copy(), []
    for t in result.args["targets"]:
        cells = s.label == t
        for g in sorted(set(s.top_guide[cells]) - {""}):
            m = cells & (s.top_guide == g)
            lab = np.where(m, g if m.sum() >= _least_cells() else EXCLUDED, lab)
            if m.sum() >= _least_cells():
                guides.append(g)
    if not guides:
        raise ValueError(f"no guide of {result.args['targets']} has {_least_cells()} cells")
    out = _rerun(result, s.with_labels(lab), guides, "by_guide")
    if isinstance(out, Result) and result.block == "genes":
        out.extra["agreement"] = guide_agreement(result, out, s)
    return out


def guide_agreement(result, per_guide, s):
    of = dict(zip(s.guides["guide"].astype(str), s.guides["target"].astype(str)))
    rows = []
    for t in result.args["targets"]:
        ch = result.table[(result.table["target"] == t) & (result.table["q"] < _q())].set_index("gene")["effect"]
        for g in [g for g in per_guide.args["targets"] if of.get(g) == t]:
            e = per_guide.table[per_guide.table["target"] == g].set_index("gene")["effect"].reindex(ch.index)
            rows.append({"target": t, "guide": g, "changed_genes": int(len(ch)),
                         "same_sign": float((np.sign(e) == np.sign(ch)).mean()) if len(ch) else np.nan})
    return pd.DataFrame(rows, columns=["target", "guide", "changed_genes", "same_sign"])


def thinned(s, deep, factor, rng):
    """The screen with the counts of the `deep` cells thinned binomially by factor (each count kept with probability
    factor), every per-cell value that depends on them recomputed."""
    X = s.X.tocsr(copy=True)
    rows = np.repeat(np.arange(X.shape[0]), np.diff(X.indptr))
    sel = deep[rows]
    X.data[sel] = rng.binomial(X.data[sel].astype(np.int64), min(1.0, factor)).astype(X.dtype)
    X.eliminate_zeros()
    t = s.with_labels(s.label)
    t.X, t.libsize = X, np.asarray(X.sum(1)).ravel()
    t.__dict__.pop("lognorm", None)
    return t


def match_depth(result, seed=0):
    """The result's own call with the deeper side's counts thinned to the other side's median depth, target by
    target (gene and score results)."""
    if result.block not in ("genes", "scores"):
        raise ValueError("match_depth is for gene and score results")
    s = _live(result)
    rng = np.random.default_rng(seed)
    parts = []
    for t in result.args["targets"]:
        mt = s.label == t
        mr = s.reference(result.args["reference"], target=t) & ~mt
        pair = s.subset(cells=mt | mr)
        pt_, pr = pair.label == t, ~(pair.label == t)
        dt, dr = np.median(pair.libsize[pt_]), np.median(pair.libsize[pr])
        deep, factor = (pt_, dr / dt) if dt > dr else (pr, dt / dr)
        r = _rerun(result, thinned(pair, deep, factor, rng), [t], "match_depth")
        if isinstance(r, Handle):
            return r
        parts.append(r.table.assign(depth_factor=round(float(min(1.0, factor)), 3)))
    return Result(result.block, {**result.args, "check": "match_depth"}, pd.concat(parts, ignore_index=True),
                  result.n, result.calls, result.cells, screen=s)


# ---- data, results, drawing ----------------------------------------------------------------------------------------
def describe(cells=None):
    """What the cells hold: their number per sample, the labels with their cells, the control guides, the cell
    columns (the conditions among them), the genes, the other calling methods, the kept cell sets, and where each
    block runs."""
    from .cell_sets import names
    s, cname, _ = _resolve(cells)
    obs = s.adata.obs
    ctrl = s.label == "control"
    return {"cells": cname, "n_cells": int(len(s.label)), "named_like": str(s.adata.obs_names[0]),
            "per_sample": {str(k): int(v) for k, v in pd.Series(s.unit).value_counts().sort_index().items()},
            "independent_units": bool(s.independent_units()),
            "labels": {str(k): int(v) for k, v in pd.Series(s.label).value_counts().head(25).items()},
            "targets_with_10_cells": len(s.targets(_least_cells())),
            "control_guides": {str(k): int(v) for k, v in pd.Series(s.top_guide[ctrl]).value_counts().head(25).items()},
            "cell_columns": sorted(map(str, obs.columns)),
            "conditions": {str(c): sorted(map(str, set(obs[c])))[:10] for c in obs.columns
                           if str(c).startswith("condition")},
            "genes": int(len(s.genes)), "other_calls": sorted(s.other_calls),
            "kept_cell_sets": names(CONTEXT["screen_dir"]),
            "runs": {"here": ["genes (wilcoxon_same_samples, wilcoxon, seurat3_lr)", "scores", "states", "on_target",
                              "the checks"],
                     "as a job (a Handle)": ["genes (edger_ql, mast)", "any_effect"]}}


class _Results:
    """Results kept under a name (Result.save), in any notebook of the project."""

    def _root(self):
        return os.path.dirname(CONTEXT["notebook_dir"])

    def list(self):
        out = {}
        for nb in sorted(os.listdir(self._root())):
            folder = os.path.join(self._root(), nb, "results")
            if os.path.isdir(folder):
                out.update({name: nb for name in sorted(os.listdir(folder))})
        return out

    def load(self, name):
        nb = self.list().get(name)
        if nb is None:
            raise ValueError(f"no kept result {name!r}; the kept results are {sorted(self.list())}")
        folder = os.path.join(self._root(), nb, "results", name)
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        r = Result(meta["block"], meta["args"], pd.read_csv(os.path.join(folder, "table.tsv.gz"), sep="\t"),
                   meta["n"], meta["calls"], meta["cells"], call_id=meta.get("call_id"))
        r.name = name
        return r


results = _Results()


def draw(result, target=None):
    """The result's chart as a figure for save_figure: a gene result's volcano (one target) or its changed genes per
    target; the energy distance per target; scores or state shares as targets by sets or states."""
    from .question_modules import charts, figures
    plt = figures._plt()
    t = result.table
    wide = figures.DOUBLE if len(result.args.get("targets", [])) > 30 else figures.SINGLE
    if result.block == "genes":
        if target or len(result.args["targets"]) == 1:
            target = target or result.args["targets"][0]
            fig = plt.figure(figsize=(figures.SINGLE, 62 * figures.MM))
            charts.volcano(t[t["target"] == target], None, f"{target}: genes vs control cells", fig=fig)
            return fig
        counts = t[t["q"] < _q()].groupby("target").size().reindex(result.args["targets"], fill_value=0)
        fig = plt.figure(figsize=(wide, 58 * figures.MM))
        charts.ranked_targets(pd.DataFrame({"target": counts.index, "genes": counts.to_numpy()}), "genes", None,
                              "Genes changed", "Genes changed per target", fig=fig)
        return fig
    if result.block == "any_effect":
        fig = plt.figure(figsize=(wide, 58 * figures.MM))
        metric = str(t["metric"].iat[0]) if "metric" in t.columns and len(t) else "edistance"
        if metric == "edistance":
            charts.ranked_targets(t, "e_distance", None, "E-distance vs controls", "Energy distance per target",
                                  q="q", fig=fig)
        else:
            charts.ranked_targets(t, "distance", None, f"{metric} vs controls", f"{metric} per target", q="q",
                                  fig=fig)
        return fig
    if result.block not in ("scores", "states", "composition"):
        raise ValueError(f"no chart for pt.{result.block} yet; draw it with matplotlib and save it with save_figure")
    col, val = ("score", "effect") if result.block == "scores" else ("state", "mean_difference")
    from .steps.effects_across_targets import one_row_per_key
    M = one_row_per_key(t, ("target", col), f"drawing pt.{result.block}").pivot(index="target", columns=col,
                                                                                values=val)
    fig = plt.figure(figsize=(figures.DOUBLE if M.shape[1] > 8 else figures.SINGLE,
                              max(40, 6 * M.shape[0] + 20) * figures.MM))
    charts.heatmap(M.to_numpy(), list(M.index), list(M.columns), None,
                   "Scores vs control cells" if col == "score" else "State shares vs control cells",
                   "Difference vs control cells", fig=fig)
    return fig


# ---- help ----------------------------------------------------------------------------------------------------------
HELP = {  # name -> (the question it serves, one line)
    "describe": ("A", "what the cells hold: cells per sample, labels, control guides, conditions, kept sets"),
    "screen": ("A", "the screen object; screen().subset(cells=..., labels=..., <column>=...) for a selection"),
    "save_cells": ("A", "keep a selection for every analysis (calls=True when it decides guide calls)"),
    "load_cells": ("A", "a kept cell set's barcodes and labels"),
    "results": ("A", "results kept under a name: results.list(), results.load(name)"),
    "save_table": ("A", "name a table you computed (table:<name>): any tool takes it by its ID"),
    "load": ("A", "any object by its ID: table:<name>, <run>:<table>, cells:<name>, result:<name>, a call's ID"),
    "on_target": ("B.efficacy", "each guide's effect on its own target gene, per sample"),
    "any_effect": ("C.any_effect", "the energy distance (or another pertpy distance, metric) of each target from the "
                                   "reference (a job)"),
    "genes": ("C.genes", "genes changed per target: wilcoxon_same_samples, wilcoxon, ttest, seurat3_lr here; edger_ql, "
                         "limma_voom, deseq2, glmgampoi, pydeseq2, mast, seurat_lr, sceptre, auto as a job"),
    "cell_type_response": ("C.any_effect", "which cell types each target moves most (Augur's AUC), for screens of "
                                           "several cell types"),
    "programs": ("C.programs", "gene programs found in the cells (cNMF, NMF, GeneNMF): top genes, each cell's usage"),
    "scores": ("C.programs", "gene-set scores (scanpy, or Seurat's AddModuleScore), or programs' usage, per target "
                             "against the reference"),
    "replicate_agreement": ("F.replicates_agree", "each pair of samples' target profiles correlated, before pooling"),
    "cell_cycle": ("C.cell_cycle", "each cell's cell-cycle scores and phase (scanpy, or Seurat's CellCycleScoring)"),
    "composition": ("C.states_and_composition", "each target's share of each state or phase: fisher, cmh, propeller, "
                                                "sccoda, milo, or auto (the design's test)"),
    "response": ("B.escape", "each cell's response to its perturbation: Mixscape's classes or Mixscale's scores"),
    "similarity": ("E.similar_targets", "two targets' (or guides') profiles correlated, against control-guide pairs"),
    "cluster_targets": ("E.co_functional_sets", "sets of targets from their effects or similarity, with stability"),
    "correlate_effects": ("E.program_axis", "two programs' effects correlated over the targets: an axis"),
    "rank_on": ("E.program_axis", "targets ranked on two programs at once, against the control-guide groups"),
    "pair_fit": ("E.combinations", "a pair against its singles (Norman et al. 2019), control pseudo-pairs"),
    "enrich": ("G.meaning", "gene sets among each target's changed genes, or its ranking (GSEApy, clusterProfiler)"),
    "states": ("C.states_and_composition", "cell states and each target's share per state"),
    "calibrate": ("checks", "a result's own call on control-guide groups: what it gives with no effect"),
    "by_guide": ("checks", "a result's own call guide by guide: whether the guides agree"),
    "match_depth": ("checks", "a result's own call with the deeper side's counts thinned"),
    "draw": ("figures", "a result's chart as a figure for save_figure"),
}


def policy(**changes):
    """Pertura's numbers for every later call of this notebook (pertura.policy): min_cells, min_cells_profile,
    min_replicates, q, min_effect. Each is a default, not a permission: a call that names its own targets, method or
    test runs wherever it can be computed, and its result says how it differs. pt.policy() alone gives them back as
    they are; pt.policy(reset=True) returns to the defaults."""
    from . import policy as pol
    if changes.pop("reset", False):
        pol.set_session()
    return pol.set_session(**changes) if changes else pol.current()


def help(name=None, question=None):
    """One line per name, the open question's first (question='C.genes'); or one name's call, choices and use."""
    if name is None:
        rows = sorted(HELP.items(), key=lambda kv: (not (question and kv[1][0].startswith(question)), kv[1][0]))
        return "\n".join(f"pt.{k:<12} [{q}] {line}" for k, (q, line) in rows)
    import inspect
    obj = globals().get(name)
    if name not in HELP or obj is None:
        raise ValueError(f"no pt.{name}; pt.help() lists them")
    text = f"pt.{name}{inspect.signature(obj) if callable(obj) else ''}\n{obj.__doc__}"
    if name in BLOCKS:
        c = BLOCKS[name].contract
        runs = "here" if name not in JOBS else (
            "as a job (a Handle)" if JOBS[name] is None else f"as a job (a Handle) for method {', '.join(JOBS[name])}")
        text += (f"\nchoices: {c.choices}\ninputs: {c.inputs}\nreturns: {c.returns}\nmisleads when: "
                 f"{c.fails_when}\nruns: {runs}")
        settings = method_settings(name)
        if settings:
            text += "\nsettings (params), with the package's defaults:" + "".join(
                f"\n  {m}: " + ("; ".join(f"{k} = {d!r} ({what})" for k, (d, what) in s.items()) or "none")
                for m, s in settings.items())
    return text


def method_settings(name):
    """{method: {setting: (default, what it does)}} of a block whose methods are a step's backends."""
    if name == "genes":
        from .steps import genes
        return {m: b["params"] for m, b in genes.BACKENDS.items()}
    if name == "programs":
        from .steps import programs
        return {m: v["params"] for m, v in programs.METHODS.items()}
    if name == "composition":
        from .steps import composition
        return {m: b["params"] for m, b in composition.BACKENDS.items()}
    if name == "response":
        from .steps import response
        return dict(response.METHODS)
    return {}


# ---- the job side --------------------------------------------------------------------------------------------------
def run_job(request_dir, screen_dir, design_path, out_dir):
    """Run a handed-over call on its cells (the request's barcodes and labels), outside the notebook: the table, the
    n and the calls in out_dir."""
    from .screen_object import load_design
    with open(os.path.join(request_dir, "request.json"), encoding="utf-8") as fh:
        req = json.load(fh)
    full = Screen(screen_dir, load_design(design_path) if design_path else {})
    cells = pd.read_csv(os.path.join(request_dir, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False)
    s = full.subset(cells=cells["barcode"].to_numpy(), labels=dict(zip(cells["barcode"], cells["label"])))
    os.makedirs(out_dir, exist_ok=True)
    here = os.getcwd()
    os.chdir(out_dir)
    CONTEXT.update({**{k: v for k, v in (req.get("context") or {}).items() if v}, "call_dir": out_dir,
                    "in_process": True})
    from . import policy
    try:
        with policy.using(req.get("policy") or None, explicit_choices=req.get("named") or ()):
            table, calls, extra = BLOCKS[req["block"]].fn(s, req["args"]["targets"], req["args"]["reference"],
                                                          **req["choices"])
            extra = {**(extra or {}), "policy": policy.used()}
    finally:
        os.chdir(here)
    _write_extras(out_dir, extra, len(s.label))
    table.to_csv(os.path.join(out_dir, "result.tsv.gz"), sep="\t", index=False)
    with open(os.path.join(out_dir, "block.json"), "w", encoding="utf-8") as fh:
        json.dump({"block": req["block"], "n": req["n"], "calls": calls, "call_id": req["call_id"]}, fh, indent=1,
                  default=str)
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump({"status": "measured", "summary": f"pt.{req['block']} on {req['cells']}: {len(table)} rows",
                   "tables": ["result"]}, fh, indent=1)
    return table


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pertura.blocks")
    sub = ap.add_subparsers(dest="command", required=True)
    j = sub.add_parser("job")
    for a in ("--request", "--screen", "--design", "--out"):
        j.add_argument(a, required=True)
    a = ap.parse_args(argv)
    print(f"{len(run_job(a.request, a.screen, a.design, a.out))} rows")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
