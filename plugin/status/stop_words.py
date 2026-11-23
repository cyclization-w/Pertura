"""What the Stop hook reads of the turn that would end: the agent's last reply and the user's last message.

A reply that waits on the user or on a job opens with the output style's status block, whose last line says what the
analysis waits on, in the chat's language ("Waiting on · nothing: ...", "在等 · 没有：..."); a short answer has none. The hook sends the agent back when that line
says nothing, when a run runs with no wait armed, when the pending list holds an item whose decider is the main
agent (each main-agent reply names the list, each item with its decider, so this works with the style off, as in
every headless run; docs/the_orchestration_record_proposals_decisions_pending_and_the_turn.md), or when the record
holds items open (open_items); never when the line says the user, when the user's last message asked a question or
said to stop, or while a wait is armed. The hook counts and caps the returns.

Standard library only, like every file of plugin/status.
"""
import json
import re

ARM = ("A run is still running and no wait is armed. Arm wait_for_runs (it returns to you when a run ends), then work "
       "on with what does not depend on it; with nothing else left, end the turn: its return wakes you.")
CARRY_ON = ("Nothing waits on the user: carry on with your next step in this turn. End it only for the user's own "
            "decisions (the design, what to learn, validation, a costly step) or when nothing is left you can do "
            "alone.")
MAIN = "main agent"  # the decider whose pending items send the agent back (analysis_service.PENDING)


def pending(items):
    """The Stop hook's reason when the pending list still holds items of the main agent's."""
    return (f"Still due before the turn ends: {', '.join(items)}. pertura_status gives each with what to do; end the "
            "turn for the user's decision.")
# the status block's last label, and the first words of its values, in each language the output style names
LABELS = ("**Waiting on**", "**在等**")
VALUES = {"you": ("you", "你", "您"), "a job": ("a job", "job", "一个任务", "任务"), "nothing": ("nothing", "没有")}
# a user's message that keeps the turn: a question, or a word to stop (a nudge withheld is the safe side). A word to
# stop counts as a whole word in a short message: a task's long text, or "the cells stop dividing", is not one
STOP_WORDS = ("stop", "pause", "wait", "停", "等一下", "等等", "先别")
STOP_SAID = re.compile(r"(?<![a-z_])(?:stop|pause|wait)(?![a-z_])|停|等一下|等等|先别")
SHORT = 200  # characters: a message to stop is a line, not a task
QUESTION_MARKS = ("?", "？")
# a user turn's text that is not the user's: this hook's own feedback (its words say "wait"), a compaction's summary,
# the harness's notices
NOT_THE_USER = ("Stop hook feedback", "[Request interrupted", "Caveat:", "This session is being continued", "<")


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None  # a tool's result, not a message
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def last_turn(event, tail=400):
    """(the agent's last reply, the user's last message) from the hook's input or its transcript; None where unknown."""
    reply, asked, _ = _last(event, tail)
    return reply, asked


def turn_key(event, tail=400):
    """The uuid of the user's last message, which starts the turn; None where the transcript's tail holds none."""
    return _last(event, tail)[2]


def _last(event, tail):
    reply = event.get("last_assistant_message") if isinstance(event.get("last_assistant_message"), str) else None
    asked = uuid = None
    path = event.get("transcript_path")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-tail:]
    except (OSError, TypeError):
        return reply, asked, uuid
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        text = _text(msg.get("content"))
        if text is None:
            continue
        if kind == "assistant" and reply is None:
            reply = text
        elif (kind == "user" and asked is None and not entry.get("isMeta")  # a loaded skill's text is meta, not the user
              and not text.lstrip().startswith(NOT_THE_USER)):
            asked, uuid = text, entry.get("uuid")
        if reply is not None and asked is not None:
            break
    return reply, asked, uuid


def waiting_on(reply):
    """'you', 'a job' or 'nothing' from the reply's status block, or None where the reply has none."""
    for line in str(reply or "").splitlines():
        for label in LABELS:
            if label in line:
                value = line.split(label, 1)[1].lstrip(" ·:：-").strip().lower()
                for what, starts in VALUES.items():
                    if value.startswith(starts):
                        return what
                return None
    return None


def user_needs_the_turn(asked):
    """Whether the user's last message asked a question or said to stop."""
    text = str(asked or "").strip().lower()
    return bool(text) and (text.endswith(QUESTION_MARKS) or (len(text) <= SHORT and bool(STOP_SAID.search(text))))


# the one-time asks of a session, where the record holds a finding (hooks.stop)
NO_TASK_LIST = ("You have findings but no task list: write the question as tasks (TaskCreate), one per thing asked "
                "with what to hand over (list, test, threshold, unit), and mark each completed when its answer stands, "
                "naming its finding.")
NO_CHOICE = ("Which thresholds or definitions of yours could change an answer? One line each, Choice: <what> = "
             "<yours>; other: <the common one>, and a worker tries it; or one line, No choice: <why none could>.")
TASK_ID = re.compile(r"\bTask #(\d+)")  # TaskCreate's result: "Task #<id> created ..."
DONE = ("completed", "deleted")


def todos_open(event):
    """The main agent's tasks not done (not a subagent's): each TaskCreate is a task, its id from its result (else
    its place in creation order), and its last TaskUpdate's status its state; open is neither completed nor deleted.
    An older Claude Code's last TodoWrite list counts too. None where the agent made neither."""
    try:
        with open(event.get("transcript_path"), encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, TypeError):
        return None
    tasks, made, todos, seen = {}, {}, None, set()  # made: a TaskCreate's call id -> its task id, until its result
    for line in lines:
        if not any(k in line for k in ('"TaskCreate"', '"TaskUpdate"', '"TodoWrite"', '"tool_result"')):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        # a request's copy of the messages ("api-request-blob") repeats the calls: only the turns themselves count
        if (entry.get("type") not in ("assistant", "user") or entry.get("isSidechain")
                or not isinstance(msg.get("content"), list)):
            continue
        for b in msg["content"]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("id"):  # a call counts once
                if b["id"] in seen:
                    continue
                seen.add(b["id"])
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            if b.get("type") == "tool_use" and b.get("name") == "TaskCreate":
                tid = str(len(tasks) + 1)
                tasks[tid] = {"subject": str(inp.get("subject") or inp.get("description") or ""), "status": "pending"}
                made[b.get("id")] = tid
            elif b.get("type") == "tool_use" and b.get("name") == "TaskUpdate":
                t = tasks.get(str(inp.get("taskId") or "").lstrip("#"))
                if t is not None:
                    t.update(status=str(inp.get("status") or t["status"]), subject=str(inp.get("subject") or
                                                                                      t["subject"]))
            elif b.get("type") == "tool_use" and b.get("name") == "TodoWrite":
                todos = [str(t.get("content")) for t in inp.get("todos") or []
                         if isinstance(t, dict) and t.get("status") != "completed"]
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in made:
                tid = made.pop(b["tool_use_id"])
                if b.get("is_error"):  # never made
                    tasks.pop(tid, None)
                    continue
                named = ((entry.get("toolUseResult") or {}).get("task") or {}).get("id") \
                    if isinstance(entry.get("toolUseResult"), dict) else None
                found = TASK_ID.search(json.dumps(b.get("content")))
                named = str(named or (found.group(1) if found else tid))
                if named != tid and named not in tasks:
                    tasks[named] = tasks.pop(tid)
    if not tasks and todos is None:
        return None
    return [t["subject"] for t in tasks.values() if t["status"] not in DONE] + (todos or [])


def open_items(facts, wait):
    """The Stop hook's reason when the record holds items open (pertura.recording stop-facts): the checks still
    running and the skeptic's reading where wait, the doubts with no answer, the choices no worker has tried, the
    task list's items not done, the answers that narrowed or withdrew a finding since the agent last wrote, and, for
    information, the claims kept as descriptions with what would give them checks."""
    out = ["The record still holds open items:"]
    if wait and facts.get("checks_running"):
        out.append(f"- checks still running on {', '.join(map(str, facts['checks_running']))}: arm wait_for_runs and "
                   "end the turn; its return wakes you with their verdicts.")
    if wait and facts.get("inbox"):
        out.append("- your last claims are not in the record yet: make one call (pertura_status), whose reply lists "
                   "what was recorded.")
    if wait and facts.get("reading"):
        out.append("- a fresh reading of your results is still running: its doubts come with your next call.")
    if facts.get("doubts_open"):
        out.append(f"- doubts with no answer: {', '.join(map(str, facts['doubts_open']))}. Answer each in one line: "
                   "Doubt <id>: <holds | holds, narrowed | weakens | overturns | stated as a limit>. <reason, with "
                   "its run>")
    if facts.get("todos_open"):
        out.append(f"- your task list still has: {'; '.join(map(str, facts['todos_open']))}. Finish each, or mark it "
                   "completed with why it cannot be done.")
    if facts.get("answers_to_update"):
        out.append("- " + "; ".join(f"doubt {a.get('doubt_id')} answered {a.get('outcome')}: update what you wrote "
                                    f"that rests on {a.get('finding_id')}: the answer or report"
                                    for a in facts["answers_to_update"] if isinstance(a, dict)) + ".")
    choices = [c for c in facts.get("choices_open") or [] if isinstance(c, dict)]
    if choices:
        out.append("- choices no worker has tried yet: " + "; ".join(f"{c.get('thread_id')} ({c.get('what')})"
                                                                   for c in choices)
                   + ". Hand each to a worker (agents/worker.md) in the background, or set it aside with its reason.")
    described = [d for d in facts.get("described") or [] if isinstance(d, dict)]
    if described:
        out.append("- kept as descriptions, with no checks (information): " + "; ".join(
            f"{d.get('finding_id')}: {d.get('to_check_it')}" for d in described[:8]))
    return "\n".join(out)
