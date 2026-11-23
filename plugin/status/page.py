"""The user's page of the design, the findings and the results, on their own machine (docs/adr/0032, D14 and
amendment 1).

The analysis runs on a server reached over ssh, and the terminal shows no images. After each Pertura tool call the
plugin's hook saves the figures the call returned to the agent (its image blocks) beside the project's status file,
and writes one HTML page, a workbench (development session 6, problem 11):
- now: the step the agent took last and why, and the runs in flight;
- the design as the user confirmed it, each value with where it came from;
- the threads, each with its status and the reason for it, the published screens' moves it carries, its steps in
  order and its findings;
- the findings, latest first, each with its figure (the one it names, else the latest of its runs), its earlier
  wordings, the user's replies, its doubts with their answers, the prior knowledge given with it and how it differs
  from the paper or from prior knowledge (a thread shows the prior knowledge it was opened with, and the knowledge
  recorded for a question of the plan has a card of its own);
- the results, latest first: every analysis the agent ran, with why, its thread, its code and its output folded, and
  the figures it drew;
- the overviews the replies carry, each its latest: the cells at the build, and the screen at the first look's end
  (its table grows as its slow columns fill).
Beside it, notebook.ipynb holds the same steps in order as a notebook, and notebook.html shows them in a browser:
each cell's code and output after a line of what it did and why, and the figures by their files. The page reloads itself every few seconds, and a click enlarges
a figure. It computes nothing from the server: it shows what the agent was shown. Beside it, knowledge.html is the
knowledge base the agent reads (knowledge_page.py): each read of the base is a step of its own among the results,
its entries linked there, a question marked Draft as the base marks its short answers; each result and finding of an
earlier release links to the entries its reply named (docs/adr/0032, amendment 2); and that page lists the entries
this project read, each linked back to the cards here that named it.

Standard library only: it runs on the user's machine, where Pertura itself is not installed.

    fold(page, tool, tool_input, reply, images, now)  ->  page (a dict kept in the status file)
    write(folder, page, title)                        ->  the page's path
"""
import base64
import builtins
import html
import io
import json
import keyword
import os
import re
import time
import tokenize

import knowledge_page

PAGE_NAME = "page.html"
NOTEBOOK_NAME = "notebook.ipynb"
NOTEBOOK_PAGE = "notebook.html"  # the notebook as a page: a browser shows an .ipynb as its JSON
FIGURES = "figures"
KEEP = 400  # figures and results the page keeps; the oldest go first
CODE_KEEP, OUTPUT_KEEP = 8000, 3000  # characters of a cell's code and of its output the page keeps
RELOAD_SECONDS = 5
STEP_SHOWN = 300  # a step's result shown whole up to this many characters; the rest under its Details
ACTIVE = ("queued", "starting", "running")  # a run's states before it ends
THREAD_ENDS = ("explained", "contradicted", "out of data", "set aside")
NUMPY_SCALAR = re.compile(r"\bnp\.(?:float|int|uint|bool_|str_)\d*\(([^()]*)\)")


def response_images(response):
    """The image blocks of a tool's response, in order: [(bytes, mime type)]. A response reaches a hook as the
    content blocks, a dict holding them, or text, so every list and dict is searched. An image block comes in MCP's
    form ({"type": "image", "data", "mimeType"}) or in the form Claude Code keeps a tool's output in, which is the
    one its hooks receive ({"type": "image", "source": {"type": "base64", "media_type", "data"}})."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("type") == "image":
                src = x["source"] if isinstance(x.get("source"), dict) else x
                if isinstance(src.get("data"), str):
                    try:
                        out.append((base64.b64decode(src["data"]),
                                    str(src.get("mimeType") or src.get("media_type") or "image/png")))
                    except ValueError:
                        pass
                    return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(response)
    return out


def replace_file(tmp, path):
    """os.replace, tried again for a moment: on Windows a reader of the file (the status line) or a virus scanner can
    hold it."""
    for wait in (0.05, 0.2, 0.5, None):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def save_images(folder, stamp, images):
    """Write the images into folder/figures, named for the call; returns their file names."""
    os.makedirs(os.path.join(folder, FIGURES), exist_ok=True)
    names = []
    for k, (data, mime) in enumerate(images):
        ext = ".jpg" if "jpeg" in mime else ".png"
        name = f"{stamp}-{k + 1}{ext}"
        path = os.path.join(folder, FIGURES, name)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        replace_file(tmp, path)
        names.append(name)
    return names


def empty():
    return {"figures": [], "findings": {}, "order": [], "results": [], "runs": {}, "design": None, "proposal": None,
            "threads": {}, "doubts": {}, "meta": {}, "now": None, "running": [], "overviews": {}, "plan": None,
            "prior": [], "differences": [], "deposit": None, "reading_checks": None}


def _overviews(page, r, names, now):
    """The cells overview (at the build) and the screen overview (at the first look's end) a reply carries, each
    kept as its latest; a screen overview's column filled later comes as that column alone and joins its rows."""
    if isinstance(r.get("cells_overview"), dict):
        page["overviews"]["cells"] = {**r["cells_overview"], "run_id": r.get("run_id"),
                                      "server": r["cells_overview"].get("figure"),
                                      "figure": names[0] if names else None, "time": now}
    got = r.get("screen_overview")
    have = page["overviews"].get("screen")
    if not isinstance(got, dict):
        if have is not None and names and r.get("run_id") and r.get("run_id") == have.get("run_id"):
            have["figure"] = names[0]  # the overview's run opened: its figure
        return
    if isinstance(got.get("rows"), list) or have is None:
        page["overviews"]["screen"] = {"path": got.get("path"), "columns": dict(got.get("columns") or {}),
                                       "rows": [dict(x) for x in got.get("rows") or [] if isinstance(x, dict)],
                                       "run_id": got.get("run_id") or r.get("run_id"),
                                       "figure": names[0] if names else None, "time": now}
        have = page["overviews"]["screen"]
    else:
        have["columns"].update(got.get("columns") or {})
        if names:
            have["figure"] = names[0]
        have["time"] = now
    for column, values in (got.get("filled") or {}).items():
        for row in have["rows"]:
            if isinstance(values, dict) and str(row.get("target")) in values:
                row[column] = values[str(row.get("target"))]


def readable(text):
    """Printed text as a reader sees it: numpy's scalars as their values (np.float64(6.7) as 6.7)."""
    return NUMPY_SCALAR.sub(r"\1", str(text or ""))


def _cut(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


def _output(r):
    """A cell's output: what it printed, its last value, and its error."""
    parts = [str(r.get("stdout") or "").rstrip()]
    if r.get("result") not in (None, ""):
        parts.append(str(r["result"]))
    if r.get("status") not in (None, "ok"):
        parts.append(str(r.get("traceback") or r.get("error") or ""))
    return readable("\n".join(p for p in parts if p))


AGENT_WORDS = {"main": "the main agent", "pertura:research-lead": "the research lead", "pertura:skeptic": "the skeptic",
               "pertura:worker": "a worker", "pertura:figure-reviewer": "the figure reviewer",
               "pertura:checks": "the checks", "subagent": "a subagent"}


def _agent(name):
    """An agent as the record names it (the main agent, or the subagent's type), in words."""
    return AGENT_WORDS.get(str(name), str(name).split(":")[-1].replace("-", " "))


def _raised(raised_by):
    """What raised a thread, in words: a run, a finding, a design key or the user's words."""
    if isinstance(raised_by, dict):
        return "; ".join(f"{k} {v}" for k, v in raised_by.items())
    return str(raised_by or "")


def _thread(page, tid, now, touch=True, **fields):
    """A thread on the page, made or brought up to date; an empty field leaves what it had. touch: the thread's own
    event (opened, taken, ended, a step or a finding on it), which orders the threads; a reply that only shows it
    does not."""
    th = page["threads"].setdefault(str(tid), {"thread_id": str(tid), "about": "", "status": "open", "moves": [],
                                               "time": now, "last": now})
    for k, v in fields.items():
        if v not in (None, "", [], {}):
            th[k] = v
    if touch:
        th["last"] = now
    return th


def _thread_views(r):
    """The threads a reply shows: after the first look (threads), at the end of a step (open_threads), in the
    status (threads)."""
    out = []
    for key in ("threads", "open_threads"):
        v = r.get(key)
        rows = v.get("open") if isinstance(v, dict) else v
        out += [t for t in rows or [] if isinstance(t, dict) and t.get("thread_id")]
    return out


def _shown_paths(tool, reply):
    """The server paths of the figures a reply shows, in the order of its images."""
    r = reply or {}
    if tool in ("run_code", "close_branch", "thread_status"):  # a thread of alternatives shows its ways' figures
        return list(r.get("figures_shown") or [])
    if tool == "module_result":
        return [r["figure"]] if isinstance(r.get("figure"), str) else []
    return []


def _line(text, limit=240):
    """A text's lines that say something, as one line cut to a length."""
    line = "; ".join(x.strip() for x in readable(text).splitlines() if x.strip())
    return line[:limit] + ("..." if len(line) > limit else "")


def _last(text, limit=240):
    """The last line of a text that says something (an error's own line), cut to a length."""
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    return _line(lines[-1], limit) if lines else ""


def _analysis(module):
    """A module's name in words, as its question's are made: the part after the letter, its underscores spaces."""
    words = str(module or "").split(".", 1)[-1].replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "An analysis"


def _words(what):
    """A job's name as the user reads it: a module's in words, anything else as it is."""
    what = str(what or "")
    return _analysis(what) if re.fullmatch(r"[A-H]\.[a-z_]+", what) else what


def _read(reply):
    """The knowledge base's ids a reply named: what its result is read beside, its moments' and its moves'."""
    beside = reply.get("read_beside_it") if isinstance(reply.get("read_beside_it"), dict) else {}
    ids = []
    for w in beside.get("ways") or []:
        ids += [i for i in (w.get("read") if isinstance(w.get("read"), list) else [])]
    ids += list(beside.get("also_read") or [])
    for m in beside.get("moments") or []:
        ids += list(m.get("read") or [])
    ids += [m.get("id") for m in reply.get("moves") or [] if isinstance(m, dict) and m.get("id")]
    return [i for i in dict.fromkeys(str(x) for x in ids if x)]


def _knowledge_read(a, r):
    """A read of the knowledge base (read_knowledge) as a step: what was asked; what came back, in a line (the entries
    read whole, those found by words, those left out and those not in the base); and the ids of the entries read
    whole."""
    asked = "; ".join(f"{k} {', '.join(map(str, v)) if isinstance(v, list) else v}"
                      for k, v in (("ids", a.get("ids")), ("module", a.get("module")), ("words", a.get("words"))) if v)
    entries = [str(x["id"]) for x in r.get("entries") or [] if isinstance(x, dict) and x.get("id")]
    found = (r["questions"] if isinstance(r.get("questions"), list) else []) + list(r.get("found") or [])
    found = "; ".join(f"{x['id']} {x.get('title') or ''}".strip() for x in found if isinstance(x, dict) and x.get("id"))
    said = "; ".join(f"{k}: {v}" for k, v in (("read", ", ".join(entries)), ("found", found),
                                              ("left out", ", ".join(map(str, r.get("left_out") or []))),
                                              ("not in the base", ", ".join(map(str, r.get("no_entry") or []))))
                     if v)
    return ("Read the knowledge base" + (f": {asked}" if asked else ""),
            said or "its questions by step, and what it holds", entries)


def _beside(page, now, prior=None, differs=None, **where):
    """What a call gave from outside the data, kept with where it belongs (finding_id, thread_id or question_id):
    prior knowledge {text, source, citation} and a difference from the paper or from prior knowledge {text, source},
    as the server took them."""
    where = {k: str(v) for k, v in where.items() if v is not None}
    if isinstance(prior, dict) and prior.get("text"):
        page["prior"].append({**where, "text": prior["text"], "source": prior.get("source"),
                              "citation": prior.get("citation"), "time": now})
        del page["prior"][:-KEEP]
    if isinstance(differs, dict) and differs.get("text"):
        page["differences"].append({**where, "text": differs["text"], "source": differs.get("source"), "time": now})
        del page["differences"][:-KEEP]


def _result(page, tool, a, r):
    """What a call did and its result, in a line each, for the page's results; None for a call that is no result."""
    if tool == "run_code":
        what = a.get("what") or "a notebook cell"
        if r.get("status") not in (None, "ok"):
            return what, f"{r.get('status')}: {_last(r.get('error') or r.get('traceback'))}"
        return what, _line(r.get("stdout")) or _line(r.get("result")) or "ran"
    if tool == "module_result" and r.get("run_id") and r.get("state") not in ACTIVE:
        what = page["runs"].get(r["run_id"]) or f"run {r['run_id']}"
        if r.get("state") == "failed":  # as a cell's error reads: the state, then its log's last line
            return what, f"failed: {_last(r.get('output'))}"
        return what, _line(r.get("summary")) or f"ended: {r.get('state')}"
    if tool == "close_branch" and a.get("branch_id"):
        return f"the ways tried at {a['branch_id']}", _line(a.get("outcome")) or "closed"
    if tool == "thread_status" and r.get("comparison") is not None and a.get("thread_id"):
        return f"the ways tried at {a['thread_id']}", _line(a.get("reason")) or a.get("status") or "ended"
    if tool == "read_knowledge" and "error" not in r:
        return _knowledge_read(a, r)[:2]
    return None


def fold(page, tool, tool_input, reply, names, now=None):
    """Fold one call into the page's state: the figures it showed (names: the files saved for its images), its result,
    a finding recorded or reworded, a reply noted, the design confirmed."""
    page = page or empty()
    for k, v in empty().items():  # a page written before it held these
        page.setdefault(k, v)
    now = time.time() if now is None else now
    a = tool_input if isinstance(tool_input, dict) else {}
    r = reply if isinstance(reply, dict) else {}
    if names:
        shown = _shown_paths(tool, r)
        what = a.get("what") or r.get("summary") or tool
        for k, name in enumerate(names):
            page["figures"].insert(0, {"file": name, "server": shown[k] if k < len(shown) else None,
                                       "run_id": r.get("run_id"), "what": str(what)[:300], "tool": tool,
                                       "worker": a.get("worker"), "time": now})
        del page["figures"][KEEP:]
    if tool in ("pertura_status", "page_state") and isinstance(r.get("digest"), dict):  # the page's top (item 5)
        page["digest"] = {**r["digest"], "time": now}
        if isinstance(r["digest"].get("line"), str):  # where the analysis stands, in the status line's words
            page["line"] = r["digest"]["line"]
    if isinstance(r.get("digest_line"), str):  # a reply of an earlier release
        page["line"] = r["digest_line"]
    if tool in ("run_code", "run_module"):  # the step the agent took last
        page["now"] = {"what": str(a.get("what") or _analysis(a.get("module")))[:300], "why": a.get("why"),
                       "thread_id": a.get("thread_id"), "worker": a.get("worker"), "time": now}
    if tool == "run_module" and r.get("run_id"):  # what a run is, for its result when it ends
        page["runs"][r["run_id"]] = _analysis(a.get("module"))
        page["meta"][r["run_id"]] = {"why": a.get("why"), "thread_id": a.get("thread_id"),
                                     "choices": a.get("choices") or None}
    if tool == "record_finding" and a.get("finding_id") and r.get("checks_run"):
        page["runs"][r["checks_run"]] = f"the checks of {a['finding_id']}"
    for rid in list(page["runs"])[:-KEEP]:
        del page["runs"][rid]
    for rid in list(page["meta"])[:-KEEP]:
        del page["meta"][rid]
    got = _result(page, tool, a, r)
    if got:
        same = next((x for x in page["results"] if r.get("run_id") and x.get("run_id") == r.get("run_id")
                     and x["tool"] == tool), None)
        if same is None:  # a result opened again stays where it was first shown
            read = _knowledge_read(a, r)[2] if tool == "read_knowledge" else _read(r)
            x = {"what": str(got[0])[:300], "result": str(got[1]), "figures": list(names), "run_id": r.get("run_id"),
                 "tool": tool, "worker": a.get("worker"), "time": now, "read": read}
            if tool == "run_code":
                x.update(why=a.get("why"), thread_id=a.get("thread_id"), code=_cut(a.get("code"), CODE_KEEP),
                         output=_cut(_output(r), OUTPUT_KEEP))
            elif tool == "module_result":
                meta = page["meta"].get(r.get("run_id")) or {}
                x.update(why=meta.get("why"), thread_id=meta.get("thread_id"), choices=meta.get("choices"))
            else:  # the ways tried at a thread of alternatives
                x.update(thread_id=a.get("thread_id") or a.get("branch_id"))
            page["results"].insert(0, x)
            del page["results"][KEEP:]
            if x.get("thread_id"):
                _thread(page, x["thread_id"], now)
        elif names and not same["figures"]:
            same["figures"] = list(names)
    if tool == "open_thread" and r.get("thread_id") and "error" not in r:
        _thread(page, r["thread_id"], now, about=a.get("about"), raised_by=_raised(a.get("raised_by")),
                status="open", user_words=a.get("user_words"), doubt_id=a.get("doubt_id"))
        _beside(page, now, a.get("prior"), thread_id=r["thread_id"])
    for t in _thread_views(r):  # the threads as the page state and pertura_status give them
        _thread(page, t["thread_id"], now, touch=False, about=t.get("about"), status=t.get("status"),
                why=t.get("why"), moves=[m["id"] for m in t.get("moves") or [] if isinstance(m, dict) and m.get("id")])
    if tool == "thread_status" and a.get("thread_id") and "error" not in r:
        _thread(page, a["thread_id"], now, status=r.get("status") or a.get("status"), why=a.get("reason"))
    if tool == "raise_doubt" and a.get("doubt_id") and "error" not in r:
        page["doubts"][str(a["doubt_id"])] = {"doubt_id": str(a["doubt_id"]), "finding_id": a.get("finding_id"),
                                              "text": a.get("text"), "would_change": a.get("would_change"),
                                              "time": now, "answers": []}
    if tool == "answer_doubt" and a.get("doubt_id") in page["doubts"] and "error" not in r:
        page["doubts"][a["doubt_id"]]["answers"].append({"outcome": a.get("outcome"), "limit": a.get("limit"),
                                                         "reason": a.get("reason"),
                                                         "run_ids": list(a.get("run_ids") or []), "time": now})
    if tool == "record_finding" and a.get("finding_id") and "error" not in r:
        fid = str(a["finding_id"])
        name = a.get("statement") or " ".join(str(a.get(k) or "") for k in ("target", "feature", "direction")).strip()
        page["findings"][fid] = {"finding_id": fid, "finding": name, "figure": a.get("figure"),
                                 "run_ids": list(a.get("run_ids") or []), "replies": [], "earlier": [], "time": now,
                                 "read": _read(r), "thread_id": a.get("thread_id"), "checks_run": r.get("checks_run")}
        page["order"] = [fid] + [f for f in page["order"] if f != fid]
        if a.get("thread_id"):
            _thread(page, a["thread_id"], now)
        _beside(page, now, a.get("prior"), a.get("differs"), finding_id=fid)
    if tool == "reword_finding" and a.get("finding_id") in page["findings"] and "error" not in r:
        f = page["findings"][a["finding_id"]]
        _beside(page, now, a.get("prior"), a.get("differs"), finding_id=a["finding_id"])
        if a.get("statement"):  # the earlier wording folds under the latest
            f.setdefault("earlier", []).insert(0, {"finding": f["finding"], "time": f["time"],
                                                   "because": a.get("reason")})
            f["finding"], f["time"] = a["statement"], now
        if a.get("figure"):
            f["figure"] = a["figure"]
        if a.get("run_ids"):
            f["run_ids"] = list(a["run_ids"])
    if tool == "note_reply" and a.get("finding_id") in page["findings"] and "error" not in r:
        page["findings"][a["finding_id"]]["replies"].append({"kind": a.get("kind"), "words": a.get("words"),
                                                             "time": now})
    if tool == "note_difference" and a.get("finding_id") and "error" not in r:
        _beside(page, now, differs=a, finding_id=a["finding_id"])
    if tool == "record_knowledge" and "error" not in r:
        _beside(page, now, a, question_id=a.get("question_id"))
    _overviews(page, r, names, now)
    plan = r.get("plan")  # the agent's plan as it stands (the page state, pertura_status)
    if isinstance(plan, list):
        plan = {"items": plan}
    if isinstance(plan, dict) and isinstance(plan.get("items"), list):
        page["plan"] = {"items": [dict(x) for x in plan["items"] if isinstance(x, dict)],
                        "version": plan.get("version"), "why": plan.get("why"), "next": plan.get("next"),
                        "time": now}
    if tool == "check_design" and isinstance(r.get("proposal"), list):  # shown before the form
        page["proposal"] = {"items": list(r["proposal"]), "time": now}
    if tool == "confirm_design" and isinstance(r.get("confirmed_values"), dict):
        page["design"] = {"values": dict(r["confirmed_values"]), "origins": dict(r.get("origins") or {}),
                          "confirmed": bool(r.get("design_confirmed")), "time": now}
    if tool == "describe_deposit" and isinstance(r.get("samples"), list):  # how the deposit is read (docs/adr/0041)
        read = r.get("read_into_cell_tables")
        page["deposit"] = {"samples": [{k: s.get(k) for k in ("name", "kind", "matrix", "read_by")}
                                       for s in r["samples"] if isinstance(s, dict)],
                           "read": read if isinstance(read, dict) else None,
                           "unreadable": [u for u in r.get("unreadable") or [] if isinstance(u, dict)], "time": now}
    if tool == "build_screen_object" and isinstance(r.get("reading_checks"), dict):  # what the build read
        page["reading_checks"] = {"facts": [str(x) for x in r["reading_checks"].get("facts") or []], "time": now}
    return page


def _same_file(f, want):
    """Whether a saved figure is the file wanted: the same server path, or, for a run that names its files from its
    own folder (./figure.png), the same name in the wanted path's run folder."""
    server, parts = f["server"], want.replace("\\", "/").split("/")
    if server == want or server.endswith("/" + want.lstrip("/")):
        return True
    return (server.startswith("./") and len(parts) > 1 and parts[-1] == server[2:]
            and parts[-2] == str(f.get("run_id")))


def _figure_of(page, finding):
    """The saved figure that shows a finding: the one at its figure's server path; else the latest figure of the
    first of its runs that drew one, in the order the finding names them (the run it rests on first, runs added
    later after it), so a cell that drew several findings' figures and was added to each shows none of theirs."""
    want = finding.get("figure")
    for f in page["figures"]:
        if want and f.get("server") and _same_file(f, str(want)):
            return f
    for rid in finding.get("run_ids") or []:
        for f in page["figures"]:  # the latest first
            if f.get("run_id") == rid:
                return f
    return None


HERE = os.path.dirname(os.path.abspath(__file__))
STYLE, SCRIPT, ALIEN = "page_style.css", "page_script.js", "pertura_alien.svg"  # inlined: the page is one file
GOOD = ("agrees", "agree", "beyond the control groups")  # a check's outcomes, as the record words them
BAD = ("other direction", "disagree", "other way, not significant", "no difference")
NOT_RUN = "not run"  # a check the data could not take (its reason says why): no agreement either way
MARK_WORDS = {"good": "Checks agree", "part": "Checks partly agree", "bad": "A check disagrees",
              "open": "No checks recorded"}
# the record's words as an interface says them
OUTCOME_WORDS = {"agrees": "Agrees", "agree": "Agrees", "disagree": "Disagrees", "other direction": "Opposite direction",
                 "other way, not significant": "Opposite direction, not significant", "no difference": "No difference",
                 "beyond the control groups": "Beyond the control groups", NOT_RUN: "Not run"}
CONCERN_WORDS = {"holds": "Ruled out", "holds, narrowed": "Narrows the finding", "weakens": "Weakens the finding",
                 "overturns": "Overturns the finding", "still open": "Not settled",
                 "stated as a limit": "Noted as a limitation", None: "Not checked yet"}
CONCERN_MARKS = {"holds": "good", "holds, narrowed": "part", "weakens": "part", "overturns": "bad",
                 "stated as a limit": "part"}
CONCERN_PLURAL = {"Narrows the finding": "narrow the finding", "Weakens the finding": "weaken the finding",
                  "Overturns the finding": "overturn the finding", "Noted as a limitation": "noted as limitations"}
LIMIT_WORDS = {"design": "the design cannot answer it", "tool": "no tool here does it"}  # a limitation's kind
OWED = "Left unchecked for time or cost"  # a limit of the session's budget: a check the tools here could still run
KIND_WORDS = {"gene": "Gene expression", "gene_set": "Gene-set score", "score": "Per-cell score", "state": "Cell states",
              "interaction": "Interaction", "description": "Description", "custom": "Other"}
DECISION_WORDS = {"confirmed": "In the report", None: "Proposed for the report"}
PLAN_WORDS = {"done": "Completed", "open": "Open", "left": "Left out"}
TOOL_WORDS = {"run_code": "Notebook", "module_result": "Analysis", "read_knowledge": "Knowledge base",
              "close_branch": "Alternatives", "thread_status": "Alternatives"}
REF_KINDS = ("run", "finding", "thread", "doubt", "knowledge", "question")
REF = re.compile(r"\[((?:run|finding|thread|doubt|knowledge|question):[^\]]+)\]")  # one mark may name several
REF_BARE = re.compile(r"\s*" + REF.pattern)  # a mark with the space before it
# a label a result's sentence starts with, as the skill asks its text to keep the observation, the inference and the
# independent evidence apart ("Observation:", "Inferred:", "Limits:"): each starts a paragraph
ARGUED = re.compile(r"(?:^|(?<=[.)\]])\s+)([A-Z][a-z]+(?: [a-z]+){0,2}):\s+")
HOUR = re.compile(r"^\d{8}T(\d{2})(\d{2})")


def _asset(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _epoch(t):
    """Seconds since the epoch: as given, or from an ISO time as the record keeps it."""
    if isinstance(t, (int, float)):
        return float(t)
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(t)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _when(t):
    """A time as hours and minutes: seconds since the epoch, or an ISO time as the record keeps it."""
    if isinstance(t, str) and len(t) >= 16 and t[10:11] in ("T", " "):
        return t[11:16]
    try:
        return time.strftime("%H:%M", time.localtime(float(t or 0)))
    except (TypeError, ValueError):
        return ""


def _read_line(ids, names, label="Knowledge base", drafts=()):
    """The entries a result or a finding was read beside, in words, each linked to its place in the knowledge page;
    a question marked Draft (drafts: their ids), as the base marks every short answer a draft for a person's
    review."""
    e = html.escape
    draft = "<span class=\"draft\">Draft</span> "
    links = [f"<a class=\"kid\" href=\"{knowledge_page.NAME}#{e(i)}\" title=\"{e(i)}\">"
             f"{draft if i in drafts else ''}{e(names.get(i) or i)}</a>" for i in ids]
    return f"<p class=\"reply\">{e(label)}: {' '.join(links)}</p>" if links else ""


def _prior_row(p, e, head=""):
    came = ", ".join(dict.fromkeys(str(x) for x in (p.get("source"), p.get("citation")) if x))
    return (f"<p class=\"reply\">{e(head)}<b>Prior knowledge</b>: {e(str(p['text']))}"
            + (f" <span class=\"muted\">({e(came)})</span>" if came else "") + "</p>")


def _beside_rows(page, e, key, value):
    """The prior knowledge and the differences kept for one finding or thread (key: finding_id or thread_id), in the
    order given."""
    rows = [_prior_row(p, e) for p in page["prior"] if p.get(key) == value]
    return rows + [f"<p class=\"reply\"><b>Differs from {e(str(d.get('source') or 'the paper'))}</b>: "
                   f"{e(str(d['text']))}</p>" for d in page["differences"] if d.get(key) == value]


def _img(name, alt):
    """A figure as drawn; loaded once it nears the screen, since the page holds every view's and reads itself again."""
    e = html.escape
    return (f"<img class=\"fig\" src=\"{FIGURES}/{e(name)}\" alt=\"{e(str(alt)[:120])}\" loading=\"lazy\" "
            "decoding=\"async\" tabindex=\"0\">")


# what is new since the user last marked the page read, kept in this browser, the first visit counting as read (all
# would be new); run after the cards are on the page
NEW_SCRIPT = ("(function(){try{var k='pertura-seen-'+location.pathname,s=+localStorage.getItem(k)||0;"
              "if(!s){s=Date.now()/1000;localStorage.setItem(k,s)}"
              "document.querySelectorAll('[data-time]').forEach(function(x){if(+x.dataset.time>s)"
              "x.classList.add('new')});var m=document.getElementById('mark-read');if(m)m.onclick=function(e){"
              "e.preventDefault();localStorage.setItem(k,Date.now()/1000);document.querySelectorAll('.new')"
              ".forEach(function(x){x.classList.remove('new')})}}catch(e){}})();")
# before the page is drawn: scripts on (one view at a time), and the theme the reader chose
HEAD_SCRIPT = ("document.documentElement.classList.add('js');try{var t=localStorage.getItem('pertura-theme');"
               "if(t)document.documentElement.setAttribute('data-theme',t)}catch(e){}")


def _on_thread(page, tid, e, base=""):
    """' on <the thread>', linked to its card (on the page named by base, this one by default); nothing when the step
    names no thread."""
    if not tid:
        return ""
    about = (page["threads"].get(str(tid)) or {}).get("about") or str(tid)
    return f" on <a href=\"{base}#{e(_thread_anchor(tid))}\">{e(_line(about, 100))}</a>"


def _thread_anchor(tid):
    return "thread-" + safe(tid)


def _finding_anchor(fid):
    return "finding-" + safe(fid)


def _report_anchor(rid):
    return "report-" + safe(rid)


def _result_anchor(x):
    """A step's anchor (a result the agent was shown): its run's id, else the time it was shown."""
    return "result-" + safe(x.get("run_id") or int(float(x.get("time") or 0) * 1000))


def _used(page):
    """The knowledge base's ids the findings and the results were read beside, latest first, each with what named it
    and the link to its card here: [[id, [[label, href], ...]], ...], for the knowledge page's "in this project"."""
    out = {}
    for fid in page["order"]:
        f = page["findings"].get(fid) or {}
        for i in f.get("read") or []:
            out.setdefault(i, []).append([f"finding {fid}: {_line(f.get('finding'), 80)}",
                                          f"{PAGE_NAME}#{_finding_anchor(fid)}"])
    for x in page["results"]:  # a step by its time, since steps alike read alike (each "Read the knowledge base")
        for i in x.get("read") or []:
            out.setdefault(i, []).append([f"{_when(x.get('time'))} · {_line(x.get('what'), 80)}",
                                          f"{PAGE_NAME}#{_result_anchor(x)}"])
    return [[i, by] for i, by in out.items()]


def _alien(state, px=2):
    return _asset(ALIEN).strip().replace(
        "<svg ", f"<svg class=\"alien {state}\" width=\"{20 * px}\" height=\"{18 * px}\" aria-hidden=\"true\" ", 1)


def _mark(outcome):
    o = str(outcome or "").strip().lower()
    return "open" if not o else "skip" if o == NOT_RUN else "good" if o in GOOD else "bad" if o in BAD else "part"


def _outcome(outcome):
    o = str(outcome or "").strip()
    return OUTCOME_WORDS.get(o.lower()) or (o[:1].upper() + o[1:] if o else "Not run yet")


def _sq(mark):
    """A status square; the words beside it say what it marks."""
    return f"<i class=\"sq {mark}\"></i>"


class _Page:
    """What the views share: the page's state, the record as the server gives it (pertura.page_view), the runs'
    methods, the knowledge base's names, and the lookups made from them."""

    def __init__(self, page, record, methods, title, link=None, due=None):
        self.page, self.methods, self.title = page, [m for m in methods or [] if isinstance(m, dict)], title
        self.link = link if isinstance(link, dict) and link.get("port") and link.get("token") else None
        self.due = due if isinstance(due, dict) else {}  # what is pending, by decider (status_file's due)
        self.rec = record if isinstance(record, dict) else {}
        self.name = title.split(" · ", 1)[1] if " · " in title else title
        self.names = knowledge_page.titles()
        self.drafts = {q["id"] for q in knowledge_page.questions() if q["short"]}
        self.rf = self.rec.get("findings") if isinstance(self.rec.get("findings"), dict) else {}
        self.fids = list(page["order"]) + [f for f in self.rf if f not in page["findings"]]  # the latest first
        got = self.rec.get("results") if isinstance(self.rec.get("results"), dict) else {}
        self.results = list(got.items())  # the record's order, the order first proposed: a new version keeps its number
        self.result_of = {}
        for n, (rid, r) in enumerate(self.results, start=1):
            for fid in r.get("finding_ids") or []:
                self.result_of.setdefault(fid, (n, rid))
        self.steps = list(reversed(page["results"]))  # in the order they were taken
        self.threads = sorted(page["threads"].values(),
                              key=lambda t: (t.get("status") in THREAD_ENDS, -float(t.get("last") or t.get("time") or 0)))

    def finding(self, fid):
        pf, rf = self.page["findings"].get(fid) or {}, self.rf.get(fid) or {}
        checks = rf.get("checks") if isinstance(rf.get("checks"), dict) else {}
        marks = [_mark(c.get("outcome")) for each in checks.values() if isinstance(each, dict)
                 for c in each.values() if isinstance(c, dict)]
        ran = [m for m in marks if m != "skip"]  # a check not run agrees with nothing, nor disagrees
        mark = ("open" if not ran else "bad" if "bad" in ran else
                "good" if all(m == "good" for m in ran) else "part")
        return {"id": fid, "statement": pf.get("finding") or rf.get("statement") or fid,
                "time": pf.get("time") or rf.get("time"), "earlier": pf.get("earlier") or [],
                "replies": pf.get("replies") or [], "read": pf.get("read") or [],
                "run_ids": [str(r) for r in pf.get("run_ids") or rf.get("run_ids") or []],
                "thread_id": pf.get("thread_id") or rf.get("thread_id"),
                "figure": rf.get("png") or pf.get("figure") or rf.get("figure"), "checks": checks,
                "kind": rf.get("kind"), "disagree": rf.get("disagree"), "mark": mark,
                "not_run": len(marks) - len(ran)}

    def figure(self, server, run_ids=()):
        return _figure_of(self.page, {"figure": server, "run_ids": list(run_ids)})


def _flink(c, e, fid, cut=200, no=None):
    """A finding's row in a list (no: its number where a result's text cites it by number), its id on hover."""
    f = c.finding(fid)
    return (f"<li data-time=\"{_epoch(f['time']):.0f}\"><a href=\"#{e(_finding_anchor(fid))}\" title=\"Finding {e(fid)}\">"
            + (f"<span class=\"no\">{no}</span>" if no else "")
            + f"{_sq(f['mark'])}<span>{e(_line(f['statement'], cut))}</span>"
            f"<span class=\"t\"><span class=\"mk\">{e(MARK_WORDS[f['mark']])}</span>{_when(f['time'])}</span></a></li>")


REF_GROUP = re.compile(r"(?:\s*" + REF.pattern + r")+")  # marks side by side, with the spaces before them


def _refs(c, e, text, numbers=None):
    """A text with its [run:...], [finding:...] and like marks as links where the page holds what they name: a finding
    numbered in numbers (its place in the list below the text) as a superscript number, as papers cite; any other
    mark as its id in small type."""
    runs = {str(x.get("run_id")): _result_anchor(x) for x in c.steps if x.get("run_id")}
    numbers = numbers or {}
    s, out, at = str(text or ""), [], 0
    for m in REF_GROUP.finditer(s):
        out.append(e(s[at:m.start()]))
        named = [p for x in REF.finditer(m.group(0)) for p in _named(x.group(1))]
        cited = [(numbers[i], i) for k, i in named if k == "finding" and i in numbers]
        links = []
        for kind, i in named:
            if kind == "finding" and i in numbers:
                continue
            href = {"run": f"#{runs[i]}" if i in runs else None, "finding": f"#{_finding_anchor(i)}",
                    "thread": f"#{_thread_anchor(i)}", "knowledge": f"{knowledge_page.NAME}#{i}"}.get(kind)
            links.append(f"<a class=\"ref\" href=\"{e(href)}\">{e(i)}</a>" if href else f"<span class=\"ref\">{e(i)}</span>")
        if cited:
            out.append("<sup class=\"refs\">" + ",".join(
                f"<a href=\"#{e(_finding_anchor(i))}\" title=\"Finding {e(i)}\">{n}</a>" for n, i in cited) + "</sup>")
        if links:
            out.append(" " + " ".join(links))
        at = m.end()
    return "".join(out) + e(s[at:])


TITLE_AT = 90  # characters: a finding's title is its statement's first sentence, cut near this


def _title(statement):
    """A finding's title: its statement's first sentence, or, past TITLE_AT characters, the sentence up to its last
    comma, colon, semicolon or parenthesis outside parentheses before that, else to a word with an ellipsis."""
    s = (_sentences(statement, 1)[0] or str(statement or "")).strip().rstrip(".")
    depth, cuts = 0, []
    for i, ch in enumerate(s[:TITLE_AT]):
        if ch == "(":
            cuts += [i] if depth == 0 else []
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch in ",;:" and depth == 0:
            cuts.append(i)
    if len(s) <= TITLE_AT and depth == 0:
        return s
    cuts = [i for i in cuts if i > 35]  # shorter would say too little
    return s[:cuts[-1]].rstrip(" ,;:") if cuts else s[:s.rfind(" ", 0, TITLE_AT)].rstrip(" ,;:") + "…"


def _named(mark):
    """The (kind, id) pairs a mark names, "finding:f6, finding:f2" or "run:a, b": a piece without a kind takes the one
    before it."""
    kind, out = None, []
    for piece in mark.split(","):
        head, _, rest = piece.strip().partition(":")
        if rest and head in REF_KINDS:
            kind, piece = head, rest
        if piece.strip():
            out.append((kind, piece.strip()))
    return out


def _kicker(e, *parts):
    """The line above a view's title, its parts in one phrase: "Result 1, in the report, 02:52"."""
    got = [str(p) for p in parts if p]
    if not got:
        return ""
    said = ", ".join(got[:1] + [p[:1].lower() + p[1:] if p[1:2].islower() else p for p in got[1:]])  # not CRISPRa
    return f"<div class=\"kicker\">{e(said)}</div>"


def _head(e, title, aside="", right=""):
    return (f"<div class=\"section-head\"><h2>{e(title)}</h2>"
            + (f"<span class=\"aside\">{e(aside)}</span>" if aside else "")
            + (f"<span class=\"right\">{right}</span>" if right else "") + "</div>")


def _view(e, vid, crumbs, rows):
    return f"<section class=\"view\" id=\"{e(vid)}\" data-crumbs=\"{e(crumbs)}\">" + "\n".join(rows) + "</section>"


# the user's pending items (analysis_service.PENDING) in the page's words; another label reads as it is
USER_ITEMS = {"results to decide": "Results are proposed for the report: confirm each or change it in the chat",
              "the user's next step": "Choose the next step after Result {n}"}


def _waits(c):
    """What waits on the user, each with where to settle it: the design proposed and not yet confirmed (the design
    gate's, before any analysis), then the user's items of the pending list the last main-agent reply named, the list
    the status line counts (status_file's due). A form the session holds open is the page's script's to show, from
    the helper."""
    out, design = [], c.page.get("design") or {}
    if not design.get("confirmed") and (design or (c.page.get("proposal") or {}).get("items")):
        out.append(("Confirm the design", "#design"))
    for label in c.due.get("user") or []:
        if label == "the user's next step":
            n, rid = next(((n, rid) for n, (rid, r) in enumerate(c.results, start=1) if r.get("next_step_waits")),
                          (None, None))
            out.append((USER_ITEMS[label].format(n=n) if n else "Choose the next step",
                        f"#{_report_anchor(rid)}" if rid else "#overview"))
        elif label == "results to decide":
            rid = next((rid for rid, r in c.results if r.get("decision") is None), None)
            out.append((USER_ITEMS[label], f"#{_report_anchor(rid)}" if rid else "#overview"))
        else:
            out.append((label, "#conversation"))
    return out


def _state(c):
    """The alien's state and the session's line: working, waiting on the user, done, or still."""
    page = c.page
    if page.get("running"):
        n = len(page["running"])
        return "working", f"Running {n} job{'s' if n != 1 else ''}"
    if _waits(c):
        return "waiting", "Needs your input"
    if c.rec.get("complete"):
        return "done", "Complete"
    n = page.get("now")
    return "still", (f"Latest step {_when(n['time'])}" if n else "No steps yet")


def _standing(c, e):
    """Where the analysis stands, from the latest digest the server gave (pertura_status); the step the agent took
    last and why, and the runs in flight."""
    page, d = c.page, c.page.get("digest") or {}
    n, running = page.get("now"), page.get("running") or []
    rows = []
    on = d.get("on")
    if on:
        rows.append(f"<p><b>Current thread:</b> {e(str(on.get('about')))} <span class=\"muted\">({e(str(on.get('thread_id')))}"
                    f"; {e(str(on.get('why') or ''))})</span></p>")
    unread = sum(1 for f in c.fids if not c.finding(f)["replies"])
    if unread:
        rows.append(f"<p>Findings without your reply: {unread} · <a href=\"#findings\">Review findings</a> "
                    "<span class=\"muted\">(the agent goes on meanwhile)</span></p>")
    for label, items in (("Recent activity", d.get("since")), ("Up next", d.get("next"))):
        if items:
            listed = "<ul>" + "".join(f"<li>{e(str(x))}</li>" for x in items) + "</ul>"
            rows.append(f"<details data-k=\"since\"><summary><b>{e(label)}:</b> {len(items)}</summary>{listed}</details>"
                        if label == "Recent activity" else f"<p><b>{e(label)}:</b></p>{listed}")
    if n:
        who = f", {e(str(n['worker']))}" if n.get("worker") else ""
        rows.append(f"<p>Latest step: <b>{e(str(n['what']))}</b> <span class=\"muted\">({_when(n['time'])}{who})</span>"
                    f"{_on_thread(page, n.get('thread_id'), e)}</p>")
        if n.get("why"):
            rows.append(f"<p class=\"reply\">Why: {e(str(n['why']))}</p>")
    if running:
        rows.append("<p>Running:</p><ul>" + "".join(
            f"<li>{e(_words(j.get('what')))} <span class=\"muted\">(since {_when(j.get('started'))})</span></li>"
            for j in running) + "</ul>")
    if not rows:
        return []
    if d:
        rows.append(f"<p class=\"muted\">Updated {_when(d.get('time'))}. Items new since you last looked are marked "
                    "new. <a href=\"#\" id=\"mark-read\">Mark all as read</a></p>")
    return [f"<div class=\"abstract\"><div class=\"who\"><b>Status</b>{e(_state(c)[1])}</div>"
            f"<div>{''.join(rows)}</div></div>"]


def _design_first(c, e):
    """While the design is open (before it is confirmed, or once it is reopened): the analysis waits on it, so the page
    says so first, with how long the design has been worked on, what is settled and what is open, and the auditor's
    last check, from the design as the server has it (the record's design)."""
    d = c.rec.get("design") if isinstance(c.rec.get("design"), dict) else {}
    if not d.get("open"):
        return []
    since = _epoch(d.get("since"))
    waited = f" (about {round((time.time() - since) / 60)} min)" if since else ""
    settled, open_ = list(d.get("confirmed") or []), list(d.get("open") or [])
    check = d.get("last_check") if isinstance(d.get("last_check"), dict) else {}
    problems = [str(p) for p in check.get("problems") or []]
    rows = [f"<p><b>The analysis starts once you confirm the design.</b> The design auditor reads the files and "
            f"measures the data first; the project opened {_when(d.get('since'))}{waited}.</p>",
            f"<p>Settled: {len(settled)} of {len(settled) + len(open_)}"
            + (f" ({e(', '.join(settled))})" if settled else "") + f". Still open: {e(', '.join(open_))}.</p>"]
    if check:
        rows.append(f"<details data-k=\"design-check\"><summary>The auditor's last check, {_when(check.get('time'))}: "
                    + (f"{_plural(len(problems), 'problem')}</summary><ul>"
                       + "".join(f"<li>{e(p)}</li>" for p in problems) + "</ul>" if problems else
                       "no problem</summary>") + "</details>")
    rows.append("<p><a href=\"#design\">What the auditor measured and proposes, item by item</a></p>")
    return [f"<div class=\"needs\"><b>The design comes first</b>{''.join(rows)}</div>"]


def _needs(c, e):
    """What waits on the user, each linked to where it is settled, and the main agent's own items in one quiet line;
    nothing when nothing waits."""
    waits, mine = _waits(c), [str(x) for x in c.due.get("main agent") or []]
    return ((["<div class=\"needs\"><b>Needs your input</b><ul>" + "".join(
        f"<li><a href=\"{e(h)}\">{e(t)}</a></li>" for t, h in waits) + "</ul></div>"] if waits else [])
            + ([f"<p class=\"muted agent-items\">Pertura is working on: {e(', '.join(mine))}</p>"] if mine else []))


SENTENCE_END = re.compile(r"(?<=[a-z0-9)%\]]\.)\s+(?=[A-Z(\[])")  # a full stop, then the next sentence's capital
STATE_MARKS = {"working": "accent", "waiting": "part", "done": "good"}


def _sentences(text, n):
    """A text's first n sentences, and the rest."""
    got = [x for x in SENTENCE_END.split(str(text or "").strip()) if x]
    return " ".join(got[:n]), " ".join(got[n:])


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _status_line(c, e):
    """The session's state, unless it only says the latest step (the pill says that on every view), and the counts."""
    state, line = _state(c)
    said = "" if state == "still" else f"{_sq(STATE_MARKS.get(state, 'open'))} <span>{e(line)}</span>"
    return (f"<p class=\"status-line\">{said}<span class=\"muted\">{_plural(len(c.steps), 'step')} and "
            f"{_plural(len(c.fids), 'finding')}</span></p>")


def _in_short(c, e):
    """The summary in its first two sentences, the results' titles, and the rest of it folded: the report's story once
    one is written, as the report opens with it, else the research lead's last."""
    lead = c.rec.get("lead") if isinstance(c.rec.get("lead"), dict) else {}
    story = c.rec.get("story") or lead.get("story")
    if not story:
        return []
    head, rest = _sentences(story, 2)
    done = bool(c.rec.get("complete"))
    titles = "".join(f"<li><a href=\"#{e(_report_anchor(rid))}\">{e(str(r.get('title') or rid))}</a></li>"
                     for rid, r in c.results)
    return ["<div class=\"in-short\"><div class=\"k\"><b>Summary</b> <span>"
            + ("from the report" if c.rec.get("story") else "by the <a href=\"#lead\">research lead</a>")
            + f"</span> <span class=\"oc\">{_sq('good' if done else 'open')} "
            f"{'Complete' if done else 'Not complete yet'}</span>"
            + (f" <span>{_when(lead['time'])}</span>" if lead.get("time") else "")
            + f"</div><p class=\"lead-text\">{_refs(c, e, head)}</p>" + (f"<ol>{titles}</ol>" if titles else "")
            + (f"<details class=\"more\" data-k=\"summary\"><summary>Read the rest of the summary</summary>"
               f"<p class=\"prose\">{_refs(c, e, rest)}</p></details>" if rest else "") + "</div>"]


def _tabs(parts, on):
    """One part at a time: a tab for each part that holds anything, (key, label as HTML, rows), the one keyed on shown
    first. The page's script keeps the reader's choice across reloads; without scripts every part shows, each under
    its own heading."""
    parts = [p for p in parts if p[2]]
    if not parts:
        return []
    on = on if on in [k for k, _, _ in parts] else parts[0][0]
    bar = "".join(f"<button type=\"button\" class=\"tab{' on' if k == on else ''}\" role=\"tab\" "
                  f"aria-selected=\"{'true' if k == on else 'false'}\" data-tab=\"{k}\">{label}</button>"
                  for k, label, _ in parts)
    return [f"<div class=\"tabs\" role=\"tablist\">{bar}</div>"] + [
        f"<div class=\"tab-panel{' on' if k == on else ''}\" role=\"tabpanel\" data-panel=\"{k}\">{''.join(rows)}</div>"
        for k, _, rows in parts]


def _overview(c, e):
    """The screen and its design in a line, where it stands, what waits on the user and the summary's first sentences
    with the results' titles; then one part at a time: the results, the latest findings, the first look, the open
    threads, the screen's table and the status. The results show first once there are any; before, the status."""
    page, d = c.page, c.page.get("design") or {}
    values = d.get("values") or {}
    picked = [str(values[k]) for k in values if str(k).lower() in ("organism", "modality")]
    rows = [_kicker(e, "Perturb-seq screen", *[_line(v, 40) for v in picked],
                    f"updated {time.strftime('%H:%M')}"), f"<h1>{e(c.name)}</h1>", _status_line(c, e)]
    rows.append("<div class=\"chips\">" + (
        f"<a class=\"chip\" href=\"#design\">Design confirmed at {_when(d.get('time'))}</a>"
        if d.get("confirmed") else "<a class=\"chip\" href=\"#design\">Design not confirmed yet</a>") + "</div>")
    rows += _design_first(c, e) + _needs(c, e) + _in_short(c, e)
    if c.rec.get("question_lacks") and not c.rec.get("complete"):
        rows.append(f"<p class=\"muted\"><b>What your question still lacks</b>, as the review reads it: "
                    f"{e(_line(c.rec['question_lacks'], 400))}</p>")
    in_results = sum(1 for f in c.fids if f in c.result_of)
    results = ["<div class=\"section\">" + _head(e, "Results", f"{len(c.results)} results, {in_results} of "
                                                 f"{len(c.fids)} findings", "<a href=\"#findings\">All findings</a>")
               + "<div class=\"results\">" + "".join(_res_card(c, e, n, rid, r)
                                                      for n, (rid, r) in enumerate(c.results, start=1))
               + "</div></div>"] if c.results else []
    latest = ["<div class=\"section\">" + _head(e, "Latest findings", f"{len(c.fids)} recorded",
                                                "<a href=\"#findings\">All findings</a>")
              + "<ul class=\"flist\">" + "".join(_flink(c, e, fid) for fid in c.fids[:8]) + "</ul></div>"] if c.fids else []
    open_ = [t for t in c.threads if t.get("status") not in THREAD_ENDS]
    tree = [x for x in c.rec.get("tree") or [] if isinstance(x, dict) and x.get("thread_id")]
    threads = ["<div class=\"section\">" + _head(e, "The analysis as a tree", f"{len(open_)} open of {len(c.threads)}",
                                                 "<a href=\"#threads\">All threads</a>") + "".join(
        f"<div class=\"thread-row\" style=\"padding-left:{14 * int(x.get('depth') or 0)}px\">"
        f"{_sq('accent' if x.get('main') else '')}<div class=\"about\">"
        f"{'★ ' if x.get('main') else '⑂ ' if x.get('fork_of') else ''}"
        f"<a href=\"#{e(_thread_anchor(x['thread_id']))}\">{e(str(x.get('about') or x['thread_id']))}</a>"
        + (f" <span class=\"muted\">changes {e(str(x.get('choice')))}</span>" if x.get("fork_of") else "")
        + f"</div><span class=\"status\">{e(str(x.get('status')))}</span><div class=\"meta\">"
        f"<span>{e(x['thread_id'])}</span></div></div>" for x in tree) + "</div>"] if tree else [
        "<div class=\"section\">" + _head(e, "Open threads", f"{len(open_)} open of {len(c.threads)}",
                                                 "<a href=\"#threads\">All threads</a>") + "".join(
        f"<div class=\"thread-row\">{_sq('accent')}<div class=\"about\"><a href=\"#"
        f"{e(_thread_anchor(th['thread_id']))}\">{e(str(th.get('about') or th['thread_id']))}</a></div>"
        f"<span class=\"status\">{e(str(th.get('status')))}</span><div class=\"meta\">"
        f"<span>{e(th['thread_id'])}</span><span>updated {_when(th.get('last'))}</span></div></div>"
        for th in open_[:6]) + "</div>"] if open_ else []
    looked = [s for s in c.rec.get("first_look") or [] if isinstance(s, dict)]
    targets = [r for r in ((page.get("overviews") or {}).get("screen") or {}).get("rows") or [] if isinstance(r, dict)]

    def n(x):
        return f"<span class=\"n\">{e(str(x))}</span>"
    rows += _tabs([("results", "Results" + n(len(c.results)), results),
                   ("findings", "Findings" + n(len(c.fids)), latest),
                   ("first-look", "First look" + n(len(looked)), _first_look(c, e)),
                   ("threads", "Threads" + n(f"{len(open_)} open"), threads),
                   ("screen", "Screen" + n(_plural(len(targets), "target")), _screen_table(c, e)),
                   ("status", "Status", _standing(c, e))], "results" if c.results else "status")
    return _view(e, "overview", f"{c.name} / Overview", rows)


def _first_look(c, e):
    """The first look's sections in the code's order, each with its figure and the findings that read it."""
    sections = [s for s in c.rec.get("first_look") or [] if isinstance(s, dict)]
    if not sections:
        return []
    cards = []
    for s in sections:
        # the section's own figure, else none: another figure of its run shows another section (states and shares)
        fig = c.figure(s.get("png"), [] if s.get("png") else [s.get("run_id")] if s.get("run_id") else [])
        thumb = f"<div class=\"thumb\">{_img(fig['file'], s.get('label'))}</div>" if fig else ""
        fids = [str(f) for f in s.get("finding_ids") or []]
        read = ("<ul class=\"flist\">" + "".join(_flink(c, e, f, 90) for f in fids[:3]) + "</ul>"
                + (f"<a href=\"#findings\">and {len(fids) - 3} more</a>" if len(fids) > 3 else "") if fids
                else f"<p class=\"unread\">{_sq('open')} No findings yet</p>")
        cond = f"<div class=\"meta\">{e(str(s['condition']))}</div>" if s.get("condition") else ""
        cards.append(f"<article class=\"card fl-card{'' if fig else ' bare'}\">{thumb}<div class=\"body\"><div class=\"label\">"
                     f"{e(str(s.get('label') or s.get('section')))}</div>{cond}{read}</div></article>")
    done = sum(1 for s in sections if s.get("finding_ids"))
    return ["<div class=\"section\">" + _head(e, "First look", f"{done} of {len(sections)} sections with findings")
            + "<div class=\"fl-grid\">" + "".join(cards) + "</div></div>"]


def _screen_table(c, e):
    """The first look's table of the screen, one row per target, its columns as the code filled them."""
    o = (c.page.get("overviews") or {}).get("screen") or {}
    rows = [r for r in o.get("rows") or [] if isinstance(r, dict)]
    if not rows:
        return []
    filled = o.get("columns") if isinstance(o.get("columns"), dict) else {}
    cols = [k for k in (filled or rows[0]) if k not in ("target", "cells_by_unit")]

    def by(k):
        module = (filled.get(k) or {}).get("module") if isinstance(filled.get(k), dict) else None
        return f"from {c.names.get(module) or _analysis(module)}" if module else "from the screen object"
    head = "<th>Target</th>" + "".join(f"<th title=\"{e(by(k))}\">{e(str(k).replace('_', ' '))}</th>" for k in cols)
    body = "".join("<tr><th>" + e(str(r.get("target"))) + "</th>" + "".join(
        f"<td>{e('–' if r.get(k) in (None, '') else _num(r.get(k)))}</td>" for k in cols) + "</tr>" for r in rows)
    return ["<div class=\"section\">" + _head(e, "Screen overview", f"{len(rows)} targets")
            + f"<div class=\"tbl-wrap tall\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
            "</div>"]


def _res_card(c, e, n, rid, r):
    fig = c.figure(r.get("png") or r.get("figure"), [r.get("figure")] if r.get("figure") else [])
    thumb = (f"<div class=\"thumb\">{_img(fig['file'], r.get('title'))}</div>" if fig
             else "<div class=\"thumb\"><span class=\"muted\">Figure not available locally</span></div>")
    first = _sentences(REF_BARE.sub("", str(r.get("text") or "")), 1)[0]  # a preview: the result's page links the marks
    fids = list(r.get("finding_ids") or [])
    return (f"<article class=\"card res-card\">{thumb}<div class=\"body\">"
            + _kicker(e, f"Result {n}", _decision(r), _when(r.get("time")))
            + f"<h3><a href=\"#{e(_report_anchor(rid))}\">{e(str(r.get('title') or rid))}</a></h3>"
            f"<p class=\"first\">{e(first)}</p>"
            f"<a href=\"#{e(_report_anchor(rid))}\">{_plural(len(fids), 'finding')}</a>"
            "</div></article>")


def _decision(r):
    d = r.get("decision")
    return DECISION_WORDS.get(d) or str(d)


def _caption(c, e, fig, label, title):
    """A figure's caption: its label, then the step that drew it as a link (the step holds the request it answered,
    its code and its output), and the button to ask about it."""
    step = next((x for x in c.steps if fig.get("run_id") and str(x.get("run_id")) == str(fig["run_id"])), None)
    drawn = (f" <a href=\"#{e(_result_anchor(step))}\">Drawn at {_when(step.get('time'))}</a>" if step else "")
    full = (f" <a class=\"full\" href=\"{FIGURES}/{e(fig['file'])}\" target=\"_blank\" "
            "rel=\"noopener\">Full size</a>")
    return (f"<figcaption><b>{e(label)}</b>{drawn}{full} "
            + _ask(c, e, "figure " + str(fig["file"]), title, "Ask about this figure") + "</figcaption>")


def _report(c, e, n, rid, r):
    fig = c.figure(r.get("png") or r.get("figure"), [r.get("figure")] if r.get("figure") else [])
    rows = [_kicker(e, f"Result {n}", _decision(r), _when(r.get("time")),
                    f"version {r['version']}" if r.get("version") else ""),
            f"<h1>{e(str(r.get('title') or rid))}</h1>",
            "<div class=\"chips\">" + _ask(c, e, f"result {rid}", r.get("title")) + "</div>"]
    if fig:
        rows.append(f"<figure class=\"block\"><div class=\"plate\">{_img(fig['file'], r.get('title'))}</div>"
                    + _caption(c, e, fig, f"Figure {n}", r.get("title")) + "</figure>")
    fids = list(r.get("finding_ids") or [])
    no = {fid: k for k, fid in enumerate(fids, start=1)}  # the text cites its findings by their place below
    parts = ARGUED.split(str(r.get("text") or "").strip())  # [before any label, label, its text, label, ...]
    rows.append("<div class=\"block\">"
                + (f"<p class=\"prose\">{_refs(c, e, parts[0], no)}</p>" if parts[0] else "")
                + "".join(f"<p class=\"prose\"><b>{e(k)}:</b> {_refs(c, e, v, no)}</p>"
                          for k, v in zip(parts[1::2], parts[2::2])) + "</div>")
    rows += _next_steps(c, e, n, rid, r)
    rows.append("<div class=\"block\"><h2>Findings</h2><ul class=\"flist\">"
                + "".join(_flink(c, e, fid, no=no[fid]) for fid in fids) + "</ul></div>")
    return _view(e, _report_anchor(rid), f"{c.name} / Result {n}", rows)


def _next_steps(c, e, n, rid, r):
    """A result's next steps (docs/adr/0050, D5) in the agent's order: each its question, why, its kind of analysis,
    how many of the studies read took a step of that kind after a result like it (n of m, m always stated) and those
    steps as references, linked to their papers. While the pick waits, a button puts the choice in the message box
    for the user to send (the main agent relays it with note_reply); once chosen, the thread the pick opened."""
    steps = [s for s in r.get("next_steps") or [] if isinstance(s, dict)]
    if not steps:
        return []
    pick = r.get("pick") if isinstance(r.get("pick"), dict) else None
    threads = c.rec.get("threads") if isinstance(c.rec.get("threads"), dict) else {}

    def to(p):
        tid = str(p.get("thread_id") or "")
        about = (threads.get(tid) or {}).get("about") if tid else None
        return (f"<a href=\"#{e(_thread_anchor(tid))}\" title=\"{e(_line(about, 120))}\">{e(tid)}</a>" if tid
                else "a thread")

    items = []
    for s in steps:
        rank, counts = s.get("rank"), s.get("counts") if isinstance(s.get("counts"), dict) else None
        said = f"Result {n} ({rid}), option {rank}: {s.get('question')}"
        if pick and pick.get("option") == rank:
            act = f"<p class=\"chosen\"><b>Chosen</b> → {to(pick)}</p>"
        elif r.get("next_step_waits"):
            act = ("<p><button class=\"ask\" type=\"button\" data-about=\"" + e(f"result {rid}") + "\" data-title=\""
                   + e(_line(r.get("title"), 90)) + "\" data-say=\"" + e(said) + "\">Choose this</button></p>"
                   if c.link else f"<p><button class=\"ask\" type=\"button\" data-copy=\"{e(said)}\">"
                                  "Copy this choice</button></p>")
        else:
            act = ""
        refs = [x for x in s.get("references") or [] if isinstance(x, dict)]
        items.append(
            f"<li class=\"next-step{' rec' if s.get('recommended') else ''}\"><p class=\"q\"><b>Option {e(str(rank))}"
            f"</b> {e(str(s.get('question') or ''))}"
            + (" <span class=\"tag\">Recommended</span>" if s.get("recommended") else "")
            + (" <span class=\"tag\">Needs new data</span>" if s.get("needs_new_data") else "") + "</p>"
            + (f"<p class=\"why\">{e(str(s['why']))}</p>" if s.get("why") else "")
            + f"<p class=\"muted\">Kind of analysis: {e(str(s.get('move') or ''))}"
            + (f". {e(str(counts.get('n')))} of the {e(str(counts.get('m')))} studies read took a step of this kind "
               f"{e(str(counts.get('situation') or ''))}" if counts else "") + ".</p>"
            + ("<ul class=\"refs\">" + "".join(
                "<li>" + (f"<a href=\"{e(str(x['source']))}\" target=\"_blank\" rel=\"noopener\">"
                          f"{e(str(x.get('paper') or x.get('row')))}</a>" if x.get("source")
                          else e(str(x.get("paper") or x.get("row"))))
                + f", step {e(str(x.get('step')))}" + (f", {e(str(x['panels']))}" if x.get("panels") else "")
                + "</li>" for x in refs) + "</ul>" if refs else "")
            + act + "</li>")
    own = (f"<p class=\"chosen\"><b>You chose your own next step</b> → {to(pick)}</p>"
           if pick and pick.get("option") is None else "")
    earlier = [p for p in r.get("earlier_picks") or [] if isinstance(p, dict)]
    before = (f"<p class=\"muted\">Chosen after the result's earlier versions: "
              + ", ".join(to(p) for p in earlier) + "</p>" if earlier else "")
    return ["<div class=\"block\"><h2>Next steps</h2><ol class=\"next-steps\">" + "".join(items) + "</ol>" + own
            + before + "</div>"]


def _steps_of(c, f):
    """The steps behind a finding: those of the runs it names, and its thread's up to its latest wording."""
    named, tid, last = set(f["run_ids"]), f.get("thread_id"), _epoch(f.get("time")) or float("inf")
    return [x for x in c.steps if (x.get("run_id") and str(x["run_id"]) in named)
            or (tid and str(x.get("thread_id") or "") == str(tid) and _epoch(x.get("time")) <= last)]


def _num(v):
    """A check's number as a reader reads it: three significant digits, a very small or large one in powers of ten."""
    if isinstance(v, float):
        return f"{v:.2e}" if v and not 1e-3 <= abs(v) < 1e5 else f"{v:.3g}"
    return str(v)


def _checks_rows(f, e):
    rows = []
    for target, each in f["checks"].items():
        if not isinstance(each, dict) or not each:
            continue
        rows.append(f"<h3 class=\"muted\" style=\"font-size:14px;margin:14px 0 6px\">{e(str(target))}</h3>"
                    "<div class=\"tbl-wrap\"><table class=\"checks\"><tbody>")
        for name, ck in each.items():
            ck = ck if isinstance(ck, dict) else {}
            nums = "; ".join(f"{k.replace('_', ' ')} {_num(v)}" for k, v in ck.items() if k not in ("outcome", "reason"))
            rows.append(f"<tr><th>{e(str(name).replace('_', ' '))}</th><td class=\"o\"><span class=\"oc\">"
                        f"{_sq(_mark(ck.get('outcome')))} {e(_outcome(ck.get('outcome')))}</span></td>"
                        f"<td class=\"muted\">{e(nums)}{(' · ' + e(str(ck['reason']))) if ck.get('reason') else ''}"
                        "</td></tr>")
        rows.append("</tbody></table></div>")
    if f.get("disagree"):
        rows.append(f"<p class=\"reply\"><b>Disagree</b>: {e(json.dumps(f['disagree'], ensure_ascii=False))}</p>")
    return rows


def _checks_said(f):
    """The checks' agreement in words, and how many checks the data could not take."""
    n = f.get("not_run") or 0
    return MARK_WORDS[f["mark"]] + (f", {n} not run" if n else "")


def _concern(a, why=True):
    """A concern's answer (None: none yet) in words, with its mark. A limitation of the design or the tools says
    which, unless why is False; one left for the session's time or cost is a check still owed, marked as not
    checked."""
    o = a.get("outcome") if a else None
    limit = a.get("limit") if o == "stated as a limit" else None
    if limit == "budget":
        return OWED, "open"
    word = CONCERN_WORDS.get(o) or str(o)
    return (f"{word}: {LIMIT_WORDS[limit]}" if why and limit in LIMIT_WORDS else word), CONCERN_MARKS.get(o, "open")


def _concerns_said(answers):
    """The concerns by their latest answers (None: none yet): "ruled out", "both ruled out", "all 3 ruled out", or
    "1 ruled out, 1 not checked yet"."""
    words = [_concern(a, why=False)[0] for a in answers]

    def say(w, n):
        w = CONCERN_PLURAL.get(w, w) if n > 1 else w
        return w[:1].lower() + w[1:]
    if len(set(words)) == 1:
        n = len(words)
        return say(words[0], 1) if n == 1 else f"both {say(words[0], 2)}" if n == 2 else f"all {n} {say(words[0], n)}"
    return ", ".join(f"{words.count(w)} {say(w, words.count(w))}" for w in dict.fromkeys(words))


def _doubt_rows(doubts, e, by=None):
    """Each doubt with who raised it (by: the record's doubts_raised_by), what would change, and its answers."""
    rows = []
    for d in doubts:
        who = (by or {}).get(d["doubt_id"])
        rows.append(f"<div class=\"doubt\"><div class=\"t\">{e(str(d.get('text')))} <span class=\"muted\">"
                    f"({e(d['doubt_id'])}, {_when(d.get('time'))}"
                    + (f", raised by {e(_agent(who))}" if who else "") + ")</span></div>")
        if d.get("would_change"):
            rows.append(f"<div class=\"wc\"><b>Would change the conclusion:</b> {e(str(d['would_change']))}</div>")
        if not d.get("answers"):
            rows.append(f"<div class=\"answer\"><span class=\"when\"></span><div><span class=\"oc\">{_sq('open')} "
                        f"{CONCERN_WORDS[None]}</span></div></div>")
        for a in d.get("answers") or []:
            runs = "".join(f" <span class=\"run\">{e(str(r))}</span>" for r in a.get("run_ids") or [])
            word, mark = _concern(a)
            rows.append(f"<div class=\"answer\"><span class=\"when\">{_when(a.get('time'))}</span><div><span "
                        f"class=\"oc\">{_sq(mark)} {e(word)}</span>{e(str(a.get('reason') or ''))}{runs}</div></div>")
        rows.append("</div>")
    return rows


def _finding_view(c, e, fid):
    page, f = c.page, c.finding(fid)
    doubts = sorted([d for d in page["doubts"].values() if d.get("finding_id") == fid],
                    key=lambda d: float(d.get("time") or 0))
    steps, inres = _steps_of(c, f), c.result_of.get(fid)
    first = min([f["time"]] + [w.get("time") for w in f["earlier"]], key=_epoch)
    rows = [_kicker(e, f"Finding {fid}", KIND_WORDS.get(f.get("kind")) or f.get("kind"), f"recorded {_when(first)}",
                    f"reworded {_when(f['time'])}" if f["earlier"] else ""),
            f"<h1>{e(_title(f['statement']))}</h1>"]
    latest = [d["answers"][-1] if d.get("answers") else None for d in doubts]  # the latest answer stands
    chips = [f"<span class=\"chip\">{_sq(f['mark'])} {e(_checks_said(f))}</span>",
             f"<span class=\"chip\">Runs <b>{len(f['run_ids'])}</b></span>",
             f"<span class=\"chip\">Concerns <b>{len(doubts)}</b>{(': ' + e(_concerns_said(latest))) if doubts else ''}"
             "</span>"]
    if f["earlier"]:
        chips.append(f"<span class=\"chip\">Revised <b>{len(f['earlier'])}×</b></span>")
    main = []  # the figure first, then the finding's words and its checks' agreement
    fig = c.figure(f["figure"], f["run_ids"])
    if fig:
        main.append(f"<figure><div class=\"plate\">{_img(fig['file'], f['statement'])}</div>"
                    + _caption(c, e, fig, "Figure", f["statement"]) + "</figure>")
    main += [f"<p class=\"lede\">{e(f['statement'])}</p>", "<div class=\"chips\">" + "".join(chips) + "</div>"]
    checks = _checks_rows(f, e)
    if checks:
        main.append("<div class=\"block\"><h2>Checks</h2>" + "".join(checks) + "</div>")
    beside = _beside_rows(page, e, "finding_id", fid)
    read = _read_line(f["read"], c.names, drafts=c.drafts)
    if beside or read:
        main.append("<div class=\"block\"><h2>Context and references</h2>" + "".join(beside) + read + "</div>")
    if f["replies"]:
        main.append("<div class=\"block\"><h2>Your comments</h2>" + "".join(
            f"<p class=\"reply\"><b>{e(str(r.get('kind')))}</b>: {e(str(r.get('words')))} <span class=\"muted\">"
            f"({_when(r.get('time'))})</span></p>" for r in f["replies"]) + "</div>")
    sk = (c.rf.get(fid) or {}).get("skeptic")
    if isinstance(sk, dict):  # the skeptic's fresh read of the finding: what it checked and how each came out
        checked = [x for x in sk.get("checked") or [] if isinstance(x, dict)]
        raised = len(sk.get("doubt_ids") or [])
        main.append("<div class=\"block\"><h2>The skeptic's check</h2>" + (
            "<div class=\"tbl-wrap\"><table class=\"checks\"><tbody>" + "".join(
                f"<tr><th>{e(str(x.get('check')))}</th><td class=\"o\"><span class=\"oc\">"
                + (f"{_sq('good')} Sound" if x.get("outcome") == "sound" else
                   f"{_sq('part')} Raised a concern ({e(str(x.get('outcome')))})") + "</span></td></tr>"
                for x in checked) + "</tbody></table></div>" if checked else
            f"<p class=\"muted\">The skeptic read this finding and "
            + (f"raised {_plural(raised, 'concern')}, below." if raised else "found no problem.") + "</p>") + "</div>")
    if doubts:
        main.append("<div class=\"block\"><h2>Concerns</h2>"
                    + "".join(_doubt_rows(doubts, e, c.rec.get("doubts_raised_by"))) + "</div>")
    if f["earlier"]:
        main.append("<div class=\"block\"><h2>Revision history</h2>" + "".join(
            f"<p class=\"earlier\">{e(str(w['finding']))} ({_when(w['time'])}"
            f"{' · reason: ' + e(str(w['because'])) if w.get('because') else ''})</p>" for w in f["earlier"])
                    + "</div>")
    if steps:  # each by what it did; its code and output are a click away, in the timeline and the notebook
        main.append("<div class=\"block\"><h2>Analysis steps</h2><div class=\"runs-list\">" + "".join(
            f"<div class=\"run-row\"><span class=\"when\">{_when(x['time'])}</span><div><a href=\"#"
            f"{e(_result_anchor(x))}\">{e(x['what'])}</a></div></div>" for x in steps)
                    + f"</div><p class=\"steps-note\">Each step's code and output: open the step, or the <a href=\""
                      f"{NOTEBOOK_PAGE}\">notebook</a>.</p></div>")
    th = page["threads"].get(str(f.get("thread_id") or ""))
    facts = [("Result", f"<a href=\"#{e(_report_anchor(inres[1]))}\">Result {inres[0]}</a>" if inres else "none yet"),
             ("Thread", f"<a href=\"#{e(_thread_anchor(f['thread_id']))}\">{e(_line(th.get('about'), 80))}</a>"
              if th else e(str(f.get("thread_id") or "none"))),
             ("Runs", str(len(f["run_ids"]))), ("Steps", str(len(steps))), ("ID", e(fid))]
    aside = ("<div class=\"aside-box\"><dl class=\"facts\">" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
             + "</dl>" + _ask(c, e, f"finding {fid}", f["statement"], "Ask about this finding") + "</div>")
    rows.append(f"<div class=\"two\"><div>{''.join(main)}</div>{aside}</div>")
    crumbs = f"{c.name} / Result {inres[0]} / {fid}" if inres else f"{c.name} / Findings / {fid}"
    return _view(e, _finding_anchor(fid), crumbs, rows)


def _findings_view(c, e):
    rows = ["<h1>Findings</h1>",
            "<div class=\"legend\" style=\"margin-top:14px\">" + "".join(
                f"<span>{_sq(k)} {e(v)}</span>" for k, v in MARK_WORDS.items()) + "</div>"]
    if not c.fids:
        rows.append("<p class=\"muted\">No finding recorded yet.</p>")
    for n, (rid, r) in enumerate(c.results, start=1):
        rows.append("<div class=\"section\">" + _head(e, f"Result {n}", _line(r.get("title"), 100))
                    + "<ul class=\"flist\">" + "".join(_flink(c, e, fid) for fid in r.get("finding_ids") or [])
                    + "</ul></div>")
    rest = [fid for fid in c.fids if fid not in c.result_of]
    if rest:
        rows.append("<div class=\"section\">" + _head(e, "Other findings" if c.results else "All findings")
                    + "<ul class=\"flist\">" + "".join(_flink(c, e, fid) for fid in rest) + "</ul></div>")
    page = c.page
    whole = [d for d in page["doubts"].values() if d.get("finding_id") not in page["findings"]]
    if whole:  # doubts on the analysis as a whole, or on a finding the page has not seen
        rows.append("<div class=\"section\">" + _head(e, "General concerns")
                    + "".join(_doubt_rows(whole, e, c.rec.get("doubts_raised_by"))) + "</div>")
    for_questions = [p for p in page["prior"] if p.get("question_id")]
    if for_questions:  # record_knowledge's, for a question of the plan rather than a finding
        rows.append("<div class=\"section\">" + _head(e, "Prior knowledge for plan questions")
                    + "".join(_prior_row(p, e, f"{p['question_id']}: ") for p in for_questions) + "</div>")
    return _view(e, "findings", f"{c.name} / Findings", rows)


def _threads_view(c, e):
    page, names = c.page, c.names
    tree = {str(r.get("thread_id")): r for r in c.rec.get("tree") or [] if isinstance(r, dict)}
    rows = ["<h1>Threads</h1>"]
    if not c.threads:
        rows.append("<p class=\"muted\">No threads yet.</p>")
    for th in c.threads:
        tid = th["thread_id"]
        steps = [x for x in c.steps if str(x.get("thread_id") or "") == tid]
        found = [f for f in c.fids if str(c.finding(f).get("thread_id") or "") == tid]
        rows.append(f"<article class=\"card pad\" id=\"{e(_thread_anchor(tid))}\" "
                    f"data-time=\"{float(th.get('last') or 0):.0f}\">")
        rows.append(f"<p><b>{e(th.get('about') or tid)}</b> <span class=\"status\">{e(str(th.get('status')))}</span> "
                    f"<span class=\"muted\">({e(tid)})</span></p>")
        if th.get("user_words"):
            rows.append(f"<p class=\"reply\">Your request: {e(str(th['user_words']))}</p>")
        node = tree.get(tid) or {}
        why = th.get("why") or (node.get("reason") if node.get("ended") else None)  # the record's, where the page
        if why:  # did not see the call that ended it (a worker's)
            rows.append(f"<p class=\"reply\">{'Closed because' if th.get('status') in THREAD_ENDS else 'Why'}: "
                        f"{e(str(why))}</p>")
        if node.get("read") is False:
            rows.append("<p class=\"reply\">It ended with no run or finding of its own.</p>")
        if node.get("opened_by"):
            rows.append(f"<p class=\"muted\">Opened by {e(_agent(node['opened_by']))}.</p>")
        if th.get("raised_by"):
            rows.append(f"<details data-k=\"raised-{e(tid)}\"><summary>Details</summary><p class=\"reply\">Raised by: "
                        f"{e(str(th['raised_by']))}</p></details>")
        rows += _beside_rows(page, e, "thread_id", tid)
        rows.append(_read_line(th.get("moves") or [], names, "Suggested next steps"))
        if found:
            rows.append("<ul class=\"flist\" style=\"margin-top:10px\">" + "".join(_flink(c, e, f) for f in found)
                        + "</ul>")
        if steps:  # folded, each by what it did and why; its output is in the step, and a failure shows here
            rows.append(f"<details class=\"steps\" data-k=\"steps-{e(tid)}\"><summary>{_plural(len(steps), 'step')}"
                        "</summary><div class=\"runs-list\">" + "".join(
                            f"<div class=\"run-row\"><span class=\"when\">{_when(x['time'])}</span><div><a href=\"#"
                            f"{e(_result_anchor(x))}\">{e(x['what'])}</a>{', ' + e(x['worker']) if x.get('worker') else ''}"
                            + (f" <span class=\"muted\">{e(_line(x['why'], 160))}</span>" if x.get("why") else "")
                            + (f" <span class=\"failed\">{e(_line(x['result'], 160))}</span>"
                               if str(x.get("result") or "").startswith(("error", "failed")) else "")
                            + "</div></div>" for x in steps) + "</div></details>")
        rows.append("</article>")
    return _view(e, "threads", f"{c.name} / Threads", rows)


def _lead_view(c, e):
    """The research lead's reviews, the latest first: each its verdict and story, the main line kept or moved and why,
    the threads it opened, the figures it planned, where a line stopped and its route, and at a complete what it left
    open (the lead's reasons are in its story)."""
    reviews = [r for r in c.rec.get("reviews") or [] if isinstance(r, dict)]
    threads = c.rec.get("threads") if isinstance(c.rec.get("threads"), dict) else {}
    rows = ["<h1>The research lead</h1>",
            "<p class=\"muted\" style=\"margin-top:12px\">The lead reads the record as a whole, judges whether the "
            "results make a story, and opens the threads for what is missing. Its reviews, the latest first.</p>"]
    if not reviews:
        rows.append("<p class=\"muted\">No review yet. The main agent calls the lead when a review is due, and each "
                    "review shows here with its verdict and the threads it opened.</p>")

    def thread(tid):
        about = (threads.get(str(tid)) or {}).get("about")
        return (f"<a href=\"#{e(_thread_anchor(str(tid)))}\">{e(str(tid))}</a>"
                + (f" <span class=\"muted\">{e(_line(about, 100))}</span>" if about else ""))
    for n in range(len(reviews), 0, -1):
        r = reviews[n - 1]
        done = r.get("verdict") == "complete"
        body = [f"<p><b>Review {n}</b> {_sq('good' if done else 'open')} "
                f"{'Complete' if done else 'Not complete yet'} "
                f"<span class=\"muted\">({_when(r.get('time'))})</span></p>",
                f"<p class=\"prose\">{_refs(c, e, str(r.get('story') or ''))}</p>"]
        ml = r.get("main_line") if isinstance(r.get("main_line"), dict) else None
        if ml:
            body.append("<p class=\"reply\"><b>Main line:</b> " + (f"moved to {thread(ml['thread_id'])}" if
                        ml.get("thread_id") else "kept") + f". {e(str(ml.get('why') or ''))}</p>")
        if r.get("threads"):
            body.append("<p><b>Threads it opened:</b></p><ul>" + "".join(f"<li>{thread(t)}</li>" for t in r["threads"])
                        + "</ul>")
        figs = [f for f in r.get("figures") or [] if isinstance(f, dict)]
        if figs:
            body.append("<details data-k=\"lead-figures\"><summary><b>Figures it planned:</b> "
                        f"{len(figs)}</summary><ul>" + "".join(
                            f"<li>{e(str(f.get('claim') or ''))} <span class=\"muted\">("
                            f"{_plural(len(f.get('panels') or []), 'panel')})</span></li>" for f in figs)
                        + "</ul></details>")
        blocks = [b for b in r.get("blocks") or [] if isinstance(b, dict)]
        if blocks:
            body.append("<p><b>Where a line stopped:</b></p><ul>" + "".join(
                f"<li>{e(str(b.get('id')))} <span class=\"muted\">({e(str(b.get('kind') or ''))})</span>: "
                + (f"routed through {thread(b['thread_id'])}. " if b.get("thread_id") else "")
                + f"{e(str(b.get('reason') or ''))}</li>" for b in blocks) + "</ul>")
        if r.get("left_open"):
            body.append("<p><b>Left open at the complete:</b></p><ul>" + "".join(
                f"<li>{e(str(x))}</li>" for x in r["left_open"]) + "</ul>")
        rows.append("<article class=\"card pad\">" + "".join(body) + "</article>")
    return _view(e, "lead", f"{c.name} / Research lead", rows)


def _proposal_table(e, proposal, settled=None):
    """The design auditor's proposal, one row per item: the value and where it comes from, what it decides, what was
    measured or flagged, and, where the server says (settled: the items confirmed), whether it is settled yet."""
    status = settled is not None
    rows = ["<div class=\"tbl-wrap\" style=\"margin-top:16px\"><table><thead><tr><th>Item</th><th>Proposed</th>"
            + ("<th>Settled</th>" if status else "") + "<th>It decides</th><th>Measured or flagged</th></tr></thead>"
            "<tbody>"]
    for x in proposal:
        came = f" <span class=\"muted\">({e(str(x['from']))})</span>" if x.get("from") else ""
        done = (f"<td class=\"o\"><span class=\"oc\">{_sq('good')} yes</span></td>" if x.get("item") in settled else
                f"<td class=\"o\"><span class=\"oc\">{_sq('open')} open</span></td>") if status else ""
        rows.append(f"<tr><th>{e(str(x.get('item')))}</th><td>{e(str(x.get('proposed')))}{came}</td>{done}"
                    f"<td class=\"muted\">{e(str(x.get('decides') or ''))}</td>"
                    f"<td class=\"muted\">{e(' | '.join(map(str, x.get('measured') or [])))}</td></tr>")
    return rows + ["</tbody></table></div>"]


def _design_view(c, e):
    page = c.page
    rows = ["<h1>The design</h1>"]
    d = page.get("design")
    if d and d.get("confirmed"):  # once, not on each item
        rows.append(f"<p class=\"status-line\">{_sq('good')} <span>You confirmed it at {_when(d.get('time'))}.</span></p>")
    now = c.rec.get("design") if isinstance(c.rec.get("design"), dict) else {}  # the design as the server has it
    proposal = (page.get("proposal") or {}).get("items") or []
    if d and d.get("confirmed") and now.get("open"):  # its answers can be taken back: reopened after the form
        rows.append(f"<div class=\"needs\"><b>Reopened</b><p>Open again: {e(', '.join(map(str, now['open'])))}. The "
                    "analysis goes on once these are confirmed.</p></div>")
    if not d:
        rows.append("<p class=\"muted\" style=\"margin-top:14px\">Not confirmed yet.</p>")
        if proposal:  # the form is one line per item; what each decides and what was measured are here
            rows += _proposal_table(e, proposal, now.get("confirmed") if now else None)
    else:
        rows.append("<div class=\"tbl-wrap\" style=\"margin-top:16px\"><table><tbody>")
        for item, value in d["values"].items():
            came = d["origins"].get(item)
            rows.append(f"<tr><th>{e(str(item))}</th><td>{e(str(value))}"
                        + (f"<div class=\"origin\"><details><summary>Source</summary>{e(came)}</details></div>" if came
                           else "") + "</td></tr>")
        rows.append("</tbody></table></div>")
        if not d.get("confirmed"):
            rows.append("<p class=\"muted\">Some of the design is still to be confirmed.</p>")
        if proposal:  # how the auditor proposed it, kept folded beside the design as confirmed
            rows.append("<details data-k=\"design-proposal\"><summary>How the design auditor proposed it</summary>"
                        + "".join(_proposal_table(e, proposal, now.get("confirmed") if now else None)) + "</details>")
    rows += _deposit_rows(page, e)
    return _view(e, "design", f"{c.name} / Design", rows)


NOT_READ = re.compile(r"^(?P<sample>[^:]+): not (?:read|written) \((?P<why>.*)\)(?P<by>.*)$")  # cell_table's notes


def _deposit_rows(page, e):
    """How the deposit is read (docs/adr/0041): which files make which sample and who read them, the samples not
    analysed and why, the files not read, and what the build read, against what any correct reading holds."""
    rows, dep, checks = [], page.get("deposit") or {}, (page.get("reading_checks") or {}).get("facts") or []
    read = dep.get("read") or {}
    if read.get("samples"):
        rows.append("<div class=\"section\">" + _head(e, "Samples and files", f"updated {_when(dep.get('time'))}")
                    + "<div class=\"tbl-wrap\"><table><thead><tr><th>Sample</th><th>Read by</th><th>Cells</th>"
                    "<th>Source files</th></tr></thead><tbody>" + "".join(
                        f"<tr><th>{e(str(n))}</th><td>{e(str(s.get('read_by') or ''))}</td>"
                        f"<td>{e('' if s.get('cells') is None else str(s['cells']))}</td>"
                        f"<td class=\"muted\">{e(', '.join(map(str, s.get('from') or [])))}</td></tr>"
                        for n, s in read["samples"].items() if isinstance(s, dict)) + "</tbody></table></div></div>")
    elif dep.get("samples"):
        rows.append("<div class=\"section\">" + _head(e, "Samples and files", f"updated {_when(dep.get('time'))}")
                    + "<div class=\"tbl-wrap\"><table><thead><tr><th>Sample</th><th>Kind</th><th>Matrix</th></tr>"
                    "</thead><tbody>" + "".join(
                        f"<tr><th>{e(str(s.get('name')))}</th><td>{e(str(s.get('kind') or ''))}</td>"
                        f"<td class=\"muted\">{e(str(s.get('matrix') or ''))}</td></tr>" for s in dep["samples"])
                    + "</tbody></table></div></div>")
    notes = [str(n) for n in read.get("notes") or []]
    left = [m for m in (NOT_READ.match(n) for n in notes) if m]
    if left:
        rows.append("<div class=\"section\">" + _head(e, "Excluded samples", f"{len(left)}") + "<ul>" + "".join(
            f"<li><b>{e(m['sample'])}</b>: {e(m['why'] + m['by'])}</li>" for m in left) + "</ul></div>")
    rest = [n for n in notes if not NOT_READ.match(n)]
    if rest:
        rows.append("<div class=\"section\">" + _head(e, "Notes") + "<ul>"
                    + "".join(f"<li>{e(n)}</li>" for n in rest) + "</ul></div>")
    if dep.get("unreadable"):
        rows.append("<div class=\"section\">" + _head(e, "Unreadable files") + "<ul>" + "".join(
            f"<li><code>{e(str(u.get('file')))}</code>: {e(str(u.get('error') or ''))}</li>" for u in dep["unreadable"])
                    + "</ul></div>")
    if checks:
        rows.append("<div class=\"section\">" + _head(e, "Data checks", "from the build")
                    + "<ul>" + "".join(f"<li>{e(x)}</li>" for x in checks) + "</ul></div>")
    return rows


def _plan_view(c, e):
    """The agent's plan as it stands: each item with what it does, why and how; done, open, or left with why."""
    plan = c.page.get("plan") or {}
    items = plan.get("items") or []
    rows = ["<h1>Analysis plan</h1>"]
    if not items:
        rows.append("<p class=\"muted\" style=\"margin-top:14px\">No plan yet.</p>")
        return _view(e, "plan", f"{c.name} / Plan", rows + _menu_rows(c, e))
    said = "; ".join(str(x) for x in (f"version {plan['version']}" if plan.get("version") else None, plan.get("why"))
                     if x)
    if said:
        rows.append(f"<p class=\"muted\" style=\"margin-top:12px\">{e(said)}</p>")
    if plan.get("next") and any(str(x.get("status") or "open") == "open" for x in items):
        rows.append(f"<p><b>Next:</b> {e(str(plan['next']))}</p>")
    rows.append("<div class=\"tbl-wrap\" style=\"margin-top:12px\"><table><tbody>")
    said = {}  # a reason for leaving items out, by the first item it was given for
    for x in items:
        state = str(x.get("status") or "open")
        runs = len(x.get("runs") or [])
        note = (f"{runs} run{'' if runs == 1 else 's'}" if runs else "") if state != "left" else (
            f"Same reason as {said[x.get('left_why')]}" if x.get("left_why") in said else x.get("left_why"))
        if state == "left" and x.get("left_why"):
            said.setdefault(x["left_why"], str(x.get("id")))
        rows.append(f"<tr><th>{e(str(x.get('id')))}</th><td>{e(str(x.get('does') or ''))}"
                    f"<div class=\"muted\">{e(str(x.get('why') or ''))}</div></td>"
                    f"<td class=\"muted\">{e(str(x.get('how') or ''))}</td><td>{e(PLAN_WORDS.get(state, state))}</td>"
                    f"<td class=\"muted\">{e(str(note or ''))}</td></tr>")
    rows.append("</tbody></table></div>")
    return _view(e, "plan", f"{c.name} / Plan", rows + _menu_rows(c, e))


MENU_ACTIONS = {"unattended": "Selected automatically", "not answered yet": "Waiting for your answer"}


def _menu_rows(c, e):
    """The module menus: each with the analyses code said could run, the picker's picks with their reasons, and what
    was chosen (on the form, or every pick when the run is unattended)."""
    menus = [m for m in c.rec.get("module_menus") or [] if isinstance(m, dict)]
    if not menus:
        return []
    rows = ["<div class=\"section\">" + _head(e, "Suggested analyses", f"{len(menus)}")]
    for m in menus:
        chosen, action = set(m.get("chosen") or []), str(m.get("action") or "not answered yet")
        picks = [p for p in m.get("picks") or [] if isinstance(p, dict)]
        rows.append(f"<article class=\"card pad\"><p><b>{_when(m.get('time'))}</b> <span class=\"status\">"
                    f"{e(MENU_ACTIONS.get(action, action))}</span> <span class=\"muted\">({len(m.get('could_run') or [])} "
                    "available)</span></p><ul class=\"flist\">" + "".join(
                        f"<li>{_sq('good' if p.get('module') in chosen or action == 'unattended' else 'open')}"
                        f"<span><b>{e(c.names.get(p.get('module')) or _analysis(p.get('module')))}</b>: "
                        f"{e(str(p.get('why') or ''))}</span></li>" for p in picks) + "</ul>"
                    + (f"<p class=\"reply\">Your note: {e(str(m['note']))}</p>" if m.get("note") else "") + "</article>")
    rows.append("</div>")
    return rows


def _methods_view(c, e):
    """The methods as the runs state them: each analysis by its module, each run with the packages it called, its test,
    its units and cells, and how its tests were corrected, as its result's diagnostics give them."""
    rows = [_kicker(e, "Generated from the runs"), "<h1>Methods</h1>",
            "<p class=\"muted\" style=\"margin-top:12px\">Packages, tests, units and corrections, as each run's result "
            f"records them. Notebook code is in the timeline and in <a href=\"{NOTEBOOK_PAGE}\">the notebook</a>.</p>"]
    if not c.methods:
        rows.append("<p class=\"muted\">No methods recorded yet.</p>")
    by = {}
    for m in c.methods:
        by.setdefault(str(m.get("module")), []).append(m)
    out = ["<div class=\"methods\">"]
    for module, runs in by.items():
        out.append(f"<h3>{e(c.names.get(module) or _analysis(module))}</h3>")
        for m in runs:
            hour = HOUR.match(str(m.get("run_id") or ""))
            facts = m.get("facts") if isinstance(m.get("facts"), dict) else {}
            said = [f"{k.replace('_', ' ')}: {v}" for k, v in facts.items()]
            calls = [" ".join(str(p.get(k)) for k in ("package", "version") if p.get(k))
                     + (f" ({p['function']})" if p.get("function") else "")
                     for p in m.get("packages") or [] if isinstance(p, dict)]
            out.append(f"<p><span class=\"run\">{hour.group(1) + ':' + hour.group(2) if hour else 'run'}</span> "
                       f"{e(str(m.get('why') or ''))}</p>")
            if calls:
                out.append(f"<p class=\"reply\"><b>Packages</b>: {e('; '.join(calls))}</p>")
            if said:
                out.append(f"<p class=\"reply\">{e('. '.join(said))}</p>")
            if m.get("summary"):
                out.append(f"<p class=\"reply muted\">{e(str(m['summary']))}</p>")
    out.append("</div>")
    return _view(e, "methods", f"{c.name} / Methods", rows + out)


def _ask(c, e, about, title=None, label="Ask about this"):
    """A button that puts an object beside the user's next message to the session (the conversation view); with no
    session to reach (another host, or no helper running) it copies the object's id instead."""
    if not c.link:
        return f"<button class=\"ask\" type=\"button\" data-copy=\"{e(about)}\">Copy reference</button>"
    return (f"<button class=\"ask\" type=\"button\" data-about=\"{e(about)}\" data-title=\"{e(_line(title, 90))}\">"
            f"{e(label)}</button>")


def _conversation_view(c, e):
    """The session's conversation, the user's messages from here or the terminal and each answer, with the box that
    sends the user's words as the session's next turn and Pertura's form while it waits (the page's script fills it
    from the helper)."""
    rows = [_kicker(e, "This Claude Code session"), "<h1>Conversation</h1>"]
    if not c.link:
        rows.append("<p class=\"muted\" style=\"margin-top:12px\">Not connected to a session. Open this project in "
                    "Claude Code with Pertura to write to it from this page; until then, Copy reference copies a "
                    "reference to paste into the chat.</p>")
        return _view(e, "conversation", f"{c.name} / Conversation", rows)
    rows += ["<p class=\"muted\" id=\"talk-state\" style=\"margin-top:12px\">Connecting…</p>",
             "<div class=\"talk\" id=\"talk\"></div>",
             "<form class=\"card form\" id=\"form-box\" hidden><p class=\"src\">Pertura asks</p>"
             "<p class=\"q\" id=\"form-message\"></p><div id=\"form-fields\"></div><div class=\"acts\">"
             "<button class=\"btn primary\" type=\"submit\">Send answer</button>"
             "<button class=\"btn\" type=\"button\" data-action=\"decline\">Decline</button>"
             "<button class=\"btn\" type=\"button\" data-action=\"terminal\">Answer in the terminal</button>"
             "<span class=\"muted\" id=\"form-clock\"></span></div></form>",
             "<form class=\"composer\" id=\"composer\"><div class=\"about\" id=\"about\" hidden></div>"
             "<div class=\"box\"><textarea id=\"say\" rows=\"2\" placeholder=\"Message Pertura\" "
             "aria-label=\"Message\"></textarea><button class=\"btn primary\" type=\"submit\">Send</button></div>"
             "<p class=\"hint\" id=\"sent\">Enter to send · Shift+Enter for a new line</p></form>"]
    return _view(e, "conversation", f"{c.name} / Conversation", rows)


CATALOG = "catalog.json"  # the analyses Pertura runs (scripts/build_the_catalog_of_analyses.py)
CATALOG_FIGURES = "catalog_figures"  # their example figures, copied beside the projects' pages (hooks.install_copies)


def _catalog():
    """The catalog of the analyses beside this file, or None where it cannot be read."""
    try:
        with open(os.path.join(HERE, CATALOG), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _analyses_view(c, e):
    """The analyses Pertura runs, from the catalog: each tool the agent calls, with its question, and each of its
    analyses with the first sentence of its biology, the data it applies to, the package functions it calls and its
    figure, measured on synthetic screens; then the first look's, which code runs."""
    cat = c.catalog or {}
    mods = cat.get("modules") or {}
    rows = [_kicker(e, "Generated from the modules"), "<h1>Analyses</h1>",
            "<p class=\"muted\" style=\"margin-top:12px\">Each tool the agent calls, with its analyses. The package "
            "functions and the example figures come from runs on synthetic screens. Each analysis has its entry in "
            f"<a href=\"{knowledge_page.NAME}\">the knowledge base</a>.</p>"]
    if not mods:
        return _view(e, "analyses", f"{c.name} / Analyses", rows + ["<p class=\"muted\">The plugin holds no catalog."
                                                                     "</p>"])

    def one(name, view=None):
        m = mods.get(name) or {}
        title = m.get("title") or _analysis(name)
        out = [f"<h3>{e(title)}"
               + (f" <span class=\"muted\">· {e(view)}</span>" if view else "") + "</h3>"]
        if m.get("biology"):
            out.append(f"<p>{e(m['biology'])}</p>")
        out.append(f"<p class=\"reply\"><b>Applies to</b>: {e(str(m.get('applies_when') or ''))}</p>")
        if m.get("calls"):
            out.append(f"<p class=\"reply\"><b>Calls</b>: {e('; '.join(m['calls']))}</p>")
        if m.get("figure"):
            src = f"../../{CATALOG_FIGURES}/{m['figure']}"
            out.append(f"<div class=\"plate\"><img src=\"{e(src)}\" alt=\"{e(title)}\" loading=\"lazy\"></div>")
        return out
    out = ["<div class=\"methods analyses\">"]
    for t in cat.get("tools") or []:
        out += [f"<h2>{e(t['tool'])}</h2>", f"<p class=\"muted\">{e(t['answers'])}</p>"]
        for v in t.get("views") or []:
            out += one(v["module"], v["view"])
    if cat.get("first_look"):
        out.append("<h2>The first look, run by code</h2>")
        for name in cat["first_look"]:
            out += one(name)
    out.append("</div>")
    return _view(e, "analyses", f"{c.name} / Analyses", rows + out)


def _step(c, e, x):
    anchor = _result_anchor(x)
    who = [TOOL_WORDS.get(x.get("tool"), "a step")] + ([x["worker"]] if x.get("worker") else [])
    rows = [f"<div class=\"who\">{e(' · '.join(who))}{_on_thread(c.page, x.get('thread_id'), e)} "
            + _ask(c, e, "step " + str(x.get("run_id") or anchor), x["what"], "Ask") + "</div>",
            f"<b>{e(x['what'])}</b><p>{e(_line(x['result'], STEP_SHOWN))}</p>"]
    if x.get("why"):
        rows.append(f"<p class=\"reply\">Why: {e(str(x['why']))}</p>")
    if x["figures"]:
        rows.append("<div class=\"figs\">" + "".join(f"<div class=\"plate\">{_img(n, x['what'])}</div>"
                                                     for n in x["figures"]) + "</div>")
    more = ([f"<p>{e(x['result'])}</p>"] if len(str(x["result"])) > STEP_SHOWN else []) + (
        [f"<p class=\"reply\">Choices: {e(json.dumps(x['choices'], ensure_ascii=False))}</p>"] if x.get("choices") else [])
    if more:
        rows.append(f"<details data-k=\"more-{e(anchor)}\"><summary>Details</summary>{''.join(more)}</details>")
    if x.get("code"):
        rows.append(f"<details data-k=\"code-{e(anchor)}\"><summary>Code</summary><pre><code>{e(x['code'])}</code>"
                    "</pre></details>")
    if x.get("output"):
        rows.append(f"<details data-k=\"out-{e(anchor)}\"><summary>Output</summary><pre>{e(x['output'])}</pre>"
                    "</details>")
    rows.append(_read_line(x.get("read") or [], c.names,
                           "Entries" if x.get("tool") == "read_knowledge" else "Knowledge base", c.drafts))
    failed = str(x.get("result") or "").startswith(("error", "failed"))
    return (f"<div class=\"ev\" id=\"{e(anchor)}\" data-time=\"{float(x.get('time') or 0):.0f}\"><div class=\"when\">"
            f"{_when(x['time'])}</div><div class=\"rail\">{_sq('bad' if failed else 'accent')}</div><div class=\"c\">"
            + "".join(rows) + "</div></div>")


TREE_LANE, TREE_LEFT, TREE_TOP = 22, 250, 30  # px: a lane's height, the labels' column, the time axis


def _tree_timeline(c, e):
    """The analysis as a tree laid across time:
    a lane per thread, from when it opened to when it ended (an open one to the latest event), the main line on top
    in the accent, a fork dashed with its one change, each joined to the lane it grew from; on each lane its steps
    as dots, its findings as larger dots and the results as diamonds, each linked to its entry. An SVG, no script."""
    rows = [x for x in c.rec.get("tree") or [] if isinstance(x, dict) and x.get("thread_id")]
    if not rows:
        return ""
    steps = [x for x in c.steps if float(x.get("time") or 0) > 0]
    own = [fid for fid, f in c.rf.items() if not f.get("thread_id")]
    times = ([_epoch(x.get("opened")) for x in rows] + [float(x["time"]) for x in steps]
             + [_epoch(c.rf[f].get("time")) for f in own]
             + [_epoch(y.get("time")) for x in rows for y in x.get("findings") or []])
    times = [t for t in times if t > 0]
    if not times:
        return ""
    t0, t1 = min(times), max(times)
    minutes = max(1.0, (t1 - t0) / 60)
    scale = max(4.0, min(14.0, 1100 / minutes))  # px a minute: a two-hour run about 1,100 px wide

    def x(t):
        t = _epoch(t) or t1
        return TREE_LEFT + (min(t1, max(t0, t)) - t0) / 60 * scale

    lanes = ([{"thread_id": "root", "about": "The main agent's own steps", "main": True}]
             + [r for r in rows if r.get("main")] + [r for r in rows if not r.get("main")])
    lane_of = {str(r["thread_id"]): i for i, r in enumerate(lanes)}

    def y(i):
        return TREE_TOP + i * TREE_LANE + 10

    width, height = TREE_LEFT + minutes * scale + 24, TREE_TOP + len(lanes) * TREE_LANE + 8
    width += 260 if any(r.get("ended") for r in rows) else 0  # room for an ending's line after the latest lane
    out = []
    every = next((m for m in (5, 10, 15, 30, 60, 120) if minutes / m <= 12), 240)
    for k in range(int(minutes // every) + 1):
        tx = TREE_LEFT + k * every * scale
        out.append(f"<line class=\"tick\" x1=\"{tx:.1f}\" y1=\"{TREE_TOP - 6}\" x2=\"{tx:.1f}\" y2=\"{height - 4}\"/>"
                   f"<text class=\"axis\" x=\"{tx:.1f}\" y=\"{TREE_TOP - 12}\" text-anchor=\"middle\">"
                   f"{e(_when(t0 + k * every * 60))}</text>")
    for i, r in enumerate(lanes):
        tid, ly = str(r["thread_id"]), y(i)
        kind = ("main" if r.get("main") else "fork" if r.get("fork_of") else "was" if r.get("was_main") else "side")
        start = x(r.get("opened")) if tid != "root" else TREE_LEFT
        end = x(r.get("ended")) if r.get("ended") else x(t1)
        if tid != "root":
            py = y(lane_of.get(str(r.get("parent") or "root"), 0))
            out.append(f"<line class=\"join {kind}\" x1=\"{start:.1f}\" y1=\"{py}\" x2=\"{start:.1f}\" y2=\"{ly}\"/>")
        out.append(f"<line class=\"lane {kind}\" x1=\"{start:.1f}\" y1=\"{ly}\" x2=\"{max(end, start + 2):.1f}\" "
                   f"y2=\"{ly}\"/>")
        mark = "★ " if r.get("main") else "⑂ " if r.get("fork_of") else "☆ " if r.get("was_main") else ""
        label = f"{mark}{'' if tid == 'root' else tid + ' '}{_line(str(r.get('about') or ''), 34)}"
        link = f"#{e(_thread_anchor(tid))}" if tid != "root" else "#timeline"
        who = f" · opened by {_agent(r['opened_by'])}" if r.get("opened_by") else ""
        out.append(f"<a href=\"{link}\"><text class=\"label {kind}\" x=\"8\" y=\"{ly + 4}\">{e(label)}"
                   f"<title>{e(str(r.get('about') or '') + who)}</title></text></a>")
        if r.get("ended"):  # the worker's conclusion in a line, or that it ended with nothing of its own read
            said = ("nothing of its own read · " if r.get("read") is False else "") + str(r.get("reason") or "")
            out.append(f"<text class=\"endnote{' bare' if r.get('read') is False else ''}\" x=\"{end + 6:.1f}\" "
                       f"y=\"{ly + 4}\">{e(_line(said, 48))}<title>{e(said)}</title></text>")
        if r.get("fork_of"):
            out.append(f"<text class=\"choice\" x=\"{start + 4:.1f}\" y=\"{ly - 5}\">changes "
                       f"{e(_line(str(r.get('choice') or ''), 40))}</text>")
    for s in steps:
        sy = y(lane_of.get(str(s.get("thread_id") or "root"), 0))
        out.append(f"<a href=\"#{e(_result_anchor(s))}\"><circle class=\"step\" cx=\"{x(s['time']):.1f}\" cy=\"{sy}\" "
                   f"r=\"2.2\"><title>{e(_when(s['time']))} {e(_line(str(s.get('what') or ''), 120))}</title>"
                   "</circle></a>")
    found = [(f, "root") for f in own] + [(y_["id"], str(r["thread_id"])) for r in rows
                                          for y_ in r.get("findings") or [] if y_.get("id") in c.rf]
    for fid, tid in found:
        f = c.rf[fid]
        out.append(f"<a href=\"#{e(_finding_anchor(fid))}\"><circle class=\"finding\" cx=\"{x(f.get('time')):.1f}\" "
                   f"cy=\"{y(lane_of.get(tid, 0))}\" r=\"4.2\"><title>{e(fid)}: "
                   f"{e(_line(str(f.get('statement') or ''), 160))}</title></circle></a>")
    for n, (rid, res) in enumerate(c.results, start=1):
        tids = [str(c.rf.get(f, {}).get("thread_id") or "root") for f in res.get("finding_ids") or []]
        lane = max((lane_of.get(t, 0) for t in tids), key=lambda i: lanes[i].get("depth", -1), default=0)
        rx, ry = x(res.get("time")), y(lane)
        out.append(f"<a href=\"#{e(_report_anchor(rid))}\"><polygon class=\"result\" points=\"{rx:.1f},{ry - 7} "
                   f"{rx + 6:.1f},{ry} {rx:.1f},{ry + 7} {rx - 6:.1f},{ry}\"><title>Result {n}: "
                   f"{e(_line(str(res.get('title') or rid), 160))}</title></polygon></a>")
    return (f"<div class=\"tt\"><svg viewBox=\"0 0 {width:.0f} {height:.0f}\" width=\"{width:.0f}\" "
            f"height=\"{height:.0f}\" role=\"img\" aria-label=\"The analysis as a tree across time: {len(rows)} "
            f"threads, the main line on top\">" + "".join(out) + "</svg></div>")


def _timeline_view(c, e):
    page = c.page
    rows = ["<h1>Timeline</h1>", _tree_timeline(c, e),
            "<p class=\"muted\" style=\"margin-top:12px\">All steps, oldest first.</p>"]
    if not c.steps:
        rows.append("<p class=\"muted\">No steps yet.</p>")
    rows.append("<div class=\"tl\">" + "".join(_step(c, e, x) for x in c.steps))
    n, running = page.get("now"), page.get("running") or []
    if n or running:
        said = (f"Latest step: <b>{e(str(n['what']))}</b>{_on_thread(page, n.get('thread_id'), e)}" if n else "")
        said += "".join(f"<p class=\"reply\">Running: {e(_words(j.get('what')))} (since {_when(j.get('started'))})</p>"
                        for j in running)
        rows.append(f"<div class=\"ev now\"><div class=\"when\">{_when(n['time']) if n else ''}</div><div class=\"rail\">"
                    f"{_sq('accent')}</div><div class=\"c\">{said}</div></div>")
    rows.append("</div>")
    return _view(e, "timeline", f"{c.name} / Timeline", rows)


def _search_data(c):
    out = [{"g": "Findings", "t": _line(c.finding(f)["statement"], 140), "s": f, "h": f"#{_finding_anchor(f)}"}
           for f in c.fids]
    out += [{"g": "Results", "t": str(r.get("title") or rid), "s": f"Result {n}", "h": f"#{_report_anchor(rid)}"}
            for n, (rid, r) in enumerate(c.results, start=1)]
    out += [{"g": "Steps", "t": _line(x["what"], 140), "s": _when(x["time"]), "h": f"#{_result_anchor(x)}"}
            for x in reversed(c.steps)]
    out += [{"g": "Threads", "t": _line(t.get("about") or t["thread_id"], 140), "s": t["thread_id"],
             "h": f"#{_thread_anchor(t['thread_id'])}"} for t in c.threads]
    read = dict.fromkeys(i for x in c.page["results"] for i in x.get("read") or [])
    out += [{"g": "Entries read", "t": c.names.get(i) or i, "s": i, "h": f"{knowledge_page.NAME}#{i}"} for i in read]
    return json.dumps(out, ensure_ascii=False).replace("</", "<\\/")


def _side(c, e):
    state = _state(c)[0]
    opened = sum(1 for t in c.threads if t.get("status") not in THREAD_ENDS)
    groups = [("Report", [("overview", "Overview", "")]
               + [(_report_anchor(rid), f"Result {n}", len(r.get("finding_ids") or []))
                  for n, (rid, r) in enumerate(c.results, start=1)] + [("methods", "Methods", len(c.methods) or "")]),
              ("Analysis", [("findings", "Findings", len(c.fids)), ("threads", "Threads", f"{opened} open"),
                            ("lead", "Research lead", len(c.rec.get("reviews") or []) or ""),
                            ("design", "Design", "confirmed" if (c.page.get("design") or {}).get("confirmed") else ""),
                            ("plan", "Plan", len((c.page.get("plan") or {}).get("items") or []) or "")]),
              ("Session", [("conversation", "Conversation", "live" if c.link else ""),
                           ("timeline", "Timeline", len(c.steps))]),
              ("Pertura", [("analyses", "Analyses", len((c.catalog or {}).get("modules") or {}) or "")])]
    rows = [f"<aside class=\"side\"><a class=\"brand\" href=\"#overview\">{_alien(state, 2)}<span><span class=\"word\">"
            "Pertura</span><br><span class=\"sub\">Perturb-seq analysis</span></span></a>",
            f"<div class=\"proj\"><div class=\"name\">{e(c.name)}</div></div><nav class=\"nav\">"]  # the pill says its state
    for label, items in groups:
        items = [i for i in items if i[0] not in ("lead", "plan") or i[2]]  # these two show once they hold anything
        rows.append(f"<div class=\"nav-label\">{e(label)}</div>")
        rows += [f"<a href=\"#{e(vid)}\" data-view=\"{e(vid)}\"><i class=\"px\"></i><span>{e(text)}</span>"
                 f"<span class=\"count\">{e(str(count))}</span></a>" for vid, text, count in items]
    rows.append(f"<a href=\"{NOTEBOOK_PAGE}\"><i class=\"px\"></i><span>Notebook ↗</span></a>"
                f"<a href=\"{knowledge_page.NAME}\"><i class=\"px\"></i><span>Knowledge base ↗</span></a></nav>")
    rows.append(f"<div class=\"side-foot\">Updates automatically every {RELOAD_SECONDS} s.</div></aside>")
    return "".join(rows)


def render(page, title, record=None, methods=None, link=None, due=None):
    """The page's HTML: the overview, each result and finding, the findings, threads, design, plan and methods, and the
    timeline, one view at a time, with the sidebar, the search and the session's pill. It reads the page's state, the
    record as the server gives it (pertura.page_view), the runs' methods and what is pending (due); it computes nothing
    about the screen."""
    e = html.escape
    for k, v in empty().items():
        page.setdefault(k, v)
    c = _Page(page, record, methods, title, link, due)
    c.catalog = _catalog()
    state, line = _state(c)
    waits = _waits(c) if state == "waiting" else []  # while the user is waited on, the pill names what for
    last = waits[0][0] if waits else c.steps[-1]["what"] if c.steps else "No steps yet"
    views = [_overview(c, e)]
    views += [_report(c, e, n, rid, r) for n, (rid, r) in enumerate(c.results, start=1)]
    views += [_finding_view(c, e, fid) for fid in c.fids]
    views += [_findings_view(c, e), _threads_view(c, e), _lead_view(c, e), _design_view(c, e), _plan_view(c, e),
              _methods_view(c, e),
              _conversation_view(c, e), _timeline_view(c, e), _analyses_view(c, e)]
    return "\n".join([
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{e(title)}</title><script>{HEAD_SCRIPT}</script><style>{_asset(STYLE)}</style></head><body>",
        "<div class=\"app\">", _side(c, e), "<div class=\"main\"><header class=\"top\">"
        "<button class=\"iconbtn menu-btn\" id=\"menu\" aria-label=\"The screen's parts\">≡</button>"
        f"<div class=\"crumbs\" id=\"crumbs\">{e(c.name)}</div>"
        "<button class=\"search\" id=\"search\" aria-label=\"Search\"><svg class=\"ico\" width=\"14\" height=\"14\" "
        "viewBox=\"0 0 16 16\" aria-hidden=\"true\"><circle cx=\"7\" cy=\"7\" r=\"5\" fill=\"none\" stroke=\"currentColor\" "
        "stroke-width=\"1.6\"/><path d=\"M11 11l3.5 3.5\" stroke=\"currentColor\" stroke-width=\"1.6\" "
        "stroke-linecap=\"round\"/></svg><span>Search findings, results, steps</span>"
        "<kbd>Ctrl K</kbd></button><button class=\"iconbtn\" id=\"theme\" aria-label=\"Light or dark\">◐</button>"
        "</header><main class=\"page\">", *views, "</main></div></div>",
        f"<a class=\"pill\" href=\"#timeline\">{_alien(state, 2)}<span><span class=\"l1\">"
        + (f"Step {len(c.steps)} at {_when(c.page['now']['time'])}" if state == "still" and c.page.get("now")
           else f"{e(line)}, step {len(c.steps)}")
        + f"</span><span class=\"l2\">{e(_line(last, 90))}</span></span></a>",
        "<div class=\"overlay\" id=\"palette\" hidden><div class=\"palette\"><input id=\"q\" type=\"search\" "
        "placeholder=\"Findings, results, steps, threads, entries read\" aria-label=\"Search\"><div class=\"res\" "
        "id=\"res\"></div></div></div>",
        "<div class=\"overlay\" id=\"lightbox\" hidden><div class=\"lb-bar\"><a class=\"btn\" id=\"lb-full\" "
        "target=\"_blank\" rel=\"noopener\">Open full size</a><button class=\"btn\" type=\"button\">Close</button></div>"
        "<img alt=\"\"></div>",
        f"<script type=\"application/json\" id=\"search-data\">{_search_data(c)}</script>",
        f"<script>window.PERTURA_LINK = {json.dumps(c.link)};</script>" if c.link else "",
        f"<script>{NEW_SCRIPT}{_asset(SCRIPT).replace('RELOAD_MS', str(RELOAD_SECONDS * 1000))}</script>",
        "</body></html>"]) + "\n"


def _lines(text):
    return str(text or "").splitlines(keepends=True)


def notebook(page):
    """The steps in order as a Jupyter notebook (nbformat 4): before each, a line of what it did, when, by whom, on
    which thread and why, with the figures it returned by their files; a cell's code with its output; a module's
    run with its result in a line."""
    cells = []
    for x in reversed(page.get("results") or []):
        head = f"**{x['what']}** ({_when(x['time'])}{', ' + x['worker'] if x.get('worker') else ''})"
        tid = x.get("thread_id")
        if tid:
            head += f", on thread {tid}: {(page['threads'].get(str(tid)) or {}).get('about') or ''}".rstrip(": ")
        if x.get("why"):
            head += f"\n\nWhy: {x['why']}"
        if x.get("code") is None:
            head += f"\n\n{x['result']}"
        head += "".join(f"\n\n![{n}]({FIGURES}/{n})" for n in x.get("figures") or [])
        cells.append({"cell_type": "markdown", "metadata": {}, "source": _lines(head)})
        if x.get("code") is not None:
            out = [{"output_type": "stream", "name": "stdout", "text": _lines(x["output"])}] if x.get("output") else []
            cells.append({"cell_type": "code", "execution_count": None, "metadata": {}, "source": _lines(x["code"]),
                          "outputs": out})
    return {"cells": cells, "nbformat": 4, "nbformat_minor": 4,
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                         "language_info": {"name": "python"}}}


BUILTIN_NAMES = frozenset(dir(builtins))
STRING_TOKENS = frozenset([tokenize.STRING] + [getattr(tokenize, n) for n in ("FSTRING_START", "FSTRING_MIDDLE",
                                                                              "FSTRING_END") if hasattr(tokenize, n)])


def _highlight(code):
    """A cell's code as HTML with its keywords, strings, comments, numbers, builtins and defined names marked for
    colour, by Python's own tokenizer; code it cannot read (R, a broken cell) as plain text."""
    e = html.escape
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, SyntaxError):
        return e(code)
    starts = [0]
    for line in code.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def at(rc):
        return min(starts[min(rc[0] - 1, len(starts) - 1)] + rc[1], len(code))
    out, done, before = [], 0, None
    for t in tokens:
        kind = ("c" if t.type == tokenize.COMMENT else "s" if t.type in STRING_TOKENS else
                "m" if t.type == tokenize.NUMBER else None)
        if t.type == tokenize.NAME:
            kind = ("k" if keyword.iskeyword(t.string) else "f" if before in ("def", "class") else
                    "b" if t.string in BUILTIN_NAMES else None)
        a, b = at(t.start), at(t.end)
        if kind and a >= done and b > a:
            out += [e(code[done:a]), f"<span class=\"{kind}\">{e(code[a:b])}</span>"]
            done = b
        if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
            before = t.string
    return "".join(out) + e(code[done:])


def notebook_page(page, title):
    """The notebook's steps as a page a browser shows, laid out as a notebook: before each cell the line of what it
    did, when, by whom, on which thread and why; then its code (In) and its output and figures (Out), or a module
    run's result. A step that failed shows its last line, its code and error folded under it."""
    e = html.escape
    steps = list(reversed(page.get("results") or []))
    cells = []
    for n, x in enumerate(steps, start=1):
        failed = str(x.get("result") or "").startswith(("error", "failed"))
        meta = [_when(x["time"])] + ([] if x.get("tool") == "run_code" else [TOOL_WORDS.get(x.get("tool"), "a step")])
        meta += [x["worker"]] if x.get("worker") else []
        head = [f"<div class=\"nb-meta\">{e(' · '.join(meta))}{_on_thread(page, x.get('thread_id'), e, PAGE_NAME)}"
                f"</div><h3>{_sq('bad') + ' ' if failed else ''}{e(x['what'])}</h3>"]
        if x.get("why"):
            head.append(f"<p class=\"why\">Why: {e(str(x['why']))}</p>")
        if x.get("code") is None or failed:
            head.append(f"<p class=\"said\">{e(_line(x.get('result'), 400) if failed else str(x.get('result') or ''))}</p>")
        rows = []
        if x.get("code") is not None:
            rows.append(f"<div class=\"nb-row\"><div class=\"nb-prompt\">In [{n}]:</div>"
                        f"<pre class=\"nb-code\"><code>{_highlight(x['code'])}</code></pre></div>")
        if x.get("output") or x.get("figures"):
            figs = ("<div class=\"nb-figs\">" + "".join(
                f"<a class=\"plate\" href=\"{FIGURES}/{e(f)}\" target=\"_blank\" rel=\"noopener\">{_img(f, x['what'])}</a>"
                for f in x.get("figures") or []) + "</div>") if x.get("figures") else ""
            out = (f"<pre class=\"nb-output{' nb-error' if failed else ''}\">{e(x['output'])}</pre>"
                   if x.get("output") else "")
            rows.append(f"<div class=\"nb-row out\"><div class=\"nb-prompt\">{'Out:' if x.get('code') is not None else ''}"
                        f"</div><div>{out}{figs}</div></div>")
        body = "".join(rows)
        if failed and body:
            body = f"<details class=\"nb-more\"><summary>The code and the error</summary>{body}</details>"
        cells.append(f"<section class=\"nb-cell{' failed' if failed else ''}\" id=\"{e(_result_anchor(x))}\">"
                     f"<div class=\"nb-md\">{''.join(head)}</div>{body}</section>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{e(title)} · Notebook</title><script>{HEAD_SCRIPT}</script><style>{_asset(STYLE)}</style></head>"
            f"<body><main class=\"page\"><p><a href=\"{PAGE_NAME}\">← {e(title)}</a></p>"
            + _kicker(e, _plural(len(steps), "step")) + "<h1>Notebook</h1>"
            "<p class=\"muted\" style=\"margin-top:12px\">All steps, oldest first: each cell's code and output, each "
            "analysis's result; a step that failed shows its last line, its code and error folded. "
            f"<a href=\"{NOTEBOOK_NAME}\">{NOTEBOOK_NAME}</a> holds the same steps for Jupyter.</p>"
            "<div class=\"nb\">" + ("".join(cells) or "<p class=\"muted\">No steps yet.</p>") + "</div></main></body>"
            "</html>\n")


def write(folder, page, title, record=None, methods=None, link=None, due=None):
    """Write the page whole, so a browser never reads half of it, and the notebook beside it; returns its path.
    record and methods: the server's view of the record (pertura.page_view) and the runs' methods, as the hook last
    fetched them; due: what is pending, by decider, as the last main-agent reply named it."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, PAGE_NAME)
    for name, text in ((PAGE_NAME, render(page, title, record, methods, link, due)),
                       (NOTEBOOK_NAME, json.dumps(notebook(page), ensure_ascii=False, indent=1) + "\n"),
                       (NOTEBOOK_PAGE, notebook_page(page, title))):
        target = os.path.join(folder, name)
        tmp = f"{target}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        replace_file(tmp, target)
    try:
        knowledge_page.write_if_old(folder, used=_used(page))
    except Exception:  # the knowledge page never stops the user's page
        pass
    return path


def stamp(now=None):
    """A name for a call's images, unique enough within a project and sortable."""
    t = time.time() if now is None else now
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(t)) + f"{int((t % 1) * 1000):03d}"


def safe(text):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(text))[:120]
