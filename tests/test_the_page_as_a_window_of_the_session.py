"""The user's page as a second window of their Claude Code session (the page plan's step 3): a helper on the user's
machine carries the user's words from the page into the session and the conversation back, and hands Pertura's forms
to the page while it is open; every request carries a token only the page and the session's file hold, and a website's
origin or another host's name is refused."""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
if not os.path.isdir(STATUS):  # the plugin is not deployed to the analysis server
    raise unittest.SkipTest("no plugin folder here")
sys.path.insert(0, STATUS)
import forms_on_the_page  # noqa: E402
import page  # noqa: E402
import page_and_session_helper as helper  # noqa: E402
import status_file as sf  # noqa: E402

HELPER = os.path.join(STATUS, "page_and_session_helper.py")


class TestALineToTheModule(unittest.TestCase):
    def test_the_users_words_reach_the_module_whole_whatever_the_pipes_code_page(self):
        out = io.TextIOWrapper(io.BytesIO(), encoding="gbk")  # the helper's stdout on a Chinese Windows
        real, sys.stdout = sys.stdout, out
        try:
            helper.say({"ask": "为什么 Stat1 弱？“对照”"})
            out.flush()
            line = out.buffer.getvalue().decode("utf-8")  # as the module reads it
        finally:
            sys.stdout = real
        self.assertEqual(json.loads(line), {"ask": "为什么 Stat1 弱？“对照”"})


class TestTheHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "ask", "s1.json")
        self.proc = subprocess.Popen([sys.executable, "-S", HELPER, self.file], stdout=subprocess.PIPE, text=True)
        first = json.loads(self.proc.stdout.readline())
        self.port, self.token = first["port"], first["token"]
        os.makedirs(os.path.dirname(self.file))
        with open(self.file, "w", encoding="utf-8") as fh:  # as the module writes it
            json.dump({"port": self.port, "token": self.token, "updated": time.time() * 1000}, fh)

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()
        self.tmp.cleanup()

    def post(self, path, body, origin="null", host=None, token=True):
        data = json.dumps({**({"token": self.token} if token else {}), **body}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "text/plain", "Origin": origin,
                                              **({"Host": host} if host else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8") or "{}")

    def test_only_the_page_with_the_token_reaches_the_session(self):
        self.assertEqual(self.post("/ask", {"text": "x"}, token=False)[0], 403)
        self.assertEqual(self.post("/ask", {"text": "x"}, origin="https://a-website.example")[0], 403)
        self.assertEqual(self.post("/ask", {"text": "x"}, host="a-name-pointed-here.example")[0], 403)
        self.assertEqual(helper.helper_for("s1", self.tmp.name), {"port": self.port, "token": self.token})
        self.assertIsNone(helper.helper_for("s1", self.tmp.name, now=time.time() + helper.STALE + 5))  # gone stale

    def test_a_page_in_a_background_tab_does_not_count_as_open(self):
        self.post("/state", {"after": 0, "visible": False})
        self.assertEqual(self.post("/open", {}), (200, {"open": False}))  # a form stays in the terminal
        self.post("/state", {"after": 0, "visible": True})
        self.assertEqual(self.post("/open", {}), (200, {"open": True}))

    def test_the_users_words_go_to_the_session_and_the_conversation_comes_back(self):
        self.assertEqual(self.post("/ask", {"text": "Why is Stat1 weak?\n\n[From the page, about: finding f3]"})[0],
                         200)
        self.assertEqual(json.loads(self.proc.stdout.readline()),
                         {"ask": "Why is Stat1 weak?\n\n[From the page, about: finding f3]"})
        self.post("/said", {"who": "you", "text": "Why is Stat1 weak?", "where": "page"})
        self.post("/said", {"who": "pertura", "text": "Stat1 has 16 cells at 6h."})
        self.post("/said", {"who": "someone else", "text": "kept out"})
        code, state = self.post("/state", {"after": 0})
        self.assertEqual(code, 200)
        self.assertEqual([(x["who"], x["text"]) for x in state["said"]],
                         [("you", "Why is Stat1 weak?"), ("pertura", "Stat1 has 16 cells at 6h.")])
        self.assertEqual(self.post("/state", {"after": state["said"][-1]["n"]})[1]["said"], [])
        self.assertIsNone(state["form"])

    def test_a_form_goes_to_the_page_while_it_is_open_and_its_answer_comes_back(self):
        self.assertEqual(self.post("/form", {"request": {"message": "m"}, "wait": 5})[0], 409)  # no page has asked
        self.post("/state", {"after": 0})  # the page asks: it is open
        got = {}
        t = threading.Thread(target=lambda: got.update(r=self.post("/form", {"request": {
            "message": "Run these?", "requested_schema": {"type": "object", "properties": {"run_0": {
                "type": "boolean"}}}}, "wait": 20})))
        t.start()
        for _ in range(200):  # up to 20 s, on a busy machine
            form = self.post("/state", {"after": 0})[1]["form"]
            if form:
                break
            time.sleep(0.1)
        self.assertEqual(form["message"], "Run these?")
        self.assertEqual(self.post("/answer", {"id": "another", "action": "accept"})[0], 409)
        self.assertEqual(self.post("/answer", {"id": form["id"], "action": "accept",
                                               "content": {"run_0": True}})[0], 200)
        t.join(10)
        self.assertEqual(got["r"], (200, {"action": "accept", "content": {"run_0": True}}))
        self.assertIsNone(self.post("/state", {"after": 0})[1]["form"])

    def test_the_page_can_hand_a_form_back_to_the_terminal(self):
        self.post("/state", {"after": 0})
        got = {}
        t = threading.Thread(target=lambda: got.update(r=self.post("/form", {"request": {"message": "Run these?"},
                                                                            "wait": 20})))
        t.start()
        for _ in range(200):
            form = self.post("/state", {"after": 0})[1]["form"]
            if form:
                break
            time.sleep(0.1)
        started = time.time()
        self.assertEqual(self.post("/answer", {"id": form["id"], "action": "terminal"})[0], 200)
        t.join(10)
        self.assertEqual(got["r"][0], 409)  # no answer: the forms hook leaves the form to the terminal
        self.assertLess(time.time() - started, 10)  # at once, not when the wait ends

    def test_the_forms_hook_asks_the_page_and_otherwise_leaves_the_form_to_the_terminal(self):
        event = {"session_id": "s1", "mode": "form", "message": "Confirm?",
                 "requested_schema": {"type": "object", "properties": {}}}
        self.assertIsNone(forms_on_the_page.answer_from_the_page(event, self.tmp.name))  # the page is not open
        self.assertIsNone(forms_on_the_page.answer_from_the_page({**event, "session_id": "s2"}, self.tmp.name))
        self.post("/state", {"after": 0})
        got = {}
        t = threading.Thread(target=lambda: got.update(a=forms_on_the_page.answer_from_the_page(event, self.tmp.name)))
        t.start()
        for _ in range(200):  # up to 20 s, on a busy machine
            form = self.post("/state", {"after": 0})[1]["form"]
            if form:
                break
            time.sleep(0.1)
        self.post("/answer", {"id": form["id"], "action": "decline"})
        t.join(10)
        self.assertEqual(got["a"], {"action": "decline"})

    def test_the_forms_hook_does_not_wait_on_a_helper_that_is_gone(self):
        import socket
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        with open(os.path.join(self.tmp.name, "ask", "s3.json"), "w", encoding="utf-8") as fh:
            json.dump({"port": dead, "token": self.token, "updated": time.time() * 1000}, fh)
        event = {"session_id": "s3", "mode": "form", "message": "m", "requested_schema": {"type": "object"}}
        t0 = time.time()
        self.assertIsNone(forms_on_the_page.answer_from_the_page(event, self.tmp.name))
        self.assertLess(time.time() - t0, forms_on_the_page.OPEN_WAIT + 1)
        self.assertEqual(self.post("/open", {})[1], {"open": False})
        self.post("/state", {"after": 0})
        self.assertEqual(self.post("/open", {})[1], {"open": True})

    def test_the_helper_ends_as_soon_as_its_session_is_gone(self):
        proc = subprocess.Popen([sys.executable, "-S", HELPER, self.file, "120", "0.2"], stdout=subprocess.PIPE,
                                text=True)
        try:
            json.loads(proc.stdout.readline())
            proc.stdout.close()  # the session's end of the pipe closes, as when it ends or is killed
            self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            proc.kill()

    def test_the_helper_ends_once_its_file_goes_stale(self):
        proc = subprocess.Popen([sys.executable, "-S", HELPER, os.path.join(self.tmp.name, "none.json"), "1", "0.2"],
                                stdout=subprocess.PIPE, text=True)
        try:
            json.loads(proc.stdout.readline())
            self.assertEqual(proc.wait(timeout=10), 0)
        finally:
            proc.kill()
            proc.stdout.close()


class TestThePageWithAndWithoutTheSession(unittest.TestCase):
    def render(self, link):
        pg = page.fold(None, "run_code", {"what": "the score", "code": "..."},
                       {"run_id": "run0001-aa", "status": "ok", "figures_shown": ["/w/fig.png"]}, ["fig-1.png"],
                       now=100.0)
        pg = page.fold(pg, "record_finding", {"finding_id": "f1", "statement": "Ifnar1 lowers the score.",
                                              "run_ids": ["run0001-aa"]}, {"finding_id": "f1"}, [], now=200.0)
        return page.render(pg, "Pertura · screen_X", None, None, link)

    def test_with_the_session_the_page_talks_to_it(self):
        text = self.render({"port": 50123, "token": "t" * 32})
        self.assertIn('window.PERTURA_LINK = {"port": 50123, "token": "' + "t" * 32 + '"};', text)
        talk = text[text.index('<section class="view" id="conversation"'):]
        talk = talk[:talk.index("</section>")]
        for part in ('id="talk"', 'id="composer"', 'id="say"', 'id="form-box"'):
            self.assertIn(part, talk)
        finding = text[text.index('<section class="view" id="finding-f1"'):]
        self.assertIn('data-about="finding f1" data-title="Ifnar1 lowers the score."', finding[:finding.index("</section>")])
        self.assertIn('data-about="step run0001-aa"', text)
        self.assertIn('data-about="figure fig-1.png"', text)
        self.assertNotIn('data-copy="', text)  # no button copies an id while the session can be reached

    def test_without_it_the_page_reads_and_copies_ids(self):
        text = self.render(None)
        self.assertNotIn("PERTURA_LINK =", text)
        self.assertIn("Not connected to a session.", text)
        self.assertIn('data-copy="finding f1">Copy reference</button>', text)
        self.assertNotIn('id="composer"', text)

    def test_the_hook_writes_the_page_with_the_session_that_called(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "ask"))
            with open(os.path.join(root, "ask", "s9.json"), "w", encoding="utf-8") as fh:
                json.dump({"port": 50124, "token": "u" * 32, "updated": time.time() * 1000}, fh)
            call = {"session_id": "s9", "tool_name": "mcp__plugin_pertura_pertura__open_project", "tool_input": {},
                    "tool_response": [{"type": "text", "text": json.dumps({"project": "/ws/screen_X-1a2b3c4d"})}],
                    "cwd": "."}
            sf.record_call(call, root)
            key = sf.project_key("/ws/screen_X-1a2b3c4d")
            with open(os.path.join(root, "projects", key, page.PAGE_NAME), encoding="utf-8") as fh:
                self.assertIn('"port": 50124', fh.read())


if __name__ == "__main__":
    unittest.main()
