# Pertura

A Claude Code plugin that analyses a Perturb-seq screen with you: it checks the design with you on a form, takes
a first look at the whole screen, follows what the data and your question raise, runs the analyses on your
analysis server, and keeps every run, finding and doubt in a shared record. You steer it by answering its findings
in the chat. Start it with `/pertura`.

## The page of findings and figures

The terminal shows no images. After each Pertura tool call the plugin saves the figures the call
returned, and writes a page on your machine, `~/.claude/pertura/projects/<the project>/page.html`, laid out like a
paper:
- the overview: the analysis's state and the decisions waiting on you, the results with their figures, the latest
  findings and the open threads;
- a page for each result and each finding, its figure first, then its checks with their numbers, its doubts and
  their answers, what it rests on from outside the data, how its wording changed, and the steps behind it;
- the findings, the threads, the design as you confirmed it (each value with where it came from), the plan, the
  methods as the runs state them (packages and versions, tests, units, corrections), and the timeline of every step,
  the newest last.
The plugin opens it in your browser once when a session opens a project (the plugin's "Open the page in the browser"
option turns that off); the page reloads itself every few seconds, Ctrl K searches it, and a click enlarges a figure.
It needs nothing installed and never leaves your machine.

## The page as a second window of the session

In Claude Code the plugin's hooks module (`hooks/page_and_session.ts`) starts a small helper on your machine with each
session (`status/page_and_session_helper.py`, standard library only), and the page gains a Conversation view:
- what you write there becomes the session's next turn, in your words, once the session is free;
- each answer shows there, beside what you wrote in the terminal;
- while the page is open, Pertura's forms appear there instead of the terminal (`status/forms_on_the_page.py`), and
  go back to the terminal if they wait unanswered for 5 minutes;
- "Ask about this" on a finding, a result, a figure or a step puts it beside your next message.
The helper listens on 127.0.0.1 only and takes a request only with a random token that the page and the session's own
file hold, so a website open in your browser cannot send words into the session. Permission prompts stay in the
terminal. In other agent tools, or with no helper running, the page is for reading, and each "Copy ID" button copies an
id to paste into the chat.

The analysis agent itself takes few figures into its context, which it reads again on every call: each run's own
figure, once, when the run's result is opened, and the others when it asks (`show_figures`, or the figure's entry
in the record). Pertura's own agents (the worker, the reviewers) see theirs as before.

## Each agent's model

`/config` lists one row per Pertura agent ("Model of the workers", and so on): opus, sonnet, haiku or fable.
"default" keeps the agent's own: the main conversation's model, which `/model` sets. The plugin applies the choice
each time the agent is launched.

## A long analysis: compaction at 200,000 tokens

A Pertura session runs for hours, and every call reads the whole conversation again, so its cost grows with its
length. Compacting earlier keeps it down; the record keeps the plan, the findings and the runs, and after a
compaction the plugin asks the agent to read the analysis's state. Once, in `~/.claude/settings.json`:
```json
"env": {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000"}
```
(A plugin cannot set it for you.)

## The analysis's state at a glance

Two steps, once:

1. **The status line** (the stage, the findings and those awaiting your reply, and the running jobs, below the
   prompt): add this to
   `~/.claude/settings.json`:
   ```json
   "statusLine": {"type": "command", "command": "python -S \"$HOME/.claude/pertura/status_line.py\"", "refreshInterval": 1}
   ```
2. **The output style** (every reply opens with the stage, the findings, the next step and the wait, on you
   or on a job): run `/output-style` and choose Pertura.

The status line needs a `python` command (Python 3.8 or later, nothing installed beyond the standard library). The
plugin copies the script to `~/.claude/pertura/` when a session starts. It reads a small file there that the
plugin keeps current after each Pertura tool call, and never contacts the server itself. While a job runs, the
plugin checks once a minute over ssh whether it has finished, by reading the job's folder with plain shell
commands.

The rows of Pertura's own agents (the worker, the skeptic) below the prompt need no step:
the plugin sets them.
