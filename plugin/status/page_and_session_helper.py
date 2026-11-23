"""The user's page as a second window of their Claude Code session. The page is a file in the browser and cannot reach
the session, so this helper, on the user's machine, carries between them; the plugin's hooks module
(hooks/page_and_session.ts) starts it at the session's start and reads what it prints.

- The page asks for the conversation and any form waiting (POST /state), sends the user's words (POST /ask), and
  answers a form (POST /answer). The helper prints each message the user sent, {ask}, and the module submits it as
  the session's next turn, in the user's words.
- The module tells the helper what was said (POST /said): the user's prompts, the page's or the terminal's, and the
  answer of each turn of the main loop.
- Claude Code's Elicitation hook (status/forms_on_the_page.py) asks whether the page is open (POST /open: it asked for
  its state from the screen, not from a tab in the background, in the last PAGE_OPEN seconds), then hands Pertura's
  form over (POST /form) and gets the page's answer back; with no page open the form stays in the terminal.

The helper listens on 127.0.0.1, at a port the system picks. Every request carries the token drawn at start, which only
the page and the session's file under the status home hold, so a website open in the browser cannot put words into a
session that runs tools; a request from a website's origin (the page is a file, its Origin "null"), or one whose Host is
not this address, is refused. It prints one JSON line at start, {port, token}, and {alive} every few seconds. It ends as
soon as such a line cannot be written (the session ended or was killed), or once its file, which the module keeps
fresh, is gone or older than STALE seconds.

Standard library only: it runs on the user's machine, as the plugin's other hooks do.

    python -S page_and_session_helper.py <the session's file> [<stale seconds> <check seconds>]
    helper_for(session_id, root)  ->  {port, token} of the session's live helper, or None
"""
import hmac
import http.server
import itertools
import json
import os
import re
import secrets
import sys
import threading
import time

FOLDER = "ask"  # under the status home: <session id>.json, {port, token, updated (ms)}, written by the module
STALE = 120  # seconds without the module's beat, after which the helper ends and the page offers no window
PAGE_OPEN = 15  # seconds since the page last asked for its state while on the screen, within which it is open
FORM_WAIT = 300  # seconds a form waits for the page's answer before the terminal shows it
KEEP = 300  # the conversation's lines the helper keeps
MAX_TEXT = 8000
MAX_BODY = 200_000
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,}")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # control characters but tab and newline


def path_for(session_id, root):
    return os.path.join(root, FOLDER, f"{re.sub(r'[^A-Za-z0-9_.-]', '-', str(session_id))}.json")


def helper_for(session_id, root, now=None):
    """The port and token of the session's helper while its module keeps the file fresh, else None."""
    if not session_id:
        return None
    try:
        with open(path_for(session_id, root), encoding="utf-8") as fh:
            x = json.load(fh)
        port, token, updated = int(x["port"]), str(x["token"]), float(x["updated"]) / 1000
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = time.time() if now is None else now
    if now - updated > STALE or not TOKEN.fullmatch(token) or not 0 < port < 65536:
        return None
    return {"port": port, "token": token}


SAYING = threading.Lock()  # one line at a time, from the server's threads and the watch


def say(x):
    """One JSON line to the module, in ASCII: its \\u escapes survive the pipe's encoding, which on Windows is the
    system's code page (GBK on a Chinese system), not the UTF-8 the module reads."""
    with SAYING:
        sys.stdout.write(json.dumps(x) + "\n")
        sys.stdout.flush()


def clean(text):
    return CONTROL.sub("", str(text or "")).strip()[:MAX_TEXT]


class Window:
    """What the helper holds: the conversation, the form waiting with its answer, and when the page last asked."""

    def __init__(self):
        self.lock = threading.Condition()
        self.said, self.n = [], itertools.count(1)
        self.form, self.answer, self.seen = None, None, 0.0

    def add(self, who, text, where=None):
        with self.lock:
            self.said.append({"n": next(self.n), "who": who, "text": text, "where": where, "time": time.time()})
            del self.said[:-KEEP]

    def is_open(self):
        with self.lock:
            return time.time() - self.seen <= PAGE_OPEN

    def state(self, after, visible=True):
        """The conversation after line n, and the form waiting; a page on the screen (not a tab in the background)
        counts as open."""
        with self.lock:
            if visible:
                self.seen = time.time()
            return {"said": [x for x in self.said if x["n"] > after],
                    "form": {k: self.form[k] for k in ("id", "message", "schema", "deadline")} if self.form else None}

    def ask_form(self, request, wait=FORM_WAIT):
        """Hold a form for the page until it answers or the wait ends: the answer, or None (the terminal shows it)."""
        with self.lock:
            if time.time() - self.seen > PAGE_OPEN or self.form is not None:
                return None
            fid = secrets.token_hex(6)
            self.form = {"id": fid, "message": request.get("message"), "schema": request.get("requested_schema"),
                         "deadline": time.time() + wait}
            self.answer = None
            got = self.lock.wait_for(lambda: self.answer is not None and self.answer[0] == fid, timeout=wait)
            answer = self.answer[1] if got else None
            self.form, self.answer = None, None
            return answer

    def give_answer(self, fid, action, content):
        """The page's answer to the form waiting: accept (with its content), decline or cancel; or terminal, which
        hands the form back to the terminal at once."""
        with self.lock:
            if not self.form or self.form["id"] != fid or action not in ("accept", "decline", "cancel", "terminal"):
                return False
            self.answer = (fid, None if action == "terminal" else
                           {"action": action, **({"content": content} if action == "accept" else {})})
            self.lock.notify_all()
            return True


class Asked(http.server.BaseHTTPRequestHandler):
    def _answer(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "null")  # the page's own origin, a file's
        self.end_headers()
        self.wfile.write(data)

    def _from_here(self):
        """Neither a website's origin nor another host's name (a name an attacker points at this address)."""
        return (self.headers.get("Origin") in (None, "null")
                and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}")

    def do_OPTIONS(self):  # a preflight, should a browser send one
        if not self._from_here():
            return self._answer(403, {"error": "refused"})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if not self._from_here():
            return self._answer(403, {"error": "refused"})
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_BODY:
            return self._answer(400, {"error": "no body, or too long"})
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._answer(400, {"error": "not JSON"})
        if not isinstance(body, dict) or not hmac.compare_digest(str(body.get("token") or ""), self.server.token):
            return self._answer(403, {"error": "refused"})
        w = self.server.window
        if self.path == "/state":
            return self._answer(200, w.state(int(body.get("after") or 0), body.get("visible") is not False))
        if self.path == "/ask":
            text = clean(body.get("text"))
            if not text:
                return self._answer(400, {"error": "no text"})
            say({"ask": text})
            return self._answer(200, {"ok": True})
        if self.path == "/said":
            text = clean(body.get("text"))
            if text and body.get("who") in ("you", "pertura"):
                w.add(body["who"], text, body.get("where"))
            return self._answer(200, {"ok": True})
        if self.path == "/answer":
            content = body.get("content") if isinstance(body.get("content"), dict) else {}
            ok = w.give_answer(str(body.get("id") or ""), body.get("action"), content)
            return self._answer(200 if ok else 409, {"ok": ok})
        if self.path == "/open":  # the forms hook asks first, so that it never waits on a page that is not there
            return self._answer(200, {"open": w.is_open()})
        if self.path == "/form":
            request = body.get("request") if isinstance(body.get("request"), dict) else {}
            got = w.ask_form(request, float(body.get("wait") or FORM_WAIT))
            return self._answer(200, got) if got else self._answer(409, {"error": "the page is not open"})
        return self._answer(404, {"error": "no such address"})

    def log_message(self, *args):  # nothing on stderr
        pass


def watch(server, path, started, stale=STALE, every=5):
    """Ends the helper once its session is gone: as soon as a line to the module cannot be written (the session
    ended or was killed, closing its end of the pipe), or once its file is gone (after the start's grace) or older
    than stale seconds."""
    while True:
        time.sleep(every)
        try:
            say({"alive": True})
        except (OSError, ValueError):
            sys.stdout = open(os.devnull, "w")  # nothing left to say: the exit's own flush has nowhere to go
            server.shutdown()
            return
        try:
            with open(path, encoding="utf-8") as fh:
                updated = float(json.load(fh)["updated"]) / 1000
        except (OSError, ValueError, KeyError, TypeError):
            updated = started
        if time.time() - updated > stale:
            server.shutdown()
            return


def main(path, stale=STALE, every=5):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Asked)
    server.daemon_threads = True
    server.token, server.window = secrets.token_urlsafe(24), Window()
    say({"port": server.server_port, "token": server.token})
    threading.Thread(target=watch, args=(server, path, time.time(), stale, every), daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1], *[float(x) for x in sys.argv[2:4]])
