"""The map of the screen (docs/build_goal_session6_fixes.md, item 27): its objects at any level (perturbations,
genes, groups of perturbations, programmes, states), the signed relations between them with the findings behind
each, and what is not yet explained. It is kept by code from the record: each finding names the relations it shows
(relates: [{from, to, sign, measured}]), prior knowledge the relations it knows (prior's relates), the open threads
and doubts are what is not yet explained. A relation a finding shows is seen unless the finding's checks disagree or
a doubt on it weakened or overturned it; a relation only prior knowledge gives is known, not tested here."""
LEVELS = ("perturbation", "gene", "group", "programme", "state", "other")
SIGNS = ("+", "-")


def check_relates(relates):
    """The problems of a finding's or a prior's relates, or []."""
    out = []
    for r in relates or []:
        if not isinstance(r, dict) or not r.get("from") or not r.get("to"):
            out.append("a relation names what it goes from and to ({from, to, sign, measured})")
        elif r.get("sign") not in SIGNS:
            out.append(f"a relation's sign is + (raises, drives) or - (lowers, represses), not {r.get('sign')!r}")
        else:
            for end in ("from_level", "to_level"):
                if r.get(end) not in (None,) + LEVELS:
                    out.append(f"{end} is one of {', '.join(LEVELS)}")
    return out


def _weakened(st, fid):
    return any(d.get("finding_id") == fid and (d.get("answer") or {}).get("outcome") in ("weakens", "overturns")
               for d in st["doubts"].values())


def build(st):
    """{objects: [{id, level}], relations: [{from, to, sign, measured, status, findings, prior}], unexplained}."""
    from . import record as rc
    rel, levels = {}, {}

    def put(r, status, finding=None, prior=None):
        key = (str(r["from"]), str(r["to"]), r["sign"])
        x = rel.setdefault(key, {"from": key[0], "to": key[1], "sign": key[2], "measured": [], "status": "known",
                                 "findings": [], "prior": []})
        if r.get("measured"):
            x["measured"].append(str(r["measured"]))
        if finding:
            x["findings"].append(finding)
            x["status"] = "seen" if status == "seen" or x["status"] == "seen" else status
        if prior:
            x["prior"].append(prior)
        for end, lev in (("from", r.get("from_level")), ("to", r.get("to_level"))):
            if lev:
                levels[str(r[end])] = lev
    earlier = rc.previous_screen_findings(st)
    for fid, f in st["findings"].items():
        said = [r["kind"] for r in f.get("replies") or [] if r["kind"] in ("drop", "keep")]
        if said and said[-1] == "drop":  # the user's latest word on it: drop
            continue
        status = ("on the previous screen" if fid in earlier else
                  "not seen" if rc.checks_disagree(f) or _weakened(st, fid) else "seen")
        for r in f.get("relates") or []:
            put(r, status, finding=fid)
    for k in st["knowledge"]:
        for r in k.get("relates") or []:
            put(r, "known", prior=f"{k.get('source')}")
    objects = sorted({x for key in rel for x in key[:2]})
    unexplained = [f"{t['thread_id']}: {t.get('about')}" for t in (st.get("threads") or {}).values()
                   if t.get("status") in ("open", "followed")]
    unexplained += [f"doubt {did}: {d['text']}" for did, d in st["doubts"].items()
                    if not d.get("answer") or d["answer"]["outcome"] == "still open"]
    return {"objects": [{"id": o, "level": levels.get(o, "other")} for o in objects],
            "relations": list(rel.values()), "unexplained": unexplained}


def lines(m, shown=12):
    """The map in a few lines, for a reply."""
    out = [f"{r['from']} {'raises' if r['sign'] == '+' else 'lowers'} {r['to']}: {r['status']}"
           + (f" ({'; '.join(r['measured'][:2])})" if r["measured"] else "")
           + (f" [{', '.join(r['findings'][:3])}]" if r["findings"] else " [prior knowledge]")
           for r in m["relations"][:shown]]
    if len(m["relations"]) > shown:
        out.append(f"and {len(m['relations']) - shown} more relations: read_record with what map")
    return out


def draw(m, path):
    """The map as a figure: the objects by level, top to bottom (perturbations, genes, groups, programmes, states),
    each relation an arrow (raises) or a bar end (lowers), solid where seen, dashed where tested and not seen, grey
    where only prior knowledge gives it. Drawn in the modules' style and checked (figures.check_figure), its layout
    issues kept beside it (<name>.checks.json). Returns the path, or None when the map has no relation."""
    if not m["relations"]:
        return None
    import json
    import os
    from .question_modules import figures
    plt = figures._plt()
    rows = {lev: [o["id"] for o in m["objects"] if o["level"] == lev] for lev in LEVELS}
    rows = {lev: ids for lev, ids in rows.items() if ids}
    place = {}
    for y, (lev, ids) in enumerate(rows.items()):
        for x, oid in enumerate(ids):
            place[oid] = ((x + 1) / (len(ids) + 1), 1 - (y + 0.5) / len(rows))
    fig, ax = plt.subplots(figsize=(figures.SINGLE, 0.9 + 0.9 * len(rows)))
    ax.set_axis_off()
    for r in m["relations"]:
        (x0, y0), (x1, y1) = place[r["from"]], place[r["to"]]
        colour = "#1d1d1f" if r["status"] in ("seen", "not seen") else "#9a9aa0"
        style = "--" if r["status"] in ("not seen", "on the previous screen") else "-"
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>" if r["sign"] == "+" else "-[", color=colour, linestyle=style,
                                    shrinkA=14, shrinkB=14, lw=1.2, connectionstyle="arc3,rad=0.08"))
        if r["measured"]:
            ax.text((x0 + x1) / 2, (y0 + y1) / 2, r["measured"][0], fontsize=6, ha="center", va="center",
                    color=colour, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))
    for oid, (x, y) in place.items():
        ax.text(x, y, oid, fontsize=7, ha="center", va="center", fontfamily="Arial",
                bbox=dict(boxstyle="round,pad=0.3", fc="#f2f2f5", ec="#6e6e73", lw=0.6))
    issues = figures._save(plt, fig, path, "The screen's objects and their relations")
    with open(os.path.splitext(path)[0] + ".checks.json", "w", encoding="utf-8") as fh:
        json.dump({"layout_issues": issues}, fh)
    return path
