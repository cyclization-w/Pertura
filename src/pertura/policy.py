"""Pertura's recommended numbers, in one place: the cells a test needs, the biological replicates the design's default
test needs, the q at which a row is called changed, and an optional effect-size cut beside it.

Each number is a default, never a permission. A run that takes the default (a target chosen by Pertura, the method
the design picks) uses it; a run that names its own choice (a target, a method, a test) runs wherever the computation
is possible (COMPUTABLE), and its result says how it differs from the default ("2 replicates; Pertura's default needs
3"). Only what cannot be computed is refused.

A run changes the numbers with `policy` (a module's input: {name: value}), or in the notebook with pt.policy(...);
the block jobs a notebook starts carry the notebook's numbers. Every result records the numbers it used and its notes.
"""
import contextlib
import contextvars

DEFAULTS = {
    "min_cells": 10,           # a target's or a group's cells for a test, where Pertura chooses the targets
    "min_cells_profile": 30,   # a profile's cells (similarity, replicates' agreement, opposite modes)
    "min_replicates": 3,       # biological replicates holding both sides, for the design's test over replicates
    "q": 0.05,                 # a row counts as changed below this q
    "min_effect": None,        # beside q, an effect at least this large (absolute, on the result's own scale)
}
# below these a test cannot be computed: a group of fewer cells has no spread to test; a test over replicates with one
# replicate a side has no residual degree of freedom (edgeR's quasi-likelihood, a t-test over units)
COMPUTABLE = {"min_cells": 3, "min_cells_profile": 3, "min_replicates": 2}
_CHANGED = contextvars.ContextVar("pertura_policy", default=None)
_EXPLICIT = contextvars.ContextVar("pertura_explicit", default=frozenset())
_NOTES = contextvars.ContextVar("pertura_policy_notes", default=None)
_SESSION = {}  # pt.policy(...) in the notebook: the numbers every later call of this process uses


def validate(changes):
    """The problems of a change, in words; [] where every name and value is one Pertura can use."""
    if changes in (None, {}):
        return []
    if not isinstance(changes, dict):
        return [f"policy is {{name: value}} with names {', '.join(DEFAULTS)}, not {changes!r}"]
    problems = []
    for k, v in changes.items():
        if k not in DEFAULTS:
            problems.append(f"policy: {k!r} is not one of Pertura's numbers ({', '.join(DEFAULTS)})")
        elif k == "min_effect" and v is None:
            continue
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"policy: {k} is a number, not {v!r}")
        elif k in COMPUTABLE and v < COMPUTABLE[k]:
            problems.append(f"policy: {k} {v} cannot be computed; the least is {COMPUTABLE[k]}")
        elif k == "q" and not 0 < v <= 1:
            problems.append("policy: q is above 0 and at most 1")
    return problems


def current():
    """The numbers in effect: the defaults, the notebook's changes, then the run's own."""
    return {**DEFAULTS, **_SESSION, **(_CHANGED.get() or {})}


def get(name):
    return current()[name]


def changed():
    """The numbers in effect that differ from the defaults."""
    now = current()
    return {k: v for k, v in now.items() if v != DEFAULTS[k]}


def explicit(choice):
    """Whether the run named this choice itself (a method, a test, its targets), rather than taking the default."""
    return choice in _EXPLICIT.get()


def floor(name, choice):
    """The least a run may use for a number: the recommendation where the run took the default for `choice`, else
    what can be computed (an explicit choice runs wherever it can)."""
    return min(get(name), COMPUTABLE[name]) if explicit(choice) else get(name)


def note(text):
    """A note a result carries on how it differs from Pertura's default."""
    notes = _NOTES.get()
    if notes is not None and text not in notes:
        notes.append(text)


def notes():
    return list(_NOTES.get() or [])


@contextlib.contextmanager
def using(changes=None, explicit_choices=()):
    """A run's numbers (changes on top of the notebook's) and the choices it named itself, with a fresh list of
    notes; the earlier ones come back after it."""
    problems = validate(changes)
    if problems:
        raise ValueError("; ".join(problems))
    # a nested run (a module's blocks) adds its notes to the outer run's
    tokens = (_CHANGED.set({**(_CHANGED.get() or {}), **(changes or {})}),
              _EXPLICIT.set(frozenset(explicit_choices) | _EXPLICIT.get()),
              _NOTES.set(_NOTES.get() if _NOTES.get() is not None else []))
    try:
        yield
    finally:
        _NOTES.reset(tokens[2])
        _EXPLICIT.reset(tokens[1])
        _CHANGED.reset(tokens[0])


def set_session(**changes):
    """pt.policy(...): the numbers every later call of this process uses (none: back to the defaults)."""
    problems = validate(changes)
    if problems:
        raise ValueError("; ".join(problems))
    if not changes:
        _SESSION.clear()
    _SESSION.update(changes)
    return current()


def named_targets(have_at, targets, least=None):
    """The targets a run named, against the cells each has: (the ones that can run, the ones that cannot). have_at(k)
    gives the targets with k or more cells. A named target below the default runs, with a note."""
    least = get("min_cells") if least is None else least
    possible = set(have_at(COMPUTABLE["min_cells"]))
    enough = set(have_at(least))
    run = [t for t in targets if t in possible]
    for t in run:
        if t not in enough:
            note(f"{t} has fewer than {least} cells (Pertura's default for a test); it ran as named")
    return run, [t for t in targets if t not in possible]


def used():
    """What a result records: the numbers in effect, those changed, and its notes."""
    return {"numbers": current(), "changed": changed(), "notes": notes()}
