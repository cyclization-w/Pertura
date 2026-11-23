"""Pertura's recorder (docs/adr/0051, D2-D3): after a turn, what the agents said and did becomes a recording in the
project's record/inbox on the analysis server; the service applies it before its next tool call.

    python -S recorder.py --session ID --transcript PATH [--agent TYPE --agent-id ID] [--wait]

The hooks start it detached at Stop and SubagentStop, and run it in place (--wait) before write_report.
- Code reads the fixed forms: a doubt's answer ("Doubt <id>: <outcome>. <reason>"), a claim's correction ("Claim <id>:
  targets A, B; feature <kind> <name>; direction up|down"), the user's pick of a next step ("... option <rank>: ..."),
  and a result kept or dropped ("keep r1", "drop r2").
- A small model reads the rest: `claude -p --model haiku --json-schema` with the schema of pertura.recording.Recording
  (recording_schema.json, written from it and checked against it by the tests).
- The skeptic reads each new result's claims with their runs' outputs (claude -p --model sonnet, Doubts).
Each recording names the transcript range it covers and carries the user's messages in it, so the service refuses a
range it has applied and a user's decision whose words are not in the user's message. Each is kept beside the status
files (recordings/). A recorder never stands in Claude's way: errors go to recorder_errors.log and the mark stays,
so the next run covers the same text."""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_file as sf  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = "PERTURA_RECORDER"  # set in the models' own sessions, so their hooks never start a recorder again
SAFE_HOST = re.compile(r"^[A-Za-z0-9._@-]+$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/@+-]+$")
MAX_TEXT = 120_000  # characters of a turn the model reads; the latest part is kept
FAILURES = 2  # readings of the same text with no recording before the mark moves past it
READING_FRESH = 1500  # seconds a skeptic's .reading marker counts as a reading that runs (the harness's own limit)
# a run's ID as the analysis service gives it: a notebook cell's (run0083-e96c5f) or a job's (20261117T015243-C-...-1ecf5e)
RUN_ID = re.compile(r"\brun\d{4}-[0-9a-f]{6}\b|\b\d{8}T\d{6}-[A-Za-z0-9_.-]+?-[0-9a-f]{6}\b")
OUTCOMES = ("holds, narrowed", "holds", "weakens", "overturns", "still open", "reworded, not tested",
            "stated as a limit")
# each fixed line may open an agent's text block, which turn_of marks "AGENT: ", and may be an item of a list
LEAD = r"^\s*(?:AGENT:\s*)?(?:(?:[-*+]|\d+[.)])\s+)?"
DOUBT_LINE = re.compile(LEAD + r"Doubt\s+([A-Za-z0-9._-]+):\s*(" + "|".join(re.escape(o) for o in OUTCOMES) +
                        r")\.\s*(.+)$", re.I | re.M)
CLAIM_LINE = re.compile(LEAD + r"Claim\s+([A-Za-z0-9._-]+):\s*targets\s+([^;]+);\s*feature\s+(gene|gene_set|score|state)"
                        r"\s+([^;]+);\s*direction\s+(up|down)\b([^\n]*)", re.I | re.M)
# the pieces a claim's checks need, after its direction: ; labelling <run> ; table <name> ; genes <a, b> ; cells <col>=<v>
CLAIM_PIECE = re.compile(r";\s*(labelling|table|genes|cells)\s+([^;]+)", re.I)
# the question a result answers, raised by an earlier result or finding (revision 3)
FROM_LINE = re.compile(LEAD + r"[*_]*From\s+(r\d+|R\d+|[A-Za-z][A-Za-z0-9._-]*)[*_]*:[*_]*\s*(\S[^\n]*)$", re.M)
PICK = re.compile(r"option\s+(\d+)\s*:", re.I)
# a choice that could change a reading, which a worker tries the other way: "Choice: <what> = <its value>; other:
# <the alternative>"
CHOICE_LINE = re.compile(LEAD + r"[*_]*Choice[*_]*:\s*(.+?)\s*=\s*(.+?)\s*;\s*other\s*:\s*(.+?)\s*$",
                         re.I | re.M)
# no threshold or definition of the agent's own could change an answer: "No choice: <why none could>"
NO_CHOICE_LINE = re.compile(LEAD + r"[*_]*No choice[*_]*:\s*(.+?)\s*$", re.I | re.M)
KEEP_DROP = re.compile(r"\b(keep|confirm|drop)\s+(r\d+)\b", re.I)
ROLE = {"pertura:worker": "a worker"}
UNRECORDED = ("pertura:design-auditor",)  # its design goes through the design tools, never the record's claims
# what a subagent's recording may hold: a worker finds; restating the record's claims or a doubt's standing is not
# its to record (the trial of 2026-11-17). The research lead's and the figure reviewer's reviews left with them
# (2026-11-20)
ROLE_FIELDS = {"pertura:worker": ("claims", "threads", "thread_updates"),
               "main": ("claims", "results", "user_decisions", "threads", "thread_updates", "doubt_answers",
)}
READ_FROM_PROSE = "Read by the recorder from the agent's words: "  # a doubt's answer not given in its one-line form
FIELDS = ("claims", "results", "user_decisions", "threads", "thread_updates", "doubt_answers")
HARNESS = ("Stop hook feedback", "[Request interrupted", "Caveat:")  # text in a user turn that is not the user's
INSTRUCTIONS = """You record one turn of a Perturb-seq analysis for its record. Read the turn below and fill the JSON
schema with only what the text states; leave a list empty where the turn holds none. Never infer.
- claims: each claim the agents state as found that names perturbed targets, a feature (a gene, a gene set, a saved
  score or a cell state), and a direction (higher: up; lower: down), with the run IDs the turn's tool replies name for
  it; for a gene set the agent defined, its genes as the text or code lists them; for a state, the states run that
  labelled it where the text names one. Where the agent's words limit the cells (one sample, model, state or
  condition), give them in its words (population) and, where RECORD's cell_columns hold them, as {column: value}
  (cells); a count of the units where it holds ("in 4 of 7 tumours") belongs to the statement and limits no cells;
  where it compared against every other target's cells, say so (reference). A statement without targets,
  feature and direction is not a claim. Use the agent's own words.
- results: each result the agent proposes to the user for the report, new or changed (title, its paragraph in the
  agent's words, the claims it rests on by position in your claims or by recorded finding ID, its figure, and the next
  steps it offers); a summary or restatement of a result is none. Where its question was
  raised by an earlier result or finding (a "From <id>:" line, or words such as "following r1"), give that ID
  (raised_by) and the question (answers).
- user_decisions: only from messages marked USER, with their exact words and message uuid, where the user keeps or
  drops a result, picks a next step, says enough, or gives knowledge of their own. While RECORD's next_steps_awaiting
  holds a menu and nothing else waits on the user, a message that assents ("go ahead", "do what you propose") picks
  its recommended step (refers_to its rank); the rest of that message is read on its own. An answer to a design
  question, or "I don't know", is no decision.
- threads: a new question only; one that RECORD's open_threads holds is named by its ID in thread_updates, never
  opened again. The user's own question is a thread raised by the user, in their exact words, with its scope: one
  question, or the whole screen when they hand it over; a remark in answer to the agent is not a question of theirs.
- thread_updates, doubt_answers: as the text states them.
The record's IDs so far are in RECORD; refer to them as they are written. The SPEAKER line says who wrote the turn."""
DOUBT_INSTRUCTIONS = """You are the skeptic of a Perturb-seq analysis. For each claim below, read its numbers, its
checks and the runs' code and output. Raise a doubt only where the evidence could be wrong in a way published screens
check and a test on these data could show: a state defined by the genes scored (circularity), an effect that rests on
one guide, a claim with no check behind it. A limit no such test settles (two experiments, one capture) is not a doubt.
Each claim carries its checks, run by code (each guide, the other tests, the units, the calibration): one that
disagrees is evidence. Name the result that would change the judgement. Raise none where the claim holds.
Raise at most 3 doubts, the weightiest first, each one that would change how the result reads. Raise none the record
already holds, in any wording (DOUBTS THE RECORD HOLDS).
Then say in one sentence what the user's question still lacks after this result (the_question_lacks), from their
words and the facts given; leave it empty if nothing. Your doubts and that sentence are information for the analyst,
who decides what to do next."""
WAIT_FOR_CHECKS = 900  # seconds the background skeptic waits for the cited claims' checks before it reads: with
# tumours as units a claim's checks take several minutes
CHECK_WORDS = 3000  # characters of a claim's checks run's words the skeptic reads with the claim
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console window from the detached recorder


def _log(text):
    try:
        path = os.path.join(sf.home(), "recorder_errors.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def _folder():
    path = os.path.join(sf.home(), "recorder")
    os.makedirs(os.path.join(path, "recordings"), exist_ok=True)
    return path


def read_entries(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("uuid") and e.get("type") in ("user", "assistant"):
                    out.append(e)
    except OSError:
        pass
    return out


def _short(x, n):
    text = x if isinstance(x, str) else json.dumps(x, default=str)
    return text if len(text) <= n else text[:n] + " ..."


def turn_of(entries, after=None, handled=(), subagent=False, typed=()):
    """The entries after the uuid `after`, as the model reads them, with the user's messages by uuid and the range's
    first and last uuid. A user message whose text the prompt hook already applied (handled, by hash) is shown and
    marked, so its decision is not taken twice. In a subagent's transcript the user's turn is the main agent's brief:
    shown as BRIEF, never as the user's words. Where the prompt hook kept the prompts the user sent (typed, by hash),
    only those are the user's words."""
    if after:
        uuids = [e["uuid"] for e in entries]
        entries = entries[uuids.index(after) + 1:] if after in uuids else entries
    lines, users = [], {}
    for e in entries:
        content = (e.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if e["type"] == "user" and b.get("type") == "text":
                text = str(b.get("text") or "")
                if text.startswith("<") or text.startswith(HARNESS) or not text.strip():
                    continue  # the harness's own notices and hooks' feedback, not the user's words
                if subagent:
                    lines.append("BRIEF: " + text)
                    continue
                if typed and prompt_hash(text) not in typed:  # in the transcript as a user turn, but not sent by them
                    lines.append("NOTE (not the user's words): " + text)
                    continue
                users[e["uuid"]] = text
                mark = " (its keep, drop or pick applied already)" if prompt_hash(text) in handled else ""
                lines.append(f"USER [{e['uuid']}]{mark}: {text}")
            elif e["type"] == "user" and b.get("type") == "tool_result":
                lines.append("REPLY: " + _short(b.get("content"), 1500))
            elif b.get("type") == "text":
                lines.append("AGENT: " + str(b.get("text") or ""))
            elif b.get("type") == "tool_use":
                lines.append(f"CALL {str(b.get('name', '')).split('__')[-1]} " + _short(b.get("input"), 600))
    text = "\n".join(lines)
    return (text[-MAX_TEXT:], users, entries[0]["uuid"] if entries else None,
            entries[-1]["uuid"] if entries else None)


def turn_runs(entries, after=None):
    """The run IDs the turn's tool calls, replies and text name, in order, read by code from their whole text: the runs
    a claim of the turn can rest on when its words name none (the trial of 2026-11-17: the agent's claims named no run,
    and none was recorded). A turn a background task's notice begins names its runs in that notice (effa573)."""
    if after:
        uuids = [e["uuid"] for e in entries]
        entries = entries[uuids.index(after) + 1:] if after in uuids else entries
    found = []
    for e in entries:
        content = (e.get("message") or {}).get("content")
        for b in [{"type": "text", "text": content}] if isinstance(content, str) else content or []:
            if isinstance(b, dict) and b.get("type") in ("tool_result", "tool_use", "text"):
                for rid in RUN_ID.findall(json.dumps(b.get("content", b.get("input", b.get("text"))), default=str)):
                    if rid not in found:
                        found.append(rid)
    return found


def prompt_hash(text):
    return hashlib.sha256(str(text).strip().encode("utf-8")).hexdigest()[:16]


def fixed_forms(agent_text, users, handled=()):
    """What code reads without a model: the doubts' answers and the claims' corrections in the agents' text, and the
    user's picks and decisions in their own fixed words."""
    rec = {"claims": [], "doubt_answers": [], "user_decisions": [], "from_lines": [], "choice_lines": [],
           "no_choice_lines": []}
    for m in DOUBT_LINE.finditer(agent_text):
        outcome = next(o for o in OUTCOMES if o.lower() == m.group(2).lower())
        rec["doubt_answers"].append({"doubt_id": m.group(1), "outcome": outcome, "reason": m.group(3).strip()})
    for m in CLAIM_LINE.finditer(agent_text):
        claim = {"statement": re.sub(LEAD, "", m.group(0)).strip(), "replaces": m.group(1),
                 "targets": [t.strip() for t in m.group(2).split(",") if t.strip()],
                 "feature": {"kind": m.group(3).lower(), "name": m.group(4).strip()},
                 "direction": m.group(5).lower(), "run_ids": RUN_ID.findall(m.group(6))}
        for piece, value in CLAIM_PIECE.findall(m.group(6)):
            value, piece = value.strip(), piece.lower()
            if piece == "labelling":
                claim["feature"]["labelling"] = value.split()[0]
            elif piece == "table":
                claim["feature"]["name"] = "table:" + value.split()[0]
            elif piece == "genes":
                claim["feature"]["genes"] = [g.strip() for g in value.split(",") if g.strip()]
            elif "=" in value:
                col, _, vals = value.partition("=")
                vals = [v.strip() for v in vals.split(",") if v.strip()]
                claim["cells"] = {col.strip(): vals[0] if len(vals) == 1 else vals}
        rec["claims"].append(claim)
    for m in CHOICE_LINE.finditer(agent_text):
        what, value, other = (x.strip().strip("*_").strip() for x in m.groups())
        rec["choice_lines"].append({"what": what, "value": value, "other": other.rstrip(".")})
    for m in NO_CHOICE_LINE.finditer(agent_text):
        rec["no_choice_lines"].append({"reason": m.group(1).strip().strip("*_").strip()})
    for m in FROM_LINE.finditer(agent_text):
        ref = m.group(1).lower() if re.fullmatch(r"[Rr]\d+", m.group(1)) else m.group(1)
        rec["from_lines"].append({"raised_by": ref, "answers": m.group(2).strip()})
    for uuid, text in users.items():
        if prompt_hash(text) in handled:
            continue
        pick = PICK.search(text)
        if pick and "next step" in text.lower():
            rec["user_decisions"].append({"kind": "next_step", "words": text.strip(), "message_uuid": uuid,
                                          "refers_to": pick.group(1)})
        for m in KEEP_DROP.finditer(text):
            rec["user_decisions"].append({"kind": "drop" if m.group(1).lower() == "drop" else "keep",
                                          "words": m.group(0), "message_uuid": uuid, "refers_to": m.group(2)})
    return rec


def ask_model(model, instructions, text, schema, timeout=240):
    """The model's answer in the schema, or None. It runs with no settings, no MCP server and no tools, from an empty
    folder, and with GUARD set, so no hook of its session records it again."""
    with tempfile.TemporaryDirectory() as cwd:
        try:
            r = subprocess.run(["claude", "-p", "--model", model, "--setting-sources", "local", "--strict-mcp-config",
                                "--restricted", "--output-format", "json", "--json-schema", json.dumps(schema)],
                               input=instructions + "\n\n" + text,  # on stdin: a turn outgrows Windows' command line
                               cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                               env=dict(os.environ, **{GUARD: "1"}), **NO_WINDOW)
            out = json.loads(r.stdout or "{}")
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            _log(f"model {model}: {e}")
            return None
    got = out.get("structured_output")
    return got if isinstance(got, dict) else None


def workspace_config(project):
    """The workspace's config on the server (the plugin's workspace_config option, or, where Git Bash rewrote it, the
    one the server names in its replies, kept in the project's status file); None where neither is a safe path."""
    config = os.environ.get("CLAUDE_PLUGIN_OPTION_WORKSPACE_CONFIG", "")
    if not SAFE_PATH.match(config) and project:
        config = str((sf.read_json(sf.project_path(sf.project_key(project))) or {}).get("workspace_config") or "")
    return config if SAFE_PATH.match(config) else None


def remote_paths(project):
    """The server's code folder and Python for the reads over ssh: the plugin's options, or, where one fails SAFE_PATH
    (Git Bash on Windows rewrites a /home/... value before Python sees it), the ones the server names in its replies,
    kept in the project's status file. (None, None) where neither holds."""
    code = os.environ.get("CLAUDE_PLUGIN_OPTION_REMOTE_CODE", "")
    python = os.environ.get("CLAUDE_PLUGIN_OPTION_REMOTE_PYTHON", "")
    if not (SAFE_PATH.match(code) and SAFE_PATH.match(python)) and project:
        st = sf.read_json(sf.project_path(sf.project_key(project))) or {}
        code, python = str(st.get("remote_code") or ""), str(st.get("remote_python") or "")
    return (code, python) if SAFE_PATH.match(code) and SAFE_PATH.match(python) else (None, None)


def _ssh(host, command, stdin=None, timeout=60):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", host, command], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, **NO_WINDOW)


def digest_of(host, project):
    try:
        r = _ssh(host, "cat " + shlex.quote(project + "/record/digest.json"), timeout=20)
        return r.stdout if r.returncode == 0 else "{}"
    except (OSError, subprocess.TimeoutExpired):
        return "{}"


def send(host, project, tag, recording):
    """The recording into the project's inbox, whole or not at all (written to .part, then renamed)."""
    inbox = project + "/record/inbox"
    q = shlex.quote
    cmd = f"mkdir -p {q(inbox)} && cat > {q(inbox + '/' + tag + '.json.part')} && mv {q(inbox + '/' + tag + '.json.part')} {q(inbox + '/' + tag + '.json')}"
    r = _ssh(host, cmd, stdin=json.dumps(recording), timeout=60)
    if r.returncode != 0:
        raise OSError(f"send {tag}: {r.stderr[:300]}")
    with open(os.path.join(_folder(), "recordings", tag + ".json"), "w", encoding="utf-8") as fh:
        json.dump(recording, fh, indent=1)


def run_outputs(host, project, run_ids):
    code, python = remote_paths(project)
    if not code or not run_ids:
        return {}
    q = shlex.quote
    try:
        r = _ssh(host, f"cd {q(code)} && PYTHONPATH=src {q(python)} -m pertura.recording run-outputs {q(project)} "
                       + " ".join(q(x) for x in run_ids), timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _schema(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return json.load(fh)


def _decision_key(u):
    """One decision of one message: a message may hold several ("Keep r2. Yes, the analysis is enough.", effa573)."""
    return u.get("message_uuid"), u.get("kind"), str(u.get("refers_to") or "")


def merged(fixed, model):
    """The model's recording with code's fixed forms first: a doubt code answered, or a user's decision code read, is
    not taken again from the model."""
    rec = {k: list(v) for k, v in (model or {}).items() if isinstance(v, list)}
    answered = {a["doubt_id"] for a in fixed["doubt_answers"]}
    read = {_decision_key(u) for u in fixed["user_decisions"]}
    rec["doubt_answers"] = fixed["doubt_answers"] + [
        {**a, "reason": READ_FROM_PROSE + str(a.get("reason") or "")} for a in rec.get("doubt_answers", [])
        if a.get("doubt_id") not in answered]
    rec["user_decisions"] = fixed["user_decisions"] + [u for u in rec.get("user_decisions", [])
                                                       if _decision_key(u) not in read]
    rec["claims"] = fixed["claims"] + rec.get("claims", [])
    return rec


def _mark_path(session, who):
    return os.path.join(_folder(), f"{session}-{re.sub(r'[^A-Za-z0-9._-]', '_', who)}.mark.json")


def unread(session, transcript, who="main"):
    """Whether the transcript holds entries past the reader's mark."""
    entries = read_entries(transcript)
    return bool(entries) and entries[-1]["uuid"] != (sf.read_json(_mark_path(session, who), {}) or {}).get("last")


def record_now(session, transcript, wait):
    """The main agent's text recorded by a recorder started in the background, waited on until it is sent, it ends,
    or `wait` seconds pass: a hook killed at its timeout leaves no lock behind. True once sent."""
    done = os.path.join(_folder(), f"{session}-{time.time_ns()}.done")
    p = start(session, transcript, done=done)
    deadline = time.time() + wait
    while p and time.time() < deadline:
        if os.path.exists(done):
            try:
                os.remove(done)
            except OSError:
                pass
            return True
        if p.poll() is not None and not os.path.exists(done):
            return False
        time.sleep(1)
    return False


def readings_running(session, fresh=READING_FRESH):
    """The skeptic's readings of the session that run now: their .reading markers younger than `fresh` seconds (an
    older one was left by a reader that died)."""
    folder, now = _folder(), time.time()
    out = []
    for n in os.listdir(folder):
        if n.startswith(f"{session}-") and n.endswith(".reading"):
            try:
                if now - os.path.getmtime(os.path.join(folder, n)) < fresh:
                    out.append(n)
            except OSError:
                continue
    return out


def stop_facts(host, project):
    """What the record holds open at a turn's end: the inbox applied first, its checks queued for the server's own
    tick (apply-inbox --queue-only), then pertura.recording stop-facts. Without the workspace's config the inbox is
    left to the agent's next call. None where the server cannot be read."""
    code, python = remote_paths(project)
    if not code or not (host and SAFE_HOST.match(host) and project and SAFE_PATH.match(project)):
        return None
    q = shlex.quote
    config = workspace_config(project)
    if config:
        try:
            _ssh(host, f"cd {q(code)} && PYTHONPATH=src {q(python)} -m pertura.mcp_server apply-inbox {q(config)} "
                       f"--queue-only --project {q(project)}", timeout=90)
        except (OSError, subprocess.TimeoutExpired):  # what stop-facts reads is then as the last call left it
            pass
    return read_stop_facts(host, project)


def read_stop_facts(host, project):
    """pertura.recording stop-facts over ssh, which only reads; None where it cannot be read."""
    code, python = remote_paths(project)
    if not code or not (host and SAFE_HOST.match(host) and project and SAFE_PATH.match(project)):
        return None
    q = shlex.quote
    try:
        r = _ssh(host, f"cd {q(code)} && PYTHONPATH=src {q(python)} -m pertura.recording stop-facts "
                       f"{q(project + '/record')}", timeout=60)
        out = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return out if isinstance(out, dict) else None


def record_turn(session, transcript, agent="main", agent_id=None, host=None, project=None, ask=ask_model, later=None,
                at_stop=False):
    """One recording of the transcript since its mark, sent to the inbox; the mark moves once it is sent. Returns the
    tag sent, or None. With later (a list), the skeptic's reading of its results is left there for the caller to run
    once the session's lock is free, since it waits for the claims' checks. at_stop: the turn's end, where the skeptic
    reads the recording's claims though it proposes no result (a task's answers are a file, not results)."""
    if agent in UNRECORDED:
        return None
    st = sf.session_status(session) or {}
    host = host or os.environ.get("CLAUDE_PLUGIN_OPTION_ANALYSIS_HOST") or st.get("analysis_host")
    project = project or st.get("project")
    if not (host and SAFE_HOST.match(host) and project and SAFE_PATH.match(project)):
        return None
    who = agent_id or agent
    subagent = agent != "main"
    mark_path = _mark_path(session, who)
    mark = sf.read_json(mark_path, {}) or {}
    handled = set(sf.read_json(os.path.join(_folder(), f"{session}.prompts.json"), []) or [])
    typed = set(sf.read_json(os.path.join(_folder(), f"{session}.user_prompts.json"), []) or [])
    entries = read_entries(transcript)
    text, users, first, last = turn_of(entries, mark.get("last"), handled, subagent, typed)
    # the turn's runs from its start, the user's last message, so a claim that sums up earlier runs keeps them where
    # the text is read a call at a time
    runs = (turn_runs(entries, list(users)[-1]) if users else
            list(dict.fromkeys((mark.get("turn_runs") or []) + turn_runs(entries, mark.get("last")))))
    if not last or not text.strip():
        return None
    fixed = fixed_forms(text, users, handled)
    speaker = ROLE.get(agent, "the main agent" if agent == "main" else agent)
    model = ask("haiku", INSTRUCTIONS, f"SPEAKER: {speaker}\nRECORD: {digest_of(host, project)}\nRUNS OF THIS TURN: "
                f"{', '.join(runs) or 'none'}\nTURN:\n{text}", _schema("recording_schema.json"))
    if model is None:  # no recording: the text is read again, up to FAILURES readings, then left with a line
        tries = (int(mark.get("failures") or 0) if mark.get("failed_from") == first else 0) + 1
        if tries < FAILURES:
            sf.write_json(mark_path, {**mark, "failed_from": first, "failures": tries})
            _log(f"{session} {who}: no recording of the text from {first}; the next reading reads it again")
            return None
        _log(f"{session} {who}: no recording of the text from {first} after {tries} readings; only code's fixed "
             "forms are sent, and the mark moves past it")
    rec = merged(fixed, model)
    keep = ROLE_FIELDS.get(agent, ())  # only the main agent proposes results and answers doubts; only the user decides
    rec.update({f: [] for f in FIELDS if f not in keep})
    if subagent:
        rec["threads"] = [t for t in rec.get("threads", []) if t.get("raised_by") != "the user"]
    hooked = {(k, d["kind"], str(d.get("refers_to") or "")) for k, t in users.items() if prompt_hash(t) in handled
              for d in fixed_forms("", {k: t})["user_decisions"]}
    rec["user_decisions"] = [u for u in rec["user_decisions"]  # what the prompt hook applied is not taken again
                             if _decision_key(u) not in hooked]
    for t in rec.get("threads", []):
        if agent in ROLE:
            t["raised_by"] = ROLE[agent]
    if not subagent and fixed["from_lines"]:
        rec["from_lines"] = fixed["from_lines"]
    for key in ("choice_lines", "no_choice_lines"):  # the main agent's alone
        if not subagent and fixed[key]:
            rec[key] = fixed[key]
    tag = time.strftime("%Y%m%d-%H%M%S-") + hashlib.sha256(f"{session}{who}{last}".encode()).hexdigest()[:8]
    rec.update(range={"session": session, "agent": who, "first": first, "last": last}, user_messages=users,
               turn_runs=runs)
    send(host, project, tag, rec)
    read = rec.get("results") or (at_stop and rec.get("claims"))  # the skeptic reads them (docs/adr/0051, D3)
    turn = _users_turn(session, transcript) if at_stop else None
    if turn and mark.get("read_at_stop") == turn:  # one fresh reading at a turn's end; a later Stop's recording
        read = False  # still applies and its checks run
    kept = turn if read and turn else mark.get("read_at_stop")
    sf.write_json(mark_path, {"last": last, "time": time.time(), "turn_runs": runs,
                              **({"read_at_stop": kept} if kept else {})})
    if read:
        if later is not None:
            later.append((host, project, tag, rec))
            return tag
        try:
            read_by_the_skeptic(host, project, tag, rec, ask)
        except Exception as e:  # the skeptic's reading never undoes the recording
            _log(f"skeptic {tag}: {e}")
    return tag


def _users_turn(session, transcript):
    """The user's turn the Stop is in: its last message's uuid, else the turn the Stop hook kept (hooks._turn)."""
    import stop_words
    return (stop_words.turn_key({"transcript_path": transcript})
            or (sf.read_json(sf.session_path(session, None, ".stop.json"), {}) or {}).get("turn"))


def checks_of(host, project, finding_ids):
    """The cited claims' checks as code ran them ({finding: {check: {outcome, reason}}}; none for a claim the record
    does not hold yet), read over ssh as run_outputs reads the runs."""
    code, python = remote_paths(project)
    if not code or not finding_ids:
        return {}
    q = shlex.quote
    try:
        r = _ssh(host, f"cd {q(code)} && PYTHONPATH=src {q(python)} -m pertura.recording checks {q(project)} "
                       + " ".join(q(x) for x in finding_ids), timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def read_by_the_skeptic(host, project, tag, rec, ask=ask_model, wait=0):
    """The skeptic reads the claims a recording's results cite, with their runs and the checks code ran on them. With
    wait (seconds), it first waits for the claims to be applied and checked (the old skeptic read the checks; the
    reader without them missed a one-guide claim on its replay of 2026-11-17)."""
    claims = rec.get("claims") or []
    ids = [f"{tag}-c{i}" for i in range(1, len(claims) + 1)]
    results = rec.get("results") or []
    cited = (sorted({ids[k] for r in results for k in r.get("claims") or [] if 0 <= k < len(ids)}
                    | {f for r in results for f in r.get("finding_ids") or []}) if results else ids)
    if not cited:
        return None
    # a claim that names no run rests on its turn's runs, as the record applies it
    shown = [dict(c, finding_id=i, run_ids=c.get("run_ids") or rec.get("turn_runs") or [])
             for c, i in zip(claims, ids) if i in cited]
    checks = checks_of(host, project, cited)
    deadline = time.time() + wait

    def unchecked():  # a claim kept as a description gets no checks: never waited for
        described = {d.get("finding_id") for d in (read_stop_facts(host, project) or {}).get("described") or []
                     if isinstance(d, dict)}
        return [f for f in cited if not checks.get(f) and f not in described]
    while time.time() < deadline and unchecked():
        time.sleep(30)
        checks = checks_of(host, project, cited)
    analysis = sorted({r for c in shown for r in c["run_ids"]})
    checking = sorted({c.get("run_id") for f in checks.values() for c in (f or {}).values() if c.get("run_id")}
                      - set(analysis))
    got = run_outputs(host, project, analysis + checking)

    def checked(fid):
        """A claim's checks with the words its checks run wrote: each target's and each guide's numbers. Given with
        the claim, as checks listed apart, under a repeated claim's first ID, were read as none."""
        own = checks.get(fid) or {}
        if not own:
            return "none ran yet"
        rid = next((c.get("run_id") for c in own.values() if c.get("run_id")), None)
        said = re.sub(r"^\S+-c\d+: ", "", str((got.get(rid) or {}).get("output") or "").strip())[:CHECK_WORDS]
        return {"outcomes": {k: c.get("outcome") for k, c in own.items()}, **({"in_words": said} if said else {})}
    shown = [dict(c, checks=checked(c["finding_id"])) for c in shown]
    recorded = [{"finding_id": f, "checks": checked(f)} for f in cited if f not in ids]
    runs = {r: got[r] for r in analysis if r in got}
    facts = reader_facts(host, project)
    doubts = ask("sonnet", DOUBT_INSTRUCTIONS, "CLAIMS, each with its checks run by code:\n" + json.dumps(shown, indent=1)
                 + "\nRECORDED FINDINGS CITED, each with its checks: " + json.dumps(recorded, indent=1)
                 + "\nRUNS:\n" + _short(runs, 60_000)
                 + "\nTHE USER'S WORDS:\n" + _short(facts.get("the_users_words") or [], 3_000)
                 + "\nDOUBTS THE RECORD HOLDS:\n" + _short(facts.get("doubts_the_record_holds") or [], 8_000)
                 + "\nTARGETS THE USER NAMED WITH NO RESULT YET (code's count): "
                 + json.dumps(facts.get("targets_the_user_named_with_no_result_yet") or []),
                 _schema("doubts_schema.json"), timeout=400)
    if isinstance(doubts, dict):  # sent whatever it raised, with the counts of what it was given
        doubts.update(range=dict(rec["range"]), cited=cited, received={
            "claims_with_checks": sum(1 for f in cited if checks.get(f)), "runs": len(runs),
            "users_words": len(facts.get("the_users_words") or []),
            "record_doubts": len(facts.get("doubts_the_record_holds") or [])})
        send(host, project, tag + "-z-doubts", doubts)
    return doubts


def reader_facts(host, project):
    """The facts the reviewer reads beside a result (pertura.recording.reader_facts), over ssh as the checks are."""
    code, python = remote_paths(project)
    if not code:
        return {}
    q = shlex.quote
    try:
        r = _ssh(host, f"cd {q(code)} && PYTHONPATH=src {q(python)} -m pertura.recording facts {q(project)}",
                 timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def record_prompt(event, host=None, project=None):
    """At UserPromptSubmit: the user's fixed words (a next step's pick, a result kept or dropped) go to the inbox at
    once, before the turn's first call, so the turn works on them; the prompt's hash marks them applied."""
    session, text = event.get("session_id"), str(event.get("prompt") or "")
    if session and text.strip():  # every prompt the user sends: only these are the user's words (revision 3)
        typed = os.path.join(_folder(), f"{session}.user_prompts.json")
        sf.write_json(typed, sorted(set(sf.read_json(typed, []) or []) | {prompt_hash(text)}))
    fixed = fixed_forms("", {"prompt-" + prompt_hash(text): text})
    if not session or not fixed["user_decisions"]:
        return None
    st = sf.session_status(session) or {}
    host = host or os.environ.get("CLAUDE_PLUGIN_OPTION_ANALYSIS_HOST") or st.get("analysis_host")
    project = project or st.get("project")
    if not (host and SAFE_HOST.match(host) and project and SAFE_PATH.match(project)):
        return None
    tag = time.strftime("%Y%m%d-%H%M%S-") + "prompt-" + prompt_hash(text)[:8]
    send(host, project, tag, {"user_decisions": fixed["user_decisions"],
                              "range": {"session": session, "agent": "prompt", "first": prompt_hash(text),
                                        "last": prompt_hash(text)},
                              "user_messages": {"prompt-" + prompt_hash(text): text}})
    path = os.path.join(_folder(), f"{session}.prompts.json")
    sf.write_json(path, sorted(set(sf.read_json(path, []) or []) | {prompt_hash(text)}))
    return tag


def _take(lock, wait):
    """The session's lock, True once taken: tried for up to 240 s where wait, else once. A lock older than 900 s was
    left by a recorder that died."""
    for _ in range(120 if wait else 2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 900:
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if wait:
                time.sleep(2)
    return False


def _again_path(session, who):
    return os.path.join(_folder(), f"{session}.again.{re.sub(r'[^A-Za-z0-9._-]', '_', str(who))}.json")


def _flags(session):
    return sorted(os.path.join(_folder(), n) for n in os.listdir(_folder())
                  if n.startswith(f"{session}.again.") and n.endswith(".json"))


def _take_flags(session):
    """The readings queued while this one ran, each taken once (its flag removed before it is read)."""
    out = []
    for path in _flags(session):
        req = sf.read_json(path, {}) or {}
        try:
            os.remove(path)
        except OSError:
            continue
        if req.get("transcript"):
            out.append(req)
    return out


def start(session, transcript, agent="main", agent_id=None, queue=False, done=None):
    """The recorder in the background, outliving the hook and the session's job (Windows: detached, own group, no
    window, out of the job). queue: where a reading of the session runs, leave it a flag to read again and end at
    once. done: a file made once this reading is sent, before the skeptic reads."""
    if os.environ.get(GUARD) or not session or not transcript:
        return False
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000
    else:
        kw["start_new_session"] = True
    args = [sys.executable, "-S", os.path.abspath(__file__), "--session", session, "--transcript", transcript,
            "--agent", agent, "--background"] + (["--agent-id", agent_id] if agent_id else []) + (
            ["--queue"] if queue else []) + (["--done", done] if done else [])
    try:
        return subprocess.Popen(args, **kw)
    except OSError:  # a job that forbids breaking away: started inside it
        kw["creationflags"] = kw.get("creationflags", 0) & ~0x01000000
        return subprocess.Popen(args, **kw)


def record_offline(transcript, out, ask=ask_model):
    """A finished run's transcript recorded turn by turn into the folder out, one Recording file per turn, sending
    nothing: for checking the recorder against the agents' own findings (the accuracy check before it ships). A
    headless run's stream-json transcript ends each turn with a result row; its messages may carry no uuid."""
    os.makedirs(out, exist_ok=True)
    turns, cur = [], []
    with open(transcript, encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "result":
                turns.append(cur)
                cur = []
            elif e.get("type") in ("user", "assistant") and not e.get("parent_tool_use_id"):
                cur.append({**e, "uuid": e.get("uuid") or f"line{n}"})
    turns.append(cur)
    written = []
    for i, entries in enumerate(t for t in turns if t):
        text, users, first, last = turn_of(entries)
        if not text.strip():
            continue
        runs = turn_runs(entries)
        rec = merged(fixed_forms(text, users), ask("haiku", INSTRUCTIONS, f"SPEAKER: the main agent\nRECORD: {{}}\n"
                                                   f"RUNS OF THIS TURN: {', '.join(runs) or 'none'}\nTURN:\n{text}",
                                                   _schema("recording_schema.json")))
        rec.update(range={"session": os.path.basename(transcript), "agent": "main", "first": first, "last": last},
                   turn_runs=runs)
        path = os.path.join(out, f"turn{i + 1:03d}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        written.append(path)
    return written


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["offline"]:  # python -S recorder.py offline TRANSCRIPT OUT_FOLDER
        print("\n".join(record_offline(argv[1], argv[2])))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--agent", default="main")
    ap.add_argument("--agent-id")
    ap.add_argument("--background", action="store_true", help="started by a hook: the skeptic waits for the checks")
    ap.add_argument("--queue", action="store_true", help="where a reading runs, leave it a flag and end")
    ap.add_argument("--done", help="a file made once this reading is sent")
    a = ap.parse_args(argv)
    later = [] if a.background else None
    lock = os.path.join(_folder(), f"{a.session}.lock")
    todo = [{"transcript": a.transcript, "agent": a.agent, "agent_id": a.agent_id, "at_stop": bool(a.done)}]
    queue = a.queue
    while True:  # one recorder at a time per session
        if not _take(lock, wait=not queue):
            if not queue:
                return 1
            if todo:  # the reading that runs reads this again when it ends
                sf.write_json(_again_path(a.session, a.agent_id or a.agent), todo[0])
            if not _take(lock, wait=False):
                break
            todo = []  # it ended meanwhile: the flag is this reading's to read
        try:
            todo = todo or _take_flags(a.session)
            while todo:
                for req in todo:
                    try:
                        record_turn(a.session, req["transcript"], req.get("agent") or "main", req.get("agent_id"),
                                    later=later, at_stop=bool(req.get("at_stop")))
                    except Exception as e:  # the mark stays: the next run covers the text
                        _log(f"{a.session} {req.get('agent')}: {type(e).__name__}: {e}")
                todo = _take_flags(a.session)
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass
        if not _flags(a.session):  # a flag left while the lock was released is read by whoever takes it next
            break
        queue = True
    if a.done:  # this reading is sent: the Stop hook that waits on it goes on
        open(a.done, "w").close()
    reading = os.path.join(_folder(), f"{a.session}-{os.getpid()}.reading")
    if later:  # while it reads, a harness that applies a run's last files waits for it
        open(reading, "w").close()
    try:
        for host, project, tag, rec in later or []:  # outside the lock: the next turn's recording never waits on it
            try:
                read_by_the_skeptic(host, project, tag, rec, wait=WAIT_FOR_CHECKS)
            except Exception as e:  # the skeptic's reading never undoes the recording
                _log(f"skeptic {tag}: {e}")
    finally:
        if later:
            try:
                os.remove(reading)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
