"""The analysis server (docs/adr/0020): the main agent's tools on a user's screen, on the full data, through the
shared record (pertura.record). The held-out exam (pertura_research.retired.agent_service: the exploration view, decoys, the lock)
stays available as its own server and is not part of this path.

The stages:
1. **A project.** open_project takes the data folder on the analysis server.
2. **The verified experiment.** The design server's tools: describe_deposit, read_metadata, measure (extract=True
   extracts the deposit's archives, after the user agrees), check_design, confirm_design (the user's form),
   get_design; then build_screen_object, which builds the screen object from the confirmed design as a background
   job.
3. **The first look and the threads.** Once the screen object is built, the first-look modules start in the
   background (docs/adr/0032, D11), and code opens threads from the design and from what the first look measures
   (docs/adr/0035, D1). The agent, the user and the skeptic open threads too (open_thread, the user's words relayed
   from the chat), and each changes status with its reason (thread_status).
4. **Work.** run_code runs a cell in the analysis notebook: a persistent Python kernel on the screen object and the
   deposit. In normal mode (docs/adr/0032, D2) its code reads any file and runs programs (R, command-line tools,
   processes), and writes only its own folder; in exam mode it is confined to the screen object and the deposit
   (pertura.confine). The resources (threads, workers, timeouts, the output cut, the figures shown) come from the
   workspace configuration (pertura.resources). The tool logs each cell in the record with its code and output and
   figures, and shows the agent the figures the cell drew. A worker passes its name and gets a notebook of its
   own.
5. **Findings and the user's replies.** record_finding: a sentence, its runs and its figure, and what is known
   beside it (prior, with its source) or how it differs from that; with a target, a feature and a direction its checks
   start as a job; reword_finding changes it in place, keeping its earlier wordings, replies and checks (check_again
   starts the checks again). The agent's own checks are its runs, cited in reword_finding or answer_doubt. note_reply
   keeps the user's reply to a finding in their words (docs/adr/0032, D6).
6. **Doubts.** raise_doubt (on a finding or the analysis), answer_doubt (its answer, on the doubt). open_thread's
   alternatives (D9), never used in the development runs, went with the retired tools (open_branch keeps them).
7. **What is open.** open_items: the critic's list, and the checks due (a figure review, the skeptic, the advisor,
   the research lead). Nothing in it
   blocks; the analysis ends when the user says enough. Only the design gate blocks, before effect analysis, and the
   design's confirmation is the one form.

    python -m pertura.mcp_server serve-analysis <config.json>   (config: {"workspace": <folder for projects>})
"""
import collections
import hashlib
import inspect
import json
import os
import re
import threading
import time

from . import candidates
from . import contracts
from . import kinds
from . import record as rc
from . import threads
from . import resources as rs
from .limits import CALL_WAIT_MAX
from . import design_shapes
from .design_service import (CHECK_WORDS, CONFIRM_WORDS, DesignError, DesignService, arguments_of,
                             make_design_config)

SERVER_INFO = {"name": "pertura-analysis", "version": "0.1.0"}
INSTRUCTIONS = (
    "Pertura's analysis server. Open the project and settle the design with the user (the design tools; the user "
    "confirms it on the one form), then build the screen object. Then analyse as the data and the user's question "
    "lead: the analyses by the field's kinds (guides, impact, genes, scores, states, programs, similarity, "
    "conditions, combinations, enrichment, reproducibility), each on any slice of the cells (cells, by, against, "
    "targets), and the notebook (run_code, where R and programs also run; its figures go to the user's page, and to "
    "you with show_figures), with the same blocks (pt.*), for what no tool does. Say your claims, results and "
    "doubts' answers in your replies, each claim right after the run that shows it: a recorder records them after "
    "each run, and the user's replies, and their checks run in code. Each reply says whether each finding's checks "
    "held and the doubts to answer; the session ends when none is open and your task list is done, or at its cap. "
    "pertura_status says what has run and what is open.")
KERNEL_THREADS = 2  # the server process's own thread variables; the notebooks' come from pertura.resources
MAX_WORKERS = rs.DEFAULTS["max_workers"]  # the default; the workspace configuration may set another


def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


_S, _A, _N, _B = {"type": "string"}, {"type": "array"}, {"type": "object"}, {"type": "boolean"}
# the instruments in view where code is written (docs/adr/0037, D5): the fair comparison's workers spent a median
# of 3 (Sonnet) and 11 (Haiku) cells learning them, and no session called pt.describe()
RUN_CODE_BLOCKS = (
    "pt is already imported (pertura.blocks): do not import it. pt.describe(): what the cells hold (labels, "
    "columns, control guides, conditions). The blocks, one line each (pt.help(name): a call's settings):\n"
    "pt.calibrate(result): the result's own call on control-guide groups, what it gives with no effect\n"
    "pt.genes(targets=..., method=...): genes changed per target; by=<cell column> runs it per value\n"
    "pt.scores(sets=...): gene-set scores per target against the reference; sets {name: genes} or an MSigDB "
    "collection's name with select= (names, or a word in them); contrast={column, levels: [a, b]} scores the control "
    "cells' own genes up and down between two levels; proteins= tests the antibody counts (CLR per cell); "
    "cell_values= tests a cell column or a saved table as it is; programs= tests programs' usage; by= too\n"
    "pt.programs(): gene programs found in the cells; pt.scores(programs=res) then tests each target's usage\n"
    "pt.composition(of=...): each target's share of each state or phase\n"
    "pt.cell_cycle(): each cell's cell-cycle scores and phase\n"
    "pt.enrich(of=...): gene sets among each target's changed genes; method=\"ulm\" with collection collectri or "
    "progeny: each factor's or pathway's activity from each target's changes (decoupler)\n"
    "pt.similarity(): targets' profiles correlated, against control-guide pairs\n"
    "pt.on_target(): each guide's effect on its own target gene\n"
    "result.by_guide(), result.match_depth(): the result's call guide by guide, or with depth thinned")
# run_module returns within RUN_WAIT_MAX seconds: a client runs one tool call after another, so a long wait there
# holds every call behind it. module_result waits at most limits.CALL_WAIT_MAX: past it a client moves the call to
# the background, and the figure in its reply would not reach the model. Every reply opens the runs that ended since
# the last call instead, so the agent works on and reads each when it has ended.
RUN_WAIT, RUN_WAIT_MAX = 10, 30
IMAGE_MAX_BYTES = 2_500_000  # a figure shown to the model as an image; a larger one is named, not sent
# each image a reply carries is named in its "images" (mcp_server); a run's own figure is named with this, and the
# plugin's hook lets these reach the main agent, which saw no module's figure while every figure was held back
RUN_FIGURE = "the figure of run "
REPORT_PAGE = 30000  # characters of the report per page of read_record(what="report")
TABLE_MAX = 30000  # characters of one read_table reply (development session 3: one reply was 100,019)
# a module result's diagnostic longer than this stays in result.json and is named in the reply; the calibration is
# always shown (docs/adr/0037, D3: one reply of round 1 carried 30,000 characters of four results)
DIAGNOSTIC_MAX = 300
DIGEST_LINE_MAX = 300  # a digest line in pertura_status's concise reply
# what every reading needs, started by code once the cells' rule is confirmed: the cells kept and left out, the
# evidence of the guide calls, each target's cells and each guide's own-gene change. The agent starts the rest in the
# order the skill gives, the screen overview among it
FIRST_LOOK = ("A.what_was_done", "B.guide_calls_and_their_evidence", "B.abundance", "B.efficacy")
# the screen overview (A.screen_overview), run by code once OVERVIEW_DUE_AFTER have run on a build, and by the agent:
# each input it is not given is the latest run of its module on this build, and the overview before it on this build
# keeps its distances
OVERVIEW = "A.screen_overview"
MENU_PICKS = 5  # the module picker picks at most this many modules for the user's form
DUE = ("skeptic due",)  # it launches its agent; read by the retired open_items and note_difference only: the main
# agent hears of it from the state list (_steering)
LAST_PROJECT = "last_project.json"  # in the workspace: the project a server of a later turn opens again
SAID = "said_in_replies.json"  # in the project: the runs the replies have named, each once
PAGE_STATE = "page_state.json"  # in the project: what the user's page shows that no reply carries
OVERVIEW_FIRST = {"abundance": "B.abundance", "efficacy": "B.efficacy", "states": "C.states_and_composition",
                  "similar": "E.similar_targets", "guides": "F.guides_agree", "replicates": "F.replicates_agree"}
OVERVIEW_LATER = {"genes": "C.genes", "common": "D.common_response"}
OVERVIEW_DUE_AFTER = ("C.genes", "E.similar_targets")  # once both have run on a build, code starts its overview
FINDING_KINDS = {"gene": "genes", "gene_set": "scores", "state": "states", "score": "scores",  # a finding's kind
                 "activity": "enrichment", "interaction": "combinations"}  # as the field's kind of analysis
AGENT_ARG = "pertura_agent"  # the plugin's hooks add it to a subagent's calls (plugin/hooks/page_and_session.ts)
CHECKED_KINDS = ("gene", "gene_set", "state", "score")  # a finding's kinds whose checks code runs (custom and
# interaction are the agent's own code)
BRIEFS_ONCE = ("threads_waiting",)  # items whose briefs a reply gives once each: on GSE267740 (d3a7dcc) the lead's
# figures' briefs took 25k characters of the main agent's replies, and no figure worker ran
# what is pending, each item with its label and its decider, named on each of the main agent's replies (docs/the_
# orchestration_record_proposals_decisions_pending_and_the_turn.md); the Stop hook sends the agent back while one of
# its own is pending, and the turn ends for the user's
MAIN, USER = "main agent", "user"
# the record is kept by the recorder (docs/adr/0051, D6): the items that asked the agent to record leave this list,
# their content stays in the replies; the loop's own items send it back (docs/adr/0052, D3; the research lead's and
# the figure reviewer's left with them, 2026-11-20), and the user's end the turn
PENDING = {"doubts_to_answer": ("doubts to answer", MAIN), "threads_waiting": ("threads waiting", MAIN),
           "next_steps_to_offer": ("next steps to offer", MAIN),
           "results_to_decide": ("results to decide", USER)}
# what a read's reply carries of the items due (the loop decided by the record, 2026-11-20: each reply says them);
# the review's line is among them, as _steering marks it said when it is due
READ_ITEMS = ("doubts_to_answer", "the_review")
DOUBTS_DO = ("answer each in one line, which the recorder reads: 'Doubt <id>: <holds | holds, narrowed | weakens | "
             "overturns | stated as a limit>. <reason, with its run>'. A doubt opened by pertura:checks says its "
             "claim's checks fail it: keep the claim, narrow it to where they hold, or withdraw it")
REVIEW_DO = "information from a fresh reading, said once: weigh it in your next step"
ONCE = ("the_maps_are_in",)  # given once in a session: on run A the overview was drawn three times in nine minutes
CHECKS_DOUBT = "pertura:checks"  # who opened a doubt on a claim its checks say fails
SAY_AGAIN_AFTER = 8  # calls before an item still undone is said once more (the lead came a median 8 calls late)


def _objects_named(item):
    """The doubts, threads, results or figures a due item names, by ID: a new one makes the item new again; a changed
    wording of the same ones does not."""
    if not isinstance(item, dict):
        return set()
    out = set()
    for field in ("doubts", "for_workers", "results", "threads", "figures", "claims"):
        for x in item.get(field) or []:
            out.add(str(x.get("doubt_id") or x.get("thread_id") or x.get("id") or x.get("finding_id")
                        or json.dumps(x, sort_keys=True, default=str)) if isinstance(x, dict) else str(x))
    return out


def _default(run):
    """Whether a module run took its module's default choices (its what names no choice, input or grouping)."""
    return " with " not in str(run.get("what") or "")


_OVERVIEW_LOCK = threading.Lock()
_SURVEY_LOCK = threading.Lock()  # two calls ending together start one of the survey's runs
SURVEY_TICK = 20  # seconds between the survey's steps when no call comes (docs/adr/0045, D2)
CHECKS_TICK = 20  # seconds between the checks queue's steps when no call comes: a queued claim's checks start while
# the agent works, waits or ends its turn (arm L on f2c247f: 23 of 35 queued claims never started)
SCOPES = ("one question", "the whole screen")  # what the user hands over (docs/adr/0045, D2)
MEANWHILE = ("it runs in the background: wait_for_runs returns when a run ends, and each reply opens the runs that "
             "have ended since your last call")
# what to do while the cells wait on the user (docs/build/open_points_across_pertura_2026-10-28.md, F2)
CELLS_NEXT = ("read the cells overview (its figure, its clusters with their markers and their shares without a guide, "
              "the round cuts per sample, the deposited object's ranges) and look at the cells in the notebook if "
              "needed; then propose the quality rule in the design with check_design (data.qc: thresholds read from "
              "the distributions as round numbers, and the clusters to leave out, such as other cell types or "
              "low-quality cells, as a filter on the overview's clusters.tsv, each with its reason in reasons; its "
              "shape: {filters: [{metric: n_genes, min: <number>}, {metric: pct_mito, max: <a share, 0 to 1>}, "
              "{metric: cluster, table: <the overview's clusters.tsv>, drop: [<clusters>]}], reasons: {<metric>: "
              "<why>}}, a metric being n_counts, n_genes, pct_mito or pct_ribo, or a column with its table), and "
              "confirm_design asks the user with the counts and the figure. The first look and the modules start once "
              "the user confirms it; the notebook stays open")
# wait_for_runs waits this long by default, and at most WAIT_MINUTES_MAX: Claude Code moves a call past two minutes to
# the background and wakes the agent when it returns (development session 4, problem 1h), and gives up on a call
# that runs 30 minutes, so a wait ends before that and the agent can call again
WAIT_MINUTES, WAIT_MINUTES_MAX = 20, 25
# MAST through Seurat's FindMarkers, one comparison: the median of six on the macrophage development screen
# (analysis/end_to_end/adr0024_gene_test_timing.py), for the time estimate of a run with method field
MAST_SECONDS = 870
# Seurat's LR through FindMarkers, one comparison: CRISPRai's activation arm, 19 targets in about 12 minutes on 6
# processes (analysis/end_to_end/adr0031_capture_aware_tests_crisprai.py), for the estimate of a run that leads with LR
LR_SECONDS = 180
# cNMF with C.programs' defaults, from a job stopped on GSE203592 (6a87acd): 508 of the 600 factorisations ended in 52
# minutes, each K's longer than the last; the rest and the consensus put the whole at about 1.5 hours
CNMF_TIME = ("about 1.5 hours with the default K range (5-15) and 100 factorisations per K: on a 70,646-cell screen "
             "with 12 processes, 508 of the 600 took 52 minutes, the larger K the longer; fewer K (components) or "
             "factorisations (n_iter) take less. It waits only behind other programs found in the cells")
CALIBRATION_WORDS = ("the calibration on the control-guide groups is computed once for the build and reused by "
                     "every later call and every finding's checks")
TOOLS = [
    {"name": "pertura_status", "description": "The analysis's state: the project, the design, the screen object, the "
                                              "running jobs, the threads, the findings and the open items, to show "
                                              "the user whenever they ask. detail: true gives every thread and the "
                                              "digest's lines in full.",
     "inputSchema": _obj({"detail": _B})},
    {"name": "open_project", "description": "Open the user's data folder on the analysis server.",
     "inputSchema": _obj({"data_folder": _S}, ["data_folder"])},
    {"name": "describe_deposit", "description": "The deposit's files, how they pair, and their feature types.",
     "inputSchema": _obj({})},
    {"name": "read_metadata", "description": "Text to read for meaning: GEO text, guide names, a table, by pages.",
     "inputSchema": _obj({"item": _S, "path": _S, "offset": {"type": "integer"}}, ["item"])},
    {"name": "measure", "description": "Measure the deposit's design facts and the problems that mislead an "
                                       "analysis. extract=true extracts its archives first (ask the user before). A "
                                       "large deposit is measured in the background: the answer says 'running'; "
                                       "call again to get the measurement.",
     "inputSchema": _obj({"refresh": {"type": "boolean"}, "extract": {"type": "boolean"}})},
    {"name": "check_design", "description": CHECK_WORDS + " The design auditor (agents/design-auditor.md) writes "
                                            "it. Animals or donors pooled in each sample that the deposit "
                                            "cannot label: units.independent_unit none, with "
                                            "units.pooled_from (for example '2 donors').",
     "inputSchema": design_shapes.input_schema()},
    {"name": "confirm_design", "description": CONFIRM_WORDS, "inputSchema": _obj({})},
    {"name": "get_design", "description": "What is confirmed, what is open, and the last check.",
     "inputSchema": _obj({})},
    {"name": "build_screen_object", "description": "Build the screen object from the confirmed design, in the "
                                                   "background; call again to see the job and its summary. wait: "
                                                   "seconds to wait for the build to end, at most 100. Once "
                                                   "built, its reply gives the cells overview: the cells' quality "
                                                   "rule is proposed from it (check_design) and confirmed by the "
                                                   "user (confirm_design), and the first-look modules start then. "
                                                   "A failed build "
                                                   "gives its log and runs again when the confirmed design "
                                                   "changes, or with retry.",
     "inputSchema": _obj({"wait": {"type": "integer"}, "retry": {"type": "boolean"}})},
    {"name": "plan_questions", "description": "The questions A-H this design calls for, as reminders: each with "
                                              "why, its status and whether a run has served it (seen); nothing "
                                              "has to close them. question: the user's words, as they gave them in "
                                              "chat. paradigms: the cards the design allows; paradigm (a card's "
                                              "id) with why records the card whose route you follow (your choice; "
                                              "say it in chat), inserted: [{paradigm_id, at}]. fork (a step's id) "
                                              "with choice and why records a fork of that route: a key fork is the "
                                              "user's, so ask them in chat and pass their words (user_words); any "
                                              "other is yours, the card's default unless you say why. routine: "
                                              "start the first-look modules (A, B, F and C.any_effect) now; they "
                                              "also start when the screen object is built. read_record what "
                                              "overview reads them together.",
     "inputSchema": _obj({"question": _S, "paradigm": _S, "why": _S, "inserted": _A, "fork": _S, "choice": _S,
                          "options": _A, "routine": {"type": "boolean"}, "user_words": _S})},
    {"name": "add_question", "description": "Add a question to the plan (letter A-H, the item, and why).",
     "inputSchema": _obj({"letter": _S, "item": _S, "because": _S}, ["letter", "item", "because"])},
    {"name": "run_code", "description": "Run Python in your notebook on the analysis server, a persistent kernel "
                                        "(pt.screen().adata = the screen object's counts and cells; screen = its "
                                        "folder, deposit = the data folder, cell_tables = the project's cell "
                                        "tables, which every pertura.cell_table call takes). R, command-line tools and processes run "
                                        "from it; it writes in its own folder. The figures the cell shows go to the "
                                        "user's page; show_figures: true shows them to you as images (a subagent "
                                        "sees its own). Logged in the record with its code, output and figures. "
                                        "save_figure(fig, name, data, caption, role) keeps a figure in the record "
                                        "(PNG, PDF, the plotted data), noting its layout issues; strict=True holds it "
                                        "to the publication standard, for a panel or the report. "
                                        "pt.save_table(name, table) names a table you computed (table:<name>) for "
                                        "any tool; pt.load(id) gives any object. The answer lists the objects the "
                                        "cell used and made: nothing to declare. worker: a worker's own notebook. "
                                        "why: one line, why this step now; thread_id: the thread it serves.\n"
                                        + RUN_CODE_BLOCKS,
     "inputSchema": _obj({"code": _S, "what": _S, "worker": _S, "timeout": {"type": "integer"}, "why": _S,
                          "thread_id": _S, "step": _S, "show_figures": _B}, ["code", "what"])},
    *[kinds.tool(t) for t in kinds.KINDS],
    {"name": "run_result", "description": "A run's state, and its result, tables and figure (to you as an image, and "
                                          "on the user's page) once done, with the notebook's lines that give the same "
                                          "result where the analysis is a recipe of blocks (in_the_notebook). run_ids: "
                                          "several runs, of which the first to finish is given. wait: seconds to wait, "
                                          "at most 100 (a longer call would be moved to the background, and its figure "
                                          "would not reach you). Each tool's reply opens the runs that ended since your "
                                          "last call, with their results and figures, so work on meanwhile. A result "
                                          "shows its summary, its biology (the genes, states, conditions or pairs it "
                                          "found, in lines), calibration and short diagnostics; the long ones (each "
                                          "comparison made, the package calls) are named, and detail: true shows them.",
     "inputSchema": _obj({"run_id": _S, "run_ids": _A, "wait": {"type": "integer"}, "detail": {"type": "boolean"},
                          "show_figures": _B})},
    {"name": "list_modules", "description": "The analysis modules, one line each: the question it serves, when it "
                                            "applies, whether it is a recipe of blocks. With name: that module's "
                                            "contract (what it needs and returns, how it misleads and the check "
                                            "for it, what it was tested on, its choices and inputs) and its "
                                            "recipe.",
     "inputSchema": _obj({"question_id": _S, "name": _S})},
    {"name": "run_module", "description": "Run a module on the screen object as a background job, logged in the "
                                          "record as a run; waits up to `wait` seconds (default 10, at most 30) "
                                          "and gives its result when done, else its estimate: start the other "
                                          "runs you need, then arm wait_for_runs. cells: a kept cell set's name "
                                          "(save_cells in the "
                                          "notebook): the module sees only its cells, with its labels. by: a cell "
                                          "column, the module run once per value (each condition, tissue or cell "
                                          "type apart, 2 to 8 values), gathered into one result. An input "
                                          "that is a table takes an object's ID (table:<name>, <run>:<table>, a "
                                          "pt call's ID) or a path; a table without a column the module reads is "
                                          "named before the run starts. why: one line, why this run now; "
                                          "thread_id: the thread it serves.",
     "inputSchema": _obj({"module": _S, "choices": _N, "inputs": _N, "cells": _S, "by": _S,
                          "wait": {"type": "integer"}, "why": _S, "thread_id": _S, "step": _S}, ["module"])},
    {"name": "module_result", "description": "A module run's state, and its result, tables and figure (to you as "
                                             "an image, and on the user's page) once done. run_ids: several runs, of which the first to "
                                             "finish is given. wait: seconds to wait, at most 100 (a longer call "
                                             "would be moved to the background, and its figure would not reach "
                                             "you). Each tool's reply opens the runs that ended since your last "
                                             "call, with their results and figures, so work on meanwhile. A result "
                                             "shows its summary, its biology (the genes, states, conditions or pairs "
                                             "it found, in lines), calibration and short diagnostics; the long ones "
                                             "(each comparison made, the package calls) are named, and detail: true "
                                             "shows them.",
     "inputSchema": _obj({"run_id": _S, "run_ids": _A, "wait": {"type": "integer"}, "detail": {"type": "boolean"},
                          "show_figures": _B})},
    {"name": "wait_for_runs", "description": "Wait for runs to end: the first of run_ids (default: every run still "
                                             "running, and those started while it waits), up to minutes (20; at most "
                                             "25, as a client gives up on a call at 30). Arm one right after "
                                             "starting runs, then work on: Claude Code moves it to the background "
                                             "and gives you its reply when a run ends. One at a time: while one is "
                                             "armed, another adds nothing. With nothing else left, end your turn: "
                                             "its reply wakes you. It does not watch workers, whose replies come "
                                             "by themselves. It says which runs ended, with their summaries, "
                                             "and which still run, with their progress; run_result then gives "
                                             "each with its figure.",
     "inputSchema": _obj({"run_ids": _A, "minutes": {"type": "integer"}, "show_figures": _B})},
    {"name": "null_of_right_unit", "description": "A recorded finding's calibration on the control guides: the "
                                                  "finding's own test on groups of control guides, each against the "
                                                  "rest of the control cells, and where the finding's effect lies "
                                                  "among theirs (for a gene, a gene set or a state). It runs as the "
                                                  "finding's calibration check and is recorded with its checks.",
     "inputSchema": _obj({"finding_id": _S, "wait": {"type": "integer"}}, ["finding_id"])},
    {"name": "module_menu", "description": "The module menu's brief, for the module picker (agents/module-picker.md): "
                                           "the modules that can run on this screen now, each with what it returns "
                                           "and its measured run time; those that wait for another module; those "
                                           "this screen cannot run, with the reason; the screen's facts, the first "
                                           "look's sections, the findings and the open doubts.",
     "inputSchema": _obj({})},
    {"name": "next_analyses", "description": "The brief of the next analyses, for the module picker (agents/"
                                             "module-picker.md): the analyses that can run on this screen now, each "
                                             "as its tool and view, with what it returns and its measured run time; "
                                             "those that wait for another; those this screen cannot run, with the "
                                             "reason; the screen's facts, the first look's sections, the findings and "
                                             "the open doubts.",
     "inputSchema": _obj({})},
    {"name": "record_module_picks", "description": "The module picker records its picks: at most five analyses that "
                                                   "can run now, each a tool call {tool, view, and cells, by, "
                                                   "against, targets, choices or inputs where it needs them} with "
                                                   "why (the open doubt, unread section or question it answers).",
     "inputSchema": _obj({"picks": {"type": "array", "items": {"type": "object"}}}, ["picks"])},
    {"name": "ask_module_menu", "description": "Put the module picker's latest picks to the user on a form, each "
                                               "ticked to run; each one ticked starts in the background, and the "
                                               "user's choice is recorded. Unattended, every pick starts, with no "
                                               "form.",
     "inputSchema": _obj({})},
    {"name": "offer_analyses", "description": "Put the module picker's latest picks to the user on a form, each "
                                              "ticked to run; each one ticked starts in the background, and the "
                                              "user's choice is recorded. Unattended, every pick starts, with no "
                                              "form.",
     "inputSchema": _obj({})},
    {"name": "confirm_calling_rule", "description": "Put a kept cell set that decides guide calls (save_cells with "
                                                    "calls=True, or one that relabels cells) to the user on the "
                                                    "design form as the screen's default calling rule: description "
                                                    "says what the rule does, in plain words. Analyses can use any "
                                                    "kept set at once; the user's confirmation only makes one the "
                                                    "default.",
     "inputSchema": _obj({"cell_set": _S, "description": _S}, ["cell_set", "description"])},
    {"name": "prior_screens", "description": "Outside knowledge: published Perturb-seq screens and protein complexes "
                                             "for targets. In Replogle et al. 2022's CRISPRi screens (K562 "
                                             "essential, K562 genome-wide, RPE1 essential): each target's own-gene "
                                             "knockdown and cells there, and the perturbations whose profiles are "
                                             "nearest (Pearson); and the CORUM complexes that hold it. A mouse "
                                             "target is read through its human orthologs. Recorded as prior "
                                             "knowledge: it informs a claim and never changes one.",
     "inputSchema": contracts.schema(contracts.PriorScreens)},
    {"name": "gene_sets", "description": "Outside knowledge: the genes of a named set, or the sets that hold a gene, "
                                         "in an MSigDB collection for the design's organism: by name (hallmark, "
                                         "go_bp, go_cc for complexes, go_mf, reactome, wikipathways, kegg, "
                                         "canonical_pathways, tf_targets, immunologic, immunesigdb, cell_types, "
                                         "oncogenic) or by MSigDB's code (c2.cgp, m5.mpt; a wrong one lists those "
                                         "held); for mouse, a human-only one is translated through HCOP. "
                                         "Recorded as prior knowledge. match: a word, the sets whose names hold it "
                                         "with their sizes, to choose the sets `scores` scores (select).",
     "inputSchema": _obj({"collection": _S, "name": _S, "gene": _S, "match": _S})},
    {"name": "search_literature", "description": "Outside knowledge: papers on PubMed and bioRxiv (Europe PMC) for a "
                                                 "query of two to four words (two genes, a gene and a pathway, a kind "
                                                 "of screen), each with its title, first author, journal, year, PMID or "
                                                 "DOI, the start of its abstract and where it matched: every word in "
                                                 "its title or abstract, or in its full text only, where the relation "
                                                 "may not be stated. One query a call; a PMID or a DOI alone looks that "
                                                 "paper up. Prior knowledge: put what you use beside a finding or a "
                                                 "thread (prior, with its source); it informs and never changes a "
                                                 "finding.",
     "inputSchema": _obj({"query": _S, "max_results": {"type": "integer"}}, ["query"])},
    {"name": "record_knowledge", "description": "Record something from outside the data that you used (a paper "
                                                "from PubMed or bioRxiv, a database entry): its source, citation and "
                                                "what it says. Tagged prior knowledge; it informs, it never changes "
                                                "a finding.",
     "inputSchema": _obj({"question_id": _S, "source": _S, "citation": _S, "text": _S},
                         ["question_id", "source", "citation", "text"])},
    {"name": "read_knowledge", "description": "Knowledge cards and paper examples. With no arguments: questions by "
                                              "analysis step. Cards: ids names entries or questions; module gives its "
                                              "entry, choices and inputs; words searches entries. Papers: give query "
                                              "(analysis: methods; proceed: follow-ups; figure: presentation) and kind "
                                              "(e.g. genes, similarity, similar targets). Returns counts and up to three "
                                              "sourced examples, prioritising the current modality and readout. Module "
                                              "lookups include analysis examples; first analysis replies and result "
                                              "proposals also bring examples. Workspace source exclusions apply to "
                                              "all paper queries. Published knowledge is prior, separate "
                                              "from this screen's evidence.",
     "inputSchema": _obj({"ids": _A, "module": _S, "words": _S,
                          "query": {"type": "string", "enum": ["analysis", "proceed", "figure"]}, "kind": _S})},
    {"name": "propose_validation", "description": "Question H: for the recorded findings (all, or finding_ids), the "
                                                  "experiment that would settle each, from its checks, as an "
                                                  "arrayed experiment; a module run.",
     "inputSchema": _obj({"finding_ids": _A, "wait": {"type": "integer"}})},
    {"name": "read_table", "description": "One table a module run wrote (result, or a name its result lists, "
                                          "such as by_unit or changed_genes), or a table the notebook saved "
                                          "(name table:<name>, as pt.load takes it): its columns "
                                          "and up to `rows` rows (default 50), optionally filtered (where: "
                                          "{column: value}, a text such as \"q < 0.05 and target == Hdac1\", or "
                                          "pandas' query, with parentheses, or and not, text values in quotes) "
                                          "and cut to the columns named (columns). A reply stays under "
                                          "30,000 characters, and says when it was cut.",
     "inputSchema": _obj({"run_id": _S, "name": _S, "rows": {"type": "integer"},
                          "where": {"type": ["object", "string"]}, "columns": _A}, ["run_id", "name"])},
    {"name": "read_record", "description": "The record itself: the findings with their checks (each in its own "
                                           "terms), the threads, the runs with what they did and their output, the "
                                           "doubts, the figures, the kept cell sets. what: findings, threads, runs, "
                                           "doubts, knowledge (outside knowledge, tagged prior), figures, cell_sets, "
                                           "block_calls (each pt call, by its call_id), objects (every table, set, "
                                           "call and figure, with the run that made it), chain (with ids: the steps "
                                           "that made these objects, findings or results, from the deposit and the "
                                           "design), map (the screen's objects and the relations the findings "
                                           "show, with what is not yet explained), report (the written report's "
                                           "text, a page at a time: "
                                           "ids [page]), overview (the routine questions' runs in one table, "
                                           "each with its status, summary and flags), coverage (what the record "
                                           "leaves open: the routes the design allows, the data no run has read, "
                                           "the findings without a direction or a figure, the doubts no run has "
                                           "answered, the candidate structures of the first look), notebook (your "
                                           "notebook's cells in order: each one's what, the end of its output and "
                                           "its figures; read it to pick your line up again after a compaction) or "
                                           "all. Without ids, each entry comes in one line (what it is and where it "
                                           "stands); ids give the full entries. A reply longer than 30,000 "
                                           "characters comes in pages, the latest entries first: page gives the "
                                           "next. figures with ids: each figure is also shown as an image.",
     "inputSchema": _obj({"what": _S, "ids": _A, "page": {"type": "integer"}})},
    {"name": "read_run", "description": "Your reading of a run's output, kept with the run, where you want one; "
                                        "nothing asks for it.",
     "inputSchema": _obj({"run_id": _S, "reading": _S}, ["run_id", "reading"])},
    {"name": "record_finding", "description": "A finding: what was found, in a sentence (statement), the runs it "
                                              "rests on and its figure (a figure's ID or a PNG path). A technical "
                                              "finding counts too. prior: what is known beside it, from outside the "
                                              "data ({text, source}: a PMID, a DOI, a database or 'the user'), tagged "
                                              "prior knowledge, never evidence; it counts as looked up when a "
                                              "search_literature of this session returned the PMID or DOI, or the "
                                              "source is the search ('search: <query>') where it found nothing; "
                                              "differs: how it differs from the "
                                              "paper or from prior knowledge ({text, source: 'the paper' or 'prior "
                                              "knowledge'}). thread_id: the thread it "
                                              "joins. relates: the relations it shows, [{from, to, sign: + or -, "
                                              "measured, from_level, to_level}] (levels: perturbation, gene, group, "
                                              "programme, state), which code keeps as the screen's map; prior's "
                                              "relates, those the knowledge gives. Its checks run when it "
                                              "also names a target (or pair; or targets, up to six that move the "
                                              "feature the same way, each checked), a feature, a direction (up or "
                                              "down: the feature higher or lower in the target's cells than in the "
                                              "reference) and a kind: gene "
                                              "(feature a gene), gene_set (feature the set's name, with genes), "
                                              "state (feature a state or a cell-cycle phase as its labelling "
                                              "names it, with state_run: a `states` run (view states or "
                                              "cell_cycle), or block_call: a pt.states, pt.cell_cycle or "
                                              "pt.composition call), score (a per-cell score your notebook "
                                              "computed: save it with pt.save_table(name, table), one row per "
                                              "cell with its barcode and score, and give feature \"table:<name>\"), "
                                              "interaction or custom (the agent's own "
                                              "code, which no check runs on). A finding without such a claim says "
                                              "so: kind \"description\" (a pattern, a count, a technical fact). "
                                              "block_call: the pt call it came "
                                              "from (its cells, "
                                              "reference and test); cells: the cells its checks run on, as the "
                                              "analysis tools take them (a kept set's name, {column: value}, or "
                                              "{state, run} for cells within a state); population is a label only. "
                                              "Recording it starts "
                                              "its checks in the background (the lead test and the checks the "
                                              "design chooses: each guide alone, the other test, the calibration "
                                              "check, depth matched, a half, the deposit's calls), each in its own "
                                              "terms; run_result with the returned run_id gives them.",
     "inputSchema": contracts.schema(contracts.RecordFinding)},
    {"name": "write_plan", "description": "Your plan for the analysis, written once the screen's map is in: a few "
                                          "items, each what you will do (does), why the data call for it (why) and "
                                          "how (a module, the notebook, a worker comparing two ways at a choice, "
                                          "the skeptic before the report), from your main line's levels. Give "
                                          "step with an item's id when you run it; the "
                                          "run's end marks it done, and an item done without a run takes status "
                                          "done with done_by. Write it again when the data call for a change, "
                                          "with why: an item keeps its id, one you leave out is left with your "
                                          "why, a new one (no id) takes the next. Each step's end gives the next "
                                          "open item; nothing is refused for being off the plan.",
     "inputSchema": _obj({"items": {"type": "array", "items": {"type": "object"}}, "why": _S}, ["items"])},
    {"name": "open_thread", "description": "Open a thread: something a result, the design, the user or the skeptic "
                                           "raised that may become a result, in a sentence with its numbers "
                                           "(about), what raised it (raised_by: a run ID, a finding ID, a design key "
                                           "or the user's words), why the user or the field cares (matters) and "
                                           "what result would tell its explanations apart (tells_apart). "
                                           "user_words: the user's words when the user raised it, as they gave "
                                           "them in chat (the first becomes the analysis's question). doubt_id: a "
                                           "thread that tests whether a finding holds (a doubt raised first), for "
                                           "a test too long for your own cell (run_code, or the tool it names); a "
                                           "worker runs it, and answer_doubt records what it showed. One "
                                           "level up (a programme, a set of "
                                           "regulators, a state, a relation between them): joins, the findings it "
                                           "joins, and test, the analysis that tests it; on_line: true for a step "
                                           "of the main line, which the main agent runs itself, false for a probe "
                                           "or a side question, which a worker takes. prior: what is known "
                                           "beside it from outside the data ({text, source}), kept as prior "
                                           "knowledge. observation: true for something you see in the first look's "
                                           "maps (raised_by: the run that shows it); you follow one as your main "
                                           "line, workers probe the others, and each ends with its evidence or is "
                                           "set aside with its reason. reads: a kind of conclusion the design can "
                                           "reach (the first look's kinds_of_conclusion), read across the screen "
                                           "by a worker; the reply gives its brief. step: what the thread is to "
                                           "the line: a new question the last result raised (its meaning, how it "
                                           "comes about, what it joins, a prediction), a check of a finding, a new "
                                           "area, or report work; a line's depth counts its new questions. "
                                           "scope, with the user's words: what they hand over, one question or the "
                                           "whole screen; with the whole screen, Pertura runs the survey of the "
                                           "comparisons the design calls for beside your line. structure: the ID "
                                           "of a set of targets alike the survey lists, which the thread reads. "
                                           "next_of: the result whose next question the thread is. A fork (the "
                                           "analysis is a tree), to see where another way leads: fork_of, the "
                                           "finding, thread or run whose step it redoes, choice, the one choice it "
                                           "changes, and matters, why that choice could change the reading; it "
                                           "grows beside that step, and may become the main line. A worker takes "
                                           "a fork or a doubt's long test; the main agent's replies give its "
                                           "brief.",
     "inputSchema": _obj({"about": _S, "raised_by": _S, "thread_id": _S, "matters": _S, "tells_apart": _S,
                          "evidence": _N, "user_words": _S, "doubt_id": _S, "joins": _A, "test": _S, "prior": _N,
                          "observation": {"type": "boolean"},
                          "on_line": {"type": "boolean"}, "step": {"type": "string", "enum": list(rc.STEPS)},
                          "reads": {"type": "string", "enum": list(candidates.KINDS)},
                          "scope": {"type": "string", "enum": list(SCOPES)}, "structure": _S, "next_of": _S,
                          "fork_of": _S, "choice": _S, "proposed_as": _S},
                         ["about", "raised_by"])},
    {"name": "thread_status", "description": "Change a thread's status, with the reason: followed (you take it: say "
                                             "why this one), explained, contradicted, out of data, set aside (why "
                                             "you leave it), or open again. A finding recorded with its thread_id "
                                             "joins the thread. Explained names the findings that hold its result "
                                             "(finding_ids; by default those that joined it), so the report can "
                                             "show it; contradicted and out of data name the runs that show it "
                                             "(run_ids). A worker records its thread's findings and ends the "
                                             "thread itself. run_ids: the runs its status rests on.",
     "inputSchema": contracts.schema(contracts.ThreadStatus)},
    {"name": "reword_finding", "description": "Change a recorded finding in place, with the reason (such as \"the "
                                              "control-relative number, from the checks\"): its sentence "
                                              "(statement), its numbers (effect), its figure, the runs it rests on, "
                                              "or its target, feature or direction; with kind (and what its kind "
                                              "needs, as in record_finding; targets for a set, up to six), a "
                                              "description becomes a claim in place, "
                                              "under its ID, and its checks start. The record keeps each earlier "
                                              "wording, and the user's replies and the checks stay with the finding. "
                                              "A new target, feature or direction is another claim: its checks start "
                                              "again, and the earlier ones stay with the earlier wording. New runs "
                                              "are new evidence; cite "
                                              "your own checks' runs here. check_again: start its checks again, "
                                              "after their run failed or was stopped. prior and differs as in "
                                              "record_finding.",
     "inputSchema": _obj({"finding_id": _S, "reason": _S, "statement": _S, "effect": {}, "figure": _S,
                          "run_ids": _A, "target": _S, "feature": _S,
                          "direction": {"type": "string", "enum": ["up", "down"]},
                          "kind": {"type": "string", "enum": list(contracts.CLAIM_KINDS)}, "genes": _A,
                          "targets": {"type": "array", "items": _S, "minItems": 1, "maxItems": 6},
                          "state_run": _S, "block_call": _S, "cells": {"anyOf": [_S, {"type": "object"}]},
                          "reference": {"type": "string", "enum": ["control_guides", "other_targets", "no_guide"]},
                          "check_again": {"type": "boolean"}, "prior": _N, "differs": _N, "relates": _A},
                         ["finding_id", "reason"])},
    {"name": "note_reply", "description": "The user's reply to a finding, in their own words as they gave them in "
                                          "chat: the user's words only, never yours (your note on a finding goes in "
                                          "reword_finding's reason). Its kind: keep (it stays), "
                                          "doubt (check something), deepen, reread (another reading of what it "
                                          "means), drop, knowledge (what they know, kept as prior knowledge), or "
                                          "enough (the analysis can end; no finding needed), or main_line with "
                                          "thread_id (the user moves the main line to that thread; with by 'main "
                                          "agent' and your reason as words, you move it yourself), or next_step "
                                          "(the user's pick among "
                                          "a result's next steps: option, its rank; without option, their own next "
                                          "step in their words), which opens its thread. When the user runs "
                                          "Pertura unattended, enough with your reason (words) is your decision to "
                                          "end, listed in the report as decided for them; it is taken only once the "
                                          "record holds nothing open (checks running, doubts to answer, parts of "
                                          "the question, choices to try).",
     "inputSchema": _obj({"kind": {"type": "string", "enum": list(rc.REPLY_KINDS)}, "words": _S, "finding_id": _S,
                          "thread_id": _S, "by": {"type": "string", "enum": ["user", "main agent"]},
                          "option": {"type": "integer", "minimum": 1}},
                         ["kind", "words"])},
    {"name": "check_finding", "description": "Start a recorded finding's checks again: after their run failed or "
                                             "was stopped (the critic lists it as a finding whose checks have not "
                                             "run).",
     "inputSchema": _obj({"finding_id": _S}, ["finding_id"])},
    {"name": "record_check", "description": "A check of your own on a finding (check 'own'), beside those the tool "
                                            "runs when it is recorded: its outcome in its own terms, the run it is "
                                            "in, and the reason.",
     "inputSchema": _obj({"finding_id": _S, "check": _S, "outcome": _S, "run_id": _S, "reason": _S},
                         ["finding_id", "check", "outcome"])},
    {"name": "raise_doubt", "description": "A doubt, and what result would change the judgement: on a finding "
                                           "(finding_id) or on the analysis as a whole. answer_doubt records what "
                                           "its test showed.",
     "inputSchema": _obj({"doubt_id": _S, "text": _S, "would_change": _S, "finding_id": _S},
                         ["doubt_id", "text", "would_change"])},
    {"name": "answer_doubt", "description": "A doubt's answer, recorded on the doubt. Each outcome, and what it "
                                            "needs: holds, 'holds, narrowed' (say the narrower claim), weakens or "
                                            "overturns: the run that tested it (run_ids), made for it; the report "
                                            "says when no run made after the doubt tested it. 'reworded, not "
                                            "tested': "
                                            "you reworded the finding to claim less without a test the data could "
                                            "still run; no run; the report lists it as open. 'stated as a limit': "
                                            "no run, and its kind (limit): the design (the data cannot answer it) "
                                            "or a tool; the report states it apart. 'still open': "
                                            "unanswered, or its test not run yet. "
                                            "Test a doubt where its answer would change the result; a rewording "
                                            "alone tests none. A later answer replaces it. The critic no longer lists "
                                            "an answered doubt; one still open stays listed, with its reason.",
     "inputSchema": _obj({"doubt_id": _S, "outcome": {"type": "string", "enum": list(rc.DOUBT_OUTCOMES)},
                          "reason": _S, "run_ids": _A,
                          "limit": {"type": "string", "enum": list(rc.LIMIT_KINDS),
                                    "description": "with 'stated as a limit': what leaves it, the design (the data "
                                                   "cannot answer it) or a tool (none here or in the "
                                                   "notebook does it)"}},
                         ["doubt_id", "outcome", "reason"])},
    {"name": "open_branch", "description": "A branch, with its plan: it tests a doubt (doubt_id), re-opens a fork "
                                           "already decided (origin fork, fork_id), follows the user's request "
                                           "to try another approach (origin user, request in their words), or "
                                           "tries the other ways at a choice you went ahead on: alternatives "
                                           "[{name, change}], the base they change (the step's code, or a "
                                           "module's recipe_as_run) and the measures every way reports. Its reply "
                                           "gives each worker's brief, and where to save your own row.",
     "inputSchema": _obj({"branch_id": _S, "plan": _S, "doubt_id": _S, "origin": _S, "fork_id": _S,
                          "request": _S, "alternatives": _A, "base": _S, "measures": _A}, ["branch_id", "plan"])},
    {"name": "close_branch", "description": "A branch's outcome and the runs it rests on. For a branch of "
                                            "alternatives it returns one table, a row for your way and one per "
                                            "alternative (the rows saved as <branch>__<name>), with their figures. "
                                            "Its workers' notebooks are shut, which frees their slots.",
     "inputSchema": _obj({"branch_id": _S, "outcome": _S, "run_ids": _A, "worker": _S},
                         ["branch_id", "outcome", "run_ids"])},
    {"name": "stop_run", "description": "Stop a module, checks or block job that is no longer needed; it is "
                                        "recorded as stopped.",
     "inputSchema": _obj({"run_id": _S}, ["run_id"])},
    {"name": "close_question", "description": "Give a question a status, where you want one; nothing asks for it: "
                                              "detected, not_detected or not_enough_data with its evidence (run "
                                              "IDs) and figure; cannot_answer with a reason; not_pursued with a "
                                              "reason: you leave it, or by user the user leaves it (their words, "
                                              "user_words).",
     "inputSchema": _obj({"question_id": _S, "status": _S, "evidence": _A, "figure": _S, "reason": _S,
                          "by": {"type": "string", "enum": ["agent", "user"]}, "user_words": _S},
                         ["question_id", "status"])},
    {"name": "record_sufficiency", "description": "The sufficiency check's answer for a closed question "
                                                  "(agents/sufficiency-check.md): sufficient or not, and its reasons.",
     "inputSchema": _obj({"question_id": _S, "sufficient": {"type": "boolean"}, "reasons": _A},
                         ["question_id", "sufficient", "reasons"])},
    {"name": "open_items", "description": "What is still open (the critic's list) and the checks due: 'skeptic due' "
                                          "(launch the skeptic). Nothing listed blocks the report; it states what is "
                                          "open.",
     "inputSchema": _obj({})},
    {"name": "note_difference", "description": "A finding differs from the original paper or from prior knowledge "
                                               "in the record (source: 'the paper' or 'prior knowledge'), and how. "
                                               "It makes the skeptic due for the finding.",
     "inputSchema": _obj({"finding_id": _S, "source": _S, "text": _S}, ["finding_id", "source", "text"])},
    {"name": "record_skeptic_check", "description": "The skeptic records its check (agents/skeptic.md) of the "
                                                    "findings it was given, with the IDs of the doubts it raised "
                                                    "(none when it found no problem), and what it checked on each "
                                                    "(checked: [{finding_id, check, outcome}], the outcome sound or "
                                                    "the doubt's ID), which the user sees. The findings a result "
                                                    "rests on are read once.",
     "inputSchema": _obj({"finding_ids": _A, "doubt_ids": _A, "checked": _A}, ["finding_ids", "doubt_ids"])},
    {"name": "propose_dossier", "description": "Propose a result to deepen: one that summarises many targets or "
                                               "genes, its title, the findings its sentence names (lead_findings), "
                                               "and main_run, the run whose figure is its map. Returns the map's "
                                               "size and each lead finding's checks.",
     "inputSchema": _obj({"dossier_id": _S, "title": _S, "lead_findings": _A, "main_run": _S, "why": _S},
                         ["dossier_id", "title", "lead_findings", "main_run", "why"])},
    {"name": "choose_dossiers", "description": "The user's choice of the proposed results that go on to "
                                               "validation, asked in chat: chosen, the dossiers' IDs (1-5), and the "
                                               "user's words (user_words). The user chooses which proposed results "
                                               "to deepen "
                                               "(2-5).",
     "inputSchema": _obj({"chosen": _A, "user_words": _S}, ["chosen", "user_words"])},
    {"name": "record_dossier_part", "description": "One part of a chosen result: effect, specificity, checks, "
                                                   "convergence (kinds: two or more kinds of evidence), mechanism "
                                                   "(knowledge_ids), relations (finding_ids), validation (the run of "
                                                   "propose_validation). Every sentence of text names its entry: "
                                                   "[run:ID], [finding:ID], [knowledge:N], [question:ID] or "
                                                   "[dossier:ID]; the part is kept either way, and the sentences "
                                                   "that name none are listed for you to fix.",
     "inputSchema": _obj({"dossier_id": _S, "part": _S, "text": _S, "run_ids": _A, "finding_ids": _A,
                          "knowledge_ids": _A, "kinds": _A}, ["dossier_id", "part", "text"])},
    {"name": "make_panel", "description": "A result's figures: tiles [{figure_id (a recorded figure), or run_id "
                                          "and part (a or b of a two-part figure, optional); caption; role}], the "
                                          "main run's figure first as the overview. role: overview (the map that "
                                          "states the result), detail (what explains it), evidence (what supports "
                                          "it) or limitation (where it fails or is untested). Overview and detail "
                                          "tiles make the main figure, evidence and limitation tiles the "
                                          "supplementary one. With paragraph (the results paragraph, every sentence "
                                          "naming its entry), it records the panel and paragraph part.",
     "inputSchema": _obj({"dossier_id": _S, "tiles": _A, "paragraph": _S}, ["dossier_id", "tiles"])},
    {"name": "report_result", "description": "Propose one result of the report, from your threads: its title (its "
                                             "conclusion, in a sentence), the findings it rests on (finding_ids), "
                                             "its figure (a figure the record holds, from save_figure, or a run of "
                                             "its findings, which stands for it until a figure is saved) and its "
                                             "text (150 to 300 words; each sentence names its entry, "
                                             "[finding:ID] or [run:ID], and each number comes from those runs). The "
                                             "same result_id again is its next version; what it leaves out stays as "
                                             "the last version had it. The user confirms or drops it in chat: "
                                             "decision (confirmed or dropped) with their words (user_words); "
                                             "unattended (open_project says so), you decide, with your reason "
                                             "(reason). A decision given with a changed title, text, findings or "
                                             "figure records that version, then the decision. Code writes the report "
                                             "again with each version and decision. answers: the question it "
                                             "answers, in a sentence; raised_by: the earlier result or finding whose "
                                             "reading raised it; the report orders the results by these links. "
                                             "samples: its n in a sentence: what its units are (mice, donors, "
                                             "captures, lanes of one pool), how many its lead tests took, and whether "
                                             "cells or units are the observations. next_steps: two or three next "
                                             "steps for the user to choose from, in your order, one recommended: each "
                                             "its question, its move (a kind of analysis) and why; a move the deposit "
                                             "cannot answer (an independent experiment, outside data) is shown as "
                                             "needing new data and is never the recommended one; code adds how many "
                                             "of the studies read went on to that move after a result like it, with "
                                             "their papers. A version without them keeps the last.",
     "inputSchema": contracts.schema(contracts.ReportResult)},
    {"name": "write_report", "description": "The report from the record: the screen; each result "
                                            "with its figure, its text, its findings with their checks and the "
                                            "prior knowledge beside them; the open questions in a few lines; the "
                                            "methods, a line per run; the doubts, the open items and the record's "
                                            "entries in an appendix. Every sentence is linked to its entry. It is "
                                            "written whatever is open. Code keeps it written as results are proposed "
                                            "or decided; call this for its "
                                            "opening (story), or when the user asks for it. The reply says where it "
                                            "is and what it holds; the user reads it on their page. story: the report's "
                                            "opening, a title sentence and three or four sentences on how the "
                                            "results lead from one to the next, each naming its entries.",
     "inputSchema": _obj({"story": _S})},
]
TOOL_NAMES = {t["name"] for t in TOOLS}
# gone from what the agent sees (docs/build_goal_session6_fixes.md, item 13; docs/adr/0035, D6): their code stays, the
# record still reads their events, and AnalysisService.call still runs them; what they did is done by the tools named
# beside them
RETIRED = {"plan_questions": "the first look starts when the screen object is built; open_thread",
           "add_question": "open_thread", "close_question": "thread_status", "record_sufficiency": "thread_status",
           "open_branch": "open_thread", "close_branch": "thread_status",
           "check_finding": "reword_finding with check_again", "record_check": "reword_finding's run_ids",
           "note_difference": "record_finding's differs", "record_knowledge": "record_finding's prior",
           "confirm_calling_rule": "the design: perturbation.assignment, confirmed on the design's form",
           "read_run": "run_result", "propose_dossier": "write_report", "record_dossier_part": "write_report",
           "make_panel": "report_result", "choose_dossiers": "report_result",
           "propose_validation": "report_result: the validation you propose in a result's text, from each lead's "
                                 "checks and the paradigm cards' last step",
           "null_of_right_unit": "record_finding: a finding's checks run its calibration on control-guide groups",
           "open_items": "pertura_status: what is pending, and each item due of you",
           "run_module": "the analyses by the field's kinds (guides, impact, genes, scores, states, programs, "
                         "similarity, conditions, combinations, enrichment, reproducibility), each starting its "
                         "modules",
           "list_modules": "each analysis tool's views, and read_knowledge with module",
           "module_result": "run_result",
           # never used in four development runs (f95ced6, 2026-11-15): the module picker's menu, the advisor's
           # standing and the plan, retired before the freeze; their code stays loadable for the research tree
           "module_menu": "read_knowledge with module; each analysis tool's views", "ask_module_menu": "read_knowledge with module; each analysis tool's views",
           "next_analyses": "read_knowledge with module; each analysis tool's views", "offer_analyses": "read_knowledge with module; each analysis tool's views",
           "record_module_picks": "read_knowledge with module; each analysis tool's views",
           "write_plan": "open_thread"}
# no agent writes the record (docs/adr/0051, D1): the recorder reads each turn's text and the inbox applies it
# through these methods, which the tests and the inbox still call; the agents see none of them
RECORDED_FROM_THE_REPLY = ("the recorder records it from what you say in your reply (docs/adr/0051); "
                           "pertura_status lists what it recorded")
RECORD_TOOLS = ("record_finding", "reword_finding", "open_thread", "thread_status", "note_reply", "raise_doubt",
                "answer_doubt", "report_result", "record_skeptic_check")
for _tool, _instead in list(RETIRED.items()):  # an earlier retired tool whose work went to one retired now
    if any(t in _instead for t in RECORD_TOOLS):
        RETIRED[_tool] = RECORDED_FROM_THE_REPLY
RETIRED.update({t: RECORDED_FROM_THE_REPLY for t in RECORD_TOOLS})
SHOWN = [t for t in TOOLS if t["name"] not in RETIRED]
# the tools that read the record: their replies go mostly to the subagents, so the main agent's hints (the module
# menu, the lead's threads waiting, the figure set's review) are not added to them, nor to a worker's calls
READS = ("read_record", "read_table", "read_knowledge", "module_result", "run_result", "list_modules",
         "gene_sets")
# what the main agent's readers alone record (the lead, the skeptic, the advisor, the module picker): their replies
# carry none of its hints either (the advisor's dry run of 2026-11-07 was told to run its own threads)
READERS_RECORD = ("record_skeptic_check", "record_module_picks")
BRIEF_HEAD = ("title", "statement", "about", "what", "text", "caption", "item", "description", "block", "kind")
BRIEF_LENGTH = 160  # characters of an entry's head in a reading without ids
# the moments that call for what is known to be looked up (docs/adr/0041, step 5; prior
# knowledge from memory serves, but a claim, a concept, a new way of analysis and something strange want a check)
KNOWN = {"result": "nothing known beside this result was looked up in this session: its prior knowledge is from "
                   "memory, which may be wrong where its conclusion, or a structure it names, rests on it. Look it up "
                   "with search_literature and give the paper beside one of its findings (reword_finding's prior, "
                   "with its PMID or DOI), or the search as the source ('search: <query>') where it found nothing",
         "own_code": "; its runs are your own code, not a module's: give the published method they follow the same "
                     "way",
         "difference": "it differs from prior knowledge that was not looked up in this session: look that knowledge "
                       "up with search_literature and give the paper beside the finding (prior, with its PMID or DOI)",
         "observation": "it stands out against what is known, and nothing about it was looked up in this session: "
                        "search_literature says whether it is expected; give what you find beside the thread or its "
                        "finding (prior, with the paper's PMID or DOI)"}


READ_MAX = 30000  # characters of one read_record reply's text: Claude Code refused 123 longer, the smallest 50,418


def _paged(out, page=1, at_most=READ_MAX):
    """A read_record reply cut to pages of at most at_most characters of text, the latest entries first: its page,
    how many pages, and the call for the next. One that fits is given whole; its images are not counted.
    shortcut: an entry longer than a page goes alone on its page; cut such an entry's own fields if one appears."""
    images = out.pop("_images", None)
    if len(json.dumps(out, default=str)) > at_most:
        fixed = {k: v for k, v in out.items() if not isinstance(v, dict)}
        entries = [(k, i, v) for k, part in out.items() if isinstance(part, dict) for i, v in reversed(list(part.items()))]
        base = len(json.dumps(fixed, default=str)) + 200
        pages, cur, size = [], [], base
        for e in entries:
            n = len(json.dumps({e[1]: e[2]}, default=str)) + len(e[0]) + 8
            if cur and size + n > at_most:
                pages.append(cur)
                cur, size = [], base
            cur.append(e)
            size += n
        pages.append(cur)
        k = max(1, min(int(page or 1), len(pages)))
        shown = {}
        for part, i, v in pages[k - 1]:
            shown.setdefault(part, {})[i] = v
        out = {**fixed, **shown, "page": k, "pages": len(pages),
               **({"next": f"page {k + 1} gives the next entries, the latest first; ids gives chosen ones"}
                  if k < len(pages) else {})}
    return {**out, "_images": images} if images else out


def in_brief(entry, key=None):
    """A record entry (under key) in one line, for a reading without IDs (development run on GSE335887, 2026-11-06:
    the workers read every thread in full, 104 entries, and named 6 afterwards): what it is, cut short, and where it
    stands (its status, state or kind, the open or answered doubt, the last figure review, the findings or runs it
    holds, what a result rests on, the run that made it)."""
    if not isinstance(entry, dict):
        return entry
    named = next((k for k in BRIEF_HEAD if entry.get(k)), None)
    head = str(entry[named]) if named else ""
    line = head if len(head) <= BRIEF_LENGTH else head[:BRIEF_LENGTH - 3] + "..."
    stands = [str(entry[k]) for k in ("status", "state", "kind")
              if k != named and isinstance(entry.get(k), str) and entry[k]]
    if entry.get("on_line") is True:
        stands.append("on the main line")
    if "answer" in entry:
        stands.append("answered" if entry["answer"] else "open")
    if entry.get("would_change") and not entry.get("answer"):  # an open doubt's test, for an answer decided from
        # this list: on GSE278572 (01f3de3) six answers went out after a compaction, from lines without their tests
        test = str(entry["would_change"])
        stands.append("its test: " + (test if len(test) <= BRIEF_LENGTH else test[:BRIEF_LENGTH - 3] + "..."))
    if entry.get("reviews"):
        stands.append(f"review {entry['reviews'][-1].get('verdict')}")
    for k in ("findings", "finding_ids", "run_ids"):
        if isinstance(entry.get(k), list) and entry[k]:
            stands.append(f"{len(entry[k])} {k.replace('_ids', 's')}")
    if isinstance(entry.get("rests_on"), dict):
        stands.append("rests on " + ", ".join(f"{k} {v}" for k, v in entry["rests_on"].items()
                                              if isinstance(v, (int, float, str))))
    if entry.get("run_id") and entry["run_id"] != key:
        stands.append(f"run {entry['run_id']}")
    return line + (f" [{'; '.join(stands)}]" if stands else "")
SCHEMAS = {t["name"]: t["inputSchema"] for t in TOOLS}
TYPE_WORDS = {"string": "a text", "integer": "a whole number", "number": "a number", "boolean": "true or false",
              "object": "an object ({...})", "array": "a list ([...])"}


def _folded(value):
    """A value as an enum's values are matched: case, '_' and '-' read as spaces."""
    return " ".join(re.split(r"[\s_-]+", str(value).strip().lower()))


def _module_named(name, registry):
    """A module by its name, or by the name without its question's letter where one module has it ("genes" is
    C.genes: the fair comparison's workers asked for it so)."""
    if name in registry:
        return name
    same = [m for m in registry if m.split(".", 1)[-1].lower() == str(name).lower()]
    if len(same) == 1:
        return same[0]
    raise AnalysisError(f"no module {name}" + (f"; it is one of {same}" if same else "; the analysis tools' views name them"))


def folded_arguments(tool, args):
    """The call's arguments, each enum value in its schema's own spelling where only case, '_' or '-' differ
    ("set_aside" is "set aside"). A value no spelling of the list matches is left as given, for the tool to answer."""
    props = (SCHEMAS.get(tool) or {}).get("properties") or {}
    out = dict(args)
    for name, value in args.items():
        spec = props.get(name)
        allowed = spec.get("enum") if isinstance(spec, dict) else None
        if allowed is None and isinstance(spec, dict):  # an optional value's enum sits under anyOf (contracts)
            allowed = next((a["enum"] for a in spec.get("anyOf") or [] if isinstance(a, dict) and "enum" in a), None)
        if not allowed or not isinstance(value, str) or value in allowed:
            continue
        same = [a for a in allowed if isinstance(a, str) and _folded(a) == _folded(value)]
        if len(same) == 1:
            out[name] = same[0]
    for name in ("choices", "inputs"):  # a value quoted twice ("\"targets_own\"") ran silently empty on GSE190604
        if isinstance(out.get(name), dict):
            out[name] = {k: re.sub(r"^(['\"])(.*)\1$", r"\2", v) if isinstance(v, str) else v
                         for k, v in out[name].items()}
    return out


def _of_type(value, want):
    if want in ("integer", "number"):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and (want == "number" or float(value).is_integer()))
    return isinstance(value, {"string": str, "object": dict, "array": list, "boolean": bool}.get(want, object))


def mistyped(tool, args):
    """The arguments whose value is not of the type the tool's schema asks, a phrase each."""
    props = (SCHEMAS.get(tool) or {}).get("properties") or {}
    out = []
    for name, value in args.items():
        spec = props.get(name)
        want = spec.get("type") if isinstance(spec, dict) else None
        if isinstance(want, str) and value is not None and not _of_type(value, want):
            out.append(f"{name} is asked as {TYPE_WORDS.get(want, want)} and was given as {type(value).__name__}")
    return out
DESIGN_TOOLS = ("describe_deposit", "read_metadata", "measure", "check_design", "confirm_design", "get_design",
                "build_screen_object")


class AnalysisError(Exception):
    pass


def _rows(path):
    """The rows of a tab-separated table (its lines but the header), or None where it cannot be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except OSError:
        return None


def _records(table):
    """A table's rows as JSON values at full precision (pandas' JSON encoder rounds small p-values to zero), with
    missing values as null."""
    def plain(x):
        if hasattr(x, "item"):
            x = x.item()
        return None if isinstance(x, float) and x != x else x
    return [{k: plain(v) for k, v in row.items()} for row in table.to_dict("records")]


_COMPARE = re.compile(r"^\s*`?(?P<col>[^`<>=!]+?)`?\s*(?P<op><=|>=|==|!=|<|>|=)\s*(?P<val>.+?)\s*$")


def _logged(e):
    """A failure a reply goes on without (an `except Exception` that says why it may), in one line of the server's
    log (the MCP log), so a run's record and that log together tell what was skipped. The service's own AnalysisError
    is a state said elsewhere (a design not confirmed yet), not a failure, and is left out."""
    import sys
    import traceback
    if isinstance(e, AnalysisError):
        return
    frame = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
    where = f" at {os.path.basename(frame.filename)}:{frame.lineno}" if frame else ""
    print(f"pertura: went on without: {type(e).__name__}: {e}{where}", file=sys.stderr, flush=True)


def _module_and_choices(run):
    """A module run's module and what it was given ({choices, inputs, cells, ...}), from its logged what."""
    what = str(run.get("what") or "")
    if not what.startswith("module "):
        return None, {}
    module, _, rest = what[len("module "):].partition(" with ")
    try:
        given = json.loads(rest) if rest else {}
    except ValueError:
        given = {}
    return module.strip(), given if isinstance(given, dict) else {}


def _queue(run):
    """The queue a logged module run waits in (question_modules.queue_of), or None."""
    from .question_modules import queue_of
    module, given = _module_and_choices(run)
    return queue_of(module, given.get("choices")) if module else None


def rows_where(table, where):
    """A table's rows that meet `where`: {column: value} (a value or a list of values, compared as text), or a text
    of comparisons joined by "and" ("q < 0.05 and target == Hdac1"; <, <=, >, >=, ==, =, !=; a number compares as a
    number, anything else as text, quotes around it dropped)."""
    import pandas as pd
    if not where:
        return table
    if isinstance(where, dict):
        for col, val in where.items():
            if col not in table:
                raise ValueError(f"no column {col!r}; its columns are {list(table.columns)}")
            vals = [str(x) for x in val] if isinstance(val, (list, tuple)) else [str(val)]
            table = table[table[col].astype(str).isin(vals)]
        return table
    if not isinstance(where, str):
        raise ValueError("where is {column: value} or a text such as \"q < 0.05 and target == Hdac1\"")
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "''", where)  # the text outside its quoted values
    if re.search(r"[()|&~]|\b(or|not)\b|\bin\s*\[", bare):  # grouped, alternatives or a list (target in [...]):
        # pandas' query; a bare "in" is a value's word ("condition == in vivo")
        # no variables, calls or attributes: only columns, values and operators
        if re.search(r"@|__|\b(?!(?:and|or|not|in)\b)[A-Za-z_]\w*\s*\(|[A-Za-z_]\.[A-Za-z_]", bare):
            raise ValueError(f"cannot read {where!r}: pandas' query here compares columns with values, without "
                             "calls, attributes or @variables")
        try:
            return table.query(where, engine="python")
        except Exception as e:
            raise ValueError(f"cannot read {where!r} as pandas' query ({type(e).__name__}: {e}); text values go in "
                             "quotes there, such as state == 'S09'") from None
    for clause in re.split(r"\s+and\s+(?=(?:[^'\"]*['\"][^'\"]*['\"])*[^'\"]*$)", where.strip(), flags=re.I):
        # "and" inside a quoted value ("state_name == 'naive and memory'") does not split it
        m = _COMPARE.match(clause)
        if not m:
            raise ValueError(f"cannot read {clause!r}: give a column, one of < <= > >= == != and a value")
        col, op, raw = m.group("col").strip(), m.group("op"), m.group("val").strip().strip("'\"")
        if col not in table:
            raise ValueError(f"no column {col!r}; its columns are {list(table.columns)}")
        try:
            val = float(raw)
            x = pd.to_numeric(table[col], errors="coerce")
        except ValueError:
            val, x = raw, table[col].astype(str)
            if op not in ("==", "=", "!="):
                raise ValueError(f"{clause!r}: {raw!r} is not a number, so only ==, = or != compare it") from None
        keep = {"<": x < val, "<=": x <= val, ">": x > val, ">=": x >= val, "==": x == val, "=": x == val,
                "!=": x != val}[op]
        table = table[keep.fillna(False) if hasattr(keep, "fillna") else keep]
    return table


def data_location():
    """Where the data and the notebook are, and which of the agent's own tools reach them, from how the plugin
    launched this server: PERTURA_LAUNCH when it says (ssh or local), else over ssh where the remote launch sets
    PERTURA_HOST or the process runs in an ssh session, else on the agent's own computer."""
    launch = os.environ.get("PERTURA_LAUNCH", "").strip().lower()
    host = os.environ.get("PERTURA_HOST", "").strip()
    if launch not in ("ssh", "local"):
        launch = "ssh" if host or os.environ.get("SSH_CONNECTION") else "local"
    if launch == "ssh":
        where = f"the analysis server ({host})" if host else "the analysis server"
        return (f"The data and the notebook are on {where}. Your own tools (Bash, Read, Glob) see the computer you "
                "run on, not this server: look at the data with run_code.")
    return "The data and the notebook are on the computer you run on: your own tools and run_code see the same files."


def with_host(reply, config=None):
    """The reply with the analysis host the remote launch names (PERTURA_HOST), so the plugin's job check can reach
    the server where the plugin's own option is not set; and the server's code (the folder that holds src), its
    Python and the workspace config it was started with (`config`), for the recorder's readings and its applying of
    the inbox on the server where the plugin's path options do not arrive as given (Git Bash on Windows rewrites a
    hook's /home/... values)."""
    host = os.environ.get("PERTURA_HOST", "").strip()
    if not host:
        return reply
    import sys
    code = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return {**reply, "analysis_host": host, "remote_code": code, "remote_python": sys.executable,
            **({"workspace_config": config} if config else {})}


CHECKS_RUN = "checks of finding "  # a checks job's what, before its finding's ID
TRIES = ("what", "value", "other")  # a declared choice ('Choice: <what> = <value>; other: <other>'), a thread tries


def checks_verdict(finding):
    """What a finding's checks say of it, as the replies give it: "did not hold: <the checks that disagree>"
    (record.disagreement), "not tested: <why>" where no lead test ran, or "held", with the lead's outcome where it
    is not significant its way; None while no check is recorded."""
    leads = [each.get("lead") or {} for each in rc.checks_by_target(finding).values() if each]
    if not leads:
        return None
    if rc.checks_disagree(finding):
        return "did not hold: " + rc.disagreement(finding)
    if all(lead.get("outcome") in (None, "not run") for lead in leads):
        why = next((str(lead["reason"]) for lead in leads if lead.get("reason")), "")
        return "not tested" + (f": {why}" if why else "")
    weak = sorted({str(lead["outcome"]) for lead in leads if lead.get("outcome") not in (None, "agrees")})
    return "held" + (f" (the lead test: {', '.join(weak)})" if weak else "")


# diagnostics a reply names, detail: true giving them; the recipe comes as the notebook's lines (in_the_notebook)
DETAIL_ONLY = ("package_calls", "recipe_as_run", "policy")
# a run that ended, as the next reply opens it (the build goal of 2026-11-11, step 5): its reading and figure; each
# reply carried its whole result, read again at every later call
ANNOUNCED = ("status", "result", "summary", "biology", "published_next", "if_it_misleads", "figure_shown")
FIRST = ("run_id", "state", "run", "result", "summary", "biology", "published_next")  # what a result's reply leads with


def _concise(out):
    """A module's result as a reply gives it: the summary and its biology first, then the status, the calibration
    and the short diagnostics. The long diagnostics, the package calls and the policy stay in result.json and are
    named (docs/adr/0037, D3); the figure's own checks stay in the record, for its review."""
    out = {k: v for k, v in out.items() if k != "figure_checks" and not (k == "job_id" and v == out.get("run_id"))}
    out = {**{k: out[k] for k in FIRST if k in out}, **out}
    d = out.get("diagnostics")
    if not isinstance(d, dict):
        return out
    kept = {k: v for k, v in d.items() if k not in DETAIL_ONLY
            and (k.startswith("calibration") or len(json.dumps(v, default=str)) <= DIAGNOSTIC_MAX)}
    left = sorted(k for k in d if k not in kept and k != "recipe_as_run")
    notebook = d.get("recipe_as_run")  # the pt lines that give the same result (docs/adr/0044, D3)
    return {**out, "diagnostics": kept, **({"in_the_notebook": notebook} if notebook else {}),
            **({"in_result_json": left} if left else {})}

# the modules whose pair input is two conditions (E.combinations' pair is a pair of perturbations)
PAIR_OF_CONDITIONS = ("E.condition_x_perturbation",)


def qm_registry():
    """The modules' registry (pertura.question_modules.REGISTRY)."""
    from .question_modules import REGISTRY
    return REGISTRY


def _the_surveys_own(run):
    """A run the survey started that no call of the agent's has taken (docs/adr/0045, D2 and D3)."""
    from .survey import SOURCE
    return run.get("source") == SOURCE and not run.get("claimed")


class AnalysisService:
    TOOLS, INSTRUCTIONS, SERVER_INFO = SHOWN, INSTRUCTIONS, SERVER_INFO
    RETIRED = RETIRED  # the tools the agent no longer sees, with the tool that does each one's work
    USES_ELICITATION = True
    CONCURRENT = True
    WAIT_TOOLS = ("wait_for_runs",)  # in the server's pool of waits, so a long wait holds no other call

    def __init__(self, config):
        self.cfg = dict(config)
        self.root = os.path.abspath(self.cfg["workspace"])
        os.makedirs(self.root, exist_ok=True)
        self.project = self.deposit = self.design = self.record = None
        self._inbox_lock = threading.Lock()  # the main agent's and the workers' calls apply the inbox one at a time
        self.notebooks = {}
        self.flows = {}  # notebook -> (its kernel's start count, pertura.flow.Kernel)
        self._opened = set()  # the runs module_result has given in this session
        self._steering_given = {}  # what is due of the main agent: {item: {ids, at, again}} (_with_lead_threads)
        self._calls_seen = 0  # the replies _with_lead_threads has steered, the clock of SAY_AGAIN_AFTER
        self._briefs_given = set()  # (item, thread or figure) whose brief a reply gave the main agent (BRIEFS_ONCE)
        self._given_once = set()  # the items a reply gave the main agent once in this session (ONCE)
        # what a reply has given once: the ended runs, each version of the design, each build's cells overview
        self._announced = set()
        self._page_state_seq = None  # the record's last event when the page state was last written
        self._digest_seq = None  # the record's last event when pertura_status last gave the digest
        self._said_kept = None  # what the replies have said, as last kept in the project
        self._survey_list = None  # (what it was worked out for, _survey_key; the survey's comparisons)
        self._survey_facts = None  # (_survey_key, the survey's facts), for the report and the page
        self._survey_baselines = None  # ((project, states run), the samples' baselines read from it)
        self._survey_done_for = None  # the _survey_key whose survey's end has written the report again
        self._survey_driver = None  # the thread that steps the survey when no call comes
        self._checks_driver = None  # the thread that steps the checks queue when no call comes
        # the MCP server's service starts queued checks with no call; apply-inbox at a turn's Stop only queues them
        # (queue_only), so every start is this one process's, under its lock, and none starts twice
        self.checks_in_background = False
        self.queue_only = False
        self._told = {}  # what the main agent's replies delivered, first time each: verdicts, doubts, open parts
        # the MCP server's service drives the survey with no call (docs/adr/0045, D2); a test's service, or one that
        # answers a single call, does not
        self.survey_in_background = False
        self._lock = threading.RLock()
        self._spec_keys = {}  # a queued checks spec's comparison, its finding and question, by its path
        self._started_specs = set()  # the checks specs this service started
        self._review_said = set()  # the review's doubts and lines a reply has given (each once)
        self._stop_waits = threading.Event()  # set at the end of the session's input: every wait ends at once
        self._wait_turn = 0  # the latest wait on every running run: an earlier one hands over to it

    def call(self, tool, args=None, elicit=None):
        args = dict(args or {})
        if tool not in TOOL_NAMES:
            return False, {"error": f"unknown tool {tool}"}
        reopened = None
        if self.project is None and tool != "open_project":
            try:
                reopened = self._reopen_last()
            except Exception as e:  # a project that cannot be opened again leaves the call to say so
                _logged(e)
                reopened = None
        recorded = None
        if self.project is not None and self.record is not None:
            try:
                recorded = self._apply_inbox()
                self._figures_for_results()
            except Exception as e:  # applying the recorder's files never fails the call
                _logged(e)
            try:
                self._start_queued_checks()
                self._doubts_from_checks()
                self._drive_the_checks()
            except Exception as e:  # starting queued checks, or opening their doubts, never fails the call
                _logged(e)
        ok, out = self._call(tool, args, elicit)
        if recorded and isinstance(out, dict):
            out = {**out, "recorded": recorded}
        if ok and isinstance(out, dict):
            self._note_told(out)
        try:
            self._write_digest()
        except Exception as e:  # the recorder's view of the record never fails the call
            _logged(e)
        try:
            self._keep_said()
        except OSError:  # what the replies said is kept for the next server; never a reason to fail the call
            pass
        try:
            self._write_page_state()
        except Exception as e:  # the page's copy never fails the call
            _logged(e)
            pass
        try:
            overview = self._overview_when_due()
        except Exception as e:  # starting the screen's map never fails the call it follows
            _logged(e)
            overview = None
        if overview and isinstance(out, dict):
            out = {**out, "screen_overview_started": overview}
        try:
            surveyed = self._survey_when_due() if self.survey_in_background else None
            self._drive_the_survey()
        except Exception as e:  # the survey's next run never fails the call it follows
            _logged(e)
            surveyed = None
        if surveyed and isinstance(out, dict):
            out = {**out, "survey_started": surveyed}
        if reopened and isinstance(out, dict):
            out = {"reopened": reopened, **out}
        return ok, out

    def _apply_inbox(self):
        """The recorder's files (docs/adr/0051, D4), applied in the order they came through this service's methods;
        each moves to inbox/applied with what was applied and refused beside it, which the reply names."""
        from . import recording
        done = []
        with self._inbox_lock:
            for path in recording.inbox_files(self.project):
                name = os.path.basename(path)
                tag = os.path.splitext(name)[0]
                claimed = os.path.join(os.path.dirname(path), "applying", name)
                os.makedirs(os.path.dirname(claimed), exist_ok=True)
                try:
                    os.rename(path, claimed)  # claimed before it is read: no file is applied twice
                except OSError:
                    continue
                try:
                    with open(claimed, encoding="utf-8") as fh:
                        out = recording.apply(self, json.load(fh), tag)
                except (OSError, ValueError) as e:
                    out = {"applied": [], "refused": [{"what": "the whole file", "why": f"unreadable: {e}"}]}
                folder = os.path.join(os.path.dirname(path), "applied")
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, tag + ".outcome.json"), "w", encoding="utf-8") as fh:
                    json.dump(out, fh, indent=1)
                os.replace(claimed, os.path.join(folder, name))
                done.append({"file": tag, **out})
        return done

    def _figures_for_results(self):
        """A result whose figure is a run of its findings standing for one (the recorder kept it although no run had
        saved a figure, revision 3) takes the latest figure its findings' runs save, as a next version."""
        st = self.record.state()
        for rid, r in (st.get("results") or {}).items():
            if r.get("figure") in st["figures"] or r.get("decision") == "dropped":
                continue
            theirs = {x for f in r.get("finding_ids") or [] for x in (st["findings"].get(f) or {}).get("run_ids") or []}
            drawn = [fid for fid, fg in st["figures"].items() if fg.get("run_id") in theirs]
            if drawn:
                try:
                    self.report_result(rid, figure=max(drawn, key=lambda fid: st["figures"][fid].get("seq") or 0))
                except AnalysisError as e:  # the result keeps the run that stands for its figure
                    _logged(e)

    def _write_digest(self):
        """What the recorder reads before it records a turn (docs/adr/0051, D2): the IDs it may refer to, each with
        a short text: findings, results with the user's decision, open doubts and threads, and the next steps that
        wait for the user's pick."""
        if self.project is None or self.record is None:
            return
        st = self.record.state()
        digest = {
            "findings": {k: str(f.get("statement") or "")[:160] for k, f in (st.get("findings") or {}).items()},
            "results": {k: {"title": r.get("title"), "decision": r.get("decision")}
                        for k, r in (st.get("results") or {}).items()},
            "open_doubts": {k: {"finding_id": d.get("finding_id"), "text": str(d.get("text") or "")[:160]}
                            for k, d in (st.get("doubts") or {}).items()
                            if not d.get("answer") or d["answer"].get("outcome") in rc.OPEN_ANSWERS},
            "open_threads": {k: str(t.get("about") or "")[:160] for k, t in (st.get("threads") or {}).items()
                             if t.get("status", "open") == "open"},
            "next_steps_awaiting": rc.next_step_awaiting(st),
            "cell_columns": self._cell_columns()}
        folder = os.path.join(self.project, "record")
        tmp = os.path.join(folder, "digest.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(digest, fh, indent=1)
        os.replace(tmp, os.path.join(folder, "digest.json"))

    def _cell_columns(self, most=12):
        """The cells' own columns a claim's cells can name, each with its values: those with 2 to `most` values (a
        sample, a condition, a model), read once per build of the screen object."""
        sd = self._screen_dir() if self.project else None
        path = os.path.join(sd, "cells.tsv.gz") if sd else None
        if not path or not os.path.exists(path):
            return {}
        key = (path, os.path.getmtime(path))
        if getattr(self, "_cell_columns_of", (None,))[0] != key:
            import pandas as pd
            table = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
            cols = {c: sorted(table[c].unique().tolist()) for c in table.columns
                    if 2 <= table[c].nunique() <= most and c not in ("barcode", "cell")}
            self._cell_columns_of = (key, cols)
        return self._cell_columns_of[1]

    def _call(self, tool, args, elicit):
        from .notebook import NotebookError
        args = folded_arguments(tool, args)
        args.pop("show_figures", None)  # the plugin's hook reads it: whether the reply's figures reach the agent
        agent = args.pop(AGENT_ARG, None)  # a subagent's call, as the plugin's hooks mark it
        try:
            return self._run_tool(tool, args, elicit, agent)
        except (AnalysisError, rc.RecordError, DesignError, NotebookError) as e:
            return False, {"error": str(e)}
        except Exception as e:  # an unexpected failure is named to the agent, never hidden as "Internal error"
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            out = {"error": f"Pertura failed inside {tool}: {type(e).__name__}: {str(e)[:500]}",
                   "note": "a fault in Pertura, not in the request; say so to the user. A run this call "
                           "started may be running: read_record lists the runs before you call it again"}
            wrong = mistyped(tool, args)
            if wrong:  # a value of another type than the tool asks: said, so the next call can give it as asked
                out["arguments"] = "; ".join(wrong)
                out["note"] = ("the arguments may be the cause: give them as the tool's description asks. A run this "
                               "call started may be running: read_record lists the runs before you call it again")
            return False, out

    def _run_tool(self, tool, args, elicit, agent=None):
        if tool in DESIGN_TOOLS:
            if self.design is None:
                return False, {"error": "open the project first (open_project with the data folder)"}
            if tool == "build_screen_object":
                try:
                    self._keep_build()  # the build before a rebuild, to compare the new one with
                except (OSError, ValueError):  # unreadable (a half-written summary): the rebuild still runs
                    pass
            ok, out = self.design.call(tool, args, elicit=elicit if tool == "confirm_design" else None)
            if ok and tool == "build_screen_object" and isinstance(out, dict) and out.get("state") == "done":
                out = {**out, **self._after_build()}
            elif ok and tool == "confirm_design" and isinstance(out, dict) and self._built_and_idle():
                out = {**out, **self._after_build()}  # the cells' rule confirmed after the build: the first look
            return ok, (self._with_ended(out) if ok else out)
        args, wrong = contracts.checked(tool, args)  # docs/adr/0038, D1
        if wrong:
            return False, {"error": wrong}
        fn = getattr(self, tool)
        sig = inspect.signature(fn)
        try:
            sig.bind(**args)
        except TypeError as e:  # the tool's own arguments named, so the next call is right (session 4, 1g)
            return False, {"error": f"bad arguments: {e}. {tool} takes {arguments_of(fn)}"}
        if tool == "wait_for_runs" and agent:  # it watches without taking over the main agent's wait
            args = {**args, "subagent": True}
        if "opened_by" in sig.parameters:  # who opened a node of the tree: the hooks' agent type, or the main agent
            args = {**args, "opened_by": agent or "main"}
        out = fn(elicit=elicit, **args) if "elicit" in sig.parameters else fn(**args)
        if (tool == "read_record" and args.get("what") == "findings" and agent == "pertura:skeptic"
                and isinstance(out, dict) and self.record is not None):
            out = {**out, **self._checks_papers_reported(self.record.state(), args.get("ids"))}  # its step 3
        # the main agent's hints and the runs ended since reach the main agent, not its readers: one server serves every
        # agent, and on GSE267740 the lead's 31 open_thread replies carried 83 KB of worker briefs, while 9 ended runs
        # were opened only in a subagent's reply. The readers' own recording tools say neither, marked or not; the
        # main agent's reads open the runs ended since (session 4, 1f), a subagent's once the hooks mark it
        if agent or tool in READERS_RECORD:
            return True, out
        out = self._with_ended(out)
        if tool in READS or args.get("worker"):  # a read: the doubts to answer and the open parts, not the rest
            return True, self._with_lead_threads(out, only=READ_ITEMS)
        return True, self._with_lead_threads(out, every=tool == "pertura_status")

    def _threads_waiting(self, st):
        """The open threads no one has taken that wait on the main agent: no run names the thread, and no worker's
        notebook is named after it (agents/worker.md names it <thread>-<what it tries>). They are the first look's
        observations and the choices the agent declared."""
        # no main line and no forks (2026-11-17): a first-look observation waits for a worker, and a choice the agent
        # declared waits for one to try its alternative (docs/adr/0032, D9, revived 2026-11-20); the user's picked step
        # is the main agent's own. The research lead's and the advisor's threads left with them (2026-11-20)
        return self._open_and_not_taken(st, lambda th: (
            th.get("kind") == rc.OBSERVATION or isinstance(th.get("tries"), dict)))

    def _waiting_brief(self, st, tid, th):
        """The brief of a waiting thread for the worker that takes it: a declared choice's, a fork's, a doubt's test,
        a kind of conclusion's or a probe's."""
        doubt = ((st.get("branches") or {}).get(tid) or {}).get("doubt_id")
        if isinstance(th.get("tries"), dict):
            return self._choice_brief(tid, th["tries"])
        if th.get("fork_of"):
            return self._fork_brief(st, tid)
        if doubt in (st.get("doubts") or {}):
            return self._doubt_brief(tid, doubt, th.get("about"), st)["worker"]
        return self._worker_brief(tid, th)

    def _time_so_far(self):
        """The minutes since the project's record began, for the user where the analysis asks whether to go on."""
        import datetime
        try:
            first = datetime.datetime.fromisoformat(str(self.record.events()[0]["timestamp"]))
            return f"{int((datetime.datetime.now(first.tzinfo) - first).total_seconds() // 60)} minutes"
        except (IndexError, KeyError, TypeError, ValueError):  # a record without its times says none
            return None

    @staticmethod
    def _choice_brief(tid, tries):
        """A declared choice's brief ('Choice: <what> = <its value>; other: <the alternative>'): the worker reruns
        the analysis with the alternative, the one change, and says whether the conclusion changes."""
        worker = f"{tid}-choice"
        return {"worker": worker, "brief": (
            f"Thread {tid}: rerun {tries['what']} with {tries['other']} instead of {tries['value']}, and say whether "
            f"the conclusion changes. Read the thread (read_record what threads, ids [{tid}]). Rerun with that one "
            f"change and nothing else, on the same targets and cells: the analysis tool of its kind (thread_id {tid}), "
            f"or your notebook, run_code with worker '{worker}'. Then say how the thread ends, with its runs: "
            "explained where the conclusion holds, contradicted where it changes, or out of data.")}

    def _worker_brief(self, tid, th):
        """A brief for the worker that takes a waiting thread: a kind of conclusion's (_kind_brief), or a probe's
        (agents/worker.md, a probe). The recorder records what the worker says (docs/adr/0051)."""
        if th.get("reads") in candidates.KINDS:
            return self._kind_brief(tid, th["reads"], th.get("about"))["worker"]
        worker = f"{tid}-probe"
        test = f" Its test: {str(th['test']).strip()}" if th.get("test") else ""
        return {"worker": worker, "brief": (
            f"Thread {tid}: {str(th.get('about') or '').strip()}{test} Read it (read_record what threads, "
            f"ids [{tid}]) and the design (get_design). Run what tests it most directly with the analysis tools (each "
            f"with thread_id {tid}, on the cells it names), and your notebook for what no tool does: run_code with "
            f"worker '{worker}'. Read each result against its control-guide line. Say each claim that stands past "
            "it, with its targets, feature, direction and runs, naming the genes, programmes, states or targets it "
            "moves; say what shows nothing. Then say how the thread ends: explained, contradicted or out of data, "
            "with its runs. Answer in at most fifteen lines: each claim, and what surprised you.")}

    def _open_and_not_taken(self, st, which):
        """The open threads, of those `which` keeps, that no worker has taken: no run names the thread, and no
        worker's notebook is named after it."""
        taken = {r.get("thread_id") for r in st["runs"].values()}
        return [t for t, th in (st.get("threads") or {}).items()
                if which(th) and th.get("status") == "open" and t not in taken
                and not any(k == t or k.startswith(f"{t}-") for k in self.notebooks)]

    def _with_lead_threads(self, out, every=False, only=None):
        """A reply names what is due of the main agent's (_steering): the doubts to answer, the open parts of the
        question, the threads waiting for a worker, the results to decide and the next steps to offer. Each comes once,
        when it is due or changes (P8: the module menu was in 70% of a run's replies); pertura_status (every) gives all
        that is due. Every reply names what is still due (pending), from this one list alone (docs/adr/0050, D2). Its
        name is from the research lead's threads, which left with the lead (2026-11-20). only (a read's reply): those
        items alone, said by the same rule; the others wait for a call that says them."""
        if self.record is None or not isinstance(out, dict):
            return out
        due = self._steering(out)
        pending = {}  # {decider: [labels]}
        for key, (label, decider) in PENDING.items():
            if key in due:
                pending.setdefault(decider, []).append(label)
        if every:  # pertura_status: all that is due; a once-only item counts as given here too
            shown = {k: v for k, v in due.items() if k not in ONCE or k not in self._given_once}
            self._given_once |= {k for k in ONCE if k in shown}
            return {**shown, "pending": pending}
        if only is None:
            self._calls_seen += 1
        else:
            due = {k: v for k, v in due.items() if k in out or k in only}
        added = set(due) - set(out)
        for key in set(self._steering_given) - added:  # no longer due: given again when it is due again
            if only is None or key in only:
                del self._steering_given[key]
        for key in added:  # an item is said when it falls due, and once more if still undone (2026-11-17)
            if key in ONCE:  # once in a session, whatever changes after
                if key in self._given_once:
                    del due[key]
                self._given_once.add(key)
                continue
            ids, given = _objects_named(due[key]), self._steering_given.get(key)
            if given is None or not ids <= given["ids"]:  # new, or it names a new doubt, thread or result
                self._steering_given[key] = {"ids": ids | (given or {}).get("ids", set()), "at": self._calls_seen,
                                             "again": False}
            elif not given["again"] and self._calls_seen - given["at"] >= SAY_AGAIN_AFTER:
                given["again"] = True
            else:  # said already: research_lead_due was said about six times an episode (d2, 27 runs)
                del due[key]
        for key in BRIEFS_ONCE:  # each thread's or figure's brief once; a later reply names it alone
            if key in due:
                briefs = {n: b for n, b in (due[key].get("briefs") or {}).items()
                          if (key, n) not in self._briefs_given}
                self._briefs_given |= {(key, n) for n in briefs}
                due[key] = {**{k: v for k, v in due[key].items() if k != "briefs"},
                            **({"briefs": briefs} if briefs else {})}
        return {**due, "pending": pending}

    def _steering(self, out):
        """The reply with every item that is due of the main agent (_with_lead_threads)."""
        try:
            st = self.record.state()
            # every doubt with no answer, the checks' and the reviewer's (the loop decided by the record, 2026-11-20:
            # the session ends when none is open), asked where the agent answers, in the form the recorder reads
            from .recording import doubt_open
            waiting = [(did, d) for did, d in (st.get("doubts") or {}).items() if doubt_open(d)]
            if waiting:
                out = {**out, "doubts_to_answer": {
                    "doubts": [{"doubt_id": did, "finding_id": d.get("finding_id"), "text": d.get("text"),
                                "opened_by": d.get("opened_by") or "the main agent",
                                **({"would_change": d["would_change"]}
                                   if d.get("would_change") and d.get("opened_by") != CHECKS_DOUBT else {})}
                               for did, d in waiting[:8]],
                    "do": DOUBTS_DO}}
            # the reviewer reads each proposed result's claims in the background, started by the recorder (docs/adr/
            # 0051, D3): its line on what the question lacks is information, said once; its doubts are above
            lacks = st.get("question_lacks") or {}
            if lacks and lacks.get("seq") not in self._review_said:
                self._review_said.add(lacks["seq"])
                out = {**out, "the_review": {"the_question_lacks": lacks["text"], "do": REVIEW_DO}}
            # the_users_next_step is gone (2026-11-17): it ended the turn 0 times in 7; the next steps are on the
            # result cards, and "go ahead" takes the recommended one
            undecided = [rid for rid, r in (st.get("results") or {}).items() if not r.get("decision")]
            if undecided:  # the user's decision on each (the orchestration page)
                out = {**out, "results_to_decide": {
                    "results": undecided,
                    "do": "show the user each in chat with its figure; they confirm or drop it, and their reply is "
                          "recorded as they gave it"}}
            owed = [rid for rid, r in (st.get("results") or {}).items()
                    if not r.get("next_steps") and r.get("decision") != "dropped"]
            if owed:
                out = {**out, "next_steps_to_offer": {
                    "results": owed, "studies_went_on_to": self._studies_went_on_to(st, owed),
                    "do": "give each two or three next steps in your reply, in your order, the first recommended, "
                          "each a question, its move (a kind of analysis the knowledge base names) and why"}}
            waiting = self._threads_waiting(st)
            if waiting:  # one notice for the threads no one has taken (five before, split by who raised them)
                threads = st.get("threads") or {}
                out = {**out, "threads_waiting": {
                    "for_workers": waiting, "briefs": {t: self._waiting_brief(st, t, threads[t]) for t in waiting},
                    "do": ("one worker each (agents/worker.md) with its brief, in the background, up to "
                           f"{self._resources()['max_workers']} at once, while you go on with your own work; or take "
                           "one yourself, or set it aside with its reason. The report lists the threads set aside "
                           "before anything probed them. Each brief comes once; pertura_status gives every brief "
                           "again")}}
        except Exception as e:  # naming the waiting threads never breaks the reply it is added to
            _logged(e)
        return out

    def _with_ended(self, out):
        """The reply, with each run that ended since the last call opened in it, once: its result, facts and figure
        as module_result gives them, so the agent reads a result when its run has ended, without asking for it and
        without waiting on it (docs/adr/0032, amendment 1). A run named only was never opened (session 11: four
        first-look runs)."""
        if self.record is None or not isinstance(out, dict):
            return out
        try:
            self.record.sync_runs(self._jobs_root())
        except Exception as e:  # opening the ended runs never breaks the reply it is added to
            _logged(e)
            return out
        runs = self.record.state()["runs"]
        ended = [r for r, x in runs.items() if x.get("job_id") and x["state"] != "running"
                 and r not in self._announced and r not in self._opened and r != out.get("run_id")]
        self._announced.update(ended)
        if not ended:
            return out
        rows, images = [], list(out.get("_images") or [])
        for r in ended:
            row = {"run_id": r, "what": runs[r].get("what"), "state": runs[r]["state"]}
            if str(runs[r].get("what") or "").startswith(CHECKS_RUN):
                row.update(self._verdict_row(runs[r]["what"][len(CHECKS_RUN):]))
            try:
                shown = self.module_result(r)
            except Exception as e:  # a run that cannot be opened is still named
                _logged(e)
                shown = None
            if isinstance(shown, dict) and _the_surveys_own(runs[r]):  # its summary and lines (docs/adr/0045, D5)
                shown.pop("_images", None)
                row["result"] = {k: shown[k] for k in ("status", "summary", "biology") if k in shown}
                row["survey"] = "the survey's run: run_result gives it whole, with its figure"
            elif isinstance(shown, dict):  # what reads the run at once; its tables and diagnostics stay in run_result
                images += shown.pop("_images", None) or []
                row["result"] = {k: shown[k] for k in ANNOUNCED if k in shown}
                row["whole"] = "run_result gives its tables and diagnostics"
            rows.append(row)
        out = {**out, "ended_since_your_last_call": rows}
        return {**out, "_images": images} if images else out

    def _verdict_row(self, fid):
        """A checks job's row, beside its run: its finding, what the checks say of it (checks_verdict), and the
        claims that repeat it, whose checks are these."""
        st = self.record.state()
        f = (st.get("findings") or {}).get(fid)
        verdict = checks_verdict(f) if f else None
        repeats = sorted(g for g, x in (st.get("findings") or {}).items() if g != fid and any(
            str(c.get("reason") or "").startswith(f"the same claim as {fid}:") for c in (x.get("checks") or {}).values()))
        return {"finding_id": fid, **({"verdict": verdict} if verdict else {}),
                **({"repeats": repeats} if repeats else {})}

    def _note_told(self, out):
        """What a reply delivered, first time each, kept with what the replies said (said_in_replies.json): each
        checks verdict, each doubt to answer and each open part of the question. A subagent's replies carry none."""
        now = round(time.time(), 1)
        for row in (out.get("ended_since_your_last_call") or []) + [out]:  # the rows, and a checks job's run_result
            if isinstance(row, dict) and row.get("verdict") and row.get("finding_id"):
                self._told.setdefault("verdicts", {}).setdefault(row["finding_id"], {"verdict": row["verdict"],
                                                                                     "at": now})
        for d in (out.get("doubts_to_answer") or {}).get("doubts") or []:
            self._told.setdefault("doubts", {}).setdefault(d["doubt_id"], now)

    def _module_runs(self, st):
        """{module: its runs on this build, in order}, on every target (record.module_runs)."""
        return rc.module_runs(st)

    def _overview_when_due(self):
        """The screen overview (OVERVIEW), started by code once C.genes and E.similar_targets have finished on this
        build and no overview has run on it: the screen's first figure, which D on 0bbbad0 never drew; and drawn again
        once a module it reads has finished a run the latest overview did not read (on GSE267740 the states ended
        after the first figure, which then had none). Returns its run and why, or None."""
        if self.record is None or not self.project:
            return None
        with _OVERVIEW_LOCK:  # two calls ending together start one overview
            st = self.record.state()
            by = self._module_runs(st)
            overviews = by.get(OVERVIEW) or []
            if not overviews:
                if not all(any(r["state"] == "done" and _default(r) for r in by.get(m, []))
                           for m in OVERVIEW_DUE_AFTER):
                    return None
                why = "the screen's first figure, started by Pertura once " + " and ".join(OVERVIEW_DUE_AFTER) + " ran"
            else:
                if overviews[-1]["state"] != "done":
                    return None
                read = " ".join(map(str, (st["runs"].get(overviews[-1]["run_id"]) or {}).get("uses") or []))
                later = sorted(k for k, rid in self._overview_inputs(st, None).items()
                               if k != "previous" and str(rid) not in read)
                if not later:
                    return None
                why = ("the screen's first figure, drawn again by Pertura with the runs that ended after it ("
                       + ", ".join(later) + "); its distances are kept")
            from . import survey
            due = [[r for r in by.get(m, []) if r["state"] == "done"] for m in OVERVIEW_DUE_AFTER]
            source = survey.SOURCE if all(rs and rs[-1].get("source") == survey.SOURCE for rs in due) else "code"
            rid = self.run_module(OVERVIEW, question_id=OVERVIEW, wait=0, why=why, source=source).get("run_id")
        return {"run_id": rid, "why": why} if rid else None

    def _overview_inputs(self, st, given):
        """The screen overview's inputs: each one not given is the latest finished run of its module on this build with
        the module's default choices, and the overview before it on this build keeps its distances (previous). A run by
        condition or on the targets' own genes stays the agent's (GSE267740: Figure 1 took one experiment's counts from
        a run by condition, in a design whose two experiments are never pooled)."""
        by = self._module_runs(st)

        def done(m):
            rs = [r for r in by.get(m, []) if r["state"] == "done" and _default(r)]
            return rs[-1]["run_id"] if rs else None
        inputs = {k: done(m) for k, m in {**OVERVIEW_FIRST, **OVERVIEW_LATER}.items() if done(m)}
        before = done(OVERVIEW)
        if before:
            inputs["previous"] = before
        return {**inputs, **(given or {})}

    # ---- the survey (docs/adr/0045): the comparisons the design calls for, run by code beside the agent's line
    def _survey_on(self):
        """Whether the survey runs: the user handed over the whole screen (once: a later question of theirs is a
        thread of it), or Pertura runs unattended."""
        return self.unattended or any(q.get("scope") == SCOPES[1] for q in self.record.state().get("asked") or [])

    def _survey_key(self):
        """What the survey's list is worked out for: the project, the screen object's build and the design as
        confirmed."""
        return self.project, self._build_id(), json.dumps(self._confirmed(), sort_keys=True, default=str)

    def _survey_comparisons(self):
        """The survey's comparisons for this project, build and confirmed design (pertura.survey.comparisons),
        worked out once for each."""
        from . import survey
        key = self._survey_key()
        if self._survey_list and self._survey_list[0] == key:
            return self._survey_list[1]
        out = survey.comparisons(self._confirmed(), self._screen_dir())
        self._survey_list = (key, out)
        return out

    def _run_result(self, run_id):
        """A finished module run's status and summary, from its result.json: (status, summary)."""
        try:
            with open(os.path.join(self._jobs_root(), run_id, "result.json"), encoding="utf-8") as fh:
                res = json.load(fh)
        except (OSError, ValueError):
            return None, ""
        return res.get("status"), str(res.get("summary") or "")

    def _survey_rows(self):
        """Each of the survey's comparisons with its state, reason and run (pertura.survey.states); the samples'
        baselines with their summary and table, read from the states run."""
        from . import survey
        rows = survey.states(self._survey_comparisons(), self.record.state(), self._run_result, _module_and_choices)
        waits = self._survey_waits() if any(r["reason"] == "queued" for r in rows) else None
        for r in rows:
            if r["row"] == "baselines" and r["state"] == survey.DESCRIPTIVE and r.get("run_id"):
                r.update(self._survey_baselines_of(r["run_id"]))
            if waits and r["state"] == survey.RUNNING and r["reason"] == "queued":
                r["reason"] = f"waits: {waits}"  # what it waits for, not "queued" (docs/adr/0047, A3)
        return rows

    def _survey_waits(self):
        """What the survey waits for before its first run, in words, or None: the screen object's build, or the
        cells' rule the user confirms after the build (_cells_wait)."""
        if not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
            return "the screen object is not built yet"
        return self._cells_wait()

    def _survey_baselines_of(self, states_run):
        """The samples' baselines read from a states run's cell states (pertura.survey.baselines), the table written
        once into the project's survey folder: {summary, table}, or the reason it was not read."""
        from . import survey
        if self._survey_baselines and self._survey_baselines[0] == (self.project, states_run):
            return self._survey_baselines[1]
        try:
            got = survey.baselines(self._table_file(states_run, "cell_states"), self._screen_dir())
        except (AnalysisError, OSError, ValueError):
            got = None
        if got is None:
            out = {"summary": "the states run's cell states could not be read", "table": None}
        else:
            folder = os.path.join(self.project, "survey")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"baselines-{states_run}.tsv")
            got[0].to_csv(path, sep="\t", index=False)
            out = {"summary": got[1], "table": path}
        self._survey_baselines = ((self.project, states_run), out)
        return out

    def _same_comparison(self, module, given):
        """A run of this build that made the same comparison, running or done, whoever started it, or None: the same
        key as the survey's (survey.key: the module, its choices other than the defaults, its inputs, targets and
        pair, its cell slice), so a run of the module on another slice or pair is another comparison; and a run
        that recorded another cells' rule read other cells (a run logged before runs recorded it is taken as read
        under the rule in force)."""
        from . import survey
        st, want, rule = self.record.state(), survey.key(module, given), self._cells_rule_id()
        for r in reversed(list(st["runs"].values())):
            if (r["state"] not in ("running", "done") or rc.on_previous_screen(st, r)
                    or r.get("cells_rule") not in (None, rule)):
                continue
            m, g = _module_and_choices(r)
            if m is not None and survey.key(m, g) == want:
                return r["run_id"]
        return None

    def _survey_when_due(self):
        """The survey's next comparison (docs/adr/0045, D2), once the cells' rule is confirmed and the first look's
        runs started by code have ended: one run at a time, in the survey's order. A heavy module (ONE_AT_A_TIME, one
        at a time in a workspace) waits while a heavy run of the agent's is in flight, so the agent's next heavy run
        takes the workspace's next turn; a cheaper comparison may go meanwhile, and a heavy one already started runs
        to its end. The first look's modules and the overview are left to the code that starts them, and started here
        only once every other comparison has ended, if they never ran. Returns {run_id, comparison} or None."""
        from . import survey
        from .question_modules import queue_of
        if self.record is None or not self.project or not self._survey_on():
            return None
        with _SURVEY_LOCK:
            try:
                self._confirmed()
            except AnalysisError:
                return None
            if not os.path.exists(os.path.join(self._screen_dir(), "screen.json")) or self._survey_waits():
                return None
            st = self.record.state()
            running = [(r, _module_and_choices(r)[0]) for r in st["runs"].values()
                       if r["state"] == "running" and not rc.on_previous_screen(st, r)]
            if any(r.get("source") == survey.SOURCE or (r.get("source") == "code" and m in FIRST_LOOK)
                   for r, m in running):
                return None
            agents_queues = {_queue(r) for r, m in running if r.get("source") != survey.SOURCE} - {None}
            rows = self._survey_rows()
            queued = [r for r in rows if r["state"] == survey.RUNNING and r["reason"] == "queued"]
            due = [r for r in queued if r["module"] not in FIRST_LOOK + (OVERVIEW,)]
            if not due and all(r["state"] != survey.RUNNING or r in queued for r in rows):  # the rest have ended
                due = queued
            for row in due:
                if queue_of(row["module"], (row["given"] or {}).get("choices")) in agents_queues:
                    continue  # the agent's run of that module takes its queue's next turn
                g = row["given"]
                try:
                    rid = self.run_module(row["module"], g.get("choices"), g.get("inputs"), wait=0,
                                          cells=g.get("cells"),
                                          why=f"the survey: {survey.ROWS[row['row']].lower()}, {row['scope']}",
                                          source=survey.SOURCE).get("run_id")
                except AnalysisError as e:  # refused before a job starts: not comparable for this list, said in its row
                    for c in self._survey_comparisons():
                        if c["id"] == row["id"]:
                            c["not_comparable"] = f"refused: {e}"
                    continue
                return {"run_id": rid, "comparison": row["id"]}
        return None

    def _drive_the_survey(self):
        """The survey goes on when no call comes (the agent waits, or its turn has ended): a thread steps it every
        SURVEY_TICK seconds until the session's input ends (docs/adr/0045, D2). Started once, by a service that
        drives the survey (survey_in_background), when the survey is on."""
        if not self.survey_in_background or self._survey_driver is not None or self.record is None \
                or not self._survey_on():
            return

        def drive():
            while not self._stop_waits.wait(SURVEY_TICK):
                try:
                    self._survey_tick()
                except Exception as e:  # a step that fails is tried again at the next tick
                    _logged(e)
                    pass
        self._survey_driver = threading.Thread(target=drive, name="pertura-survey", daemon=True)
        self._survey_driver.start()

    def _drive_the_checks(self):
        """The checks queue goes on when no call comes: a thread starts the queued checks and opens the doubts of the
        claims they say fail every CHECKS_TICK seconds, until the session's input ends. Started once, by a service
        that drives it (checks_in_background), once a project is open."""
        if not self.checks_in_background or self._checks_driver is not None or self.record is None:
            return

        def drive():
            while not self._stop_waits.wait(CHECKS_TICK):
                if self.record is None:
                    continue
                try:
                    self._start_queued_checks()
                    self._doubts_from_checks()
                except Exception as e:  # a step that fails is tried again at the next tick
                    _logged(e)
        self._checks_driver = threading.Thread(target=drive, name="pertura-checks", daemon=True)
        self._checks_driver.start()

    def _survey_tick(self):
        """One step of the survey with no call: the runs that ended are read, the next comparison is started, and once
        every comparison has ended the report, where one was written, is written again with the last tables."""
        from . import survey
        if self.record is None or not self.project or not self._survey_on():
            return None
        self.record.sync_runs(self._jobs_root())
        started = self._survey_when_due()
        key = self._survey_key()
        if survey.complete(self._survey_rows()) and self._survey_done_for != key:
            self._survey_done_for = key
            if os.path.exists(os.path.join(self.project, "report", "report.md")):
                self._keep_the_report()
        return started

    def _overview_inline(self, run_id, whole=False):
        """The overview as the user's page takes it: its table's rows and columns.json the first time on a build, or
        whole; later, only the columns it filled since the overview before it."""
        import pandas as pd
        folder = os.path.join(self._jobs_root(), run_id)
        t = pd.read_csv(os.path.join(folder, "targets.tsv"), sep="\t")
        with open(os.path.join(folder, "columns.json"), encoding="utf-8") as fh:
            columns = json.load(fh)
        out = {"run_id": run_id, "path": os.path.join(folder, "targets.tsv"), "columns": columns,
               "figure": os.path.join(folder, "figure.png")}
        st = self.record.state()
        before = [r for r in self._module_runs(st).get(OVERVIEW, []) if r["run_id"] != run_id
                  and r["state"] == "done"]
        old = os.path.join(self._jobs_root(), before[-1]["run_id"], "columns.json") if before else None
        if old and os.path.exists(old) and not whole:
            with open(old, encoding="utf-8") as fh:
                was = json.load(fh)
            new = [c for c, v in columns.items() if v.get("state") == "filled" and
                   (was.get(c) or {}).get("state") != "filled"]
            out["filled"] = {c: {str(k): row[c] for k, row in zip(t["target"], _records(t[[c]]))} for c in new}
            return out
        out["rows"] = _records(t)
        return out

    def _open_threads(self, payloads):
        """Open the threads code made (docs/adr/0035, D1); one already open is left as it is."""
        have = self.record.state().get("threads") or {}
        for p in payloads:
            if p["thread_id"] not in have:
                self.record.append("thread_opened", p, rc.TOOL)

    def _turn_end_coverage(self, st=None):
        """The turn-end line's lists (pertura.turn_end), once the screen object is built; None before."""
        from . import design_gate, turn_end
        if self.project is None or not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
            return None
        st = st or self.record.state()
        path = self._design_path()
        waits = sorted(design_gate.status(path)["missing"]) if os.path.exists(path) else []
        cells = getattr(self, "_cells_wait", None)  # the cells' quality rule, asked after the build
        reason = cells() if callable(cells) else None
        if reason:
            waits.append(str(reason))
        return turn_end.coverage(st, waits)

    def _digest_seen(self, seq=False):
        """The record's last event when the digest was last given, kept in the project (it outlives the server's
        process); with seq, kept as that."""
        path = os.path.join(self.project, "digest_seen.json")
        if seq is not False:
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump({"seq": seq}, fh)
            os.replace(path + ".tmp", path)
            return seq
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh).get("seq")
        except (OSError, ValueError):
            return None

    def _known_thread(self, thread_id):
        """A run's thread is one the record holds, so the digest can tell the run under it."""
        threads_ = self.record.state().get("threads") or {}
        if thread_id is not None and thread_id not in threads_:
            raise AnalysisError(f"no thread {thread_id!r}; the threads are {sorted(threads_)[:12]} (a thread opens "
                                "when you name its question in your reply, and the recorder records it)")

    def _design_confirmed(self):
        try:
            self._confirmed()
        except Exception as e:  # not confirmed, or no project: the digest says the design waits
            _logged(e)
            return False
        return True

    def _threads_view(self, st, shown=threads.SHOWN):
        from . import knowledge
        try:
            base = knowledge.load()
        except Exception as e:  # the knowledge never breaks the reply it is added to
            _logged(e)
            base = None
        return threads.open_threads(st, base, shown)

    def write_plan(self, items, why=None):
        """The agent's plan (docs/build_goal_subtraction_after_session8.md, S7): its items numbered, an item given
        again with its id kept, a new one given the next id; recorded as a version, with why when it changes."""
        self._need()
        st = self.record.state()
        before = st.get("plan")
        known = set(before["items"]) | set(before["left"]) if before else set()
        if not isinstance(items, list) or not items:
            raise AnalysisError("items: a list of {does, why, how}, and cards, from the map and the cards that fit")
        ids = [int(i) for i in known if str(i).isdigit()]
        nxt = max(ids, default=0) + 1
        out = []
        for i in items:
            if not isinstance(i, dict):
                raise AnalysisError("each item is {does, why, how}, and cards, status done with done_by")
            bad = sorted(set(i) - set(rc.PLAN_ITEM_FIELDS))
            if bad:
                raise AnalysisError(f"an item holds {', '.join(rc.PLAN_ITEM_FIELDS)}, not {', '.join(bad)}")
            given = str(i["id"]) if i.get("id") not in (None, "") else None
            if given is not None and given not in known:
                raise AnalysisError(f"no item {given} in your plan (its items: {', '.join(sorted(known)) or 'none'}); "
                                    "give a new item without an id")
            if given is None:
                given, nxt = str(nxt), nxt + 1
            out.append({**i, "id": given})
        self.record.append("plan_written", {"items": out, "why": str(why or "").strip() or None}, rc.AGENT)
        st = self.record.state()
        return {"plan": rc.plan_items(st), **self._plan_moment()}

    def _plan_moment(self):
        """What the agent sees where it plans: the first look's candidate structures and what to do with the maps
        (docs/adr/0039, D3), the routes published screens took that this design allows, the data the screen object
        holds that no run has read. The research lead's due item is the state list's (docs/adr/0050, D2)."""
        st = self.record.state()
        return {"candidate_structures": self._candidate_structures(st),
                "the_maps": ("read each map of the first look, its figure and its table; name each thing you see "
                             "worth a probe in your reply, with its numbers: it becomes a thread; follow first what "
                             "answers the question, with your reason, and hand others to workers"),
                "survey_first": ("the report opens with what the first look shows: say a finding on each section's "
                                 "run (the states named from their markers, what stands out, or that a section "
                                 "shows nothing) before you propose a result; then go deep on what answers the "
                                 "question"),
                "kinds_of_conclusion": self._kinds_of_conclusion(st),
                "breadth": ("each kind of conclusion that applies and no thread reads can go to a worker: open_thread "
                            "with reads (the kind) and on_line false, whose reply gives the worker's brief; workers run "
                            "in parallel, in the background. Choose your main line from the maps and these kinds "
                            "while they work; their findings come back to you"),
                **self._chains_that_fit(), **self._unread_data()}

    def _candidate_structures(self, st, complexes=True, agents_only=False):
        """The first look's candidate structures (pertura.candidates), each from the latest finished run of its module
        on this build (every finished run, for the contrast between conditions: one per pair); a module that has not
        run is named, and a table that cannot be read is said, never raised. agents_only: from the agent's runs and the
        survey's runs it took, as the first look's sections read them (nothing waits for the survey, docs/adr/0045)."""
        import pandas as pd
        from . import candidates
        by = self._module_runs(st)
        if agents_only:
            by = {m: [r for r in rs if not _the_surveys_own(r)] for m, rs in by.items()}

        def done(module):
            return [r["run_id"] for r in by.get(module, []) if r["state"] == "done"]

        def table(run_id, name):
            try:
                return pd.read_csv(self._table_file(run_id, name), sep="\t")
            except (AnalysisError, OSError, ValueError):
                return None
        def said(run_id):  # the run's result.json: its status, summary and diagnostics
            try:
                with open(os.path.join(self._jobs_root(), run_id, "result.json"), encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return None
        out, not_run = {}, []
        similar, overview = done("E.similar_targets"), done(OVERVIEW)
        if similar:
            run = said(similar[-1]) or {}
            lines = run.get("diagnostics") or {}  # the control-guide pairs' 95th and 5th percentiles
            alike = candidates.sets_alike(table(similar[-1], "result"), reference=lines.get("control_pairs_95th_percentile"),
                                          low=lines.get("control_pairs_5th_percentile"))
            if not alike["count"] and run:  # too few cells, no control pairs to read against, or none alike
                alike["why_none"] = f"the run's reading ({run.get('status')}): {run.get('summary')}"
            out["sets_of_targets_alike"] = {"run_id": similar[-1], **alike,
                                            **(self._known_complexes(alike["sets"]) if complexes else {})}
        else:
            not_run.append("E.similar_targets")
        if overview:
            out["targets_far_from_the_control_cells"] = {"run_id": overview[-1],
                                                         **candidates.far_from_controls(table(overview[-1], "targets"))}
        else:
            not_run.append(OVERVIEW)
        states = done("C.states_and_composition")
        if states:
            out["targets_shifting_state_shares"] = {"run_id": states[-1],
                                                    **candidates.states_shifting(table(states[-1], "result"))}
        cutting = done("D.cutting_response")
        if cutting:
            out["the_two_kinds_of_control"] = {"run_id": cutting[-1], **candidates.controls_differ(said(cutting[-1]))}
        contrasts = done("E.condition_x_perturbation")
        if contrasts:
            out["targets_changing_between_conditions"] = [
                {"run_id": rid, **candidates.changing_between_conditions(table(rid, "result"))} for rid in contrasts]
        else:
            from . import design_gate
            try:
                names = design_gate.condition_names(
                    (design_gate.node(self._confirmed(), "experiment.conditions") or {}).get("value"))
            except Exception as e:  # the list is information: a design that cannot be read leaves this source unsaid
                _logged(e)
                names = []
            if len(names) > 1:
                not_run.append("E.condition_x_perturbation")
        if not_run:  # by the tool calls that start them (docs/adr/0044), where a tool does
            from .paradigms import called
            out["not_run_yet"] = [m if called(f"module:{m}") == m else f"{called(f'module:{m}')} ({m})"
                                  for m in not_run]
        return out

    def _first_look_sections(self, st):
        """The first look's sections (pertura.candidates.SECTIONS), read before a result is proposed (survey first,
        then depth): every view of the first look that ran, once (docs/adr/0041, step 3: a view whose list counts
        nothing is still read, "nothing stands out" a reading; GSE250558's time contrast listed nothing and was never
        read). Each: {section, condition, run_id (its latest run), run_ids, entries (what its list counts, beside it),
        png, finding_ids}, the findings that read any of its runs (resting on one, or on a run that read one)."""
        from . import candidates
        from .page_view import _png
        # the survey's own runs open no section, so nothing waits for the survey (docs/adr/0045); one the agent took
        # is the agent's. The page's copy is written at each change
        listed = self._candidate_structures(st, complexes=False, agents_only=True)
        states = [r["run_id"] for r in self._module_runs(st).get("C.states_and_composition", [])
                  if r["state"] == "done" and not _the_surveys_own(r)]
        sections = []
        for key, (_, kind) in candidates.SECTIONS.items():
            given = ([{"run_id": states[-1], "count": None}] if states else []) if kind is None else listed.get(kind)
            given = [x for x in (given if isinstance(given, list) else [given]) if isinstance(x, dict) and x.get("run_id")]
            if given:
                last = given[-1]
                own = candidates.SECTION_FIGURES.get(key)  # a section drawn by another of its run's figures
                sections.append({"section": key, "condition": None, "run_id": last["run_id"],
                                 "run_ids": [x["run_id"] for x in given], "entries": last.get("count"),
                                 "png": (own and _png(st, f"{last['run_id']}:{own}")) or _png(st, last["run_id"])})
        reading = {}
        for fid, f in (st.get("findings") or {}).items():
            runs = set(f.get("run_ids") or [])
            runs |= {str(u).split(":")[0] for r in list(runs)
                     for u in ((st.get("runs") or {}).get(r) or {}).get("uses") or []}
            for r in runs:
                reading.setdefault(r, []).append(fid)
        for s in sections:
            s["finding_ids"] = list(dict.fromkeys(f for r in s["run_ids"] for f in reading.get(r, [])))
        return sections

    def _first_look_unread(self, st):
        """The first look's sections no finding reads yet (_first_look_sections): the lead's coverage lists them, and
        the report's first look says each is not read yet; nothing waits for them (docs/adr/0042, amendment 1)."""
        return [s for s in self._first_look_sections(st) if not s["finding_ids"]]

    def _known_complexes(self, sets):
        """Beside each set of targets alike, the known complexes that hold two or more of its targets (MSigDB's GO
        cellular component sets, docs/adr/0039 D6), with their source; where they cannot be read, why."""
        from . import candidates
        from . import gene_sets as gs
        if not sets:
            return {}
        try:
            organism = self._organism()
            known = gs.msigdb("go_cc", organism)
        except (AnalysisError, OSError, ValueError) as e:
            return {"known_complexes": f"not read: {e}"}
        for s in sets:
            s["known_complexes"] = candidates.known_complexes(s["targets"], known)
        return {"known_complexes_from": f"MSigDB GO cellular component sets (release 2025.1, {organism}) of at "
                                        f"most {candidates.COMPLEX_MAX} genes, holding two or more of a set's "
                                        "targets: prior knowledge, not a test"}

    FIRM = ("agree", "agrees", "beyond the control groups")  # a check that ran and holds the finding's way

    @staticmethod
    def _standing(f):
        """A finding's standing from its checks, never its words: confirmed (the lead test and the control-guide line
        both ran and hold, and every other check that ran holds), checks_disagree (finding_checks.disagrees), or
        unsettled (checks pending, partly, or within the control groups' range); and the checks that did not run."""
        from .finding_checks import disagrees
        checks = f.get("checks") or {}
        ran = {k: c.get("outcome") for k, c in checks.items() if c.get("outcome") != "not run"}
        not_run = sorted(k for k, c in checks.items() if c.get("outcome") == "not run")
        if disagrees(checks.get("lead"), [c for k, c in checks.items() if k != "lead"]):
            return "checks_disagree", not_run
        if ran.get("lead") == "agrees" and ran.get("calibration") == "beyond the control groups" and \
                all(o in AnalysisService.FIRM for o in ran.values()):
            return "confirmed", not_run
        return "unsettled", not_run

    def _coverage_beside_the_record(self, st):
        """What the record leaves open, beside it (read_record what coverage): the record as it stands, the doubts
        answered, the threads ended and the results, the routes published screens took, what papers did after results
        like these, and the results as a chain. It was the research lead's view (docs/adr/0036), which also read what
        changed since its last review, the advisor's standings and the figures' set review; they left with those roles
        on 2026-11-20."""
        after = self._what_papers_did_after(st)
        cov = {**self._chains_that_fit(), "routes_by_kind_of_stopped_line": candidates.BLOCK_ROUTES,
               "the_record_as_it_stands": self._record_as_it_stands(st),
               "doubts_answered": [
                   {"doubt_id": did, "finding_id": d.get("finding_id"), "outcome": d["answer"]["outcome"],
                    "limit": d["answer"].get("limit"), "reason": d["answer"]["reason"],
                    "would_change": d.get("would_change"), "tested": rc.doubt_tested(st, did)}
                   for did, d in st["doubts"].items() if d.get("answer")],
               "threads_ended": [{"thread_id": t, "status": th["status"], "reason": th.get("reason"),
                                  "findings": th.get("findings") or []}
                                 for t, th in (st.get("threads") or {}).items() if th.get("status") in rc.THREAD_ENDS],
               "results": [{"result_id": i, "title": r.get("title"), "version": r.get("version"),
                            "decision": r.get("decision")} for i, r in (st.get("results") or {}).items()],
               **({"what_papers_did_after_results_like_these": after} if after else {}),
               "results_as_a_chain": rc.result_chain_text(st)}
        empty = (None, "", [], {})  # a key with nothing in it is left out (the fresh review, #11)
        return {k: v for k, v in cov.items()
                if v not in empty and not (isinstance(v, dict) and all(x in empty for x in v.values()))}

    def _record_as_it_stands(self, st):
        """The record as it stands (docs/build/the_record_as_it_stands_2026-11-16.md), the one view the main agent
        tells the user: the findings their checks confirm, leave unsettled or
        contradict, each with its key; the descriptions no check reads; the open questions in one list, each with
        where it came from; and what findings share (a target, a gene that is itself a target, a set of targets alike
        and the targets opposite it). Read from the record's fields and the screen's labels, never from sentences."""
        claims = {"confirmed": [], "checks_disagree": [], "unsettled": []}
        descriptions, by_target, features = [], collections.defaultdict(list), {}
        for fid, f in st["findings"].items():
            targets = list(f.get("targets") or ([f["target"]] if f.get("target") else []))
            for t in targets:
                by_target[t].append(fid)
            if not f.get("direction") or f.get("kind") in (None, "description", "custom"):
                descriptions.append({"finding_id": fid, "statement": str(f.get("statement") or "")[:160]})
                continue
            standing, not_run = self._standing(f)
            claims[standing].append({"finding_id": fid, "targets": targets, "feature": f.get("feature"),
                                     "direction": f.get("direction"), "kind": f.get("kind"),
                                     **({"checks_not_run": not_run} if not_run else {})})
            if f.get("feature"):
                features.setdefault(str(f["feature"]), []).append(fid)
        open_items = {
            "new_questions_behind_each_result": self._new_questions_behind(st),
            "next_questions_ended_without_a_run": rc.next_questions_ended_without_a_run(st),
            "observations_not_yet_explained": rc.observations_not_yet_explained(st),
            "kinds_of_conclusion_no_thread_reads": [k for k in self._kinds_of_conclusion(st)
                                                    if k.get("applies") and not k.get("thread_ids")],
            "first_look_sections_no_finding_reads": self._first_look_unread(st),
            "doubts_no_run_has_answered": [did for did, d in st["doubts"].items() if not rc.doubt_tested(st, did)
                                           and (d.get("answer") or {}).get("outcome") != rc.LIMIT],
            "places_a_line_stopped": rc.blocks_without_an_answer(st),
            "known_not_looked_up": rc.known_not_looked_up(st),
            "first_look_runs_not_run_yet": self._candidate_structures(st, complexes=False).get("not_run_yet") or [],
            **self._unread_data()}
        screen_targets = self._screen_labels()
        shared = {
            "targets_in_several_findings": {t: fids for t, fids in sorted(by_target.items(), key=lambda kv: -len(kv[1]))
                                            if len(fids) > 1},
            "genes_that_are_targets_too": {g: {"findings_on_it": fids, "its_own_findings": by_target.get(g, [])}
                                           for g, fids in features.items() if g in screen_targets},
            "sets_of_targets_alike": self._structures(st)}
        empty = (None, "", [], {})

        def kept(d):  # a key with nothing in it is left out, as in the coverage
            return {k: v for k, v in d.items()
                    if v not in empty and not (isinstance(v, dict) and all(x in empty for x in v.values()))}
        return kept({"findings_their_checks_confirm": claims["confirmed"],
                     "findings_their_checks_contradict": claims["checks_disagree"],
                     "findings_not_settled_yet": claims["unsettled"],
                     "descriptions_no_check_reads": descriptions,
                     "open": kept(open_items), "shared": kept(shared)})

    def _screen_labels(self):
        """The screen's target labels (screen.json), or an empty set before the build."""
        try:
            with open(os.path.join(self._screen_dir(), "screen.json"), encoding="utf-8") as fh:
                labels = json.load(fh).get("labels") or {}
        except (OSError, ValueError, AnalysisError):
            return set()
        from .question_modules.data import NOT_TARGETS
        return {t for t, n in labels.items() if n and t not in NOT_TARGETS}

    def _structures(self, st):
        """The screen's structures the survey lists, on which a complete waits (docs/adr/0048): each set of targets
        alike as the similarity lines name it (candidates.apart), with the targets opposite it and the threads that
        read it (open_thread with structure). None unless the whole screen is analysed, where the survey runs."""
        if not self._survey_on():
            return []
        alike = self._candidate_structures(st, complexes=False).get("sets_of_targets_alike") or {}
        threads = st.get("threads") or {}
        out = []
        for x in candidates.apart(alike.get("sets") or []):
            sid = f"alike:{x['targets'][0]}+{len(x['targets']) - 1}"
            out.append({"structure": sid, "targets": x["targets"],
                        "opposite": [o["target"] for o in x.get("opposite") or []], "run_id": alike.get("run_id"),
                        "thread_ids": [t for t, th in threads.items() if th.get("structure") == sid]})
        return out

    def _ended(self, st, tid):
        """Whether a thread ended with something read for it (record.ended_with_something_read). On GSE267740
        (d3a7dcc) two threads ended citing only other threads' findings, with nothing read for them, and passed."""
        return rc.ended_with_something_read(st, tid)

    def _kinds_of_conclusion(self, st):
        """The kinds of conclusion this design can reach (pertura.candidates.KINDS; 2026-11-07, P17), each with whether
        the design allows it and why, how a worker reads it across the screen, and the threads reading it (open_thread
        with reads) with the findings they hold. Facts for the main agent and the lead, who start from them."""
        reach, threads = self._reach_of_the_design(st), st.get("threads") or {}
        try:  # the survey's runs read the kinds too: read before a worker runs them again
            rows = self._survey_rows() if self._survey_on() else []
        except (AnalysisError, OSError, ValueError):
            rows = []
        out = []
        for key, (what, how) in candidates.KINDS.items():
            applies, why = reach.get(key, (None, "known once the design is confirmed and the first look has run"))
            mine = [t for t, th in threads.items() if th.get("reads") == key]
            ran = [r["run_id"] for r in rows if r.get("run_id") and (r["row"] in candidates.KIND_ROWS.get(key, ())
                                                                     or r["id"] in candidates.KIND_ROWS.get(key, ()))]
            out.append({"kind": key, "reads": what, "applies": applies, "why": why, "how": how, "thread_ids": mine,
                        "finding_ids": sorted({f for t in mine for f in threads[t].get("findings") or []}),
                        **({"survey_run_ids": ran} if ran else {})})
        return out

    def _reach_of_the_design(self, st):
        """Whether the design allows each kind of conclusion, and why: its organism (programmes), its conditions, the
        targets whose own gene the overview measured, and the screen object's antibody features."""
        from . import design_gate
        try:
            design = self._confirmed()
        except Exception as e:  # a list of facts: a design not confirmed, or not readable, leaves the kinds unsaid
            _logged(e)
            return {}
        organism = (design_gate.node(design, "experiment.organism") or {}).get("value")
        try:
            conditions = (design_gate.node(design, "experiment.conditions") or {}).get("value")
            names = design_gate.condition_names(conditions)
            apart = not all(f["compared"] for f in design_gate.condition_factors(conditions))
        except Exception as e:  # a list of facts: a design that cannot be read leaves this kind unsaid
            _logged(e)
            names, apart = [], False
        try:
            with open(os.path.join(self._screen_dir(), "screen.json"), encoding="utf-8") as fh:
                antibodies = json.load(fh).get("antibody_features") or []
        except (OSError, ValueError, AttributeError, TypeError):
            antibodies = []
        sets = organism in ("human", "mouse")
        reach = {"states": (True, "every cell of the screen falls in a state"),
                 "programmes": (sets, f"organism {organism}: MSigDB, PROGENy and CollecTRI hold its sets" if sets
                                else f"organism {organism or 'not confirmed'}: no gene-set base for it"),
                 "conditions_apart": (len(names) > 1 and not apart,
                                      f"{len(names)} conditions, read apart and not compared (the design)" if apart
                                      else f"{len(names)} conditions: {', '.join(map(str, names))}"
                                      if len(names) > 1 else "one condition"),
                 "second_readout": (bool(antibodies), f"{len(antibodies)} antibody features in the screen object"
                                    if antibodies else "RNA alone")}
        overview = [r["run_id"] for r in self._module_runs(st).get(OVERVIEW, []) if r["state"] == "done"]
        if overview:
            try:
                with open(os.path.join(self._jobs_root(), overview[-1], "result.json"), encoding="utf-8") as fh:
                    n = int((json.load(fh).get("diagnostics") or {}).get("network_targets") or 0)
            except (OSError, ValueError, TypeError):
                n = 0
            reach["targets_on_each_other"] = (n >= 3, f"{n} targets' own genes measured in the cells, beside `genes`'"
                                              " test of every gene" if n else "fewer than 3 targets' own genes "
                                              "measured, or no `genes` run beside the overview")
        return reach

    def _new_questions_behind(self, st):
        """Each proposed result's chain of new questions (pertura.record.thread_chains; P15), with each thread's
        sentence, the deepest first."""
        chains, threads = rc.thread_chains(st), st.get("threads") or {}
        out = {}
        for r in rc.results_shown(st):
            n, chain = rc.new_questions_behind(st, r["finding_ids"], chains)
            out[r["result_id"]] = {"new_questions": n,
                                   "chain": [{"thread_id": t, "about": threads[t].get("about")} for t in chain]}
        return out

    def _notebook_cells(self, st, recent=40, tail=300):
        """The main notebook's cells in order (docs/adr/0039, D1): each one's run, what it did, its state and the
        figures it saved; the latest `recent` with the end of their output. The workers' notebooks are their own."""
        cells = [(rid, r) for rid, r in st["runs"].items()
                 if str(rid).startswith("run") and not r.get("worker") and not r.get("job_id")]
        figures = {}
        for fid, f in (st.get("figures") or {}).items():
            figures.setdefault(f.get("run_id"), []).append(fid)
        out = []
        for k, (rid, r) in enumerate(cells):
            cell = {"run_id": rid, "what": r.get("what"), "state": r.get("state"),
                    **({"figures": figures[rid]} if rid in figures else {}),
                    **({"thread_id": r["thread_id"]} if r.get("thread_id") else {})}
            if k >= len(cells) - recent:
                text = str(r.get("output") or "")
                cell["output_end"] = text if len(text) <= tail else "..." + text[-tail:]
            out.append(cell)
        return {"cells": out, "count": len(out)}

    def _unread_data(self):
        """The measurements beside the RNA that no run has read (session 11 never read its 141 antibody counts, CD25
        among them, the readout its targets were chosen for): from the screen object's summary and the runs' code."""
        try:
            with open(os.path.join(self._screen_dir(), "screen.json"), encoding="utf-8") as fh:
                antibodies = json.load(fh).get("antibody_features") or []
        except (OSError, ValueError, AttributeError):
            return {}
        if not antibodies or any("antibody" in str(r.get("code") or "")
                                 for r in self.record.state()["runs"].values()):
            return {}
        return {"data_no_run_has_read": [f"{len(antibodies)} antibody features (surface proteins, hashtags among "
                                         f"them) in obsm['antibody_counts'], such as {', '.join(antibodies[:6])}"]}

    def _chains_that_fit(self):
        """The routes of published screens this design allows (pertura.paradigms.chains), once the design is
        confirmed and the screen built: the cards' steps in view where the agent plans (session 11 never read card
        P2, whose score from the design's contrast gives its paper's main findings)."""
        from pertura import paradigms
        try:
            found = paradigms.chains(self._cards(), self._design_facts())
        except (AnalysisError, OSError, KeyError, ValueError):  # no confirmed design or screen yet
            return {}
        return {"published_chains_this_design_allows": found} if found else {}

    def _knowledge_design(self):
        """The confirmed modality and system, and the measured readout, in the paper reader's vocabulary."""
        if self.design is None:
            return {}
        try:
            confirmed = self._confirmed()
            modes = rc.modalities(confirmed)
        except (AnalysisError, OSError, ValueError, TypeError):
            return {}
        modality = {frozenset({"crispr_ko"}): "CRISPR knockout", frozenset({"crispri"}): "CRISPRi",
                    frozenset({"crispra"}): "CRISPRa",
                    frozenset({"crispri", "crispra"}): "CRISPRi and CRISPRa"}.get(frozenset(modes))
        system = {"in_vivo": "in vivo", "primary_in_vitro": "primary cells",
                  "cell_line": "cell line"}.get(rc._v(confirmed, "experiment.context"))
        out = {k: v for k, v in (("modality", modality), ("system", system)) if v}
        if self.project is not None:
            try:
                with open(os.path.join(self._screen_dir(), "screen.json"), encoding="utf-8") as fh:
                    screen = json.load(fh)
                out["readout"] = "transcriptome and protein" if screen.get("antibody_features") else "transcriptome"
            except (OSError, ValueError):
                pass
        return out

    def _paper_knowledge(self, query, kind):
        """A lookup's examples, without its counts of papers: a count of papers says nothing to an agent deciding its
        next step. A lookup with no example keeps the line that says why."""
        from . import knowledge_store
        try:
            out = knowledge_store.query(query, kind, design=self._knowledge_design(),
                                        excluded=self.cfg.get("literature_excluded"))
        except ValueError as e:
            raise AnalysisError(str(e)) from e
        return {k: v for k, v in out.items() if k not in ("practice", "counts", "studies")} if out["examples"] else out

    def _what_papers_did_after(self, st):
        """For the research lead at each review (each launches a fresh lead): for each result not dropped, by the
        kinds of analysis behind it, what papers did after a result like it (knowledge_store.after_results), in their
        words; before any result, after the main line's findings. On 01f3de3 the lead got each close paper's steps
        as kinds of analysis, 8.7k characters at each review, and no lead named a paper."""
        from . import knowledge_store
        on = {i: [i] for i, r in (st.get("results") or {}).items() if r.get("decision") != "dropped"} or {
            "the main line": [f for th in (st.get("threads") or {}).values() if th.get("status") == "followed"
                              for f in th.get("findings") or []]}
        out, shown = {}, set()
        try:
            for name, ids in on.items():
                lines = [x for x in knowledge_store.after_results(self._kinds_named(st, ids), self._knowledge_design(),
                                                                  excluded=self.cfg.get("literature_excluded"))
                         if x not in shown]  # a line shown under an earlier result is not shown again
                shown.update(lines)
                if lines:
                    out[name] = lines
        except Exception as e:  # an unreadable store or a bad row costs the lines, not the lead's review
            _logged(e)
            return {}
        return out

    def _kinds_named(self, st, ids):
        """The kinds of analysis behind what ids name: findings; a result's findings; a doubt's finding; a thread's
        findings and what raised it, tested or forked; a run's module."""
        findings, kinds, seen, todo = [], [], set(), [str(i) for i in ids if i]
        while todo:
            i = todo.pop()
            if i in seen:
                continue
            seen.add(i)
            if i in st["findings"]:
                findings.append(st["findings"][i])
            elif i in (st.get("results") or {}):
                todo += st["results"][i].get("finding_ids") or []
            elif i in st["doubts"]:
                todo.append(st["doubts"][i].get("finding_id"))
            elif i in (st.get("threads") or {}):
                th = st["threads"][i]
                todo += [fid for fid, f in st["findings"].items() if f.get("thread_id") == i]
                todo += [th.get(k) for k in ("raised_by", "doubt_id", "fork_of")]
            elif i in st["runs"]:
                module, given = _module_and_choices(st["runs"][i])
                kinds.append(self._knowledge_kind(module or st["runs"][i].get("question_id"), given.get("choices")))
            todo = [str(t) for t in todo if t]
        return [k for k in dict.fromkeys(kinds + self._kinds_of(st, findings)) if k]

    def _ranked_steps(self, st, figure_run, finding_ids, steps):
        """A result's next steps as the user sees them (docs/adr/0050, D5): in the agent's order, each with how many of
        the studies read went on to its move after a result of the result's main kind (n of m, m always stated) and up
        to three of those steps as references (knowledge_store.went_on_to). An option whose move needs data the deposit
        lacks is marked so and is never the recommended one: the recommended option is the agent's, else the first the
        data can answer, else none. Returns (the steps, a note where the recommendation moved)."""
        from .knowledge_store import NEEDS_NEW_DATA
        new_data = [s["move"] in NEEDS_NEW_DATA for s in steps]
        asked = next((i for i, s in enumerate(steps) if s.get("recommended")), 0)
        top = asked if not new_data[asked] else next((i for i, n in enumerate(new_data) if not n), None)
        kinds = self._main_kind(st, figure_run, finding_ids)
        out = []
        for i, s in enumerate(steps):
            step = {"rank": i + 1, "question": s["question"], "move": s["move"], "why": s["why"],
                    "recommended": i == top, **({"needs_new_data": True} if new_data[i] else {})}
            went = self._went_on_to(kinds, s["move"])
            if went:
                step.update(counts={"n": went["n"], "m": went["m"],
                                    "situation": "after a result of " + " or ".join(went["after"])},
                            references=went["references"])
            out.append(step)
        note = None if top == asked else (
            f"option {asked + 1} needs new data ({steps[asked]['move']}), so it is not the recommended one: "
            + (f"option {top + 1} is, the first the data can answer" if top is not None else
               "no option is, since none can be answered from the data"))
        return out, note

    def _studies_went_on_to(self, st, result_ids):
        """For each result owed its next steps: the moves the studies read made after a result of its main kind, the
        most studies first, so the agent's choices can be set against the field's."""
        results = st.get("results") or {}
        went = {rid: self._went_on_to(self._main_kind(st, results[rid].get("figure_run"),
                                                      results[rid].get("finding_ids") or []))
                for rid in result_ids}
        return {rid: {**w, "moves": w["moves"][:5]} for rid, w in went.items() if w}

    def _main_kind(self, st, figure_run, finding_ids):
        """The kind of analysis a result rests on, for its next steps' counts: the kind of the run its figure comes
        from (the run that argues it), else its findings' most common kind; kinds that check the screen (guides,
        reproducibility) are left out. Pooling every kind behind a result counted 67 of the 78 studies read (the
        interactive trial of 2026-11-16)."""
        from .knowledge_store import TECHNICAL
        drawn = [k for k in self._kinds_named(st, [figure_run]) if k not in TECHNICAL] if figure_run else []
        if drawn:
            return drawn[:1]
        found = [k for k in self._kinds_of(st, [st["findings"][f] for f in finding_ids if f in st["findings"]])
                 if k and k not in TECHNICAL]
        return [collections.Counter(found).most_common(1)[0][0]] if found else []

    def _went_on_to(self, kinds, move=None):
        """knowledge_store.went_on_to for these kinds, with the screen's design and the studies kept out; None where no
        kind is given or the store cannot be read (it costs the counts, not the reply)."""
        from . import knowledge_store
        try:
            return kinds and knowledge_store.went_on_to(kinds, move, design=self._knowledge_design(),
                                                        excluded=self.cfg.get("literature_excluded")) or None
        except Exception as e:
            _logged(e)
            return None

    def _papers_for_the_worker(self, worker):
        """The paper store in a worker's first reply, for the decision it makes there: a thread's worker gets how
        papers did the kinds its thread comes from (the research lead's planned figures' drawing workers, who got the
        charts papers drew, left with the lead on 2026-11-20). On GSE267740 (16ea437) both reached others, after the
        decision."""
        if not worker or self.record is None:
            return {}
        from . import knowledge_store
        st, design, excluded = self.record.state(), self._knowledge_design(), self.cfg.get("literature_excluded")
        try:
            thread = max((t for t in st.get("threads") or {} if worker == t or worker.startswith(f"{t}-")),
                         key=len, default=None)
            found = {k: knowledge_store.checks_reported(k, design, excluded=excluded)
                     for k in (self._kinds_named(st, [thread]) if thread else [])}
        except Exception as e:  # an unreadable store or a bad row costs the lines, not the worker's cell
            _logged(e)
            return {}
        found = {k: v for k, v in found.items() if v}
        return {"how_papers_did_it": found} if found else {}

    def _checks_papers_reported(self, st, ids):
        """For the skeptic's findings: the checks papers reported for results of each finding's kinds, one line a paper
        (knowledge_store.checks_reported), so a doubt's test can name the field's and its paper."""
        from . import knowledge_store
        findings = [f for fid, f in st["findings"].items() if not ids or fid in ids]
        try:
            lines = {k: knowledge_store.checks_reported(k, self._knowledge_design(),
                                                        excluded=self.cfg.get("literature_excluded"))
                     for k in dict.fromkeys(k for k in self._kinds_of(st, findings) if k)}
        except Exception as e:  # an unreadable store or a bad row costs the lines, not the skeptic's read
            _logged(e)
            return {}
        lines = {k: v for k, v in lines.items() if v}
        return {"checks_papers_reported": lines} if lines else {}

    @staticmethod
    def _knowledge_kind(module, choices=None):
        try:
            kind = kinds.kind_of(module)[0]
        except ValueError:
            return None
        if module == "C.programs" and (choices or {}).get("gene_sets") == "cnmf":
            return "programs"
        return kind

    def _kinds_of(self, st, findings):
        """The kinds of analysis of findings: their source runs' modules, else their question's or their declared kind."""
        wanted = []
        for f in findings:
            question = f.get("question_id")
            sources = []
            for rid in f.get("run_ids") or []:
                run = st.get("runs", {}).get(rid, {})
                module, given = _module_and_choices(run)
                module = module or run.get("question_id")
                if not question or module == question:
                    sources.append(self._knowledge_kind(module, given.get("choices")))
            sources = [k for k in sources if k]
            wanted.extend(sources or [self._knowledge_kind(question) or FINDING_KINDS.get(f.get("kind"))])
        return wanted

    def _figure_knowledge(self, st, figure_ids):
        """The figures' declared findings and source analyses, without classifying their free text."""
        selected = set(figure_ids)
        fids = {f for r in st.get("results", {}).values() if r.get("figure") in selected
                for f in r.get("finding_ids") or []}
        wanted = self._kinds_of(st, [f for fid, f in st.get("findings", {}).items()
                                     if fid in fids or f.get("figure") in selected])
        if not any(wanted):
            for fid in selected:
                rid = st.get("figures", {}).get(fid, {}).get("run_id")
                run = st.get("runs", {}).get(rid, {})
                module, given = _module_and_choices(run)
                wanted.append(self._knowledge_kind(module or run.get("question_id"), given.get("choices")))
        wanted = list(dict.fromkeys(k for k in wanted if k))
        if not wanted:
            return {}
        papers = self._paper_knowledge("figure", wanted)
        return papers if papers["examples"] else {}

    def _published_next(self, module, choices=None):
        """The hand-written follow-up moves beside a detected result. The papers' steps go to the research lead, who
        chooses the next threads (what_papers_did_after_results_like_these): on GSE267740 (16ea437) they came here
        40 times, to workers and to the main agent collecting runs, and were never used."""
        from pertura import moves, paradigms
        try:
            fit = paradigms.fitting(self._cards(), self._design_facts())
        except (AnalysisError, OSError, KeyError, ValueError, TypeError):  # no confirmed design or screen yet
            fit = set()
        return moves.after(str(module or ""), fit)

    def _cards(self):
        from pertura import paradigms
        from pertura import question_modules as qm
        try:
            return paradigms.load(self.cfg.get("cards_dir"), modules=set(qm.REGISTRY))
        except paradigms.CardError as e:
            raise AnalysisError(f"a paradigm card in the skill does not load ({e}); tell the user")

    def _design_facts(self):
        """The facts a card's requires are measured on (pertura.paradigms.measured): the modes, the control guides,
        the pairs, the time points and the conditions."""
        import pandas as pd
        from . import design_gate
        design = self._confirmed()
        sd = self._screen_dir()
        g = pd.read_csv(os.path.join(sd, "guides.tsv"), sep="\t")
        controls = g[~g["kind"].isin(["targeting", "unknown"])]["guide"].nunique()
        return {"modes": sorted(rc.modalities(design)), "n_control_guides": int(controls),
                "pairs": rc.screen_facts(sd)["pairs"],
                "time_course": bool(rc._v(design, "experiment.time_points") or
                                    rc._v(design, "experiment.differentiation")),
                "n_conditions": len(design_gate.condition_names(rc._v(design, "experiment.conditions")))}

    def _plan_step(self, step):
        """A run's step, as the item of the plan it serves: (the step, None), or (None, why it is not kept) where
        there is no plan, the plan has no such item, or it left the item. The run goes ahead either way; its reply
        says why."""
        if step in (None, ""):
            return None, None
        plan = self.record.state().get("plan")
        step = str(step)
        if not plan:
            return None, f"step {step} is not kept: there is no plan"
        if step in plan["left"]:
            return None, (f"step {step} is not kept: the plan left it ({plan['left'][step].get('left_why')}); "
                          "the plan is no longer written")
        if step not in plan["items"]:
            return None, f"step {step} is not kept: the plan's items are {', '.join(plan['order'])}"
        return step, None

    def open_thread(self, about, raised_by, thread_id=None, matters=None, tells_apart=None, evidence=None,
                    user_words=None, doubt_id=None, joins=None,
                    test=None, prior=None, observation=False, on_line=None, step=None, reads=None, scope=None,
                    structure=None, next_of=None, fork_of=None, choice=None, opened_by=None, proposed_as=None,
                    tries=None):
        """A thread the main agent opens, of its own, from the user's words or as the research lead named it
        (proposed_as: the item of a review's threads_to_open; docs/adr/0035, D1). The user's words make it the user's
        (the first of them the analysis's question, relayed); a doubt makes it a branch as well, under the thread's
        ID, which tests whether a finding holds; a fork redoes a step with one choice changed, to see where another
        way leads (two channels, each for its own job). reads: a kind of conclusion of the
        design, read across the screen by a worker, whose brief the reply gives (P6). step: what the thread is to the
        line (P15); a doubt's thread is a check, an observation's or a kind's a new area. Only the main agent opens
        threads (docs/adr/0050, D3): on run A the lead opened 8 in 1.5 minutes and the main line sat for 50."""
        self._need()
        if opened_by not in (None, "main"):
            raise AnalysisError("threads are the main agent's to open: name the thread you would open in your "
                                "review's threads_to_open (the fields open_thread takes), or in your answer, and the "
                                "main agent opens it")
        st = self.record.state()
        named = {t["id"] for r in st.get("reviews") or [] for t in r.get("threads_to_open") or []}
        if proposed_as is not None and proposed_as not in named:
            raise AnalysisError(f"no proposal {proposed_as}: proposed_as names an item of the research lead's "
                                f"threads_to_open ({', '.join(sorted(named)) or 'none yet'})")
        if step is not None and step not in rc.STEPS:
            raise AnalysisError(f"step is one of {', '.join(rc.STEPS)}")
        if reads is not None and reads not in candidates.KINDS:
            raise AnalysisError(f"reads is a kind of conclusion: {', '.join(candidates.KINDS)}")
        if structure is not None:
            listed = [x["structure"] for x in self._structures(st)]
            if structure not in listed:
                raise AnalysisError(f"structure is the ID of a set the survey lists: {', '.join(listed) or 'none'}")
        if next_of is not None and next_of not in (st.get("results") or {}):
            raise AnalysisError(f"next_of names a proposed result: {', '.join(st.get('results') or {}) or 'none'}")
        if tries is not None and not (isinstance(tries, dict) and all(str(tries.get(k) or "").strip()
                                                                      for k in TRIES)):
            raise AnalysisError("tries is the choice a worker tries, from a 'Choice:' line: what, value and other, "
                                "each in words")
        step = step or ("check" if doubt_id is not None else
                        "new area" if observation or reads else None)
        n = len(st.get("threads") or {}) + 1
        tid = str(thread_id or f"t{n}").strip()
        while not thread_id and tid in (st.get("threads") or {}):
            n += 1
            tid = f"t{n}"
        words = str(user_words or "").strip()
        if doubt_id is not None and doubt_id not in st["doubts"]:
            raise AnalysisError(f"no doubt {doubt_id}: raise_doubt first")
        prior_events = self._beside_events(None, prior, None, thread_id=tid)
        events = [("thread_opened", {"thread_id": tid, "about": str(about or "").strip(), "raised_by": raised_by,
                                     "kind": rc.OBSERVATION if observation else rc.BREADTH if reads else "opened",
                                     "evidence": evidence or {}, "strength": None, "moves": [],
                                     "matters": matters, "tells_apart": tells_apart,
                                     **({"joins": list(joins)} if joins else {}),
                                     **({"test": str(test)} if test else {}),
                                     **({"on_line": bool(on_line)} if on_line is not None else {}),
                                     **({"step": step} if step else {}), **({"reads": reads} if reads else {}),
                                     **({"structure": structure} if structure else {}),
                                     **({"next_of": next_of} if next_of else {}),
                                     **({"fork_of": fork_of, "choice": str(choice or "").strip()} if fork_of else {}),
                                     **({"tries": {k: str(tries[k]).strip() for k in TRIES}} if tries else {}),
                                     **({"opened_by": opened_by} if opened_by else {}),
                                     **({"proposed_as": proposed_as} if proposed_as else {}),
                                     **({"user_words": words, "relayed": True} if words else {})},
                   rc.AGENT)]  # the user's words relayed by the agent, as note_reply keeps them
        if scope is not None and (scope not in SCOPES or not words):
            raise AnalysisError(f"scope is what the user hands over, {' or '.join(SCOPES)}, with their words "
                                "(user_words)")
        asked = st.get("question") or {}
        if words and (not asked.get("text") or (scope and scope != asked.get("scope"))):
            events.append(("question_asked", {"text": words, "relayed": True, **({"scope": scope} if scope else {})},
                           rc.AGENT))
        if doubt_id is not None:
            events.append(("branch_opened", {"branch_id": tid, "plan": str(about or "").strip(),
                                             "doubt_id": doubt_id}, rc.AGENT))
        self.record.append_many(events + prior_events)  # all or nothing
        out = {"thread_id": tid, "status": "open"}
        known = self._known_now(thread_id=tid) if observation else None
        if known:
            out["known"] = known
        if doubt_id is not None or fork_of or tries:  # its brief reaches the main agent alone, in threads_waiting
            out["next"] = "a worker takes it: the main agent's replies give its brief (threads_waiting)"
        elif reads:
            out.update(self._kind_brief(tid, reads, about))
        return out

    def _kind_brief(self, tid, kind, about):
        """A thread reading a kind of conclusion across the screen (P6): its worker's brief, an objective, how to read
        it, what to record and the answer's form, so the main agent goes on while the worker reads."""
        what, how = candidates.KINDS[kind]
        worker = f"{tid}-{kind.replace('_', '-')}"
        return {"worker": {"worker": worker, "brief": (
            f"Thread {tid} reads {what}, across every target of the screen. {str(about or '').strip()} How: {how}; "
            f"each tool on the cells it reads (cells, by, targets). Your notebook, for what no tool does: run_code "
            f"with worker '{worker}'. Read each result's biology and figure. Say a claim for each target, or set of "
            "targets, that stands past its control-guide line, with its runs, naming the genes, states or conditions "
            "it moves; say what shows nothing. Then say how the thread ends: explained, or out of data with its "
            "reason. Answer in at most fifteen lines: each claim, and what surprised you.")},
            "launch": (f"a worker (agents/worker.md) per kind, in the background; they can run in parallel, up to "
                       f"{self._resources()['max_workers']} at once, while you go on with your own work")}

    def _branch_now(self, tid):
        """A branch's findings and runs at the moment the line moves to it, kept with the move: the item the main
        agent reads stays as it was then (one that grew with the branch was given again at each change)."""
        st = self.record.state()
        th = (st.get("threads") or {}).get(tid) or {}
        found = list(th.get("findings") or [])
        runs = [r for r, x in st["runs"].items() if x.get("thread_id") == tid]
        runs += [r for f in found for r in (st["findings"].get(f) or {}).get("run_ids") or [] if r not in runs]
        return {"findings": found, "runs": runs}

    def _main_line_moved(self, st):
        """Where the main line moved, for the main agent, as it was at the move: the thread, why and by whom, its
        findings and runs then (its worker's variables are not in the main agent's kernel: only the record's runs,
        tables and findings carry over), where the work goes on, and the step the line was about to take."""
        moved = st["main_line"]
        tid = moved["thread_id"]
        return {"to": tid, "about": st["threads"][tid].get("about"), "why": moved["why"], "by": moved["by"],
                "moved_at": moved["seq"], **({"user_words": moved["user_words"]} if moved.get("user_words") else {}),
                "its_findings": list(moved.get("findings") or []), "its_runs": list(moved.get("runs") or []),
                "do": (f"go on from it yourself, in your own notebook: record on it (thread_id {tid}) and open its "
                       "next steps raised_by its findings with on_line true. Its runs' tables load with "
                       "pt.load('<run id>:<table>'), and its findings are in the record. First open the step you "
                       "were about to take on the line before as a thread (open_thread raised_by your last finding "
                       "there, on_line false), so a worker takes it or it waits. A fork's worker grows one level and "
                       "ends: launch none on this thread now that it is your line")}

    def _fork_brief(self, st, tid):
        """A fork's worker brief (the analysis as a tree, 2026-11-13): the step it redoes, with the calls its runs
        made, the one change and why it could change the reading; one level grown, then the next question said, so
        the main agent or the lead decides whether the branch grows on (a worker that grew on would hold its slot)."""
        th = st["threads"][tid]
        ref = th["fork_of"]
        f = (st.get("findings") or {}).get(ref)
        runs = (f or {}).get("run_ids") or ([ref] if ref in st["runs"] else
                                           [r for r, x in st["runs"].items() if x.get("thread_id") == ref])
        calls = []
        for rid in runs[:4]:
            module, given = _module_and_choices(st["runs"].get(rid) or {})
            calls.append(f"run {rid}: " + (f"{module} with {json.dumps(given)}" if module else
                                           str(st["runs"].get(rid, {}).get("what") or "a notebook cell")[:160]))
        what = (f"finding {ref} ({str(f.get('statement') or '')[:200]}; target {f.get('target')}, feature "
                f"{f.get('feature')}, direction {f.get('direction')})" if f else f"{ref}")
        worker = f"{tid}-fork"
        return {"worker": worker, "brief": (
            f"Thread {tid} is a fork of {what}: it redoes that step with one change, {th.get('choice')}. Why it could "
            f"change the reading: {th.get('matters')}. The step's runs: {'; '.join(calls) or 'none named'}.\n"
            f"First open your notebook: run_code with worker '{worker}' and pt.describe(). Then run the step with "
            "the one change and nothing else, on the same targets and cells: the analysis tool of its kind with that "
            "choice, or your notebook. Record what you find (record_finding with thread_id "
            f"{tid}), with its target, feature and direction where it has them, so its checks run. Then end the "
            "thread (thread_status explained, contradicted or out of data) and answer in at most six lines: what the "
            "step gives with the change, beside what it gave before, and the next question your finding raises. "
            "The main agent decides whether the branch grows on.")}

    def _doubt_brief(self, tid, doubt_id, plan, st):
        """A thread that tests a doubt: its worker's brief (the doubt, what would change the judgement, the finding
        with its runs, the plan and the notebook), so the main agent goes on while the worker tests it."""
        d = st["doubts"][doubt_id]
        f = (st.get("findings") or {}).get(d.get("finding_id")) or {}
        runs = list(f.get("run_ids") or [])
        on = (f"finding {d['finding_id']} ({f.get('statement') or 'no sentence'}), resting on the runs "
              f"{', '.join(runs) or 'none named'}" if d.get("finding_id") else
              f"question {d.get('question_id')}" if d.get("question_id") else "the analysis")
        worker = f"{tid}-doubt"
        return {"worker": {"worker": worker, "brief": (
            f"Thread {tid} tests doubt {doubt_id} on {on}. The doubt: {d.get('text')}. What would change the "
            f"judgement: {d.get('would_change')}. The plan: {str(plan or '').strip()}. Your notebook: run_code with "
            f"worker '{worker}'. Do the plan, then say in a few lines what you ran, its numbers beside the same measure "
            "on the control-guide groups, and whether the result would change the judgement.")},
            "launch": ("one worker (agents/worker.md) with this brief, in the background; go on with your own work; "
                       "when it returns, answer_doubt with its runs")}

    def thread_status(self, thread_id, status, reason, run_ids=None, finding_ids=None):
        """A thread's status changed, with the reason (docs/adr/0035, D1). A thread that is also a branch closes it
        when it ends (explained, contradicted, out of data, set aside): a branch of alternatives returns its table.
        Explained names the findings that hold it, by default those recorded with its thread_id (docs/adr/0038, D4:
        a worker's score was ended in a line of reason, and no report could show it)."""
        self._need()
        st = self.record.state()
        th = (st.get("threads") or {}).get(thread_id) or {}
        unprobed = status == "set aside" and rc.set_aside_unprobed(st, thread_id, th)
        self.record.append("thread_changed", {"thread_id": thread_id, "status": status,
                                              "reason": str(reason or "").strip(),
                                              **({"run_ids": list(run_ids)} if run_ids else {}),
                                              **({"finding_ids": list(finding_ids)} if finding_ids else {})},
                           rc.AGENT)
        st = self.record.state()
        th = st["threads"][thread_id]
        out = {"thread_id": thread_id, "status": th["status"], "findings": th["findings"],
               **({"listed": "off your line and set aside before anything probed it: the report lists it so"}
                  if unprobed else {})}  # docs/adr/0049, D4: recorded, not refused
        branch = st.get("branches", {}).get(thread_id)
        if branch is not None and status in rc.THREAD_ENDS and branch.get("outcome") is None:
            closed = self.close_branch(thread_id, str(reason or "").strip(), list(run_ids or []))
            out.update({k: v for k, v in closed.items() if k != "branch_id"})
        if status in ("set aside", "out of data"):  # where a line stops: the check that could still change it
            out.update(self._next_check_said(st))
        if status in rc.THREAD_ENDS:  # the workers that tried it: their notebooks shut, their slots free
            freed = [k for k in list(self.notebooks) if (k == thread_id or k.startswith(f"{thread_id}-"))
                     and self._free_notebook(k)]
            if freed:
                out["notebook_shut"] = sorted(set(out.get("notebook_shut") or []) | set(freed))
        return out

    def close(self):
        """At the server's end: its notebooks shut. Its running jobs go on to their end, and the next server reads
        them (a client without a window starts a server for each turn)."""
        for nb in self.notebooks.values():
            nb.shutdown()

    def _stop_jobs_past_their_limit(self, grace=120):
        """When a project opens: stop each job of its record still running past its time limit (one whose launcher
        could not stop it), and log its end."""
        from . import runner
        stopped = False
        for r in self.record.state()["runs"].values():
            if r["state"] != "running" or not r.get("job_id"):
                continue
            try:
                st = runner.status(r["job_id"], self._jobs_root())
            except KeyError:
                continue
            limit = float(st.get("limit_seconds") or runner.JOB_LIMIT_SECONDS)
            if st["state"] in runner.ACTIVE and self._running_seconds(r) > limit + grace:
                runner.stop(r["job_id"], self._jobs_root())
                stopped = True
        if stopped:
            self.record.sync_runs(self._jobs_root())

    # ---- the project
    def _need(self):
        if self.project is None:
            held = self._workspace_projects()
            raise AnalysisError("open the project first (open_project with the data folder)" + (
                f"; this workspace holds {', '.join(held[:8])}" if held else ""))

    def _workspace_projects(self):
        """The project folders of this workspace, by name."""
        try:
            return sorted(d for d in os.listdir(self.root) if os.path.isdir(os.path.join(self.root, d, "record")))
        except OSError:
            return []

    def _reopen_last(self):
        """A server that starts with no project opens the workspace's last one (a client without a window starts a
        server for each turn), through open_project; returns what the reply says of it, or None."""
        try:
            with open(os.path.join(self.root, LAST_PROJECT), encoding="utf-8") as fh:
                last = json.load(fh)
        except (OSError, ValueError):
            return None
        folder = str(last.get("data_folder") or "")
        if not os.path.isdir(folder):
            return None
        self.open_project(folder)
        return (f"{self.project}: this workspace's last project, opened again as this server started (each turn "
                "may start a new server)")

    def _said(self):
        """What this project's replies have said, kept in the project (it outlives the server's process), or None
        for a project that has not kept it."""
        try:
            with open(os.path.join(self.project, SAID), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _keep_said(self):
        if self.project is None:
            return
        said = {"announced": sorted(self._announced), "opened": sorted(self._opened),
                "told": {kind: dict(v) for kind, v in self._told.items()}}
        if said == self._said_kept:
            return
        path = os.path.join(self.project, SAID)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(said, fh)
        os.replace(path + ".tmp", path)
        self._said_kept = said

    def open_project(self, data_folder):
        data_folder = os.path.abspath(data_folder)
        if not os.path.isdir(data_folder):
            raise AnalysisError(f"no folder {data_folder}")
        name = os.path.basename(data_folder.rstrip(os.sep)) or "project"
        tag = hashlib.sha256(data_folder.encode()).hexdigest()[:8]
        project = os.path.join(self.root, f"{name}-{tag}")
        if self.project and project != self.project:  # another project: an open kernel holds the other's names, and
            self.close()  # reads and writes the other's folders (the open points of 2026-11-19)
            self.notebooks, self.flows = {}, {}
        self.project = project
        self.deposit = data_folder
        self.design = DesignService(make_design_config(data_folder, os.path.join(self.project, "design"), name=name,
                                                       go_ahead=False, background=True))
        self.record = rc.Record(os.path.join(self.project, "record"), self.cfg.get("switches"))
        self.record.build_of = self._build_id  # each run records the build of the screen object it reads (item 2)
        self.record.rule_of = self._cells_rule_id  # and the cells' rule it reads the object with
        try:  # each job's time limit, from the workspace's resources (a long screen's runs may need longer)
            self.record.job_limit_seconds = float(self._resources()["job_hours"]) * 3600
        except AnalysisError:  # resources that do not load are said by the tools that read them
            self.record.job_limit_seconds = None
        self._stop_jobs_past_their_limit()
        said = self._said()
        if said is None:  # the runs already ended when the project first opens are the record's, not news
            self._announced = {r for r, x in self.record.state()["runs"].items()
                               if x.get("job_id") and x["state"] != "running"}
            self._opened = set()
            self._told = {}
        else:  # a server of a later turn says what the earlier ones have not said yet
            self._announced, self._opened = set(said.get("announced") or []), set(said.get("opened") or [])
            self._told = {kind: dict(v) for kind, v in (said.get("told") or {}).items() if isinstance(v, dict)}
        self._said_kept = said
        self._page_state_seq = None
        self._keep_said()
        last = os.path.join(self.root, LAST_PROJECT)
        with open(last + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"data_folder": data_folder, "project": self.project}, fh)
        os.replace(last + ".tmp", last)
        away = {"unattended": "the user runs Pertura unattended: after the design and their question they are "
                              "away. The results stay proposed, for them to decide later (docs/adr/0051, amendment "
                              "2); write what the task asks. Never end a turn to ask; end when the task's output is "
                              "written, or nothing is left that you can do alone: the threads opened are run, or set "
                              "aside with their reasons"} if self.unattended else {}
        return with_host({"project": self.project, "data_folder": data_folder, "data_location": data_location(),
                          **away}, self.cfg.get("config_path"))

    @property
    def unattended(self):
        """Whether the workspace configuration says the user runs Pertura unattended (its 'unattended' key)."""
        return bool(self.cfg.get("unattended"))

    def _screen_dir(self):
        return os.path.join(self.project, "design", "screen_object")

    def _confirmed(self):
        from . import design_gate
        path = self._design_path()
        if not os.path.exists(path):
            raise AnalysisError("the design is not confirmed yet (confirm_design)")
        st = design_gate.status(path)
        if st["missing"]:
            raise AnalysisError("the design is not confirmed yet (open: " + ", ".join(st["missing"]) + ")")
        return design_gate.load(path)[0]

    def pertura_status(self, detail=False):
        if self.project is None:
            return with_host({"project": None, "data_location": data_location(),
                              "next": "ask the user where the screen's data is, then open_project"},
                             self.cfg.get("config_path"))
        ok, design = self.design.call("get_design", {})
        self.record.sync_runs(self._jobs_root())  # a run that ended since is listed as ended
        self._note_build_quietly()
        from . import digest
        events = self.record.events()
        st = rc.reduce(events)
        try:
            resources = self._resources()
        except AnalysisError as e:
            resources = {"error": str(e)}
        # what happened since the last pertura_status of this session, by thread (item 5's digest)
        told = digest.digest(st, events, self._design_confirmed(), self._digest_seen())
        self._digest_seen(events[-1]["seq"] if events else None)
        if not detail:  # the fair comparison's replies ran to 40,000-49,000 characters, mostly these two parts
            told = {**told, "since": [x if len(x) <= DIGEST_LINE_MAX else x[:DIGEST_LINE_MAX] + " ..."
                                      for x in told["since"]]}
        if ok and not detail and isinstance(design, dict) and not design.get("open"):  # confirmed: the check's
            # problems and flags were for the confirmation (2.9k of the 6.7k characters on GSE190604, a9510b4)
            design = {k: v for k, v in design.items() if k != "last_check"}
        if ok and not detail:  # the design in full once per version (GSE267740: 7,800 characters in each status)
            key = "design:" + hashlib.sha256(json.dumps(design, sort_keys=True, default=str).encode()).hexdigest()[:12]
            if key in self._announced:
                design = {"unchanged": "as an earlier reply gave it; get_design gives it in full"}
            else:
                self._announced.add(key)
        from . import knowledge_store
        return with_host({
                "summary": self._summary(st, ok and design), "digest": told, "project": self.project,
                "data_folder": self.deposit, "design": design if ok else None,
                "screen_object": os.path.exists(os.path.join(self._screen_dir(), "screen.json")),
                "runs": len(st["runs"]), "open_items": len(self.record.critic()),
                "blocks_report": self.record.blocks_report(), "running": self._running(),
                "threads": self._threads_view(st, shown=10 ** 6 if detail else threads.SHOWN),
                "plan": {"items": rc.plan_items(st), "version": (st.get("plan") or {}).get("version"),
                         "why": ((st.get("plan") or {}).get("versions") or [{}])[-1].get("why"),
                         "next": rc.plan_next(st)},
                "findings": self._findings_line(st),
                "notebooks": sorted(self.notebooks), "resources": resources, **self._status_coverage(st),
                "tree": rc.tree_text(st),  # the analysis as a tree, its main line marked
                **({} if os.path.exists(knowledge_store.store_path()) else {"knowledge": knowledge_store.CARDS_ONLY}),
                **self._survey_status(),
                **({} if detail else {"detail": "detail: true gives every thread and the digest's lines in full"})},
            self.cfg.get("config_path"))

    def _survey_report(self):
        """The survey's facts and rows for the report (results_report.survey_lines), with whether it is complete and
        how many comparisons have ended, while the survey is on; or None."""
        from . import survey
        try:
            if not self._survey_on() or not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
                return None
            key = self._survey_key()
            if not (self._survey_facts and self._survey_facts[0] == key):  # read once for each
                facts = survey.facts(self._confirmed(), self._screen_dir())
                facts.pop("_cells", None)
                self._survey_facts = (key, facts)
            rows = self._survey_rows()
            return {"facts": self._survey_facts[1], "rows": rows, "complete": survey.complete(rows),
                    "ended": sum(r["state"] != survey.RUNNING for r in rows), "waits": self._survey_waits()}
        except (AnalysisError, OSError, ValueError):
            return None

    def _survey_status(self):
        """While the survey is on, each of its comparisons in a line: its state, reason and run (docs/adr/0045, D5).
        Each of its runs, as it ends, opens in the next reply as its summary and lines (_with_ended)."""
        from . import survey
        try:
            if not self._survey_on() or not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
                return {}
            rows = self._survey_rows()
        except (AnalysisError, OSError, ValueError):
            return {}
        return {"survey": {"complete": survey.complete(rows), "comparisons": [
            f"{survey.ROWS[r['row']]}, {r['scope']}: {r['state']}" + (f" ({r['reason']})" if r["reason"] else "")
            + (f": {r['summary']}" if r.get("summary") else "") + (f", run {r['run_id']}" if r["run_id"] else "")
            for r in rows]}}

    def _write_page_state(self):
        """The project's page state (PAGE_STATE), written when the record has moved since it was last written: the
        laptop's hook reads it at the end of a turn, and while a run runs, for the user's page."""
        if self.project is None or self.record is None:
            return
        events = self.record.events()
        seq = events[-1]["seq"] if events else None
        if seq == self._page_state_seq and os.path.exists(os.path.join(self.project, PAGE_STATE)):
            return
        path = os.path.join(self.project, PAGE_STATE)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(self._page_state(events), fh, default=str)
        os.replace(path + ".tmp", path)
        self._page_state_seq = seq

    def _page_state(self, events=None):
        """What the page shows that no reply carries, in pertura_status's words: the digest, the threads, the coverage
        and the screen overview's rows, and the record as the workbench reads it (pertura.page_view). It reads only:
        the digest is not marked as given."""
        from . import digest, page_view
        events = self.record.events() if events is None else events
        st = self.record.state()
        out = {"project": self.project, "seq": events[-1]["seq"] if events else None,
               "digest": digest.digest(st, events, self._design_confirmed(), self._digest_seen()),
               "threads": self._threads_view(st, shown=10 ** 6), **self._status_coverage(st),
               "record": page_view.view(st, first_look=[{k: s[k] for k in page_view.FIRST_LOOK_FIELDS}
                                                        for s in self._first_look_sections(st)])}
        if isinstance(self.design, DesignService):  # the design as the auditor has it: the analysis waits on it
            try:
                from .design_service import TITLES
                g = self.design.get_design()
                out["record"]["design"] = {"confirmed": [TITLES.get(k, k) for k in g.get("confirmed") or []],
                                           "open": [TITLES.get(k, k) for k in g.get("open") or []],
                                           "last_check": g.get("last_check"), "measured": g.get("measured"),
                                           "since": events[0].get("timestamp") if events else None}
            except Exception as e:  # a design not opened yet, or its file missing, never breaks the page state
                _logged(e)
        self._methods_seen = getattr(self, "_methods_seen", None) or {}  # a run's result is fixed once it ended
        out["methods"] = page_view.methods(st, self._jobs_root(), self._methods_seen)
        plan = st.get("plan") or {}
        if plan.get("items") or plan.get("left"):  # the agent's plan as it stands, and the line its next step gets
            versions = plan.get("versions") or [{}]
            out["plan"] = {"items": rc.plan_items(st), "version": plan.get("version"), "why": versions[-1].get("why"),
                           "next": rc.plan_next(st)}
        done = [r for r in self._module_runs(st).get(OVERVIEW, []) if r["state"] == "done"]
        if done:
            try:
                out["screen_overview"] = self._overview_inline(done[-1]["run_id"], whole=True)
            except (OSError, ValueError, KeyError):  # the page keeps the copy it has
                pass
        from .survey import ROWS
        survey_now = self._survey_report()  # None while the survey is off; each row with its label, for the page
        out["survey"] = survey_now and {**survey_now, "rows": [{**r, "label": ROWS[r["row"]]}
                                                               for r in survey_now["rows"]]}
        return out

    def _status_coverage(self, st):
        """pertura_status's coverage (the turn-end line's lists, each cut to its first entries) and the line."""
        from . import turn_end
        try:
            cov = self._turn_end_coverage(st)
        except Exception as e:  # the coverage never breaks the status
            _logged(e)
            return {}
        if cov is None:
            return {}
        return {"coverage": turn_end.kept(cov), "coverage_line": turn_end.line(cov)}

    def _summary(self, st, design):
        """Where things stand, in plain words for the user (docs/adr/0032, D14): what has been found and how sure,
        what is running, and what comes next. No module or card code appears in it."""
        from .results_report import finding_name
        fs = st["findings"]
        words = []
        if not (design or {}).get("design_file") and not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
            words.append("The design is not settled yet: the auditor reads the files, then the user confirms the "
                         "design on a form.")
        elif not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
            words.append("The design is confirmed; the screen object is being built.")
        if not fs:
            words.append("No finding recorded yet.")
        else:
            agree = sum(1 for f in fs.values() if rc.checks_ran(f) and not rc.checks_disagree(f))
            against = sum(1 for f in fs.values() if rc.checks_disagree(f))
            kept = sum(1 for f in fs.values() if rc.kept_by_user(f))
            waiting = sum(1 for f in fs.values() if not f.get("replies"))
            sure = ([f"{agree} with checks that agree"] if agree else []) + (
                [f"{against} whose checks disagree"] if against else [])
            words.append(f"{len(fs)} finding{'s' if len(fs) != 1 else ''} so far" +
                         (f" ({', '.join(sure)})" if sure else "") + f"; {kept} kept by the user; {waiting} not yet "
                         "answered by the user.")
            words.append(f"The latest: {finding_name(list(fs.values())[-1])}.")
        running = len(self._running())
        words.append(f"{running} analys{'es' if running != 1 else 'is'} running in the background." if running else
                     "Nothing is running in the background.")
        missing = self._plan_next(st)
        if rc.enough_stands(st):
            words.append("The user said enough: next is the report.")
        elif missing:
            words.append("Next: " + missing.split(":")[0] + ", asked in chat.")
        return " ".join(words)

    def _findings_line(self, st, shown=10):
        """The findings, latest first: each with its sentence (or its target and feature), whether its checks ran or
        disagree, and the user's replies (docs/adr/0032, D6)."""
        from .results_report import finding_name
        out = []
        earlier = rc.previous_screen_findings(st)
        for fid, f in list(st["findings"].items())[::-1][:shown]:
            checks = ("disagree" if rc.checks_disagree(f) else "ran" if rc.checks_ran(f) else
                      "running or not run" if rc.checkable(f) else "none")
            out.append({"finding_id": fid, "finding": finding_name(f), "checks": checks,
                        "replies": [{"kind": r["kind"], "words": r["words"]} for r in f.get("replies") or []],
                        **({"screen": "previous"} if fid in earlier else {})})
        return out

    def _build_id(self):
        """The ID of the screen object's build as it stands, or None before a build."""
        from . import screen_object
        try:
            return screen_object.current_build(self._screen_dir())
        except (OSError, ValueError):
            return None

    def _cells_rule_id(self):
        """The cells' rule the screen object is read with now (the design's data.qc, as Screen applies it), as a
        short hash; None without a design or a rule. A run or a check made under another rule is not given again."""
        from .blocks import _cells_rule
        try:
            rule = _cells_rule(self._design_path())
        except Exception as e:  # an unreadable design: no rule known, and nothing matched by one
            _logged(e)
            return None
        return None if rule in (None, "null") else hashlib.sha256(rule.encode()).hexdigest()[:12]

    def _note_build_quietly(self):
        """The screen object's build noted in the record if a rebuild ended unread (item 2); a failure here never stops
        the call it comes with."""
        try:
            self._note_build()
        except Exception as e:  # noted at the next build's reply instead
            _logged(e)
            pass

    def _builds_dir(self):
        return os.path.join(self.project, "design", "screen_object_builds")

    def _confirmed_rule(self):
        """The confirmed cells' quality rule (data.qc's value), or None."""
        from . import design_gate
        design = design_gate.load(self._design_path())[0]
        return design_gate.confirmed(design, "data.qc")

    def _built_and_idle(self):
        """Whether the screen object is built, its build given to the agent (noted in the record) and no build runs:
        a confirmation of the cells' rule then reaches the first look."""
        from . import screen_object
        sd = self._screen_dir()
        if not os.path.exists(os.path.join(sd, "screen.json")) or self.record is None:
            return False
        if screen_object.current_build(sd) not in (self.record.state().get("builds") or {}):
            return False
        work = self.design.in_background() if isinstance(self.design, DesignService) else []
        return not any(w["what"] == "the screen object's build" for w in work)

    def _keep_build(self):
        """The screen object's build as it stands (its summary and its cells' table), kept under its ID before a
        rebuild can write over it; nothing is deleted."""
        import shutil
        from . import screen_object
        sd = self._screen_dir()
        bid = screen_object.current_build(sd)
        folder = os.path.join(self._builds_dir(), str(bid))
        if bid is None or os.path.exists(os.path.join(folder, "cells.tsv.gz")):
            return
        os.makedirs(folder, exist_ok=True)
        for name in ("screen.json", "cells.tsv.gz"):
            if os.path.exists(os.path.join(sd, name)):
                shutil.copyfile(os.path.join(sd, name), os.path.join(folder, name + ".tmp"))
                os.replace(os.path.join(folder, name + ".tmp"), os.path.join(folder, name))

    def _note_build(self):
        """The build in the record, once: the first, or a rebuild compared with the build before it (item 2): when no
        cell's label, unit or condition changed, nothing is marked; else the runs on the earlier build are on the
        previous screen. Returns what the build's reply says, or {}."""
        import pandas as pd
        from . import screen_object
        sd = self._screen_dir()
        bid = screen_object.current_build(sd)
        st = self.record.state()
        if bid is None or bid in (st.get("builds") or {}):
            return {}
        self._keep_build()  # this build too, for the next rebuild to compare with
        previous = st.get("build")
        if previous is None:  # a record from before the builds: its runs' build, else the build kept before this one
            previous = next((r["build"] for r in reversed(list(st["runs"].values())) if r.get("build")), None)
        if previous is None and os.path.isdir(self._builds_dir()):
            kept = sorted((d for d in os.listdir(self._builds_dir()) if d != bid),
                          key=lambda d: os.path.getmtime(os.path.join(self._builds_dir(), d)))
            previous = kept[-1] if kept and st["runs"] else None
        old = os.path.join(self._builds_dir(), str(previous), "cells.tsv.gz")
        if previous is None or not os.path.exists(old):
            self.record.append("screen_rebuilt", {"build_id": bid, "previous": previous,
                                                  "changed": previous is not None,
                                                  "what": {"unknown": True} if previous is not None else {}}, rc.TOOL)
            return {}
        read = dict(sep="\t", keep_default_na=False, dtype=str)
        what = screen_object.cells_changed(pd.read_csv(old, **read),
                                           pd.read_csv(os.path.join(sd, "cells.tsv.gz"), **read))
        self.record.append("screen_rebuilt", {"build_id": bid, "previous": previous, "changed": what["changed"],
                                              "what": what}, rc.TOOL)
        if not what["changed"]:
            return {"rebuilt": "no cell's label, unit or condition changed: every earlier run stands"}
        st = self.record.state()
        runs = [r for r, run in st["runs"].items() if rc.on_previous_screen(st, run)]
        return {"rebuilt": {"what_changed": rc.what_changed(st, previous), "runs_on_the_previous_screen": len(runs),
                            "findings_resting_on_them": sorted(rc.previous_screen_findings(st)),
                            "next": "the first look reruns on this build (first_look); rerun the other runs a finding "
                                    "rests on, then reword the finding. Nothing earlier is deleted"}}

    def _after_build(self):
        """Once the screen object is built: the build in the record (what a rebuild changed), and the first-look
        modules started in the background, each once per build that changed the cells (docs/adr/0032, D11). The agent
        plans the rest (write_plan)."""
        try:
            self._confirmed()
        except AnalysisError:
            return {}
        noted = self._note_build()
        waits = self._cells_wait()
        if waits:  # the cells' rule first: the overview, for the agent's proposal and the user's form
            from . import cells_overview
            key = f"cells_overview:{self._build_id()}"
            if key in self._announced:  # once per build (GSE267740: 23,600 characters and a figure in each reply)
                return {**noted, "cells_overview": "as an earlier reply gave it, for this build; its figure and tables "
                                                   f"are in {os.path.join(self._screen_dir(), cells_overview.FOLDER)}",
                        "waits_on_user": f"the cells' quality rule ({waits})", "next": CELLS_NEXT}
            self._announced.add(key)
            ov = cells_overview.reply(self._screen_dir())
            out = {**noted, "cells_overview": ov, "waits_on_user": f"the cells' quality rule ({waits})",
                   "next": CELLS_NEXT}
            images, _ = self._images([ov["figure"]])
            if images:
                out["_images"] = images
            return out
        started = self._start_routine()
        return {**noted, **({"first_look": started} if started["started"] else {})}

    def _cells_wait(self):
        """Why the cells wait on the user, or None: the screen object holds a cells overview (pertura.cells_overview)
        and its quality rule (data.qc) is not confirmed since that build. A build without an overview waits for
        nothing."""
        from . import cells_overview, design_gate
        sd = self._screen_dir()
        meta = os.path.join(sd, "screen.json")
        if not (os.path.exists(meta) and os.path.exists(os.path.join(sd, cells_overview.FOLDER, "measures.tsv.gz"))):
            return None
        design = design_gate.load(self._design_path())[0]
        if design_gate.missing_keys(design, ["data.qc"]):
            return "data.qc is not confirmed"
        dates = [c.get("date") for c in design.get("confirmations") or []
                 if isinstance(c, dict) and c.get("key") == "data.qc"]
        if cells_overview.confirmed_on_this_build(sd, dates):
            return None
        return "data.qc was confirmed before this build"

    def _coverage(self, st):
        """{question: whether a run has served it}: the questions A-H as a coverage list (docs/adr/0032, D7)."""
        served = {r.get("question_id") for r in st["runs"].values()}
        return {qid: qid in served for qid in st["order"]}

    def _routine_ids(self, st):
        """The first look's modules (FIRST_LOOK, in its order) this design calls for: each of them, B.efficacy where a
        modality acts on the target's own gene (its contract's applies_when), a mixed library's modes among them."""
        from . import question_modules as qm
        try:
            design = self._confirmed()
        except AnalysisError:
            return []
        own_gene = bool(rc.modalities(design) & {"crispri", "crispra", "crispr_ko"})
        return [q for q in FIRST_LOOK if q in qm.REGISTRY and (q != "B.efficacy" or own_gene)]

    def _routine_rows(self, st):
        """Each routine question's latest module run: its state, and from its result its status, summary and flags
        (its diagnostics' flags, warnings and caveats, and not enough data)."""
        rows = []
        for qid in self._routine_ids(st):
            runs = [r for r in st["runs"].values() if r.get("question_id") == qid and r.get("job_id")]
            if not runs:
                rows.append({"question_id": qid, "state": "not run"})
                continue
            r = runs[-1]
            row = {"question_id": qid, "run_id": r["run_id"], "state": r["state"]}
            path = os.path.join(self._jobs_root(), r["job_id"], "result.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    res = json.load(fh)
                diag = res.get("diagnostics") or {}
                flags = []
                for k in ("flags", "warnings", "caveats"):
                    v = diag.get(k)
                    flags += ([f"{a}: {b}" for a, b in v.items()] if isinstance(v, dict) else
                              [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else [])
                if res.get("status") == "not_enough_data":
                    flags.append("not enough data")
                row.update(status=res.get("status"), summary=str(res.get("summary") or "")[:400], flags=flags)
            rows.append(row)
        return rows

    def _start_routine(self):
        """The first look's modules started at once, each a run of its own; one already run on this screen, or a
        question a record's plan closed, is left as it is."""
        st = self.record.state()
        started = {}
        for qid in self._routine_ids(st):
            if (st["questions"].get(qid) or {}).get("status") is not None or any(
                    r.get("question_id") == qid and not rc.on_previous_screen(st, r) for r in st["runs"].values()):
                continue
            try:
                started[qid] = self.run_module(qid, question_id=qid, wait=0, source="code").get("run_id")
            except AnalysisError as e:
                started[qid] = {"not started": str(e)}
        ids = [v for v in started.values() if isinstance(v, str)]
        return {"started": started,
                "next": ("they run in the background; each reply opens the runs that have ended since your last "
                         "call, and read_record with what overview gives them in one table, each with its flags")
                if ids else
                        "the first-look modules have runs already: read_record with what overview"}

    # ---- the plan
    def plan_questions(self, question=None, paradigm=None, why=None, inserted=None, fork=None, choice=None,
                       options=None, routine=False, user_words=None):
        """The questions A-H this design calls for, as reminders (docs/adr/0032, D7), each with whether a run has
        served it; the user's question in their words, relayed from the chat; and the first-look modules. A card's
        route (paradigm, fork) has left the product (pertura_research.retired.paradigm_route): the agent reads the
        cards that fit and writes its plan."""
        self._need()
        design = self._confirmed()
        sd = self._screen_dir()
        if not os.path.exists(os.path.join(sd, "screen.json")):
            raise AnalysisError("build the screen object first (build_screen_object)")
        if paradigm is not None or fork is not None:
            raise AnalysisError("a card's route has left the product: read the cards that fit (read_knowledge) and "
                                "open a thread for the question it answers (open_thread)")
        planned = self.record.plan(design, rc.screen_facts(sd))
        st = self.record.state()
        seen = self._coverage(st)
        from . import design_fit
        out = {"questions": [{**q, "status": st["questions"].get(q["question_id"], {}).get("status"),
                              "seen": seen.get(q["question_id"], False)} for q in planned],
               "tests": design_fit.plan_leads(sd, design),  # the tests the design chooses, shown with the plan
               "switched_off": not self.record.enabled("plan_and_critic")}
        if question is not None and str(question).strip():
            self.record.append("question_asked", {"text": str(question).strip(), "relayed": True}, rc.AGENT)
        st = self.record.state()
        out["question"] = st["question"]
        missing = self._plan_next(st)
        if missing:
            out["missing"] = missing
        if routine:
            out["routine"] = self._start_routine()
        return out

    def _ask_question(self, elicit):
        """The user's question, in their words, on a form (stage 1 of docs/adr/0030)."""
        action, content = elicit(
            "Pertura: what do you want to learn from this screen? Say it in your own words. It leads the analysis: "
            "the approach the analysis agent (an AI) proposes, which results matter, and which leads it follows. It "
            "is recorded as you write it.",
            {"type": "object", "properties": {"question": {
                "type": "string", "title": "Your question",
                "description": "for example: which regulators move the cells from one program to another"}},
             "required": ["question"]})
        text = str((content or {}).get("question") or "").strip() if action == "accept" else ""
        if text:
            self.record.append("question_asked", {"text": text}, rc.USER)

    def _plan_next(self, st, cards=None):
        """What is missing for the analysis, or None: since docs/adr/0032 (D1) a reply names a next step only when
        something is missing, never a card's next step."""
        if st["question"] is None:
            return ("the user's question: ask them in chat what they want to learn from this screen, and record "
                    "their words (open_thread with user_words)")
        return None

    def add_question(self, letter, item, because):
        self._need()
        if letter not in "ABCDEFGH" or len(letter) != 1:
            raise AnalysisError("letter is one of A-H")
        qid = f"{letter}.{item.replace(' ', '_')}"
        self.record.append("question_planned", {"question_id": qid, "letter": letter, "item": item,
                                                "because": because}, rc.AGENT)
        return {"question_id": qid}

    # ---- runs
    def _resources(self):
        """The resources in force (pertura.resources), read again from the configuration file each time."""
        try:
            return rs.load(self.cfg)
        except (rs.ResourceError, OSError, ValueError) as e:
            raise AnalysisError(f"the workspace configuration's resources: {e}")

    def _notebook(self, worker=None):
        from .notebook import Notebook
        key = worker or "main"
        if key not in self.notebooks:
            res = self._resources()
            if worker and sum(1 for k in self.notebooks if k != "main") >= res["max_workers"]:
                open_ = sorted(k for k in self.notebooks if k != "main")
                raise AnalysisError(f"at most {res['max_workers']} workers' notebooks at once (open: {open_}); "
                                    "a thread's end, said in your reply and recorded, shuts its workers' notebooks and "
                                    "frees their slots")
            # before the build the notebook serves the design step: the design auditor draws the counts that decide
            # which cells belong where, runs the field's tools on the deposit, and reads a deposit Pertura's readers
            # cannot into cell tables; the blocks (pt.*) need the screen object and say so
            sd = self._screen_dir()
            folder = os.path.join(self.project, "notebooks", key)
            os.makedirs(folder, exist_ok=True)
            # the deposit, the design folder (the design, the extracted archives, the jobs' outputs) and every
            # notebook's folder, read only: each writes its own, and the project's cell tables (pertura.cell_table),
            # where the agent's own reader puts a deposit Pertura's readers cannot read; the manifest names its run
            extra = [self.deposit, os.path.join(self.project, "design"), os.path.join(self.project, "notebooks")]
            tables = os.path.join(self.project, "design", "cell_tables")
            if self._jobs_root() not in extra:
                extra.append(self._jobs_root())
            self.notebooks[key] = Notebook(
                folder, sd, threads=res["worker_threads"] if worker else res["main_threads"], extra_read=extra,
                timeout=res["cell_timeout"], max_timeout=res["cell_timeout_max"], max_text=res["output_chars"],
                memory_gb=res["memory_gb"], mode=res["mode"], network=res["network"], extra_write=[tables])
        return self.notebooks[key]

    def _workspace_note(self, nb):
        """What a notebook holds, for its first reply: the screen object's data as pt.screen().adata gives it, read
        from the object's files, and what the notebook allows."""
        sd = self._screen_dir()
        note = {"data": "pt.screen().adata: the screen object as an AnnData, the raw counts (cells x genes) with "
                        "each cell's columns; pt.screen().lognorm holds log1p of counts per 10,000 (cells x genes, "
                        "sparse). scanpy and the field's packages work on the AnnData directly.",
                "cells_rule": "the confirmed cells' rule is in the cells' columns: qc_reason (why a cell is left "
                              "out, '' for a kept one) and label (the label every module reads, 'failed QC' for a "
                              "cell left out; label_after_qc is the same); label_before_qc is the build's, before "
                              "the rule"}
        try:
            with open(os.path.join(sd, "screen.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            note["cells"] = meta.get("cells")
            note["labels"] = len(meta.get("labels") or {})
            note["units"] = len(meta.get("units") or {})
            import pandas as pd
            cells = pd.read_csv(os.path.join(sd, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False)
            note["cell_columns"] = [c for c in cells.columns if c != "barcode"]
            from .screen_object import load_design
            from .question_modules.data import value
            path = self._design_path() if self.design is not None else ""
            merge = (value(load_design(path), "units.merge") or {}) if os.path.exists(path) else {}
            units = sorted({str(merge.get(u, u)) for u in meta.get("units") or {}})
            note["units_column"] = (f"unit: each cell's unit ({len(units)}: {', '.join(units[:8])}"
                                    f"{', ...' if len(units) > 8 else ''}), as the confirmed design names them"
                                    + ("; capture: the build's, before units.merge" if merge else ""))
            one = {c: str(cells[c].iat[0]) if len(cells) else "" for c in note["cell_columns"]
                   if cells[c].nunique() <= 1}
            if one:  # the same in every cell, so they tell no cells apart
                note["columns_with_one_value"] = one
            import glob
            counts = sorted(glob.glob(os.path.join(sd, "counts-*.h5ad")), key=os.path.getmtime)
            if counts:  # the build writes the counts beside the object; their shape without loading them
                import h5py
                with h5py.File(counts[-1], "r") as f:
                    x = f["X"]
                    note["shape"] = [int(v) for v in (x.attrs["shape"] if "shape" in x.attrs else x.shape)]
        except (OSError, ValueError, KeyError, ImportError):
            pass
        info = nb.info()
        note["notebook"] = {
            "mode": info["mode"], "threads": info["threads"], "longest_cell_seconds": info["max_cell_seconds"],
            "output_chars": info["output_chars"], "network": info["network"],
            "may": ("read any file and run programs (R scripts, command-line tools, processes); write in this folder"
                    if info["mode"] == "normal" else "read the screen object and the deposit; write in this folder")}
        return note

    def _cell_images(self, out, saved, written, cap):
        """The figures a cell drew, for its reply: the ones it showed inline, then the ones save_figure kept, then
        PNG files it wrote where it showed none inline (a saved PNG would otherwise be shown twice). Returns
        (images, paths shown, paths named only)."""
        def real(p):  # the trace writes real paths, which differ from the given ones under a linked folder
            return os.path.normcase(os.path.realpath(p))
        inline = list(out.get("figures") or [])
        kept = [s["png"] for s in saved if s.get("png")]
        files = [] if inline else [p for p in written if str(p).lower().endswith(".png")]
        paths, seen = [], set()
        for p in inline + kept + files:
            if real(p) not in seen:
                seen.add(real(p))
                paths.append(p)
        images, left = self._images(paths[:cap])
        shown = [p for p in paths[:cap] if p not in left]
        return images, shown, left + paths[cap:]

    def run_code(self, code, what, question_id=None, worker=None, timeout=None, step=None, why=None, thread_id=None):
        self._need()
        self._known_thread(thread_id)
        step, step_note = self._plan_step(step)
        nb = self._notebook(worker)
        res = self._resources()  # a cell's limits as the configuration has them now
        nb.timeout, nb.max_timeout, nb.max_text = res["cell_timeout"], res["cell_timeout_max"], res["output_chars"]
        first = not nb.alive()  # a new kernel, in this session: its reply says what the notebook holds
        rid = f"run{len(self.record.state()['runs']) + 1:04d}-" + hashlib.sha256(
            f"{time.time()}{code}".encode()).hexdigest()[:6]
        # the cell-table folder and this run's ID, so code that reads the deposit into cell tables names its run
        prefix = (f"screen = {self._screen_dir()!r}\ndeposit = {self.deposit!r}\n"
                  f"cell_tables = {os.path.join(self.project, 'design', 'cell_tables')!r}\nrun_id = {rid!r}\n"
                  "import pertura.blocks as pt\n"  # the blocks (docs/adr/0024): where they read and write
                  f"pt.context(screen_dir=screen, design_path={self._design_path()!r}, notebook_dir={nb.dir!r}, "
                  f"jobs_dir={self._jobs_root()!r})\n"
                  "try:\n    pt.save_figure = save_figure\nexcept NameError:\n    pass\n")  # the notebook's own
        from .notebook import saved_figures
        self._note_build_quietly()
        self.record.append("run_logged", {"run_id": rid, "what": what, "code": code, "question_id": question_id,
                                          "inputs": [self._screen_dir(), self.deposit], "worker": worker,
                                          "step": step, "build": self._build_id(), "why": why,
                                          "thread_id": thread_id}, rc.TOOL)
        from . import cell_sets
        sets_folder = os.path.join(nb.dir, cell_sets.FOLDER)
        sets_before = cell_sets.pending(sets_folder)
        before = saved_figures(nb.figures)
        self._base_objects()
        nb.start()
        kernel = self._kernel_flow(worker or "main", nb)  # before the cell: a stuck cell restarts its kernel
        out = nb.run(prefix + code, timeout=timeout)
        files = out.pop("files", None) or {}
        uses = self._earlier_versions(kernel.inputs(code, self._objects_read(files.get("read") or [])), nb,
                                      files.get("written") or [])
        text = "\n".join(str(out.get(k) or "") for k in ("stdout", "result", "error", "traceback") if out.get(k))
        saved = [s for p, (h, s) in saved_figures(nb.figures).items() if before.get(p, (None,))[0] != h]
        figs = [s["png"] for s in saved] + (out.get("figures") or [])
        self.record.append("run_finished", {"run_id": rid, "state": out.get("status", "done"), "output": text,
                                            "figure": figs[0] if figs else None, "uses": uses}, rc.TOOL)
        entered = self.record.enter_saved_figures(rid, saved, prefix=worker)
        kept, refused = self._enter_cell_sets(rid, sets_folder, sets_before)
        extra = {"cell_sets_kept": kept} if kept else {}
        calls, jobs = self._enter_blocks(rid, nb, worker or "main")
        made = self._enter_written(rid, nb, files.get("written") or [], uses, calls, kept, entered)
        if not out.get("kernel_restarted"):  # a restarted kernel has none of this cell's names
            kernel.bind(code, uses, made, rid)
        if uses or made:
            extra["objects"] = {"used": uses[:20], "made": made[:20]}
        if calls:
            extra["block_calls"] = calls
        if jobs:
            extra["block_jobs_started"] = jobs
        if refused:
            extra["cell_sets_refused"] = refused
        images, shown, named = self._cell_images(out, saved, files.get("written") or [], res["images_per_cell"])
        if images:
            extra["_images"] = images
            extra["figures_shown"] = shown
        if named:
            extra["figures_not_shown"] = named
        if files.get("written_by_programs"):
            extra["written_by_programs"] = files["written_by_programs"][:20]
        if first:
            extra.update(self._papers_for_the_worker(worker))
            extra["workspace"] = self._workspace_note(nb)
            from . import envs
            extra["packages"] = {"by_use": envs.field_packages(),
                                 "how": "the field's packages installed here, by use: call them in your cells; a "
                                        "package listed as missing is the user's question, never replaced by "
                                        "another you choose"}
        if any(k["calls"] for k in kept):
            extra["cell_sets_note"] = ("a kept set that relabels cells is usable at once, and findings on it say so "
                                       "(the screen's default calling rule is the design's perturbation.assignment)")
        if step_note:
            extra["step_note"] = step_note
        if "MemoryError" in str(out.get("error") or ""):  # the fair comparison's D: nine, with nothing said of the limit
            extra["memory"] = (f"the kernel's data limit is {nb.memory_gb:g} GB, and a copy of a large screen's whole "
                               "matrix takes several: cut the cells first (pt.screen().subset(...)), copy what the "
                               "step needs, and run one block call at a time (each uses the notebook's threads)")
        return {"run_id": rid, **out, "figures_entered": entered, **extra}

    def _chain_reply(self, run_id):
        """Nothing: since docs/adr/0032 (D1) a reply does not name a card's next step. A run still names its step,
        which the record keeps for the report's methods."""
        return {}

    # ---- the data flow (docs/adr/0027)
    def _base_objects(self):
        """The deposit, the confirmed design and the screen object, the flow's base: registered once, and again when
        the design file changes."""
        objs = self.record.state().get("objects", {})
        design = self._design_path()
        sha = None
        if design and os.path.exists(design):
            with open(design, "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
        if "deposit" not in objs:
            self.record.append("object_registered", {"object_id": "deposit", "kind": "deposit", "run_id": None,
                                                     "path": self.deposit}, rc.TOOL)
        if (objs.get("design") or {}).get("sha256") != sha or "screen" not in objs:
            self.record.append("object_registered", {"object_id": "design", "kind": "design", "run_id": None,
                                                     "path": design, "uses": ["deposit"], "sha256": sha}, rc.TOOL)
            self.record.append("object_registered", {"object_id": "screen", "kind": "screen", "run_id": None,
                                                     "path": self._screen_dir(), "uses": ["deposit", "design"]},
                               rc.TOOL)

    def _kernel_flow(self, key, nb):
        """The names of this notebook's kernel and the objects behind them, new with each kernel it starts."""
        from . import flow
        have = self.flows.get(key)
        if have is None or have[0] != nb.starts:
            have = (nb.starts, flow.Kernel())
            self.flows[key] = have
        return have[1]

    def _objects_read(self, paths):
        """The objects whose files these are, each once."""
        from . import flow
        index = flow.path_index(self.record.state(), self._screen_dir(), self._jobs_root())
        return [o for o in dict.fromkeys(flow.object_of(p, index) for p in paths) if o]

    def _earlier_versions(self, uses, nb, written):
        """A cell that reads an object and saves it again (pt.load then pt.save_table under the same name) used the
        earlier version: its run stands in the cell's inputs, so a chain keeps the history."""
        from . import flow
        objs = self.record.state().get("objects", {})
        again = set(flow.written_objects(nb.dir, written))
        return list(dict.fromkeys(objs[u]["run_id"] if u in again and (objs.get(u) or {}).get("run_id") else u
                                  for u in uses))

    def _enter_written(self, run_id, nb, written, uses, calls, kept, figures):
        """The objects a cell made: its block calls, kept cell sets and figures (entered by their own tools), and
        the tables and files it wrote, registered here with the cell's inputs and its block calls as theirs."""
        from . import flow
        made = [c["call_id"] for c in calls] + [f"cells:{k['name']}" for k in kept] + list(figures)
        theirs = list(dict.fromkeys(list(uses) + [c["call_id"] for c in calls]))
        events = []
        for oid, (kind, path) in flow.written_objects(nb.dir, written).items():
            meta = {}
            sidecar = path[:-len(".tsv.gz")] + ".json" if kind == "table" else None
            if sidecar and os.path.exists(sidecar):
                try:
                    with open(sidecar, encoding="utf-8") as fh:
                        raw = json.load(fh)
                    meta = {k: v for k, v in raw.items() if k in ("columns", "rows", "note")} \
                        if isinstance(raw, dict) else {}
                except (OSError, ValueError):
                    meta = {}
            events.append(("object_registered", {"object_id": oid, "kind": kind, "run_id": run_id, "path": path,
                                                  "uses": theirs, **meta}, rc.TOOL))
            made.append(oid)
        if events:  # one transaction for all of them, however many files the cell wrote
            self.record.append_many(events)
        return made

    def _object_inputs(self, module, inputs):
        """A module's inputs with each object ID replaced by its file, the objects they are, and each table input
        checked for the columns the module reads (docs/adr/0027, D2 and rule 7). An ID the record does not hold
        is named; nothing else is refused."""
        import pandas as pd
        from . import blocks, flow
        from .question_modules.contract import FULL_GENE_TABLES, TABLE_INPUTS, TABLE_PRODUCERS
        self.record.sync_runs(self._jobs_root())  # a module run that has ended has its tables registered
        st = self.record.state()
        objs = flow.objects(st)
        index = flow.path_index(st, self._screen_dir(), self._jobs_root())
        inputs = self._tables_from_runs(module, dict(inputs), st, TABLE_INPUTS)
        out, uses = {}, []
        for k, v in inputs.items():
            run, _, table = v.partition(":") if isinstance(v, str) else ("", "", "")
            if table and v not in objs and st["runs"].get(run, {}).get("job_id"):  # a run ended before ADR 0027
                path = rc.folder_tables(os.path.join(self._jobs_root(), run)).get(table)
                if path:
                    objs[v] = {"kind": "table", "run_id": run, "path": path}
            full = (blocks.genes_table_file(os.path.join(self._jobs_root(), run))
                    if (module, k) in FULL_GENE_TABLES and run in st["runs"] else None)
            if full:  # the run's test of every gene in place of its summary or its changed genes
                uses += [o for o in (v, f"{run}:changed_genes", f"{run}:result") if o in objs][:1]
                v = full
            elif isinstance(v, str) and v.startswith("cells:") and v[6:] in st.get("cell_sets", {}):
                uses.append(v)
                v = os.path.join(self._screen_dir(), "cell_sets", v[6:] + ".tsv")
            elif isinstance(v, str) and v in objs and objs[v].get("path"):
                uses.append(v)
                v = objs[v]["path"]
                if os.path.isdir(v) and os.path.exists(os.path.join(v, "table.tsv.gz")):  # a kept block result
                    v = os.path.join(v, "table.tsv.gz")
            elif isinstance(v, str) and (v.split(":", 1)[0] in ("table", "cells", "result", "file")
                                         or v.split(":", 1)[0] in st["runs"]):
                raise AnalysisError(f"{module}'s input {k}: no object {v!r} in the record; read_record with what "
                                    "'objects' lists them")
            elif isinstance(v, str) and os.path.exists(v):
                o = flow.object_of(v, index)
                if o:
                    uses.append(o)
            if (module, k) in TABLE_INPUTS and isinstance(v, str) and os.path.isfile(v):
                try:
                    have = [str(c) for c in pd.read_csv(v, sep="\t", nrows=0).columns]
                except Exception as e:
                    raise AnalysisError(f"{module}'s input {k}: {inputs[k]} is not a readable table ({e})")
                missing = [c for c in TABLE_INPUTS[(module, k)] if c not in have]
                if missing:
                    made_by = TABLE_PRODUCERS.get((module, k))
                    raise AnalysisError(f"{module}'s input {k} needs the columns {missing}; {inputs[k]} has {have}"
                                        + (f". It comes from {made_by}" if made_by else ""))
            out[k] = v
        return out, list(dict.fromkeys(uses))

    def _tables_from_runs(self, module, inputs, st, table_inputs):
        """A module's table inputs named by a run: a run's ID alone takes that run's table of the input's name, else
        its result; and a table input left out is taken from a run another input names where the module declares
        it (contract.TABLES_FROM_ANOTHER_INPUT) and one of that run's tables (the input's name, else its result) has
        the columns the module reads: a C.states_and_composition run gives C.within_states both each cell's state and
        the composition."""
        import pandas as pd
        from .paradigms import called
        from .question_modules.contract import TABLES_FROM_ANOTHER_INPUT, TABLES_FROM_THE_LATEST_RUN

        def run_of(v):
            run = v.partition(":")[0] if isinstance(v, str) else ""
            return run if run in st["runs"] else None

        def table_of(run, k):
            tables = rc.folder_tables(os.path.join(self._jobs_root(), run))
            need = table_inputs.get((module, k), ())
            for name in (k, "result"):
                path = tables.get(name)
                if not path:
                    continue
                try:
                    have = set(map(str, pd.read_csv(path, sep="\t", nrows=0).columns))
                except Exception as e:
                    _logged(e)
                    continue
                if all(c in have for c in need):
                    return f"{run}:{name}"
            return None
        mine = [k for (m, k) in table_inputs if m == module]
        for k in mine:
            v = inputs.get(k)
            if isinstance(v, str) and v in st["runs"]:  # a run's ID alone
                inputs[k] = table_of(v, k) or v
        for k in mine:  # given by no run: the build's latest finished run of the module that makes the table
            maker = TABLES_FROM_THE_LATEST_RUN.get((module, k))
            if k in inputs or not maker or any(run_of(v) for v in inputs.values()):
                continue
            done = [r.get("run_id") or r.get("job_id") for r in rc.module_runs(st).get(maker, [])
                    if r["state"] == "done"]
            taken = next((t for t in (table_of(r, k) for r in reversed(done)) if t), None)
            if taken is None:
                raise AnalysisError(f"{module}'s input {k} is a {maker} run's table, and none has ended on this "
                                    f"build: run {called(f'module:{maker}')} first, or give {k}")
            inputs[k] = taken
        runs = [r for r in (run_of(v) for v in inputs.values()) if r]
        for k in mine:
            if k not in inputs and (module, k) in TABLES_FROM_ANOTHER_INPUT:
                taken = next((t for t in (table_of(r, k) for r in runs) if t), None)
                if taken:
                    inputs[k] = taken
        return inputs

    def _enter_cell_sets(self, run_id, folder, before):
        """The cell sets this run's code saved: checked against the screen object and kept (recorded), or refused with
        their problems."""
        from . import cell_sets
        kept, refused = [], []
        for name, (sha, meta) in cell_sets.pending(folder).items():
            if before.get(name, (None,))[0] == sha:
                continue
            try:
                summary = cell_sets.enter(self._screen_dir(), folder, name, run_id)
            except (ValueError, OSError, KeyError) as e:
                refused.append({"name": name, "problems": str(e)})
                continue
            self.record.append("cell_set_saved", summary, rc.TOOL)
            kept.append(summary)
        return kept, refused

    def _enter_blocks(self, run_id, nb, key):
        """The block calls this cell logged (pertura.blocks), entered in the record; and the calls handed to a job,
        started (their handles find the run by its run.json)."""
        import sys
        folder = os.path.join(nb.dir, "blocks")
        log = os.path.join(folder, "calls.jsonl")
        calls, jobs = [], []
        if os.path.exists(log):
            seen = sum(1 for c in self.record.state().get("block_calls", {}).values() if c.get("notebook") == key)
            with open(log, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            for ln in lines[seen:]:
                try:
                    entry = json.loads(ln)
                except ValueError:
                    continue
                self.record.append("block_called", {**entry, "notebook": key, "run_id": run_id}, rc.TOOL)
                calls.append({k: entry.get(k) for k in ("call_id", "block", "targets", "cells")})
        requests = os.path.join(folder, "requests")
        for cid in sorted(os.listdir(requests)) if os.path.isdir(requests) else []:
            d = os.path.join(requests, cid)
            if not os.path.exists(os.path.join(d, "request.json")) or os.path.exists(os.path.join(d, "run.json")):
                continue
            with open(os.path.join(d, "request.json"), encoding="utf-8") as fh:
                req = json.load(fh)
            cmd = [sys.executable, "-m", "pertura.blocks", "job", "--request", d, "--screen", self._screen_dir(),
                   "--design", self._design_path(), "--out", "."]
            from .flow import cells_of
            rid = self.record.start_run(f"pt.{req['block']} on {req['cells']} ({req['call_id']}, a job)", cmd,
                                        [self._screen_dir(), d], self._jobs_root(), name=f"block-{req['block']}",
                                        uses=[cells_of(req)])
            with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
                json.dump({"run_id": rid, "call_id": req["call_id"]}, fh)
            jobs.append({"call_id": req["call_id"], "run_id": rid, "block": req["block"], "n": req["n"]})
        return calls, jobs

    def _cells(self, name):
        """A kept cell set an analysis may use: it exists, and a set that decides guide calls is confirmed."""
        if name is None:
            return None
        sets = self.record.state().get("cell_sets", {})
        info = sets.get(name)
        if info is None:
            raise AnalysisError(f"no kept cell set {name!r}; save_cells in the notebook keeps one (kept: "
                                f"{sorted(sets)})")
        # a labeling that passed the guide-evidence check is used at once (ADR 0026, D2); findings on it say so, and
        # the screen's default calling rule stays the design's (perturbation.assignment, confirmed on the design form)
        return name

    def confirm_calling_rule(self, cell_set, description, elicit=None):
        from .design_service import ANSWER_NAMES, ANSWERS
        self._need()
        info = self.record.state().get("cell_sets", {}).get(cell_set)
        if info is None:
            raise AnalysisError(f"no kept cell set {cell_set!r}")
        if not info["calls"]:
            raise AnalysisError(f"the cell set {cell_set!r} only leaves cells out; it needs no confirmation")
        if elicit is None:
            raise AnalysisError("this client cannot show Pertura's form (it has no MCP elicitation), so the calling "
                                "rule cannot be confirmed from this session. Tell the user")
        samples = ", ".join(f"{k} {v}" for k, v in info["per_sample"].items())
        message = (f"Pertura: please confirm this screen's guide-calling rule. The analysis agent (an AI) proposes "
                   f"the cell set {cell_set!r} as the calls every analysis uses: {description}. It keeps "
                   f"{info['cells']} of the screen object's {info['of']} cells ({samples}) and relabels "
                   f"{info['relabelled']}. The agent's reason: {info['reason']}. The deposit's own calls stay, and "
                   "a finding on this rule is also checked on them. Your answer is recorded as you give it.")
        action, content = elicit(message, {"type": "object", "properties": {
            "answer": {"type": "string", "title": "Guide calls", "enum": ANSWERS, "enumNames": ANSWER_NAMES},
            "note": {"type": "string", "title": "Corrections or comments"}}, "required": ["answer"]})
        if action != "accept" or not isinstance(content, dict):
            return {"confirmed": False, "action": action, "note": "the user did not answer the form"}
        self.record.append("calling_rule_confirmed", {"cell_set": cell_set, "answer": content.get("answer"),
                                                      "description": description, "note": content.get("note", "")},
                           rc.USER)
        return {"confirmed": content.get("answer") == "confirm", "answer": content.get("answer"),
                "note": content.get("note", "")}

    # ---- modules
    def _jobs_root(self):
        root = self.design.cfg.get("runner_root") or os.path.join(self.project, "design", "jobs")
        os.makedirs(root, exist_ok=True)
        return root

    def _design_path(self):
        operator = self.design.cfg.get("operator_design")
        return operator if operator and os.path.exists(operator) else self.design.design_file

    # ---- the module menu (agreed 2026-11-05): code says which modules can run, the module picker (Haiku) picks a few
    # with its reasons, the user ticks the ones to run on a form, and each one chosen starts in the background
    def _menu_facts(self, st):
        from .question_modules import applicability
        done = sorted(m for m, rs in self._module_runs(st).items() if any(r["state"] == "done" for r in rs))
        shifts = None
        if "C.states_and_composition" in done:
            listed = self._candidate_structures(st, complexes=False)
            shifts = (listed.get("targets_shifting_state_shares") or {}).get("count", 0)
        return applicability.facts(self._screen_dir(), self._confirmed(), done, shifts)

    def module_menu(self):
        """The module picker's brief: the modules that can run now with what each returns and its measured run time,
        those that wait for another module, those this screen cannot run with the reason, and the screen's facts, the
        first look's sections, the findings and the open doubts the picks can serve."""
        from .question_modules import REGISTRY, applicability
        self._need()
        if not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
            raise AnalysisError("the menu is read from the screen object: build it first (build_screen_object)")
        st = self.record.state()
        f = self._menu_facts(st)
        minutes = applicability.typical_minutes()
        rows = []
        for r in applicability.menu(f):
            try:  # each analysis as the tool and view that start it; the A modules are the first look's
                r["tool"], r["view"] = kinds.kind_of(r["module"])
            except ValueError:
                continue
            rows.append(r)
        for r in rows:
            t = minutes.get(r["module"])
            r["returns"] = REGISTRY[r["module"]].contract.returns[:300]
            if t:
                r["typical"] = (f"median {t['median_minutes']} min over {t['runs']} finished runs, longest "
                                f"{t['longest_minutes']}" + (f"; {t['stopped_or_failed']} stopped or failed before "
                                                             "finishing" if t["stopped_or_failed"] else ""))
        now = [r for r in rows if r["state"] == "now"]
        self.record.append("module_menu_given", {"modules": [r["module"] for r in now]}, rc.TOOL)
        return {"facts": applicability.facts_words(f), "can_run_now": now,
                "after_another_module": [{k: r[k] for k in ("module", "reason")} for r in rows if r["state"] == "after"],
                "not_for_this_screen": [{k: r[k] for k in ("module", "reason")} for r in rows if r["state"] == "no"],
                "findings": [{"finding_id": fid, "statement": str(x.get("statement") or "")[:240]}
                             for fid, x in st["findings"].items()],
                "open_doubts": [{"doubt_id": did, "text": str(d.get("text") or "")[:240]}
                                for did, d in st["doubts"].items() if not d.get("answer")],
                "next": f"record_module_picks: at most {MENU_PICKS} analyses that can run now, each a tool call "
                        "(tool, view, and its slice, choices or inputs where it needs them) with why: the open "
                        "doubt, unread section or question it answers on this screen"}

    def record_module_picks(self, picks):
        """The module picker's picks: each a tool call {tool, view, and its slice, choices or inputs where it needs
        them} with why; a pick that names a module is its tool and view."""
        from .question_modules import REGISTRY
        self._need()
        if not isinstance(picks, list) or len(picks) > MENU_PICKS:
            raise AnalysisError(f"picks: at most {MENU_PICKS} analyses, each a tool call {{tool, view, why}} with its "
                                "slice, choices or inputs where it needs them; an empty list when nothing on the menu "
                                "answers anything open")
        out = []
        for p in picks:
            if not isinstance(p, dict) or not str(p.get("why") or "").strip():
                raise AnalysisError("each pick says why: the open doubt, unread section or question it answers")
            tool, view = ((p["tool"], p.get("view")) if p.get("tool") else
                          kinds.kind_of(_module_named(p.get("module"), REGISTRY)))
            if tool not in kinds.KINDS:
                raise AnalysisError(f"a pick: no analysis tool {tool!r}; they are {', '.join(kinds.KINDS)}")
            try:
                view, module, _ = kinds.view_of(tool, view)
            except ValueError as e:
                raise AnalysisError(f"a pick: {e}")
            own = kinds.PROMOTED.get(tool, ())
            out.append({"tool": tool, "view": view, "module": module, "why": str(p["why"]).strip(),
                        **{k: p[k] for k in kinds.SLICE + own + ("inputs", "choices") if p.get(k)}})
        self.record.append("module_picks_recorded", {"picks": out}, rc.AGENT)
        return {"picks": out, "next": ("the main agent runs them (offer_analyses), as the user is away"
                                       if self.unattended else "the main agent puts them to the user "
                                                               "(offer_analyses)") if out else
                "nothing goes to the user this time"}

    def ask_module_menu(self, elicit=None):
        """The module picker's latest picks on the user's form, each ticked to run; each one ticked starts in the
        background, and the choice is recorded. Unattended, every pick runs, as the analysis's decision, with no form
        (GSE250558 on a15df12: told the user was away and the picks were the user's to tick, the agent skipped the
        menu)."""
        from .question_modules import applicability
        self._need()
        menus = self.record.state().get("module_menus") or []
        picks = next((m for m in reversed(menus) if m["kind"] == "module_picks_recorded"), None)
        answered = next((m for m in reversed(menus) if m["kind"] == "module_menu_answered"), None)
        if picks is None or not picks["picks"] or (answered is not None and answered["seq"] > picks["seq"]):
            raise AnalysisError("no picks wait for the user: launch the module picker (agents/module-picker.md) "
                                "first")
        if self.unattended:
            action, content = "unattended", {f"run_{i}": True for i in range(len(picks["picks"]))}
        elif elicit is None:
            return {"picks": picks["picks"], "next": "this client shows no form: ask the user in chat which to run, "
                                                     "and run each one they choose with its tool"}
        else:
            minutes = applicability.typical_minutes()
            props = {}
            for i, p in enumerate(picks["picks"]):
                t = minutes.get(p["module"])
                label = f"{p['tool']}, {p['view']}" if p.get("tool") else p["module"]
                props[f"run_{i}"] = {"type": "boolean", "default": True, "title": f"{label}: {p['why']}"
                                     + (f" (about {t['median_minutes']} min)" if t else "")}
            props["note"] = {"type": "string", "title": "Anything else (optional)"}
            action, content = elicit("Pertura: the analysis agent proposes these analyses of your screen. Tick the "
                                     "ones to run; each runs in the background.",
                                     {"type": "object", "properties": props})
            content = content if action == "accept" and isinstance(content, dict) else {}
        chosen = [p for i, p in enumerate(picks["picks"]) if content.get(f"run_{i}")]
        started = []
        for p in chosen:
            try:
                why = f"chosen on the next analyses' form: {p['why']}"
                rid = (self.run_kind(p["tool"], p.get("view"), choices=p.get("choices"), inputs=p.get("inputs"),
                                     wait=0, why=why, **{k: p.get(k) for k in kinds.SLICE},
                                     **{k: p[k] for k in kinds.PROMOTED.get(p["tool"], ()) if k in p})
                       if p.get("tool") else  # a pick recorded before the tools
                       self.run_module(p["module"], choices=p.get("choices"), inputs=p.get("inputs"), wait=0,
                                       why=why)).get("run_id")
                started.append({"module": p["module"], "run_id": rid})
            except AnalysisError as e:
                started.append({"module": p["module"], "error": str(e)})
        self.record.append("module_menu_answered", {"offered": [p["module"] for p in picks["picks"]],
                                                    "chosen": [p["module"] for p in chosen], "action": action,
                                                    "note": str(content.get("note") or "")},
                           rc.AGENT if action == "unattended" else rc.USER)
        return {"chosen": [p["module"] for p in chosen], "started": started, "next": MEANWHILE}

    def _menu_due(self, st):
        """Why the module menu is due, or None: the screen overview is in and no picks were made, or the research lead
        reviewed since the last picks; or the picks wait for the user (unattended: wait to run)."""
        if not any(r["state"] == "done" for r in self._module_runs(st).get(OVERVIEW, [])):
            return None
        menus = st.get("module_menus") or []
        latest = next((m for m in reversed(menus) if m["kind"] == "module_picks_recorded"), None)
        picks = latest["seq"] if latest else None
        answered = next((m["seq"] for m in reversed(menus) if m["kind"] == "module_menu_answered"), None)
        then = "runs them, as the user is away" if self.unattended else "puts them to the user"
        if latest and latest["picks"] and (answered is None or answered < picks):
            return f"the module picker's picks wait: offer_analyses {then}"
        review = (st.get("reviews") or [{}])[-1].get("seq")
        if picks is None or (review is not None and review > picks):
            return ("launch the module picker (agents/module-picker.md) in the background: it reads next_analyses and "
                    f"records its picks; then offer_analyses {then}")
        return None

    next_analyses, offer_analyses = module_menu, ask_module_menu  # their names since docs/adr/0044, D4

    # ---- the analyses by the field's kinds (docs/adr/0044): each tool starts a module on a slice of the cells
    def run_kind(self, tool, view=None, cells=None, by=None, against=None, targets=None, choices=None, inputs=None,
                 wait=RUN_WAIT, why=None, thread_id=None, step=None, **options):
        """A tool's view run as its module on the tool's slice: cells (a kept set, {column: value or values} or
        {state, run}, kept as a set), by, against (the module's reference) and targets (the module's own input where it
        takes one, else the other targets' cells left out of the reading, as the blocks leave them). options: the tool's
        own parameters (kinds.PROMOTED), each its module's choice or input; an input that goes with one value of a
        choice brings that value, unless the call chose it."""
        from .question_modules import REGISTRY
        try:
            view, module, preset = kinds.view_of(tool, view)
        except ValueError as e:
            raise AnalysisError(str(e))
        contract = REGISTRY[module].contract
        own = kinds.PROMOTED.get(tool, ())
        unknown = sorted(set(options) - set(own))
        if unknown:
            raise AnalysisError(f"{tool} has no parameter {unknown}" + (f"; its own are {', '.join(own)}" if own else "")
                                + "; a view's other choices and inputs go in choices and inputs")
        chosen, inputs = dict(choices or {}), {**preset.get("inputs", {}), **(inputs or {})}
        for k, v in options.items():
            if v is None:
                continue
            if k in contract.choices:
                chosen[k] = v
            elif k in contract.inputs:
                inputs[k] = v
            else:
                raise AnalysisError(f"{tool}, view {view} ({module}), takes no {k}; view "
                                    f"{' or '.join(kinds.takers(tool, k))} does")
        choices = {**preset.get("choices", {}), **chosen}
        # a tool's own input brings the one choice value its module reads it with (contrast: gene_sets contrast); an
        # input given in inputs is the module's to check
        for k in (k for k, v in options.items() if v is not None and k in contract.inputs):
            choice, values = contract.scope.get(k, (None, ()))
            if choice and len(values) == 1 and choice not in chosen:
                choices[choice] = values[0]
        if against and "reference" in contract.choices:
            choices["reference"] = against
        elif against and against != "control_guides":
            raise AnalysisError(f"{tool}, view {view} ({module}), is not read against a reference of your choice: "
                                "give it without against")
        if targets:
            targets = [targets] if isinstance(targets, str) else [str(t) for t in targets]
            self._targets_known(targets)
            if "targets" in contract.inputs:
                inputs["targets"], targets = targets, None
            elif choices.get("reference") == "other_targets":
                raise AnalysisError(f"{tool}, view {view}, reads every target against the others: give targets with "
                                    "against control_guides, or read the targets' rows of its table (read_table)")
        out = self.run_module(module, choices or None, inputs or None, wait=wait, cells=self._kept_slice(cells),
                              step=step, why=why, thread_id=thread_id, by=by, targets=targets)
        return {"analysis": f"{tool}, view {view}: {module}", **out}

    def _kept_slice(self, cells):
        """A tool's cells: a kept set's name as given; {column: value or values} of the cells' own columns kept as a
        cell set named for them (as save_cells keeps one), so a finding on the run names the same cells."""
        if not isinstance(cells, dict):
            return cells or None
        import hashlib
        import pandas as pd
        from . import cell_sets
        self._need()
        sd = self._screen_dir()
        if not os.path.exists(os.path.join(sd, "cells.tsv.gz")):
            raise AnalysisError("build the screen object first (build_screen_object)")
        table = pd.read_csv(os.path.join(sd, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False)
        keep = pd.Series(True, index=table.index)
        run, states = cells.get("run"), None
        for col, want in cells.items():
            want = [str(w) for w in (want if isinstance(want, list) else [want])]
            if col == "run":
                continue
            if col in table:
                keep &= table[col].isin(want)
            elif col in ("state", "state_name"):  # a states run's column: its ID or the name it was given
                states = self._states_of(run) if states is None else states
                keep &= table["barcode"].isin(states.loc[states["state"].isin(want)
                                                         | states["state_name"].isin(want), "barcode"])
            else:
                raise AnalysisError(f"cells: no cell column {col!r}; the cells' columns are "
                                    f"{sorted(table.columns)}, or state (with run, a states run; the latest by "
                                    "default)")
        if not keep.any():
            raise AnalysisError(f"cells: no cell has {json.dumps(cells)}")
        barcodes = table.loc[keep, "barcode"]
        words = "; ".join(f"{c} {', '.join(map(str, v if isinstance(v, list) else [v]))}" for c, v in cells.items())
        sha = hashlib.sha256("\n".join(barcodes).encode()).hexdigest()[:8]
        name = (re.sub(r"[^A-Za-z0-9_.-]+", "_", words).strip("_.-")[:55] or "cells") + f"-{sha}"
        if name not in self.record.state().get("cell_sets", {}):
            folder = os.path.join(self.project, "cell_sets_of_the_tools")
            try:
                read = f", the states of run {states.attrs['run']}" if states is not None else ""
                cell_sets.write(folder, name, barcodes, reason=f"the cells with {words}{read}, a tool's cells")
                summary = cell_sets.enter(sd, folder, name, None)
            except (ValueError, OSError) as e:
                raise AnalysisError(f"cells: {e}")
            self.record.append("cell_set_saved", summary, rc.TOOL)
        return name

    def _states_of(self, run=None):
        """The cells' states of a states run (its cell_states: barcode, state, state_name): the run named, else this
        build's latest that ended."""
        import pandas as pd
        if run is None:
            done = [r for r in self._module_runs(self.record.state()).get("C.states_and_composition", [])
                    if r["state"] == "done"]
            if not done:
                raise AnalysisError("cells by state: no states run has ended on this build; run states first, "
                                    "or name a run (run)")
            run = done[-1].get("run_id") or done[-1].get("job_id")
        path = self._run_labelling(run)
        if not path or not path.endswith("cell_states.tsv"):
            raise AnalysisError(f"cells by state: run {run} holds no cell states (a states run's cell_states)")
        out = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=["barcode", "state",
                                                                                    "state_name"])
        out.attrs["run"] = run
        return out

    def _targets_known(self, targets):
        """Targets the screen object has no cell of are refused, with some it has."""
        path = os.path.join(self._screen_dir(), "screen.json")
        if not os.path.exists(path):  # run_module says to build it
            return
        with open(path, encoding="utf-8") as fh:
            labels = json.load(fh).get("labels") or {}
        missing = [t for t in targets if not labels.get(t)]
        if missing:
            have = sorted(t for t, n in labels.items() if n and t not in ("control", "none", "multi"))
            raise AnalysisError(f"targets: no cell of {missing}; the screen's labels are such as {have[:8]}")

    def list_modules(self, question_id=None, name=None):
        from . import question_modules as qm
        if name:
            name = _module_named(name, qm.REGISTRY)
            return {"module": qm.REGISTRY[name].describe(),
                    "knowledge": f"read_knowledge with module {name}: what it asks, how its result can mislead, what "
                                 "to read and do, and what follows"}
        mods = [{"name": m.name, "serves": m.contract.serves, "applies_when": m.contract.applies_when,
                 "recipe": m.recipe is not None}
                for m in qm.REGISTRY.values() if question_id in (None, m.contract.serves)]
        return {"modules": mods, "next": "list_modules with name gives a module's contract and recipe"}

    def run_module(self, module, choices=None, inputs=None, question_id=None, wait=RUN_WAIT, cells=None, step=None,
                   why=None, thread_id=None, by=None, targets=None, source=None):
        import sys
        from . import question_modules as qm
        self._need()
        self._known_thread(thread_id)
        step, step_note = self._plan_step(step)
        self._note_build_quietly()
        self._confirmed()
        module = _module_named(module, qm.REGISTRY)
        bad = sorted(set(choices or {}) - set(qm.REGISTRY[module].contract.choices))
        if bad:
            raise AnalysisError(f"{module} has no choice {bad}; its choices are "
                                f"{qm.REGISTRY[module].contract.choices}")
        bad = sorted(set(inputs or {}) - set(qm.REGISTRY[module].contract.inputs) - {"policy"})  # every module's
        if bad:
            raise AnalysisError(f"{module} has no input {bad}; its inputs are {qm.REGISTRY[module].contract.inputs}")
        try:  # refused now, before a job starts, rather than when it fails
            qm.REGISTRY[module].check({**(choices or {}), **(inputs or {})})
        except ValueError as e:
            raise AnalysisError(str(e))
        self._sets_named(module, choices or {}, inputs or {})
        if module in PAIR_OF_CONDITIONS and not (inputs or {}).get("pair"):
            from . import design_gate
            from .question_modules.e import pair_needed
            names = design_gate.condition_names(
                (design_gate.node(self._confirmed(), "experiment.conditions") or {}).get("value"))
            if len(names) > 2:
                raise AnalysisError(f"{module}: {pair_needed(names)}")
            if len(names) == 2:  # the design's order, given as the survey gives it, so the two calls are one
                inputs = {**(inputs or {}), "pair": list(names)}
        sd = self._screen_dir()
        if not os.path.exists(os.path.join(sd, "screen.json")):
            raise AnalysisError("build the screen object first (build_screen_object)")
        waits = self._cells_wait()
        if waits:  # the cells every module reads are the user's to confirm first (data.qc, after the build)
            raise AnalysisError(f"the cells wait on the user ({waits}): {CELLS_NEXT}")
        self._base_objects()
        given_inputs = inputs
        if module == OVERVIEW:  # the screen overview reads this build's latest runs of its modules, unless given
            inputs = self._overview_inputs(self.record.state(), inputs)
        inputs, used = self._object_inputs(module, inputs or {})
        cmd = [sys.executable, "-m", "pertura.question_modules", "run", module, "--screen", sd,
               "--design", self._design_path(), "--out", ".", "--choices", json.dumps(choices or {}),
               "--inputs", json.dumps(inputs or {})]
        if self._cells(cells):
            cmd += ["--cells", cells]
        if by:
            cmd += ["--by", by]
        if targets:
            cmd += ["--targets", json.dumps(targets)]
        given = {k: v for k, v in (("cells", cells), ("by", by), ("targets", targets), ("choices", choices),
                                   ("inputs", given_inputs)) if v}
        uses = ["screen"] + ([f"cells:{cells}"] if cells else []) + used
        claimed = self._same_comparison(module, given) if source is None else None  # source None: the agent's call
        if claimed:  # this comparison ran on this build: its run is given back (docs/adr/0045, D2 and amendment 1)
            surveys = _the_surveys_own(self.record.state()["runs"][claimed])
            self.record.append("run_claimed", {"run_id": claimed, **{k: v for k, v in (
                ("step", step), ("thread_id", thread_id), ("why", why)) if v}}, rc.TOOL)
            out = self.module_result(claimed, max(0, min(int(wait or 0), RUN_WAIT_MAX)))
            return {**out, **({"from_the_survey": "the survey had made this run on this build; it is yours now"}
                              if surveys else {"from_an_earlier_run": "the same comparison ran on this build as this "
                                                                      "run; it is given back"})}
        rid = self.record.start_run(f"module {module}" + (f" with {json.dumps(given)}" if given else ""), cmd,
                                    [sd], self._jobs_root(), question_id=question_id or
                                    qm.REGISTRY[module].contract.serves, name=module.replace(".", "-"),
                                    uses=uses, step=step, why=why, thread_id=thread_id, source=source)
        out = self.module_result(rid, max(0, min(int(wait or 0), RUN_WAIT_MAX)))
        if step_note:
            out["step_note"] = step_note
        if out.get("state") != "running":
            return out
        estimate = self._estimate(module, choices or {}, sd, (inputs or {}).get("targets"))
        out["next"] = MEANWHILE
        ahead = self._ahead_of(module, rid, choices)
        if ahead:
            out["queued"] = (f"it starts when {', '.join(ahead)} has ended: each of {', '.join(qm.ONE_AT_A_TIME)} runs "
                             "one at a time in a workspace, beside the others; programs found in the cells (cNMF and "
                             "the like) in a queue of their own")
        return {**out, "estimate": estimate} if estimate else out

    def _ahead_of(self, module, run_id, choices=None):
        """The runs a module of ONE_AT_A_TIME waits for: the workspace's other running ones in its queue (queue_of)."""
        from . import question_modules as qm
        queue = qm.queue_of(module, choices)
        if queue is None:
            return []
        return [r for r, x in self.record.state()["runs"].items() if r != run_id and x["state"] == "running"
                and x.get("job_id") and _queue(x) == queue]

    def _estimate(self, module, choices, sd, targets=None):
        """How long a slow method takes, from its measured times (docs/adr/0024, D9), with the time of one target
        alone (C.genes takes targets; its calibration, once computed for the build, is not run again), or None."""
        from .question_modules.c import CALIBRATION_GROUPS
        from .question_modules.data import NOT_TARGETS
        DE_WORKERS = self._job_workers()
        method = choices.get("method", "auto")
        if module == "C.programs" and choices.get("gene_sets") == "cnmf":
            return CNMF_TIME
        if module == "C.genes" and method == "seurat_lr":
            return self._lr_estimate(method, sd, targets)
        if module != "C.genes" or method != "field":
            return None
        with open(os.path.join(sd, "screen.json"), encoding="utf-8") as fh:
            labels = json.load(fh).get("labels") or {}
        n = len(targets) if targets else sum(1 for lab, k in labels.items()
                                             if k >= 10 and lab not in NOT_TARGETS and "|" not in lab)
        n += CALIBRATION_GROUPS
        hours = -(-n // max(DE_WORKERS, 1)) * MAST_SECONDS / 3600
        return (f"about {hours:.1f} hours: {n} comparisons ({n - CALIBRATION_GROUPS} targets and the calibration "
                f"groups), {DE_WORKERS} at a time; MAST took a median of {MAST_SECONDS / 60:.0f} minutes per "
                "comparison on a 76,344-cell screen against 4,337 control cells"
                + self._one_target(n - CALIBRATION_GROUPS, MAST_SECONDS))

    @staticmethod
    def _job_workers():
        """The processes a module job runs its comparisons in: its threads (limits.THREAD_LIMIT), not this server's."""
        from . import limits
        from .question_modules.c import de_workers
        return de_workers(limits.THREAD_LIMIT)

    @staticmethod
    def _one_target(n, seconds):
        """The scoped alternative's time, where more than one target is asked: one comparison once the build's
        calibration is computed, the calibration groups beside it the first time."""
        from .question_modules.c import CALIBRATION_GROUPS
        DE_WORKERS = AnalysisService._job_workers()
        if n <= 1:
            return ""
        first = -(-(1 + CALIBRATION_GROUPS) // max(DE_WORKERS, 1)) * seconds
        return (f". One target alone (inputs targets: [its label]) takes about {seconds / 60:.0f} minutes once the "
                f"calibration is computed (about {first / 60:.0f} the first time): {CALIBRATION_WORDS}")

    def _lr_estimate(self, method, sd, targets=None):
        """C.genes' time where Seurat's LR is asked, for the targets the plan's tests cover; None where there are
        none."""
        from . import design_fit
        from .question_modules.c import CALIBRATION_GROUPS
        from .screen_object import load_design
        DE_WORKERS = self._job_workers()
        if getattr(self, "design", None) is None:  # no project open: the plan's tests are not known
            return None
        try:
            rows = design_fit.plan_leads(sd, load_design(self._design_path()))[
                "genes (C.genes, C.within_states, D.cutting_response)"]
        except (OSError, KeyError, ValueError):
            return None
        n = sum(r["targets"] for r in rows)
        if not n:
            return None
        if targets:
            n = min(n, len(targets))
        n += CALIBRATION_GROUPS
        minutes = -(-n // max(DE_WORKERS, 1)) * LR_SECONDS / 60
        return (f"about {minutes:.0f} minutes: {n} comparisons by Seurat's LR ({n - CALIBRATION_GROUPS} targets and the "
                f"calibration groups), {DE_WORKERS} at a time; LR took about {LR_SECONDS / 60:.0f} minutes per "
                "comparison on a 24,661-cell screen" + self._one_target(n - CALIBRATION_GROUPS, LR_SECONDS))

    def module_result(self, run_id=None, wait=0, run_ids=None, detail=False):
        self._need()
        root = self._jobs_root()
        self.record.sync_runs(root)
        runs = self.record.state()["runs"]
        ids = ([run_id] if run_id else []) + [r for r in (run_ids or []) if r != run_id]
        if not ids:
            raise AnalysisError("give run_id, or run_ids to wait for the first of several to finish")
        for r in ids:
            if r not in runs:  # a guessed ID: name the runs there are, latest first
                recent = [x for x in reversed(list(runs)) if runs[x].get("job_id") or runs[x]["state"] == "running"]
                raise AnalysisError(f"no run {r}; run IDs come from the tools' replies. The latest runs: "
                                    f"{', '.join(recent[:8]) or 'none'} (pertura_status lists all)")
        wait = max(0, min(int(wait or 0), CALL_WAIT_MAX))
        first = self._wait_first(ids, root, wait) if wait else None
        self.record.sync_runs(root)
        runs = self.record.state()["runs"]
        if first is None:
            first = next((r for r in ids if runs[r]["state"] != "running"), ids[0])
        run = runs[first]
        out = {"run_id": first, "state": run["state"], "job_id": run.get("job_id")}  # a notebook cell has no job
        st = self.record.state()
        if rc.on_previous_screen(st, run):
            newer = [r for r, x in st["runs"].items() if x.get("question_id") == run.get("question_id")
                     and x.get("what") == run.get("what") and not rc.on_previous_screen(st, x)]
            out["screen"] = (f"previous: this run read an earlier build, before {rc.what_changed(st)}"
                             + (f"; updating in {newer[-1]}" if newer else ""))
        others = [r for r in ids if r != first and runs[r]["state"] == "running"]
        if others:
            out["still_running"] = others
        # the other runs that have ended come opened in the same reply (_with_ended)
        if run["state"] != "running":
            self._opened.add(first)
        if run["state"] == "running":
            out["running_seconds"] = self._running_seconds(run)
            said = self._progress(run)
            if said:
                out["progress"] = said
            return {**out, "next": f"{MEANWHILE}. run_result waits at most {CALL_WAIT_MAX} s; stop_run ends a run "
                                   "no longer needed"}
        res = os.path.join(root, first, "result.json")
        from . import result_states
        out["run"] = result_states.run_state(run["state"])  # item 24.3: running, done or failed
        if os.path.exists(res):
            with open(res, encoding="utf-8") as fh:
                out.update(json.load(fh))
            out.update(result_states.result_words(out))  # found, not found, measured or not testable (why)
            module = _module_and_choices(run)[0]
            if out["result"] in ("not found", "not testable") and module in qm_registry():
                out["if_it_misleads"] = [f"{how}: {check}"  # docs/adr/0045, amendment 1
                                         for how, check in qm_registry()[module].contract.fails_when]
            out["folder"] = os.path.join(root, first)
            nxt = (self._published_next(run.get("question_id"), _module_and_choices(run)[1].get("choices"))
                   if out["result"] == "found" else [])
            if nxt:
                out["published_next"] = nxt
            self._tag_knowledge(first, out)  # what to do with the maps is the state list's, once (docs/adr/0050, D2)
            fig = out.get("figure")
            if isinstance(fig, str) and fig:
                images, _ = self._images([fig if os.path.isabs(fig) else os.path.join(out["folder"], fig)])
                if images:
                    out["_images"] = [{**images[0], "about": f"{RUN_FIGURE}{first} ({run.get('what') or 'a run'})"}]
                    out["figure_shown"] = True
        else:
            out["output"] = run.get("output")
        if str(run.get("what") or "").startswith(CHECKS_RUN):  # a checks job: what its checks say of the finding
            out.update(self._verdict_row(run["what"][len(CHECKS_RUN):]))
        if others:
            out["next"] = f"still running: {others}; each reply opens a run once it has ended"
        return out if detail else _concise(out)

    run_result = module_result  # its name since docs/adr/0044, D3

    def read_knowledge(self, ids=None, module=None, words=None, query=None, kind=None):
        if query is not None:
            return self._paper_knowledge(query, kind)
        if kind is not None:
            raise AnalysisError("give query with kind: analysis, proceed or figure")
        from . import knowledge
        out = knowledge.read_reply(ids, module, words)
        if module:  # the tool calls that run it, and what each choice and input takes, as its contract says
            from .question_modules import REGISTRY
            try:
                name = _module_named(module, REGISTRY)
            except AnalysisError:
                return out
            c = REGISTRY[name].contract
            out["run_by"] = [f"{t}, view {v}" for t, v in kinds.places(name)] or ["code, in the first look"]
            out["choices"], out["inputs"] = c.choices, {k: v for k, v in c.inputs.items()
                                                          if k not in kinds.HIDDEN_INPUTS}
            if kinds.places(name):
                papers = self._paper_knowledge("analysis", kinds.kind_of(name)[0])
                if papers["examples"]:
                    out["published_analysis"] = papers
        return out

    def wait_for_runs(self, run_ids=None, minutes=WAIT_MINUTES, subagent=False):
        """The first of the runs to end (every running run by default), waiting up to minutes; the runs that ended,
        each with its summary, and those still running, with their progress. Waiting only reads. A subagent's wait
        (subagent, as the plugin's hooks mark it) neither takes over the main agent's nor marks its runs said."""
        from . import runner
        self._need()
        root = self._jobs_root()
        self.record.sync_runs(root)
        runs = self.record.state()["runs"]
        unknown = [r for r in run_ids or [] if r not in runs]
        if unknown:
            raise AnalysisError(f"no run {unknown}; pertura_status lists the runs")
        def in_flight(rs):
            return [r for r, x in rs.items() if x["state"] == "running" and x.get("job_id")]
        ids = list(run_ids or in_flight(runs))
        minutes = max(0, min(int(minutes if minutes is not None else WAIT_MINUTES), WAIT_MINUTES_MAX))
        if not ids:
            return self._wait_for_the_design(minutes)
        jobs = [runs[r]["job_id"] for r in ids if runs[r]["state"] == "running" and runs[r].get("job_id")]
        turn, taken_over = None, False
        if jobs and len(jobs) == len(ids):  # every one still running: wait for the first to end
            if not run_ids and not subagent:  # one wait at a time: this one takes over from an earlier one, whose
                # caller may have stopped listening (GSE203592: a wait of 01:10 still held, and the agent's next waits
                # were refused while the Stop hook asked for one)
                self._wait_turn += 1
                turn = self._wait_turn
            t0, polls = time.monotonic(), 0
            while time.monotonic() - t0 < minutes * 60:
                polls += 1
                if not run_ids and polls % 6 == 0:  # every 30 s, the runs started since are watched too
                    now = self.record.state()["runs"]
                    ids += [r for r in in_flight(now) if r not in ids]
                    jobs = [now[r]["job_id"] for r in ids if now[r].get("job_id")]
                try:
                    if any(runner.status(j, root)["state"] not in runner.ACTIVE for j in jobs):
                        break
                except KeyError:
                    break
                if turn is not None and turn != self._wait_turn:
                    taken_over = True
                    break
                if self._stop_waits.wait(5.0):
                    break
        self.record.sync_runs(root)
        runs = self.record.state()["runs"]
        ended, running = [], []
        for r in ids:
            if runs[r]["state"] == "running":
                said = self._progress(runs[r])
                running.append({"run_id": r, "what": runs[r].get("what"), **({"progress": said} if said else {})})
                continue
            row = {"run_id": r, "what": runs[r].get("what"), "state": runs[r]["state"]}
            res = os.path.join(root, r, "result.json")
            if os.path.exists(res):
                try:
                    with open(res, encoding="utf-8") as fh:
                        row["summary"] = json.load(fh).get("summary")
                except (OSError, ValueError):
                    pass
            ended.append(row)
            if not subagent:
                self._announced.add(r)
        images = []
        for row in [] if subagent else ended:  # each run that ended comes back opened, with its figure
            if row["run_id"] not in self._opened:
                shown = self.module_result(row["run_id"])
                images += shown.pop("_images", None) or []
                # what the row says already (its run, state and summary) is not said again: 15% of the ended
                # entries on GSE190604 (a9510b4) were such copies
                row["result"] = {k: v for k, v in shown.items() if k != "next" and row.get(k) != v}
        out = {"ended": ended, "still_running": running,
               **({"taken_over": "a newer wait watches the runs now; its reply comes when the first ends"}
                  if taken_over else {})}
        return {**out, "_images": images} if images else out

    def _wait_for_the_design(self, minutes):
        """With no module run in flight: the design's work in the background (the measurement, the screen object's
        build), waited for until the first of them ends, up to minutes."""
        work = self.design.in_background() if isinstance(self.design, DesignService) else []
        if not work:
            return {"ended": [], "still_running": [], "note": "no run is running, and neither the measurement nor "
                                                              "the screen object's build; a worker's reply "
                                                              "comes by itself when it ends"}
        t0 = time.monotonic()
        while time.monotonic() - t0 < minutes * 60 and not any(w["done"]() for w in work):
            if self._stop_waits.wait(5.0):
                break
        ended = [w for w in work if w["done"]()]
        rows = [{k: w[k] for k in ("what", "started", "job_id") if w.get(k)} for w in work]
        nxt = {"the measurement": "measure gives the measurement",
               "the screen object's build": "build_screen_object gives the build's summary, or its log where it "
                                            "failed"}
        return {"ended": [r for r, w in zip(rows, work) if w in ended],
                "still_running": [r for r, w in zip(rows, work) if w not in ended],
                "next": "; ".join(nxt[w["what"]] for w in ended) if ended else "wait_for_runs again waits for it"}

    def stop_waiting(self):
        """The session's input has ended: every wait returns now, so the server closes without leftovers."""
        self._stop_waits.set()

    def _wait_first(self, ids, root, timeout):
        """The first of the runs to leave the running states, waiting up to timeout seconds (None if none did).
        Waiting only reads."""
        from . import runner
        t0 = time.monotonic()
        while True:
            for r in ids:
                try:
                    if runner.status(r, root)["state"] not in runner.ACTIVE:
                        return r
                except KeyError:  # not a job (a notebook cell): nothing to wait for
                    return r
            if time.monotonic() - t0 >= timeout:
                return None
            time.sleep(2.0)

    def _images(self, paths):
        """PNG figures as MCP image items, each at most IMAGE_MAX_BYTES and named by its file; ([items], [paths left
        out])."""
        import base64
        items, left = [], []
        for p in paths:
            if p and os.path.exists(p) and p.lower().endswith(".png") and os.path.getsize(p) <= IMAGE_MAX_BYTES:
                with open(p, "rb") as fh:
                    items.append({"data": base64.b64encode(fh.read()).decode("ascii"), "mimeType": "image/png",
                                  "about": os.path.basename(p)})
            else:
                left.append(p)
        return items, left

    def _figure_png(self, fig):
        """A recorded figure's PNG on this server: its path as recorded, or under its run's folder or the project."""
        p = ((fig or {}).get("paths") or {}).get("png")
        if not p:
            return None
        for cand in ([p] if os.path.isabs(p) else [os.path.join(self._jobs_root(), str(fig.get("run_id")), p),
                                                   os.path.join(self.project, p), p]):
            if os.path.exists(cand):
                return cand
        return p

    def _report_page(self, page=1):
        """The written report's text, a page of REPORT_PAGE characters."""
        path = os.path.join(self.project, "report", "report.md")
        if not os.path.exists(path):
            raise AnalysisError("no report yet: write_report writes it")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        pages = max(1, -(-len(text) // REPORT_PAGE))
        page = min(max(1, int(page)), pages)
        out = {"path": path, "page": page, "pages": pages,
               "text": text[(page - 1) * REPORT_PAGE: page * REPORT_PAGE]}
        if page < pages:
            out["next"] = f"read_record with what report and ids [{page + 1}] gives the next page"
        return out

    def _running_seconds(self, run):
        from . import runner
        try:
            created = runner.status(run.get("job_id") or run["run_id"], self._jobs_root())["created"]
            return int(time.time() - time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")))
        except (KeyError, ValueError, TypeError):
            return 0

    # ---- outside knowledge (question G): always recorded as prior knowledge
    def _sets_named(self, module, choices, inputs):
        """A collection's sets named by select, checked against the collection before the run waits in its queue: on
        GSE190604 (a9510b4) a C.programs naming hallmark sets without their prefix failed after 18 minutes in the
        queue."""
        from . import gene_sets as gs
        collection, select = choices.get("gene_sets"), inputs.get("select")
        if module != "C.programs" or collection not in gs.NAMED or not isinstance(select, (list, tuple)):
            return
        try:
            names = set(gs.msigdb(collection, self._organism()))
        except (AnalysisError, ValueError, OSError) as e:  # the collection unread here: the run itself says why
            _logged(e)
            return
        missing = [n for n in select if n not in names]
        if missing:
            near = {n: sorted(x for x in names if x.endswith("_" + n) or n.lower() in x.lower())[:2] for n in missing}
            raise AnalysisError(f"no set {missing} in {collection}; near names: {near}; the gene_sets tool lists a "
                                "collection's sets")

    def _organism(self):
        """The organism the user confirmed. Gene sets need it and nothing else of the design, so they are read while
        other items are still open (development session 9)."""
        from . import design_gate
        path = self._design_path()
        try:
            design = design_gate.load(path)[0] if os.path.exists(path) else {}
        except Exception as e:  # an unreadable design confirms nothing
            _logged(e)
            design = {}
        n = design_gate.node(design, "experiment.organism") or {}
        org = n.get("value") if n.get("status") == "confirmed" else None
        if org not in ("human", "mouse"):
            raise AnalysisError("gene sets need the design's organism confirmed (experiment.organism: human or mouse; "
                                "the confirmation form asks it)")
        return org

    def gene_sets(self, collection="hallmark", name=None, gene=None, match=None, question_id=None):
        from . import gene_sets as gs
        self._need()
        if not (name or gene or match):
            raise AnalysisError("give a set's name, a gene, or a word in the sets' names (match)")
        sets = gs.msigdb(collection, self._organism())
        if match and not (name or gene):  # names to choose from, for C.programs' select: no genes, no prior knowledge
            found = {k: len(v) for k, v in sets.items() if match.lower() in k.lower()}
            return {"sets_and_sizes": dict(list(found.items())[:200]), "sets": len(found),
                    **({"shown": 200} if len(found) > 200 else {})}
        if name:
            if name not in sets:
                raise AnalysisError(f"no set {name} in {collection}")
            out, item = {name: sets[name]}, f"the genes of {name}"
        else:
            out, item = {k: v for k, v in sets.items() if gene in v}, f"the {collection} sets holding {gene}"
        how = " (human sets translated through HCOP)" if gs.translated(collection, self._organism()) else ""
        self.record.append("knowledge_recorded", {"question_id": question_id or "G.meaning",
                                                  "source": f"MSigDB {collection} ({self._organism()}){how}",
                                                  "item": item,
                                                  "text": ", ".join(sorted(out))}, rc.TOOL)
        return {"sets": out, "tag": rc.PRIOR}

    def prior_screens(self, targets, references=None, neighbours=5):
        """Published screens and complexes for targets (pertura.prior_screens, docs/adr/0051 D9), recorded as prior
        knowledge."""
        from . import prior_screens as ps
        self._need()
        rows = ps.look_up(list(targets), references, int(neighbours), self._organism())
        held = sorted({r["target"] for r in rows if r.get("in_reference")})
        self.record.append("knowledge_recorded", {"question_id": "E.similar_targets",
                                                  "source": ps.SOURCE + "; CORUM (Zenodo 17419058)",
                                                  "item": f"published screens and complexes of {len(targets)} targets",
                                                  "text": f"held in a reference: {', '.join(held) or 'none'}"},
                           rc.TOOL)
        return {"rows": rows, "tag": rc.PRIOR}

    def search_literature(self, query, max_results=10):
        """Papers for a query (pertura.literature), less those the workspace keeps out (literature_excluded: a test
        session's own screen's papers); the search and the papers it returned are recorded as prior knowledge."""
        from . import literature
        self._need()
        try:
            found = literature.search(query, max_results, self.cfg.get("literature_excluded"))
        except literature.NoAnswer as e:  # recorded, so nothing is called new on a search that never ran (ADR 0047, A4)
            self.record.append("knowledge_recorded", {"question_id": "literature", "source": "Europe PMC",
                                                      "item": f"search failed: {str(query or '').strip()}",
                                                      "text": f"not searched: {e}"}, rc.TOOL)
            raise AnalysisError(f"{e}. The search did not run, so it is not a search that found nothing: search "
                                "again before anything rests on it")
        except literature.LiteratureError as e:
            raise AnalysisError(str(e))
        self.record.append("knowledge_recorded", {"question_id": "literature", "source": "Europe PMC",
                                                  "item": f"search: {found['query']}",
                                                  "text": ", ".join(p["id"] for p in found["papers"]) or "none"},
                           rc.TOOL)
        return {**found, "tag": rc.PRIOR,
                "next": "put what you use beside a finding or a thread (prior: {text, source}, the source its PMID or "
                        "DOI; the search itself, 'search: <query>', where it found nothing); it informs and never "
                        "changes a finding"}

    def record_knowledge(self, question_id, source, citation, text):
        self._need()
        self.record.append("knowledge_recorded", {"question_id": question_id, "source": source, "item": citation,
                                                  "text": text}, rc.AGENT)
        return {"recorded": True, "tag": rc.PRIOR}

    def _tag_knowledge(self, run_id, res):
        """A finished run of a G module is outside knowledge: its result is marked so, and it is recorded once as
        prior knowledge with the sets it found."""
        import pandas as pd
        run = self.record.state()["runs"].get(run_id, {})
        if not str(run.get("question_id", "")).startswith("G.") or not res.get("summary"):
            return
        res["tag"] = rc.PRIOR
        if run_id in {k.get("run_id") for k in self.record.state()["knowledge"]}:
            return
        table = os.path.join(self._jobs_root(), run_id, "result.tsv")
        found = []
        if os.path.exists(table) and os.path.getsize(table) > 1:
            t = pd.read_csv(table, sep="\t")
            if {"set", "q"} <= set(t.columns):
                found = t.loc[t["q"] < 0.05, "set"].astype(str).tolist()
            elif {"target", "source", "q"} <= set(t.columns):  # activities: each target's sources past its line
                hit = t["q"] < 0.05
                if "band" in t.columns and t["band"].notna().any():
                    hit &= t["past_band"].astype(bool)
                found = (t.loc[hit, "target"].astype(str) + ": " + t.loc[hit, "source"].astype(str)).tolist()[:200]
        self.record.append("knowledge_recorded", {"question_id": run["question_id"], "source": run["what"],
                                                  "item": res["summary"], "text": ", ".join(found) or res["summary"],
                                                  "run_id": run_id}, rc.TOOL)

    def propose_validation(self, finding_ids=None, wait=60):
        raise AnalysisError("the module H.what_next has left the product: propose the validation in the report, "
                            "from each lead's checks and the paradigm cards' last step")

    def read_table(self, run_id, name, rows=50, where=None, columns=None):
        import pandas as pd
        self._need()
        t = pd.read_csv(self._table_file(run_id, name), sep="\t")
        try:
            t = rows_where(t, where)
        except ValueError as e:
            raise AnalysisError(f"table {name}: {e}") from e
        every = list(t.columns)
        if columns:
            missing = [c for c in columns if c not in t]
            if missing:
                raise AnalysisError(f"table {name} has no column {missing}; its columns are {every}")
            t = t[list(columns)]
        n = int(rows)
        out = {"columns": list(t.columns), "rows_total": int(len(t)), "rows": _records(t.head(n))}
        while n > 1 and len(json.dumps(out, default=str)) > TABLE_MAX:
            n = max(1, n // 2)
            out["rows"] = _records(t.head(n))
        if len(json.dumps(out, default=str)) > TABLE_MAX:  # one row too wide: the columns asked for are needed
            out["rows"] = []
        if len(out["rows"]) < min(int(rows), len(t)):
            out["note"] = (f"cut to {len(out['rows'])} rows to stay under {TABLE_MAX} characters; name the columns you "
                           f"need (columns) or filter (where). Its columns: {every}")
        return out

    def _table_file(self, run_id, name):
        """A module run's table, or any table the record holds as an object (a cell's table:<name>, a run's
        <run>:<table>, a block call's table), named by name, by run_id, or by the two together, as pt.load reads it."""
        from . import flow
        folder = os.path.join(self._jobs_root(), str(run_id))
        path = os.path.join(folder, f"{name}.tsv")
        if os.path.exists(path):
            return path
        objs = flow.objects(self.record.state())
        for oid in (str(name), str(run_id), f"table:{name}", f"{run_id}:{name}"):
            p = (objs.get(oid) or {}).get("path")
            if p and os.path.isdir(p) and os.path.exists(os.path.join(p, "table.tsv.gz")):  # a kept block result
                p = os.path.join(p, "table.tsv.gz")
            if p and os.path.isfile(p) and p.endswith((".tsv", ".tsv.gz")):
                return p
        runs = self.record.state()["runs"]
        if not os.path.isdir(folder) and str(run_id) not in runs:  # a guessed ID was told it "has no table" (P7)
            recent = [x for x in reversed(list(runs)) if runs[x].get("job_id")]
            raise AnalysisError(f"no run {run_id}; run IDs come from the tools' replies. The latest module runs: "
                                f"{', '.join(recent[:8]) or 'none'} (pertura_status lists all)")
        have = sorted(f[:-4] for f in os.listdir(folder) if f.endswith(".tsv")) if os.path.isdir(folder) else []
        tables = sorted(o for o, x in objs.items() if x.get("kind") == "table" and o.startswith("table:"))
        raise AnalysisError(f"run {run_id} has no table {name}; it has {have}"
                            + (f". The tables the notebook saved: {tables[:20]}" if tables else ""))

    def read_record(self, what="all", ids=None, page=None):
        """The record's entries: in one line each without ids, in full with them; in pages of READ_MAX characters,
        the latest first (P9: Claude Code refused 123 longer replies, 116 of them a subagent's). The report comes in
        its own pages."""
        out = self._read_record(what, ids)
        return out if what == "report" else _paged(out, page)

    def _read_record(self, what="all", ids=None):
        from . import flow
        self._need()
        st = self.record.state()
        if what == "chain":
            if not ids:
                raise AnalysisError("a chain needs ids: the objects, findings, results (dossiers) or runs to trace")
            return {"chain": flow.chain(st, flow.starts(st, ids))}
        if what == "report":
            return {"report": self._report_page(int(ids[0]) if ids else 1)}
        if what == "overview":
            self.record.sync_runs(self._jobs_root())
            return {"overview": self._routine_rows(self.record.state())}
        if what == "map":  # item 27: the screen's objects, the relations the findings show, what is not explained
            from . import screen_map
            return {"map": screen_map.build(st)}
        if what == "coverage":  # what the record leaves open, beside it (once the research lead's view)
            return {"coverage": self._coverage_beside_the_record(st)}
        if what == "notebook":  # docs/adr/0039, D1: the main line, cell by cell, as the agent's memory
            return {"notebook": self._notebook_cells(st)}
        parts = {"questions": st["questions"], "findings": st["findings"], "threads": st.get("threads", {}),
                 "results": st.get("results", {}), "runs": st["runs"],
                 "doubts": {**st["doubts"], **{f"branch {k}": v for k, v in st["branches"].items()}},
                 "knowledge": {str(k["seq"]): k for k in st["knowledge"]}, "figures": st["figures"],
                 "cell_sets": st.get("cell_sets", {}), "block_calls": st.get("block_calls", {}),
                 "objects": flow.objects(st)}
        if what not in list(parts) + ["all"]:
            raise AnalysisError("what is one of " + ", ".join(list(parts) + ["chain", "report", "overview", "map",
                                                                             "coverage", "notebook", "all"]))
        out = parts if what == "all" else {what: parts[what]}
        if ids:
            out = {k: {i: v for i, v in part.items() if i in ids} for k, part in out.items()}
        if "results" in out:  # what each rests on, counted (docs/adr/0039, D2), on a copy of the state's entries;
            # its earlier versions by their titles (a result reached 22-28 versions, each with its text)
            out["results"] = {i: {**r, "rests_on": rc.result_facts(st, r),
                                  "versions": [{"version": v.get("version"), "title": v.get("title")}
                                               for v in r.get("versions") or []]}
                              for i, r in out["results"].items()}
        for run in out.get("runs", {}).values():  # the output's end is what a reader needs
            if run.get("output") and len(run["output"]) > 3000:
                run["output"] = "..." + run["output"][-3000:]
            if rc.on_previous_screen(st, run):
                run["screen"] = "previous: it read an earlier build of the screen object, which a rebuild changed"
            from . import result_states
            run["run"] = result_states.run_state(run["state"])  # item 24.3
        earlier = rc.previous_screen_findings(st)
        for fid, f in out.get("findings", {}).items():
            if fid in earlier:
                f["screen"] = f"previous: it rests on runs of an earlier build ({', '.join(earlier[fid])})"
        if not ids:  # the entries in one line: the reader asks for the full ones it needs by their IDs
            out = {k: {i: in_brief(v, i) for i, v in part.items()} if isinstance(part, dict) else part
                   for k, part in out.items()}
            out["in_brief"] = "each entry in one line: read_record with what and ids gives the full entries"
        if what == "figures":  # the figures themselves, for a reviewer on any host (problem 24); without ids the
            # results' own, the set a reviewer reads (development run on GSE203592: the set's review saw no image)
            from .results_report import results_of
            shown = ids or list(dict.fromkeys(r["figure"] for r in results_of(st) if r.get("figure")))
            papers = self._figure_knowledge(st, shown)
            if papers:
                out["published_figures"] = papers
            images, left = self._images([self._figure_png(st["figures"].get(i)) for i in shown if i in st["figures"]])
            if images:
                out["_images"] = images
                out["images_shown"] = len(images)
            if left:
                out["images_left_out"] = [p for p in left if p]
        return out

    def null_of_right_unit(self, finding_id, wait=60):
        """The finding's calibration on the control guides, run as its calibration check (pertura.finding_checks)."""
        self._need()
        out = self.check_finding(finding_id, only=["calibration"])
        return {**out, "next": "read_record shows the finding's calibration check when its run ends"}

    def _block_call(self, call_id):
        call = self.record.state().get("block_calls", {}).get(call_id)
        if call is None:
            raise AnalysisError(f"no recorded pt call {call_id!r}; read_record shows the calls")
        return call

    def read_run(self, run_id, reading):
        self._need()
        self.record.append("run_read", {"run_id": run_id, "reading": reading}, rc.AGENT)
        return {"run_id": run_id}

    # ---- findings, doubts, branches
    def record_finding(self, finding_id, question_id=None, target=None, feature=None, direction=None, population=None,
                       run_ids=None, kind=None, effect=None, genes=None, state_run=None, block_call=None, cells=None,
                       reference=None, statement=None, figure=None, thread_id=None, prior=None, differs=None,
                       relates=None, targets=None):
        """A finding (docs/adr/0032, D6): what was found, in a sentence, the runs it rests on and its figure. With a
        target (or several: targets), a feature, a direction and a kind, its checks (docs/adr/0025, section 3) start as
        a job: the lead test and the checks the design chooses, for each target, each recorded by the tool in its own
        terms. The arguments keep their earlier order, so a structured finding is recorded as before. cells take the
        analysis tools' forms (docs/adr/0050, D4: on A-cut a within-state number from the notebook needed a step more
        to become a claim, and none did)."""
        self._need()
        if run_ids is None:
            raise AnalysisError("a finding names the runs it rests on (run_ids)")
        cells = self._kept_slice(cells) if isinstance(cells, dict) else cells
        if any(ch in str(finding_id) for ch in ("/", "\\", ":")):  # it names the checks' files
            raise AnalysisError("a finding_id is a name: letters, digits, '_', '-' or '.', without '/', '\\' or ':'")
        if direction is not None and direction not in ("up", "down"):
            raise AnalysisError("direction is up or down: the feature higher or lower in the target's cells than in "
                                "the reference (a state's share, a set's score, a gene's expression)")
        from . import screen_map
        bad = screen_map.check_relates(relates) + screen_map.check_relates((prior or {}).get("relates")
                                                                           if isinstance(prior, dict) else None)
        if bad:
            raise AnalysisError("; ".join(dict.fromkeys(bad)))
        named = list(targets) if targets else [target] if target else []
        checkable = bool(named and feature and direction)
        if not checkable and not str(statement or "").strip():
            raise AnalysisError("a finding says what was found, in a sentence (statement); with a target, a feature "
                                "and a direction, its checks run")
        if checkable and kind is None:
            raise AnalysisError("a finding with a target, a feature and a direction names its kind: gene, gene_set, "
                                "state, score, interaction or custom")
        if checkable and kind == "state" and not state_run and not block_call:  # the one states run it rests on
            labelled = [r for r in run_ids if self._run_labelling(r) is not None]
            if len(labelled) == 1:  # run A: the main agent's f4 was refused for want of it (docs/adr/0050, D6)
                state_run = labelled[0]
        parts = {"cells": cells, "reference": reference}
        for t in named if checkable else []:  # each target as the first: one wrong refuses the finding
            parts = self._finding_parts(t, kind, genes, state_run, block_call, cells, reference, feature)
        beside = self._beside_events(finding_id, prior, differs)
        choices = self._choices_of(run_ids)
        self.record.append_many([("finding_recorded", {
            "finding_id": finding_id, "statement": str(statement).strip() if statement else None, "figure": figure,
            "question_id": question_id, "target": named[0] if named else None, "feature": feature,
            "direction": direction, **({"targets": named} if len(named) > 1 else {}),
            "population": population or ("all" if checkable else None), "effect": effect, "run_ids": run_ids,
            "kind": kind, "genes": list(genes) if genes else None, "state_run": state_run, "cells": parts["cells"],
            "reference": parts["reference"], "block_call": block_call, "thread_id": thread_id,
            **({"score_table": parts["score_table"]} if parts.get("score_table") else {}),
            **({"relates": list(relates)} if relates else {}), **({"choices": choices} if choices else {})},
            rc.AGENT)] + beside)  # all or nothing
        shown = {} if figure else {"figure": "none named: the user's page shows the latest figure of its runs"}
        if choices:  # the choices it rests on, with the alternatives a worker may try (the open points' F5)
            shown["rests_on_choices"] = choices
        if differs is not None:  # the skeptic's and the lead's items are the state list's (docs/adr/0050, D2)
            known = self._known_now(finding_id=finding_id)
            if known:
                shown["known"] = known
        if not checkable:
            if named:  # on GSE278572 (01f3de3) a worker recorded six claims on named targets as descriptions
                shown["to_start_its_checks"] = (f"it names {', '.join(named)}: a claim on a gene, a set or a state, "
                                                "with a direction, takes that kind, its feature and its direction "
                                                "(reword_finding with target, feature, direction and kind), and its "
                                                "checks run; a pattern or a count stays a description")
            return {"finding_id": finding_id, "checks": "none: a finding's checks need a target (or pair), a feature "
                                                        "and a direction, with its kind",
                    "a_claim_gets": (f"{self._checks_cover()}, each where the design allows: a claim (its target or "
                                     "targets, a feature, a direction and its kind) gets these; a description none"),
                    **shown}
        f = {"target": named[0], "targets": named, "feature": feature, "direction": direction,
             "question_id": question_id}
        out = {"finding_id": finding_id, "kind": kind, **self._start_checks(finding_id, f, parts), **shown}
        if kind in CHECKED_KINDS:  # what they cover, so a claim is not checked again by hand (A-cut redid them)
            out["its_checks"] = (f"{self._checks_cover()}: each where the design allows, run in code; run_result "
                                 "with checks_run gives each in its own terms, and names any that cannot run for "
                                 "this finding, with why. Do not run them again by hand; go further only for an "
                                 "explanation they miss")
        if kind == "custom":
            kinds = self._kinds_with_checks(run_ids, block_call)
            if kinds:
                out["kinds_with_checks"] = kinds
                out["next"] = ("recorded as custom, so no check runs. It rests on runs whose kind the tool checks: "
                               "reword it in place with that kind (reword_finding with kind), and its checks run (each "
                               "guide, the other test, the calibration, depth matched)")
        return out

    def _choices_of(self, run_ids):
        """The choices a finding rests on, read from the module runs it names: each choice of the module's contract
        with the value the run took (its default where it named none) and the others as alternatives, and the cells
        it ran on. [{choice, value, module, alternatives}]."""
        from . import question_modules as qm
        st = self.record.state()
        out, seen = [], set()
        for rid in run_ids or []:
            module, given = _module_and_choices(st["runs"].get(rid) or {})
            if module not in qm.REGISTRY:
                continue
            contract = qm.REGISTRY[module].contract
            for k, values in contract.choices.items():
                if (module, k) in seen or len(values) < 2:
                    continue
                seen.add((module, k))
                value = given.get("choices", {}).get(k, qm.REGISTRY[module].default(k))  # unnamed: its own
                out.append({"choice": k, "value": value, "module": module,
                            "alternatives": [v for v in values if v != value]})
            if (module, "cells") not in seen:
                seen.add((module, "cells"))
                out.append({"choice": "cells", "value": given.get("cells") or "the cells the quality rule keeps",
                            "module": module, "alternatives": []})
        return out

    def _open_at_the_end(self):
        """What the record still holds open, as stop-facts reads it: the findings whose checks are queued or running,
        the doubts with no answer, the parts of the question and the declared choices still open, each as its IDs;
        only the lists with something in them."""
        from .recording import stop_facts
        facts = stop_facts(self.record.folder, self._jobs_root())
        return {k: [x.get("id") or x.get("thread_id") if isinstance(x, dict) else x for x in facts[k]]
                for k in ("checks_running", "doubts_open", "choices_open") if facts.get(k)}

    def note_reply(self, kind, words, finding_id=None, thread_id=None, by="user", option=None):
        """The user's reply to a finding (or, for enough and knowledge, to the analysis), in their words as the agent
        relays them from the chat (docs/adr/0032, D6). by 'main agent', with kind main_line: the main agent moves the
        main line itself, its words its reason (docs/adr/0050, D3: the direction is the main agent's and the user's).
        next_step: the user's pick among a result's next steps (option, its rank), or their own next step without
        option (_next_step_taken). Unattended, enough is the analysis's own, taken when the record holds nothing open
        (_open_at_the_end)."""
        self._need()
        words = str(words or "").strip()
        own = by == "main agent"
        if by not in ("user", "main agent") or (own and kind != "main_line"):
            raise AnalysisError("by is the user (their words, relayed) or the main agent, whose own reason moves the "
                                "main line (kind main_line)")
        mine = self.unattended  # unattended, the words are the analysis's own reason, not the user's
        if kind == "next_step":
            return self._next_step_taken(words, option, mine)
        left = self._open_at_the_end() if kind == "enough" and mine else {}
        if left:  # unattended, the analysis ends when the record holds nothing open (the lead's complete, retired)
            raise AnalysisError("unattended, the analysis ends when the record holds nothing open; still open: "
                                + "; ".join(f"{k.replace('_', ' ')}: {', '.join(map(str, v))}" for k, v in left.items()))
        if kind == "main_line" and thread_id not in (self.record.state().get("threads") or {}):
            raise AnalysisError("main_line moves the main line to a thread: give its thread_id, with the user's words "
                                "(or, by main agent, your reason)")
        reply = ("reply_recorded", {"kind": kind, "words": words, "finding_id": finding_id, "relayed": not mine,
                                    **({"thread_id": thread_id} if thread_id else {}),
                                    **({"by_analysis": True} if mine else {})}, rc.AGENT)
        moved = ("main_line_moved", {"thread_id": thread_id, "why": words, "by": "main agent" if own else "user",
                                     **({} if own else {"user_words": words}), **self._branch_now(thread_id)},
                 rc.AGENT)  # the main agent's own move is no reply of the user's
        self.record.append_many(([] if own else [reply]) + ([moved] if kind == "main_line" else []))
        out = {"recorded": kind, "finding_id": finding_id}
        if kind == "main_line":
            out["main_line"] = self._main_line_moved(self.record.state())
        if kind == "knowledge":
            f = self.record.state()["findings"].get(finding_id) if finding_id else None
            self.record.append("knowledge_recorded", {"question_id": (f or {}).get("question_id"), "source": "the user",
                                                      "item": "the user's words", "text": words}, rc.AGENT)
            out["knowledge"] = "kept as prior knowledge: it explains a finding and never changes one"
        if kind == "enough":
            out["enough"] = "the user says enough: write the report (write_report), which states what is still open"
        return out

    def _next_step_taken(self, words, option, mine):
        """The user's pick among the next steps that wait (rc.next_step_awaiting), which ends the offer: the option of
        that rank opens its thread on the line, next_of the result; without option, their own next step, in their
        words, does (docs/adr/0050, D5)."""
        waits = rc.next_step_awaiting(self.record.state())
        if waits is None:
            raise AnalysisError("no next steps wait for the user's pick: a pick is read once a reply offers a result's "
                                "next steps (option N lines)")
        rid = waits["result_id"]
        if not words:  # checked before its thread opens, as the record checks the reply
            raise AnalysisError("a next_step reply is the user's words, as they gave them")
        if option is None:
            opened = self.open_thread(about=words, raised_by=rid, next_of=rid, user_words=words)
        else:
            step = next((s for s in waits["options"] if s["rank"] == option), None)
            if step is None:
                raise AnalysisError(f"option is the rank of one of {rid}'s next steps: "
                                    + ", ".join(str(s["rank"]) for s in waits["options"]))
            opened = self.open_thread(about=step["question"], raised_by=rid, next_of=rid, matters=step["why"])
        # shortcut: the thread and the reply are two writes; the reply's one check (its words) ran first, so only a
        # failed disk leaves a thread without its reply. One write would need open_thread to take events from its
        # caller, which the dispatcher would let an agent pass too
        self.record.append("reply_recorded", {"kind": "next_step", "words": words, "finding_id": None,
                                              "relayed": not mine, "result_id": rid, "thread_id": opened["thread_id"],
                                              **({"option": option} if option is not None else {}),
                                              **({"by_analysis": True} if mine else {})}, rc.AGENT)
        return {"recorded": "next_step", "result_id": rid, "thread_id": opened["thread_id"]}

    def _beside_events(self, finding_id, prior, differs, thread_id=None):
        """The events for what is known beside a finding or a thread (prior: {text, source, citation, relates}), kept
        as prior knowledge, and how a finding differs from the paper or that knowledge (differs: {text, source});
        checked here, before anything is written."""
        for name, given in (("prior", prior), ("differs", differs)):
            if given is not None and not (isinstance(given, dict) and str(given.get("text") or "").strip()
                                          and str(given.get("source") or "").strip()):
                raise AnalysisError(f"{name} is {{text, source}}: what it says and where it comes from ("
                                    + ("a PMID, a DOI, a database or 'the user')" if name == "prior" else
                                       f"one of {', '.join(rc.DIFFERENCE_SOURCES)})"))
        if differs is not None and str(differs["source"]).strip() not in rc.DIFFERENCE_SOURCES:
            raise AnalysisError(f"differs' source is one of {', '.join(rc.DIFFERENCE_SOURCES)}")
        out = []
        if prior is not None:
            out.append(("knowledge_recorded", {"question_id": None, "source": str(prior["source"]).strip(),
                                               "item": str(prior.get("citation") or prior["source"]).strip(),
                                               "text": str(prior["text"]).strip(), "finding_id": finding_id,
                                               **({"thread_id": thread_id} if thread_id else {}),
                                               **({"relates": list(prior["relates"])} if prior.get("relates")
                                                  else {})}, rc.AGENT))
        if differs is not None:
            out.append(("difference_noted", {"finding_id": finding_id, "source": str(differs["source"]).strip(),
                                             "text": str(differs["text"]).strip()}, rc.AGENT))
        return out

    def reword_finding(self, finding_id, reason, statement=None, effect=None, figure=None, run_ids=None,
                       target=None, feature=None, direction=None, check_again=False, prior=None, differs=None,
                       relates=None, kind=None, genes=None, state_run=None, block_call=None, cells=None,
                       reference=None, targets=None):
        """A finding changed in place (docs/adr/0032, amendment 1): its sentence, numbers or figure, with the reason.
        The record keeps each earlier wording, and the user's replies and the checks stay with the finding. A new
        target, feature or direction is another claim, whose checks start again; a kind with them makes a
        description a claim under its ID (GSE267740, d3a7dcc: ten descriptions, no check ran, and the only route was
        a new finding and a new version of each result). cells take the analysis tools' forms (docs/adr/0050, D4)."""
        self._need()
        f = self.record.state()["findings"].get(finding_id)
        if f is None:
            raise AnalysisError(f"no finding {finding_id}")
        cells = self._kept_slice(cells) if isinstance(cells, dict) else cells
        from . import screen_map
        bad = screen_map.check_relates(relates)
        if bad:
            raise AnalysisError("; ".join(dict.fromkeys(bad)))
        if kind is not None and kind not in contracts.CLAIM_KINDS:
            raise AnalysisError(f"a rewording's kind is a claim's: {', '.join(contracts.CLAIM_KINDS)}")
        if targets is not None:  # a set claimed in place, as record_finding takes it
            if target is not None or not 1 <= len(targets) <= 6 or len(set(targets)) < len(targets):
                raise AnalysisError("targets names one to six targets, each once, in place of target")
            if (f.get("targets") or [])[1:] and list(targets) != f["targets"]:
                raise AnalysisError(f"finding {finding_id} is a claim on {len(f['targets'])} targets: another set of "
                                    "targets is another finding (record_finding)")
            target = targets[0]
        given = {k: v for k, v in (("statement", statement), ("effect", effect), ("figure", figure),
                                   ("run_ids", run_ids), ("target", target), ("feature", feature),
                                   ("direction", direction), ("relates", relates), ("kind", kind), ("genes", genes),
                                   ("state_run", state_run), ("block_call", block_call), ("cells", cells),
                                   ("reference", reference),
                                   ("targets", list(targets) if targets and len(targets) > 1 else None))
                 if v is not None}
        claim = {k: given.get(k, f.get(k)) for k in rc.CLAIM}
        now = {k: given.get(k, f.get(k)) for k in ("kind", "genes", "state_run", "block_call", "cells", "reference")}
        other = (any(claim[k] != f.get(k) for k in rc.CLAIM) or now["kind"] != f.get("kind")
                 or ("targets" in given and given["targets"] != f.get("targets")))
        if kind is not None and not all(claim.values()):  # a kind alone would silence the result's notice, unchecked
            raise AnalysisError("a claim's kind comes with its target (or targets), feature and direction")
        parts = None
        if other and all(claim.values()):  # checked before the rewording is recorded: a wrong one changes nothing
            if f.get("targets") and claim["target"] != f.get("target"):
                raise AnalysisError(f"finding {finding_id} is a claim on {len(f['targets'])} targets: another set of "
                                    "targets is another finding (record_finding)")
            if now["kind"] in (None, "description"):
                raise AnalysisError(f"finding {finding_id} has no claim's kind, so no check runs on it: give its kind "
                                    "with the claim (kind, and what that kind needs, as in record_finding)")
            if claim["direction"] not in ("up", "down"):
                raise AnalysisError("direction is up or down")
            for t in given.get("targets") or f.get("targets") or [claim["target"]]:  # each target, as record_finding
                parts = self._finding_parts(t, now["kind"], now["genes"], now["state_run"], now["block_call"],
                                            now["cells"], now["reference"], claim["feature"])
            if parts.get("score_table"):  # the version of the table this claim read
                given["score_table"] = parts["score_table"]
        beside = self._beside_events(finding_id, prior, differs)
        events = ([("finding_reworded", {"finding_id": finding_id, "reason": reason, **given}, rc.AGENT)]
                  if given or not (check_again or prior is not None or differs is not None) else [])
        if events or beside:
            self.record.append_many(events + beside)  # all or nothing
        st = self.record.state()
        out = {"finding_id": finding_id, "reworded": sorted(given),
               "earlier_wordings": len(st["findings"][finding_id]["wordings"])}
        if parts is not None:
            named = given.get("targets") or f.get("targets") or [claim["target"]]
            out.update(self._start_checks(finding_id, {**claim, "targets": named, "question_id": f.get("question_id")},
                                          parts))
            out["checks"] = "another claim: its checks start again; the earlier ones stay with the earlier wording"
        elif check_again:
            out.update(self.check_finding(finding_id))
            out["checks"] = "started again"
        elif other or not all(claim.values()):
            out["checks"] = "none: a finding's checks need a target (or pair), a feature and a direction"
        else:
            out["checks"] = "kept: the target, feature and direction are the same"
        unanswered =[d for d, x in st["doubts"].items() if x.get("finding_id") == finding_id and not x.get("answers")]
        if unanswered:  # a rewording that meets a doubt is said with answer_doubt, or the doubt stays open
            out["doubts_unanswered"] = f"{', '.join(unanswered)}: answer_doubt for each this wording answers"
        known = self._known_now(finding_id=finding_id) if prior is not None or differs is not None else None
        if known:  # what it gave beside the finding is still from memory
            out["known"] = known
        return out

    def _known_now(self, finding_id=None, thread_id=None, result_id=None):
        """The line a reply carries at a moment that calls for what is known to be looked up, where nothing beside
        it was in this session (rc.known_not_looked_up): a result, a finding in one or differing from prior
        knowledge, an observation. None otherwise."""
        st = self.record.state()
        left = rc.known_not_looked_up(st)
        if result_id is not None:
            if result_id not in left.get("results", []):
                return None
            runs = [x for f in st["results"][result_id].get("finding_ids") or []
                    for x in (st["findings"].get(f) or {}).get("run_ids") or []]
            own = bool(runs) and not any((st["runs"].get(x) or {}).get("question_id") for x in runs)
            return KNOWN["result"] + (KNOWN["own_code"] if own else "")
        if finding_id is not None:
            if finding_id in left.get("differences", []):
                return KNOWN["difference"]
            ins = [r for r in left.get("results", []) if finding_id in (st["results"][r].get("finding_ids") or [])]
            return KNOWN["result"] if ins else None
        return KNOWN["observation"] if thread_id in left.get("observations", []) else None

    def check_finding(self, finding_id, only=None):
        """A recorded finding's checks started again: after their run failed or was stopped, or for a finding
        recorded before the checks ran in code; only: the checks to run (the lead always runs)."""
        self._need()
        f = self.record.state()["findings"].get(finding_id)
        if f is None:
            raise AnalysisError(f"no finding {finding_id}")
        self.record.sync_runs(self._jobs_root())
        running = [r["run_id"] for r in self._running() if r["what"] == f"checks of finding {finding_id}"]
        if running:
            raise AnalysisError(f"the checks of finding {finding_id} are running: {running}")
        if f.get("kind") is None:
            raise AnalysisError(f"finding {finding_id} was recorded without its kind; record it again under a new "
                                "finding_id with its kind")
        if f["direction"] not in ("up", "down"):
            raise AnalysisError(f"finding {finding_id}'s direction {f['direction']!r} is not up or down; record it "
                                "again under a new finding_id")
        parts = self._finding_parts(f["target"], f["kind"], f.get("genes"), f.get("state_run"), f.get("block_call"),
                                    f.get("cells"), f.get("reference"), f.get("feature"),
                                    score_table=f.get("score_table"))
        return {"finding_id": finding_id, **self._start_checks(finding_id, f, parts, again=True, only=only)}

    def _finding_parts(self, target, kind, genes, state_run, block_call, cells, reference, feature=None,
                       score_table=None):
        """What a finding's checks need, checked now rather than in the job: its kind, its cells, its reference, its
        lead (the test of the pt call it came from), its state labelling, and a score's saved table (score_table:
        the copy the finding holds, read as it is)."""
        from . import design_fit
        if kind not in design_fit.KINDS:
            raise AnalysisError(f"kind is one of {design_fit.KINDS}")
        score_table = self._score_table(feature, bound=score_table) if kind == "score" else None
        if reference not in (None, "control_guides", "other_targets"):
            raise AnalysisError("reference is control_guides (the control cells) or other_targets (every other "
                                "target's cells)")
        lead, state_table = None, None
        if block_call is not None:  # the call the finding came from gives its cells, reference and test
            call = self._block_call(block_call)
            if target not in call["targets"]:
                raise AnalysisError(f"the finding's target {target!r} is not one of the call's targets "
                                    f"{call['targets']}")
            if not call.get("kept_cells"):
                raise AnalysisError(f"the call {block_call} ran on {call['cells']}: keep those cells first "
                                    "(pt.save_cells) and run the call on the kept set, so its checks can rerun it")
            cells = cells or (None if call["cells"] == "every cell" else call["cells"])
            reference = reference or call["reference"]
            method = (call.get("choices") or {}).get("method")
            if call["block"] == "genes" and method in ("wilcoxon", "wilcoxon_same_samples", "edger_ql"):
                lead = {"method": method, "reason": {
                    "wilcoxon": "Wilcoxon on cells, as the call ran it",
                    "wilcoxon_same_samples": "Wilcoxon on cells against the control cells of the same samples, as "
                                             "the call ran it",
                    "edger_ql": "edgeR-QL pseudobulk, as the call ran it"}[method]}
            if call["block"] in ("states", "cell_cycle", "composition"):
                state_table = self._labelling_of(call)
        if kind == "gene_set" and not genes:
            raise AnalysisError("a gene_set finding needs its genes")
        if kind == "state" and state_table is None:
            state_table = self._run_labelling(state_run)
            if state_table is None:
                raise AnalysisError("a state finding needs state_run (the `states` run it came from, view states or "
                                    "cell_cycle) or block_call (a pt.states, pt.cell_cycle or "
                                    "pt.composition call); its feature is the state or phase as the labelling names "
                                    "it (such as S10 or G1)")
        self._cells(cells)
        if cells is None and kind in ("gene", "gene_set", "state", "score"):
            if not os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
                raise AnalysisError("build the screen object first (build_screen_object): a finding rests on it")
            with open(os.path.join(self._screen_dir(), "screen.json"), encoding="utf-8") as fh:
                labels = json.load(fh).get("labels") or {}
            if target not in labels:
                raise AnalysisError(f"no target {target!r} among the screen object's labels; a finding on other "
                                    "cells names their cell set (cells)")
        return {"kind": kind, "cells": cells, "reference": reference or "control_guides", "lead": lead,
                "state_table": state_table, "genes": list(genes) if genes else None, "score_table": score_table}

    def _score_table(self, feature, bound=None):
        """The saved table a score finding names (feature table:<name>, pt.save_table in any notebook, the latest of
        that name), read now so that a wrong one is said when the finding is recorded, and kept as that version: a
        copy in the project's checks/score_tables, named by its content, which the finding's checks read from then
        on (bound: that copy, as a finding holds it). pt.save_table writes over a table saved under the same name,
        so a check that read the latest of a name could take another notebook's table, or a later version."""
        import glob
        import shutil
        from . import finding_checks
        if bound and os.path.exists(bound):
            return bound
        name = str(feature or "")[len("table:"):] if str(feature or "").startswith("table:") else ""
        if not name:
            raise AnalysisError("a score finding's feature is table:<name>, a table the notebook saved with "
                                "pt.save_table: one row per cell, its barcode and its score")
        found = sorted(glob.glob(os.path.join(self.project, "notebooks", "*", "tables", f"{name}.tsv.gz")),
                       key=os.path.getmtime)
        if not found:
            raise AnalysisError(f"no saved table {name!r}: save the score first, pt.save_table({name!r}, table), one "
                                "row per cell with its barcode and its score")
        try:
            finding_checks.score_values(found[-1])
        except ValueError as e:
            raise AnalysisError(str(e)) from None
        with open(found[-1], "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:12]
        folder = os.path.join(self.project, "checks", "score_tables")
        os.makedirs(folder, exist_ok=True)
        copy = os.path.join(folder, f"{name}-{digest}.tsv.gz")
        if not os.path.exists(copy):
            shutil.copyfile(found[-1], copy + ".tmp")
            os.replace(copy + ".tmp", copy)
        return copy

    def _labelling_of(self, call, depth=0):
        """The per-cell labelling a states, cell-cycle or composition call rests on: the states or phases table a
        pt.states or pt.cell_cycle call wrote, or that of the call a pt.composition call read (its of)."""
        outputs = call.get("outputs") or {}
        if call["block"] == "states":
            return outputs.get("states")
        if call["block"] == "cell_cycle":
            return outputs.get("phases")
        of = (call.get("choices") or {}).get("of")
        if call["block"] == "composition" and isinstance(of, str) and depth < 3:
            calls = self.record.state().get("block_calls", {})
            if of in calls:
                return self._labelling_of(calls[of], depth + 1)
        return None

    def _run_labelling(self, run_id):
        """A module run's per-cell labelling: C.states_and_composition's cell_states.tsv, or the phases that
        C.cell_cycle's pt.cell_cycle call wrote in the run's blocks."""
        import glob
        if not run_id:
            return None
        folder = os.path.join(self._jobs_root(), str(run_id))
        table = os.path.join(folder, "cell_states.tsv")
        if os.path.exists(table):
            return table
        found = sorted(glob.glob(os.path.join(folder, "blocks", "outputs", "cell_cycle-*", "phases.tsv.gz")))
        return found[0] if found else None

    def _kinds_with_checks(self, run_ids, block_call):
        """For a finding recorded as custom: the kinds its runs or its call would have checked, each with what to
        give (docs/build/development_sessions/session_03_crisprai_haiku, problems 11 and 16)."""
        out = []
        call = self.record.state().get("block_calls", {}).get(block_call) if block_call else None
        if call is not None:
            if call["block"] == "genes":
                out.append(f"gene (the feature a gene) or gene_set (the set's name, with genes), block_call "
                           f"{block_call}")
            elif call["block"] in ("scores", "programs"):
                out.append(f"gene_set (the set's or program's name, with genes), block_call {block_call}")
            elif call["block"] in ("states", "cell_cycle", "composition"):
                out.append(f"state (the feature the state or phase as named, such as G1), block_call {block_call}")
        for r in run_ids or []:
            r = str(r)
            if "-C-genes-" in r:
                out.append(f"gene (the feature a gene) or gene_set (a set of its changed genes, with genes), from "
                           f"run {r}")
            elif "-C-programs-" in r:
                out.append(f"gene_set (the program's or set's name, with genes), from run {r}")
            elif "-C-states_and_composition-" in r or "-C-cell_cycle-" in r:
                out.append(f"state (the feature the state or phase as named, such as S10 or G1), state_run {r}")
        return out

    @staticmethod
    def _checks_cover():
        """What a claim's checks cover, in the checks' own words (finding_checks.WORDS), one list for every reply."""
        from .finding_checks import CHECK_NAMES, WORDS
        return ", ".join(WORDS[n] for n in CHECK_NAMES)

    def _start_checks(self, finding_id, f, parts, again=False, only=None):
        """The checks of a recorded finding (pertura.finding_checks): f its target, feature, direction and question;
        parts what _finding_parts found. The spec is queued as a file and started when a slot is free
        (_start_queued_checks)."""
        kind = parts["kind"]
        if kind in ("interaction", "custom"):
            why = ("a combination is checked by E.combinations' own agreement between units" if kind == "interaction"
                   else "the agent's own code: its choices are not declared, so the tool does not check it")
            if not again:
                self.record.append("check_recorded", {"finding_id": finding_id, "check": "lead",
                                                      "outcome": "not run", "reason": why}, rc.TOOL)
            return {"checks": "not run here: " + why}
        if not self.record.enabled("robustness"):
            return {"checks": "switched off for this record"}
        spec = {"target": f["target"], "targets": list(f.get("targets") or [f["target"]]), "kind": kind,
                "feature": f["feature"], "direction": -1 if f["direction"] == "down" else 1,
                "reference": parts["reference"], "cells": parts["cells"], "genes": parts["genes"],
                "state_table": parts["state_table"], "score_table": parts.get("score_table"), "lead": parts["lead"],
                "only": list(only) if only else None,
                "_queue": {"finding_id": finding_id, "question_id": f["question_id"], "build": self._build_id(),
                           "cells_rule": self._cells_rule_id()}}
        folder = os.path.join(self.project, "checks")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{finding_id}-{len(os.listdir(folder)) + 1}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=1)
        moved = self._start_queued_checks()
        at_once = self._resources()["checks_at_once"]
        if path in moved["reused"]:
            return {"checks": f"the same claim as {moved['reused'][path]}, on the same cells: its checks are this "
                              "finding's too"}
        said = "Each reply opens them once they have ended; a finding they say fails is a doubt to answer"
        if path in moved["started"]:
            return {"checks_run": moved["started"][path],
                    "next": f"its checks run in the background, up to {at_once} findings' at a time. {said}, and "
                            "run_result with this run_id gives them, each in its own terms"}
        return {"checks": f"queued behind {moved['ahead'].get(path, 0)} others; up to {at_once} findings' checks "
                          f"run at a time. {said}"}

    def _spec_key(self, path):
        """A queued checks spec's comparison (the spec without the queue's note, its build and cells' rule) and its
        finding."""
        if path not in self._spec_keys:
            with open(path, encoding="utf-8") as fh:
                spec = json.load(fh)
            note = spec.pop("_queue", None) or {}
            self._spec_keys[path] = (json.dumps([spec, note.get("build"), note.get("cells_rule")], sort_keys=True,
                                                default=str),
                                     note.get("finding_id"), note.get("question_id"))
        return self._spec_keys[path]

    def _start_queued_checks(self):
        """Start the queued checks specs, oldest first, while fewer than checks_at_once checks jobs run. A spec waits
        as a file, never as a process (a job loads the whole screen object): a call starts it, or the server's queue
        thread (_drive_the_checks) when no call comes; with queue_only (apply-inbox at a turn's Stop) nothing starts.
        A spec that repeats one whose checks ended (the same comparison on the same build) takes its checks; one that
        repeats a spec queued or running before it waits for it. Returns {"started": {spec: run}, "reused": {spec:
        finding}, "ahead": {spec: specs before it}}."""
        import glob
        import sys
        out = {"started": {}, "reused": {}, "ahead": {}}
        folder = os.path.join(self.project or "", "checks")
        if self.record is None or not os.path.isdir(folder) or self.queue_only:
            return out
        with self._lock:
            self.record.sync_runs(self._jobs_root())
            st = self.record.state()
            runs = {r["inputs"][1]: r for r in st["runs"].values()
                    if str(r.get("what", "")).startswith("checks of finding ") and len(r.get("inputs") or []) > 1}

            def order(p):
                tail = os.path.splitext(os.path.basename(p))[0].rsplit("-", 1)[-1]
                return int(tail) if tail.isdigit() else 0

            queued = sorted((p for p in glob.glob(os.path.join(folder, "*.json"))
                             if p not in runs and p not in self._started_specs), key=order)
            if not queued:
                return out
            busy = sum(1 for r in st["runs"].values()  # every checks job running holds a slot
                       if r["state"] == "running" and str(r.get("what", "")).startswith("checks of finding "))
            ended, waiting = {}, set()
            for p, r in runs.items():
                try:
                    key, fid, _ = self._spec_key(p)
                except (OSError, ValueError):
                    continue
                if r["state"] == "running":
                    waiting.add(key)
                elif fid in st["findings"] and st["findings"][fid].get("checks"):
                    ended[key] = fid
            at_once = self._resources()["checks_at_once"]
            from . import limits
            for p in queued:
                try:
                    key, fid, qid = self._spec_key(p)
                except (OSError, ValueError) as e:
                    _logged(e)
                    continue
                if key in ended:
                    self._take_checks(ended[key], fid)
                    os.replace(p, p + ".reused")
                    out["reused"][p] = ended[key]
                    continue
                if key in waiting or busy >= at_once:
                    waiting.add(key)
                    out["ahead"][p] = len(out["ahead"])
                    continue
                cmd = [sys.executable, "-m", "pertura.finding_checks", "run", "--spec", p, "--screen",
                       self._screen_dir(), "--design", self._design_path(), "--out", ".", "--record",
                       self.record.folder, "--finding", fid, "--at-once", str(at_once)]
                out["started"][p] = self.record.start_run(
                    f"checks of finding {fid}", cmd, [self._screen_dir(), p], self._jobs_root(), question_id=qid,
                    name="checks", threads=max(1, limits.THREAD_LIMIT // at_once))  # at_once share the cores
                self._started_specs.add(p)  # started once, also before the record lists its run
                busy += 1
                waiting.add(key)
        return out

    def _doubts_from_checks(self):
        """A claim its checks say fails (finding_checks.disagrees) gets one doubt, opened by the checks: the one
        obligation the loop keeps (2026-11-18). The main agent keeps the claim (holds), narrows it or withdraws it
        (overturns). One for each claim, never again for it, also when it is checked again."""
        with self._lock:  # a call and the queue's thread, one at a time: never two doubts for one claim
            st = self.record.state()
            opened = {d.get("finding_id") for d in (st.get("doubts") or {}).values()
                      if d.get("opened_by") == CHECKS_DOUBT}
            for fid, f in (st.get("findings") or {}).items():
                if fid in opened or not rc.checks_disagree(f):
                    continue
                if any(str(c.get("reason") or "").startswith("the same claim as ")
                       for c in (f.get("checks") or {}).values()):
                    continue  # a repeat took its checks from the claim it repeats, whose doubt is the one to answer
                self.raise_doubt(self.new_doubt_id(), f"its checks disagree with it: {rc.disagreement(f)}",
                                 "the claim kept, narrowed to where its checks hold, or withdrawn", finding_id=fid,
                                 opened_by=CHECKS_DOUBT)

    def _take_checks(self, source, finding_id):
        """A repeated claim takes the checks of the claim it repeats, each said to come from it."""
        f = self.record.state()["findings"][source]
        by = f.get("checks_by_target") or {}  # a claim on several targets: each target's checks, with its target
        rows = ([(t, name, e) for t, d in by.items() for name, e in d.items()] if by else
                [(None, name, e) for name, e in (f.get("checks") or {}).items()])
        self.record.append_many([("check_recorded", {
            "finding_id": finding_id, "check": name, "outcome": e["outcome"], "run_id": e.get("run_id"),
            "reason": f"the same claim as {source}: " + str(e.get("reason") or ""), "numbers": e.get("numbers") or {},
            **({"target": t} if t is not None else {})}, rc.TOOL) for t, name, e in rows])

    def record_check(self, finding_id, check, outcome, run_id=None, reason=None):
        self._need()
        self.record.append("check_recorded", {"finding_id": finding_id, "check": check, "outcome": outcome,
                                              "run_id": run_id, "reason": reason}, rc.AGENT)
        return {"finding_id": finding_id, "check": check}

    def new_doubt_id(self):
        """The next short doubt ID of the project (D1, D2, ...), as the agent answers it."""
        taken = [int(d[1:]) for d in self.record.state().get("doubts") or {} if d[:1] == "D" and d[1:].isdigit()]
        return f"D{max(taken, default=0) + 1}"

    def raise_doubt(self, doubt_id, text, would_change, finding_id=None, question_id=None, opened_by=None):
        """A doubt, and what result would change the judgement; opened_by, the agent that raised it (the skeptic or
        the main agent), as the hooks mark the call, so the user's page shows whose doubt it is."""
        self._need()
        self.record.append("doubt_raised", {"doubt_id": doubt_id, "text": text, "would_change": would_change,
                                            "finding_id": finding_id, "question_id": question_id,
                                            **({"opened_by": opened_by} if opened_by else {})}, rc.AGENT)
        return {"doubt_id": doubt_id, "on": finding_id or question_id or "the analysis"}

    def answer_doubt(self, doubt_id, outcome, reason, run_ids=None, limit=None):
        """A doubt's answer, recorded on the doubt (docs/adr/0032, amendment 1): what its test showed of its finding,
        the reason and the runs it rests on; a limit with its kind (docs/adr/0045, amendment 1)."""
        self._need()
        if outcome == rc.LIMIT and limit not in rc.LIMIT_KINDS:
            raise AnalysisError(f"a doubt stated as a limit says what leaves it (limit): {', '.join(rc.LIMIT_KINDS)}")
        self.record.append("doubt_answered", {"doubt_id": doubt_id, "outcome": outcome, "reason": reason,
                                              "run_ids": list(run_ids or []),
                                              **({"limit": limit} if limit is not None else {})}, rc.AGENT)
        st = self.record.state()
        d = st["doubts"][doubt_id]
        out = {"doubt_id": doubt_id, "answer": outcome, "on": d.get("finding_id") or d.get("question_id"),
               "answers": len(d["answers"])}
        if not rc.doubt_tested(st, doubt_id):  # the doubt's test where its answer is decided (GSE267740, 16ea437)
            out["its_test"] = d.get("would_change")
            out["to_test_it"] = ("no run made after the doubt answers it: run its test (run_code, or the tool it "
                                 "names) and answer again citing that run; a long one can go to a worker "
                                 "(open_thread with this doubt_id)")
        if outcome == rc.NARROWED and d.get("finding_id"):
            out["reword"] = f"reword {d['finding_id']} to the narrower claim (reword_finding)"
        if outcome == rc.LIMIT:
            out.update(self._next_check_said(st))
        return out

    def _next_check_said(self, st):
        """The open check most likely to change the main result (record.next_check), as a reply carries it."""
        nxt = rc.next_check(st)
        return {"the_check_most_likely_to_change_the_main_result": nxt} if nxt else {}

    def open_branch(self, branch_id, plan, doubt_id=None, origin=None, fork_id=None, request=None, alternatives=None,
                    base=None, measures=None):
        """A branch (docs/adr/0030): from a doubt, a decided fork or the user's request; or, at a choice the main
        agent went ahead on, its alternatives (docs/adr/0032, D9), each tried by a worker in its own notebook."""
        self._need()
        if alternatives is not None and origin is None:
            origin = "alternatives"
        from .flow import NAME
        bad = [a.get("name") for a in alternatives or [] if isinstance(a, dict) and not NAME.match(
            f"{branch_id}__{a.get('name')}")]
        if bad:
            raise AnalysisError(f"alternatives {bad}: a name of letters, digits, _ and -, so it names the worker's "
                                "notebook and its row")
        self.record.append("branch_opened", {k: v for k, v in (
            ("branch_id", branch_id), ("plan", plan), ("doubt_id", doubt_id), ("origin", origin),
            ("fork_id", fork_id), ("request", request), ("alternatives", alternatives), ("base", base),
            ("measures", measures)) if v is not None}, rc.AGENT)
        out = {"branch_id": branch_id}
        if origin == "alternatives":
            out.update(self._branch_reply(branch_id, alternatives, base, measures))
            out["launch"] = ("one worker per alternative (agents/worker.md), each with its brief, in parallel; then "
                             "close_branch with the runs, which returns the table of every way")
        return out

    def _branch_reply(self, branch_id, alternatives, base, measures):
        """Where the main agent saves its own row, and each worker's brief."""
        return {
            "your_row": (f"compute the measures for your own way and save them as one row: "
                         f"pt.save_table('{branch_id}__main', {{measure: value, ...}})"),
            "workers": [{
                "worker": f"{branch_id}-{a['name']}", "alternative": a["name"], "change": a["change"],
                "save_row_as": f"{branch_id}__{a['name']}",
                "brief": (f"Branch {branch_id}, alternative {a['name']}: run the base with this one change: "
                          f"{a['change']}. Your notebook: run_code with worker '{branch_id}-{a['name']}'. Compute "
                          f"the measures {list(measures)} by the base's definitions and save them as one row: "
                          f"pt.save_table('{branch_id}__{a['name']}', {{measure: value, ...}}); draw the same "
                          f"figure as the base. The base:\n{base}")} for a in alternatives]}

    def _comparison(self, b):
        """A branch of alternatives' table: one row per way, the main agent's first, each from the row its notebook
        saved (table:<branch>__<name>). Returns (rows, the ways whose row is missing)."""
        import pandas as pd
        objs = self.record.state().get("objects", {})
        rows, missing = [], []
        ways = [("main", "the main agent's way")] + [(a["name"], a["change"]) for a in b["alternatives"]]
        for name, change in ways:
            o = objs.get(f"table:{b['branch_id']}__{name}")
            if not o or not o.get("path") or not os.path.exists(o["path"]):
                missing.append(name)
                continue
            t = pd.read_csv(o["path"], sep="\t")
            row = _records(t.head(1))[0] if len(t) else {}
            rows.append({"way": name, "change": change, **row})
        return rows, missing

    def _branch_figures(self, run_ids):
        """The figures of a branch's runs, each run's in order: the ones it kept in the record, then the one its
        run shows."""
        st = self.record.state()
        out = []
        for r in run_ids or []:
            out += [self._figure_png(f) for f in st["figures"].values() if f.get("run_id") == r]
            fig = (st["runs"].get(r) or {}).get("figure")
            if fig:
                out.append(fig)
        return [p for p in dict.fromkeys(out) if p and os.path.exists(p)]

    def close_branch(self, branch_id, outcome, run_ids, worker=None):
        """A branch's outcome. A branch of alternatives also returns its table, one row per way, and its runs'
        figures (docs/adr/0032, D9); its workers' notebooks are shut."""
        self._need()
        b = self.record.state().get("branches", {}).get(branch_id)
        keys = [worker or branch_id, branch_id]
        comparison, missing, images, shown = None, [], [], []
        if b and b.get("origin") == "alternatives":
            comparison, missing = self._comparison(b)
            keys += [f"{branch_id}-{a['name']}" for a in b["alternatives"]]
            figs = self._branch_figures(run_ids)
            cap = self._resources()["images_per_cell"] * 2
            images, left = self._images(figs[:cap])
            shown = [p for p in figs[:cap] if p not in left]
        self.record.append("branch_closed", {"branch_id": branch_id, "outcome": outcome, "run_ids": run_ids,
                                             **({"comparison": comparison} if comparison is not None else {})},
                           rc.AGENT)
        freed = [k for k in dict.fromkeys(keys) if self._free_notebook(k)]
        out = {"branch_id": branch_id, **({"notebook_shut": freed} if freed else {})}
        if comparison is not None:
            out["comparison"] = comparison
            if missing:
                out["rows_missing"] = missing
            if images:
                out["_images"] = images
                out["figures_shown"] = shown
        return out

    def _free_notebook(self, key):
        """Shut a worker's notebook (never the main one); its folder and files stay."""
        nb = self.notebooks.pop(key, None) if key and key != "main" else None
        if nb is not None:
            nb.shutdown()
            self.flows.pop(key, None)  # a later notebook for the worker has none of these names
        return nb is not None

    def stop_run(self, run_id):
        from . import runner
        self._need()
        run = self.record.state()["runs"].get(run_id)
        if run is None:
            raise AnalysisError(f"no run {run_id}")
        if run["state"] != "running":
            return {"run_id": run_id, "state": run["state"], "note": "it is not running"}
        if not run.get("job_id"):  # a notebook cell, not a job (GSE203592: "failed inside stop_run: KeyError")
            return {"run_id": run_id, "state": "running",
                    "note": "a notebook cell, not a job: it stops at its cell's time limit, and its kernel is "
                            "interrupted then; the agent whose notebook runs it gets its reply"}
        out = runner.stop(run["job_id"], self._jobs_root())
        self.record.sync_runs(self._jobs_root())
        return {"run_id": run_id, "state": self.record.state()["runs"][run_id]["state"], "stopped": out["stopped"]}

    def _running(self):
        """The runs whose jobs are running: what each is, how long it has run and how far it is, where it says."""
        from . import progress, runner
        out = []
        for rid, r in self.record.state()["runs"].items():
            if r["state"] == "running" and r.get("job_id"):
                try:
                    st = runner.status(r["job_id"], self._jobs_root())
                except KeyError:
                    continue
                said = progress.words(st.get("progress"))
                out.append({"run_id": rid, "what": r["what"], "since": st["created"],
                            **({"progress": said} if said else {})})
        return out

    def _progress(self, run):
        """A running run's progress in words ('7 of 12 comparisons, about 9 minutes left'), or None."""
        from . import progress, runner
        if not run.get("job_id"):
            return None
        try:
            return progress.words(runner.status(run["job_id"], self._jobs_root()).get("progress"))
        except KeyError:
            return None

    def record_sufficiency(self, question_id, sufficient, reasons):
        self._need()
        self.record.append("sufficiency_checked", {"question_id": question_id, "sufficient": bool(sufficient),
                                                   "reasons": list(reasons)}, rc.AGENT)
        return {"question_id": question_id, "sufficient": bool(sufficient)}

    def close_question(self, question_id, status, evidence=None, figure=None, reason=None, by="agent",
                       user_words=None):
        """A question's status, where the agent wants one; nothing asks for it (docs/adr/0032, D7). A question the user
        leaves is asked in chat and recorded with their words."""
        self._need()
        if status == "not_pursued" and by == "user" and not str(user_words or "").strip():
            raise AnalysisError("the user leaves a question in chat: pass their words (user_words)")
        taken = None
        if figure is None and status in rc.ANSWERED and evidence:
            # the figure of an evidence run, where the agent gave none (development session 3, problem 15: every
            # question was refused once for want of a figure ID, then closed again)
            figs = self.record.state().get("figures", {})
            taken = next((f for r in evidence for f in (f"{r}:figure",) if f in figs), None) or next(
                (fid for r in evidence for fid, f in figs.items() if f.get("run_id") == r), None)
            figure = taken
        self.record.append("question_closed", {"question_id": question_id, "status": status, "evidence": evidence or [],
                                               "figure": figure, "reason": reason,
                                               **({"user_words": str(user_words).strip(), "relayed": True}
                                                  if by == "user" else {})}, rc.AGENT)
        out = {"question_id": question_id, "closed": True, "status": status}
        if taken:
            out["figure"] = f"{taken} (its evidence run's figure, as none was given)"
        return out

    def open_items(self):
        self._need()
        items = self.record.critic()
        return {"open": items, "due": [i for i in items if i["kind"] in DUE],
                "blocks_report": self.record.blocks_report()}

    def note_difference(self, finding_id, source, text):
        self._need()
        self.record.append("difference_noted", {"finding_id": finding_id, "source": source, "text": text},
                           rc.AGENT)
        return {"finding_id": finding_id, "due": [i for i in self.record.critic()
                                                  if i["kind"] == "skeptic due" and i["id"] == finding_id]}

    def record_skeptic_check(self, finding_ids, doubt_ids, checked=None, question_lacks=None):
        self._need()
        self.record.append("skeptic_checked", {"finding_ids": list(finding_ids), "doubt_ids": list(doubt_ids),
                                               **({"checked": list(checked)} if checked else {}),
                                               **({"question_lacks": str(question_lacks)} if question_lacks else {})},
                           rc.AGENT)
        return {"checked": list(finding_ids), "doubts": list(doubt_ids)}

    # ---- results: dossiers, panels and the report (docs/adr/0020, section 7)
    def _run_folder(self, run_id):
        run = self.record.state()["runs"].get(run_id)
        if run is None:
            raise AnalysisError(f"no run {run_id}")
        return os.path.join(self._jobs_root(), run["job_id"] or run_id)

    def _map_size(self, run_id):
        """The distinct targets (or pairs) and genes in a run's tables (result, and those its result lists); for a
        notebook cell's run, whose tables Pertura does not read, {targets: None, genes: None, measured: False}."""
        import pandas as pd
        if not (self.record.state()["runs"].get(run_id) or {}).get("job_id"):
            return {"targets": None, "genes": None, "measured": False}
        folder = self._run_folder(run_id)
        names, genes = set(), set()
        listed = ["result"]
        try:
            with open(os.path.join(folder, "result.json"), encoding="utf-8") as fh:
                listed = json.load(fh).get("tables") or listed
        except (OSError, ValueError):
            pass
        for name in listed:
            try:
                t = pd.read_csv(os.path.join(folder, f"{name}.tsv"), sep="\t")
            except (OSError, ValueError):
                continue
            for col in ("target", "other", "pair", "activation", "inhibition"):
                if col in t:
                    names |= set(t[col].dropna().astype(str))
            if "gene" in t:
                genes |= set(t["gene"].dropna().astype(str))
        return {"targets": len(names), "genes": len(genes)}

    def _unlinked(self, text):
        """The sentences of a text that name no record entry ([run:ID], [finding:ID], [knowledge:N], [question:ID]
        or [dossier:ID]): listed for the agent to fix, the text kept (docs/adr/0027, D5)."""
        from . import results_report
        return results_report.unlinked(text, self.record.state())

    def propose_dossier(self, dossier_id, title, lead_findings, main_run, why):
        self._need()
        size = self._map_size(main_run)
        self.record.append("dossier_proposed", {"dossier_id": dossier_id, "title": title,
                                                "lead_findings": list(lead_findings), "main_run": main_run,
                                                "map_size": size, "why": why}, rc.AGENT)
        st = self.record.state()
        return {"dossier_id": dossier_id, "map_size": size,
                "map_level": None if size.get("measured") is False else
                size["targets"] >= rc.MAP_TARGETS or size["genes"] >= rc.MAP_GENES,
                "lead_findings": {f: {"checks_ran": rc.checks_ran(st["findings"][f]),
                                      "disagree": rc.disagreement(st["findings"][f]) or None,
                                      "checks": {k: c["outcome"] for k, c in st["findings"][f]["checks"].items()}}
                                  for f in lead_findings}}

    def choose_dossiers(self, chosen, user_words):
        """The user's choice of the results that go on to validation, asked in chat and relayed with their words
        (docs/adr/0032, D8)."""
        self._need()
        st = self.record.state()
        if not st["dossiers"]:
            raise AnalysisError("propose dossiers first (propose_dossier)")
        if not str(user_words or "").strip():
            raise AnalysisError("the user chooses in chat: pass their words (user_words) with the dossiers they chose")
        ids = [str(d) for d in chosen or []]
        self.record.append("dossier_chosen", {"dossier_ids": ids, "relayed": True,
                                              "user_words": str(user_words).strip()}, rc.AGENT)
        return {"chosen": ids}

    def record_dossier_part(self, dossier_id, part, text, run_ids=None, finding_ids=None, knowledge_ids=None,
                            kinds=None):
        self._need()
        if part == "panel_and_paragraph":
            raise AnalysisError("the panel and its paragraph are recorded by make_panel")
        unlinked = self._unlinked(text)
        payload = {"dossier_id": dossier_id, "part": part, "text": text, "run_ids": list(run_ids or []),
                   "finding_ids": list(finding_ids or []), "knowledge_ids": [int(k) for k in knowledge_ids or []],
                   "kinds": list(kinds or [])}
        self.record.append("dossier_part_recorded", payload, rc.AGENT)
        out = {"recorded": part, "open": [i for i in self.record.critic() if i["id"] == dossier_id]}
        if unlinked:
            out.update(unlinked=unlinked, next="name the entry each of these sentences rests on, and record the part "
                                               "again; the report lists sentences without one")
        return out

    def make_panel(self, dossier_id, tiles, paragraph=None):
        from . import panels, results_report
        self._need()
        st = self.record.state()
        d = st["dossiers"].get(dossier_id)
        if d is None:
            raise AnalysisError(f"no dossier {dossier_id}")
        runs, figs = st["runs"], st["figures"]
        placed = []
        for t in tiles:  # each tile's run and PNG: a recorded figure, or a run's figure (or one part of it)
            if not t.get("caption") or len(results_report.sentences(t["caption"])) != 1:
                raise AnalysisError("each tile has a caption of one sentence")
            if t.get("role") not in rc.ROLES:
                raise AnalysisError(f"each tile has a role: {', '.join(rc.ROLES)}")
            if t.get("figure_id"):
                f = figs.get(t["figure_id"])
                if f is None:
                    raise AnalysisError(f"no figure {t['figure_id']} in the record (read_record with what=figures)")
                open_notes = list(f.get("checks") or []) + list(f.get("notes") or [])
                if f.get("strict") is False and open_notes:  # kept while exploring: a panel holds the standard
                    from .question_modules.figures import STRICT_FIX
                    raise AnalysisError(f"figure {t['figure_id']} was kept while exploring, with: "
                                        f"{'; '.join(open_notes)}. For a panel, {STRICT_FIX}, with its caption and "
                                        "data; then make the panel again")
                rid, path = f["run_id"], f["paths"]["png"]
            else:
                rid = t.get("run_id")
                if rid not in runs:
                    raise AnalysisError(f"no run {rid}: a tile names a figure_id, or a run_id")
                name = f"figure_part_{t['part']}.png" if t.get("part") else "figure.png"
                path = os.path.join(self._run_folder(rid), name)
            if not os.path.exists(path):
                raise AnalysisError(f"run {rid} has no {os.path.basename(path)}")
            placed.append({**t, "run_id": rid, "path": path})
        first = runs.get(placed[0]["run_id"]) if placed else None
        if first is None or (first["run_id"] != d["main_run"] and first["what"] != runs[d["main_run"]]["what"]):
            raise AnalysisError(f"the first tile is the main run's figure ({d['main_run']}), or a rerun of the same "
                                "analysis")
        if placed[0]["role"] != "overview":
            raise AnalysisError("the first tile, the main run's figure, is the overview")
        folder = os.path.join(self.project, "dossiers")
        os.makedirs(folder, exist_ok=True)
        out, supplementary = (os.path.join(folder, f"{dossier_id}-{n}.png") for n in ("panel", "supplementary"))
        tiles_out = []
        for figure, path in (("main", out), ("supplementary", supplementary)):
            these = [t for t in placed if (t["role"] in rc.MAIN_ROLES) == (figure == "main")]
            if not these:
                supplementary = None
                continue
            for p, t in zip(panels.compose([t["path"] for t in these], path), these):
                fid = t.get("figure_id") or (f"{t['run_id']}:figure" if f"{t['run_id']}:figure" in figs else None)
                tiles_out.append({"letter": p["letter"], "run_id": t["run_id"], "figure_id": fid,
                                  "part": t.get("part"), "caption": t["caption"], "role": t["role"],
                                  "figure": figure, "scaled": p["scaled"]})
        unlinked = self._unlinked(paragraph) if paragraph else []
        if paragraph:
            self.record.append("dossier_part_recorded", {"dossier_id": dossier_id, "part": "panel_and_paragraph",
                                                         "text": paragraph, "figure": out,
                                                         "supplementary": supplementary, "tiles": tiles_out},
                               rc.AGENT)
        res = {"panel": out, "supplementary": supplementary, "tiles": tiles_out, "recorded": bool(paragraph)}
        if unlinked:
            res.update(unlinked=unlinked, next="name the entry each of these sentences rests on, and make the panel "
                                               "again with the paragraph")
        return res

    SOURCE_ROWS = 2000  # rows of a run's table the number check reads: enough for a target's genes, bounded

    def _result_sources(self, finding_ids):
        """What the numbers of a result's text come from: its findings' statements' runs, their outputs, and each
        module run's summary, diagnostics and first rows."""
        import pandas as pd
        st = self.record.state()
        out = []
        runs = [r for f in finding_ids for r in (st["findings"].get(f) or {}).get("run_ids") or []]
        targets = {str((st["findings"].get(f) or {}).get("target")) for f in finding_ids} - {"None"}
        for rid in dict.fromkeys(runs):
            run = st["runs"].get(rid) or {}
            out.append(str(run.get("output") or ""))
            folder = os.path.join(self._jobs_root(), str(run.get("job_id") or rid))
            res, _ = threads.read_run(folder)
            if res:
                out.append({"summary": res.get("summary"), "diagnostics": res.get("diagnostics")})
            path = os.path.join(folder, "result.tsv")
            if os.path.exists(path):  # a run's table: its rows of the findings' targets, or its first rows
                try:
                    t = pd.read_csv(path, sep="\t")
                    if targets and "target" in t and t["target"].astype(str).isin(targets).any():
                        t = t[t["target"].astype(str).isin(targets)]
                    out.append(t.head(self.SOURCE_ROWS).to_dict("records"))
                except (OSError, ValueError):
                    pass
        for f in finding_ids:
            for checks in rc.checks_by_target(st["findings"].get(f) or {}).values():  # each target's, where several
                out.append({k: c.get("numbers") for k, c in checks.items()})
        return out

    def report_result(self, result_id, title=None, finding_ids=None, figure=None, text=None, decision=None,
                      user_words=None, reason=None, answers=None, raised_by=None, samples=None, next_steps=None):
        """A result of the report (docs/build_goal_session6_fixes.md, item 9): proposed from the findings of a thread,
        its figure drawn by code from their runs; the user confirms or drops it in chat, relayed with their words, or,
        when they run Pertura unattended, the analysis decides with its reason. next_steps: the user's choices after
        it, ranked by code with what the studies read did after results like it (_ranked_steps)."""
        from . import text_numbers
        from .results_report import sentences, unlinked
        self._need()
        st = self.record.state()
        if decision is not None and not any((title, finding_ids, figure, text, next_steps)):
            return self._decide(result_id, decision, user_words, reason)
        now = st["results"].get(result_id)
        if now:  # what a version leaves out stays as the last one had it: a decision given with the whole result
            # again had a changed text dropped, and r1 went through 11 versions on GSE250558 (P7)
            title, finding_ids, text = title or now["title"], finding_ids or now["finding_ids"], text or now["text"]
            figure = figure or now["figure"]
        taken = None
        if title and finding_ids and not figure:  # its findings' latest figure (docs/adr/0050, D6: run A was refused)
            theirs = {r for f in finding_ids for r in (st["findings"].get(f) or {}).get("run_ids") or []}
            drawn = [fid for fid, fg in st["figures"].items() if fg.get("run_id") in theirs]
            own = [r for r in theirs if (st["runs"].get(r) or {}).get("figure")]  # a module run's own figure
            if not drawn and not own:
                raise AnalysisError("a result has its figure, and none of its findings' runs saved a figure yet: "
                                    "save one (pt.save_figure in the notebook) or name a run of theirs (figure)")
            if drawn:  # a figure saved again keeps its place in the record's order but takes a new seq
                figure = taken = max(drawn, key=lambda fid: st["figures"][fid].get("seq") or 0)
            else:  # the live trial of 2026-11-17: a states run drew its figure, but no saved figure named it
                figure = taken = max(own, key=lambda r: st["runs"][r].get("seq") or 0)
        if not title or not finding_ids or not figure:
            raise AnalysisError("a result has its title (its conclusion), the findings it rests on (finding_ids) "
                                "and its figure")
        if figure in st["figures"]:
            figure_id, figure_run = figure, st["figures"][figure]["run_id"]
        elif figure in st["runs"]:
            theirs = {r for f in finding_ids for r in (st["findings"].get(f) or {}).get("run_ids") or []}
            if figure not in theirs and not rc.runs_read(st, figure) & theirs:  # the rule before what it drew
                raise AnalysisError(f"a result's figure is drawn from the runs of its findings "
                                    f"({', '.join(sorted(theirs)) or 'none'}): {figure} is none of them and read "
                                    "none of them; the cell that draws it reads what they made (their files, "
                                    "tables or objects). Where it draws a new run, as a figure fixed after its "
                                    "review does, add that run to the finding it supports first (reword_finding "
                                    "with run_ids), then report the result again")
            drawn = [fid for fid, f in st["figures"].items() if f.get("run_id") == figure]
            own = f"{figure}:figure"
            if own not in st["figures"] and len(drawn) > 1:
                raise AnalysisError(f"run {figure} saved {len(drawn)} figures ({', '.join(drawn)}): give the one this "
                                    "result shows, by its ID")
            # a run that saved no figure stands for it until the lead's planned figure is drawn (figures_to_draw):
            # on GSE267740 (d3a7dcc) the refusal put drawing inside the analysis loop
            figure_id, figure_run = (own if own in st["figures"] else drawn[0] if drawn else None), figure
        else:
            near = sorted(f for f in st["figures"] if f.rsplit("/", 1)[-1] == str(figure).rsplit("/", 1)[-1])
            raise AnalysisError(f"no figure or run {figure!r}: a result's figure is one the record holds (save_figure "
                                "in the notebook) or a run of its findings, whose figure it is"
                                + (f"; the record holds {', '.join(near)}" if near else ""))
        steps, moved = self._ranked_steps(st, figure_run, finding_ids, next_steps) if next_steps else (None, None)
        same = {"title": str(title).strip(), "finding_ids": list(finding_ids), "figure": figure_id or figure_run,
                "text": str(text or "").strip() or None}
        if now and all(now.get(k) == v for k, v in same.items()) and (
                not answers or now.get("answers") == str(answers).strip()) and (
                not samples or now.get("samples") == str(samples).strip()) and (
                not steps or now.get("next_steps") == steps):  # proposed again (docs/adr/0038, D5)
            if decision is not None:
                return {**self._decide(result_id, decision, user_words, reason), "version": now["version"]}
            return {"result_id": result_id, "version": now["version"], "unchanged": True,
                    "next": "this result is as proposed already; nothing new was recorded"}
        checked =(text_numbers.unsupported(" ".join(sentences(text)), self._result_sources(finding_ids))
                   if text else [])  # before the result is written: a failure here leaves no half call
        self.record.append("result_reported", {"result_id": result_id, "title": str(title).strip(),
                                               "finding_ids": list(finding_ids), "figure": figure_id or figure_run,
                                               "figure_run": figure_run, "text": str(text or "").strip() or None,
                                               **({"answers": str(answers).strip()} if answers else {}),
                                               **({"raised_by": str(raised_by).strip()} if raised_by else {}),
                                               **({"samples": str(samples).strip()} if samples else {}),
                                               **({"next_steps": steps} if steps else {})},
                           rc.AGENT)
        kept = (decision is None and self.unattended and now and now.get("decision") == "confirmed"
                and now.get("by_analysis"))  # d3a7dcc: 8 versions of 2 results, each confirmed again by the agent
        if kept:  # a dropped result reported again is revived, so only a confirmation carries
            decision, reason = "confirmed", f"kept from version {now['version']}: {now.get('reason') or ''}".strip()
        decided = (self._decide(result_id, decision, user_words, reason) if decision is not None else
                   self._keep_the_report())
        st = self.record.state()
        r = st["results"][result_id]
        out = {"result_id": result_id, "version": r["version"], "rests_on": rc.result_facts(st, r)}
        if taken:
            out["figure_taken"] = (f"{taken}: the latest figure its findings' runs saved; report it again with figure "
                                   "to show another")
        if moved:
            out["recommended_moved"] = moved
        known = self._known_now(result_id=result_id)
        if known:
            out["known"] = known
        if text:
            loose = unlinked(text, st)
            if loose:
                out["sentences_without_an_entry"] = loose
                out["entry_marker"] = "name each sentence's entry in brackets: [finding:ID] or [run:ID]"
            numbers = checked
            if numbers:
                out["numbers_not_in_its_runs"] = (f"{', '.join(numbers)}: found in none of its findings' runs; give "
                                                  "the runs they come from, or take the number from them")
        # the reviews this result makes due are the state list's, given in this reply (docs/adr/0050, D2)
        if len(st["results"]) == 1 and r["version"] == 1:
            from . import knowledge
            fg = (knowledge.load().get("FG1") or {}).get("text") or ""
            if fg:
                out["figure_forms_of_published_screens"] = fg.split("\n", 1)[-1].strip()
        claims = [f for f in r["finding_ids"] if (st["findings"].get(f) or {}).get("kind") in CHECKED_KINDS
                  and all((st["findings"].get(f) or {}).get(k) for k in rc.CLAIM)]
        if r["version"] == 1 and not claims:  # GSE267740 (d3a7dcc): ten descriptions, "checks: none" said ten times
            out["no_claim_checked"] = ("none of its findings has a claim its checks run on: give the finding it stands "
                                       "on its claim in place (reword_finding with target, feature, direction and "
                                       "kind)")
        if r["version"] == 1:  # once per result, as Biomni's critic asks once: in three of three runs this question,
            # asked after the report, narrowed a result that the skeptic and the lead had passed
            out["check_its_weakest_part"] = ("Find the part of this result you would not yet stand behind, check it "
                                             "now with a run, and reword the result where it does not hold. Whether "
                                             "a finding holds: raise a doubt and run its test yourself (raise_doubt, "
                                             "then the analysis tool it names, or run_code for what no tool does, "
                                             "then answer_doubt with that run; "
                                             "a long test can go to a worker, open_thread with doubt_id). Where "
                                             "another choice "
                                             "leads: open a fork (open_thread with fork_of the finding, choice and "
                                             "matters).")
        if kept:
            out["kept"] = (f"confirmed in version {now['version']}; drop it here (report_result with decision "
                           "dropped and your reason) if the new text no longer holds")
        elif decision is None:
            out["next"] = ("decide whether it stays once the skeptic's doubts on its findings are answered "
                           "(report_result with decision and your reason; a later version keeps your confirmation)"
                           if self.unattended
                           else "show the user the result in chat, with its figure; they confirm or drop it "
                                "(report_result with decision and their words)")
        return {**out, **decided}

    def _decide(self, result_id, decision, user_words, reason):
        """A result confirmed or dropped: relayed with the user's words, or, unattended, decided with a reason."""
        words = str(user_words or "").strip()
        mine = self.unattended and not words
        self.record.append("result_decided", {"result_id": result_id, "decision": decision, "user_words": words,
                                              "relayed": not mine, **({"by_analysis": True,
                                                                       "reason": str(reason or "").strip()}
                                                                      if mine else {})}, rc.AGENT)
        return {"result_id": result_id, "decision": decision, **({"decided_for_the_user": True} if mine else {}),
                **self._keep_the_report()}

    def _build_report(self, items=None):
        """The report written from the record as it stands (pertura.results_report.build): its path."""
        from . import results_report
        st = self.record.state()
        try:
            design = self._confirmed()
        except AnalysisError:
            design = None
        screen = None
        if os.path.exists(os.path.join(self._screen_dir(), "screen.json")):
            with open(os.path.join(self._screen_dir(), "screen.json"), encoding="utf-8") as fh:
                screen = json.load(fh)
        title = f"Pertura: {os.path.basename(str(self.deposit)).rstrip(os.sep) or 'the screen'}"
        items = self.record.critic() if items is None else items
        try:  # what the record holds open, for the end line: a report without that clause rather than none
            open_now = self._open_at_the_end()
        except Exception as e:
            _logged(e)
            open_now = None
        return results_report.build(st, self._jobs_root(), os.path.join(self.project, "report"), title,
                                    open_items=items, design=design,
                                    screen=screen, screen_dir=self._screen_dir() if screen else None,
                                    first_look=self._first_look_sections(st), survey=self._survey_report(),
                                    open_now=open_now)

    def _keep_the_report(self):
        """The report kept written by code (P5): again where a result is proposed or decided and at the research
        lead's complete, so one stands whenever a session ends (half the runs with follow-ups ended at their caps).
        It never breaks the reply it follows, and that reply says where it is: without that, the main agent wrote it
        again after each result, 12 to 16 times a run (P7)."""
        try:
            return {"report": f"written again by code: {self._build_report()}"}
        except Exception as e:  # written again by the next result, or write_report, which says why
            _logged(e)
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {}

    def write_report(self, story=None):
        """The report, whatever is open: it states the critic's items (docs/adr/0022, section 13). story: its opening,
        recorded, then the report opens with it; without one ever given, the research lead's last story (docs/adr/0036,
        D6). Code keeps it written as results are proposed (_keep_the_report)."""
        from . import results_report
        self._need()
        reviews = self.record.state().get("reviews") or []
        if story is None and reviews and not self.record.state().get("story"):
            story = reviews[-1]["story"]
        if story is not None:
            self.record.append("story_written", {"text": str(story).strip()}, rc.AGENT)
        st = self.record.state()
        items = self.record.critic()
        path = self._build_report(items)
        links = results_report.check_links(path, st)
        shown = results_report.results_of(st)
        out = {"report": path, "pages": self._report_page(1)["pages"],
               "results": [{"result_id": r["result_id"], "title": r["title"], "version": r["version"],
                            "decision": r.get("decision") or "proposed", "words": len(str(r.get("text") or "").split())}
                           for r in shown],
               "links": {"sentences": links.get("sentences"), "linked": links.get("linked"),
                         "unlinked": (links.get("unlinked") or [])[:5]},
               "open": len(items)}  # its text is on the user's page; reading it back was 15% of a run's replies (P5)
        unanswered = [i["id"] for i in items if i["kind"] == "doubt without an answer"]
        if unanswered:  # a doubt a finding answers stays open until its answer is recorded (the replay of four stops)
            out["doubts_without_an_answer"] = (", ".join(unanswered) + ": the report shows them open. Answer each "
                                               "in one line, 'Doubt <id>: <outcome>. <reason, with its run>', with "
                                               "the run that answers it, a finding's run among them, or, where no run "
                                               "can test it, state it as a limit (the design or a tool)")
        surveyed = self._survey_report()
        if surveyed and not surveyed["complete"]:  # docs/adr/0045, D3: the report says so until its last table
            out["survey"] = (f"the survey is still running ({surveyed['ended']} of {len(surveyed['rows'])} comparisons "
                             "ended), as the report says; code writes the report again once they have ended, while "
                             "this session runs" + ("; unattended, wait for them (wait_for_runs) before you end"
                                                    if self.unattended else ""))
        if not self.unattended and not rc.enough_stands(st):
            out["enough"] = ("the record holds no 'enough' from the user: code keeps the report written as results "
                             "are proposed; the recorder records their words when they say the analysis can end")
        if not shown:  # development session 3, problem 20
            out = {"first": "No result is in this report. Propose each result in your reply: its title (its "
                            "conclusion), the claims it rests on, its figure and its text; the user confirms or "
                            "drops it in chat.", **out}
        return out


def _kind_method(tool):
    """A tool by the field's kinds as a method of the service, as every tool is (AnalysisService.call)."""
    def run(self, view=None, cells=None, by=None, against=None, targets=None, choices=None, inputs=None,
            wait=RUN_WAIT, why=None, thread_id=None, step=None, **options):
        return self.run_kind(tool, view, cells, by, against, targets, choices, inputs, wait, why, thread_id, step,
                             **options)
    run.__name__ = run.__qualname__ = tool
    run.__doc__ = kinds.KINDS[tool][0]
    return run


for _tool in kinds.KINDS:
    setattr(AnalysisService, _tool, _kind_method(_tool))
