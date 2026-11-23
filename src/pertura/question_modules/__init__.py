"""Pertura's analysis modules (docs/adr/0020, section 2): each serves one question of the plan, declares when it
applies, and returns a result, its diagnostics, a figure and a status.

A module is a function `run(screen, design, out_dir, **choices) -> Result`, registered with its Contract:
- serves: the plan's question it answers (a question ID of pertura.record's plan, such as "B.efficacy");
- applies_when: in words, and as a function of the plan's question list (the module runs when its question is
  planned);
- needs: its inputs;
- returns: what its result table holds;
- fails_when: the ways it misleads, each with the check that catches it;
- tested_against: the reference it was checked on (a learning screen's hand result, or a method's own output);
- choices: its consequential choices and their alternatives (the robustness engine varies them, ADR 0020).

    from pertura import question_modules
    m = question_modules.REGISTRY["B.efficacy"]
    r = m.run(screen, design, out_dir)        # screen: pertura.question_modules.data.Screen
"""
from .contract import REGISTRY, Contract, Result, register  # noqa: F401

# the modules that compute the most: in a workspace they run one at a time, so that two never share the workspace's
# cores and each runs as fast as alone
ONE_AT_A_TIME = ("C.genes", "C.programs", "C.states_and_composition")
PROGRAM_METHODS = ("cnmf", "sklearn_nmf", "genenmf", "genenmf_meta")  # found in the cells (pertura.steps.programs)


def queue_of(module, choices=None):
    """The queue a run of ONE_AT_A_TIME waits in, or None for any other module: each module's own, and programs found
    in the cells theirs ("programs"), so a run waits only for one of its own kind. In one shared queue, three C.genes
    of 1-7 minutes waited 31-42 minutes behind states and programs runs on GSE190604 (a9510b4); alone each module
    used 1-3 cores and at most 21 GB (timed 2026-11-14); a one-minute C.genes once waited 55 minutes behind a cNMF on
    GSE203592."""
    if module not in ONE_AT_A_TIME:
        return None
    return "programs" if module == "C.programs" and (choices or {}).get("gene_sets") in PROGRAM_METHODS else module


from . import a, b, c, d, e, f, g, overview  # noqa: F401,E402  (each registers its modules)
