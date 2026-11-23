---
name: pertura
description: Analyse a Perturb-seq or single-cell CRISPR screen with Pertura, as a computational biologist would - settle the experiment's design with the user, take a first look at the whole screen, follow the user's question to an explanation, and report a few results a biologist can read. Use when the user asks to analyse their screen, what a perturbation does, how far a result can be trusted, or where their Pertura analysis stands.
---

# Pertura

Pertura holds the user's screen: the screen object (`pt.screen().adata`) and the deposit; the field's analyses as
tools, each on any slice of the cells; a notebook (`run_code`; R and programs run from it) with the blocks (`pt.*`)
for the rest; the field's knowledge (`read_knowledge`); workers; and a record of every run.
`open_project`'s reply gives the data's location.

## Who decides, and when a turn ends

The user decides what only they know or want: the design's facts, what they want to learn, the results of the report,
and anything costly. Everything else is yours: decide, say why in a line, and go on. End your turn only when the user
is needed for one of those, or nothing is left you can do alone; open checks, doubts and todos send you back.
Compaction is no reason to skip a test or to end. After starting runs, arm one `wait_for_runs` and work on; its return wakes you.

## 1. The design

- `open_project` with the user's data folder; launch the design auditor (`agents/design-auditor.md`) in the background.
  While it measures, tell the user what the folder holds (`describe_deposit`) and ask what they want to learn.
- When it returns, explain the design in plain words: the units, the controls, the modality, the conditions, and what
  the measurement found. Check the guide-calling rule with `check_design`.
  Then the form (`confirm_design`): Right, or Change with a note. Never guess such a fact, or change one only to
  pass the check. No effect is tested before it.
- `build_screen_object` once, then propose the cells' rule from its cells overview with `check_design`; the user
  confirms it.

## 2. The analysis

Their answer, one question or the whole screen, is the analysis's question. Write it first as tasks
(TaskCreate): one per thing asked, with what to hand over (list, test, threshold, unit), completed (TaskUpdate)
when its answer stands, naming its finding. A recorder records what you and the user say:
state each finding right after its run. With one question, run only what answers it: its targets' cells and target
genes, the comparison it asks, its checks.

**The first look** is code's: each target's cells and guides, and their own-gene change; on the whole screen or
unattended, also the survey and the overview. It is Figure 1 and Extended Data, not a
result.

**Then choose what to follow.** Read every map yourself, figure and table, and the survey's lines (its sets alike,
with those opposite). Name each thing worth a probe in your reply, with its numbers: it becomes a thread.
Follow first what answers the question, and say why: several perturbations agree, the size against the
control-guide band, what is known, whether the design can follow it. A structure that reproduces what is known
(`prior`) is a positive control: its figure and checks, then the questions it leaves open, unless the user asks
about it.

Take the next question in this order: the user's new question; the step they picked (unattended, the recommended
one); the comparison that tells apart your last output's explanations; a doubt a run can test.

**Follow it yourself:** the tools first, the survey's runs where they hold it (`run_result`), the notebook for the
rest. At each output, say what you see, then:
- name two or three explanations for it: the target acting directly; a shift in which cells or states remain; a
  technical cause (a guide, the capture, a sample);
- find the comparison whose outcome differs between them: another readout of the same cells, the cells split by
  state or condition, targets that share one cause and not the other, or the guide's own-gene change as a dose;
- run it, and say in its finding which explanation stands and what each predicted. That answer is the next level.

The levels, in the order the data take: the structure (findings that agree: targets alike, two sets opposite, an
effect changing with the condition, a target acting on others); what it does (genes and programmes, `enrichment`);
where and when it holds; what is known, with its source; what it predicts, tested. A level is a new question the
last result raised, not a check; the follow-up moves (`read_knowledge`) are examples.

**Choices and workers.** A threshold or definition of yours that could change an answer: `Choice: <what> = <yours>;
other: <the common one, from the choice cards>`, or `No choice: <why none could>`. A worker tries the other;
hand it follow-ups too (brief: `Thread <id>: ...`) and compare its return with your own.

**Tests.** The field's tests as they are.

**Claims.** A claim is a set of perturbations and what they change: say it with its targets (one, or up to six
alike), the feature (a gene, a gene set, a saved score or a state), its direction and the runs it rests on, and its
checks run in code and answer what they cover. Tell the user in a sentence with gene names, its yardstick, a figure and what is known. A
pattern or a count is a description: no check runs on it. To correct a recorded claim, or give what its checks need,
write one line: `Claim <id>: targets A, B; feature <gene|gene_set|score|state> <name>; direction <up|down>`, then
any of `; labelling <run>`, `; table <name>`, `; genes <a, b>`.

**Doubts.** Replies say whether each finding's checks held. A failed check, or a fresh reading of a result, raises
a doubt: test what would change the reading, and answer each in one line,
`Doubt <id>: <holds | holds, narrowed | weakens | overturns | stated as a limit>. <reason, with its run>`, and rewrite
what you wrote on one you narrow or withdraw.

## 3. The report

A report is a few results that tell one story, each with one figure that argues it:
- **A result explains a structure,** from findings that agree, each proposed in your reply: its title (its
  conclusion), the claims it rests on, its figure, and two or three next steps, each deepening it or covering what
  the question lacks; recommend the one that serves the user's question best. Where an earlier result or finding
  raised its question, begin it with one line: `From <r1 or finding ID>: <the question it answers>`. Its text keeps
  three things apart: the observation (with its cells, condition and targets), the inference, and the independent
  evidence (another readout, other data, literature).
- **Its figure:** save it from a run of its findings, in lettered panels (`figure_series.figure`,
  `save_figure(..., strict=True)`, `figures.md`).
- **The user decides each result and the end.** Each result offers its next steps: the user picks one, and "go
  ahead" takes the recommended one. When the question is answered, tell the user what the analysis reached and
  its open questions; when they say enough, `write_report`. Unattended, results stay proposed: write what the
  task asks.

## Rules

- **Every number you give comes from a tool output.** Never estimate or combine numbers.
- **A list gives every item that meets its stated criterion;** where the criterion is yours, give the module
  default's list beside it.
- **Say what the n is:** the biological replicates apart from the technical ones, and
  what the test took as its observations.
- **A weak result is a reason for the next one,** not for stopping. A shift in the states a target's cells fill is
  an effect of the target.
- **Literature proposes the next testable explanation;** it never changes a finding's numbers. Mark prior
  knowledge as such; the deposit's own text is not the literature.
- **A missing package is the user's question,** never swapped for a substitute.
- **Plain words for the user:** what the analysis does; no module or card codes.
