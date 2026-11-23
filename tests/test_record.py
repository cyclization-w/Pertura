"""The shared record (pertura.record): event checks, replay, the critic's query, the switches and the plan's rules.
Step 3's bars 2, 3, 4 and 5 (docs/build_goal_adr0020.md)."""
import pytest

from pertura import record as rc


def node(v):
    return {"value": v, "status": "confirmed"}


def schmidt_design():
    return {"schema": "pertura.design/v1",
            "experiment": {"conditions": node(["stimulated", "resting"]), "context": node("primary_in_vitro")},
            "perturbation": {"modality": node("CRISPRa"),
                             "controls": node([{"kind": "non_targeting",
                                                "guides": [f"NO-TARGET-{i}" for i in range(1, 9)]}])},
            "units": {"independent_unit": node("donor"), "unit_identification": node("genotype_demultiplexing"),
                      "n_units": node(2)}}


def crisprai_design():
    return {"schema": "pertura.design/v1",
            "experiment": {"conditions": node([]), "context": node("cell_line")},
            "perturbation": {"modality": node("CRISPRa and CRISPRi"), "target_map": node("feature_reference"),
                             "controls": node([{"kind": "non_targeting", "guides": [f"NTC-{i}-a" for i in range(1, 7)]
                                                + [f"NTC-{i}-i" for i in range(1, 7)]}])},
            "units": {"independent_unit": node("none"), "unit_identification": node("lanes"), "n_units": node(6)}}


# the hand lists of docs/build/03_plan_rules_and_hand_lists.md, without D's choice of reference, F's units agree and
# F's null of the right unit, which no longer go on every screen's plan (docs/adr/0022, sections 4 and 5)
SCHMIDT = {"A": ["what was done"],
           "B": ["guide calls and their evidence", "efficacy", "dose", "abundance"],
           "C": ["any effect", "genes", "programs", "states and composition"],
           "D": ["common response"],
           "E": ["similar targets", "condition x perturbation"],
           "F": ["guides agree", "findings checked", "selection on outcomes"],
           "G": ["meaning"], "H": ["what next"]}
CRISPRAI = {"A": ["what was done"],
            "B": ["guide calls and their evidence", "vector structure", "efficacy", "escape", "dose", "abundance"],
            "C": ["any effect", "genes", "programs", "states and composition"],
            "D": ["common response"],
            "E": ["similar targets", "opposite modes", "combinations"],
            "F": ["guides agree", "findings checked", "selection on outcomes"],
            "G": ["meaning"], "H": ["what next"]}


def as_lists(plan):
    out = {}
    for q in plan:
        out.setdefault(q["letter"], []).append(q["item"])
    return out


def test_the_plans_equal_the_hand_lists():
    assert as_lists(rc.plan_questions(schmidt_design(), {"n_targets": 73, "guides_per_target": 2, "pairs": False})) \
        == SCHMIDT
    assert as_lists(rc.plan_questions(crisprai_design(), {"n_targets": 19, "guides_per_target": 4, "pairs": True})) \
        == CRISPRAI


def test_modalities_read_from_a_text_naming_several():
    assert rc.modalities(crisprai_design()) == {"crispra", "crispri"}
    assert rc.modalities(schmidt_design()) == {"crispra"}


def _filled(tmp_path):
    rec = rc.Record(tmp_path / "rec")
    rec.plan(schmidt_design(), {"n_targets": 73, "guides_per_target": 2, "pairs": False})
    rec.append("run_logged", {"run_id": "r1", "what": "cells per guide", "code": "python x.py", "inputs": ["cells"]},
               rc.TOOL)
    rec.append("run_finished", {"run_id": "r1", "state": "done", "output": "table", "figure": "f.png"}, rc.TOOL)
    rec.append("run_read", {"run_id": "r1", "reading": "every target has cells in both donors"}, rc.AGENT)
    return rec


def test_events_are_checked():
    rec_events = [("run_logged", {"run_id": "r", "what": "w", "code": "c", "inputs": []}, rc.AGENT)]
    st = rc.empty()
    assert any("written by the tools" in p for p in rc.problems(*rec_events[0][:2], rec_events[0][2], st))
    st["questions"]["A.what_was_done"] = {"status": None}
    p = rc.problems("question_closed", {"question_id": "A.what_was_done", "status": "detected"}, rc.AGENT, st)
    assert any("needs its evidence" in x for x in p)
    p = rc.problems("question_closed", {"question_id": "A.what_was_done", "status": "not_pursued",
                                        "reason": "out of scope"}, rc.AGENT, st)
    assert p == []  # the agent may leave a question, with its reason (docs/adr/0030)
    p = rc.problems("question_closed", {"question_id": "A.what_was_done", "status": "not_pursued"}, rc.AGENT, st)
    assert any("needs a reason" in x for x in p)


def test_replay_gives_the_same_state(tmp_path):
    rec = _filled(tmp_path)
    rec.append("question_closed", {"question_id": "A.what_was_done", "status": "detected", "evidence": ["r1"],
                                   "figure": "f.png"}, rc.AGENT)
    again = rc.Record(tmp_path / "rec")
    assert again.state_hash() == rec.state_hash()
    # switched_off comes from the record's switches, not its events (the lead and the figure reviewer off by
    # default, docs/adr/0052)
    assert rc.reduce(rec.events()) == {k: v for k, v in again.state().items() if k != "switched_off"}
    assert again.state()["questions"]["A.what_was_done"]["status"] == "detected"


def test_the_critic_finds_the_planted_gaps_and_nothing_else(tmp_path):
    # docs/adr/0032 (D7): a question without a status, a run without a reading and the user's question are no longer
    # obligations, so the critic no longer lists them; the planted gaps of those kinds stay planted, unlisted. The lead
    # and the figure reviewer are the ablation's since docs/adr/0052: switched on here, the critic lists them too
    rec = rc.Record(tmp_path / "rec", {"research_lead": True, "figure_review": True})
    for i in range(10):  # 10 questions: 5 closed, 5 left open (5 gaps)
        rec.append("question_planned", {"question_id": f"q{i}", "letter": "C", "item": f"item {i}",
                                        "because": "always"}, rc.PLAN)
    for i in range(10):  # 10 runs: 5 with a reading; 3 without a reading, 2 without output (5 gaps)
        rec.append("run_logged", {"run_id": f"r{i}", "what": f"run {i}", "code": "c", "inputs": []}, rc.TOOL)
        if i < 8:
            rec.append("run_finished", {"run_id": f"r{i}", "state": "done", "output": "o", "figure": "f"}, rc.TOOL)
        if i < 5:
            rec.append("run_read", {"run_id": f"r{i}", "reading": "read"}, rc.AGENT)
    for i in range(5):
        rec.append("question_closed", {"question_id": f"q{i}", "status": "detected", "evidence": ["r0"],
                                       "figure": "f"}, rc.AGENT)
    for i in range(8):  # 8 findings: 3 checked and agreeing; 3 whose checks have not run, 2 whose guides
        # disagree (5 gaps)
        rec.append("finding_recorded", {"finding_id": f"f{i}", "question_id": "q0", "target": "T", "feature": "g",
                                        "direction": "up", "population": "all", "run_ids": ["r0"],
                                        "kind": "gene"}, rc.AGENT)
        if 3 <= i < 6:
            continue
        rec.append("check_recorded", {"finding_id": f"f{i}", "check": "lead", "outcome": "agrees",
                                      "reason": "the design's lead"}, rc.TOOL)
        rec.append("check_recorded", {"finding_id": f"f{i}", "check": "guides_agree",
                                      "outcome": "agree" if i < 3 else "disagree"}, rc.TOOL)
    for i in range(8):  # 8 doubts: 3 with a branch, 5 without (5 gaps)
        rec.append("doubt_raised", {"doubt_id": f"d{i}", "question_id": "q0", "text": f"doubt {i}",
                                    "would_change": "the sign"}, rc.AGENT)
        if i < 3:
            rec.append("branch_opened", {"branch_id": f"b{i}", "doubt_id": f"d{i}", "plan": "test it"}, rc.AGENT)
    found = {(x["kind"], x["id"]) for x in rec.critic()}
    planted = ({("run without output", f"r{i}") for i in (8, 9)}
               | {("finding whose checks have not run", f"f{i}") for i in range(3, 6)}
               | {("finding whose checks disagree", f"f{i}") for i in (6, 7)}
               | {("doubt without an answer", f"d{i}") for i in range(3, 8)})  # the skeptic reads what results rest on
    assert len(planted) == 12 and found == planted  # the research lead, due once findings were in, is retired
    gone = {"question without a status", "run without a reading", "the user's question not recorded",
            "no paradigm chosen"}
    assert not {k for k, _ in found} & gone
    assert rec.blocks_report() == []  # the critic lists; nothing blocks the report (docs/adr/0022, section 13)


def test_switches_remove_their_pieces(tmp_path):
    off = rc.Record(tmp_path / "off", switches={"record": False})
    assert off.append("fact_recorded", {"key": "k", "value": 1, "source": "s"}, rc.TOOL) is None
    assert {k: v for k, v in off.state().items() if k != "switched_off"} == rc.empty()
    noplan = rc.Record(tmp_path / "noplan", switches={"plan_and_critic": False})
    assert noplan.plan(schmidt_design(), {"n_targets": 73, "guides_per_target": 2}) == []
    assert noplan.state()["questions"] == {} and noplan.critic() == [] and noplan.blocks_report() == []
    lean = rc.Record(tmp_path / "lean", switches={"robustness": False, "skeptic": False})
    assert not lean.enabled("robustness") and not lean.enabled("skeptic") and lean.enabled("record")
    assert rc.Record(tmp_path / "lean").enabled("robustness") is False  # kept with the record's folder
    with pytest.raises(rc.RecordError):
        rc.Record(tmp_path / "bad", switches={"nonsense": False})


def test_a_closed_question_judged_not_sufficient_is_listed(tmp_path):
    r = rc.Record(str(tmp_path / "rec"))
    r.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes", "because": "always"},
             rc.PLAN)
    with pytest.raises(rc.RecordError):  # only a closed question is checked
        r.append("sufficiency_checked", {"question_id": "C.genes", "sufficient": False, "reasons": ["x"]}, rc.AGENT)
    r.append("question_closed", {"question_id": "C.genes", "status": "cannot_answer", "reason": "no units"},
             rc.AGENT)
    r.append("sufficiency_checked", {"question_id": "C.genes", "sufficient": False,
                                     "reasons": ["the design has units"]}, rc.AGENT)
    items = [i for i in r.critic() if i["id"] == "C.genes"]
    assert items == [{"kind": "question closed but judged not sufficient", "id": "C.genes",
                      "what": "the design has units"}]
    assert r.blocks_report() == []  # it is listed, not blocking


def test_a_findings_checks_are_the_tools_and_the_agent_adds_its_own(tmp_path):
    rec = rc.Record(tmp_path / "rec")
    rec.append("question_planned", {"question_id": "q", "letter": "C", "item": "genes", "because": "always"}, rc.PLAN)
    rec.append("finding_recorded", {"finding_id": "f", "question_id": "q", "target": "T", "feature": "g",
                                    "direction": "up", "population": "all", "run_ids": [], "kind": "gene",
                                    "cells": "tumour"}, rc.AGENT)
    assert rec.state()["findings"]["f"]["kind"] == "gene" and rec.state()["findings"]["f"]["cells"] == "tumour"
    with pytest.raises(rc.RecordError, match="run by the tool"):
        rec.append("check_recorded", {"finding_id": "f", "check": "guides_agree", "outcome": "agree"}, rc.AGENT)
    with pytest.raises(rc.RecordError, match="names its run"):
        rec.append("check_recorded", {"finding_id": "f", "check": "own", "outcome": "agrees"}, rc.AGENT)
    with pytest.raises(rc.RecordError, match="needs a reason"):
        rec.append("check_recorded", {"finding_id": "f", "check": "half", "outcome": "not run"}, rc.TOOL)
    rec.append("check_recorded", {"finding_id": "f", "check": "lead", "outcome": "its way, not significant",
                                  "reason": "the design's lead", "numbers": {"test": "wilcoxon", "q": 0.3}}, rc.TOOL)
    rec.append("check_recorded", {"finding_id": "f", "check": "own", "outcome": "agrees", "run_id": "r1"}, rc.AGENT)
    f = rec.state()["findings"]["f"]
    assert f["checks"]["lead"]["numbers"]["test"] == "wilcoxon" and f["checks"]["own"]["by"] == rc.AGENT
    assert rc.checks_ran(f) and not rc.checks_disagree(f)
    rec.append("check_recorded", {"finding_id": "f", "check": "other_test", "outcome": "other direction"}, rc.TOOL)
    assert rc.disagreement(rec.state()["findings"]["f"]) == "other_test: other direction"


def test_the_question_the_paradigm_and_the_forks_are_recorded(tmp_path):
    # docs/adr/0030: the user's question verbatim, the paradigm with its card's version, each fork with who decided;
    # docs/adr/0032 (D8): the paradigm is the agent's choice, a key fork the user's words relayed, and neither the
    # question nor the paradigm is an open item
    rec = rc.Record(tmp_path / "rec")
    rec.plan(schmidt_design(), {"n_targets": 73, "guides_per_target": 2, "pairs": False})
    assert ("the user's question not recorded", "question") not in {(x["kind"], x["id"]) for x in rec.critic()}
    with pytest.raises(rc.RecordError):
        rec.append("question_asked", {"text": "  "}, rc.USER)
    rec.append("question_asked", {"text": "which regulators set the IL-2 response?"}, rc.USER)
    st = rec.state()
    assert st["question"]["text"] == "which regulators set the IL-2 response?" and st["question"]["by"] == rc.USER
    assert st["question"]["time"]
    assert ("no paradigm chosen", "paradigm") not in {(x["kind"], x["id"]) for x in rec.critic()}
    rec.append("paradigm_chosen", {"paradigm_id": "P1", "version": 1, "why": "programs and their regulators"},
               rc.AGENT)
    assert (rec.state()["paradigm"]["paradigm_id"], rec.state()["paradigm"]["by"]) == ("P1", rc.AGENT)
    assert not {x["kind"] for x in rec.critic()} & {"no paradigm chosen", "the user's question not recorded"}
    with pytest.raises(rc.RecordError, match="key fork"):
        rec.append("fork_decided", {"fork_id": "P1.4", "choice": "the IL-2 programs", "key": True}, rc.AGENT)
    with pytest.raises(rc.RecordError, match="user_words"):  # relayed, but without the user's words
        rec.append("fork_decided", {"fork_id": "P1.4", "choice": "the IL-2 programs", "key": True,
                                    "relayed": True}, rc.AGENT)
    with pytest.raises(rc.RecordError, match="says why"):
        rec.append("fork_decided", {"fork_id": "P1.1", "choice": "K = 30"}, rc.AGENT)
    rec.append("fork_decided", {"fork_id": "P1.1", "choice": "control cells, K by stability", "default": True},
               rc.AGENT)
    rec.append("fork_decided", {"fork_id": "P1.4", "choice": "the IL-2 programs", "key": True,
                                "options": ["the IL-2 programs", "all programs"], "relayed": True,
                                "user_words": "only the IL-2 ones, please"}, rc.AGENT)
    rec.append("fork_decided", {"fork_id": "P1.1", "choice": "K = 30", "why": "the user asked for finer programs"},
               rc.AGENT)
    forks = rec.state()["forks"]
    assert [d["choice"] for d in forks["P1.1"]] == ["control cells, K by stability", "K = 30"]  # the latest stands
    assert forks["P1.4"][-1]["relayed"] and forks["P1.4"][-1]["key"]
    assert forks["P1.4"][-1]["user_words"] == "only the IL-2 ones, please"
    again = rc.Record(tmp_path / "rec")
    assert again.state() == rec.state()


def test_a_branch_comes_from_a_doubt_a_fork_or_the_users_request(tmp_path):
    rec = rc.Record(tmp_path / "rec")
    rec.append("question_planned", {"question_id": "q0", "letter": "C", "item": "genes", "because": "always"},
               rc.PLAN)
    rec.append("doubt_raised", {"doubt_id": "d1", "question_id": "q0", "text": "one capture", "would_change": "x"},
               rc.AGENT)
    rec.append("branch_opened", {"branch_id": "b1", "doubt_id": "d1", "plan": "by capture"}, rc.AGENT)
    with pytest.raises(rc.RecordError, match="no fork"):
        rec.append("branch_opened", {"branch_id": "b2", "origin": "fork", "fork_id": "P1.4", "plan": "p"}, rc.AGENT)
    rec.append("fork_decided", {"fork_id": "P1.4", "choice": "states", "key": True}, rc.USER)
    rec.append("branch_opened", {"branch_id": "b2", "origin": "fork", "fork_id": "P1.4", "plan": "programs instead"},
               rc.AGENT)
    with pytest.raises(rc.RecordError, match="request"):
        rec.append("branch_opened", {"branch_id": "b3", "origin": "user", "plan": "p"}, rc.AGENT)
    rec.append("branch_opened", {"branch_id": "b3", "origin": "user", "request": "try the states first",
                                 "plan": "P3 from the start"}, rc.AGENT)
    st = rec.state()
    assert [st["branches"][b]["origin"] for b in ("b1", "b2", "b3")] == ["doubt", "fork", "user"]
    assert st["doubts"]["d1"]["branches"] == ["b1"]


def test_a_run_records_the_paradigm_step_it_serves(tmp_path):
    rec = rc.Record(tmp_path / "rec")
    rec.append("run_logged", {"run_id": "r1", "what": "programs", "code": "c", "inputs": [], "step": "P1.1"}, rc.TOOL)
    rec.append("run_logged", {"run_id": "r2", "what": "cells per guide", "code": "c", "inputs": []}, rc.TOOL)
    runs = rec.state()["runs"]
    assert runs["r1"]["step"] == "P1.1" and runs["r2"]["step"] is None


def test_the_skeptic_is_due_when_a_lead_is_proposed(tmp_path):
    # docs/adr/0030: at the chain's leads, before the user chooses which go to validation (the figure review, due with
    # it, is retired with the figure reviewer, 2026-11-20; a switch naming a retired role is ignored)
    rec = rc.Record(tmp_path / "rec", {"research_lead": True, "figure_review": True})
    rec.append("question_planned", {"question_id": "q0", "letter": "C", "item": "genes", "because": "always"},
               rc.PLAN)
    rec.append("run_logged", {"run_id": "r1", "what": "the map", "code": "c", "inputs": []}, rc.TOOL)
    rec.append("run_finished", {"run_id": "r1", "state": "done", "output": "o"}, rc.TOOL)
    rec.append("figure_saved", {"figure_id": "r1:figure", "run_id": "r1", "paths": {"png": "x.png"},
                                "caption": "the map"}, rc.TOOL)
    rec.append("finding_recorded", {"finding_id": "f1", "question_id": "q0", "target": "T", "feature": "g",
                                    "direction": "up", "population": "all", "run_ids": ["r1"], "kind": "gene"},
               rc.AGENT)
    due = {(i["kind"], i["id"]) for i in rec.critic()}
    assert ("skeptic due", "f1") not in due
    rec.append("dossier_proposed", {"dossier_id": "d1", "title": "t", "lead_findings": ["f1"], "main_run": "r1",
                                    "map_size": {"targets": 9}}, rc.AGENT)
    due = {(i["kind"], i["id"]) for i in rec.critic()}  # once proposed, not at the end (the fresh review of 11-13, #2)
    assert ("skeptic due", "f1") in due and not {k for k, _ in due} & {"figure review due", "research lead due"}
    assert not rec.state()["chosen"]
