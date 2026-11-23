"""One set of words for states (docs/build_goal_session6_fixes.md, item 24.3). A run is running, done or failed; a
result is found, not found, measured (a description, with no test to pass) or not testable, with its reason. The
record and the modules keep their own codes (a module's status, a runner's state, a check's outcome), so earlier
records read as they did; the replies, the views and the report say these words, through the mappings here."""
RUN_STATES = ("running", "done", "failed")
RESULT_STATES = ("found", "not found", "measured", "not testable")

# a runner's or a notebook cell's state -> the run's
RUN = {"running": "running", "queued": "running", "starting": "running", "done": "done", "ok": "done",
       "failed": "failed", "error": "failed", "stopped": "failed", "lost": "failed", "timeout": "failed"}
# a module's status -> the result's
RESULT = {"detected": "found", "not_detected": "not found", "measured": "measured", "not_enough_data": "not testable"}


def run_state(state):
    """The run's state in the words above; an unknown state is said as it is."""
    return RUN.get(str(state), str(state))


def result_state(status):
    """The result's state in the words above; an unknown status is said as it is."""
    return RESULT.get(str(status), str(status))


def result_words(result):
    """{result, why} of a module's result.json: its state, and for one not testable the reason its summary gives."""
    status = (result or {}).get("status")
    out = {"result": result_state(status)}
    if status == "not_enough_data":
        out["why"] = str((result or {}).get("summary") or "not enough data")
    return out
