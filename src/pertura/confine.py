"""Confinement of agent code (docs/adr/0009-confine-agent-code-with-audit-hooks.md; architecture v4, C7).

Agent-written code runs in the notebook's kernel and in the sandbox child (pertura_research.retired.own_analysis), as the server's
user. `install` adds a Python audit hook (PEP 578) that denies, with a PermissionError saying why, and logs:
- reading a file or listing a folder outside the read allowlist;
- writing, renaming, removing or creating outside the write allowlist;
- starting a program (subprocess, os.system, exec, spawn);
- connecting a socket to a network address.

The read allowlist always holds the Python installation, the system's library and configuration folders, the
user's cache and configuration folders, and Pertura's package without the recipes' grade files (computed on
screens' hidden checks). Callers add the folders the code may see (the view's folder, the working folder). Writes
go only to the given folders.

h5py opens files below Python, where the hook cannot see, so `h5py.File` is wrapped to apply the read allowlist
(anndata.read_h5ad goes through it). File access below Python by other means (h5py's low-level API, pyarrow,
ctypes) is not seen: the hook is a guard for honest code, not a sandbox against hostile code (PEP 578 says so too),
and the evaluation's collector flags such code in the notebook.

The same hook can also trace (docs/adr/0027, D3): given a trace file, it writes one line per file opened under the
caller's own folders (not the Python installation's), "R" or "W" and the path, so the tool that ran a cell knows
which files the cell read and wrote. Tracing never denies anything.

Two modes (docs/adr/0032, D2):
- **exam:** everything above, for the optional exam and for comparisons whose answers may sit on the server;
- **normal:** the analysis notebook's default. The code reads any file the server lets it read, and every file it
  opens outside the Python installation and the system's folders is traced. It may start programs (R, command-line
  tools, processes). It writes only to the write allowlist, so the record, the design and the screen object change
  only through Pertura's tools. It connects to network addresses only when the caller allows the network. A
  program's own writes are not seen by the hook; the notebook finds them by comparing its folder before and after a
  cell.

An audit hook cannot be removed once added.
"""
import os
import sys
import threading

DENIED_NAMES = ("grade.json", "reproduction.json")  # the recipes' grades: computed on screens' hidden checks
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _real(path):
    return os.path.realpath(os.path.abspath(os.fsdecode(path)))


def _norm(path):
    return os.path.normcase(_real(path))


def _under(path, roots):
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def default_read_roots():
    """What any Python code needs to read: the installation, system folders and Pertura's package. Not the user's
    home folders: caches and settings are pointed inside the working folder (pertura.notebook), since a writable
    ~/.ipython would let code plant start-up files that run unconfined, and ~/.local holds other kernels'
    connection files (the harness review, 2026-10-09)."""
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Pertura's src folder, not the release's root
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, getattr(sys, "base_exec_prefix", sys.prefix), src}
    # /proc/self is resolved to /proc/<pid> and its links (root, cwd, fd, exe) to their targets before matching
    roots |= {"/usr", "/lib", "/lib64", "/lib32", "/bin", "/etc", "/proc/self", "/sys", "/dev/null", "/dev/urandom",
              "/dev/random", "/opt/conda"}
    roots |= {f"/proc/{x}" for x in ("meminfo", "cpuinfo", "stat", "loadavg", "uptime", "version", "sys")}
    try:  # the offline gene-set databases (pertura.gene_sets), which the agent is told to use
        from pertura import gene_sets
        roots.add(gene_sets.folder())
    except Exception:
        pass
    if os.name == "nt":
        roots |= {os.environ.get("SYSTEMROOT", r"C:\Windows")}
    return roots


def default_write_roots():
    return {"/dev/null"}


class Policy:
    """Which paths agent code may read and write. Immutable once made."""

    def __init__(self, read=(), write=(), defaults=True):
        r = set(read) | (default_read_roots() if defaults else set())
        w = set(write) | (default_write_roots() if defaults else set())
        self.write = tuple(sorted({_norm(p) for p in w if p}))
        self.read = tuple(sorted({_norm(p) for p in r if p} | set(self.write)))
        self.package = _norm(os.path.dirname(os.path.abspath(__file__)))

    def readable(self, path):
        try:
            p = _norm(path)
        except (OSError, ValueError):  # a path that cannot be resolved is not allowed
            return False
        return self.readable_normed(p)

    def readable_normed(self, p):
        if not _under(p, self.read):
            return False
        return not (_under(p, (self.package,)) and os.path.basename(p) in DENIED_NAMES)

    def writable(self, path):
        try:
            return _under(_norm(path), self.write)
        except (OSError, ValueError):
            return False


def _is_write(mode, flags):
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & WRITE_FLAGS)


MODES = ("exam", "normal")


def install(read=(), write=(), log=None, label="agent code", defaults=True, trace=None, untraced=(), mode="exam",
            network=False):
    """Install the audit hook. read and write: extra folders (or files) allowed; log: a path for the denial log,
    opened now, before the hook, so the confined code cannot open it; trace: a path for the trace of the files
    opened under read and write (not under untraced), opened now too; mode: exam or normal (the module's docstring);
    network: in normal mode, whether the code may connect to network addresses. Returns the policy."""
    if mode not in MODES:
        raise ValueError(f"mode is one of {MODES}, not {mode!r}")
    normal = mode == "normal"
    policy = Policy(read, write, defaults)
    sink = open(log, "a", encoding="utf-8", buffering=1) if log else None
    # normal mode traces every file opened outside the installation and the system's folders, wherever it is
    tracer = _tracer(trace, read, write, untraced, everywhere_but=default_read_roots() if normal else None)
    busy = threading.local()
    # the folders the code may write besides its own (the first), named in a refusal: a write it needs has a place
    others = [str(w) for w in list(write)[1:]]

    def deny(event, what):
        msg = ((f"{label} may not {what}: in this notebook, code writes only in its own folder"
                + (f" and in {', '.join(others)}" if others else "")
                + ", and the record, the design and the screen object change only through Pertura's tools "
                "(docs/adr/0032)") if normal else
               f"{label} may not {what}: Pertura confines agent code to the screen's exploration view and its own "
               f"working folder (docs/adr/0009)")
        if sink is not None:
            try:
                sink.write(f"DENIED\t{event}\t{what}\n")
            except Exception:
                pass
        raise PermissionError(msg)

    def check_read(event, path):
        if isinstance(path, int) or path is None:
            return
        try:
            real = _real(path)
        except (OSError, ValueError):
            real = None
        if normal:
            if real is not None:
                tracer("R", real)
            return
        if real is None or not policy.readable_normed(os.path.normcase(real)):
            deny(event, f"read {os.fsdecode(path)}")
        tracer("R", real)

    def check_write(event, path):
        if isinstance(path, int) or path is None:
            return
        if not policy.writable(path):
            deny(event, f"write {os.fsdecode(path)}")
        tracer("W", path)

    def hook(event, args):
        if getattr(busy, "on", False):
            return
        busy.on = True
        try:
            if event == "open":
                path, how, flags = (tuple(args) + (None, None, None))[:3]
                if isinstance(path, (str, bytes, os.PathLike)):
                    (check_write if _is_write(how, flags) else check_read)(event, path)
            elif event in ("os.listdir", "os.scandir", "os.chdir"):
                if not normal:  # a listing reads no file: normal mode neither denies nor traces it
                    path = args[0] if args else "."
                    check_read(event, "." if path is None else path)
            elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.truncate", "os.chmod", "os.utime",
                           "shutil.rmtree"):
                if args:
                    check_write(event, args[0])
            elif event in ("os.rename", "os.link", "os.symlink", "shutil.copyfile", "shutil.copytree",
                           "shutil.move"):
                if len(args) >= 2:
                    check_write(event, args[1])
                    if event in ("os.rename", "shutil.move"):
                        check_write(event, args[0])
                    else:
                        check_read(event, args[0])
            elif event == "sqlite3.connect":  # any database: ATTACH inside a connection is not audited
                if not normal:
                    deny(event, f"open a database ({str(args[0] if args else '')[:120]})")
            elif event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile",
                           "pty.spawn", "os.fork", "os.forkpty", "_winapi.CreateProcess"):
                if not normal:
                    deny(event, f"start a process ({str(args[0] if args else '')[:120]})")
            elif event in ("gc.get_objects", "gc.get_referrers", "gc.get_referents"):
                if not normal:
                    deny(event, "walk the interpreter's objects")
            elif event in ("socket.connect", "socket.bind", "socket.sendto"):
                addr = args[1] if len(args) > 1 else None
                if isinstance(addr, tuple):
                    if not (normal and network):
                        deny(event, f"use the network ({addr[0]!r})" + (
                            "; the project's configuration does not allow it (resources: network)" if normal else ""))
                elif isinstance(addr, (str, bytes)) and addr and not normal:
                    check_read(event, addr)
            elif event == "ctypes.dlopen" and sink is not None:
                try:
                    sink.write(f"NOTED\t{event}\t{args[0] if args else ''}\n")
                except Exception:
                    pass
        finally:
            busy.on = False

    sys.addaudithook(hook)
    _wrap_h5py(policy, deny, tracer, reads_open=normal)
    if not normal:
        _block_fork_exec(deny)
    return policy


def _tracer(trace, read, write, untraced, everywhere_but=None):
    """A function (kind, path) that writes the line for a file opened under the traced folders; a no-op without a
    trace file. everywhere_but: trace a file opened anywhere except under these folders (and untraced), rather than
    only under read and write."""
    if not trace:
        return lambda kind, path: None
    out = open(trace, "a", encoding="utf-8", buffering=1)
    roots = tuple(sorted({_norm(p) for p in list(read) + list(write) if p}))
    skip = tuple(sorted({_norm(p) for p in untraced if p}))
    system = tuple(sorted({_norm(p) for p in everywhere_but or () if p}))

    def wanted(p):
        if _under(p, skip):
            return False
        if _under(p, roots):  # the caller's own folders, even where they sit below a system folder
            return True
        return everywhere_but is not None and not _under(p, system)

    def write_line(kind, path):
        """path: resolved for a read (_real), as given for a write; written in its own case, matched case-folded."""
        try:
            real = path if kind == "R" else _real(path)
            if real and wanted(os.path.normcase(real)):
                out.write(f"{kind}\t{real}\n")
        except Exception:  # tracing never stops the code
            pass
    return write_line


def _block_fork_exec(deny):
    """multiprocessing's spawn and fork servers start interpreters through _posixsubprocess.fork_exec, which raises
    no audit event: replace it, so such code fails loudly instead of running unconfined."""
    try:
        import _posixsubprocess
    except ImportError:
        return

    def fork_exec(*args, **kwargs):
        deny("_posixsubprocess.fork_exec", "start a process")

    _posixsubprocess.fork_exec = fork_exec


def _wrap_h5py(policy, deny, tracer=lambda kind, path: None, reads_open=False):
    """h5py opens files in C: apply the allowlists in h5py.File's constructor (writing modes need the write list;
    reading needs the read list unless reads_open) (anndata reads through it). The class itself is kept, so
    isinstance checks on h5py.File still hold."""
    try:
        import h5py
    except Exception:
        return
    cls = h5py.File
    real_init = cls.__init__

    def __init__(self, name, *args, **kwargs):
        if isinstance(name, (str, bytes, os.PathLike)):
            mode = kwargs.get("mode", args[0] if args else None)
            if mode not in (None, "r"):
                if not policy.writable(name):
                    deny("h5py.File", f"write {os.fsdecode(name)}")
                tracer("W", name)
            elif not reads_open and not policy.readable(name):
                deny("h5py.File", f"read {os.fsdecode(name)}")
            else:
                tracer("R", _real(name))
        real_init(self, name, *args, **kwargs)

    __init__.__doc__ = real_init.__doc__
    cls.__init__ = __init__


def kernel_boot(view, notebook_dir, log, home=None, extra_read=(), trace=None, untraced=(), mode="exam", network=False,
                extra_write=()):
    """The code the notebook runs in its kernel before the agent's first cell (pertura.notebook). It drops the
    working folder from the import path and loads this module from its own file, so a module planted in the
    notebook folder cannot stand in for it at a restart. extra_read: further files or folders the code may read
    (a screen object's deposit); trace: the file for the trace of the files the cells open (untraced: folders left
    out of it, such as the kernel's caches); mode and network: as install takes them; extra_write: further folders the
    code may write (the project's cell tables, which the agent's own reader writes)."""
    here = os.path.abspath(__file__)
    vdir = os.path.dirname(os.path.abspath(view))  # the view's own files, not its folder (whatever else sits there)
    reads = ([os.path.abspath(view)] + [os.path.join(vdir, f) for f in ("design.json", "manifest.json")] +
             ([os.path.abspath(home)] if home else []) + [os.path.abspath(x) for x in extra_read])
    return ("import sys as _s, os as _o, importlib.util as _u\n"
            f"_s.path[:] = [p for p in _s.path if p not in ('', '.') and _o.path.abspath(p) != {os.path.abspath(notebook_dir)!r}]\n"
            f"_spec = _u.spec_from_file_location('pertura_confine_boot', {here!r})\n"
            "_m = _u.module_from_spec(_spec)\n_spec.loader.exec_module(_m)\n"
            f"_m.install(read={reads!r}, write={[os.path.abspath(notebook_dir)] + [os.path.abspath(x) for x in extra_write]!r}, "
            f"log={os.path.abspath(log)!r}, "
            f"label='notebook code', trace={os.path.abspath(trace) if trace else None!r}, "
            f"untraced={[os.path.abspath(x) for x in untraced]!r}, mode={mode!r}, network={bool(network)!r})\n"
            "del _s, _o, _u, _spec, _m\n")
