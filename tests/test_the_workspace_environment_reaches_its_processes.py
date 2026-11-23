"""The workspace's environment (resources' "environment", docs/build/numpy_huge_pages_on_the_analysis_server_proposal.md):
the server puts it in its own environment when it starts, a value already there winning, and every process the server
starts inherits it: a job through the runner, a notebook's kernel."""
import os
import sys
import tempfile
import unittest
from unittest import mock

from pertura import resources as rs

NAME = "PERTURA_TEST_ENVIRONMENT_VARIABLE"


class TestTheServerSetsIt(unittest.TestCase):
    def test_it_goes_into_the_server_s_environment_and_a_value_already_there_wins(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(NAME, None)
            done = rs.at_server_start({"resources": {"environment": {NAME: "0"}}})
            self.assertEqual(done, {"environment": {NAME: "0"}})
            self.assertEqual(os.environ[NAME], "0")
            os.environ[NAME] = "1"
            self.assertEqual(rs.at_server_start({"resources": {"environment": {NAME: 0}}}),
                             {"environment": {NAME: "1"}})

    def test_a_wrong_environment_is_refused_and_a_right_one_is_a_known_resource(self):
        for bad in ([NAME, "0"], {"1BAD": "0"}, {NAME: True}, {NAME: ["0"]}, {"A-B": "0"}):
            with self.assertRaises(rs.ResourceError, msg=repr(bad)):
                rs.at_server_start({"resources": {"environment": bad}})
        self.assertEqual(rs.load({"resources": {"environment": {NAME: "0"}}})["environment"], {NAME: "0"})


class TestItsProcessesInheritIt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.dict(os.environ, {NAME: "0"})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_job_inherits_it(self):
        from pertura import runner
        jid = runner.start([sys.executable, "-c", f"import os; print('seen', os.environ.get({NAME!r}))"], "env",
                           root=self.tmp.name)
        st = runner.wait(jid, self.tmp.name, timeout=120)
        self.assertEqual(st["state"], "done", st)
        with open(os.path.join(self.tmp.name, jid, "job.log"), encoding="utf-8") as fh:
            self.assertIn("seen 0", fh.read())

    def test_a_kernel_inherits_it(self):
        try:
            import ipykernel  # noqa: F401
            import jupyter_client  # noqa: F401
        except ImportError:
            self.skipTest("needs jupyter_client and ipykernel")
        from pertura.notebook import Notebook
        nb = Notebook(os.path.join(self.tmp.name, "nb"), os.path.join(self.tmp.name, "view"), mode="normal")
        self.addCleanup(nb.shutdown)
        nb.start()
        out = nb.run(f"import os; print('seen', os.environ.get({NAME!r}))")
        self.assertIn("seen 0", out.get("stdout", ""), out)


if __name__ == "__main__":
    unittest.main()
