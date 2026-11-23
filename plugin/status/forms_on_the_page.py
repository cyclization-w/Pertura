"""Claude Code's Elicitation hook (hooks/hooks.json): while the user's page is open, Pertura's form goes to the page,
which shows it in its conversation, and the page's answer goes back to Claude Code as the hook's output: accepted with
its content, declined or cancelled. With no page open, no helper, or no answer in time, the hook prints nothing and
Claude Code shows the form in the terminal, as without the page. Claude Code passes the request on stdin.

    python -S forms_on_the_page.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_and_session_helper as helper  # noqa: E402
import status_file as sf  # noqa: E402

OPEN_WAIT = 3  # seconds to learn whether the page is open


def answer_from_the_page(event, root=None):
    """The page's answer to the form ({action, content}), or None when the terminal is to show it."""
    if event.get("mode", "form") != "form" or not isinstance(event.get("requested_schema"), dict):
        return None
    link = helper.helper_for(event.get("session_id"), root or sf.home())
    if not link:
        return None
    request = {k: event.get(k) for k in ("message", "requested_schema", "mcp_server_name", "elicitation_id")}
    try:  # first whether the page is open, at once: a helper gone or stuck leaves the form to the terminal
        if not _post(link, "/open", {}, OPEN_WAIT).get("open"):
            return None
        got = _post(link, "/form", {"request": request}, helper.FORM_WAIT + 30)
    except (urllib.error.URLError, OSError, ValueError):  # gone, not open (409), or no answer in time
        return None
    return got if got.get("action") in ("accept", "decline", "cancel") else None


def _post(link, path, body, timeout):
    data = json.dumps({"token": link["token"], **body}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{link['port']}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def main():
    try:
        got = answer_from_the_page(json.loads(sys.stdin.read() or "{}"))
    except Exception:  # a hook never stands in Claude's way: the terminal shows the form
        got = None
    if got:
        out = {"hookEventName": "Elicitation", "action": got["action"]}
        if got["action"] == "accept":
            out["content"] = got.get("content") or {}
        print(json.dumps({"hookSpecificOutput": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
