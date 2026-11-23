"""Confinement of agent code (pertura.confine; docs/adr/0009): the audit hook lets agent code read its view and
working folder and denies the hidden side, writes elsewhere, programs and the network, and logs each denial.
Each case runs in a fresh Python process, since an audit hook cannot be removed."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

PROBE = textwrap.dedent('''
    import json, os, sys
    sys.path.insert(0, {src!r})
    import numpy as np
    import pandas as pd
    from pertura import confine
    confine.install(read=[{view!r}], write=[{work!r}], log={log!r}, label="test code")
    out = {{}}
    def attempt(name, fn):
        try:
            fn()
            out[name] = "ok"
        except PermissionError as e:
            out[name] = "denied"
        except Exception as e:
            out[name] = "error: " + type(e).__name__ + ": " + str(e)[:200]
    attempt("read_view", lambda: open(os.path.join({view!r}, "design.json")).read())
    attempt("read_key", lambda: open({key!r}).read())
    attempt("list_build", lambda: os.listdir({build!r}))
    attempt("list_view", lambda: os.listdir({view!r}))
    attempt("np_load_hidden", lambda: np.load({hidden_npy!r}))
    attempt("np_save_work", lambda: np.save(os.path.join({work!r}, "a.npy"), np.arange(3)))
    attempt("np_save_outside", lambda: np.save(os.path.join({build!r}, "b.npy"), np.arange(3)))
    attempt("pd_read_json_key", lambda: pd.read_json({key!r}))
    attempt("pd_read_csv_hidden", lambda: pd.read_csv({hidden_csv!r}))
    attempt("pd_to_csv_outside", lambda: pd.DataFrame({{"a": [1]}}).to_csv(os.path.join({build!r}, "c.csv")))
    attempt("pd_to_csv_work", lambda: pd.DataFrame({{"a": [1]}}).to_csv(os.path.join({work!r}, "c.csv")))
    attempt("forge_event_store", lambda: open(os.path.join({session!r}, "memory", "events.db"), "ab").write(b"x"))
    import sqlite3
    attempt("sqlite_event_store", lambda: sqlite3.connect(os.path.join({session!r}, "memory", "events.db")))
    attempt("sqlite_memory", lambda: sqlite3.connect(":memory:"))
    attempt("home_ipython", lambda: open(os.path.expanduser("~/.ipython/profile_default/startup/x.py")).read())
    import gc
    attempt("gc_objects", lambda: gc.get_objects())
    if hasattr(os, "fork"):
        attempt("fork", lambda: os._exit(0) if os.fork() == 0 else None)
    import multiprocessing
    def spawn():
        p = multiprocessing.get_context("spawn").Process(target=print)
        p.start()
        p.join(5)
    attempt("spawn", spawn)
    import subprocess
    attempt("subprocess", lambda: subprocess.run([sys.executable, "-V"], capture_output=True))
    attempt("os_system", lambda: os.system("echo hi"))
    import socket
    def net():
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", 9))
        finally:
            s.close()
    attempt("network", net)
    attempt("grade_file", lambda: open({grade!r}).read() if {grade!r} else None)
    attempt("card_file", lambda: open({card!r}).read() if {card!r} else None)
    attempt("import_after", lambda: __import__("json.decoder"))
    attempt("hard_link_key", lambda: os.link({key!r}, os.path.join({work!r}, "k1")))
    attempt("symlink_key", lambda: os.symlink({key!r}, os.path.join({work!r}, "k2")))
    attempt("proc_root_key", lambda: open("/proc/self/root" + os.path.abspath({key!r})).read())
    attempt("rename_into_work", lambda: os.rename({key!r}, os.path.join({work!r}, "k3")))
    try:
        import h5py
        def h5_hidden():
            with h5py.File({hidden_h5!r}, "r") as f:
                list(f)
        def h5_view():
            with h5py.File(os.path.join({view!r}, "view.h5"), "r") as f:
                list(f)
                assert isinstance(f["x"].file, h5py.File)
        def h5_write_view():
            with h5py.File(os.path.join({view!r}, "new.h5"), "w") as f:
                f["y"] = [1]
        def h5_write_work():
            with h5py.File(os.path.join({work!r}, "new.h5"), mode="w") as f:
                f["y"] = [1]
        attempt("h5py_write_view", h5_write_view)
        attempt("h5py_write_work", h5_write_work)
        attempt("h5py_hidden", h5_hidden)
        attempt("h5py_view", h5_view)
    except ImportError:
        pass
    print("RESULT" + json.dumps(out))
''')


class TestConfine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="pertura-confine-")
        r = cls.root
        cls.build = os.path.join(r, "build")
        cls.view = os.path.join(cls.build, "view")
        cls.session = os.path.join(r, "session")
        cls.work = os.path.join(cls.session, "notebook")
        for d in (cls.view, cls.work, os.path.join(cls.session, "memory")):
            os.makedirs(d)
        with open(os.path.join(cls.view, "design.json"), "w") as fh:
            json.dump({"ok": 1}, fh)
        cls.key = os.path.join(cls.build, "key.json")
        with open(cls.key, "w") as fh:
            json.dump({"decoys": ["NT_1"]}, fh)
        import numpy as np
        cls.hidden_npy = os.path.join(cls.build, "cells.npy")
        np.save(cls.hidden_npy, np.arange(5))
        cls.hidden_csv = os.path.join(cls.build, "table.csv")
        with open(cls.hidden_csv, "w") as fh:
            fh.write("a,b\n1,2\n")
        with open(os.path.join(cls.session, "memory", "events.db"), "wb") as fh:
            fh.write(b"")
        cls.hidden_h5 = os.path.join(cls.build, "universe.h5")
        try:
            import h5py
            for path in (cls.hidden_h5, os.path.join(cls.view, "view.h5")):
                with h5py.File(path, "w") as f:
                    f["x"] = [1, 2, 3]
        except ImportError:
            pass
        grades = sorted(glob.glob(os.path.join(SRC, "pertura", "recipes", "*", "grade.json")))
        cards = sorted(glob.glob(os.path.join(SRC, "pertura", "recipes", "*", "card.json")))
        cls.grade = grades[0] if grades else ""
        cls.card = cards[0] if cards else ""
        cls.log = os.path.join(cls.session, "confinement.log")
        code = PROBE.format(src=SRC, view=cls.view, work=cls.work, log=cls.log, key=cls.key, build=cls.build,
                            hidden_npy=cls.hidden_npy, hidden_csv=cls.hidden_csv, session=cls.session,
                            grade=cls.grade, card=cls.card, hidden_h5=cls.hidden_h5)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, cwd=cls.work)
        line = [x for x in p.stdout.splitlines() if x.startswith("RESULT")]
        if not line:
            raise AssertionError(f"the probe failed: {p.stdout[-2000:]} {p.stderr[-3000:]}")
        cls.out = json.loads(line[-1][len("RESULT"):])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_view_and_the_working_folder_are_open(self):
        for case in ("read_view", "list_view", "np_save_work", "pd_to_csv_work", "import_after"):
            self.assertEqual(self.out[case], "ok", case)

    def test_the_hidden_side_is_denied(self):
        for case in ("read_key", "list_build", "np_load_hidden", "pd_read_csv_hidden"):
            self.assertEqual(self.out[case], "denied", case)
        # some pandas versions catch the PermissionError and say the file does not exist: the key is still unread
        self.assertTrue(self.out["pd_read_json_key"] == "denied" or
                        self.out["pd_read_json_key"].startswith("error: FileNotFoundError"), self.out["pd_read_json_key"])

    def test_writes_outside_and_the_event_store_are_denied(self):
        for case in ("np_save_outside", "pd_to_csv_outside", "forge_event_store", "sqlite_event_store"):
            self.assertEqual(self.out[case], "denied", case)

    def test_links_renames_and_proc_paths_to_the_key_are_denied(self):
        cases = ("hard_link_key", "symlink_key", "rename_into_work") + (("proc_root_key",) if os.name == "posix" else ())
        for case in cases:
            self.assertEqual(self.out[case], "denied", case)
        self.assertTrue(os.path.exists(self.key))

    def test_databases_home_folders_processes_and_object_walks_are_denied(self):
        for case in ("sqlite_memory", "home_ipython", "gc_objects", "spawn") + (("fork",) if "fork" in self.out else ()):
            self.assertEqual(self.out[case], "denied", case)

    def test_programs_and_the_network_are_denied(self):
        for case in ("subprocess", "os_system", "network"):
            self.assertEqual(self.out[case], "denied", case)

    def test_recipe_grades_are_denied_but_cards_are_not(self):
        if not self.grade:
            self.skipTest("no recipe grade files")
        self.assertEqual(self.out["grade_file"], "denied")
        self.assertEqual(self.out["card_file"], "ok")

    def test_h5py_applies_the_allowlist(self):
        if "h5py_hidden" not in self.out:
            self.skipTest("no h5py")
        self.assertEqual(self.out["h5py_hidden"], "denied")
        self.assertEqual(self.out["h5py_view"], "ok")
        self.assertEqual(self.out["h5py_write_view"], "denied")  # the shared view is never written
        self.assertEqual(self.out["h5py_write_work"], "ok")

    def test_denials_are_logged(self):
        with open(self.log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("key.json", text)
        self.assertIn("DENIED\tsubprocess.Popen", text)


if __name__ == "__main__":
    unittest.main()
