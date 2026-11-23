"""What development sessions 5 and 6 showed (session 6, problem 14), fixed for any launch (docs/build_goal_session6_fixes
.md, item 18.1): the agent's first call was `ls` on its own computer for a path on the analysis server. open_project's
reply says where the data are and which of the agent's tools reach them, from how the plugin launched the server; the
skill names no host and no deployment."""
import os
import unittest
from unittest import mock

from pertura import analysis_service as svc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestWhereTheDataAre(unittest.TestCase):
    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in ("PERTURA_LAUNCH", "PERTURA_HOST", "SSH_CONNECTION")}
        return mock.patch.dict(os.environ, {**clean, **values}, clear=True)

    def test_a_launch_over_ssh(self):
        with self.env(PERTURA_HOST="analysis.example.org"):
            words = svc.data_location()
        self.assertIn("on the analysis server (analysis.example.org)", words)
        self.assertIn("look at the data with run_code", words)

    def test_a_local_launch(self):
        with self.env():
            words = svc.data_location()
        self.assertIn("on the computer you run on", words)
        self.assertNotIn("analysis server", words)

    def test_the_launch_can_say_it_itself(self):
        with self.env(PERTURA_LAUNCH="local", SSH_CONNECTION="10.0.0.1 22 10.0.0.2 22"):
            self.assertIn("on the computer you run on", svc.data_location())
        with self.env(PERTURA_LAUNCH="ssh"):
            self.assertIn("on the analysis server.", svc.data_location())

    def test_the_skill_names_no_deployment(self):
        with open(os.path.join(ROOT, "plugin", "skills", "pertura", "SKILL.md"), encoding="utf-8") as fh:
            skill = " ".join(fh.read().split())
        self.assertIn("`open_project`'s reply gives the data's location", skill)
        self.assertNotIn("do not see the data", skill)


if __name__ == "__main__":
    unittest.main()
