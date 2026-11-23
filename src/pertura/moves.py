"""The follow-up moves a module's result calls for (docs/adr/0032, D13 and amendment 2): each read from the result's
own measured numbers, never guessed from its text, with what published screens did in that case.

Each move is an entry of the knowledge base's `follow_up_moves.md` (pertura.knowledge), by its id: what to run and
the papers that made it are read there. Its papers were chosen by hand from the catalogue of 1,149 moves from 105
papers (`analysis/end_to_end/follow_up_moves_of_screens/`), as examples of the move. The screen's own paper is never
among them.

    text("each perturbation acts") -> {"id", "run", "papers"}

The moves a result's own numbers call for (for_result) went to the replies until they carried their call's result only
(docs/build_goal_subtraction_after_session8.md, S1); they are in pertura_research.retired.knowledge_pushes. Those were
ids to look up, and none was read. after() gives, with a module's found result, what published screens did next as
lines of content (the change list's P13: content in view is used, pointers are not).
"""

import re

MOVES = {  # the move's name -> its id in the knowledge base
    "each perturbation acts": "T3",
    "guides agree": "T1",
    "each member of a pair acts": "T4",
    "programmes": "O1",
    "what the changed genes mean": "X1",
    "similar perturbations": "O4",
    "the test is calibrated": "T2",
    "between or within states": "L1",
}
# the modules whose results can call for each move, as their entries in the knowledge base name them
CALLED_BY = {"T3": "B.efficacy", "T1": "F.guides_agree", "T4": "E.combinations", "O1": "C.genes", "X1": "C.genes",
             "O4": "C.genes", "T2": "C.genes", "L1": "C.states_and_composition"}


def after(module, fitting=(), base=None, at_most=6):
    """What published screens did after this module's reading, a line each: the moves its knowledge-base entry calls
    for and names next, each with when it is worth considering, what it runs and a screen that did it; and the steps
    it names of the paradigm cards the design allows (fitting: their ids), other than its own."""
    from . import knowledge
    base = base or knowledge.load()
    entry = base.get(module) or {}
    out = []
    for i in dict.fromkeys(list(entry.get("calls_for") or {}) + knowledge.ids_in(entry.get("follows") or "")):
        x = base.get(i) or {}
        f = knowledge.fields(x.get("text") or "")
        if x.get("kind") == "follow-up move":
            run = str(f.get("Run") or "").split(". ")[0].rstrip(".")
            if set(re.findall(r"\b([A-H]\.[a-z_]+)", run)) == {module}:  # this module again, by its tool or name
                continue
            seen = str((f.get("Published screens items") or [""])[0]).split(": ")[0]
            out.append(f"{x['title']}, when {str(f.get('Worth considering when') or '').rstrip('.')}: {run}"
                       + (f" (as {seen})" if seen else ""))
        elif x.get("kind") == "card step" and i.split(".")[0] in fitting:
            from .paradigms import called, module_of
            part = next((l[len("Part: "):] for l in x["text"].splitlines() if l.startswith("Part: ")), "")
            if module_of(part) != module:
                out.append(f"{x['title']} [{called(part)}] (card {i.split('.')[0]}, step {i})")
    return out[:at_most]


def text(move, base=None):
    """A move's id, what to run and its papers, from its entry in the knowledge base."""
    from . import knowledge
    mid = MOVES[move]
    entry = (base or knowledge.load()).get(mid)
    f = knowledge.fields(entry["text"]) if entry else {}
    return {"id": mid, "run": f.get("Run", ""), "papers": f.get("Published screens items", [])}
