"""Biology tool layer: one wrapper per analysis tool, all with the same contract.

A tool has:
- name: the method name in the grammar;
- claim_type and unit_kind ('pseudobulk' or 'cells');
- environment and packages: the interpreter, and the packages whose versions shape its results;
- version: the wrapper's own version;
- run(spec, data, groupset, workdir), which returns a table with columns group, construct ('all' for the pooled test,
  else the construct label), feature, effect, p.

Every tool tests each group pooled and each of its constructs separately. The grammar reads its method options from
this registry, so registering a tool adds it to the multiverse.

Subpackages: assignment/ (vector calls), qc/ (cell filters), de/ (differential expression), programs/ (program
usage), composition/ (cell states). Pathways, perturbation-level and knockdown tools follow the same contract.
"""
from .de.pseudobulk import PseudobulkR
from .de.wilcoxon import CellWilcoxon
from .composition import FisherCells, PropPaired, Propeller, QuasiBinomial
from .programs import UsagePaired, UsageWilcoxon

RESULT_COLUMNS = ("group", "construct", "feature", "effect", "p")
TOOLS = {t.name: t for t in (PseudobulkR("edger_ql"), PseudobulkR("limma_voom"), PseudobulkR("deseq2"),
                             PseudobulkR("glmgampoi"), CellWilcoxon(), UsagePaired(), UsageWilcoxon(),
                             Propeller(), QuasiBinomial(), PropPaired(), FisherCells())}


def _factory_modules():
    """Modules the factory built (factory/), registered only where PERTURA_FACTORY_MODULES lists their module.py
    files (separated by os.pathsep): the factory's end-to-end validation runs them through the job system."""
    import importlib.util
    import os
    for path in filter(None, os.environ.get("PERTURA_FACTORY_MODULES", "").split(os.pathsep)):
        spec = importlib.util.spec_from_file_location(f"factory_{len(TOOLS)}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        m = mod.Module()
        if m.name in TOOLS:
            raise ValueError(f"factory module {m.name!r} would replace a registered tool")
        TOOLS[m.name] = m


_factory_modules()


def get_tool(name):
    try:
        return TOOLS[name]
    except KeyError:
        raise ValueError(f"no tool named {name!r} (registered: {sorted(TOOLS)})") from None


def tool_for(spec):
    return get_tool(spec["method"])


def methods_for(claim_type, unit_kind):
    """Registered tool names for a claim type and unit kind, in a stable order. It reads a snapshot of the registry,
    which the server's threads may add a session's new method to meanwhile (try_runner.register_new_method)."""
    return tuple(sorted(n for n, t in list(TOOLS.items()) if t.claim_type == claim_type and t.unit_kind == unit_kind))
