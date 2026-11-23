"""The paradigm cards (docs/adr/0030): the routes deep analyses of published screens take, one card per paradigm.

Each card is a markdown file of the knowledge base (plugin/skills/pertura-knowledge/paradigms/; pertura.knowledge).
Its YAML header is read here;
its text is read by the agent. The header (docs/build/biology_depth_papers/paradigm_card_kit.md):
- id, version (a whole number, raised at every change), updated, name, use_when, steps and leads; not_when, bring,
  requires, connects and papers are optional;
- requires: what the design must hold for the card to apply, among REQUIRES. The agent reads use_when and requires
  against the user's question and the design (the cards are knowledge; the route they once drove is in
  pertura_research.retired.paradigm_route);
- each step: id, does and part; control, fork, fork_key, fork_default and when are optional. part is module:<a
  registered module>, tool:<a tool's call: the tool, then view <a view>, then the parameters it is given, as
  "tool:scores contrast" or "tool:genes genes=targets_own">, notebook, user_table, other_experiment, outside_data or
  missing:<what Pertura lacks>. fork is the question the step puts; fork_key true makes it a key fork, which stops for
  the user; fork_default is the card's choice at any other fork. when: the step is taken only when this holds, as the
  agent judges.
"""
import os

PARTS = ("notebook", "user_table", "other_experiment", "outside_data")
TOOL = "tool:"  # a step that one tool's call does, by its own parameters (docs/adr/0044)
REQUIRED = ("id", "version", "updated", "name", "use_when", "steps", "leads")
STEP_REQUIRED = ("id", "does", "part")
STEP_FIELDS = STEP_REQUIRED + ("control", "fork", "fork_key", "fork_default", "when")
FIELDS = REQUIRED + ("not_when", "bring", "requires", "connects", "papers")
REQUIRES = {  # what a card may require of the design, each measured from the design and the screen object
    "control_guides": "control guides, enough for groups of them to calibrate the tests",
    "pairs": "cells carrying a pair or set of perturbations by design",
    "time_course": "time points or a differentiation",
    "conditions": "two or more conditions",
    "two_modes": "CRISPRa and CRISPRi in one library",
}


MIN_CONTROL_GUIDES = 4  # groups of two control guides calibrate a test (pertura.question_modules.c)


class CardError(Exception):
    pass


def measured(design_facts):
    """Which REQUIRES the design holds: {requirement: bool}, from the facts the plan reads (the modes, the control
    guides, the pairs, the time points, the conditions)."""
    f = design_facts
    return {"control_guides": (f.get("n_control_guides") or 0) >= MIN_CONTROL_GUIDES,
            "pairs": bool(f.get("pairs")),
            "time_course": bool(f.get("time_course")),
            "conditions": (f.get("n_conditions") or 0) >= 2,
            "two_modes": {"crispra", "crispri"} <= set(f.get("modes") or ())}


def fitting(cards, design_facts):
    """The ids of the cards whose requires this design holds."""
    have = measured(design_facts)
    return {cid for cid, c in cards.items() if all(have.get(r) for r in c.get("requires") or [])}


def chains(cards, design_facts):
    """The routes of published screens that this design allows, one line each, for the replies where the agent plans
    (content in view; the card's controls and forks stay in its text): [card's chain]."""
    fit = fitting(cards, design_facts)
    out = []
    for cid, c in sorted(cards.items()):
        if cid not in fit:
            continue
        steps = []
        for n, s in enumerate(c["steps"], 1):
            part = str(s["part"])
            where = called(part) if part.startswith(("module:", TOOL)) else part.replace("_", " ")
            step = f"{n}. {' '.join(str(s['does']).split())}"
            step += (f" (where {' '.join(str(s['when']).split())})"
                     if s.get("when") and "where" not in str(s["does"]) else "")
            step += f"; default: {' '.join(str(s['fork_default']).split())}" if s.get("fork_default") else ""
            steps.append(f"{step} [{where}]")
        use = " ".join(str(c["use_when"]).split()).split(". ")[0]
        out.append(f"{cid}, {c['name']}. Used when {use[0].lower() + use[1:]}. " + " ".join(steps))
    return out


def _tool_call(part):
    """(tool, view or None, [the parameters as the part gives them]) of a tool: part."""
    words = part[len(TOOL):].split()
    rest = words[1:]
    view = rest[1] if rest[:1] == ["view"] and len(rest) > 1 else None
    return (words[0] if words else ""), view, rest[2:] if view else rest


def called(part):
    """A step's part as the agent runs it: a tool's call ("tool:scores contrast" -> "scores with contrast"), a module
    by the tool calls that start it (docs/adr/0044), anything else as the card names it."""
    part = str(part)
    if part.startswith(TOOL):
        tool, view, given = _tool_call(part)
        return tool + (f" view {view}" if view else "") + (f" with {', '.join(given)}" if given else "")
    if not part.startswith("module:"):
        return part
    from .kinds import places
    name = part[len("module:"):]
    return "; ".join(t + (f" view {v}" if v != t else "") for t, v in places(name)) or name


def module_of(part):
    """The module a step's part runs (a tool's by its view), or None."""
    part = str(part)
    if part.startswith("module:"):
        return part[len("module:"):]
    if part.startswith(TOOL):
        from .kinds import view_of
        tool, view, _ = _tool_call(part)
        return view_of(tool, view)[1]
    return None


def _tool_problems(part):
    """Why a tool: part is not a call there is: its tool, its view and each parameter's name, against the tools."""
    from . import kinds
    tool, view, given = _tool_call(part)
    if tool not in kinds.KINDS:
        return [f"no tool {tool!r}"]
    if view and view not in kinds.KINDS[tool][1]:
        return [f"{tool} has no view {view}"]
    own = kinds.tool(tool)["inputSchema"]["properties"]
    return [f"{tool} takes no {p.split('=')[0]}" for p in given if p.split("=")[0] not in own]


def cards_dir():
    """The cards' folder: PERTURA_CARDS, else the knowledge base's."""
    from . import knowledge
    return os.environ.get("PERTURA_CARDS") or os.path.join(knowledge.folder(), "paradigms")


def read(path):
    """One card's header, with its file's path; CardError when it is not a card."""
    import yaml
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---"):
        raise CardError(f"{path}: a card starts with its YAML header between --- lines")
    try:
        head = yaml.safe_load(text.split("\n---", 1)[0].lstrip("-\n"))
    except yaml.YAMLError as e:
        raise CardError(f"{path}: its header is not YAML: {e}")
    if not isinstance(head, dict):
        raise CardError(f"{path}: its header is not a mapping")
    return {**head, "path": os.path.abspath(path)}


def problems(card, modules=None):
    """Why a card's header is not as the kit fixes it, or []. modules: the registered modules' names, to check its
    module parts."""
    out = [f"needs {k}" for k in REQUIRED if k not in card]
    out += [f"unknown field {k}" for k in card if k not in FIELDS + ("path",)]
    if not isinstance(card.get("version"), int) or card.get("version", 0) < 1:
        out.append("version is a whole number from 1")
    out += [f"requires {r}, which the code cannot measure" for r in card.get("requires") or [] if r not in REQUIRES]
    ids = set()
    for s in card.get("steps") or []:
        sid = s.get("id", "?")
        out += [f"step {sid} needs {k}" for k in STEP_REQUIRED if k not in s]
        out += [f"step {sid}: unknown field {k}" for k in s if k not in STEP_FIELDS]
        if sid in ids:
            out.append(f"step {sid} twice")
        ids.add(sid)
        part = str(s.get("part", ""))
        if part.startswith("module:"):
            if modules is not None and part[7:] not in modules:
                out.append(f"step {sid}: no module {part[7:]}")
        elif part.startswith(TOOL):
            out += [f"step {sid}: {e}" for e in _tool_problems(part)]
        elif not (part in PARTS or part.startswith("missing:")):
            out.append(f"step {sid}: part {part!r} is module:<name>, tool:<call>, {', '.join(PARTS)} or "
                       "missing:<what>")
        if (s.get("fork_key") or s.get("fork_default")) and not s.get("fork"):
            out.append(f"step {sid}: fork_key and fork_default belong to a step with a fork")
        if s.get("fork_key") and s.get("fork_default"):
            out.append(f"step {sid}: a key fork is the user's; it has no default")
    for c in card.get("connects") or []:
        if c.get("at") not in ids:
            out.append(f"connects at {c.get('at')}, which is not a step")
    return out


def load(folder=None, modules=None):
    """{id: card} for every card in the folder; CardError naming each card that does not pass problems()."""
    folder = folder or cards_dir()
    out, bad = {}, []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md"):
            continue
        card = read(os.path.join(folder, name))
        found = problems(card, modules)
        if found:
            bad.append(f"{name}: {'; '.join(found)}")
        elif card["id"] in out:
            bad.append(f"{name}: id {card['id']} is also {os.path.basename(out[card['id']]['path'])}'s")
        else:
            out[card["id"]] = card
    if bad:
        raise CardError(" | ".join(bad))
    return out
