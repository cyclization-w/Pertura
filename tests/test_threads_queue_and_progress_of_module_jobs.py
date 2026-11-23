"""Module jobs' speed (docs/build/development_sessions/session_11_cd4_rest_activation_gse278572_sonnet_after_session10_batch,
problems 5 and 6; docs/build/module_speed_on_gse278572.md): a job gets its spec's threads, whatever its server keeps for
itself; the processes a step starts share them, so C.genes' tests run in as many processes as the job has threads, one
thread each; those tests count in the job's progress as each ends; and the heaviest modules wait for each other in a
workspace."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pertura import limits, progress

HAS_SCANPY = importlib.util.find_spec("scanpy") is not None
SRC = os.path.dirname(os.path.dirname(os.path.abspath(limits.__file__)))


class TestThreads(unittest.TestCase):
    def test_a_process_counts_its_own_threads_at_most_the_limit(self):
        with mock.patch.dict(os.environ, {"OMP_NUM_THREADS": "4"}):
            self.assertEqual(limits.own_threads(), min(4, limits.THREAD_LIMIT))
            self.assertEqual(limits.worker_threads(2), max(1, min(4, limits.THREAD_LIMIT) // 2))
            self.assertEqual(limits.worker_threads(99), 1)
        for given in ("999", "", "two"):
            with mock.patch.dict(os.environ, {"OMP_NUM_THREADS": given}):
                self.assertEqual(limits.own_threads(), limits.THREAD_LIMIT, given)

    def test_a_worker_takes_its_threads_before_it_loads_anything_more(self):
        code = ("import os; from pertura import limits; limits.set_threads(2); "
                "print(sorted({os.environ[v] for v in limits.THREAD_VARIABLES}))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                             env={**os.environ, "PYTHONPATH": SRC, "OMP_NUM_THREADS": "12"})
        self.assertEqual(out.stdout.strip(), "['2']", out.stderr)

    @unittest.skipUnless(sys.platform.startswith("linux"), "the runner's launcher runs on Linux")
    def test_a_job_gets_its_spec_s_threads_not_its_server_s(self):
        from pertura import runner
        code = "import json, os; print(json.dumps({v: os.environ.get(v) for v in %r}))" % (limits.THREAD_VARIABLES,)
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.dict(os.environ, {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "2"}):
            jid = runner.start([sys.executable, "-c", code], "threads", root=root, threads=3)
            self.assertEqual(runner.wait(jid, root, timeout=120)["state"], "done")
            with open(os.path.join(root, jid, "job.log"), encoding="utf-8") as fh:
                got = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(set(got.values()), {"3"})

    def test_an_r_process_of_several_side_by_side_takes_one_thread_for_each_kind(self):
        from pertura import envs
        with mock.patch.dict(os.environ, {v: "12" for v in limits.THREAD_VARIABLES}):
            one = envs.r_env({"OMP_NUM_THREADS": "1"})
            alone = envs.r_env()
            given = envs.r_env({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "3"})
        self.assertEqual({one[v] for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                                           "R_DATATABLE_NUM_THREADS")}, {"1"})
        self.assertEqual({alone[v] for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "R_DATATABLE_NUM_THREADS")},
                         {"12"})  # an R process alone keeps the job's threads
        self.assertEqual(given["OPENBLAS_NUM_THREADS"], "3")

    def test_a_finding_s_checks_job_gets_its_share_of_the_cores(self):
        from pertura import record as rc
        with tempfile.TemporaryDirectory() as root, mock.patch("pertura.runner.start", return_value="job1") as start:
            r = rc.Record(os.path.join(root, "record"))
            r.start_run("checks of finding f1", ["true"], [], root, name="checks", threads=4)
        self.assertEqual(start.call_args.kwargs["threads"], 4)

    def test_c_genes_runs_as_many_processes_as_the_job_has_threads(self):
        from pertura.analysis_service import AnalysisService
        from pertura.question_modules import c
        with mock.patch.dict(os.environ, {"OMP_NUM_THREADS": "4", "PERTURA_DE_WORKERS": ""}):
            self.assertEqual(c.de_workers(), min(4, limits.THREAD_LIMIT))
            # the server, at one thread itself, estimates a job's time from the job's processes
            self.assertEqual(AnalysisService._job_workers(), limits.THREAD_LIMIT)
        with mock.patch.dict(os.environ, {"PERTURA_DE_WORKERS": "3"}):
            self.assertEqual(c.de_workers(12), 3)


class TestProgress(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.job = os.path.join(self.d, "job")
        os.makedirs(self.job)
        self.state = dict(progress._state)
        progress._state.update({"done": 0, "of": 0, "started": None})

    def tearDown(self):
        progress._state.clear()
        progress._state.update(self.state)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_each_part_counts_as_it_ends_a_target_not_fitted_too(self):
        import pandas as pd
        from pertura.question_modules import c
        with mock.patch.dict(os.environ, {progress.VARIABLE: self.job}), \
                mock.patch.object(c, "_wilcoxon_part", lambda *a: pd.DataFrame({"names": ["g"], "target": [a[3][0]]})):
            progress.expect(3)
            found = c._each_part(lambda k: None if k == "B" else (None, None, None, [k]), ["A", "B", "C"], 1)
            p = progress.read(self.job)
        self.assertEqual([t["target"].iat[0] for t in found], ["A", "C"])
        self.assertEqual((p["done"], p["of"]), (3, 3))

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_tests_on_cells_count_each_target_as_it_ends(self):
        from test_genes_against_same_sample_controls import screen_with_a_sample_effect
        from pertura.question_modules import c
        os.makedirs(os.path.join(self.d, "screen"))  # the screen's files go there
        s, _ = screen_with_a_sample_effect(os.path.join(self.d, "screen"))
        targets = [t for t in s.targets(10)][:2]
        with mock.patch.dict(os.environ, {progress.VARIABLE: self.job}):
            c.genes_wilcoxon(s, targets, "control_guides", workers=1)
            first = progress.read(self.job)
            c.genes_wilcoxon_same_samples(s, targets, "control_guides", workers=1)
            both = progress.read(self.job)
        self.assertEqual((first["done"], first["of"]), (len(targets), len(targets)))
        self.assertEqual((both["done"], both["of"]), (2 * len(targets), 2 * len(targets)))  # one count for a job

    @unittest.skipUnless(HAS_SCANPY, "needs scanpy")
    def test_the_tests_in_worker_processes_give_the_same_table_and_count(self):
        import pandas as pd
        from test_genes_against_same_sample_controls import screen_with_a_sample_effect
        from pertura.question_modules import c
        os.makedirs(os.path.join(self.d, "screen"))  # the screen's files go there
        s, _ = screen_with_a_sample_effect(os.path.join(self.d, "screen"))
        targets = [t for t in s.targets(10)][:3]
        alone, _ = c.genes_wilcoxon(s, targets, "control_guides", workers=1)
        same_alone, _ = c.genes_wilcoxon_same_samples(s, targets, "control_guides", workers=1)
        progress._state.update({"done": 0, "of": 0, "started": None})  # the count is the process's: the pooled calls'
        with mock.patch.dict(os.environ, {progress.VARIABLE: self.job}):
            pooled, call = c.genes_wilcoxon(s, targets, "control_guides", workers=2)
            same_pooled, same_call = c.genes_wilcoxon_same_samples(s, targets, "control_guides", workers=2)
            p = progress.read(self.job)
        self.assertEqual((call["params"]["processes"], same_call["params"]["processes"]), (2, 2))  # where allowed
        pd.testing.assert_frame_equal(alone, pooled)
        pd.testing.assert_frame_equal(same_alone, same_pooled)
        self.assertEqual((p["done"], p["of"]), (2 * len(targets), 2 * len(targets)))


class TestOneAtATime(unittest.TestCase):
    def test_the_heaviest_modules_wait_in_a_job_and_nothing_else_does(self):
        import contextlib
        from pertura.question_modules import ONE_AT_A_TIME, __main__ as entry
        self.assertEqual(set(ONE_AT_A_TIME), {"C.genes", "C.programs", "C.states_and_composition"})
        with tempfile.TemporaryDirectory() as root:
            job = os.path.join(root, "20261102T000000-C-genes-abc123")
            with mock.patch.dict(os.environ, {"PERTURA_JOB_DIR": job}):
                q = entry.queued("C.genes")
                self.assertEqual(q.prefix, os.path.join(root, entry.lock_of("C.genes")))  # beside the jobs, its own
                self.assertEqual(q.n, 1)
                self.assertIsInstance(entry.queued("B.efficacy"), contextlib.nullcontext)
            with mock.patch.dict(os.environ, {"PERTURA_JOB_DIR": ""}):
                self.assertIsInstance(entry.queued("C.genes"), contextlib.nullcontext)  # outside a job

    def test_run_module_names_the_heavy_runs_a_new_one_waits_for(self):
        from pertura.analysis_service import AnalysisService
        svc = AnalysisService.__new__(AnalysisService)
        svc.record = mock.Mock()
        svc.record.state.return_value = {"runs": {
            "r1": {"run_id": "r1", "job_id": "r1", "state": "running", "what": "module C.programs"},
            "r2": {"run_id": "r2", "job_id": "r2", "state": "running", "what": "module B.efficacy"},
            "r3": {"run_id": "r3", "job_id": "r3", "state": "done", "what": "module C.genes"},
            "r4": {"run_id": "r4", "job_id": "r4", "state": "running", "what": "module C.genes"},
            "r6": {"run_id": "r6", "job_id": "r6", "state": "running", "what": "module C.genes"}}}
        # each module's own queue (GSE190604, a9510b4: C.genes of 1-7 minutes waited 31-42 behind states and programs)
        self.assertEqual(svc._ahead_of("C.genes", "r4"), ["r6"])
        self.assertEqual(svc._ahead_of("C.programs", "r7"), ["r1"])
        self.assertEqual(svc._ahead_of("B.abundance", "r5"), [])

    def test_a_collections_sets_are_checked_before_the_run_waits(self):
        """GSE190604 (a9510b4): C.programs named hallmark sets without their prefix and failed after 18 minutes in
        the queue."""
        from pertura.analysis_service import AnalysisError, AnalysisService
        svc = AnalysisService.__new__(AnalysisService)
        sets = {"HALLMARK_TNFA_SIGNALING_VIA_NFKB": ["TNF"], "HALLMARK_INFLAMMATORY_RESPONSE": ["IL1B"]}
        with mock.patch.object(svc, "_organism", return_value="human"),                 mock.patch("pertura.gene_sets.msigdb", return_value=sets):
            with self.assertRaises(AnalysisError) as said:
                svc._sets_named("C.programs", {"gene_sets": "hallmark"}, {"select": ["TNFA_SIGNALING_VIA_NFKB"]})
            self.assertIn("HALLMARK_TNFA_SIGNALING_VIA_NFKB", str(said.exception))  # the near name, said
            svc._sets_named("C.programs", {"gene_sets": "hallmark"}, {"select": ["HALLMARK_TNFA_SIGNALING_VIA_NFKB"]})
            svc._sets_named("C.genes", {}, {"select": ["anything"]})  # only a collection's named sets are checked

    def test_programs_found_in_the_cells_wait_in_a_queue_of_their_own(self):
        """GSE203592 on 6a87acd: a one-minute C.genes waited 55 minutes behind a cNMF, unfinished at 54 minutes."""
        from pertura.analysis_service import AnalysisService
        from pertura.question_modules import __main__ as entry
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.dict(os.environ, {"PERTURA_JOB_DIR": os.path.join(root, "20261113T102948-C-programs")}):
                self.assertEqual(entry.queued("C.programs", {"gene_sets": "cnmf"}).prefix,
                                 os.path.join(root, entry.PROGRAMS_QUEUE))
                self.assertEqual(entry.queued("C.programs", {"gene_sets": "hallmark"}).prefix,
                                 os.path.join(root, entry.lock_of("C.programs")))
        svc = AnalysisService.__new__(AnalysisService)
        svc.record = mock.Mock()
        svc.record.state.return_value = {"runs": {
            "r1": {"run_id": "r1", "job_id": "r1", "state": "running",
                   "what": 'module C.programs with {"choices": {"gene_sets": "cnmf"}}'},
            "r2": {"run_id": "r2", "job_id": "r2", "state": "running", "what": "module C.genes"}}}
        self.assertEqual(svc._ahead_of("C.genes", "r2"), [])
        self.assertEqual(svc._ahead_of("C.programs", "r3", {"gene_sets": "genenmf"}), ["r1"])
        self.assertIn("1.5 hours", svc._estimate("C.programs", {"gene_sets": "cnmf"}, None))


class TestAJobsLauncherAlive(unittest.TestCase):
    def test_alive_while_it_runs_and_not_after(self):
        """Without /proc (Windows) the check asks the system: os.kill(pid, 0) there sends CTRL_C_EVENT, and a test
        run on the laptop ended its whole console."""
        from pertura.runner import _alive
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            self.assertTrue(_alive(child.pid))
        finally:
            child.kill()
            child.wait()
        self.assertFalse(_alive(child.pid))
        self.assertFalse(_alive(0))

if __name__ == "__main__":
    unittest.main()
