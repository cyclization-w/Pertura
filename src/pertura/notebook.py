"""The agent's notebook (docs/agent_architecture.md, the workspace): a persistent Jupyter kernel on the screen's
exploration view, run through MCP, as CellVoyager and Biomni run code. Variables, imports and loaded data carry
across cells, so a large screen loads once.

- **One kernel per session.** It starts on the first cell, in the session's notebook folder (its working folder),
  with one thread per numerical library and a lower priority (nice 10 where the system has it).
- **What it is given.** `view` (the exploration view's h5ad: the reference cells and each target's visible
  constructs) and `figures` (a folder). Nothing in it names the hidden checks or the held-out constructs.
- **Each cell's outputs** come back to the agent: printed text and errors (each cut to MAX_TEXT characters), the
  cell's result as text, and figures saved as PNG files (their paths). A cell longer than its timeout is
  interrupted; the kernel keeps its state.
- **Limits** (docs/adr/0014). A cell's timeout is at most MAX_TIMEOUT. A cell still busy GRACE seconds after its
  interrupt is ended with its kernel, which restarts (the variables are gone, the record is kept). On Linux the
  kernel lowers its own data limit (RLIMIT_DATA) to memory_gb before anything runs, so a too-large allocation fails
  inside the cell (MemoryError) and the kernel survives.
- **The record.** Every cell and its full outputs are appended to notebook.ipynb (nbformat 4, figures embedded), so
  the user can open the whole exploration in Jupyter.
- **Confinement** (docs/adr/0009, 2026-10-09). The kernel runs as the server's user, so before the first cell it
  installs Pertura's audit hook (pertura.confine): the code may read the view's folder, its own folder and the
  Python installation, and write only its own folder (TMPDIR points inside it); other files, listings, programs and
  the network are denied, and each denial is logged in the session's confinement.log, outside the kernel's reach.
  The hook guards honest code, not hostile code; OS confinement needs an administrator (ADR 0009). That is the exam
  mode, the default here. In normal mode (docs/adr/0032, D2; the analysis server's default) the same hook lets the
  code read any file and start programs, traces what it reads, still keeps its writes to its own folder, and keeps
  the network off unless it is allowed; the files a program writes are found by comparing the folder before and
  after the cell.
- **The files each cell used** (docs/adr/0027, D3). The hook also writes the files the code opens under the view,
  the extra folders and its own folder (not its caches) to files_<notebook>.tsv beside confinement.log, and each
  cell's result gives the files it read and wrote, for the tool that ran it to enter as the cell's inputs and outputs.
Written from Blackboard's KernelSession (blackboard/_kernel.py), with figures, results, interrupts and the record.
"""
import atexit
import base64
import json
import os
import re
import sys
import threading
import time

from pertura import confine

MAX_TEXT = 6000
TIMEOUT = 600
MAX_TIMEOUT = 1800  # docs/adr/0014: a longer cell is split, or run as a job
GRACE = 30  # seconds a cell has to stop after its interrupt before its kernel is restarted
MEMORY_GB = 64  # the kernel's data limit on Linux; PERTURA_KERNEL_MEMORY_GB overrides it
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def memory_limit_code(gb):
    """Kernel code that lowers its own data limit (soft and hard) to gb, never above the limit already set; on a
    system without the resource module, nothing."""
    return ("try:\n    import resource as _r\nexcept ImportError:\n    _r = None\n"
            "if _r is not None:\n"
            f"    _lim = int({float(gb)!r} * 1024 ** 3)\n"
            "    _s, _h = _r.getrlimit(_r.RLIMIT_DATA)\n"
            "    for _x in (_s, _h):\n"
            "        if _x != _r.RLIM_INFINITY:\n"
            "            _lim = min(_lim, _x)\n"
            "    _r.setrlimit(_r.RLIMIT_DATA, (_lim, _lim))\n"
            "    del _s, _h, _lim, _x\n"
            "del _r\n")


# the kernel's save_figure (docs/adr/0021, section 2; docs/adr/0032, D4): pertura.question_modules.figures.save_figure
# into the figures folder; the tool that ran the cell enters what it saved (saved_figures) and shows it to the agent,
# so the figure is closed here rather than shown a second time inline
SAVE_FIGURE_CODE = (
    "def save_figure(fig, name, data=None, caption=None, role=None, strict=False):\n"
    "    \"\"\"Keep a figure in the record: PNG (300 dpi), PDF, and, where given, the plotted data (a DataFrame or "
    "{name: DataFrame}) and its caption; role: overview, detail, evidence or limitation. The layout checks note "
    "issues; strict=True (for a panel or the report) sets the width to a column and refuses a figure that fails "
    "them.\"\"\"\n"
    "    from pertura.question_modules.figures import save_figure as _save_figure\n"
    "    out = _save_figure(fig, name, data, caption, role, folder=figures, strict=strict)\n"
    "    try:\n"
    "        import matplotlib.pyplot as _plt\n"
    "        _plt.close(fig)\n"
    "    except Exception:\n"
    "        pass\n"
    "    return out\n")


# the kernel's cell sets (docs/adr/0024, section 3): save_cells writes into the notebook's cell_sets folder and the
# tool that ran the cell checks and keeps the set (pertura.cell_sets); load_cells reads a kept set
CELL_SETS_CODE = (
    "def save_cells(name, barcodes, labels=None, reason='', calls=False, base=None):\n"
    "    \"\"\"Keep a named set of the screen object's cells for every analysis (blocks, runners, robustness), with "
    "each cell's label where the set changes guide calls. reason: why; calls=True when the set decides guide calls "
    "(it relabels cells, or leaves cells out by their guide evidence: the user confirms it as the calling rule); "
    "base: the kept set it is cut from (a population such as one tissue).\"\"\"\n"
    "    from pertura.cell_sets import write as _write\n"
    "    return _write(os.path.join(os.path.dirname(figures), 'cell_sets'), name, barcodes, labels, reason, calls, "
    "base)\n"
    "def load_cells(name):\n"
    "    \"\"\"A kept cell set's table (barcode, and label where the set gives them).\"\"\"\n"
    "    from pertura.cell_sets import load as _load\n"
    "    return _load(view, name)[0]\n")


def saved_figures(folder):
    """The sidecars save_figure wrote in folder: {path: (its contents' hash, its contents)}."""
    import hashlib
    from pertura.question_modules.figures import SIDECAR
    out = {}
    if not os.path.isdir(folder):
        return out
    for f in sorted(os.listdir(folder)):
        if f.endswith(SIDECAR):
            p = os.path.join(folder, f)
            try:
                with open(p, "rb") as fh:
                    raw = fh.read()
                out[p] = (hashlib.sha256(raw).hexdigest(), json.loads(raw))
            except (OSError, ValueError):
                continue
    return out


class NotebookError(Exception):
    pass


def _cut(text, n=MAX_TEXT):
    text = ANSI.sub("", text or "")
    if len(text) <= n:
        return text
    return text[: n // 2] + f"\n... [{len(text) - n} characters cut; the notebook has them all] ...\n" + text[-n // 2:]


SHUTDOWN_WAIT = 5  # seconds the shutdown at exit waits for a running cell's turn (a hung cell must not block it)


def _one_at_a_time(method):
    """One call at a time on a notebook (ADR 0016 §6's second unknown, measured 2026-10-11): kernels driven from
    several threads keep their outputs apart, one kernel per thread, but two calls on one kernel's channels at once
    interleave and break its messages (tests/test_notebook_concurrency.py)."""
    import functools

    @functools.wraps(method)
    def wrapper(self, *a, **k):
        with self._lock:
            return method(self, *a, **k)
    return wrapper


class Notebook:
    def __init__(self, folder, view, timeout=TIMEOUT, threads=1, memory_gb=None, grace=GRACE, extra_read=(),
                 mode="exam", network=False, max_timeout=MAX_TIMEOUT, max_text=MAX_TEXT, extra_write=()):
        if mode not in confine.MODES:
            raise NotebookError(f"mode is one of {confine.MODES}, not {mode!r}")
        self._lock = threading.RLock()
        self.extra_read = [os.path.abspath(x) for x in extra_read]  # further paths the code may read
        self.extra_write = [os.path.abspath(x) for x in extra_write]  # further folders it may write
        self.mode, self.network = mode, bool(network)
        self.max_timeout, self.max_text = int(max_timeout), int(max_text)  # a caller may change them between cells
        self._at_exit = False
        self.dir = os.path.abspath(folder)
        self.memory_gb = float(memory_gb or os.environ.get("PERTURA_KERNEL_MEMORY_GB") or MEMORY_GB)
        self.grace = grace
        self.view = os.path.abspath(view)
        self.figures = os.path.join(self.dir, "figures")
        self.path = os.path.join(self.dir, "notebook.ipynb")
        self.tmp = os.path.join(self.dir, "tmp")
        self.log = os.path.join(os.path.dirname(self.dir), "confinement.log")  # outside the kernel's folder
        self.trace = os.path.join(os.path.dirname(self.dir), f"files_{os.path.basename(self.dir)}.tsv")
        self.home = os.path.join(os.path.dirname(self.dir), "kernel_home")  # read-only for the kernel
        self.timeout = timeout
        self.threads = int(threads)
        self.km = self.kc = None
        self.count = 0
        self.starts = 0  # kernels started: a new one has none of the earlier variables
        self.cells_in_kernel = 0  # cells run in the current kernel
        self.cells = []
        if os.path.exists(self.path):  # a session reopened: keep the record, the kernel's state is gone
            with open(self.path, encoding="utf-8") as fh:
                self.cells = json.load(fh).get("cells", [])
            self.count = max([c.get("execution_count") or 0 for c in self.cells], default=0)

    # ---- the kernel
    def alive(self):
        return self.km is not None and self.km.is_alive()

    @_one_at_a_time
    def start(self):
        if self.alive():
            return
        try:
            from jupyter_client.manager import KernelManager
        except ImportError:
            raise NotebookError("the notebook needs jupyter_client and ipykernel in the server's environment")
        os.makedirs(self.figures, exist_ok=True)
        os.makedirs(self.tmp, exist_ok=True)
        env = {**os.environ, "OMP_NUM_THREADS": str(self.threads), "OPENBLAS_NUM_THREADS": str(self.threads),
               "MKL_NUM_THREADS": str(self.threads), "NUMBA_NUM_THREADS": str(self.threads),
               "MPLBACKEND": "module://matplotlib_inline.backend_inline", "PERTURA_VIEW": self.view,
               "TMPDIR": self.tmp, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        # never the user's home (docs/adr/0009; the harness review, 2026-10-09). Settings that can run code at
        # start-up (IPython's profile and start-up files, Jupyter's configuration) live beside the notebook folder,
        # readable but not writable by the kernel, so code cannot plant something that runs unconfined at the next
        # restart. Caches, and matplotlib's settings (they run no code: the backend is set above), live inside the
        # notebook folder, where the kernel may write.
        env.update({"IPYTHONDIR": os.path.join(self.home, "ipython"), "XDG_CONFIG_HOME": os.path.join(self.home, "config"),
                    "JUPYTER_CONFIG_DIR": os.path.join(self.home, "jupyter"),
                    "JUPYTER_DATA_DIR": os.path.join(self.home, "jupyter_data"),
                    "MPLCONFIGDIR": os.path.join(self.tmp, "matplotlib"), "XDG_CACHE_HOME": os.path.join(self.tmp, "cache"),
                    "NUMBA_CACHE_DIR": os.path.join(self.tmp, "numba")})
        for d in ("ipython", "config", "jupyter", "jupyter_data"):
            os.makedirs(os.path.join(self.home, d), exist_ok=True)
        # pertura importable from the kernel (gene_sets): its absolute path, since the server's may be relative
        src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = os.pathsep.join([src] + [x for x in env.get("PYTHONPATH", "").split(os.pathsep) if x])
        # on a shared Linux server, local sockets instead of TCP ports other users could connect to
        self.km = KernelManager(kernel_name="python3", transport="ipc" if os.name == "posix" else "tcp")
        if os.name == "posix":
            self.km.ip = os.path.join(self._socket_dir(), "kernel")
        self.km.start_kernel(env=env, cwd=self.dir)
        pid = getattr(getattr(self.km, "provisioner", None), "pid", None) or getattr(self.km, "kernel", None)
        pid = getattr(pid, "pid", pid)
        if isinstance(pid, int) and hasattr(os, "setpriority"):
            try:
                os.setpriority(os.PRIO_PROCESS, pid, 10)
            except OSError:
                pass
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)
        self.starts += 1
        self.cells_in_kernel = 0
        if not self._at_exit:  # once per notebook, however often its kernel restarts
            atexit.register(self.shutdown, wait=SHUTDOWN_WAIT)
            self._at_exit = True
        # the confinement first, so nothing the boot imports afterwards (matplotlib's backend) comes unconfined
        boot = (confine.kernel_boot(self.view, self.dir, self.log, self.home, self.extra_read, trace=self.trace,
                                    untraced=[self.tmp, self.home], mode=self.mode, network=self.network,
                                    extra_write=self.extra_write) +
                memory_limit_code(self.memory_gb) +
                f"import os\nview = {self.view!r}\nfigures = {self.figures!r}\nos.chdir({self.dir!r})\n"
                "try:\n    get_ipython().run_line_magic('matplotlib', 'inline')\nexcept Exception:\n    pass\n"
                + SAVE_FIGURE_CODE + CELL_SETS_CODE)
        out = self._execute(boot, 60)
        if out["status"] != "ok":
            raise NotebookError("the notebook's kernel did not start: " + out["error"] + out["traceback"])

    def _socket_dir(self):
        """Where the kernel's sockets go: the notebook folder when its path is short enough for a Unix socket
        (about 107 characters), otherwise a private temporary folder (PERTURA_RUNTIME if set), removed at
        shutdown."""
        if len(os.path.join(self.dir, "kernel-65535")) < 100:
            return self.dir
        import tempfile
        base = os.environ.get("PERTURA_RUNTIME") or None
        if base:
            os.makedirs(base, exist_ok=True)
        self._tmp = tempfile.mkdtemp(prefix="pk", dir=base)
        return self._tmp

    def shutdown(self, wait=None):
        """End the kernel, after the running cell (if any) has had its turn. wait: seconds to wait for that turn,
        None for as long as it takes; only the shutdown at exit sets one, so a hung cell cannot block the exit, and
        a session closing never kills a cell that is still running (the reviewer, 2026-10-11)."""
        got = self._lock.acquire() if wait is None else self._lock.acquire(timeout=wait)
        try:
            self._shutdown()
        finally:
            if got:
                self._lock.release()

    def _shutdown(self):
        if self.kc is not None:
            try:
                self.kc.stop_channels()
            except Exception:
                pass
        if self.km is not None:
            try:
                if self.km.is_alive():
                    self.km.shutdown_kernel(now=True)
            except Exception:
                pass
        self.km = self.kc = None
        if getattr(self, "_tmp", None):
            import shutil
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    @_one_at_a_time
    def restart(self):
        self.shutdown()
        self.start()
        return {"restarted": True, "note": "the kernel's variables are gone; the notebook record is kept"}

    # ---- cells
    @_one_at_a_time
    def run(self, code, timeout=None):
        """Run one cell. Returns what the agent sees; the full outputs go to the notebook record. The first cell in a
        new kernel, where the record holds earlier cells, says their names are gone."""
        self.start()
        new_kernel = self.count > 0 and self.cells_in_kernel == 0
        t0 = time.time()
        asked = int(timeout or self.timeout)
        limit = max(1, min(asked, self.max_timeout))
        mark = os.path.getsize(self.trace) if os.path.exists(self.trace) else 0
        before = self._folder_state() if self.mode == "normal" else None
        out = self._execute(code, limit)
        files = self.files_since(mark)
        if before is not None:  # a program's writes, which the hook does not see
            seen = {os.path.normcase(os.path.realpath(p)) for p in files["written"]}
            by_programs = [p for p in self._changed_since(before)
                           if os.path.normcase(os.path.realpath(p)) not in seen]
            files["written"] += by_programs
            if by_programs:
                files["written_by_programs"] = by_programs
        stuck = out.pop("_stuck", False)
        self.count += 1
        self.cells_in_kernel += 1
        figs = []
        for k, png in enumerate(out.pop("_pngs")):
            p = os.path.join(self.figures, f"cell{self.count:03d}_{k + 1}.png")
            with open(p, "wb") as fh:
                fh.write(base64.b64decode(png))
            figs.append(p)
        self.cells.append({"cell_type": "code", "execution_count": self.count, "metadata": {},
                           "source": code, "outputs": out.pop("_outputs")})
        self._write()
        restarted, restart_error = False, None
        if stuck:  # still busy after its interrupt: end it with its kernel, after the record (docs/adr/0014)
            self.shutdown()
            try:
                self.start()
                restarted = True
            except Exception as e:  # the next cell tries again
                restart_error = f"{type(e).__name__}: {e}"[:500]
        res = {"cell": self.count, "status": out["status"], "seconds": round(time.time() - t0, 1),
               "limit_seconds": limit, "stdout": _cut(out["stdout"], self.max_text),
               "stderr": _cut(out["stderr"], self.max_text), "result": _cut(out["result"], self.max_text),
               "error": out["error"], "traceback": _cut(out["traceback"], 3000), "figures": figs, "files": files}
        notes = []
        if new_kernel:  # a server's notebooks end with it, and a client without a window starts a server each turn
            notes.append("this cell ran in a new kernel: the names the earlier cells defined are gone (the notebook "
                         "record and this folder's files are kept)")
        if asked > self.max_timeout:
            notes.append(f"the limit asked for ({asked} s) was cut to {self.max_timeout} s; split longer work into "
                         "cells, or run it as a job")
        if stuck:
            res["kernel_restarted"] = restarted
            notes.append(f"the cell did not stop within {self.grace} s of its interrupt, so its kernel was ended: "
                         "its variables are gone (the notebook record is kept); load the view again"
                         + ("" if restarted else f"; the new kernel did not start ({restart_error}), the next cell "
                                                 "tries again"))
        if notes:
            res["note"] = "; ".join(notes)
        return res

    def _folder_state(self):
        """{path: (size, mtime)} of the files in the notebook's folder, its temporary folder left out."""
        out, todo = {}, [self.dir]
        tmp = os.path.normcase(os.path.abspath(self.tmp))
        while todo:
            d = todo.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if os.path.normcase(os.path.abspath(e.path)) != tmp:
                            todo.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        out[os.path.abspath(e.path)] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    continue
        return out

    def _changed_since(self, before):
        """The files of the folder that are new or changed since before (_folder_state), in path order."""
        now = self._folder_state()
        return sorted(p for p, s in now.items() if before.get(p) != s and os.path.basename(p) != "notebook.ipynb")

    def files_since(self, mark):
        """The files the code read and wrote since the trace held mark bytes: {read: [...], written: [...]}, each
        path once, in the order first opened."""
        read, written = {}, {}
        if os.path.exists(self.trace):
            with open(self.trace, "rb") as fh:
                fh.seek(mark)
                for line in fh.read().decode("utf-8", "replace").splitlines():
                    kind, _, path = line.partition("\t")
                    if path:
                        (read if kind == "R" else written).setdefault(path, None)
        return {"read": list(read), "written": list(written)}

    def _execute(self, code, timeout):
        msg_id = self.kc.execute(code)
        res = {"status": "ok", "stdout": "", "stderr": "", "result": "", "error": "", "traceback": "",
               "_pngs": [], "_outputs": []}
        deadline, interrupted = time.time() + timeout, False
        while True:
            if not interrupted and time.time() > deadline:
                self.km.interrupt_kernel()
                interrupted, deadline = True, time.time() + self.grace
                res["status"] = "timeout"
            elif interrupted and time.time() > deadline:
                res["_stuck"] = True  # the cell ignored its interrupt; run() restarts the kernel
                break
            try:
                msg = self.kc.get_iopub_msg(timeout=1)
            except Exception:
                if not self.km.is_alive():
                    res.update(status="error", error="the kernel died (perhaps out of memory); restart it")
                    break
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            kind, c = msg["header"]["msg_type"], msg["content"]
            if kind == "stream":
                res["stdout" if c.get("name") == "stdout" else "stderr"] += c.get("text", "")
                res["_outputs"].append({"output_type": "stream", "name": c.get("name", "stdout"),
                                        "text": c.get("text", "")})
            elif kind in ("execute_result", "display_data"):
                data = c.get("data", {})
                if "image/png" in data:
                    res["_pngs"].append(data["image/png"])
                elif "text/plain" in data:
                    res["result"] += data["text/plain"] + "\n"
                o = {"output_type": kind, "data": {k: v for k, v in data.items()
                                                   if k in ("text/plain", "image/png", "text/html")},
                     "metadata": {}}
                if kind == "execute_result":
                    o["execution_count"] = self.count + 1
                res["_outputs"].append(o)
            elif kind == "error":
                if res["status"] != "timeout":
                    res["status"] = "error"
                res["error"] = f"{c.get('ename', '')}: {c.get('evalue', '')}"
                res["traceback"] = "\n".join(c.get("traceback", []))
                res["_outputs"].append({"output_type": "error", "ename": c.get("ename", ""),
                                        "evalue": c.get("evalue", ""), "traceback": c.get("traceback", [])})
            elif kind == "status" and c.get("execution_state") == "idle":
                break
        return res

    def _write(self):
        nb = {"nbformat": 4, "nbformat_minor": 5,
              "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                           "language_info": {"name": "python"}, "pertura": {"view": self.view}},
              "cells": self.cells}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(nb, fh, indent=1)
        os.replace(tmp, self.path)

    def info(self):  # reads only, so a status call never waits behind a running cell
        return {"notebook": self.path, "cells": self.count, "kernel_alive": self.alive(), "view": self.view,
                "figures": self.figures, "mode": self.mode, "confined": self.mode == "exam",
                "network": self.network, "threads": self.threads, "max_cell_seconds": self.max_timeout,
                "output_chars": self.max_text, "memory_limit_gb": self.memory_gb if os.name == "posix" else None}


if __name__ == "__main__":  # a quick manual check: python -m pertura.notebook <folder> <view.h5ad>
    nb = Notebook(sys.argv[1], sys.argv[2])
    print(nb.run("import anndata as ad\na = ad.read_h5ad(view)\na"))
    nb.shutdown()
