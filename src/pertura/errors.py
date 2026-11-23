"""Whose failure a failed run is (GPT's reviews, 2026-10-11). Every layer keeps the two kinds apart, because they
lead to different records:
- **The machine's:** MemoryError, OSError (disk, files) and MachineError. MachineError covers:
  - a run killed from outside (SIGKILL, as by the out-of-memory killer);
  - Pertura's own steps in a sandbox child failing on memory or files (own_analysis.EXIT_MACHINE);
  - R running out of memory in Pertura's R tools.

  The run did not answer anything: a sweep stops and resumes, a lock stays "running" and resumes, and nothing is
  recorded as the path's or the method's result.
- **The method's:** own_analysis.OwnAnalysisError, try_runner.TryRunError, and a tool's own error (RuntimeError from
  an R tool's non-zero exit). OwnAnalysisError covers code that raised, output that broke the table's rules, and a run
  over its time or CPU limit. The path is recorded as failed, or the band as not graded.
  - **Inside a try** (pertura_research.retired.attempts), the method's failure is MethodError, raised only where a tool runs
    (try_runner's tool runs). Any other error in a try's work is Pertura's own (its groups, folds, checks, spikes): the
    try is blocked, for an operator to fix, never failed (the fresh review of phase 7, 2026-10-12).

The class is decided by where an error happened, or by a tool Pertura wrote, never by searching a log an agent's code
can write. Messages from a run on the hidden side never reach an agent: only fixed texts do (OwnAnalysisError.public,
machine_error).
"""


class MachineError(Exception):
    """The machine stopped a run: not the method's failure."""


MACHINE_ERRORS = (MemoryError, OSError, MachineError)


class MethodError(Exception):
    """A method's own failure inside a try, raised where its tool ran: the try ends with it as its result."""

    @classmethod
    def of(cls, e):
        out = cls(f"{type(e).__name__}: {e}")
        out.original = e
        return out
R_OUT_OF_MEMORY = ("cannot allocate vector", "bad_alloc", "cannot allocate memory")  # R's own words, not an agent's


def killed_from_outside(returncode):
    """A subprocess's exit that is the machine's: SIGKILL (-9). Pertura's own limits never send it: the wall clock is
    handled before, the CPU limit's soft value sends SIGXCPU (its hard value is set above it), and the memory limit
    raises MemoryError in the child."""
    return returncode == -9


def machine_error(e, where):
    """A fresh machine error of the same broad kind, with a fixed message: the original's text can hold data (numpy's
    out-of-memory message gives the array's shape) and its context the run's log."""
    if isinstance(e, MemoryError):
        return MemoryError(f"{where} ran out of memory")
    if isinstance(e, MachineError):
        return MachineError(f"{where} was stopped by the machine")
    return OSError(f"{where} failed on the machine's files or disk")
