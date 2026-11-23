"""The analysis server's resources for its notebooks and cells (docs/adr/0032, D3), from the workspace configuration's
"resources" section rather than from the code.

When each value is read:
- a kernel's threads, memory, mode and network when the kernel starts;
- a cell's timeouts, output cut and figures shown at each cell;
- the core set, the thread limit and the environment when the server starts (pertura.limits, and every process the
  server starts).

Editing the configuration file therefore changes the next kernel and the next cell without restarting the server.
Light: it imports nothing heavy, so the server can read it before numpy starts."""
import json
import os
import re

DEFAULTS = {
    "main_threads": 8,  # the main notebook's kernel
    "worker_threads": 2,  # each worker's kernel
    "max_workers": 6,  # workers' notebooks open at once: the design's five kinds of conclusion, and a probe (P6)
    "checks_at_once": 3,  # findings' checks jobs running together; each loads the whole screen object
    "memory_gb": 64,  # each kernel's data limit (Linux)
    "cell_timeout": 600,  # a cell's time when run_code names none, in seconds
    "cell_timeout_max": 3600,  # the longest a cell may run; longer work runs as a job
    "job_hours": 4,  # a module, checks or block job is stopped at this time limit, in hours
    "output_chars": 12000,  # printed output kept in a cell's reply; the notebook keeps all of it
    "images_per_cell": 4,  # figures a cell's reply shows the agent as images
    "mode": "normal",  # normal, or exam: the confinement of docs/adr/0009
    "network": False,  # whether notebook code may connect to network addresses
}
# pertura.limits reads these when the server starts; environment ({name: value}) goes into the server's environment
# then, so its kernels and jobs inherit it (NUMPY_MADVISE_HUGEPAGE=0 where memory compaction stalls NumPy's huge pages)
AT_SERVER_START = ("cores", "thread_limit", "environment")
VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MODES = ("normal", "exam")
THREAD_LIMIT_VARIABLE = "PERTURA_THREAD_LIMIT"


class ResourceError(ValueError):
    pass


def _section(cfg):
    """The configuration's resources: read again from its file where the configuration names one (config_path), so
    an edit counts at once; else the section the configuration was given with."""
    path = (cfg or {}).get("config_path")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return dict((json.load(fh) or {}).get("resources") or {})
    return dict((cfg or {}).get("resources") or {})


def load(cfg):
    """The resources in force: DEFAULTS, overridden by the configuration's resources section. A value of the wrong
    kind is refused with what it should be; a name not listed here is refused too, so a misspelt name is never
    silently ignored."""
    given = _section(cfg)
    unknown = sorted(set(given) - set(DEFAULTS) - set(AT_SERVER_START))
    if unknown:
        raise ResourceError(f"unknown resources {unknown}; the resources are {sorted(DEFAULTS) + list(AT_SERVER_START)}")
    out = dict(DEFAULTS)
    for k, v in given.items():
        if k in AT_SERVER_START:
            out[k] = v
        elif k == "mode":
            if v not in MODES:
                raise ResourceError(f"mode is one of {MODES}, not {v!r}")
            out[k] = v
        elif k == "network":
            if not isinstance(v, bool):
                raise ResourceError(f"network is true or false, not {v!r}")
            out[k] = v
        elif k in ("memory_gb", "job_hours"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise ResourceError(f"{k} is a positive number, not {v!r}")
            out[k] = float(v)
        else:
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ResourceError(f"{k} is a whole number of 1 or more, not {v!r}")
            out[k] = v
    if out["cell_timeout"] > out["cell_timeout_max"]:
        if "cell_timeout" in given:
            raise ResourceError(f"cell_timeout ({out['cell_timeout']}) is above cell_timeout_max "
                                f"({out['cell_timeout_max']})")
        out["cell_timeout"] = out["cell_timeout_max"]  # a lower longest time lowers the default with it
    return out


def as_ranges(cpus):
    """Sorted CPU numbers as the text pertura.limits reads: (60, 61, ..., 71) -> "60-71", (0, 2, 4, 5, 6) -> "0,2,4-6"."""
    parts, cpus = [], sorted(cpus)
    i = 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        parts.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(parts)


def workspace_config(root, registry=None, cores=None, thread_limit=None):
    """The configuration a workspace's analysis server reads, written once by make-workspace-config: the workspace
    folder, and in its resources the core set and thread limit the server pins every job to. What is not given is
    written as pertura.limits would choose it on this machine (the thread limit, and the highest CPUs this process may
    use), so the file says what the server will use and a site changes it there. A registry of built screens is for
    the earlier servers."""
    from . import limits
    listed = None
    if cores is not None:
        try:
            listed = limits.parse_cores(cores)
        except ValueError:
            raise ResourceError(f"cores is a list of CPUs such as 60-71 or 0,2,4-6, not {cores!r}") from None
        if not listed:
            raise ResourceError(f"cores names no CPU: {cores!r}")
    n = (len(listed) if listed else limits.THREAD_LIMIT) if thread_limit is None else thread_limit
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ResourceError(f"thread_limit is a whole number of 1 or more, not {n!r}")
    if listed and len(listed) != n:
        raise ResourceError(f"cores names {len(listed)} CPUs and thread_limit is {n}: the server pins its jobs to "
                            "thread_limit cores of the set, so give as many cores as the thread limit")
    if cores is None and hasattr(os, "sched_getaffinity"):
        cores = as_ranges(sorted(os.sched_getaffinity(0))[-n:])
    resources = {"thread_limit": n, **({"cores": str(cores)} if cores else {})}
    cfg = {"workspace": os.path.abspath(root), "resources": resources}
    if registry:
        cfg["registry"] = os.path.abspath(registry)
    return cfg


def at_server_start(cfg):
    """Before the server pins itself (pertura.limits) and before numpy starts: the configuration's core set, thread
    limit and environment, put where pertura.limits and every process the server starts read them. A value already in
    the environment wins for each, as it does for the runner's jobs (a batch pinned to its own cores keeps them).
    Returns what the server uses."""
    given = _section(cfg)
    done = {}
    if given.get("cores"):
        from . import limits
        os.environ.setdefault(limits.CORES_VARIABLE, str(given["cores"]))
        done["cores"] = os.environ[limits.CORES_VARIABLE]
    if given.get("thread_limit") is not None:
        n = given["thread_limit"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ResourceError(f"thread_limit is a whole number of 1 or more, not {n!r}")
        from . import limits
        os.environ.setdefault(THREAD_LIMIT_VARIABLE, str(n))
        limits.THREAD_LIMIT = int(os.environ[THREAD_LIMIT_VARIABLE])
        done["thread_limit"] = limits.THREAD_LIMIT
    env = given.get("environment")
    if env is not None:
        if not isinstance(env, dict) or not all(
                isinstance(k, str) and VARIABLE.match(k) and isinstance(v, (str, int, float)) and not isinstance(v, bool)
                for k, v in env.items()):
            raise ResourceError(f"environment is {{name: value}}, each name letters, digits and _, each value text or a "
                                f"number, not {env!r}")
        for k, v in env.items():
            os.environ.setdefault(k, str(v))
        done["environment"] = {k: os.environ[k] for k in env}
    return done
