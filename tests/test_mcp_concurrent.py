"""The concurrent MCP server (pertura.mcp_server._ConcurrentServer; docs/adr/0016 §6): parallel subagents' calls
reach the server at the same time (measured 2026-10-11), so a service that declares CONCURRENT is served by one
reader, a pool of threads and one stdout lock, with forms routed to the call that asked."""
import json
import os
import queue
import threading
import time
import unittest

from pertura import mcp_server


class Lines:
    """A stdin that gives a line only when the test sends one ("" ends the input)."""

    def __init__(self):
        self.q = queue.Queue()

    def send(self, msg):
        self.q.put(json.dumps(msg) + "\n")

    def end(self):
        self.q.put("")

    def readline(self):
        return self.q.get()


class Out:
    """A stdout that keeps every message written, and waits for one."""

    def __init__(self):
        self.lock, self.msgs, self.cv = threading.Lock(), [], threading.Condition()

    def write(self, text):
        with self.cv:
            self.msgs += [json.loads(x) for x in text.splitlines() if x.strip()]
            self.cv.notify_all()

    def flush(self):
        pass

    def wait_for(self, pred, timeout=20):
        end = time.time() + timeout
        with self.cv:
            while True:
                hit = [m for m in self.msgs if pred(m)]
                if hit:
                    return hit[0]
                left = end - time.time()
                if left <= 0:
                    raise AssertionError(f"no such message in {self.msgs}")
                self.cv.wait(left)


def call(i, name, **args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}


class Service:
    CONCURRENT = True
    USES_ELICITATION = True
    USES_CANCEL = True
    WAIT_TOOLS = ("wait",)
    TOOLS = [{"name": n, "inputSchema": {"type": "object"}} for n in ("slow", "ask", "late_ask", "wait")]

    def __init__(self):
        self.closed, self.release, self.stopped = False, threading.Event(), threading.Event()

    def call(self, name, args, elicit=None, cancel=None):
        if name == "slow":
            time.sleep(args.get("seconds", 1.0))
            return True, {"label": args.get("label")}
        if name in ("ask", "late_ask"):
            if name == "late_ask":
                time.sleep(0.5)  # it reaches its form after the input has ended
            action, content = elicit("Which one?", {"type": "object"})
            return True, {"action": action, "content": content}
        end = time.time() + 30  # wait: until released, cancelled or stopped
        while time.time() < end and not self.release.is_set() and not cancel.is_set() and not self.stopped.is_set():
            time.sleep(0.05)
        return True, {"waited": self.release.is_set(), "cancelled": cancel.is_set()}

    def current_user_actions(self):
        return [{"action": "new_direction"}]

    def stop_waiting(self):
        self.stopped.set()

    def close(self):
        self.closed = True


class TestServerLimits(unittest.TestCase):
    def test_the_thread_variables_hold_numba_and_count_the_servers_own_pools(self):
        from unittest import mock
        from pertura import limits
        self.assertIn("NUMBA_NUM_THREADS", limits.THREAD_VARIABLES)
        per = limits.THREAD_LIMIT // 3  # 2 runs' R children and the process's own pools: 3 x 4 at the limit of 12
        self.assertEqual(limits.threads_per_run(2), per)
        with mock.patch.dict(os.environ, {"OMP_NUM_THREADS": "1"}, clear=False):
            for v in limits.THREAD_VARIABLES[1:]:
                os.environ.pop(v, None)
            with mock.patch.object(os, "nice", create=True, side_effect=lambda n: 10 if n == 0 else n):
                self.assertEqual(limits.limit_this_process(2), per)
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "1")  # set lower already: kept
            self.assertEqual(os.environ["NUMBA_NUM_THREADS"], str(per))

    def test_only_the_servers_own_descendants_are_ended(self):
        parents = {10: 1, 11: 10, 12: 11, 13: 10, 20: 1, 21: 20}  # 20 (say tee) shares nothing with 10's tree
        self.assertEqual(sorted(mcp_server.descendants(10, parents)), [11, 12, 13])
        self.assertEqual(mcp_server.descendants(99, parents), [])


class TestConcurrentServer(unittest.TestCase):
    def setUp(self):
        self.svc, self.inp, self.out = Service(), Lines(), Out()
        self.thread = threading.Thread(target=mcp_server.serve, args=(self.svc, self.inp, self.out), daemon=True)
        self.thread.start()
        self.inp.send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}}}})
        self.out.wait_for(lambda m: m.get("id") == 0)

    def tearDown(self):
        self.svc.release.set()
        self.inp.end()
        self.thread.join(timeout=20)

    def by_id(self, i):
        return self.out.wait_for(lambda m: m.get("id") == i and "result" in m)

    def test_calls_from_two_workers_run_at_the_same_time_and_a_ping_is_answered_meanwhile(self):
        t0 = time.time()
        self.inp.send(call(1, "slow", seconds=1.5, label="w1"))
        self.inp.send(call(2, "slow", seconds=1.5, label="w2"))
        self.inp.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(self.by_id(3)["result"], {})
        self.assertLess(time.time() - t0, 1.0)  # answered on the reader, not behind the calls
        self.assertEqual(self.by_id(1)["result"]["structuredContent"]["label"], "w1")
        self.assertEqual(self.by_id(2)["result"]["structuredContent"]["label"], "w2")
        self.assertLess(time.time() - t0, 2.7)  # together, not one after the other (3 s)

    def test_a_form_is_answered_while_another_call_runs(self):
        self.inp.send(call(1, "ask"))
        self.inp.send(call(2, "slow", seconds=1.0, label="w2"))
        form = self.out.wait_for(lambda m: m.get("method") == "elicitation/create")
        self.inp.send({"jsonrpc": "2.0", "id": form["id"], "result": {"action": "accept", "content": {"x": 1}}})
        self.assertEqual(self.by_id(1)["result"]["structuredContent"], {"action": "accept", "content": {"x": 1}})
        self.assertEqual(self.by_id(2)["result"]["structuredContent"]["label"], "w2")

    def test_a_cancelled_call_ends_its_open_form(self):
        self.inp.send(call(1, "ask"))
        self.out.wait_for(lambda m: m.get("method") == "elicitation/create")
        self.inp.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}})
        self.assertEqual(self.by_id(1)["result"]["structuredContent"]["action"], "cancel")

    def test_a_wait_beyond_the_wait_pool_is_told_to_retry(self):
        n = mcp_server.WAIT_POOL
        for i in range(1, n + 1):
            self.inp.send(call(i, "wait"))
        time.sleep(0.3)
        self.inp.send(call(n + 1, "wait"))
        extra = self.by_id(n + 1)["result"]
        self.assertTrue(extra["isError"])
        self.assertEqual(extra["structuredContent"]["error"], "retry")
        self.assertEqual(extra["structuredContent"]["user_actions"], [{"action": "new_direction"}])
        self.assertEqual(json.loads(extra["content"][0]["text"]), extra["structuredContent"])
        self.svc.release.set()
        for i in range(1, n + 1):
            self.assertTrue(self.by_id(i)["result"]["structuredContent"]["waited"])

    def test_a_cancelled_call_ends_its_wait(self):
        self.inp.send(call(1, "wait"))
        time.sleep(0.2)
        self.inp.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}})
        self.assertTrue(self.by_id(1)["result"]["structuredContent"]["cancelled"])

    def test_a_form_asked_for_after_the_end_of_input_is_cancelled_and_the_server_ends(self):
        self.inp.send(call(1, "late_ask"))
        self.inp.send(call(2, "wait"))
        time.sleep(0.1)
        self.inp.end()
        self.thread.join(timeout=20)
        self.assertFalse(self.thread.is_alive())  # no form left waiting, no wait holding the pools
        self.assertEqual(self.by_id(1)["result"]["structuredContent"]["action"], "cancel")
        self.assertFalse(self.by_id(2)["result"]["structuredContent"]["waited"])
        self.assertTrue(self.svc.closed)

    def test_the_end_of_input_lets_calls_in_flight_finish_before_the_service_closes(self):
        self.inp.send(call(1, "slow", seconds=1.0, label="last"))
        time.sleep(0.2)
        self.inp.end()
        self.thread.join(timeout=20)
        self.assertEqual(self.by_id(1)["result"]["structuredContent"]["label"], "last")
        self.assertTrue(self.svc.closed)


if __name__ == "__main__":
    unittest.main()
