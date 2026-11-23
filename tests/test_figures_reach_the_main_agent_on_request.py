"""The fair comparison (2026-11-03): the main agent's context, read again on every call, was most of a Pertura
session's cost, and about a sixth of it at the end was figures (35 in each of B and D). The plugin's hook keeps a
reply's figures out of the main agent's context, after saving them for the user's page; the agent sees them where it
asks (show_figures, read_record's figures by id, Read on an image's path), and subagents see their own as before."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "status"))

import hooks  # noqa: E402
import status_file as sf  # noqa: E402

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfigure").decode("ascii")


def event(tool, tool_input=None, **extra):
    """A PostToolUse input as Claude Code gives it: the image blocks under "source", Claude Code's line naming each
    image's cached path, the reply's text."""
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}},
              {"type": "text", "text": "[Image: source: /home/u/.claude/projects/p/img1.png]"},
              {"type": "text", "text": json.dumps({"run_id": "run0001-aa", "figures_shown": ["/ws/f.png"]})}]
    return {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": sf.TOOL_PREFIX + tool,
            "tool_input": tool_input or {}, "tool_response": blocks, **extra}


class TestTheMainAgentSeesFiguresWhereItAsks(unittest.TestCase):
    def test_a_cells_figures_are_kept_out_and_said_so(self):
        out = hooks.agent_view(event("run_code", {"code": "plt.plot([1])", "what": "a line"}))
        kept = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertFalse([b for b in kept if b.get("type") == "image"])
        self.assertIn("run0001-aa", kept[1]["text"])  # the reply itself is kept
        self.assertIn("[Image: source:", kept[0]["text"])  # the path the agent can Read
        self.assertIn("show_figures", kept[-1]["text"])

    def test_asked_by_a_subagent_by_id_or_at_the_cells_overview_they_stay(self):
        self.assertIsNone(hooks.agent_view(event("run_code", {"show_figures": True})))
        self.assertIsNone(hooks.agent_view(event("module_result", {}, agent_id="a1", agent_type="pertura:worker")))
        self.assertIsNone(hooks.agent_view(event("read_record", {"what": "figures", "ids": ["fig1"]})))
        self.assertIsNone(hooks.agent_view(event("build_screen_object")))
        self.assertIsNone(hooks.agent_view({**event("run_code"), "tool_name": "mcp__other__tool"}))

    def test_a_reply_without_figures_is_left_as_it_is(self):
        e = event("thread_status")
        e["tool_response"] = [b for b in e["tool_response"] if b["type"] != "image"]
        self.assertIsNone(hooks.agent_view(e))

    def test_the_shape_claude_code_keeps_is_kept(self):
        e = event("module_result")
        e["tool_response"] = {"content": e["tool_response"], "structuredContent": {"run_id": "m1"}}
        kept = hooks.agent_view(e)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(kept["structuredContent"], {"run_id": "m1"})
        self.assertFalse([b for b in kept["content"] if b.get("type") == "image"])

    def test_the_hook_script_prints_its_answer(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "USERPROFILE": home, "PERTURA_STATUS_HOME": home}
            p = subprocess.run([sys.executable, "-S", os.path.join(ROOT, "plugin", "status", "hooks.py"),
                                "after-tool-call"], input=json.dumps(event("run_code", {"code": "x"})).encode(),
                               capture_output=True, env=env, timeout=60)
            self.assertEqual(p.returncode, 0)
            out = json.loads(p.stdout.decode("utf-8"))
            self.assertIn("updatedToolOutput", out["hookSpecificOutput"])


def named(tool, names):
    """A reply with one image per name, as Claude Code gives it, the server's "images" naming them in order."""
    e = event(tool)
    image = e["tool_response"][0]
    e["tool_response"] = [dict(image) for _ in names] + [
        {"type": "text", "text": json.dumps({"run_id": "run0001-aa", "images": names})}]
    return e


class TestARunsOwnFigureReachesTheMainAgent(unittest.TestCase):
    """With every figure held back, the main agent saw no module's figure: GSE250558's network, in panel e of the
    overview's Figure 1, was scored "visible, not read" in both runs on 3541cc4 (2026-11-07). A run's own figure now
    reaches it when the run's result is opened, in whichever reply opens it; the notebook's figures still go to the
    page alone."""

    def test_a_runs_figure_stays_and_a_cells_goes(self):
        out = hooks.agent_view(named("run_code", ["cell026_1.png", hooks.RUN_FIGURE + "r1 (module C.genes)"]))
        kept = out["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(len([b for b in kept if b.get("type") == "image"]), 1)
        self.assertIn("show_figures", kept[-1]["text"])

    def test_a_reply_holding_runs_figures_only_is_left_as_it_is(self):
        self.assertIsNone(hooks.agent_view(named("wait_for_runs", [hooks.RUN_FIGURE + "r1 (module A.screen_overview)",
                                                                   hooks.RUN_FIGURE + "r2 (module C.genes)"])))

    def test_the_names_are_read_where_claude_code_keeps_them(self):
        e = named("record_finding", ["fig_relay.png", hooks.RUN_FIGURE + "r3 (module E.similar_targets)"])
        blocks = e["tool_response"]
        e["tool_response"] = {"content": blocks[:-1], "structuredContent": json.loads(blocks[-1]["text"])}
        kept = hooks.agent_view(e)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(len([b for b in kept["content"] if b.get("type") == "image"]), 1)

    def test_an_image_the_reply_does_not_name_goes_to_the_page_alone(self):
        e = named("module_result", [hooks.RUN_FIGURE + "r4 (module C.genes)", "unnamed"])
        e["tool_response"][-1]["text"] = json.dumps({"images": [hooks.RUN_FIGURE + "r4 (module C.genes)"]})
        kept = hooks.agent_view(e)["hookSpecificOutput"]["updatedToolOutput"]
        self.assertEqual(len([b for b in kept if b.get("type") == "image"]), 1)

    def test_the_hook_and_the_server_name_a_runs_figure_alike(self):
        from pertura import analysis_service
        self.assertEqual(hooks.RUN_FIGURE, analysis_service.RUN_FIGURE)


if __name__ == "__main__":
    unittest.main()
