"""The shared record of an analysis (docs/adr/0020, section 3): the facts, the plan, every run, the findings with
their evidence, and the open doubts, held outside any one agent's context.

Events are appended to SQLite (events.db, pertura.events_store) and never edited; the state is rebuilt by
replaying them, so the same events always give the same state.

Events and their required fields:
- fact_recorded (key, value, source)
- question_planned (question_id, letter, item, because): the plan; the agent or the user may add one, saying why
- question_closed (question_id, status, reason, evidence, figure): a status from STATUSES. detected, not_detected
  and not_enough_data need evidence (run IDs) and a figure; cannot_answer and not_pursued need a reason. The user
  marks a question not pursued on a form, or the agent leaves it with its reason (docs/adr/0030)
- plan_written (items, and why): the agent's plan, written after the screen's map and again when it changes (with
  why): each item {id, does, why, how, and cards, status "done" with done_by for an item done without a run}. An item
  keeps its id across versions; one left out of a later version is left, with that version's why. Code holds the
  agent to it and never writes it (plan_items, plan_next)
- question_asked (text, and relayed): the user's question, in their words, before any result (docs/adr/0030):
  from the user's form, or relayed by the agent (relayed true)
- paradigm_chosen (paradigm_id, version, and inserted, why): the paradigm card whose route the analysis follows,
  with the card's version, and the cards inserted at a step ([{paradigm_id, at}]). Since docs/adr/0032 it is the
  agent's choice, said in chat; a card is a reference, not a chain
- fork_decided (fork_id, choice, and step_id, options, why, key, default, relayed, user_words): one fork of a card's
  route. A key fork is the user's: asked in chat, and relayed by the agent with the user's words (docs/adr/0032), or
  on a form in an earlier record; any other is the agent's, the card's default (default true) or another choice
  with its reason. A fork decided again keeps the earlier decisions; the latest stands
- run_logged (run_id, what, code, inputs, question_id, uses, step): written by the tools that make runs (actor
  "tool"), never by an agent remembering to; uses: the objects the run takes (docs/adr/0027, pertura.flow); step:
  the item of the agent's plan the run serves (plan_written), or, in an earlier record, a paradigm card's step
- run_finished (run_id, state, output, figure, uses): the tools; a notebook cell's uses are known when it ends (the
  objects whose files it read and those behind the names it reads)
- run_read (run_id, reading): the agent's reading of a run's output, the run's fourth part
- finding_recorded (finding_id, run_ids, and statement, figure, question_id, target, feature, direction,
  population, effect): a finding in a sentence with the runs it rests on and its figure (docs/adr/0032, D6). Its
  checks run when it also names a target, a feature and a direction; a technical finding may name none
- reply_recorded (kind, words, and finding_id, relayed): the user's reply to a finding (or, for enough and knowledge,
  to the analysis), in their words, as the agent relays it: one of REPLY_KINDS. Knowledge is also prior knowledge;
  enough ends the analysis when the agent writes the report
- check_recorded (finding_id, check, outcome, run_id, reason, numbers): one of a finding's checks (CHECKS,
  docs/adr/0025): the tool writes the lead and each check the design chooses when the finding is recorded
  (pertura.finding_checks), each in its own terms; "not run" with the reason where the data do not allow it; the agent
  may add its own ("own", with its run)
- doubt_raised (doubt_id, text, would_change, and finding_id, question_id): what result would change the
  judgement, on a finding (finding_id), on a question (question_id), or on the analysis as a whole
- doubt_answered (doubt_id, outcome, reason, and run_ids): the doubt's answer, recorded on the doubt: one of
  DOUBT_OUTCOMES, its reason and the runs it rests on; a later answer replaces it, and the earlier ones are kept
- finding_reworded (finding_id, reason, and REWORDED): a finding changed in place (docs/adr/0032, amendment 1):
  each earlier wording is kept with its time and the reason it changed. The user's replies and the checks stay;
  a new target, feature or direction is another claim, so the earlier checks go with the earlier wording. Runs
  it adds are new evidence
- branch_opened (branch_id, plan, and origin, doubt_id, fork_id, request, alternatives, base, measures) and
  branch_closed (branch_id, outcome, run_ids, and comparison): a branch tests a doubt (origin doubt, the default),
  re-opens a fork (origin fork), follows the user's request to try another approach (origin user, with the request),
  or tries the other ways at a choice the main agent went ahead on (origin alternatives, docs/adr/0032, D9: each
  alternative a name and its one change from the base, and the measures every way reports; closing it keeps the
  comparison, one row per way)
- robustness_planned (finding_id, plan_id, primary, variants), robustness_result (finding_id, plan_id, analysis,
  effect, low, high, outcome) and robustness_verdict (finding_id, plan_id, verdict, reason, counts): written by the
  robustness engine only (pertura_research.retired.robustness); a result needs its plan, recorded first, to name its analysis
- sufficiency_checked (question_id, sufficient, reasons): the fresh-context check of a closed question
  (agents/sufficiency-check.md), as the main agent reports it
- knowledge_recorded (question_id, source, item, text): something from outside the data (a gene set, an enrichment,
  a paper). The state tags every such item "prior knowledge", whoever wrote it, so it stays apart from evidence
- dossier_proposed (dossier_id, title, lead_findings, main_run, map_size): a result the main agent proposes to
  deepen, one that summarises many targets or genes; map_size is measured by the tool from the main run's table
- dossier_chosen (dossier_ids, and relayed, user_words): the user's choice of 1-5 proposed dossiers, the only ones a
  report shows; asked in chat and relayed with the user's words (docs/adr/0032), or on a form in an earlier record
- dossier_part_recorded (dossier_id, part, text, and run_ids, finding_ids, knowledge_ids, kinds, figure as the part
  needs): one of a dossier's parts (DOSSIER_PARTS); a later one of the same part replaces it
- figure_saved (figure_id, run_id, paths, caption, and role, checks): a figure the run drew, entered by the tools
  (docs/adr/0021, section 2): a notebook cell's save_figure, or a module's figure when its run ends. paths holds
  the png, and the pdf and data tables where written. Saving the same figure_id again makes a new version, which
  needs its own review
- figure_reviewed (figure_id, verdict, issues): the fresh-context figure review (agents/figure-reviewer.md) of the
  figure's current version, as the main agent reports it: ok with no issues, or fix with at least one
- figure_issue_answered (figure_id, issue, answer): the main agent's reasoned answer to one issue (its number, from
  1) of the figure's latest review, when the figure is not changed for it
- difference_noted (finding_id, source, text): the finding differs from the original paper or from prior knowledge in
  the record (source: DIFFERENCE_SOURCES)
- skeptic_checked (finding_ids, doubt_ids, checked): the skeptic's fresh-context check of these findings, with what it
  checked on each and how each came out (agents/skeptic.md),
  with the doubts it raised (none when it found no problem), as the main agent reports it
- advice_recorded (result_id, standings, thread_ids): the advisor's reading of a result (agents/advisor.md,
  docs/adr/0042): each relation's standing against the literature (STANDINGS), with the pathways its genes are known
  for, the pathways a new one links and its sources, each looked up in this session; and the threads it opened
  (raised_by ADVISOR) for the substantiation and the routes around a block it asks for, listed in the lead's coverage
  and the report until answered
- cell_set_saved (name, run_id, cells, calls, and reason, per_sample, per_label, relabelled, base): a named set of the
  screen object's cells the agent's run saved, checked and kept by the tools (pertura.cell_sets, docs/adr/0024)
- calling_rule_confirmed (cell_set, answer, and description, note): the user's answer, on the design form, to a cell
  set that decides guide calls; analyses use such a set once the answer is confirm
- block_called (call_id, block, choices, cells, and targets, reference, n, kept_cells, check_of, check, notebook,
  run_id, job): one call of a block in a notebook (pertura.blocks), entered by the tool that ran the cell
- object_registered (object_id, kind, run_id, path, and uses, columns, rows, note): an object an analysis made, by the
  tools (docs/adr/0027): the base (the deposit, the design, the screen object; no run), a module run's table, a
  table or file a notebook cell wrote. Registering an ID again makes a new version; nothing about it is refused
- thread_opened (thread_id, about, raised_by, and kind, evidence, strength, moves, matters, tells_apart): a thread
  (docs/adr/0035, D1), something a result or the design raises, in a sentence with its numbers (about), and what
  raised it (raised_by: a run, a finding, a design key, or the user's words). Code opens threads from measured
  patterns (pertura.threads: strength orders them, evidence holds the numbers, moves the knowledge base's entries
  for such a thread); the agent, the user and the skeptic open their own. matters: why the user or the field cares;
  tells_apart: what result would tell its explanations apart; neither is scored
- thread_changed (thread_id, status, reason): a thread's status (THREAD_STATUSES), with its reason: followed (the
  agent takes it, and why), explained, contradicted, out of data, set aside, or open again. Each change is kept.
  A finding recorded with a thread_id joins that thread. Explained names the findings that hold its result
  (finding_ids, which join it; by default those that joined), except a thread of alternatives, which ends with its
  comparison (docs/adr/0038, D4)

The critic is a query over the state (critic). It lists; it never blocks (docs/adr/0022, section 13), and the report
states what remains. Since docs/adr/0032 nothing in it is an obligation of the flow: a question without a status, a
run without a reading, a missing question or paradigm are no longer listed. It lists findings whose checks have not
run or disagree, doubts without a branch, runs without output, and for each chosen result (a dossier) a part missing,
a main figure covering
fewer than MAP_TARGETS targets and MAP_GENES genes, a lead finding with no sensitivity check or one that holds under no
analysis choice, and each of its figures not in the record. The skeptic is due when code says so (docs/adr/0022,
section 12), for each finding that leads a result, holds under some methods and not others, or differs from the paper
or prior knowledge, until it has checked it since its latest evidence, once each result is proposed (docs/adr/0047,
A6). The figure reviews left with the figure reviewer (2026-11-20). Only the design gate blocks: no effect analysis
before the design is confirmed.

Switches (switches.json in the record's folder, set when it is made) turn pieces off for ablations: "record" (no
event is written and the state stays empty), "plan_and_critic" (no plan, no open items), "robustness" and "skeptic"
(read by those pieces through enabled()). The four roles once switched off by default (the advisor, the complete
check, the research lead and the figure reviewer) moved to pertura_research.retired.roles_switched_off on 2026-11-20;
a record's switches that name them are ignored, and the events they wrote still fold, so their records still open.
"""
import json
import os
import re
import threading
import time
import uuid

from . import design_gate
from .events_store import _db, _rows, _tx, state_hash

_SYNCING = threading.Lock()  # Record.sync_runs, one at a time in a process

STATUSES = ("detected", "not_detected", "not_enough_data", "cannot_answer", "not_pursued")
ANSWERED = ("detected", "not_detected", "not_enough_data")
CHECKS = ("lead", "guides_agree", "tests", "other_test", "calibration", "depth_matched", "half", "deposit_calls",
          "own")  # other_test and depth_matched: in records written before the open points' F5
# a finding's checks (docs/adr/0025, section 3); "robustness" is read in records the robustness engine wrote
CHECKLIST = CHECKS + ("robustness",)
DISAGREE = ("disagree", "other direction")  # a check's outcomes that disagree with the finding
# what a finding's checks need beside its target, feature and direction: its kind (design_fit.KINDS), a gene set's
# genes, a state's labelling (state_run), its cell set, its reference, the block call it came from, and a score's
# table as the finding read it (a copy, so a table saved again under its name changes no finding's checks)
FINDING_PARTS = ("kind", "genes", "state_run", "cells", "reference", "block_call", "targets", "score_table")
LEAD_AGAINST = ("other direction", "other way, not significant", "no difference")  # the lead not showing its way
# the checklist before docs/adr/0022 (section 4): units agreeing, the shared response, the null of the unit and tries
# are no longer asked of every finding
EARLIER_CHECKLIST = ("guides_agree", "units_agree", "beyond_shared_response", "null_of_right_unit", "robustness",
                     "tries")
DIFFERENCE_SOURCES = ("the paper", "prior knowledge")
# the advisor (docs/adr/0042): what its threads are raised by, and a relation's standings against the literature
ADVISOR = "the advisor"
KNOWN, NEW, AGAINST = "reproduces what is known", "new as far as the literature shows", "against what is known"
STANDINGS = (KNOWN, NEW, AGAINST, "a check", "a restatement")
PIECES = ("record", "plan_and_critic", "robustness", "skeptic")
# the roles switched off by default since 2026-11-17 (one simple loop; d2's counts: 72% of the lead's reviews said "not
# yet", and 13 of the figure reviewer's 314 reviews said ok), moved to research on 2026-11-20: a switch naming one is
# ignored, so a workspace that still passes it opens
RETIRED_SWITCHES = ("advisor", "complete_check", "research_lead", "figure_review")
DOSSIER_PARTS = ("effect", "specificity", "checks", "convergence", "mechanism", "relations",
                 "panel_and_paragraph", "validation")
MAP_TARGETS, MAP_GENES = 5, 20
TOOL, AGENT, USER, PLAN = "tool", "agent", "user", "plan"
EVENTS = {
    "fact_recorded": ("key", "value", "source"),
    "question_planned": ("question_id", "letter", "item", "because"),
    "question_closed": ("question_id", "status"),
    "run_logged": ("run_id", "what", "code", "inputs"),
    "run_finished": ("run_id", "state", "output"),
    "run_read": ("run_id", "reading"),
    "run_claimed": ("run_id",),
    "finding_recorded": ("finding_id", "run_ids"),
    "reply_recorded": ("kind", "words"),
    "check_recorded": ("finding_id", "check", "outcome"),
    "doubt_raised": ("doubt_id", "text", "would_change"),
    "doubt_answered": ("doubt_id", "outcome", "reason"),
    "finding_reworded": ("finding_id", "reason"),
    "branch_opened": ("branch_id", "plan"),
    "branch_closed": ("branch_id", "outcome", "run_ids"),
    "robustness_planned": ("finding_id", "plan_id", "primary", "variants"),
    "robustness_result": ("finding_id", "plan_id", "analysis", "effect", "outcome"),
    "robustness_verdict": ("finding_id", "plan_id", "verdict", "reason", "counts"),
    "sufficiency_checked": ("question_id", "sufficient", "reasons"),
    "knowledge_recorded": ("question_id", "source", "item"),
    "dossier_proposed": ("dossier_id", "title", "lead_findings", "main_run", "map_size"),
    "dossier_chosen": ("dossier_ids",),
    # a result of the report (docs/build_goal_session6_fixes.md, item 9): its title (its conclusion), the findings it
    # rests on and its figure, drawn from their runs; proposed by the agent, confirmed or dropped by the user
    "result_reported": ("result_id", "title", "finding_ids", "figure_run"),
    "result_decided": ("result_id", "decision", "user_words"),
    "dossier_part_recorded": ("dossier_id", "part", "text"),
    "figure_saved": ("figure_id", "run_id", "paths", "caption"),
    "figure_reviewed": ("figure_id", "verdict", "issues"),
    "figure_issue_answered": ("figure_id", "issue", "answer"),
    "difference_noted": ("finding_id", "source", "text"),
    "skeptic_checked": ("finding_ids", "doubt_ids"),
    "advice_recorded": ("result_id", "standings", "thread_ids"),
    "cell_set_saved": ("name", "run_id", "cells", "calls"),
    "calling_rule_confirmed": ("cell_set", "answer"),
    "block_called": ("call_id", "block", "choices", "cells"),
    "object_registered": ("object_id", "kind", "run_id", "path"),
    "question_asked": ("text",),
    "paradigm_chosen": ("paradigm_id", "version"),
    "fork_decided": ("fork_id", "choice"),
    "thread_opened": ("thread_id", "about", "raised_by"),
    # a new build of the screen object (docs/build_goal_session6_fixes.md, item 2): the build before it, and whether
    # any cell's label, unit or condition changed (what: which targets, whether the control cells did, the counts)
    "screen_rebuilt": ("build_id", "previous", "changed"),
    "thread_changed": ("thread_id", "status", "reason"),
    # the main line moved to a thread, with why and by whom (the research lead, or the user in their words): the
    # analysis is a tree whose main line can be switched
    "main_line_moved": ("thread_id", "why", "by"),
    # in records from before 2026-11-13: a later run of the module a finding rests on, with another value of one of
    # its choices (111 of 118 such events in three runs were no one choice changed; nothing writes it now)
    "choice_tried": ("finding_id", "choice", "run_id"),
    "plan_written": ("items",),
    # the report's opening (the user's decision of 2026-11-02): a title sentence and how the results lead from one to
    # the next
    "story_written": ("text",),
    # the research lead's review (docs/adr/0036): its verdict against a paper-level story, the story so far, the
    # threads it opened for the gaps and its plan of the report's figures
    "lead_reviewed": ("verdict", "story"),
    # the figure reviewer's review of the report's figures as a set, against the lead's plan
    "figures_reviewed_as_a_set": ("verdict", "issues"),
    # the module menu (agreed 2026-11-05): the modules that can run now as code read them, the module picker's picks
    # with its reasons, and the user's choice among them; each run chosen is a run of its own
    "module_menu_given": ("modules",),
    "module_picks_recorded": ("picks",),
    "module_menu_answered": ("offered", "chosen"),
    # the parts of the user's question (the loop decided by the record, 2026-11-20): each a span of a user's message,
    # taken at that message's first reading; a part closes on findings, results or runs, or as not answerable with a
    # reason. Nothing writes them since 2026-11-21 (the agent's todo list replaced them; pertura_research.retired.
    # question_parts_taken_by_the_recorder): kept so older records still reduce
    "question_parts_recorded": ("message_uuid", "parts"),
    "part_closed": ("part_id",),
    # the main agent's "No choice: <reason>" line: no threshold or definition of its own could change an answer
    "no_choice_said": ("reason",),
}
# an item of the agent's plan as the state keeps it, and the runs' states that leave an item open
PLAN_ITEM_FIELDS = ("id", "does", "why", "how", "cards", "status", "done_by")
NOT_DONE = ("running", "failed", "stopped", "error", "timeout")
# a thread's statuses (docs/adr/0035, D1): open until the agent takes it (followed) or it ends, with a reason
THREAD_STATUSES = ("open", "followed", "explained", "contradicted", "out of data", "set aside")
THREAD_FIELDS = ("thread_id", "about", "raised_by", "kind", "evidence", "strength", "moves", "matters",
                 "tells_apart", "user_words", "relayed", "joins", "test", "on_line", "step", "reads", "structure",
                 "next_of", "fork_of", "choice", "opened_by", "proposed_as", "tries")
# what a thread's step is (2026-11-07, P15): of the threads that follow a finding, 68% checked it again, and the
# levels counted figures to draw; a line's depth counts its new questions alone (new_questions_behind)
NEW_QUESTION = "new question"
STEPS = (NEW_QUESTION, "check", "new area", "report work")
BREADTH = "breadth"  # a thread that reads one kind of conclusion of the design across the screen (P6, P17)
# where a line stops, and what a scientist does next (docs/adr/0043, part B; 2026-11-07, P16): the kinds of block,
# whose routes the follow-up moves card gives, and "not a block" for a line set aside for its interest, not its data.
# Lines ended at their blocks: "a state shift cannot be told from a graded change", "no guide lowers EGR1"
BLOCK_KINDS = ("the readout does not measure it", "two things cannot be told apart", "pooled", "too few",
               "confounded by the design", "data quality", "a reference missing", "not a block")
THREAD_ENDS = ("explained", "contradicted", "out of data", "set aside")  # a thread's statuses that end its line
SCREEN_THREADS = ("replicates", "guides", "cells")  # the first look's threads about the whole screen
RESULT_DECISIONS = ("confirmed", "dropped")  # the user's decision on a proposed result
ORIGINS = ("doubt", "fork", "user", "alternatives")  # where a branch comes from (docs/adr/0030, 0032)
# the user's replies to a finding (docs/adr/0032, D6); "reread" is a re-reading of what the finding means
REPLY_KINDS = ("keep", "doubt", "deepen", "reread", "drop", "knowledge", "enough", "main_line", "next_step")
# a finding's fields as the state keeps them; only finding_id and run_ids are required (docs/adr/0032, D6)
FINDING_FIELDS = ("finding_id", "question_id", "target", "feature", "direction", "population", "run_ids",
                  "statement", "figure", "relates")
# what a rewording may change, and the part of a finding its checks test
REWORDED = ("statement", "figure", "effect", "target", "feature", "direction", "run_ids", "relates", "kind", "genes",
            "state_run", "block_call", "cells", "reference", "targets", "score_table")  # a description given its claim
# and kind
# becomes one
CLAIM = ("target", "feature", "direction")
# what a doubt's test showed of its finding, or a limit stated with no run: the doubts a result's reading does not
# turn on are listed in the report as its limits, not each tested (each answer once named a run, and the loop of
# doubts, runs and findings held 43-50% of the calls after the first result; 2026-11-07, P3)
LIMIT = "stated as a limit"
NARROWED = "holds, narrowed"  # the finding stands in a narrower form, which the answer's reason says
# the finding reworded to claim less, its doubt not tested: open, as one still open is, since the data could answer it
# (GSE190604 on 2b6a17a: thirteen rewordings, refused as narrowed without a run, were re-sent as limits of the design)
REWORDED_ONLY = "reworded, not tested"
OPEN_ANSWERS = ("still open", REWORDED_ONLY)
DOUBT_OUTCOMES = ("holds", NARROWED, "weakens", "overturns", "still open", REWORDED_ONLY, LIMIT)
# what leaves a doubt stated as a limit (docs/adr/0045, amendment 1): the design cannot answer it; no tool or notebook
# code here does it (a missing package is the user's question). A check here could answer one left for time: it stays
# still open (no agent is shown the time or spend, and on 01f3de3 'budget' closed checks of seconds at under 30% of the
# caps). An answer recorded before has none, and reads as it did
LIMIT_KINDS = ("design", "tool")
LEAD_VERDICTS = ("complete", "not yet")  # the research lead's verdict against a paper-level story (docs/adr/0036)
VERDICTS = ("ok", "fix")  # the figure reviewer's
# a figure, and the report's figures as a set, are due for review once; the issues not fixed stay with them and the
# report states them (reviews that went round took a third of a run's main-agent calls, the fair comparison's problems
# list #25; at two each, figure reviews were still 4-7 launches a run, 2026-11-07, P4). A review is allowed again,
# never due again, for a newer version or where the user asks (docs/adr/0049, D5)
REVIEWS_AT_MOST = 1
SET = "the set"  # the figure ID of the report's figures reviewed as a set
MISSING = "missing:"  # how a set review's issue begins when a planned figure or panel is not drawn at all
# a thread the main agent opens for something it sees in the first look's maps (docs/adr/0039, D3): the main line is
# the one it follows, workers probe the others, and each ends with its evidence or is set aside with its reason
OBSERVATION = "observation"
ROLES =("overview", "detail", "evidence", "limitation")  # a tile's role in a figure set (docs/adr/0021)
MAIN_ROLES = ("overview", "detail")  # in the report's main figure; the others go to its supplementary figure
# what each dossier part must point to, beyond its text
PART_NEEDS = {"effect": ("run_ids",), "specificity": ("run_ids",), "convergence": ("run_ids", "kinds"),
              "mechanism": ("knowledge_ids",), "relations": ("finding_ids",), "panel_and_paragraph": ("figure",),
              "validation": ("run_ids",)}
PRIOR = "prior knowledge"
TOOL_ONLY = ("run_logged", "run_finished", "run_claimed", "robustness_planned", "robustness_result", "robustness_verdict",
             "figure_saved", "cell_set_saved", "block_called", "object_registered", "screen_rebuilt")


class RecordError(Exception):
    pass


def empty():
    return {"facts": {}, "questions": {}, "order": [], "runs": {}, "findings": {}, "doubts": {}, "branches": {},
            "knowledge": [], "dossiers": {}, "chosen": [], "figures": {}, "cell_sets": {}, "block_calls": {},
            "objects": {}, "question": None, "asked": [], "paradigm": None, "paradigms": [], "forks": {},
            "replies": [], "enough": None, "story": None, "threads": {}, "results": {}, "builds": {}, "build": None,
            "plan": None, "reviews": [], "set_reviews": [], "module_menus": [], "main_line": None, "main_line_moves": []}


def apply(st, e):
    kind, p = e["event_type"], e["payload"]
    if kind == "fact_recorded":
        st["facts"][p["key"]] = {"value": p["value"], "source": p["source"], "seq": e["seq"]}
    elif kind == "question_planned":
        st["questions"][p["question_id"]] = {"question_id": p["question_id"], "letter": p["letter"],
                                             "item": p["item"], "because": p["because"], "by": e["actor"],
                                             "status": None, "evidence": [], "figure": None, "reason": None}
        st["order"].append(p["question_id"])
    elif kind == "question_closed":
        q = st["questions"][p["question_id"]]
        q.update(status=p["status"], evidence=list(p.get("evidence") or []), figure=p.get("figure"),
                 reason=p.get("reason"), closed_by=e["actor"], user_words=p.get("user_words"))
    elif kind == "run_logged":
        st["runs"][p["run_id"]] = {"run_id": p["run_id"], "what": p["what"], "code": p["code"],
                                   "inputs": p["inputs"], "question_id": p.get("question_id"),
                                   "job_id": p.get("job_id"), "uses": list(p.get("uses") or []),
                                   "step": p.get("step"), "state": "running", "output": None, "figure": None,
                                   "reading": None, "build": p.get("build"), "cells_rule": p.get("cells_rule"),
                                   "why": p.get("why"),
                                   "thread_id": p.get("thread_id"), "worker": p.get("worker"), "seq": e["seq"],
                                   "source": p.get("source")}
    elif kind == "run_finished":
        st["runs"][p["run_id"]].update(state=p["state"], output=p["output"], figure=p.get("figure"))
        if p.get("uses") is not None:
            st["runs"][p["run_id"]]["uses"] = list(p["uses"])
    elif kind == "run_read":
        st["runs"][p["run_id"]]["reading"] = p["reading"]
    elif kind == "run_claimed":  # an agent's call took a run of the same comparison: the agent's too, with its step,
        # thread and why where the run had none; who started it stays said (docs/adr/0045, D2 and amendment 1)
        run = st["runs"][p["run_id"]]
        run["claimed"] = True
        run.update({k: p[k] for k in ("step", "thread_id", "why") if p.get(k) and not run.get(k)})
    elif kind == "finding_recorded":
        st["findings"][p["finding_id"]] = {**{k: p.get(k) for k in FINDING_FIELDS}, "effect": p.get("effect"),
                                           **{k: p.get(k) for k in FINDING_PARTS},
                                           "checks": {}, "robustness": {}, "differences": [], "replies": [],
                                           "evidence_seq": e["seq"], "skeptic": None, "wordings": [], "seq": e["seq"],
                                           "time": e.get("timestamp"), "thread_id": p.get("thread_id"),
                                           "choices": list(p.get("choices") or [])}
        th = st.setdefault("threads", {}).get(p.get("thread_id"))
        if th is not None:
            th["findings"].append(p["finding_id"])
            if th["status"] == "open":  # a finding on an open thread: the agent has taken it
                th["status"] = "followed"
    elif kind == "reply_recorded":
        r = {"kind": p["kind"], "words": p["words"], "finding_id": p.get("finding_id"),
             "relayed": bool(p.get("relayed", True)), "by_analysis": bool(p.get("by_analysis")), "by": e["actor"],
             "time": e.get("timestamp"), "seq": e["seq"],
             **{k: p[k] for k in ("result_id", "thread_id", "option") if p.get(k) is not None}}  # a next_step's pick
        st["replies"].append(r)
        if p.get("finding_id"):
            st["findings"][p["finding_id"]]["replies"].append(r)
        if p["kind"] == "enough":
            st["enough"] = r
    elif kind == "robustness_planned":
        st["findings"][p["finding_id"]]["robustness"][p["plan_id"]] = {
            "primary": p["primary"], "variants": p["variants"], "planned_seq": e["seq"], "results": {},
            "verdict": None}
    elif kind == "robustness_result":
        st["findings"][p["finding_id"]]["robustness"][p["plan_id"]]["results"][p["analysis"]] = {
            "effect": p["effect"], "low": p.get("low"), "high": p.get("high"), "units": p.get("units"),
            "outcome": p["outcome"],
            "seq": e["seq"]}
    elif kind == "knowledge_recorded":
        st["knowledge"].append({**{k: p.get(k) for k in ("question_id", "source", "item", "text", "run_id",
                                                          "finding_id", "thread_id", "relates")},
                                "tag": PRIOR, "by": e["actor"], "seq": e["seq"]})
    elif kind == "dossier_proposed":  # a dossier proposed again keeps the parts recorded so far
        kept = (st["dossiers"].get(p["dossier_id"]) or {}).get("parts", {})
        st["dossiers"][p["dossier_id"]] = {**{k: p[k] for k in EVENTS[kind]}, "why": p.get("why"), "parts": kept,
                                           "by": e["actor"], "seq": e["seq"]}
    elif kind == "dossier_chosen":
        st["chosen"] = list(p["dossier_ids"])
        st["chosen_by"] = {"relayed": bool(p.get("relayed")), "user_words": p.get("user_words"), "by": e["actor"]}
    elif kind == "dossier_part_recorded":
        st["dossiers"][p["dossier_id"]]["parts"][p["part"]] = {
            **{k: p.get(k) for k in ("text", "run_ids", "finding_ids", "knowledge_ids", "kinds", "figure",
                                     "supplementary", "units_as_dots", "tiles")}, "by": e["actor"], "seq": e["seq"]}
    elif kind == "sufficiency_checked":
        st["questions"][p["question_id"]]["sufficiency"] = {"sufficient": bool(p["sufficient"]),
                                                            "reasons": p["reasons"], "seq": e["seq"]}
    elif kind == "robustness_verdict":
        st["findings"][p["finding_id"]]["robustness"][p["plan_id"]].update(
            verdict=p["verdict"], reason=p["reason"], counts=p["counts"], figure=p.get("figure"),
            related=p.get("related") or [])
        st["findings"][p["finding_id"]]["evidence_seq"] = e["seq"]
    elif kind == "check_recorded":
        f = st["findings"][p["finding_id"]]
        entry = {"outcome": p["outcome"], "run_id": p.get("run_id"), "reason": p.get("reason"),
                 "numbers": p.get("numbers") or {}, "by": e["actor"], "seq": e["seq"]}
        if p.get("target") is not None:  # one of a claim's several targets: its first's checks are also the finding's
            f.setdefault("checks_by_target", {}).setdefault(p["target"], {})[p["check"]] = entry
        if p.get("target") in (None, f.get("target")):
            f["checks"][p["check"]] = entry
        f["evidence_seq"] = e["seq"]
    elif kind == "difference_noted":
        f = st["findings"][p["finding_id"]]
        f["differences"].append({"source": p["source"], "text": p["text"], "by": e["actor"], "seq": e["seq"]})
        f["evidence_seq"] = e["seq"]
    elif kind == "skeptic_checked":
        if p.get("question_lacks"):  # the review's line on what the user's question still lacks: the latest stands
            st["question_lacks"] = {"text": p["question_lacks"], "seq": e["seq"]}
        for fid in p["finding_ids"]:
            st["findings"][fid]["skeptic"] = {"seq": e["seq"], "doubt_ids": list(p["doubt_ids"]),
                                              "checked": [{"check": c["check"], "outcome": c["outcome"]}
                                                          for c in p.get("checked") or [] if c["finding_id"] == fid]}
        for r in (st.get("results") or {}).values():  # a result is read once, with the findings it holds now
            if not r.get("skeptic") and all((st["findings"].get(f) or {}).get("skeptic") for f in r["finding_ids"]):
                r["skeptic"] = e["seq"]
    elif kind == "advice_recorded":
        r = st["results"][p["result_id"]]
        r["advice"] = r.get("advice", []) + [{"standings": list(p["standings"]), "thread_ids": list(p["thread_ids"]),
                                              "version": r["version"], "seq": e["seq"], "by": e["actor"]}]
    elif kind == "question_parts_recorded":
        for q in p["parts"]:
            st.setdefault("question_parts", {})[q["id"]] = {"words": q["words"], "message_uuid": p["message_uuid"],
                                                             "closed_by": [], "not_answerable": None, "seq": e["seq"]}
    elif kind == "part_closed":
        q = st["question_parts"][p["part_id"]]
        q.update(closed_by=list(p.get("by") or []), not_answerable=p.get("not_answerable") or None,
                 closed_seq=e["seq"])
    elif kind == "no_choice_said":
        st.setdefault("no_choice", []).append({"reason": p["reason"], "by": e["actor"], "seq": e["seq"]})
    elif kind == "cell_set_saved":
        st.setdefault("cell_sets", {})[p["name"]] = {**p, "confirmed": None, "by": e["actor"], "seq": e["seq"]}
    elif kind == "block_called":
        st.setdefault("block_calls", {})[p["call_id"]] = {**p, "seq": e["seq"]}
    elif kind == "object_registered":
        before = st.setdefault("objects", {}).get(p["object_id"])
        st["objects"][p["object_id"]] = {**{k: v for k, v in p.items() if k != "object_id"},
                                         "version": (before or {}).get("version", 0) + 1, "seq": e["seq"]}
    elif kind in ("module_menu_given", "module_picks_recorded", "module_menu_answered"):
        st.setdefault("module_menus", []).append({"kind": kind, **p, "seq": e["seq"], "time": e.get("timestamp")})
    elif kind == "calling_rule_confirmed":
        st.setdefault("cell_sets", {})[p["cell_set"]]["confirmed"] = {
            "answer": p["answer"], "description": p.get("description", ""), "note": p.get("note", ""),
            "by": e["actor"], "seq": e["seq"]}
    elif kind == "doubt_raised":
        st["doubts"][p["doubt_id"]] = {**{k: p[k] for k in EVENTS[kind]}, "finding_id": p.get("finding_id"),
                                       "question_id": p.get("question_id"), "branches": [], "answer": None,
                                       "answers": [], "seq": e["seq"], "opened_by": p.get("opened_by")}
    elif kind == "doubt_answered":
        d = st["doubts"][p["doubt_id"]]
        d["answer"] = {"outcome": p["outcome"], "reason": p["reason"], "run_ids": list(p.get("run_ids") or []),
                       "limit": p.get("limit"), "by": e["actor"], "time": e.get("timestamp"), "seq": e["seq"]}
        d["answers"].append(d["answer"])
    elif kind == "finding_reworded":
        f = st["findings"][p["finding_id"]]
        given = {k: p[k] for k in REWORDED if p.get(k) is not None}
        earlier = {**{k: f.get(k) for k in REWORDED}, "time": f.get("time"), "replaced": e.get("timestamp"),
                   "because": p["reason"]}
        if any(given.get(k, f.get(k)) != f.get(k) for k in CLAIM + ("kind", "targets")):  # another claim: the checks
            # tested
            # the earlier
            earlier["checks"], f["checks"] = f["checks"], {}
            f.pop("checks_by_target", None)
        if set(given.get("run_ids") or []) - set(f.get("run_ids") or []):
            f["evidence_seq"] = e["seq"]
        f["wordings"].append(earlier)
        f.update(given, time=e.get("timestamp"))
    elif kind == "branch_opened":
        st["branches"][p["branch_id"]] = {"branch_id": p["branch_id"], "origin": p.get("origin") or "doubt",
                                          "doubt_id": p.get("doubt_id"), "fork_id": p.get("fork_id"),
                                          "request": p.get("request"), "plan": p["plan"], "outcome": None,
                                          "alternatives": list(p.get("alternatives") or []), "base": p.get("base"),
                                          "measures": list(p.get("measures") or []), "comparison": None,
                                          "run_ids": [], "by": e["actor"], "seq": e["seq"]}
        if p.get("doubt_id"):
            st["doubts"][p["doubt_id"]]["branches"].append(p["branch_id"])
    elif kind == "question_asked":
        q = {"text": p["text"], "relayed": bool(p.get("relayed")), "by": e["actor"], "time": e.get("timestamp"),
             "seq": e["seq"], "scope": p.get("scope")}  # scope: one question, or the whole screen (ADR 0045)
        st["asked"].append(q)
        st["question"] = q
    elif kind == "paradigm_chosen":
        c = {"paradigm_id": p["paradigm_id"], "version": p["version"], "inserted": list(p.get("inserted") or []),
             "why": p.get("why"), "by": e["actor"], "seq": e["seq"]}
        st["paradigms"].append(c)
        st["paradigm"] = c
    elif kind == "fork_decided":
        st["forks"].setdefault(p["fork_id"], []).append(
            {"fork_id": p["fork_id"], "step_id": p.get("step_id"), "options": list(p.get("options") or []),
             "choice": p["choice"], "why": p.get("why"), "key": bool(p.get("key")),
             "default": bool(p.get("default")), "relayed": bool(p.get("relayed")), "user_words": p.get("user_words"),
             "by": e["actor"], "seq": e["seq"]})
    elif kind == "branch_closed":
        st["branches"][p["branch_id"]].update(outcome=p["outcome"], run_ids=list(p["run_ids"]),
                                              comparison=p.get("comparison"))
    elif kind == "figure_saved":
        before = st["figures"].get(p["figure_id"])
        earlier = [] if before is None else before["earlier"] + [
            {k: before[k] for k in ("run_id", "paths", "caption", "version", "seq", "reviews")}]
        st["figures"][p["figure_id"]] = {
            "figure_id": p["figure_id"], "run_id": p["run_id"], "paths": p["paths"], "caption": p["caption"],
            "role": p.get("role"), "checks": p.get("checks"), "figure_checks": p.get("figure_checks"),
            "strict": p.get("strict"), "notes": list(p.get("notes") or []),
            "version": len(earlier) + 1, "seq": e["seq"], "earlier": earlier, "reviews": []}
    elif kind == "figure_reviewed":
        f = st["figures"][p["figure_id"]]
        f["reviews"].append({"version": f["version"], "verdict": p["verdict"], "issues": list(p["issues"]),
                             "shown_not_said": list(p.get("shown_not_said") or []), "answers": {}, "by": e["actor"],
                             "seq": e["seq"], "user_words": p.get("user_words")})
    elif kind == "figure_issue_answered":
        last = (st["set_reviews"][-1] if p["figure_id"] == SET else st["figures"][p["figure_id"]]["reviews"][-1])
        last.setdefault("answers", {})[str(p["issue"])] = {"answer": p["answer"], "seq": e["seq"]}
    elif kind == "screen_rebuilt":
        st.setdefault("builds", {})[p["build_id"]] = {"previous": p["previous"], "changed": bool(p["changed"]),
                                                       "what": p.get("what") or {}, "seq": e["seq"],
                                                       "time": e.get("timestamp")}
        st["build"] = p["build_id"]
    elif kind == "result_reported":  # a result reported again is its next version, under the same ID
        before = st.setdefault("results", {}).get(p["result_id"])
        kept = ("title", "finding_ids", "figure", "figure_run", "text", "version", "seq")
        st["results"][p["result_id"]] = {
            "result_id": p["result_id"], "title": p["title"], "finding_ids": list(p["finding_ids"]),
            "figure": p.get("figure"), "figure_run": p["figure_run"], "text": p.get("text"),
            "version": before["version"] + 1 if before else 1,
            "versions": (before["versions"] + [{k: before[k] for k in kept}]) if before else [],
            "decision": None, "user_words": None, "by": e["actor"], "seq": e["seq"], "time": e.get("timestamp"),
            "answers": p.get("answers") or (before or {}).get("answers"),
            "raised_by": p.get("raised_by") or (before or {}).get("raised_by"),
            "samples": p.get("samples") or (before or {}).get("samples"),
            # its next steps, and the seq of the version that offered them: a version without a list keeps both, so a
            # pick made after the offer stays its answer (docs/adr/0050, D5)
            "next_steps": p.get("next_steps") or (before or {}).get("next_steps"),
            "next_steps_seq": e["seq"] if p.get("next_steps") else (before or {}).get("next_steps_seq"),
            "advice": (before or {}).get("advice") or [],  # the advisor reads a result once, whatever its version
            # and the skeptic: read once its findings are, whatever joins it later (skeptic_due)
            "skeptic": (before or {}).get("skeptic") or (
                e["seq"] if all((st["findings"].get(f) or {}).get("skeptic") for f in p["finding_ids"]) else None)}
    elif kind == "story_written":
        st["story"] = {"text": p["text"], "seq": e["seq"]}
    elif kind == "lead_reviewed":
        st.setdefault("reviews", []).append({"verdict": p["verdict"], "story": p["story"],
                                             "threads": list(p.get("threads") or []),
                                             "figures": list(p.get("figures") or []),
                                             "blocks": list(p.get("blocks") or []), "seq": e["seq"],
                                             "main_line": p.get("main_line"), "time": e.get("timestamp"),
                                             "threads_to_open": list(p.get("threads_to_open") or []),
                                             "left_open": list(p.get("left_open") or [])})  # what its complete left
    elif kind == "figures_reviewed_as_a_set":  # with the results' figures and versions it read (docs/adr/0049, D5)
        st.setdefault("set_reviews", []).append({"verdict": p["verdict"], "issues": list(p["issues"]),
                                                 "shown_not_said": list(p.get("shown_not_said") or []),
                                                 "answers": {}, "seq": e["seq"], "time": e.get("timestamp"),
                                                 "figures": set_figures(st), "user_words": p.get("user_words")})
    elif kind == "result_decided":
        st["results"][p["result_id"]].update(decision=p["decision"], user_words=p["user_words"],
                                             decided_seq=e["seq"], decided_time=e.get("timestamp"),
                                             reason=p.get("reason"),
                                             by_analysis=bool(p.get("by_analysis")))
    elif kind == "thread_opened":
        st.setdefault("threads", {})[p["thread_id"]] = {
            **{k: p.get(k) for k in THREAD_FIELDS}, "moves": list(p.get("moves") or []),
            "evidence": p.get("evidence") or {}, "status": "open", "reason": None, "findings": [], "changes": [],
            "by": e["actor"], "seq": e["seq"], "time": e.get("timestamp")}
    elif kind == "choice_tried":
        st["findings"][p["finding_id"]].setdefault("choices_tried", []).append(
            {"choice": p["choice"], "run_id": p["run_id"], "value": p.get("value")})
    elif kind == "plan_written":  # a later version keeps an item's id; one left out is left, with its reason
        before = st.get("plan")
        version = before["version"] + 1 if before else 1
        items = {str(i["id"]): {**{k: i.get(k) for k in PLAN_ITEM_FIELDS}, "id": str(i["id"])} for i in p["items"]}
        left = dict(before["left"]) if before else {}
        for iid, i in (before["items"] if before else {}).items():
            if iid not in items:
                left[iid] = {**i, "left_why": p.get("why"), "left_in": version}
        st["plan"] = {"version": version, "order": list(items), "items": items, "left": left,
                      "versions": (before["versions"] if before else []) + [
                          {"version": version, "why": p.get("why"), "items": list(items), "seq": e["seq"],
                           "time": e.get("timestamp")}]}
    elif kind == "main_line_moved":
        st["main_line"] = {"thread_id": p["thread_id"], "why": p["why"], "by": p["by"],
                           "user_words": p.get("user_words"), "findings": list(p.get("findings") or []),
                           "runs": list(p.get("runs") or []), "seq": e["seq"], "time": e.get("timestamp")}
        st.setdefault("main_line_moves", []).append(st["main_line"])
    elif kind == "thread_changed":
        th = st["threads"][p["thread_id"]]
        th["changes"].append({"status": p["status"], "reason": p["reason"], "by": e["actor"],
                              "time": e.get("timestamp"), "seq": e["seq"], "run_ids": list(p.get("run_ids") or [])})
        th.update(status=p["status"], reason=p["reason"])
        th["findings"] += [f for f in p.get("finding_ids") or [] if f not in th["findings"]]  # those that explain it


def reduce(events):
    st = empty()
    for e in events:
        apply(st, e)
    return st


def problems(kind, p, actor, st):
    """Why an event cannot be appended to the state, or []."""
    if kind not in EVENTS:
        return [f"unknown event {kind}"]
    out = [f"{kind} needs {k}" for k in EVENTS[kind] if k not in p]
    if out:
        return out
    if kind in TOOL_ONLY and actor != TOOL:
        out.append(f"{kind} is written by the tools that make runs, not by {actor}")
    qs, runs, fs, ds, bs = st["questions"], st["runs"], st["findings"], st["doubts"], st["branches"]
    if kind == "calling_rule_confirmed":
        if p["cell_set"] not in st.get("cell_sets", {}):
            out.append(f"no cell set {p['cell_set']}")
        if actor != USER:
            out.append("a calling rule is confirmed by the user, on the design form")
    if kind == "question_planned" and p["question_id"] in qs:
        out.append(f"question {p['question_id']} is already planned")
    if kind == "question_planned" and actor in (AGENT, USER) and not p["because"]:
        out.append("a question the agent or user adds needs a reason")
    if kind == "question_closed":
        if p["question_id"] not in qs:
            out.append(f"no question {p['question_id']}")
        if p["status"] not in STATUSES:
            out.append(f"status is one of {', '.join(STATUSES)}")
        elif p["status"] in ANSWERED:
            if not p.get("evidence") or not p.get("figure"):
                out.append(f"a question closed {p['status']} needs its evidence (run IDs) and its figure")
            missing = [r for r in p.get("evidence") or [] if r not in runs]
            if missing:
                out.append(f"no run {', '.join(missing)}")
        elif not p.get("reason"):
            out.append(f"a question closed {p['status']} needs a reason")
    if kind == "question_parts_recorded":
        have = st.get("question_parts") or {}
        if any(q["message_uuid"] == p["message_uuid"] for q in have.values()):
            out.append(f"the parts of message {p['message_uuid']} were taken at its first reading")
        parts = p["parts"] if isinstance(p["parts"], list) else []
        if not parts or not all(isinstance(q, dict) and str(q.get("id") or "").strip()
                                and str(q.get("words") or "").strip() for q in parts):
            out.append("each part has its id and the user's words")
        elif any(q["id"] in have for q in parts):
            out.append("a part's id is taken")
    if kind == "part_closed":
        by, why = p.get("by") or [], str(p.get("not_answerable") or "").strip()
        if p["part_id"] not in (st.get("question_parts") or {}):
            out.append(f"no part {p['part_id']} of the question")
        if bool(by) == bool(why):
            out.append("a part closes on findings, results or runs (by), or as not answerable with its reason")
        missing = [str(x) for x in by if x not in fs and x not in (st.get("results") or {}) and x not in runs]
        if missing:
            out.append(f"no finding, result or run {', '.join(missing)}")
    if kind == "no_choice_said" and not str(p["reason"] or "").strip():
        out.append("a No choice line gives its reason")
    if kind == "object_registered" and p["run_id"] is not None and p["run_id"] not in runs:
        out.append(f"no run {p['run_id']}")
    if kind == "run_logged" and p["run_id"] in runs:
        out.append(f"run {p['run_id']} is already logged")
    if kind in ("run_finished", "run_read", "run_claimed") and p["run_id"] not in runs:
        out.append(f"no run {p['run_id']}")
    if kind == "finding_recorded":
        if p["finding_id"] in fs:
            out.append(f"finding {p['finding_id']} is already recorded")
        if p.get("thread_id") is not None and p["thread_id"] not in st.get("threads", {}):
            out.append(f"no thread {p['thread_id']}; pertura_status lists the threads")
        if p.get("question_id") is not None and p["question_id"] not in qs:
            out.append(f"no question {p['question_id']}")
        if not str(p.get("statement") or "").strip() and not (p.get("target") and p.get("feature")):
            out.append("a finding says what was found: a sentence (statement), or a target and a feature")
        if p.get("direction") is not None and p["direction"] not in ("up", "down"):
            out.append("a finding's direction is up or down")
        out += [f"no run {r}" for r in p.get("run_ids") or [] if r not in runs and p.get("statement")]
    if kind == "thread_opened":
        if p["thread_id"] in st.get("threads", {}):
            out.append(f"thread {p['thread_id']} is already open")
        if not str(p["about"] or "").strip():
            out.append("a thread says, in a sentence with its numbers, what was raised (about)")
        if not p["raised_by"]:
            out.append("a thread names what raised it (raised_by: a run, a finding, a design key or the user's words)")
        out += [(f"{f} is a thread: joins names findings, and the thread this one goes on from is its raised_by"
                 if f in st.get("threads", {}) else f"no finding {f}")  # six refusals of "no finding t2" in a row
                for f in p.get("joins") or [] if f not in st["findings"]]
        if p.get("joins") is not None and not p["joins"]:
            out.append("a thread one level up rests on one finding or more (joins)")
    if kind == "thread_opened" and p.get("fork_of") is not None:
        if not node_of(st, p["fork_of"]) and p["fork_of"] not in st.get("threads", {}):
            out.append(f"fork_of names a finding, a thread or a run of the record: {p['fork_of']} is none")
        if not str(p.get("choice") or "").strip():
            out.append("a fork names the one choice it changes (choice)")
        if not str(p.get("matters") or "").strip():
            out.append("a fork says why that choice could change the reading (matters)")
    if kind == "main_line_moved":
        if p["thread_id"] not in st.get("threads", {}):
            out.append(f"no thread {p['thread_id']}: the main line moves to a thread of the record")
        if not str(p["why"] or "").strip():
            out.append("the main line's move says why")
    if kind == "thread_changed":
        if p["thread_id"] not in st.get("threads", {}):
            out.append(f"no thread {p['thread_id']}")
        if p["status"] not in THREAD_STATUSES:
            out.append(f"a thread's status is one of {', '.join(THREAD_STATUSES)}")
        if not str(p["reason"] or "").strip():
            out.append("a thread's change says why (reason)")
        out += [f"no finding {f}" for f in p.get("finding_ids") or [] if f not in st["findings"]]
    if kind == "reply_recorded":
        if p["kind"] not in REPLY_KINDS:
            out.append(f"a reply's kind is one of {', '.join(REPLY_KINDS)}")
        if not str(p["words"] or "").strip():
            out.append("a reply is the user's words, as they gave them")
        if p.get("finding_id") is not None and p["finding_id"] not in fs:
            out.append(f"no finding {p['finding_id']}")
        if p.get("finding_id") is None and p["kind"] not in ("enough", "knowledge", "main_line", "next_step"):
            out.append(f"a {p['kind']} reply answers a finding (finding_id)")
    if kind == "check_recorded":
        if p["finding_id"] not in fs:
            out.append(f"no finding {p['finding_id']}")
        elif p.get("target") is not None and p["target"] not in (fs[p["finding_id"]].get("targets")
                                                                  or [fs[p["finding_id"]].get("target")]):
            out.append(f"finding {p['finding_id']} has no target {p['target']}")
        if p["check"] not in CHECKLIST:
            out.append(f"check is one of {', '.join(CHECKLIST)}")
        elif actor != TOOL and p["check"] != "own":
            out.append("a finding's checks are run by the tool when it is recorded; add a check of your own as "
                       "check 'own', with its run")
        if p["outcome"] in ("not applicable", "not run") and not p.get("reason"):
            out.append(f"a check marked {p['outcome']} needs a reason")
        if p["check"] == "own" and not p.get("run_id"):
            out.append("a check of your own names its run (run_id)")
    if kind == "difference_noted":
        if p["finding_id"] not in fs:
            out.append(f"no finding {p['finding_id']}")
        if p["source"] not in DIFFERENCE_SOURCES:
            out.append(f"source is one of {', '.join(DIFFERENCE_SOURCES)}")
        if not str(p["text"] or "").strip():
            out.append("a difference says what differs")
    if kind == "skeptic_checked":
        if not p["finding_ids"]:
            out.append("the skeptic's check names the findings it checked")
        out += [f"no finding {f}" for f in p["finding_ids"] if f not in fs]
        out += [f"no doubt {d}" for d in p["doubt_ids"] if d not in ds]
        for c in p.get("checked") or []:
            if not isinstance(c, dict) or c.get("finding_id") not in p["finding_ids"] or not all(
                    str(c.get(k) or "").strip() for k in ("check", "outcome")):
                out.append("each item of checked names a finding it checked (finding_id), what it checked (check) "
                           "and how it came out (outcome: sound, or the ID of the doubt it raised)")
                break
    if kind == "advice_recorded":
        if p["result_id"] not in st.get("results", {}):
            out.append(f"no result {p['result_id']}")
        if not p["standings"]:
            out.append("the advisor's reading gives each relation of the result its standing (standings)")
        out += [f"no thread {t}" for t in p["thread_ids"] if t not in st.get("threads", {})]
        out += standing_problems(st, p["standings"])
    if kind == "doubt_raised":
        if p["doubt_id"] in ds:
            out.append(f"doubt {p['doubt_id']} is already raised")
        if p.get("finding_id") is not None and p["finding_id"] not in fs:
            out.append(f"no finding {p['finding_id']}")
        if p.get("question_id") is not None and p["question_id"] not in qs:
            out.append(f"no question {p['question_id']}: a doubt on a finding names it with finding_id")
    if kind == "doubt_answered":
        if p["doubt_id"] not in ds:
            out.append(f"no doubt {p['doubt_id']}")
        if p["outcome"] not in DOUBT_OUTCOMES:
            out.append(f"a doubt's answer is one of {', '.join(DOUBT_OUTCOMES)}")
        if p.get("limit") is not None and (p["outcome"] != LIMIT or p["limit"] not in LIMIT_KINDS):
            out.append(f"a limit's kind (limit) is one of {', '.join(LIMIT_KINDS)}, on a doubt stated as a limit")
        if not str(p["reason"] or "").strip():
            out.append("a doubt's answer gives its reason")
        out += [f"no run {r}" for r in p.get("run_ids") or [] if r not in runs]
    if kind == "finding_reworded":
        f = fs.get(p["finding_id"])
        given = [k for k in REWORDED if p.get(k) is not None]
        if f is None:
            out.append(f"no finding {p['finding_id']}")
        if not str(p["reason"] or "").strip():
            out.append("a rewording gives its reason")
        if not given:
            out.append(f"a rewording changes one of {', '.join(REWORDED)}")
        elif f is not None and all(p[k] == f.get(k) for k in given):
            out.append("the rewording changes nothing: each value given is the finding's already")
        if "statement" in given and not str(p["statement"]).strip():
            out.append("a finding's sentence is not empty")
        if p.get("direction") is not None and p["direction"] not in ("up", "down"):
            out.append("a finding's direction is up or down")
        out += [f"no run {r}" for r in p.get("run_ids") or [] if r not in runs]
    if kind == "branch_opened":
        origin = p.get("origin") or "doubt"
        if origin not in ORIGINS:
            out.append(f"origin is one of {', '.join(ORIGINS)}")
        elif origin == "doubt" and p.get("doubt_id") not in ds:
            out.append(f"no doubt {p.get('doubt_id')}: a branch tests a raised doubt, re-opens a decided fork "
                       "(origin fork, with fork_id) or follows the user's request (origin user, with request)")
        elif origin == "fork" and p.get("fork_id") not in st.get("forks", {}):
            out.append(f"no fork {p.get('fork_id')} decided")
        elif origin == "user" and not str(p.get("request") or "").strip():
            out.append("a branch the user asked for says what they asked (request)")
        elif origin == "alternatives":
            alts = p.get("alternatives") or []
            if not alts or any(not isinstance(a, dict) or not str(a.get("name") or "").strip()
                               or not str(a.get("change") or "").strip() for a in alts):
                out.append("a branch of alternatives names each one with its change ([{name, change}])")
            elif len({str(a["name"]) for a in alts}) != len(alts) or "main" in {str(a["name"]) for a in alts}:
                out.append("each alternative has its own name, and 'main' is the main agent's way")
            if not str(p.get("base") or "").strip():
                out.append("a branch of alternatives gives the base every alternative changes (base)")
            if not p.get("measures"):
                out.append("a branch of alternatives names the measures each way reports (measures)")
        if p["branch_id"] in bs:
            out.append(f"branch {p['branch_id']} is already open")
    if kind == "question_asked" and not str(p["text"] or "").strip():
        out.append("the user's question is their words, not empty")
    if kind == "paradigm_chosen":
        if not str(p["paradigm_id"] or "").strip():
            out.append("a paradigm is named by its card's id")
        if not isinstance(p["version"], int) or p["version"] < 1:
            out.append("a paradigm's version is its card's version, a whole number")
        if actor not in (USER, AGENT):
            out.append("a paradigm is chosen by the agent, or by the user")
    if kind == "fork_decided":
        if not str(p["choice"] or "").strip():
            out.append("a fork's decision names its choice")
        if p.get("key") and actor != USER and not (p.get("relayed") and str(p.get("user_words") or "").strip()):
            out.append("a key fork is the user's to decide: ask them in chat and relay their words (user_words)")
        if actor == AGENT and not p.get("default") and not p.get("relayed") and not str(p.get("why") or "").strip():
            out.append("a fork the agent decides against the card's default says why")
    if kind == "branch_closed" and p["branch_id"] not in bs:
        out.append(f"no branch {p['branch_id']}")
    if kind == "knowledge_recorded" and not (p["source"] and p["item"]):
        out.append("an item of outside knowledge needs its source and what it is")
    if kind == "dossier_proposed":  # proposed again (another main run, title or leads) until its panel is made
        if "panel_and_paragraph" in (st["dossiers"].get(p["dossier_id"]) or {}).get("parts", {}):
            out.append(f"dossier {p['dossier_id']} already has its panel; propose a new dossier to change it")
        if not p["lead_findings"]:
            out.append("a dossier names its lead findings")
        out += [f"no finding {f}" for f in p["lead_findings"] if f not in fs]
        if p["main_run"] not in runs:
            out.append(f"no run {p['main_run']}")
    if kind == "lead_reviewed":
        if p["verdict"] not in LEAD_VERDICTS:
            out.append(f"a review's verdict is one of {', '.join(LEAD_VERDICTS)}")
        if not str(p["story"] or "").strip():
            out.append("a review gives the story so far (story)")
        out += [f"no thread {x}" for x in p.get("threads") or [] if x not in st.get("threads", {})]
        given = p.get("blocks") or []
        # a block answered may be any thread or doubt the record holds (the lead routed doubts not yet answered, on
        # GSE335887, and was refused three times)
        known = {b["id"] for b in block_candidates(st)} | set(st.get("threads") or {}) | set(st.get("doubts") or {})
        for b in given:  # each block's kind, and its route or why no route applies (P16)
            if b.get("id") not in known:
                out.append(f"no thread or doubt {b.get('id')}: the record as it stands lists where a line stopped (open: places_a_line_stopped)")
            if b.get("kind") not in BLOCK_KINDS:
                out.append(f"a block's kind is one of {', '.join(BLOCK_KINDS)}")
            if b.get("thread_id") and b["thread_id"] not in st.get("threads", {}):
                out.append(f"no thread {b['thread_id']} (a block's route)")
            if not b.get("thread_id") and not str(b.get("reason") or "").strip():
                out.append(f"block {b.get('id')}: a thread that routes around it, or the reason no route applies")
    if kind == "figures_reviewed_as_a_set":  # due once; again on a newer figure or the user's ask (docs/adr/0049, D5)
        sets = st.get("set_reviews") or []
        if (len(sets) >= REVIEWS_AT_MOST and not str(p.get("user_words") or "").strip()
                and not set_changed_since(st, sets[-1])):
            out.append("the figures as a set have had their review, and no result's figure is newer than it or new "
                       "since: its open issues stay, and the report states them; its issues are answered with answers "
                       "alone, without a verdict. It is reviewed again once a result's figure changes or is added, or "
                       "where the user asks (user_words)")
        if p["verdict"] not in VERDICTS:
            out.append(f"a set review's verdict is one of {', '.join(VERDICTS)}")
        if p["verdict"] == "fix" and not p["issues"]:
            out.append("a set review that asks for a fix names the issues")
    if kind == "story_written" and not str(p["text"] or "").strip():
        out.append("a story is a title sentence and how the results lead from one to the next (story)")
    if kind == "result_reported":
        if not str(p["title"] or "").strip():
            out.append("a result has a title: its conclusion, in a sentence")
        rb = p.get("raised_by")
        if rb and rb == p["result_id"]:
            out.append("a result is raised by an earlier result or finding, not by itself (raised_by)")
        elif rb and rb not in st.get("results", {}) and rb not in fs and rb not in st.get("threads", {}):
            out.append(f"no result, finding or thread {rb} (raised_by)")
        if not p["finding_ids"]:
            out.append("a result names the findings it rests on (finding_ids)")
        missing = [f for f in p["finding_ids"] or [] if f not in fs]
        out += [f"no finding {f}" for f in missing]
        theirs = {r for f in p["finding_ids"] or [] if f in fs for r in fs[f].get("run_ids") or []}
        if not missing and p["figure_run"] not in theirs and not runs_read(st, p["figure_run"]) & theirs:
            out.append(f"a result's figure is drawn from the runs of its findings ({', '.join(sorted(theirs)) or 'none'}"
                       f"): {p['figure_run']} is none of them and read none of them; the cell that draws it reads "
                       "what they made (their files, tables or objects). Where it draws a new run, as a figure fixed "
                       f"after its review does, add {p['figure_run']} to the finding it supports first (reword_finding "
                       "with run_ids), then report the result again")
    if kind == "plan_written":
        items = p.get("items") if isinstance(p.get("items"), list) else []
        if not items:
            out.append("a plan is a list of items, each {does, why, how}")
        ids = [str(i.get("id")) for i in items if isinstance(i, dict)]
        if len(set(ids)) < len(ids):
            out.append("a plan's items have different ids")
        for i in items:
            if not isinstance(i, dict):
                out.append("each item of a plan is {does, why, how}")
                continue
            for k in ("id", "does", "why", "how"):
                if not str(i.get(k) or "").strip():
                    out.append(f"item {i.get('id', '?')}: needs {k}")
            if i.get("status") not in (None, "done"):
                out.append(f"item {i.get('id', '?')}: its status is done, or none while it is open")
            if i.get("status") == "done" and not str(i.get("done_by") or "").strip():
                out.append(f"item {i.get('id', '?')}: an item done without a run says what did it (done_by)")
        if st.get("plan") and not str(p.get("why") or "").strip():
            out.append("a plan written again says why it changes")
    if kind == "result_decided":
        if p["result_id"] not in st.get("results", {}):
            out.append(f"no result {p['result_id']}")
        if p["decision"] not in RESULT_DECISIONS:
            out.append(f"a decision is one of {', '.join(RESULT_DECISIONS)}")
        if not (p.get("relayed") and str(p.get("user_words") or "").strip()) and not (
                p.get("by_analysis") and str(p.get("reason") or "").strip()):
            out.append("only the user confirms or drops a result: ask them in chat and relay their words "
                       "(user_words); when they run Pertura unattended, decide with your reason (reason)")
    if kind == "dossier_chosen":
        if actor != USER and not (p.get("relayed") and str(p.get("user_words") or "").strip()):
            out.append("only the user chooses the dossiers: ask them in chat and relay their words (user_words)")
        if not 1 <= len(p["dossier_ids"]) <= 5:
            out.append("the user chooses 1-5 dossiers")
        out += [f"no dossier {d} proposed" for d in p["dossier_ids"] if d not in st["dossiers"]]
    if kind == "dossier_part_recorded":
        if p["dossier_id"] not in st["dossiers"]:
            out.append(f"no dossier {p['dossier_id']}")
        if p["part"] not in DOSSIER_PARTS:
            out.append(f"part is one of {', '.join(DOSSIER_PARTS)}")
        if not p["text"]:
            out.append("a dossier part needs its text")
        if p["part"] == "mechanism":  # prior knowledge, or what in the data tests it, or the findings it is about
            if not (p.get("knowledge_ids") or p.get("run_ids") or p.get("finding_ids")):
                out.append("the mechanism part needs knowledge_ids (prior knowledge), run_ids (what in the data tests "
                           "a mechanism), or finding_ids (the findings for which no mechanism is claimed)")
        else:
            out += [f"the {p['part']} part needs {k}" for k in PART_NEEDS.get(p["part"], ()) if not p.get(k)]
        if p["part"] == "convergence" and len(set(p.get("kinds") or [])) < 2:
            out.append("convergence needs two or more kinds of evidence")
        out += [f"no run {r}" for r in p.get("run_ids") or [] if r not in runs]
        out += [f"no finding {f}" for f in p.get("finding_ids") or [] if f not in fs]
        seqs = {k["seq"] for k in st["knowledge"]}
        out += [f"no knowledge item {k}" for k in p.get("knowledge_ids") or [] if k not in seqs]
    if kind == "figure_saved":
        if p["run_id"] not in runs:
            out.append(f"no run {p['run_id']}: a figure is entered with the run that drew it")
        if not isinstance(p["paths"], dict) or not p["paths"].get("png"):
            out.append("a figure's paths hold its png")
        if p["caption"] is not None and not str(p["caption"]).strip():
            out.append("a figure's caption states its content, or is left out (None) for a figure kept while "
                       "exploring")
        if p.get("role") is not None and p["role"] not in ROLES:
            out.append(f"role is one of {', '.join(ROLES)}")
    if kind == "figure_issue_answered" and p["figure_id"] == SET:  # an issue of the set's latest review
        sets = st.get("set_reviews") or []
        last = sets[-1] if sets else None
        if last is None or last["verdict"] != "fix":
            out.append("only an issue of the set's latest review, a fix, is answered")
        elif not isinstance(p["issue"], int) or not 1 <= p["issue"] <= len(last["issues"]):
            out.append(f"issue is the issue's number, 1 to {len(last['issues'])}")
        elif str(p["issue"]) in (last.get("answers") or {}):
            out.append(f"issue {p['issue']} is already answered")
        if not str(p["answer"] or "").strip():
            out.append("an answer gives its reason")
    elif kind in ("figure_reviewed", "figure_issue_answered") and p["figure_id"] not in st["figures"]:
        out.append(f"no figure {p['figure_id']} in the record")
    elif kind == "figure_reviewed":  # due once; again for a newer version or the user's ask (docs/adr/0049, D5)
        f = st["figures"][p["figure_id"]]
        if len(f["reviews"]) >= REVIEWS_AT_MOST and not str(p.get("user_words") or "").strip():
            out.append(f"figure {p['figure_id']}, version {f['version']}, has had its review: its open issues stay "
                       "with it, and the report states them; its issues are answered with answers alone, without a "
                       "verdict. It is reviewed again once it is drawn anew (a newer version), or where the user asks "
                       "(user_words)")
        if p["verdict"] not in VERDICTS:
            out.append(f"verdict is one of {', '.join(VERDICTS)}")
        elif not isinstance(p["issues"], list) or any(not str(i).strip() for i in p["issues"]):
            out.append("issues is a list of what is wrong, each one said")
        elif p["verdict"] == "fix" and not p["issues"]:
            out.append("a fix verdict names at least one issue")
        elif p["verdict"] == "ok" and p["issues"]:
            out.append("an ok verdict has no issues")
    elif kind == "figure_issue_answered":
        reviews = st["figures"][p["figure_id"]]["reviews"]
        last = reviews[-1] if reviews else None
        if last is None or last["verdict"] != "fix":
            out.append("only an issue of the figure's latest review, a fix, is answered")
        elif not isinstance(p["issue"], int) or not 1 <= p["issue"] <= len(last["issues"]):
            out.append(f"issue is the issue's number, 1 to {len(last['issues'])}")
        elif str(p["issue"]) in last["answers"]:
            out.append(f"issue {p['issue']} is already answered")
        if not str(p["answer"] or "").strip():
            out.append("an answer gives its reason")
    if kind == "sufficiency_checked":
        q = qs.get(p["question_id"])
        if q is None:
            out.append(f"no question {p['question_id']}")
        elif q["status"] is None:
            out.append("the sufficiency check is of a closed question")
    if kind.startswith("robustness_"):
        plans = fs.get(p["finding_id"], {}).get("robustness", {})
        if p["finding_id"] not in fs:
            out.append(f"no finding {p['finding_id']}: robustness runs on recorded findings only")
        elif kind == "robustness_planned" and p["plan_id"] in plans:
            out.append(f"plan {p['plan_id']} is already recorded")
        elif kind != "robustness_planned" and p["plan_id"] not in plans:
            out.append(f"no plan {p['plan_id']}: the plan is recorded before any result")
        elif kind == "robustness_result":
            names = ["primary"] + [v.get("name") for v in plans[p["plan_id"]]["variants"]]
            if p["analysis"] not in names:
                out.append(f"analysis {p['analysis']!r} is not in plan {p['plan_id']}")
    return out


class Record:
    """The record of one analysis, in folder (events.db, switches.json)."""

    def __init__(self, folder, switches=None):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self.path = os.path.join(folder, "events.db")
        sw = os.path.join(folder, "switches.json")
        if switches is not None or not os.path.exists(sw):
            given = {k: v for k, v in dict(switches or {}).items() if k not in RETIRED_SWITCHES}
            bad = sorted(set(given) - set(PIECES))
            if bad:
                raise RecordError(f"unknown switches: {', '.join(bad)}")
            with open(sw, "w", encoding="utf-8") as fh:
                json.dump({k: bool(given.get(k, True)) for k in PIECES}, fh)
        with open(sw, encoding="utf-8") as fh:
            self.switches = json.load(fh)
        with _db(self.path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT "
                        "UNIQUE, event_type TEXT, timestamp TEXT, actor TEXT, payload TEXT)")

    def enabled(self, piece):
        if piece not in PIECES:
            raise RecordError(f"unknown piece {piece}")
        return bool(self.switches.get(piece, True))

    def append(self, kind, payload, actor):
        """Check and append one event in one transaction; returns it (None when the record is switched off)."""
        out = self.append_many([(kind, payload, actor)])
        return out[0] if out else None

    def append_many(self, items):
        """Check and append several events, [(kind, payload, actor)], in one transaction, each checked against the
        state with the ones before it applied: all or none. Returns them (None when the record is switched off)."""
        if not self.enabled("record"):
            return None
        with _tx(self.path) as con:
            st = empty()
            for e in _rows(con):
                apply(st, e)
            out = []
            for kind, payload, actor in items:
                payload = json.loads(json.dumps(dict(payload), default=str))
                bad = problems(kind, payload, actor, st)
                if bad:
                    raise RecordError("; ".join(bad))
                e = {"event_id": uuid.uuid4().hex, "event_type": kind,
                     "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": actor, "payload": payload}
                cur = con.execute("INSERT INTO events(event_id, event_type, timestamp, actor, payload) "
                                  "VALUES (?,?,?,?,?)", (e["event_id"], kind, e["timestamp"], actor,
                                                         json.dumps(payload)))
                e["seq"] = cur.lastrowid
                apply(st, e)
                out.append(e)
            return out

    def events(self):
        with _db(self.path) as con:
            return _rows(con)

    def state(self):
        return reduce(self.events())

    def state_hash(self):
        return state_hash(self.state())

    # ---- the plan
    def plan(self, design, screen):
        """Plan the applicable questions (plan_questions) once; returns them. Nothing when the plan is switched
        off."""
        if not self.enabled("plan_and_critic"):
            return []
        planned = self.state()["questions"]
        out = []
        for q in plan_questions(design, screen):
            if q["question_id"] not in planned:
                self.append("question_planned", q, PLAN)
            out.append(q)
        return out

    def critic(self):
        return critic(self.state()) if self.enabled("plan_and_critic") else []

    def blocks_report(self):
        return blocks_report(self.state()) if self.enabled("plan_and_critic") else []

    # ---- runs, logged by the tools that make them
    build_of = None  # set by the analysis service: the ID of the screen object's build a run reads now
    rule_of = None  # set by the analysis service: the cells' rule a run reads the object with now, as a short hash
    job_limit_seconds = None  # set by the analysis service from the workspace's resources; None: the runner's own

    def start_run(self, what, cmd, inputs, root, question_id=None, name="run", uses=None, step=None, why=None,
                  thread_id=None, threads=None, source=None):
        """Start a run as a runner job and log it, with the build of the screen object it reads; returns the run ID
        (its job ID). uses: the objects it takes; step: the paradigm step it serves; threads: the job's (the
        runner's default, limits.THREAD_LIMIT, unless given); source: who started it, where not the agent (the
        survey, docs/adr/0045)."""
        from . import runner
        jid = runner.start(cmd, name, root, threads=threads, limit_seconds=self.job_limit_seconds)
        rule = self.rule_of() if self.rule_of else None
        self.append("run_logged", {"run_id": jid, "job_id": jid, "what": what,
                                   "code": cmd if isinstance(cmd, str) else " ".join(map(str, cmd)),
                                   "inputs": inputs, "question_id": question_id, "uses": list(uses or []),
                                   "step": step, "build": self.build_of() if self.build_of else None,
                                   **({"why": why} if why else {}), **({"thread_id": thread_id} if thread_id else {}),
                                   **({"source": source} if source else {}),
                                   **({"cells_rule": rule} if rule else {})},
                    TOOL)
        return jid

    def sync_runs(self, root, figures=None):
        """Log the output of every run whose job ended: its state and log tail, and a figure path when the job
        wrote one (figures: run ID -> path, or files named figure*.png in the job's folder). Then enter each figure
        the job wrote (folder_figures) as figure_saved, its ID the run's and the file's, and each table it wrote
        (folder_tables) as an object, <run>:<table>. One sync at a time in a process (a call's thread, the checks
        queue's and the survey's), so no run's end is logged twice."""
        with _SYNCING:
            return self._sync_runs(root, figures)

    def _sync_runs(self, root, figures=None):
        from . import runner
        done = []
        for rid, r in self.state()["runs"].items():
            # a notebook cell's run has no job: run_code finishes it itself, so it is never looked up here
            if r["state"] != "running" or not r.get("job_id"):
                continue
            try:
                st = runner.status(r["job_id"], root)
            except KeyError:  # the job's folder is not under this root: nothing to log from it
                continue
            if st["state"] in runner.ACTIVE:
                continue
            folder = os.path.join(root, r["job_id"])
            drawn = folder_figures(folder, r["what"])
            fig = (figures or {}).get(rid)
            if fig is None and drawn:
                fig = next((f["paths"]["png"] for f in drawn if f["name"] == "figure"), drawn[0]["paths"]["png"])
            self.append("run_finished", {"run_id": rid, "state": st["state"], "output": "\n".join(st["log_tail"]),
                                         "figure": fig}, TOOL)
            for f in drawn:
                self.append("figure_saved", {"figure_id": f"{rid}:{f['name']}", "run_id": rid,
                                             **{k: v for k, v in f.items() if k != "name"}}, TOOL)
            self.append_many([("object_registered", {"object_id": f"{rid}:{name}", "kind": "table", "run_id": rid,
                                                     "path": path}, TOOL)
                              for name, path in folder_tables(folder).items()])
            done.append(rid)
        return done

    def enter_saved_figures(self, run_id, entries, prefix=None):
        """Enter the figures a notebook cell saved with save_figure (their sidecars' contents) as figure_saved."""
        out = []
        for s in entries:
            fid = f"{prefix}/{s['name']}" if prefix else s["name"]
            paths = {"png": s["png"], "pdf": s.get("pdf"), "data": list(s.get("data") or [])}
            self.append("figure_saved", {"figure_id": fid, "run_id": run_id, "paths": paths,
                                         "caption": s.get("caption"), "role": s.get("role"), "checks": s.get("checks"),
                                         "strict": s.get("strict", True), "notes": list(s.get("notes") or [])}, TOOL)
            out.append(fid)
        return out


def _png_title(path):
    """The title kept in a PNG's metadata (the modules' figures keep theirs there), or None."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return (im.info.get("Title") or "").strip() or None
    except Exception:
        return None


def folder_tables(folder):
    """The tables a job wrote in its folder, {name: path}: result.json's list where it has one, else every .tsv
    (and .tsv.gz)."""
    if not os.path.isdir(folder):
        return {}
    names = sorted(os.listdir(folder))
    listed = None
    if "result.json" in names:
        try:
            with open(os.path.join(folder, "result.json"), encoding="utf-8") as fh:
                res = json.load(fh)
            listed = res.get("tables") if isinstance(res, dict) else None
        except ValueError:
            listed = None
    out = {}
    for f in names:
        for ext in (".tsv", ".tsv.gz"):
            if f.endswith(ext) and (listed is None or f[:-len(ext)] in listed):
                out[f[:-len(ext)]] = os.path.join(folder, f)
    return out


def folder_figures(folder, fallback):
    """The figures a job wrote in its folder: every figure*.png except the parts of a two-part figure, each with its
    PDF where one was written, the job's tables (result.json's list, else every .tsv), the module's figure checks for
    its main figure, and a caption: the PNG's title, else the job's summary, else fallback."""
    if not os.path.isdir(folder):
        return []
    names = sorted(os.listdir(folder))
    res = {}
    if "result.json" in names:
        try:
            with open(os.path.join(folder, "result.json"), encoding="utf-8") as fh:
                res = json.load(fh)
        except ValueError:
            res = {}
    listed = [f"{t}.tsv" for t in res.get("tables") or []] if isinstance(res, dict) else []
    tables = [os.path.join(folder, f) for f in (listed or [f for f in names if f.endswith(".tsv")]) if f in names]
    out = []
    for f in names:
        if not (f.startswith("figure") and f.endswith(".png")) or "_part_" in f:
            continue
        stem = f[:-4]
        paths = {"png": os.path.join(folder, f), "data": tables}
        if stem + ".pdf" in names:
            paths["pdf"] = os.path.join(folder, stem + ".pdf")
        out.append({"name": stem, "paths": paths,
                    "caption": _png_title(paths["png"]) or (res.get("summary") if isinstance(res, dict) else None)
                    or fallback,
                    "role": None, "figure_checks": res.get("figure_checks") if stem == "figure" else None})
    return out


# ---- the plan's rules (docs/build/03_plan_rules_and_hand_lists.md) ----------------------------------------------
def _v(design, key):
    n = design_gate.node(design, key)
    return None if n is None else n.get("value")


def modalities(design):
    """The confirmed modalities as codes ('crispri', 'crispra', 'crispr_ko', 'other'): one value, a list, or a text
    naming several ('CRISPRa and CRISPRi', 'CRISPRa+CRISPRi'); for a mixed library, the modes its guides carry
    (perturbation.guides, ADR 0026)."""
    v = _v(design, "perturbation.modality")
    items = v if isinstance(v, list) else [v] if v else []
    out = set()
    for x in items:
        for part in str(x).replace("+", ",").replace("/", ",").replace(" and ", ",").split(","):
            code = design_gate.modality_code(part)
            if code:
                out.add(code)
    modes = {design_gate.modality_code(a["mode"]) or "other" for a in design_gate.guide_attributes(design).values()
             if a.get("mode")}  # a mode Pertura does not know is "other"
    if modes:  # the guides say how they act: their modes stand for "mixed" or "other"
        out = (out - {"other"}) | modes
    return out


def plan_questions(design, screen):
    """The applicable questions A-H for a confirmed design and its screen object's facts (screen: n_targets,
    guides_per_target, pairs). Each with the reason it applies."""
    mods = modalities(design)
    context = str(_v(design, "experiment.context") or "")
    conditions = design_gate.condition_names(_v(design, "experiment.conditions"))
    vectors = [v for v in (_v(design, "perturbation.vectors") or []) if isinstance(v, (list, dict))]
    pairs = bool(screen.get("pairs")) or bool(_v(design, "perturbation.sets"))
    time_course = bool(_v(design, "experiment.time_points") or _v(design, "experiment.differentiation"))
    gpt = screen.get("guides_per_target") or 0
    n_targets = screen.get("n_targets") or 0
    n_units = screen.get("n_units") or 0
    organism = str(_v(design, "experiment.organism") or "")
    kinds = set(design_gate.control_kinds(_v(design, "perturbation.controls")))
    acting = bool(kinds & {"intergenic", "safe_targeting", "cutting_control"}) and "non_targeting" in kinds
    rules = [
        ("A", "what was done", True, "always"),
        ("B", "guide calls and their evidence", True, "always"),
        ("B", "vector structure", bool(vectors) or pairs, "vectors of two or more guides, or two guides per cell by "
                                                          "design"),
        ("B", "efficacy", bool(mods & {"crispri", "crispra", "crispr_ko"}), "a modality acting on the own gene"),
        ("B", "escape", bool(mods & {"crispri", "crispr_ko"}), "CRISPRi or knockout"),
        ("B", "dose", bool(mods & {"crispri", "crispra"}), "CRISPRi or CRISPRa"),
        ("B", "abundance", True, "always"),
        ("B", "clonality", context == "in_vivo", "in vivo"),
        ("C", "any effect", True, "always"),
        ("C", "genes", True, "always"),
        ("C", "programs", True, "always"),
        ("C", "states and composition", True, "always"),
        ("C", "cell cycle", organism in ("human", "mouse"), "human or mouse: each target's cell-cycle phase shares"),
        ("C", "trajectories and fate", time_course, "time points or a differentiation"),
        ("D", "common response", n_targets >= 3, "3 or more targets: each also against the other targets' cells"),
        ("D", "cutting response", acting, "control guides that target where no gene is beside non-targeting ones"),
        ("E", "similar targets", n_targets >= 3, "3 or more targets"),
        ("E", "opposite modes", {"crispra", "crispri"} <= mods, "CRISPRa and CRISPRi in one library"),
        ("E", "combinations", pairs, "pairs or sets"),
        ("E", "condition x perturbation", len(conditions) >= 2, "2 or more conditions"),
        ("F", "guides agree", gpt >= 2, "2 or more guides per target"),
        ("F", "replicates agree", n_units >= 2, "2 or more samples or lanes: the screen's reproducibility, replicate "
                                               "against replicate, before pooling"),
        ("F", "findings checked", True, "always"),
        ("F", "selection on outcomes", True, "always"),
        ("G", "meaning", True, "always"),
        ("H", "what next", True, "always"),
    ]
    return [{"question_id": f"{letter}.{item.replace(' ', '_')}", "letter": letter, "item": item, "because": why}
            for letter, item, applies, why in rules if applies]


def screen_facts(screen_dir):
    """The facts the plan reads from a screen object folder: targets, guides per target (median), whether labels
    pair perturbations ('a|b') or name sets ('a+b'), and the samples with 30 cells or more."""
    import pandas as pd
    g = pd.read_csv(os.path.join(screen_dir, "guides.tsv"), sep="\t")
    targeting = g[g["kind"] == "targeting"]
    per = targeting.groupby("target")["guide"].nunique()
    with open(os.path.join(screen_dir, "screen.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    labels, units = summary["labels"], summary.get("units") or {}
    pairs = sum(n for lab, n in labels.items() if "|" in lab or "+" in lab) >= 0.1 * max(sum(labels.values()), 1)
    return {"n_targets": int(len(per)), "guides_per_target": float(per.median()) if len(per) else 0, "pairs": pairs,
            "n_units": sum(1 for n in units.values() if n >= 30)}


# ---- the critic's query -----------------------------------------------------------------------------------------
def next_check(st):
    """The open check most likely to change the main result (docs/adr/0045, amendment 1), from the record alone: on
    the first result not dropped (else on the findings of the threads followed), a doubt still open or not answered,
    then a finding whose checks disagree, then one whose checks have not run; None
    where nothing is open. It lists, never blocks."""
    results = sorted((r for r in (st.get("results") or {}).values() if r.get("decision") != "dropped"),
                     key=lambda r: r["seq"])
    if results:
        on, fids = f"result {results[0]['result_id']}", list(results[0]["finding_ids"])
    else:
        on = "the main line"
        fids = [f for t in (st.get("threads") or {}).values() if t.get("status") == "followed"
                for f in t.get("findings") or []]
    if not fids:
        return None

    def rank(d):
        a = d.get("answer") or {}
        if (not a and not d["branches"]) or a.get("outcome") in OPEN_ANSWERS:
            return 1, "not answered"
        return None
    open_ = sorted((rank(d), d["seq"], did) for did, d in st["doubts"].items()
                   if d.get("finding_id") in fids and rank(d))
    if open_:
        (_, why), _, did = open_[0]
        d = st["doubts"][did]
        return {"doubt_id": did, "on": on, "check": str(d["text"]), "why": why,
                "would_change": d.get("would_change")}
    for why, test in (("its checks disagree", checks_disagree),
                      ("its checks have not run", lambda f: checkable(f) and not checks_ran(f))):
        hit = next((f for f in fids if f in st["findings"] and test(st["findings"][f])), None)
        if hit:
            return {"finding_id": hit, "on": on, "check": "the finding's own checks", "why": why}
    return None


def critic(st):
    """What is still open: [{kind, id, what}]. It lists and never blocks."""
    out = []
    # docs/adr/0032: the user's question, the paradigm, each question's status and each run's reading are no longer
    # obligations of the flow, so they are not listed
    for fid, f in st["findings"].items():
        if not checks_ran(f) and checkable(f):
            out.append({"kind": "finding whose checks have not run", "id": fid,
                        "what": "they start when it is recorded; if their run failed or was stopped, reword_finding "
                                "with check_again starts them again"})
        elif checks_disagree(f):
            out.append({"kind": "finding whose checks disagree", "id": fid, "what": disagreement(f)})
    for did, d in st["doubts"].items():
        answer = d.get("answer")
        if answer and answer["outcome"] in OPEN_ANSWERS:
            out.append({"kind": "doubt still open", "id": did, "what": f"{d['text']}: {answer['reason']}"})
        elif not answer and not d["branches"]:
            out.append({"kind": "doubt without an answer", "id": did, "what": str(d["text"])})
    out += threads_without_an_answer(st)
    out += threads_without_an_end(st, {i["id"] for i in out if i["kind"] == "thread without an answer"})
    out += [{"kind": "place a line stopped without an answer", "id": b["id"],
             "what": f"{b['what']}: a thread that routes around it, or why no route applies"}
            for b in blocks_without_an_answer(st)]
    for qid in st["order"]:
        q = st["questions"][qid]
        if q["status"] is not None and q.get("sufficiency") and not q["sufficiency"]["sufficient"]:
            out.append({"kind": "question closed but judged not sufficient", "id": qid,
                        "what": "; ".join(map(str, q["sufficiency"]["reasons"]))})
    for fid, runs in previous_screen_findings(st).items():
        read = [st["runs"][r].get("build") or first_build(st) for r in runs]
        out.append({"kind": "finding on the previous screen", "id": fid,
                    "what": f"its runs {', '.join(runs)} read the screen object before "
                            f"{what_changed(st, read[0]) or 'a rebuild'}; rerun them on this build, then reword the "
                            "finding"})
    out += dossier_items(st)
    out += result_items(st)
    out += contradictions(st)
    out += known_items(st)
    out += skeptic_due(st)
    for rid, r in st["runs"].items():
        if r["output"] is None:
            out.append({"kind": "run without output", "id": rid, "what": r["what"]})
    return out


def current_builds(st):
    """The builds whose runs read the screen object as it is now: the latest, and those before it that no rebuild
    changed (a rebuild that changes no cell's label, unit or condition marks nothing)."""
    out, b = set(), st.get("build")
    while b is not None and b not in out:
        out.add(b)
        info = (st.get("builds") or {}).get(b) or {}
        if info.get("changed", True):
            break
        b = info.get("previous")
    return out


def first_build(st):
    """The build a record's first runs read: the build before its first noted rebuild, else its first build."""
    builds = sorted((st.get("builds") or {}).items(), key=lambda kv: kv[1]["seq"])
    if not builds:
        return None
    bid, info = builds[0]
    return info.get("previous") or bid


def on_previous_screen(st, run):
    """Whether a run read an earlier build of the screen object that a rebuild has changed since (a run logged
    before builds had IDs read the record's first build)."""
    if not run or not st.get("build"):
        return False
    b = run.get("build") or first_build(st)
    return bool(b and b not in current_builds(st))


def what_changed(st, since=None):
    """What the rebuilds that changed the cells after the build `since` changed (every such rebuild when since is
    None, or not found), in words; None when none did."""
    builds = sorted((st.get("builds") or {}).items(), key=lambda kv: kv[1]["seq"])
    ids = [bid for bid, _ in builds]
    if since in ids:  # the rebuilds after it
        start = ids.index(since) + 1
    else:  # a build kept from before the record's builds: the rebuild from it on
        start = next((k for k, (_, info) in enumerate(builds) if since and info.get("previous") == since), 0)
    changed = [info for _, info in builds[start:] if info.get("changed") and info.get("previous")]
    if not changed:
        return None
    if any((i.get("what") or {}).get("unknown") for i in changed):
        return "the rebuild's changes are not known: the earlier build's cells were not kept"
    n = {k: sum(int((i["what"] or {}).get(k, 0)) for i in changed) for k in ("cells_changed", "cells_gone", "cells_new")}
    ts = sorted({t for i in changed for t in (i["what"] or {}).get("targets") or []})
    controls = any((i["what"] or {}).get("controls") for i in changed)
    return (f"the rebuild{'s' if len(changed) > 1 else ''} changed {n['cells_changed']} cells' label, unit or "
            f"condition, {n['cells_gone']} cells left and {n['cells_new']} came"
            + ("; the control cells changed, so every target's reference did" if controls else "")
            + (f"; targets {', '.join(ts[:6])}" + (f" and {len(ts) - 6} more" if len(ts) > 6 else "") if ts else ""))


def previous_screen_findings(st):
    """The findings resting on a run of an earlier build that a rebuild changed: {finding_id: [run IDs]}."""
    out = {}
    for fid, f in st["findings"].items():
        runs = [r for r in f.get("run_ids") or [] if on_previous_screen(st, st["runs"].get(r))]
        if runs:
            out[fid] = runs
    return out


def checkable(finding):
    """Whether a finding names what its checks need: a target, a feature and a direction (docs/adr/0032, D6)."""
    return bool(finding.get("target") and finding.get("feature") and finding.get("direction"))


def kept_by_user(finding):
    """The user's latest keep of a finding (its reply's seq), or None."""
    keeps = [r["seq"] for r in finding.get("replies") or [] if r["kind"] == "keep"]
    return max(keeps) if keeps else None


def checks_by_target(finding):
    """A finding's checks by target, {target: {check: entry}}: a claim on several targets has its own for each, and
    a claim on one (or a record from before several) has the finding's checks."""
    return finding.get("checks_by_target") or {finding.get("target"): finding.get("checks", {})}


def checks_ran(finding):
    """Whether the tool has run the finding's checks (or, in a record the robustness engine wrote, its check)."""
    ran = any(c.get("by") == TOOL for each in checks_by_target(finding).values() for c in each.values())
    return ran or "robustness" in finding.get("checks", {})


def checks_disagree(finding):
    """Whether its checks disagree with it: the lead test does not show its way, its guides or halves disagree, or a
    re-test gives the other direction (docs/adr/0025, section 3)."""
    return bool(_disagreeing(finding))


def _disagreeing(finding):
    out, several = [], bool(finding.get("checks_by_target"))
    for target, each in checks_by_target(finding).items():
        for name, c in each.items():
            if (name == "lead" and c["outcome"] in LEAD_AGAINST) or (name != "lead" and c["outcome"] in DISAGREE):
                out.append(f"{target} {name}: {c['outcome']}" if several else f"{name}: {c['outcome']}")
    return out


def disagreement(finding):
    return "; ".join(_disagreeing(finding))


def latest_verdict(finding):
    """A finding's latest robustness verdict and its reason, or (None, None)."""
    last = latest_plan(finding)
    return (None, None) if last is None else (last["verdict"], last.get("reason"))


def latest_plan(finding):
    """A finding's latest robustness plan with a verdict, or None."""
    done = [p for p in finding.get("robustness", {}).values() if p.get("verdict")]
    return max(done, key=lambda p: p["planned_seq"]) if done else None


def conclusive(finding):
    """Robust, or conditional with the choice it depends on named."""
    v, why = latest_verdict(finding)
    return v == "robust" or (v == "conditional" and bool(why))


def depends_on_a_method(finding):
    """Whether its latest sensitivity check shows it holding under some methods and contradicted under others (the
    related measures are not counted: they ask another question)."""
    last = latest_plan(finding)
    if last is None:
        return False
    methods = {"primary"} | {v.get("name") for v in last["variants"] if v.get("kind", "method") == "method"}
    outcomes = {r["outcome"] for name, r in last["results"].items() if name in methods}
    return {"holds", "contradicted"} <= outcomes


def dossier_items(st):
    """The critic's items for the results (dossiers) the user chose."""
    out = []
    if st["dossiers"] and not st["chosen"]:
        out.append({"kind": "results proposed, none chosen", "id": "dossiers", "what": ", ".join(st["dossiers"])})
    for did in st["chosen"]:
        d = st["dossiers"][did]
        missing = [p for p in DOSSIER_PARTS if p not in d["parts"]]
        if missing:
            out.append({"kind": "result part missing", "id": did, "what": ", ".join(missing)})
        size = d["map_size"] or {}
        if size.get("measured") is False:  # the agent's own figure: its size is not read, so no item
            pass
        elif (size.get("targets") or 0) < MAP_TARGETS and (size.get("genes") or 0) < MAP_GENES:
            out.append({"kind": "result's main figure covers few targets or genes", "id": did,
                        "what": f"its main figure covers {size.get('targets') or 0} targets and "
                                f"{size.get('genes') or 0} genes, fewer than the {MAP_TARGETS} targets or {MAP_GENES} "
                                "genes this list takes for a map (a mark of the list, not a rule: a result on one or "
                                "two targets can stand on a smaller figure)"})
        for f in d["lead_findings"]:
            if not checks_ran(st["findings"][f]):
                out.append({"kind": "lead finding whose checks have not run", "id": did,
                            "what": f"{f}: if their run failed or was stopped, reword_finding with check_again "
                                    "starts them again"})
            elif checks_disagree(st["findings"][f]):
                out.append({"kind": "lead finding whose checks disagree", "id": did,
                            "what": f"{f}: {disagreement(st['findings'][f])}"})
        for t in (d["parts"].get("panel_and_paragraph") or {}).get("tiles") or []:
            if t.get("figure_id") not in st["figures"]:
                out.append({"kind": "result figure not in the record", "id": did,
                            "what": f"{t.get('figure', 'main')} figure, tile {t.get('letter')} (run "
                                    f"{t.get('run_id')}): save it with save_figure, then review it"})
    return out


def results_shown(st):
    """The results the report shows, in order: those reported and not dropped."""
    return [r for r in (st.get("results") or {}).values() if r.get("decision") != "dropped"]


def findings_no_result_cites(st):
    """The findings no shown result rests on, by thread ("main line" for the agent's own), once a result is proposed:
    the lead weighs each for a result or leaves it as evidence (GSE203592 on 99a1f49: the cytotoxic genes up and
    INO80's metabolic programmes stayed in their threads; GSE267740: the four sets of targets alike)."""
    shown = results_shown(st)
    if not shown:
        return {}
    cited = {f for r in shown for f in (r.get("finding_ids") or [])}
    out = {}
    for fid, f in (st.get("findings") or {}).items():
        if fid not in cited:
            out.setdefault(f.get("thread_id") or "main line", []).append(fid)
    return out


def set_figures(st):
    """The results' figures as they stand, each with its version: what a review of the figures as a set reads
    (docs/adr/0049, D5)."""
    return [{"result_id": r["result_id"], "figure_id": r["figure"], "version": st["figures"][r["figure"]]["version"]}
            for r in results_shown(st) if r.get("figure") in st["figures"]]


def set_changed_since(st, review):
    """Whether a result's figure is newer than the set's review, or was not in it: a figure redrawn or a result
    added. A review recorded before it named its figures reads by its seq: a result proposed or changed since."""
    if review.get("figures") is None:
        return _latest_result_seq(st) > review["seq"]
    read = {(x["figure_id"], x["version"]) for x in review["figures"]}
    return any((x["figure_id"], x["version"]) not in read for x in set_figures(st))


def result_items(st):
    """The critic's items for the results: a finding a result rests on whose checks have not run or disagree."""
    out = []
    for r in results_shown(st):
        for f in r["finding_ids"]:
            if not checkable(st["findings"][f]):
                continue
            if not checks_ran(st["findings"][f]):
                out.append({"kind": "lead finding whose checks have not run", "id": r["result_id"],
                            "what": f"{f}: if their run failed or was stopped, reword_finding with check_again "
                                    "starts them again"})
            elif checks_disagree(st["findings"][f]):
                out.append({"kind": "lead finding whose checks disagree", "id": r["result_id"],
                            "what": f"{f}: {disagreement(st['findings'][f])}"})
    return out


def made_after(st, run_ids, seq):
    """The runs of run_ids made after an event (its seq): a test cites a run made for it, not one from before."""
    return [r for r in run_ids or [] if (st["runs"].get(r) or {}).get("seq", -1) > (seq or 0)]


def doubt_tested(st, did):
    """Whether a run made after the doubt tested it: one its answer cites, or one of the thread that tests it. On
    GSE190604 (a9510b4) two answers cited a run from before the doubt, as the run the refusal asked for."""
    d = st["doubts"][did]
    if made_after(st, (d.get("answer") or {}).get("run_ids"), d.get("seq")):
        return True
    return any(r.get("thread_id") in d.get("branches", []) and r.get("seq", -1) > (d.get("seq") or 0)
               for r in st["runs"].values())


def ran_since_opened(st, tid):
    """Whether a thread had a run of its own after it opened: named with it, or cited by its ending. On GSE190604
    (a9510b4) the line's next step ended 15 s after it opened, on a run from before it and a description written to
    pass the refusal."""
    th = st["threads"][tid]
    cited = {r for c in th.get("changes") or [] for r in c.get("run_ids") or []}
    return bool(made_after(st, cited, th.get("seq"))) or any(
        r.get("thread_id") == tid and r.get("seq", -1) > (th.get("seq") or 0) for r in st["runs"].values())


def contradictions(st):
    """The critic's items for results resting on a finding that a later thread or doubt contradicted: a thread ended
    contradicted, on the finding its doubt is on (or that raised it), or a doubt answered as overturning. A result
    reported again after it is taken as read. On GSE190604 (a9510b4) t12 contradicted R2's cell-cycle finding, and
    the contradiction lived only in t12's reason."""
    against = []  # (finding, seq, what)
    for tid, th in (st.get("threads") or {}).items():
        if th.get("status") == "contradicted" and th.get("changes"):
            doubt = (st.get("branches", {}).get(tid) or {}).get("doubt_id")
            f = (st["doubts"].get(doubt) or {}).get("finding_id") if doubt else th.get("raised_by")
            against.append((f, th["changes"][-1]["seq"], f"thread {tid} contradicts it: {th.get('reason')}"))
    for did, d in st["doubts"].items():
        a = d.get("answer") or {}
        if a.get("outcome") == "overturns":
            against.append((d.get("finding_id"), a.get("seq"), f"doubt {did} overturns it: {a.get('reason')}"))
    return [{"kind": "lead finding a later thread or doubt contradicts", "id": r["result_id"], "what": f"{f}: {what}"}
            for f, seq, what in against if f in st["findings"]
            for r in results_shown(st) if f in r["finding_ids"] and (r.get("seq") or 0) < (seq or 0)]


def unanswered(thread):
    """Whether no one has answered a thread: it is open, and its status has not changed since it was opened."""
    return thread.get("status") == "open" and not thread.get("changes")


def module_runs(st):
    """{module: its runs on the screen object as it is now, in order}: the module runs on every target (a run on some
    targets, or on a cell set, does not read the screen as a whole). The screen overview and the turn-end line read
    the same."""
    out = {}
    for r in st["runs"].values():
        what = str(r.get("what") or "")
        if not (r.get("job_id") and what.startswith("module ")) or on_previous_screen(st, r):
            continue
        if " with " in what and any(k in what.split(" with ", 1)[1] for k in ('"targets"', '"only"', '"cells"')):
            continue
        out.setdefault(what.split()[1], []).append(r)
    return out


def runs_read(st, run_id):
    """Every run whose output a run read: the runs its trace names (a run, one of its tables, a figure it saved, a file
    or object it made), and through each of them the runs that one read, since a notebook cell may draw what an earlier
    cell loaded."""
    out, todo = set(), [run_id]
    while todo:
        for used in map(str, (st["runs"].get(todo.pop()) or {}).get("uses") or []):
            run = used.partition(":")[0]
            run = run if run in st["runs"] else ((st.get("figures", {}).get(used) or {}).get("run_id")
                                                 or (st.get("objects", {}).get(used) or {}).get("run_id"))
            if run and run != run_id and run not in out:
                out.add(run)
                todo.append(run)
    return out


def plan_items(st):
    """The agent's plan as it stands, in its order and then the items left: each with its status (done: a finished
    run names it as its step, or the agent marked it done with what did it; left: left out of a later version, with
    that version's reason; else open) and the runs that name it."""
    plan = st.get("plan")
    if not plan:
        return []
    runs_of = {}
    for rid, r in st["runs"].items():
        if r.get("step") is not None:
            runs_of.setdefault(str(r["step"]), []).append(r)
    out = []
    for iid in plan["order"]:
        i = plan["items"][iid]
        rs = runs_of.get(iid, [])
        done = i.get("status") == "done" or any(r["state"] not in NOT_DONE for r in rs)
        out.append({**i, "status": "done" if done else "open", "runs": [r["run_id"] for r in rs], "left_why": None})
    for iid, i in plan["left"].items():
        out.append({**i, "status": "left", "runs": [r["run_id"] for r in runs_of.get(iid, [])]})
    return out


def plan_next(st):
    """The plan's first open item with its how, for the status and the user's page; once every item is done, the
    results it supports; None before any plan."""
    items = plan_items(st)
    if items:
        nxt = next((i for i in items if i["status"] == "open"), None)
        if nxt is None:
            return ("every item of your plan is done: propose the results it supports (report_result), or write_plan "
                    "again with why, if the data call for more")
        return f"your plan's next item {nxt['id']}: {nxt['does']} ({nxt['how']}); give step {nxt['id']} when you run it"
    return None


def threads_without_an_answer(st):
    """The threads the reader one level up and the advisor opened, and the first look's about the whole screen, that no
    one has answered (taken, or set aside with its reason): the report lists them. It never blocks."""
    out = []
    for tid, th in (st.get("threads") or {}).items():
        asked = (th.get("raised_by") in ("one level up", ADVISOR) or th.get("kind") in SCREEN_THREADS
                 or th.get("kind") in (OBSERVATION, BREADTH))
        if asked and unanswered(th):
            out.append({"kind": "thread without an answer", "id": tid,
                        "what": f"{th.get('about')}: taken (thread_status followed, and why) or set aside with its "
                                "reason"})
    return out


def threads_without_an_end(st, listed=()):
    """The first look's observations and the threads reading a kind of conclusion (and, in a record made before the
    research lead was retired, its threads), taken up and not ended (lead_threads_without_an_end), beside those no one
    has answered (listed): the report lists them, and nothing waits for them (docs/adr/0042, amendment 1)."""
    ths = st.get("threads") or {}
    loose = dict(lead_threads_without_an_end(st))
    return [{"kind": "thread without an end", "id": t,
             "what": f"{ths[t].get('about')} ({status}): ended with its evidence, followed into a finding, or set "
                     "aside with its reason"} for t, status in loose.items() if t not in listed]


def enough_stands(st):
    """Whether the user's enough stands (docs/build/the_record_as_it_stands_2026-11-16.md): recorded, with no later
    reply of another kind from the user (the analysis's own notes aside) and no thread opened after it, as a complete
    gives way to a thread opened after it (lead_complete). The enough itself stays in the record for the report."""
    e = st.get("enough")
    if not e:
        return False
    if any((r.get("seq") or 0) > e["seq"] and r.get("kind") != "enough" and not r.get("by_analysis")
           for r in st.get("replies") or []):
        return False
    return not any((th.get("seq") or 0) > e["seq"] for th in (st.get("threads") or {}).values())


def skeptic_due(st):
    """The findings the skeptic reads, once each result is proposed: those of each proposed result (a lead's dossier,
    docs/adr/0030) it has not read, once. On three screens it read them all at the end (docs/adr/0047, A6), and 43 of
    its 47 doubts were stated as limits with no run (the fresh review of 2026-11-13, #2). A result read stays read: a
    finding that joins it later, a rewording or new evidence does not make it due again, and its doubts are listed in
    the report (each answered doubt's finding joining the result made it due again, 2026-11-07, P3); another reading
    is the user's to ask for."""
    leads = {f for d in st["dossiers"].values() for f in d["lead_findings"]} | {
        f for r in results_shown(st) if not r.get("skeptic") for f in r["finding_ids"]}
    return [{"kind": "skeptic due", "id": fid, "what": "a result rests on it"}
            for fid, f in st["findings"].items() if fid in leads and not f.get("skeptic")]


def _latest_result_seq(st):
    return max([r.get("seq") or 0 for r in (st.get("results") or {}).values()] or [0])


def said(issue):
    """A review's issue in words: given as text, or as an object holding its text ({issue, text}, as the figure
    reviewer gave the set's issues in D on 0bbbad0, when a join of them broke the critic, the status and the report)."""
    if isinstance(issue, dict):
        return str(issue.get("text") or issue.get("issue") or json.dumps(issue))
    return str(issue)


def lead_threads_without_an_end(st):
    """The research lead's threads (raised one level up), the observations of the first look (docs/adr/0039, D3) and
    the threads reading a kind of conclusion of the design (P6) that have neither ended nor been followed into a
    finding, as [(thread_id, status)] (docs/adr/0038, D4)."""
    return [(t, th.get("status") or "open") for t, th in (st.get("threads") or {}).items()
            if (th.get("raised_by") == "one level up" or th.get("kind") in (OBSERVATION, BREADTH))
            and th.get("status") not in THREAD_ENDS and not (th.get("status") == "followed" and th.get("findings"))]


def observations_not_yet_explained(st):
    """The observations of the first look and the research lead's threads that no one has ended or followed into a
    finding, the strongest first (threads.strength_order), as the lead's coverage gives them: its first thread takes
    one up, with the comparison that would tell its explanations apart. It lists; which one matters is the lead's."""
    from . import threads
    ths = st.get("threads") or {}
    loose = dict(lead_threads_without_an_end(st))
    return [{"thread_id": t, "about": ths[t].get("about"), "status": loose[t]} for t in threads.strength_order(st)
            if t in loose and (ths[t].get("kind") == OBSERVATION or ths[t].get("raised_by") == "one level up")]


def block_answers(st):
    """The research lead's answer to each block, the latest by its ID: {id: {id, kind, thread_id, reason, seq}}."""
    out = {}
    for r in st.get("reviews") or []:
        for b in r.get("blocks") or []:
            out[b["id"]] = {**b, "seq": r["seq"]}
    return out


def block_candidates(st):
    """Where a line stopped, by structure (P16): the threads ended out of data or set aside, and the doubts still
    open on a proposed result's findings. Code reads no words: the lead says each one's kind, and the thread that
    routes around it or why no route applies. A thread named as a block's route is no candidate itself."""
    routes = {b.get("thread_id") for b in block_answers(st).values() if b.get("thread_id")}
    on_results = {f for r in results_shown(st) for f in r["finding_ids"]}
    out = []
    for tid, th in (st.get("threads") or {}).items():
        if tid not in routes and th.get("status") in ("out of data", "set aside"):
            out.append({"id": tid, "from": "thread", "what": f"{th.get('about')}: {th['status']}, {th.get('reason')}"})
    for did, d in (st.get("doubts") or {}).items():
        a = d.get("answer") or {}
        if a.get("outcome") in OPEN_ANSWERS and d.get("finding_id") in on_results:
            out.append({"id": did, "from": "doubt", "what": f"{d.get('text')}: {a['outcome']}, {a.get('reason')}"})
    return out


def blocks_without_an_answer(st, given=()):
    """The block candidates the lead has not answered (block_answers, with given: a review's own answers)."""
    answered = set(block_answers(st)) | {b.get("id") for b in given}
    return [b for b in block_candidates(st) if b["id"] not in answered]


def thread_chains(st):
    """Each thread's chain of new questions: a thread raised by, or joining, the findings or runs of earlier threads
    follows the deepest of them, and adds one when its own step is a new question; a check, a new area or report
    work adds none. {thread_id: (new questions, [the new-question threads of its chain])}."""
    of_finding = {fid: f.get("thread_id") for fid, f in (st.get("findings") or {}).items() if f.get("thread_id")}
    of_run = {rid: r.get("thread_id") for rid, r in (st.get("runs") or {}).items() if r.get("thread_id")}
    out = {}
    for tid, th in (st.get("threads") or {}).items():  # in the order opened: a thread's parents come first
        named = [x.strip() for x in str(th.get("raised_by") or "").replace(";", ",").split(",")]
        ups = [u for u in (of_finding.get(x) or of_run.get(x) for x in named + list(th.get("joins") or []))
               if u in out and u != tid]
        n, chain = max((out[u] for u in ups), key=lambda c: c[0], default=(0, []))
        out[tid] = (n + 1, chain + [tid]) if th.get("step") == NEW_QUESTION else (n, chain)
    return out


def new_questions_behind(st, finding_ids, chains=None):
    """The longest chain of new questions behind some findings, through their threads: (count, thread IDs)."""
    chains = thread_chains(st) if chains is None else chains
    ts = [(st["findings"].get(f) or {}).get("thread_id") for f in finding_ids]
    return max((chains[t] for t in ts if t in chains), key=lambda c: c[0], default=(0, []))


def result_facts(st, result):
    """What a result rests on, counted (docs/adr/0039, D2): its findings, the targets they name, how many had their
    checks run and how many of those agree, how many carry prior knowledge, and how many came from a thread that
    tested something (an observation's probe, a thread one level up, or one that names what would tell it apart).
    Facts for the lead and the reader; nothing is required of them. prior_looked_up: the findings with prior
    knowledge looked up in this session (looked_up)."""
    fs = [st["findings"][f] for f in result.get("finding_ids") or [] if f in st["findings"]]
    targets = sorted({t for f in fs for t in ([f.get("target")] if f.get("target") else []) + list(f.get("targets")
                                                                                                 or [])})
    with_prior = {k.get("finding_id") for k in st.get("knowledge") or [] if k.get("finding_id")}
    looked = {k.get("finding_id") for k in st.get("knowledge") or [] if k.get("finding_id") and looked_up(st, k)}
    threads = st.get("threads") or {}

    def tested(f):
        th = threads.get(f.get("thread_id")) or {}
        return th.get("kind") == OBSERVATION or th.get("raised_by") == "one level up" or bool(th.get("tells_apart"))
    checked = [f for f in fs if checks_ran(f)]
    return {"findings": len(fs), "targets": targets, "checked": len(checked),
            "checks_agree": sum(1 for f in checked if not checks_disagree(f)),
            "with_prior_knowledge": sum(1 for f in fs if f.get("finding_id") in with_prior),
            "prior_looked_up": sum(1 for f in fs if f.get("finding_id") in looked),
            "from_a_thread_that_tested_something": sum(1 for f in fs if tested(f)),
            "new_questions_behind_it": new_questions_behind(st, result.get("finding_ids") or [])[0]}


PMID = re.compile(r"PMID[:\s]*(\d{5,9})", re.I)
DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;)\]]+", re.I)


def _papers(text):
    """The PMIDs and DOIs a text names, as 'pmid:<n>' and 'doi:<lower case>'."""
    text = str(text or "")
    return {f"pmid:{m}" for m in PMID.findall(text)} | {f"doi:{m.lower().rstrip('.')}" for m in DOI.findall(text)}


def looked_up(st, k):
    """Whether an item of prior knowledge was looked up in this session (docs/adr/0041, step 5): a paper one of its
    literature searches returned (by PMID or DOI), one of its searches named as the source ('search: <query>', where
    it found nothing), the user, or a gene-set base or G module that code read in it. Anything else is the agent's
    memory, which may be right and is not checked."""
    if k.get("by") == TOOL:  # code's own reading of a base or a search
        return True
    said = f"{k.get('source') or ''} {k.get('item') or ''}".strip().lower()
    if str(k.get("source") or "").strip().lower() == "the user":
        return True
    searches = [x for x in st.get("knowledge") or [] if x.get("question_id") == "literature"]
    if _papers(said) & {p for x in searches for p in _papers(x.get("text"))}:
        return True
    if any(str(x.get("item") or "").strip().lower() in said for x in searches if x.get("item")):
        return True
    bases = set()
    for x in st.get("knowledge") or []:
        if x.get("by") == TOOL and x.get("question_id") != "literature":
            if x.get("run_id"):  # a G module's run, named by its module or its run
                bases |= {str(x.get("question_id") or ""), str(x["run_id"])}
            else:  # a base read through gene_sets, named by the base (MSigDB)
                bases.add(str(x.get("source") or "").split(" ")[0])
    return any(len(b) >= 4 and b.lower() in said for b in bases)


def standing_problems(st, standings):
    """What the advisor's standings lack (docs/adr/0042, D1): one that reproduces or goes against what is known names
    a paper a literature search of this session returned, and one new as far as the literature shows names the
    searches of this session that found nothing ('search: <query>'). What the advisor remembers leads to a search; it
    is not a standing."""
    def query(said):  # a search's words, as compared: 'search: <query>' without its prefix, spacing and case
        return " ".join(str(said).split(":", 1)[1].split()).lower()

    searches = [k for k in st.get("knowledge") or [] if k.get("question_id") == "literature"]
    returned = {x for k in searches for x in _papers(k.get("text"))}
    asked = {query(k["item"]) for k in searches if str(k.get("item") or "").lower().startswith("search:")}
    # a search that failed and was not run again: nothing is new until it is (docs/adr/0047, A4)
    failed = sorted({query(k["item"]) for k in searches
                     if str(k.get("item") or "").lower().startswith("search failed:")} - asked)
    out = []
    for s in standings or []:
        what, sources = s.get("relation") or "a relation", [str(x) for x in s.get("sources") or []]
        papers = set().union(*map(_papers, sources))
        out += [f"{what}: '{x}' is a source when it is a PMID, a DOI or a search ('search: <query>')"
                for x in sources if not _papers(x) and not x.strip().lower().startswith("search:")]
        out += [f"{what}: {x.upper() if x.startswith('pmid:') else 'DOI:' + x[4:]} came back from no search of "
                "this session; search for it (search_literature with its PMID or DOI alone)"
                for x in sorted(papers - returned)]
        if s.get("standing") in (KNOWN, AGAINST) and not papers & returned:
            out.append(f"{what}: '{s['standing']}' names the paper it rests on, from a search of this session")
        if s.get("standing") == NEW:
            runs = [query(x) for x in sources if x.strip().lower().startswith("search:")]
            if not runs:
                out.append(f"{what}: '{NEW}' names the searches of this session that found nothing "
                           "('search: <query>', the query as you gave it)")
            out += [f"{what}: no search of this session was '{x}'" for x in runs if x not in asked]
            if failed:
                out.append(f"{what}: '{NEW}' waits until the searches of this session that failed have been run "
                           "again: " + "; ".join(failed))
    return out


def known_not_looked_up(st):
    """The moments that call for what is known to be looked up (prior knowledge from memory
    serves, but a claim, a concept, a new way of analysis and something strange want an outside check), where nothing
    beside them was looked up in this session: each result not dropped (its claim and the structure it names), each
    finding that differs from prior knowledge, and each observation opened on the maps."""
    ks = st.get("knowledge") or []
    found = {k.get("finding_id") for k in ks if k.get("finding_id") and looked_up(st, k)}
    beside_thread = {k.get("thread_id") for k in ks if k.get("thread_id") and looked_up(st, k)}
    out = {"results": [rid for rid, r in (st.get("results") or {}).items() if r.get("decision") != "dropped"
                       and not set(r.get("finding_ids") or []) & found and not r.get("advice")],
           "differences": [fid for fid, f in st["findings"].items() if fid not in found
                           and any(d.get("source") == "prior knowledge" for d in f.get("differences") or [])],
           "observations": [t for t, th in (st.get("threads") or {}).items() if th.get("kind") == OBSERVATION
                            and t not in beside_thread and not set(th.get("findings") or []) & found
                            and th.get("status") not in THREAD_ENDS]}
    return {k: v for k, v in out.items() if v}


def known_items(st):
    """The results and the differences from prior knowledge with nothing beside them looked up, as open items: the
    report lists them, and nothing waits for them."""
    left = known_not_looked_up(st)
    return ([{"kind": "result without prior knowledge looked up", "id": rid,
              "what": "the prior knowledge beside its findings is the agent's memory, not looked up in this session"}
             for rid in left.get("results", [])]
            + [{"kind": "difference from prior knowledge not looked up", "id": fid,
                "what": "the knowledge it differs from is the agent's memory, not looked up in this session"}
               for fid in left.get("differences", [])])


def workers_back(st):
    """The threads off the main line a worker took (a run or a finding names them), each with the seq of its first
    end, or None while it is out (docs/adr/0049, D2). A finding on a thread still worked does not bring it back: a
    review while its threads were being worked cost a review each (the build goal of 2026-11-11, step 5)."""
    taken = {r.get("thread_id") for r in st["runs"].values()} | {f.get("thread_id") for f in st["findings"].values()}
    out = {}
    for tid, th in (st.get("threads") or {}).items():
        if th.get("on_line") is False and tid in taken:
            ends = [c["seq"] for c in th.get("changes") or [] if c["status"] in THREAD_ENDS]
            out[tid] = min(ends) if ends else None
    return out


def probed_by_nothing(st, tid, th=None):
    """Whether no finding and no run of a thread's own (one that names it) probed it."""
    th = th if th is not None else (st.get("threads") or {}).get(tid) or {}
    return not th.get("findings") and not any(r.get("thread_id") == tid for r in st["runs"].values())


def set_aside_unprobed(st, tid, th=None):
    """Whether a thread off the main line has nothing that probed it. Set aside so, it is recorded, not refused, and
    the report lists it (docs/adr/0049, D4)."""
    th = th if th is not None else (st.get("threads") or {}).get(tid) or {}
    return th.get("on_line") is False and probed_by_nothing(st, tid, th)


def next_questions_ended_without_a_run(st):
    """The results' next questions (threads next_of a result) that ended with no run or finding of their own: on
    GSE190604 all four ended out of data within 30 seconds, each citing an earlier run, one of them answerable in the
    data. Listed in the lead's coverage and the report, never refused."""
    return [t for t, th in (st.get("threads") or {}).items()
            if th.get("next_of") and th.get("status") in THREAD_ENDS and probed_by_nothing(st, t, th)]


ROOT = "root"  # the tree's root: the question, and the main agent's own findings on no thread
TREE_LINES = 40  # the tree as text, in a reply: at most these lines, ended leaves folded into their parent's


def ended_with_something_read(st, tid):
    """Whether a thread ended with something read for it: an ending status, and a run of its own (named with it, or
    cited by its ending) or a finding recorded on it."""
    th = (st.get("threads") or {}).get(tid) or {}
    if th.get("status") not in THREAD_ENDS:
        return False
    cited = {r for c in th.get("changes") or [] for r in c.get("run_ids") or []}
    return (bool(cited & set(st["runs"])) or any(r.get("thread_id") == tid for r in st["runs"].values())
            or any(f.get("thread_id") == tid for f in st["findings"].values()))


def node_of(st, ref):
    """The tree node a record's entry sits in: a thread is its own node, a finding or a run is its thread's (the
    root where it has none); None for anything else (the user's words, a design key, "one level up")."""
    if not isinstance(ref, str):
        return None
    if ref in (st.get("threads") or {}):
        return ref
    for kind in ("findings", "runs"):
        if ref in (st.get(kind) or {}):
            return st[kind][ref].get("thread_id") or ROOT
    return None


def tree(st):
    """The analysis as a tree: each thread a node under the node it grew
    from: the thread of the finding or run that raised it; for one level up, the deepest node the threads of its
    joins share; for a result's next question, the deepest node its findings sit in; a fork beside the node it
    redoes with one choice changed (fork_of), under that node's parent; the root for anything else. A parent is
    always opened before its child. The main line is the path from the root to its tip: the thread it last moved to
    (main_line_moved), or the latest thread on the line (on_line) opened since and grown from it. Returns {parent,
    children, depth, line, tip, forks}."""
    threads = st.get("threads") or {}
    parent = {}

    def ancestors(n):  # n, its parent, ... the root
        out = []
        while n and n != ROOT:
            out.append(n)
            n = parent.get(n, ROOT)
        return out + [ROOT]

    def common(nodes):
        paths = [list(reversed(ancestors(n))) for n in nodes if n in parent or n == ROOT]
        shared = ROOT
        for level in zip(*paths) if paths else ():
            if len(set(level)) != 1:
                break
            shared = level[0]
        return shared

    for tid, th in sorted(threads.items(), key=lambda kv: kv[1].get("seq") or 0):
        if th.get("fork_of"):
            beside = node_of(st, th["fork_of"])
            p = parent.get(beside, ROOT) if beside in parent else ROOT
        elif th.get("next_of") and th["next_of"] in (st.get("results") or {}):  # where the result's line reached
            r = st["results"][th["next_of"]]  # as it stood when the question was opened: a node never moves later
            then = [v for v in (r.get("versions") or []) + [r] if (v.get("seq") or 0) < (th.get("seq") or 0)]
            fids = (max(then, key=lambda v: v.get("seq") or 0) if then else r).get("finding_ids") or []
            nodes = [n for n in (node_of(st, f) for f in fids) if n in parent]
            p = max(nodes, key=lambda n: (len(ancestors(n)), threads[n].get("seq") or 0)) if nodes else ROOT
        elif th.get("joins"):
            p = common([node_of(st, f) for f in th["joins"]])
        else:
            p = node_of(st, th.get("raised_by")) or ROOT
        parent[tid] = p if p == ROOT or p in parent else ROOT
    children = {}
    for tid, p in parent.items():
        children.setdefault(p, []).append(tid)
    moved = st.get("main_line") or {}
    since, base = moved.get("seq") or 0, moved.get("thread_id")
    on = [t for t, th in threads.items() if th.get("on_line") and (th.get("seq") or 0) > since
          and (base is None or base in ancestors(t))]
    tip = max(on, key=lambda t: threads[t].get("seq") or 0) if on else (base or ROOT)
    return {"parent": parent, "children": children, "depth": {t: len(ancestors(t)) - 1 for t in parent},
            "line": list(reversed(ancestors(tip))), "tip": tip,
            "forks": [t for t, th in threads.items() if th.get("fork_of")]}


def result_chain_text(st, at_most=TREE_LINES):
    """The results as a chain (2026-11-17, for the research lead, in place of the threads' tree with its main line):
    each result under the result that raised it (or the one citing the finding that did), with the question it
    answers, the findings it rests on and the doubts on them with their outcomes."""
    results, findings, doubts = st.get("results") or {}, st.get("findings") or {}, st.get("doubts") or {}
    citing = {f: rid for rid, r in results.items() for f in r.get("finding_ids") or []}
    parent = {rid: (r.get("raised_by") if r.get("raised_by") in results else citing.get(r.get("raised_by")))
              for rid, r in results.items()}
    lines = [f"the question: {(st.get('question') or {}).get('text') or 'none asked'}"]

    def show(rid, depth):
        r, pad = results[rid], "  " * depth
        source = f" (from {r['raised_by']})" if r.get("raised_by") else ""
        lines.append(f"{pad}{rid} [{r.get('decision') or 'proposed'}, version {r.get('version')}]{source}: "
                     f"{str(r.get('title') or '')[:140]}" + (f" | answers: {str(r['answers'])[:120]}"
                                                            if r.get("answers") else ""))
        for fid in r.get("finding_ids") or []:
            f = findings.get(fid) or {}
            told = [f"{did} {(d.get('answer') or {}).get('outcome') or 'open'}" for did, d in doubts.items()
                    if d.get("finding_id") == fid]
            lines.append(f"{pad}  - {fid}: {str(f.get('statement') or '')[:110]}"
                         + (f" | doubts: {', '.join(told)}" if told else ""))
        for kid in [k for k, p in parent.items() if p == rid and k != rid]:
            show(kid, depth + 1)
    for rid in [k for k, p in parent.items() if p is None or p == k]:
        show(rid, 0)
    return "\n".join(lines[:at_most]) + (f"\n... {len(lines) - at_most} lines more" if len(lines) > at_most else "")


def tree_text(st, at_most=TREE_LINES):
    """The tree in lines, as pertura_status and the research lead read it: the question, then each node indented under
    its parent, ★ on the main line, ⑂ a fork with its one change, ☆ once on the line, its status, findings and who
    opened it; ended leaves off the line, forks apart, folded into their parent's line."""
    t, threads = tree(st), st.get("threads") or {}
    line = set(t["line"])
    asked = (st.get("question") or {}).get("text") or "the analysis"
    own = sum(1 for f in (st.get("findings") or {}).values() if not f.get("thread_id"))
    out = [f"{'★' if ROOT in line else '-'} {asked[:90]} · {own} findings of the main agent's own"]

    was = {m["thread_id"] for m in st.get("main_line_moves") or []} | {n for n, th in threads.items()
                                                                        if th.get("on_line")}

    def ended_leaf(n):  # a fork stays in sight: the lead decides from it whether it grows on or becomes the line
        return (threads[n].get("status") in THREAD_ENDS and not t["children"].get(n) and n not in line
                and not threads[n].get("fork_of"))

    def walk(n, indent):
        kids = sorted(t["children"].get(n, []), key=lambda c: threads[c].get("seq") or 0)
        folded = [c for c in kids if ended_leaf(c)]
        if folded:
            out[-1] += f" · ended below: {', '.join(folded)}"
        for c in kids:
            if c in folded:
                continue
            th = threads[c]
            mark = "★" if c in line else "⑂" if th.get("fork_of") else "☆" if c in was else "-"
            fork = f" · fork of {th['fork_of']}: {th.get('choice')}" if th.get("fork_of") else ""
            by = f" · by {th['opened_by']}" if th.get("opened_by") and th["opened_by"] != "main" else ""
            out.append(f"{'  ' * indent}{mark} {c} {str(th.get('about') or '')[:70]} · {th.get('status')} · "
                       f"{len(th.get('findings') or [])} findings{fork}{by}")
            walk(c, indent + 1)

    walk(ROOT, 1)
    return out if len(out) <= at_most else out[:at_most] + [f"... {len(out) - at_most} more nodes (read_record "
                                                            "what threads)"]


def result_path(st, result):
    """A result's path in the tree: the nodes from the root to the deepest node its findings sit in, and the forks
    beside that path (each with its one change and its findings)."""
    t = tree(st)
    nodes = [node_of(st, f) for f in result.get("finding_ids") or []]
    deepest = max((n for n in nodes if n), key=lambda n: t["depth"].get(n, 0), default=ROOT)
    path, n = [], deepest
    while n and n != ROOT:
        path.append(n)
        n = t["parent"].get(n, ROOT)
    path = list(reversed(path))
    own = set(result.get("finding_ids") or [])
    threads = st.get("threads") or {}
    forks = [f for f in t["forks"] if threads[f]["fork_of"] in own or node_of(st, threads[f]["fork_of"]) in path]
    return {"path": path, "forks": forks}


def next_step_awaiting(st):
    """The result whose next steps wait for the user's pick: the newest offer (the version that gave its list,
    next_steps_seq) with no reply of kind next_step after it; with all its options, and the one to take by default, the
    recommended one (rank and question None where every option needs new data, so none is recommended). None when no
    offer waits. The test driver's simulated user follows the same rule on the raw events (docs/adr/0050, D5)."""
    offers = [(r.get("next_steps_seq") or 0, rid, r) for rid, r in (st.get("results") or {}).items()
              if r.get("next_steps") and r.get("decision") != "dropped"]
    if not offers:
        return None
    seq, rid, r = max(offers)
    if any(x.get("kind") == "next_step" and (x.get("seq") or 0) > seq for x in st.get("replies") or []):
        return None
    steps = r["next_steps"]
    pick = next((s for s in steps if s.get("recommended")), None) or {}
    return {"result_id": rid, "rank": pick.get("rank"), "question": pick.get("question"), "options": steps}


def main_line_the_lead_suggests(st):
    """The main line the research lead's latest review suggests, while the line is elsewhere and no one has decided
    since: the thread it names, off the line, with its reason. The main agent or the user moves the line, or keeps
    it by naming the thread it stays on, with the reason (docs/adr/0050, D3). Listed, never blocking."""
    reviews = st.get("reviews") or []
    line = (reviews[-1].get("main_line") or {}) if reviews else {}
    tid = line.get("thread_id")
    decided = any((m.get("seq") or 0) > reviews[-1]["seq"] for m in st.get("main_line_moves") or []) if reviews else 0
    if not tid or decided or tid not in (st.get("threads") or {}) or tid in tree(st)["line"]:
        return None
    return {"thread_id": tid, "why": line.get("why")}


def blocks_report(st):
    """Nothing in the record blocks a report (docs/adr/0022, section 13): the critic lists what is open, and the report
    states it. Only the design gate blocks, and it blocks effect analysis, not the report."""
    return []
