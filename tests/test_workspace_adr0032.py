"""The workspace of docs/adr/0032 (build step 1): the resources from the workspace configuration (D3), the notebook's
normal and exam modes (D2), the figures a cell drew shown to the agent (D4), a figure kept while exploring and held
to the standard only in a panel (D4), and the notebook's first reply naming the data (D5)."""
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest

from pertura import resources as rs

try:
    import ipykernel  # noqa: F401
    import jupyter_client  # noqa: F401
    HAVE_KERNEL = True
except ImportError:
    HAVE_KERNEL = False


class TestResources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pertura-res-")
        self.path = os.path.join(self.tmp, "workspace.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, resources):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"workspace": self.tmp, "resources": resources}, fh)

    def test_the_defaults_hold_without_a_resources_section(self):
        self.assertEqual(rs.load({}), rs.DEFAULTS)
        self.assertEqual(rs.load({"workspace": self.tmp}), rs.DEFAULTS)
        d = rs.DEFAULTS
        self.assertEqual((d["main_threads"], d["worker_threads"], d["max_workers"], d["cell_timeout_max"],
                          d["output_chars"], d["images_per_cell"], d["mode"], d["network"]),
                         (8, 2, 6, 3600, 12000, 4, "normal", False))  # six workers: five kinds and a probe (P6)

    def test_the_file_is_read_again_so_an_edit_counts_at_once(self):
        self.write({"main_threads": 3})
        cfg = {"workspace": self.tmp, "config_path": self.path}
        self.assertEqual(rs.load(cfg)["main_threads"], 3)
        self.write({"main_threads": 5, "mode": "exam"})
        got = rs.load(cfg)
        self.assertEqual((got["main_threads"], got["mode"]), (5, "exam"))

    def test_a_wrong_or_unknown_value_is_refused_with_what_it_should_be(self):
        for bad, why in (({"main_thread": 3}, "unknown"), ({"main_threads": 0}, "whole number"),
                         ({"main_threads": True}, "whole number"), ({"mode": "free"}, "mode is one of"),
                         ({"network": "yes"}, "true or false"), ({"memory_gb": -1}, "positive"),
                         ({"cell_timeout": 900, "cell_timeout_max": 600}, "above")):
            with self.assertRaises(rs.ResourceError, msg=bad) as e:
                rs.load({"resources": bad})
            self.assertIn(why, str(e.exception))
        self.assertEqual(rs.load({"resources": {"cell_timeout_max": 120}})["cell_timeout"], 120)  # lowered with it

    def test_a_jobs_time_limit_comes_from_the_workspaces_resources(self):
        self.assertEqual(rs.DEFAULTS["job_hours"], 4)
        self.assertEqual(rs.load({"resources": {"job_hours": 8}})["job_hours"], 8.0)
        for bad in (0, -1, True, "8"):
            with self.assertRaises(rs.ResourceError, msg=bad):
                rs.load({"resources": {"job_hours": bad}})
        from unittest import mock
        from pertura import record as rc
        rec = rc.Record(os.path.join(self.tmp, "record"))
        rec.job_limit_seconds = 8 * 3600.0
        with mock.patch("pertura.runner.start", return_value="job-1") as start:
            rec.start_run("module B.dose", ["true"], [], self.tmp)
        self.assertEqual(start.call_args.kwargs["limit_seconds"], 8 * 3600.0)
        from test_design_service import make_deposit
        from pertura.analysis_service import AnalysisService
        deposit = os.path.join(self.tmp, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        svc = AnalysisService({"workspace": os.path.join(self.tmp, "ws"), "resources": {"job_hours": 8}})
        try:
            self.assertTrue(svc.call("open_project", {"data_folder": deposit})[0])
            self.assertEqual(svc.record.job_limit_seconds, 8 * 3600.0)  # every job the project starts
        finally:
            svc.close()

    def test_the_core_set_and_the_thread_limit_are_put_where_every_process_reads_them(self):
        from pertura import limits
        before = {k: os.environ.get(k) for k in (limits.CORES_VARIABLE, rs.THREAD_LIMIT_VARIABLE)}
        limit = limits.THREAD_LIMIT
        try:
            os.environ.pop(limits.CORES_VARIABLE, None)
            os.environ.pop(rs.THREAD_LIMIT_VARIABLE, None)
            done = rs.at_server_start({"resources": {"cores": "60-71", "thread_limit": 12}})
            self.assertEqual(done, {"cores": "60-71", "thread_limit": 12})
            self.assertEqual(os.environ[rs.THREAD_LIMIT_VARIABLE], "12")
            self.assertEqual(limits.THREAD_LIMIT, 12)
            # a value already in the environment wins for both, as for the runner's jobs
            os.environ[limits.CORES_VARIABLE], os.environ[rs.THREAD_LIMIT_VARIABLE] = "48-71", "24"
            done = rs.at_server_start({"resources": {"cores": "60-71", "thread_limit": 12}})
            self.assertEqual(done, {"cores": "48-71", "thread_limit": 24})
            self.assertEqual(limits.THREAD_LIMIT, 24)
        finally:
            limits.THREAD_LIMIT = limit
            for k, v in before.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestTheSetUpCommand(unittest.TestCase):
    """make-workspace-config writes the workspace with the core set and thread limit the server pins its jobs to, and
    needs no research code."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pertura-setup-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_configuration_names_the_cores_and_the_thread_limit(self):
        cfg = rs.workspace_config(self.tmp, cores="60-71", thread_limit=12)
        self.assertEqual(cfg, {"workspace": os.path.abspath(self.tmp),
                               "resources": {"thread_limit": 12, "cores": "60-71"}})
        self.assertEqual(rs.load(cfg)["cores"], "60-71")
        self.assertEqual(rs.workspace_config(self.tmp, "reg.json")["registry"], os.path.abspath("reg.json"))
        for bad in ({"thread_limit": 0}, {"thread_limit": True}, {"cores": ","}, {"cores": "60-"},
                    {"cores": "48-71", "thread_limit": 12}):
            with self.assertRaises(rs.ResourceError, msg=bad):
                rs.workspace_config(self.tmp, **bad)
        # cores alone: the thread limit is their number, so the file says what the server pins to
        self.assertEqual(rs.workspace_config(self.tmp, cores="48-71")["resources"], {"thread_limit": 24,
                                                                                     "cores": "48-71"})

    def test_what_is_not_given_is_written_as_the_server_would_choose_it(self):
        from pertura import limits
        cfg = rs.workspace_config(self.tmp)
        self.assertEqual(cfg["resources"]["thread_limit"], limits.THREAD_LIMIT)
        if hasattr(os, "sched_getaffinity"):
            self.assertEqual(limits.parse_cores(cfg["resources"]["cores"]),
                             tuple(sorted(os.sched_getaffinity(0))[-limits.THREAD_LIMIT:]))
        else:
            self.assertNotIn("cores", cfg["resources"])
        self.assertEqual((rs.as_ranges([60, 61, 62, 63]), rs.as_ranges([0, 2, 4, 5, 6])), ("60-63", "0,2,4-6"))

    def test_the_command_prints_it_without_research_code(self):
        import contextlib
        import io
        import subprocess
        from pertura import mcp_server
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mcp_server.main(["make-workspace-config", self.tmp, "--cores", "4-7", "--thread-limit", "4"])
        self.assertEqual(json.loads(out.getvalue())["resources"], {"thread_limit": 4, "cores": "4-7"})
        src = os.path.dirname(os.path.dirname(os.path.abspath(rs.__file__)))
        code = ("import sys; sys.argv = ['x', 'make-workspace-config', sys.argv[1], '--thread-limit', '4']; "
                "import pertura.mcp_server as m; m.main(sys.argv[1:]); "
                "assert not [k for k in sys.modules if k.startswith('pertura_research')], 'research code was imported'")
        done = subprocess.run([sys.executable, "-c", code, self.tmp], capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": src})
        self.assertEqual(done.returncode, 0, done.stderr)


class TestTheFiguresShownOnce(unittest.TestCase):
    """A figure reached by two paths, a folder's given one and its real one (the trace writes real paths), is shown
    to the agent once (the gate on the analysis server, whose workspace sits below a linked folder)."""

    PNG = __import__("base64").b64decode(  # a 1 x 1 PNG
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def test_a_saved_figure_written_under_a_linked_folder_is_shown_once(self):
        from pertura.analysis_service import AnalysisService
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "real")
            os.makedirs(real)
            with open(os.path.join(real, "saved.png"), "wb") as fh:
                fh.write(self.PNG)
            link = os.path.join(d, "link")
            try:
                os.symlink(real, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("no symbolic links here")
            svc = AnalysisService({"workspace": os.path.join(d, "ws")})
            images, shown, named = svc._cell_images({"figures": []}, [{"png": os.path.join(link, "saved.png")}],
                                                    [os.path.join(real, "saved.png")], 4)
        self.assertEqual((len(images), named), (1, []))
        self.assertEqual(shown, [os.path.join(link, "saved.png")])


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestNormalMode(unittest.TestCase):
    """A kernel in normal mode reads any file and runs programs, writes only its own folder, and keeps the network
    off unless it is allowed."""

    @classmethod
    def setUpClass(cls):
        from pertura.notebook import Notebook
        cls.root = tempfile.mkdtemp(prefix="pertura-normal-")
        cls.view = os.path.join(cls.root, "screen")
        os.makedirs(cls.view)
        cls.elsewhere = os.path.join(cls.root, "elsewhere")  # outside every folder exam mode allows
        os.makedirs(cls.elsewhere)
        cls.outside_file = os.path.join(cls.elsewhere, "other_screen.txt")
        with open(cls.outside_file, "w") as fh:
            fh.write("another screen's table")
        cls.nb = Notebook(os.path.join(cls.root, "session", "notebook"), cls.view, timeout=60, mode="normal")
        cls.online = Notebook(os.path.join(cls.root, "session", "online"), cls.view, timeout=60, mode="normal",
                              network=True)

    @classmethod
    def tearDownClass(cls):
        cls.nb.shutdown()
        cls.online.shutdown()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_it_runs_a_program(self):
        out = self.nb.run("import subprocess, sys\n"
                          "print(subprocess.run([sys.executable, '-c', 'print(6 * 7)'], capture_output=True, "
                          "text=True).stdout.strip())")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["stdout"].strip(), "42")

    def test_it_reads_a_file_outside_and_the_read_is_traced(self):
        out = self.nb.run(f"print(open({self.outside_file!r}).read())")
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("another screen's table", out["stdout"])
        traced = {os.path.normcase(os.path.realpath(p)) for p in out["files"]["read"]}
        self.assertIn(os.path.normcase(os.path.realpath(self.outside_file)), traced)

    def test_a_write_outside_its_folder_is_refused(self):
        out = self.nb.run(f"open({os.path.join(self.elsewhere, 'written.txt')!r}, 'w').write('x')")
        self.assertEqual(out["status"], "error")
        self.assertIn("PermissionError", out["error"])
        self.assertIn("only through Pertura's tools", out["error"])
        self.assertFalse(os.path.exists(os.path.join(self.elsewhere, "written.txt")))

    def test_a_file_a_program_writes_in_its_folder_is_entered(self):
        out = self.nb.run("import subprocess, sys\n"
                          "subprocess.run([sys.executable, '-c', \"open('by_a_program.tsv', 'w').write('a\\\\n1\\\\n')\"],"
                          " check=True)")
        self.assertEqual(out["status"], "ok", out)
        written = [os.path.basename(p) for p in out["files"]["written"]]
        self.assertIn("by_a_program.tsv", written)
        self.assertEqual([os.path.basename(p) for p in out["files"]["written_by_programs"]], ["by_a_program.tsv"])

    def test_the_network_is_off_unless_allowed(self):
        code = ("import socket\ntry:\n    socket.create_connection(('127.0.0.1', 9), timeout=1)\n"
                "except PermissionError as e:\n    print('refused:', e)\nexcept OSError as e:\n"
                "    print('tried:', type(e).__name__)\n")
        self.assertIn("refused:", self.nb.run(code)["stdout"])
        self.assertIn("tried:", self.online.run(code)["stdout"])

    def test_info_says_the_mode(self):
        info = self.nb.info()
        self.assertEqual((info["mode"], info["confined"], info["network"]), ("normal", False, False))


@unittest.skipUnless(HAVE_KERNEL, "needs jupyter_client and ipykernel")
class TestWorkspaceThroughTheService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_design_service import answer, confirm_the_cells, make_deposit, proposal

        from pertura import design_gate, screen_object
        from pertura.analysis_service import AnalysisService
        cls.tmp = tempfile.TemporaryDirectory()
        deposit = os.path.join(cls.tmp.name, "deposit")
        os.makedirs(deposit)
        make_deposit(deposit)
        cls.config = os.path.join(cls.tmp.name, "workspace.json")
        cls.write_resources({"main_threads": 3, "worker_threads": 1, "output_chars": 500, "cell_timeout_max": 120})
        cls.svc = AnalysisService({"workspace": os.path.join(cls.tmp.name, "ws"), "config_path": cls.config})
        for tool, args, elicit in (("open_project", {"data_folder": deposit}, None), ("measure", {}, None),
                                   ("check_design", {"proposal": proposal()}, None),
                                   ("confirm_design", {}, answer())):
            ok, out = cls.svc.call(tool, args, elicit=elicit)
            assert ok, out
        design = design_gate.load(cls.svc.design.design_file)[0]
        screen_object.build(deposit, design, cls.svc._screen_dir())
        confirm_the_cells(cls.svc)

    @classmethod
    def write_resources(cls, resources):
        with open(cls.config, "w", encoding="utf-8") as fh:
            json.dump({"workspace": os.path.join(cls.tmp.name, "ws"), "resources": resources}, fh)

    @classmethod
    def tearDownClass(cls):
        cls.svc.close()
        cls.tmp.cleanup()

    def ok(self, tool, **args):
        ok, out = self.svc.call(tool, args)
        self.assertTrue(ok, out)
        return out

    def draw(self, n):
        return ("import matplotlib.pyplot as plt\n"
                f"for i in range({n}):\n    fig, ax = plt.subplots(figsize=(2, 2))\n    ax.plot([0, i])\n"
                "plt.show()\n")

    def test_1_the_first_reply_names_the_data_and_the_configuration_reaches_the_kernel(self):
        out = self.ok("run_code", what="the thread variables", code="import os\nprint(os.environ['OMP_NUM_THREADS'])")
        self.assertEqual(out["stdout"].strip(), "3")
        w = out["workspace"]
        self.assertIn("pt.screen().adata", w["data"])
        self.assertEqual(w["cells"], json.load(open(os.path.join(self.svc._screen_dir(), "screen.json")))["cells"])
        self.assertEqual(len(w["shape"]), 2)
        self.assertEqual(w["shape"][0], w["cells"])
        self.assertTrue({"label", "unit", "guides", "umis"} <= set(w["cell_columns"]), w["cell_columns"])
        self.assertEqual((w["notebook"]["mode"], w["notebook"]["threads"], w["notebook"]["longest_cell_seconds"]),
                         ("normal", 3, 120))
        again = self.ok("run_code", what="nothing", code="x = 1")
        self.assertNotIn("workspace", again)  # only a kernel's first reply

    def test_2_the_output_cut_and_the_longest_cell_follow_the_file(self):
        out = self.ok("run_code", what="long output", code="print('x' * 3000)")
        self.assertLess(len(out["stdout"]), 700)
        out = self.ok("run_code", what="a long limit asked for", code="x = 2", timeout=1000)
        self.assertEqual(out["limit_seconds"], 120)
        self.write_resources({"main_threads": 3, "worker_threads": 1, "output_chars": 5000, "cell_timeout_max": 120})
        out = self.ok("run_code", what="long output again", code="print('x' * 3000)")
        self.assertEqual(len(out["stdout"].strip()), 3000)  # the next cell reads the edited file

    def test_3_the_figures_a_cell_drew_are_shown_up_to_the_cap(self):
        two = self.ok("run_code", what="two figures", code=self.draw(2))
        self.assertEqual(len(two["_images"]), 2)
        self.assertEqual(two["_images"][0]["mimeType"], "image/png")
        self.assertEqual(two["figures_shown"], two["figures"])
        five = self.ok("run_code", what="five figures", code=self.draw(5))
        self.assertEqual(len(five["_images"]), 4)
        self.assertEqual(five["figures_not_shown"], five["figures"][4:])
        self.assertEqual(len(five["figures_not_shown"]), 1)

    def test_4_a_workers_kernel_takes_the_workers_values_and_shows_its_figures(self):
        self.write_resources({"main_threads": 3, "worker_threads": 2, "output_chars": 5000, "cell_timeout_max": 120})
        self.ok("open_branch", branch_id="w1", plan="a check in a worker's notebook", origin="user",
                request="try it in a worker")
        out = self.ok("run_code", what="a worker's figures", worker="w1",
                      code="import os\nprint(os.environ['OMP_NUM_THREADS'])\n" + self.draw(2))
        self.assertEqual(out["stdout"].strip(), "2")  # the edited file, read when this kernel started
        self.assertEqual(len(out["_images"]), 2)
        self.ok("close_branch", branch_id="w1", outcome="a check", run_ids=[out["run_id"]])

    def test_5_a_panel_holds_a_figure_kept_while_exploring_to_the_standard(self):
        from pertura import record as rc
        rec = self.svc.record
        quick = self.ok("run_code", what="a quick look",
                        code="import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([0, 1])\n"
                             "save_figure(fig, 'quick_look')\n")
        self.assertEqual(quick["figures_entered"], ["quick_look"])
        f = self.ok("read_record", what="figures", ids=["quick_look"])["figures"]["quick_look"]
        self.assertEqual((f["caption"], f["strict"]), (None, False))
        rec.append("question_planned", {"question_id": "C.genes", "letter": "C", "item": "genes", "because": "x"},
                   rc.PLAN)
        rec.append("finding_recorded", {"finding_id": "fq", "question_id": "C.genes", "target": "TA", "feature": "G1",
                                        "direction": "down", "population": "all", "run_ids": [quick["run_id"]]},
                   rc.AGENT)
        rec.append("dossier_proposed", {"dossier_id": "dq", "title": "t", "lead_findings": ["fq"],
                                        "main_run": quick["run_id"], "map_size": {"targets": 8}}, rc.AGENT)
        ok, out = self.svc.call("make_panel", {"dossier_id": "dq", "tiles": [
            {"figure_id": "quick_look", "caption": "A quick look.", "role": "overview"}]})
        self.assertFalse(ok)
        self.assertIn("kept while exploring", out["error"])
        self.assertIn("strict=True", out["error"])

    def test_6_a_reply_opens_the_other_runs_that_have_ended(self):  # session 4, 1f; session 11, problem 16
        a = self.ok("run_module", module="A.what_was_done", wait=0)
        b = self.ok("run_module", module="B.abundance", wait=0)
        first = self.ok("module_result", run_ids=[a["run_id"], b["run_id"]], wait=240)
        other = b["run_id"] if first["run_id"] == a["run_id"] else a["run_id"]
        given = {r["run_id"]: r for r in first.get("ended_since_your_last_call", [])}
        if other not in given:  # still running at the reply's last look: wait for it to end, without opening it
            from pertura import runner
            runner.wait(self.svc.record.state()["runs"][other]["job_id"], self.svc._jobs_root(), timeout=240)
            given = {r["run_id"]: r for r in self.ok("module_result", run_id=first["run_id"])
                     .get("ended_since_your_last_call", [])}
        self.assertIn(other, given)  # opened in the reply: its result there, not its name alone
        self.assertIn("summary", given[other]["result"])
        again = self.ok("module_result", run_id=first["run_id"])
        self.assertNotIn(other, [r["run_id"] for r in again.get("ended_since_your_last_call", [])])  # once


if __name__ == "__main__":
    unittest.main()
