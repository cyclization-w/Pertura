"""ADR 0016 §6's second unknown, tested before the MCP tools (phase 7): the server's pool threads drive one notebook
kernel per worker at the same time. Each kernel's outputs must stay its own, and two calls to one worker's notebook
must take turns rather than interleave on its channels."""
import os
import shutil
import tempfile
import threading
import unittest

try:
    import ipykernel  # noqa: F401
    import jupyter_client  # noqa: F401
    HAVE_KERNEL = True
except ImportError:
    HAVE_KERNEL = False

WORKERS = ("w1", "w2", "w3")
CELLS = 4


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestNotebooksFromThreads(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="pertura-nb-threads-")
        self.view = os.path.join(self.root, "view", "view.h5ad")
        os.makedirs(os.path.dirname(self.view))
        open(self.view, "w").close()
        self.nbs = []

    def tearDown(self):
        for nb in self.nbs:
            nb.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)

    def notebook(self, name):
        from pertura.notebook import Notebook
        nb = Notebook(os.path.join(self.root, "session", name, "notebook"), self.view, timeout=60)
        self.nbs.append(nb)
        return nb

    def drive(self, jobs):
        """Run each (notebook, label) job's cells in its own thread, all at once; returns ({label: stdouts}, errors)."""
        outs, errors = {}, []

        def work(nb, label):
            try:
                got = []
                for i in range(CELLS):
                    out = nb.run(f"import time\ntoken = {label + '-' + str(i)!r}\ntime.sleep(0.3)\nprint(token)")
                    got.append(out["stdout"].strip() if out["status"] == "ok" else f"error: {out['error']}")
                outs[label] = got
            except Exception as e:  # noqa: BLE001 - reported by the test
                errors.append(f"{label}: {type(e).__name__}: {e}")
        threads = [threading.Thread(target=work, args=job, daemon=True) for job in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)
        self.assertFalse([t for t in threads if t.is_alive()], "a thread hung")
        return outs, errors

    def test_kernels_driven_from_several_threads_keep_their_outputs_apart(self):
        jobs = [(self.notebook(w), w) for w in WORKERS]  # kernels also start at the same time, in their threads
        outs, errors = self.drive(jobs)
        self.assertEqual(errors, [])
        for w in WORKERS:
            self.assertEqual(outs[w], [f"{w}-{i}" for i in range(CELLS)])
        self.assertEqual(len({nb.km.connection_file for nb in self.nbs}), len(WORKERS))

    def test_two_calls_to_one_notebook_take_turns(self):
        nb = self.notebook("w1")
        nb.start()
        outs, errors = self.drive([(nb, "a"), (nb, "b")])
        self.assertEqual(errors, [])
        self.assertEqual(outs["a"], [f"a-{i}" for i in range(CELLS)])
        self.assertEqual(outs["b"], [f"b-{i}" for i in range(CELLS)])
        self.assertEqual(nb.count, 2 * CELLS)  # every cell recorded once


    def test_a_shutdown_waits_for_the_running_cell(self):
        nb = self.notebook("w1")
        nb.start()
        out = {}
        t = threading.Thread(target=lambda: out.update(nb.run("import time\ntime.sleep(8)\nprint('done')")))
        t.start()
        import time
        time.sleep(1)  # the cell is running
        nb.shutdown()  # a session closing: it waits its turn rather than killing the cell after 5 s
        t.join(timeout=60)
        self.assertEqual((out.get("status"), out.get("stdout", "").strip()), ("ok", "done"))
        self.assertEqual(nb.count, 1)
        self.assertFalse(nb.alive())


if __name__ == "__main__":
    unittest.main()
