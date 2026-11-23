"""The data flow (docs/adr/0027): every object an analysis made, the run that made it and what that run used, read
from the record; each notebook cell's inputs, found with nothing for the agent to declare; and each result's chain
back to the deposit and the confirmed design.

An object is one of:
- the base: the deposit, the confirmed design and the screen object built from them ("deposit", "design",
  "screen");
- a module run's table ("<run>:<table>"), a table a cell saved with pt.save_table ("table:<name>"), a block result
  kept with result.save ("result:<name>"), any other file a cell wrote in its notebook ("file:<path in it>");
- a kept cell set ("cells:<name>"), a block call (its call ID) and a figure (its figure ID), which the record holds
  already.

A run's inputs ("uses") are object IDs:
- a module run: the screen object, its cell set, and the objects given as its inputs;
- a notebook cell: the objects whose files it read (the confinement's audit hook sees every file it opens), the
  objects behind the names it reads that earlier cells assigned (the kernel keeps its variables between cells), and
  the cells of the block calls it made.

Nothing here refuses (ADR 0027, rule 1): an input that is no object is left out, and a chain stops where the record
does. Following names between cells reads the cell's code (assignments, imports, a method called on a name); data
passed in ways the code does not show (a global changed inside a function) is not followed.
"""
import ast
import os
import re

BASE = ("deposit", "design", "screen")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")  # a saved table's or kept result's name
UNTRACKED = ("figures", "cell_sets", "blocks", "tmp")  # notebook folders whose files are entered by their own tools


def norm(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def objects(st):
    """Every object the record holds: {id: {kind, run_id, path, uses, ...}}; uses only where the object has its own
    inputs (a block call: its cells), else the run's are meant."""
    out = {oid: dict(o) for oid, o in st.get("objects", {}).items()}
    for name, c in st.get("cell_sets", {}).items():
        out.setdefault(f"cells:{name}", {"kind": "cell_set", "run_id": c.get("run_id"), "path": None})
    for cid, c in st.get("block_calls", {}).items():
        outputs = c.get("outputs") or {}
        out.setdefault(cid, {"kind": "block_result", "run_id": c.get("run_id"), "uses": [cells_of(c)],
                             "path": outputs.get("table") or next(iter(outputs.values()), None),
                             "block": c.get("block"), "choices": c.get("choices") or {}, "cells": c.get("cells")})
    for fid, f in st.get("figures", {}).items():
        out.setdefault(fid, {"kind": "figure", "run_id": f.get("run_id"), "path": (f.get("paths") or {}).get("png")})
    return out


def cells_of(call):
    """The object a block call's cells are: its kept cell set, else the screen object (every cell, or a selection
    made in the cell's code)."""
    return f"cells:{call['cells']}" if call.get("kept_cells") and call.get("cells") not in (None, "every cell") \
        else "screen"


def path_index(st, screen_dir=None, jobs_root=None):
    """(path, object ID) pairs, longest path first, to find the object a file belongs to: an object's own file or
    folder, a kept cell set's table, a block call's outputs, and a job run's folder (its run ID)."""
    pairs = []
    for oid, o in objects(st).items():
        if o.get("path"):
            pairs.append((norm(o["path"]), oid))
    for cid, c in st.get("block_calls", {}).items():
        for p in (c.get("outputs") or {}).values():
            pairs.append((norm(os.path.dirname(p)), cid))
    if screen_dir:
        for name in st.get("cell_sets", {}):
            for ext in (".tsv", ".json"):
                pairs.append((norm(os.path.join(screen_dir, "cell_sets", name + ext)), f"cells:{name}"))
    if jobs_root:
        for rid, r in st.get("runs", {}).items():
            if r.get("job_id"):
                pairs.append((norm(os.path.join(jobs_root, r["job_id"])), rid))
    return sorted(set(pairs), key=lambda x: -len(x[0]))


def object_of(path, index):
    p = norm(path)
    for root, oid in index:
        if p == root or p.startswith(root.rstrip(os.sep) + os.sep):
            return oid
    return None


def written_objects(notebook_dir, paths):
    """The objects for the files a cell wrote in its notebook folder: {id: (kind, path)}. A saved table is
    table:<name>, a kept block result result:<name>, any other file file:<its path in the folder>; the folders
    whose files their own tools enter (figures, cell sets, block calls, caches) are left out."""
    out = {}
    root = os.path.realpath(os.path.abspath(notebook_dir))
    written = {os.path.normcase(os.path.realpath(os.path.abspath(p))) for p in paths}
    for p in paths:
        real = os.path.realpath(os.path.abspath(p))  # its own case: an ID keeps the name the agent gave
        if not os.path.normcase(real).startswith(os.path.normcase(root) + os.sep) or not os.path.isfile(real):
            continue
        parts = os.path.relpath(real, root).split(os.sep)
        if parts[0] in UNTRACKED:
            continue
        stem = parts[1][:-len(".json")] if len(parts) == 2 and parts[1].endswith(".json") else None
        table = os.path.join(root, "tables", f"{stem}.tsv.gz") if stem else None
        if parts[0] == "tables" and len(parts) == 2 and parts[1].endswith(".tsv.gz") and NAME.match(parts[1][:-7]):
            out[f"table:{parts[1][:-7]}"] = ("table", real)
        elif parts[0] == "tables" and table and os.path.normcase(table) in written:
            continue  # a saved table's sidecar, written with it
        elif parts[0] == "results" and len(parts) >= 2:
            out[f"result:{parts[1]}"] = ("result", os.path.join(root, "results", parts[1]))
        else:
            out["file:" + "/".join(parts)] = ("file", real)
    return out


# ---- names between cells -----------------------------------------------------------------------------------------
def _python(code):
    """The cell as Python: IPython's magics and shell lines (%time, !ls) turned into the calls IPython makes."""
    try:
        from IPython.core.inputtransformer2 import TransformerManager
        return TransformerManager().transform_cell(code)
    except Exception:
        return "\n".join("" if ln.lstrip().startswith(("%", "!")) else ln for ln in code.splitlines())


def names(code):
    """(the names a cell reads, the names it assigns, the names it may change, the names it imports). Assigning to
    an attribute or an item of a name (df["x"] = ...) assigns it; a statement that is only a method call on a name
    (rows.append(x), df.drop(columns=c, inplace=True)) may change it, while a method called inside an expression
    (print(df.head())) reads it. Code that does not parse gives none."""
    try:
        tree = ast.parse(_python(code))
    except SyntaxError:
        return set(), set(), set(), set()
    reads, assigned, changed, imports = set(), set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (assigned if isinstance(node.ctx, (ast.Store, ast.Del)) else reads).add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
            assigned |= _base(node)
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Attribute)):
            changed |= _base(node.value.func.value)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            assigned |= set(node.names)
        if isinstance(node, ast.Call) and _magic_code(node):  # %time x = ...: the code the magic runs
            r, a, c, i = names(_magic_code(node))
            reads, assigned, changed, imports = reads | r, assigned | a, changed | c, imports | i
    return reads, assigned - imports, changed - imports, imports


class _FirstReads(ast.NodeVisitor):
    """The names a cell reads before it binds them itself, in the order Python runs it: an assignment's value before
    its targets, a loop's iterable before its target, a comprehension's iterables before its element, a function's
    body with its parameters bound."""

    def __init__(self, bound=()):
        self.bound, self.reads = set(bound), set()

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            if node.id not in self.bound:
                self.reads.add(node.id)
        else:
            self.bound.add(node.id)

    def _in_order(self, *parts):
        for part in parts:
            for node in part if isinstance(part, list) else [part]:
                if node is not None:
                    self.visit(node)

    def visit_Assign(self, node):
        self._in_order(node.value, node.targets)

    def visit_AnnAssign(self, node):
        self._in_order(node.value, node.annotation, node.target)

    def visit_AugAssign(self, node):  # x += 1 reads x
        self._in_order(node.value)
        if isinstance(node.target, ast.Name) and node.target.id not in self.bound:
            self.reads.add(node.target.id)
        self._in_order(node.target)

    def visit_NamedExpr(self, node):
        self._in_order(node.value, node.target)

    def visit_For(self, node):
        self._in_order(node.iter, node.target, node.body, node.orelse)

    visit_AsyncFor = visit_For

    def _comprehension(self, node, *element):
        for g in node.generators:
            self._in_order(g.iter, g.target, g.ifs)
        self._in_order(*element)

    def visit_ListComp(self, node):
        self._comprehension(node, node.elt)

    visit_SetComp = visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self._comprehension(node, node.key, node.value)

    def _body(self, args, body):
        a = args
        params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs} | {x.arg for x in (a.vararg, a.kwarg) if x}
        inner = _FirstReads(self.bound | params)
        inner._in_order(body)
        self.reads |= inner.reads

    def visit_FunctionDef(self, node):
        self._in_order(node.decorator_list, node.args.defaults, node.args.kw_defaults)
        self._body(node.args, node.body)
        self.bound.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self._in_order(node.args.defaults, node.args.kw_defaults)
        self._body(node.args, node.body)

    def visit_ClassDef(self, node):
        self._in_order(node.decorator_list, node.bases, node.keywords, node.body)
        self.bound.add(node.name)

    def visit_Import(self, node):
        self.bound |= {(a.asname or a.name).split(".")[0] for a in node.names}

    visit_ImportFrom = visit_Import

    def visit_ExceptHandler(self, node):
        self._in_order(node.type)
        if node.name:
            self.bound.add(node.name)
        self._in_order(node.body)

    def visit_Call(self, node):
        code = _magic_code(node)  # %time x = ...: the code the magic runs, in its place
        if code is not None:
            try:
                self._in_order(ast.parse(code).body)
            except SyntaxError:
                pass
        self.generic_visit(node)


def first_reads(code):
    """The names a cell reads that earlier cells gave it: those it reads before it binds them itself (a figure cell
    that assigns fig and then draws on it does not read the fig of the cell before). Code that does not parse gives
    none."""
    try:
        tree = ast.parse(_python(code))
    except SyntaxError:
        return set()
    v = _FirstReads()
    v.visit(tree)
    return v.reads


CODE_MAGICS = ("time", "timeit", "prun", "capture", "debug")  # magics whose argument or cell is Python code


def _magic_code(call):
    """The Python code of a magic that runs it (get_ipython().run_line_magic('time', code), or run_cell_magic's
    cell), or None."""
    f = call.func
    if not (isinstance(f, ast.Attribute) and f.attr in ("run_line_magic", "run_cell_magic") and call.args
            and isinstance(call.args[0], ast.Constant) and call.args[0].value in CODE_MAGICS):
        return None
    k = 1 if f.attr == "run_line_magic" else 2
    arg = call.args[k] if len(call.args) > k else None
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None


def _base(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return {node.id} if isinstance(node, ast.Name) else set()


class Kernel:
    """What the names of one kernel stand for: the objects behind each name the cells bound. Forgotten when the
    kernel restarts, as its variables are."""

    def __init__(self, modules=("pt", "os")):
        self.behind = {}
        self.modules = set(modules)

    def inputs(self, code, uses_from_files):
        """A cell's inputs: the objects of the files it read, then those behind the names earlier cells gave it, each
        once."""
        reads = first_reads(code)
        behind = sorted({o for n in reads if n not in self.modules for o in self.behind.get(n, ())})
        return list(dict.fromkeys(list(uses_from_files) + behind))

    def bind(self, code, uses, made, run_id=None):
        """After a cell: the names it assigned or changed stand for its inputs, for what it made and for the cell
        itself (its run, so a chain through them has the cell as a step)."""
        _, assigned, changed, imports = names(code)
        for n in imports:
            self.behind.pop(n, None)
            self.modules.add(n)
        self.modules -= assigned
        for n in assigned | (changed - self.modules):  # a name read and changed keeps what it had: it is in uses
            self.behind[n] = set(uses) | set(made) | ({run_id} if run_id else set())

    def cell(self, code, uses_from_files, made, run_id=None):
        """inputs, then bind: a cell's inputs, with its names bound. Returns the inputs."""
        uses = self.inputs(code, uses_from_files)
        self.bind(code, uses, made, run_id)
        return uses


# ---- chains --------------------------------------------------------------------------------------------------------
def _step_of(oid, objs, runs):
    o = objs.get(oid)
    if o is None:
        return ("run", oid) if oid in runs else None
    if o["kind"] == "block_result":
        return ("block", oid)
    if o.get("run_id") and o["run_id"] in runs:
        return ("run", o["run_id"])
    return ("base", oid) if o.get("run_id") is None else None


def chain(st, ids):
    """The steps that made these objects (or runs), from the base: [{step, by, run_id, what, uses, makes}], each
    step once and after the steps it uses. by: 'pertura' for the base, a module, a block or another of Pertura's
    jobs, 'agent' for a notebook cell (the agent's own code). makes: the step's objects that a later step uses or
    that were asked for."""
    objs, runs = objects(st), st.get("runs", {})
    calls = st.get("block_calls", {})
    made_in = {}
    for oid, o in objs.items():
        if o.get("run_id") and o["kind"] != "block_result":
            made_in.setdefault(o["run_id"], []).append(oid)
    steps, seen = [], set()
    failed = {r for r, x in runs.items() if not x.get("job_id") and x.get("state") in ("error", "timeout")}

    def through(uses, past=()):
        """Uses with each failed cell replaced by what it used: a failed cell is no step, what it read still is."""
        out = []
        for u in uses:
            if u in failed and u not in past:
                out += through(runs[u].get("uses") or [], past + (u,))
            elif u not in failed:
                out.append(u)
        return list(dict.fromkeys(out))

    def deps(key):
        kind, ident = key
        if kind in ("base", "block"):
            return list(objs[ident].get("uses") or [])
        own = list(runs.get(ident, {}).get("uses") or [])
        if not runs.get(ident, {}).get("job_id"):  # a cell's own block calls come before what it made
            own += [cid for cid, c in calls.items() if c.get("run_id") == ident]
        return through(own)

    def visit(key):
        if key is None or key in seen:
            return
        seen.add(key)
        uses = deps(key)
        for u in uses:
            visit(_step_of(u, objs, runs))
        if key[0] != "run" or key[1] not in failed:  # a failed cell asked for gives what it used, not itself
            steps.append((key, uses))

    for oid in ids:
        visit(_step_of(oid, objs, runs))
    wanted = set(ids) | {u for _, uses in steps for u in uses}
    out = []
    for (kind, ident), uses in steps:
        if kind == "base":
            out.append({"step": ident, "by": "pertura", "run_id": None, "what": _base_words(ident), "uses": uses,
                        "makes": [ident]})
        elif kind == "block":
            c = calls.get(ident, {})
            out.append({"step": ident, "by": "pertura", "run_id": c.get("run_id"), "what": _block_words(c),
                        "uses": uses, "makes": [ident]})
        else:
            r = runs.get(ident, {})
            makes = [o for o in made_in.get(ident, []) if o in wanted]
            out.append({"step": ident, "by": "pertura" if r.get("job_id") else "agent", "run_id": ident,
                        "what": r.get("what") or "", "uses": [u for u in uses if not (u in calls and
                                                                                     calls[u].get("run_id") == ident)],
                        "makes": makes})
    return out


def _base_words(oid):
    return {"deposit": "the deposit", "design": "the design the user confirmed",
            "screen": "the screen object, built by Pertura from the deposit and the confirmed design"}.get(oid, oid)


def _block_words(call):
    choices = ", ".join(f"{k} {v}" for k, v in (call.get("choices") or {}).items())
    cells = call.get("cells") or "every cell"
    what = f"pt.{call.get('block')}" + (f" ({choices})" if choices else "") + f" on {cells}"
    if call.get("check"):
        what += f", the {call['check']} check of {call.get('check_of')}"
    return what


def starts(st, ids):
    """What a chain starts from for these IDs: a finding's runs and block call, a result's (a dossier's) main run and
    its lead findings', a run, or an object."""
    out = []
    for i in ids:
        d = st.get("dossiers", {}).get(i)
        r = st.get("results", {}).get(i)
        f = st.get("findings", {}).get(i)
        if r is not None:
            out += [r["figure_run"]] + starts(st, r.get("finding_ids") or [])
        elif d is not None:
            out += [d["main_run"]] + starts(st, d.get("lead_findings") or [])
        elif f is not None:
            out += list(f.get("run_ids") or []) + ([f["block_call"]] if f.get("block_call") else [])
        else:
            out.append(i)
    return list(dict.fromkeys(out))
