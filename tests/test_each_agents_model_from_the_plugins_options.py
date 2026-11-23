"""Each of Pertura's agents runs on the model the user chose for it: an option per agent in the plugin's manifest (a
row of /config), applied by the PreToolUse hook on the Agent tool as the launch's own model, which outranks the one
the agent's file names; "default" keeps the file's."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "plugin", "status")
sys.path.insert(0, STATUS)
import hooks  # noqa: E402


class TestEachAgentsModel(unittest.TestCase):
    def test_every_agent_has_an_option_that_keeps_its_own_model_by_default(self):
        with open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            options = json.load(fh)["userConfig"]
        agents = [f[:-3] for f in os.listdir(os.path.join(ROOT, "plugin", "agents")) if f.endswith(".md")]
        self.assertTrue(agents)
        for name in agents:
            o = options[name.replace("-", "_") + "_model"]
            self.assertEqual((o["type"], o["default"], o["options"][0]), ("string", "default", "default"), name)
            self.assertEqual(set(o["options"][1:]), set(hooks.MODELS), name)

    def test_the_chosen_model_becomes_the_launchs_own(self):
        launch = {"subagent_type": "pertura:research-lead", "description": "review", "prompt": "Review the line."}
        out = hooks.agent_model({"tool_input": launch}, {"CLAUDE_PLUGIN_OPTION_RESEARCH_LEAD_MODEL": "sonnet"})
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"], {**launch, "model": "sonnet"})
        worker = {**launch, "subagent_type": "pertura:worker"}
        for env, given in (({"CLAUDE_PLUGIN_OPTION_RESEARCH_LEAD_MODEL": "default"}, launch),  # the file's model
                           ({}, launch),
                           ({"CLAUDE_PLUGIN_OPTION_WORKER_MODEL": "haiku"}, {**worker, "subagent_type": "other:worker"}),
                           ({"CLAUDE_PLUGIN_OPTION_WORKER_MODEL": "haiku"}, {**worker, "model": "haiku"})):
            self.assertIsNone(hooks.agent_model({"tool_input": given}, env), (env, given))

    def test_the_hooks_file_runs_it_before_each_launch(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json"), encoding="utf-8") as fh:
            pre = json.load(fh)["hooks"]["PreToolUse"]
        self.assertIn(("Agent", True), [(h["matcher"], "agent-model" in h["hooks"][0]["command"]) for h in pre])
        event = json.dumps({"tool_input": {"subagent_type": "pertura:advisor", "prompt": "p"}}).encode()
        r = subprocess.run([sys.executable, "-S", os.path.join(STATUS, "hooks.py"), "agent-model"], input=event,
                           capture_output=True, env={**os.environ, "CLAUDE_PLUGIN_OPTION_ADVISOR_MODEL": "opus"})
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]["model"], "opus")


if __name__ == "__main__":
    unittest.main()
