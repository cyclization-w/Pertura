"""The server's limits for our work (CLAUDE.md), in a module that imports nothing heavy, so a server can set its
thread variables before numpy or R start (mcp_server's serve-loop)."""
import os

# threads for our jobs in all: 12 unless the workspace configuration sets another (pertura.resources); the server
# puts its value in PERTURA_THREAD_LIMIT, so every process it starts reads the same
THREAD_LIMIT = int(os.environ.get("PERTURA_THREAD_LIMIT") or 12)
NICE = 10  # every process of ours runs niced
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                    "NUMBA_NUM_THREADS", "R_DATATABLE_NUM_THREADS")  # numba: scanpy's normalisation (the review)
# All our jobs share one set of THREAD_LIMIT cores. Pinning keeps the limit even when a library starts more threads
# than the thread variables allow.
CORES_VARIABLE = "PERTURA_CORES"  # an explicit set, such as "60-71"; otherwise the highest THREAD_LIMIT allowed CPUs
PROTECTED_VARIABLE = "PERTURA_PROTECTED_PIDS"  # processes never to be signalled, such as "1234,5678"
# The longest a tool call waits for a job. A client such as Claude Code moves a call that runs past about 120 s to
# the background, and the images in its reply then no longer reach the model, so no call waits longer than this.
CALL_WAIT_MAX = 100


def protected_pids():
    """The PIDs in PROTECTED_VARIABLE (a site's own setting; empty by default)."""
    text = os.environ.get(PROTECTED_VARIABLE, "")
    return frozenset(int(p) for p in text.replace(" ", "").split(",") if p.isdigit())


def parse_cores(text):
    """'60-71' or '0,2,4-6' -> a sorted tuple of CPU numbers."""
    out = set()
    for part in str(text).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return tuple(sorted(out))


def core_set():
    """The shared set of cores for our jobs: CORES_VARIABLE when set, else the highest THREAD_LIMIT CPUs this process
    may use. None where CPU affinity does not exist (Windows, macOS)."""
    if not hasattr(os, "sched_getaffinity"):
        return None
    allowed = sorted(os.sched_getaffinity(0))
    given = os.environ.get(CORES_VARIABLE)
    if given:
        cores = tuple(c for c in parse_cores(given) if c in allowed)
        if not cores:
            raise ValueError(f"{CORES_VARIABLE}={given} names no CPU this process may use")
        return cores[:THREAD_LIMIT]
    return tuple(allowed[-THREAD_LIMIT:])


def pin_this_process():
    """Pin the calling thread, and every thread and process it starts afterwards, to core_set(). Call it before
    numpy or R start their pools: on Linux it does not move threads that already exist. Returns the cores, or None
    where affinity does not exist."""
    cores = core_set()
    if cores is not None:
        os.sched_setaffinity(0, cores)
    return cores


def threads_per_run(runs):
    """Threads for each of `runs` concurrent runs: their R children use this many each, and the process's own pools
    (BLAS, numba), which its runs share, this many once more, so runs + 1 shares fit THREAD_LIMIT."""
    return max(1, THREAD_LIMIT // (max(1, int(runs)) + 1))


def own_threads():
    """This process's threads: its OMP_NUM_THREADS (a job's are its spec's, a notebook kernel's its own), at most
    THREAD_LIMIT."""
    given = os.environ.get("OMP_NUM_THREADS", "")
    return max(1, min(int(given) if given.isdigit() else THREAD_LIMIT, THREAD_LIMIT))


def set_threads(n):
    """A worker process's initializer: n threads for each of its pools, so that n workers share their parent's
    threads. The variables serve the libraries it loads later; threadpoolctl and numba resize those already loaded."""
    import sys
    for v in THREAD_VARIABLES:
        os.environ[v] = str(int(n))
    try:
        from threadpoolctl import threadpool_limits
        threadpool_limits(int(n))
    except ImportError:
        pass
    if "numba" in sys.modules:
        try:
            sys.modules["numba"].set_num_threads(int(n))
        except (AttributeError, ValueError):
            pass


def worker_threads(workers):
    """The threads of each of `workers` worker processes: this process's threads shared among them."""
    return max(1, own_threads() // max(1, int(workers)))


def limit_this_process(runs, per=None):
    """Before the heavy imports: the thread variables at threads_per_run, or at `per` when that is lower (a variable
    already set lower is kept), the process pinned to the shared cores, and niced to NICE. Returns the threads per
    run."""
    pin_this_process()
    per = min(threads_per_run(runs), int(per)) if per else threads_per_run(runs)
    for v in THREAD_VARIABLES:
        old = os.environ.get(v)
        if not (old and old.isdigit() and 0 < int(old) <= per):
            os.environ[v] = str(per)
    if hasattr(os, "nice"):
        now = os.nice(0)
        if now < NICE:
            os.nice(NICE - now)
    return per
