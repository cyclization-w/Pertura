"""The agent's notebook (pertura.notebook): a persistent kernel, its outputs, interrupts, and the record."""
import json
import os
import shutil
import tempfile
import unittest

try:
    import ipykernel  # noqa: F401
    import jupyter_client  # noqa: F401
    HAVE_KERNEL = True
except ImportError:
    HAVE_KERNEL = False


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestNotebook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pertura.notebook import Notebook
        cls.root = tempfile.mkdtemp(prefix="pertura-nb-")
        cls.view = os.path.join(cls.root, "view.h5ad")
        open(cls.view, "w").close()
        cls.key = os.path.join(cls.root, "key.json")  # beside the view, as the referee's key is (docs/adr/0009)
        with open(cls.key, "w") as fh:
            json.dump({"decoys": ["NT_1"]}, fh)
        cls.nb = Notebook(os.path.join(cls.root, "session", "notebook"), cls.view, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.nb.shutdown()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_1_state_carries_across_cells_and_the_view_is_given(self):
        self.assertEqual(self.nb.run("x = 41")["status"], "ok")
        out = self.nb.run("print(x + 1)\nimport os\nprint(os.path.basename(view), os.path.samefile(os.getcwd(), os.path.dirname(figures)))")
        self.assertEqual(out["stdout"].split(), ["42", "view.h5ad", "True"])  # samefile: /tmp can be a link
        self.assertIn("43", self.nb.run("x + 2")["result"])

    def test_1b_pertura_is_importable_in_the_kernel(self):
        out = self.nb.run("import pertura.gene_sets as gs" + chr(10) + "print(gs.NAMED['hallmark'])")
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("mh.all", out["stdout"])

    def test_2_errors_come_back(self):
        out = self.nb.run("1 / 0")
        self.assertEqual(out["status"], "error")
        self.assertIn("ZeroDivisionError", out["error"])
        self.assertNotIn("\x1b[", out["traceback"])

    def test_3_a_long_cell_is_interrupted_and_the_state_is_kept(self):
        out = self.nb.run("import time\ny = 7\ntime.sleep(30)", timeout=2)
        self.assertEqual(out["status"], "timeout")
        self.assertTrue(self.nb.alive())
        self.assertEqual(self.nb.run("print(y)")["stdout"].strip(), "7")

    def test_4_figures_are_saved_and_long_output_is_cut(self):
        out = self.nb.run("import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.show()")
        self.assertEqual(len(out["figures"]), 1)
        self.assertTrue(os.path.getsize(out["figures"][0]) > 100)
        long = self.nb.run("print('a' * 20000)")
        self.assertLess(len(long["stdout"]), 7000)
        self.assertIn("characters cut", long["stdout"])

    def test_5_the_record_is_a_notebook_with_every_cell(self):
        with open(self.nb.path, encoding="utf-8") as fh:
            nb = json.load(fh)
        self.assertEqual(nb["nbformat"], 4)
        self.assertEqual(len(nb["cells"]), self.nb.count)
        self.assertTrue(any("image/png" in o.get("data", {}) for c in nb["cells"] for o in c["outputs"]))
        self.assertTrue(any(len(o.get("text", "")) == 20001 for c in nb["cells"] for o in c["outputs"]))

    def test_6_restart_keeps_the_record_and_clears_the_state(self):
        n = self.nb.count
        self.nb.restart()
        self.assertEqual(self.nb.run("x")["status"], "error")
        self.assertEqual(self.nb.count, n + 1)

    def test_7_the_kernel_is_confined_before_and_after_a_restart(self):
        read_key = f"open({self.key!r}).read()"
        plant = (f"import os\np = os.path.join({self.nb.home!r}, 'ipython', 'profile_default', 'startup')\n"
                 "open(os.path.join(p, 'x.py'), 'w').write('print(1)')")
        for code in (read_key, plant, "import sqlite3\nsqlite3.connect(':memory:')"):
            out = self.nb.run(code)
            self.assertIn("PermissionError", out["error"], code)
        self.nb.restart()
        self.assertIn("PermissionError", self.nb.run(read_key)["error"])
        with open(self.nb.log, encoding="utf-8") as fh:
            self.assertIn("key.json", fh.read())

    def test_8_a_limit_above_the_cap_is_cut_and_said(self):
        from pertura.notebook import MAX_TIMEOUT
        out = self.nb.run("1 + 1", timeout=99999)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["limit_seconds"], MAX_TIMEOUT)
        self.assertIn("was cut", out["note"])
        self.assertNotIn("note", self.nb.run("2 + 2", timeout=5))
        self.assertEqual(self.nb.info()["max_cell_seconds"], MAX_TIMEOUT)


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestANewKernelOnAnEarlierRecord(unittest.TestCase):
    def test_the_first_cell_in_a_new_kernel_says_the_earlier_names_are_gone(self):
        """A server's notebooks end with it, and a client without a window starts a server each turn: the next
        server's notebook reads the record, starts a new kernel, and its first cell says so."""
        from pertura.notebook import Notebook
        root = tempfile.mkdtemp(prefix="pertura-nb-")
        try:
            view = os.path.join(root, "view.h5ad")
            open(view, "w").close()
            folder = os.path.join(root, "session", "notebook")
            first = Notebook(folder, view, timeout=60)
            self.assertNotIn("note", first.run("x = 1"))  # the session's first cell
            self.assertNotIn("note", first.run("x + 1"))
            first.shutdown()  # the server's end
            again = Notebook(folder, view, timeout=60)  # the next turn's server
            out = again.run("x")
            self.assertEqual(out["status"], "error")
            self.assertIn("ran in a new kernel", out["note"])
            self.assertNotIn("note", again.run("y = 2"))
            again.restart()
            self.assertIn("ran in a new kernel", again.run("y")["note"])
            again.shutdown()
        finally:
            shutil.rmtree(root, ignore_errors=True)


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestNotebookLimits(unittest.TestCase):
    """docs/adr/0014: a cell that ignores its interrupt ends with its kernel; the memory limit (Linux)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pertura-nb-lim-")
        self.view = os.path.join(self.root, "view.h5ad")
        open(self.view, "w").close()
        self.key = os.path.join(self.root, "key.json")
        with open(self.key, "w") as fh:
            json.dump({"decoys": ["NT_1"]}, fh)

    def tearDown(self):
        self.nb.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_cell_that_ignores_its_interrupt_ends_with_its_kernel(self):
        from pertura.notebook import Notebook
        self.nb = Notebook(os.path.join(self.root, "session", "notebook"), self.view, timeout=60, grace=2)
        self.nb.run("z = 5")
        out = self.nb.run("import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\ntime.sleep(120)",
                          timeout=2)
        self.assertEqual(out["status"], "timeout")
        self.assertTrue(out["kernel_restarted"])
        self.assertIn("variables are gone", out["note"])
        self.assertLess(out["seconds"], 60)
        self.assertTrue(self.nb.alive())
        self.assertIn("NameError", self.nb.run("z")["error"])
        self.assertIn("PermissionError", self.nb.run(f"open({self.key!r}).read()")["error"])  # confined again
        with open(self.nb.path, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["cells"]), 4)  # the record keeps every cell

    @unittest.skipUnless(os.name == "posix", "the memory limit is Linux's (RLIMIT_DATA)")
    def test_a_too_large_allocation_fails_in_the_cell_and_the_kernel_survives(self):
        from pertura.notebook import Notebook
        self.nb = Notebook(os.path.join(self.root, "session", "notebook"), self.view, timeout=60, memory_gb=1)
        out = self.nb.run("import numpy as np\na = np.ones(400_000_000)")  # 3.2 GB
        self.assertEqual(out["status"], "error")
        self.assertIn("MemoryError", out["error"])
        self.assertEqual(self.nb.run("print(1 + 1)")["stdout"].strip(), "2")
        self.assertEqual(self.nb.info()["memory_limit_gb"], 1.0)
        out = self.nb.run("import resource\nresource.setrlimit(resource.RLIMIT_DATA, (-1, -1))")
        self.assertEqual(out["status"], "error")  # the kernel cannot raise its own hard limit



class TestMemoryLimitCode(unittest.TestCase):
    def test_it_compiles_and_does_nothing_without_the_resource_module(self):
        from pertura.notebook import memory_limit_code
        exec(compile(memory_limit_code(2), "<boot>", "exec"), {}) if os.name != "posix" else None

    @unittest.skipUnless(os.name == "posix", "the memory limit is Linux's (RLIMIT_DATA)")
    def test_it_never_raises_a_limit_already_set(self):
        import subprocess
        import sys
        from pertura.notebook import memory_limit_code
        gb = 1024 ** 3
        # (a soft limit above the hard one is not allowed: -1, unlimited, only for both or for the hard)
        for soft, hard, want in ((2 * gb, 3 * gb, 2 * gb), (-1, -1, 64 * gb), (5 * gb, -1, 5 * gb)):
            code = (f"import resource\nresource.setrlimit(resource.RLIMIT_DATA, ({soft}, {hard}))\n"
                    + memory_limit_code(64) + "import resource\nprint(resource.getrlimit(resource.RLIMIT_DATA))")
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), str((want, want)), out.stderr)


if __name__ == "__main__":
    unittest.main()
