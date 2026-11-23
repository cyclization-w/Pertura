"""The record's event store: one SQLite file (events.db), written one transaction at a time and read in order.
pertura.record appends and replays its events through these."""
import contextlib
import hashlib
import json
import sqlite3


def _rows(con):
    rows = con.execute("SELECT seq, event_id, event_type, timestamp, actor, payload FROM events ORDER BY seq")
    return [{"seq": r[0], "event_id": r[1], "event_type": r[2], "timestamp": r[3], "actor": r[4],
             "payload": json.loads(r[5])} for r in rows]


@contextlib.contextmanager
def _tx(path):
    """One write transaction, taken at once (BEGIN IMMEDIATE): committed on success, rolled back on any error."""
    con = sqlite3.connect(path, timeout=60, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
    finally:
        con.close()


@contextlib.contextmanager
def _db(path):
    """A connection that commits and is closed (sqlite3's own context manager only commits)."""
    con = sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
