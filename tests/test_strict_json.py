"""Every message the MCP server writes is strict JSON (docs/adr/0024, section 6): a missing number goes out as null,
since a client drops a line that carries NaN and the call it answers never returns (trial 1, attempt 3)."""
import io
import json
import queue
import threading
import time
import unittest

import numpy as np

from pertura import mcp_server


def strict(text):
    """json.loads that refuses NaN and Infinity, as a JavaScript client does."""
    def refuse(name):
        raise ValueError(f"not JSON: {name}")
    return json.loads(text, parse_constant=refuse)


ANSWER = {"effect": float("nan"), "low": None, "high": float("inf"), "units": np.int64(0),
          "per_unit": {"a": np.float64("nan"), "b": np.float64(0.5)}, "values": np.array([1.0, np.nan, -np.inf]),
          "flag": np.bool_(True), "rows": [{"effect": float("-inf")}, (np.float32(1.5), "x")]}


class Service:
    TOOLS = [{"name": "result", "inputSchema": {"type": "object"}}]

    def call(self, name, args, **kw):
        return True, ANSWER


class ConcurrentService(Service):
    CONCURRENT = True


class Lines:
    def __init__(self, msgs):
        self.q = queue.Queue()
        for m in msgs:
            self.q.put(json.dumps(m) + "\n")

    def readline(self):
        try:
            return self.q.get(timeout=5)
        except queue.Empty:
            return ""


class Out:
    def __init__(self):
        self.lines, self.lock = [], threading.Lock()

    def write(self, text):
        with self.lock:
            self.lines += [x for x in text.splitlines() if x.strip()]

    def flush(self):
        pass


def call(i):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": "result", "arguments": {}}}


class TestStrictJson(unittest.TestCase):
    def check(self, line):
        msg = strict(line)
        r = msg["result"]
        self.assertEqual(strict(r["content"][0]["text"]), r["structuredContent"])
        s = r["structuredContent"]
        self.assertIsNone(s["effect"])
        self.assertIsNone(s["high"])
        self.assertEqual(s["units"], 0)
        self.assertEqual(s["per_unit"], {"a": None, "b": 0.5})
        self.assertEqual(s["values"], [1.0, None, None])
        self.assertIs(s["flag"], True)
        self.assertEqual(s["rows"], [{"effect": None}, [1.5, "x"]])

    def test_a_missing_number_goes_out_as_null(self):
        self.check(mcp_server.dumps(mcp_server.handle(Service(), call(1), {})))

    def test_the_one_at_a_time_loop_writes_strict_json(self):
        out = io.StringIO()
        mcp_server.serve(Service(), io.StringIO(json.dumps(call(1)) + "\n"), out)
        self.check(out.getvalue().splitlines()[0])

    def test_the_concurrent_server_writes_strict_json(self):
        out = Out()
        t = threading.Thread(target=mcp_server.serve, args=(ConcurrentService(), Lines([call(1)]), out), daemon=True)
        t.start()
        end = time.time() + 10
        while not out.lines and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(out.lines)
        self.check(out.lines[0])

    def test_plain_values_are_unchanged(self):
        v = {"a": 1, "b": "x", "c": [True, None, 2.5], "d": {"e": 0}}
        self.assertEqual(strict(mcp_server.dumps(v)), v)


if __name__ == "__main__":
    unittest.main()
