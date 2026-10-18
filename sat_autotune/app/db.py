"""Opslag in SQLite (/data), blijft behouden na herstart."""
from __future__ import annotations

import json
import sqlite3
import time


class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS changes(
                id INTEGER PRIMARY KEY, ts REAL, key TEXT, field TEXT, old TEXT, new TEXT,
                source TEXT, reason TEXT, status TEXT, eval_ts REAL, baseline TEXT, result TEXT,
                closed_ts REAL);
            CREATE TABLE IF NOT EXISTS days(date TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, ts REAL, data TEXT);
            """
        )

    def get(self, k, default=None):
        r = self.c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k, v):
        self.c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))
        self.c.commit()

    def add_report(self, kind, data, ts=None):
        self.c.execute("INSERT INTO reports(ts,kind,data) VALUES(?,?,?)", (ts or time.time(), kind, json.dumps(data)))
        self.c.commit()

    def upsert_day(self, date, data):
        self.c.execute("INSERT OR REPLACE INTO days VALUES(?,?)", (date, json.dumps(data)))

    def days(self):
        return [json.loads(r["data"]) for r in self.c.execute("SELECT data FROM days ORDER BY date")]

    def commit(self):
        self.c.commit()

    def prune(self, kind, keep):
        self.c.execute("DELETE FROM reports WHERE kind=? AND id NOT IN "
                       "(SELECT id FROM reports WHERE kind=? ORDER BY ts DESC LIMIT ?)", (kind, kind, keep))
        self.c.commit()

    def reports(self, kind=None, limit=20):
        q = "SELECT * FROM reports" + (" WHERE kind=?" if kind else "") + " ORDER BY ts DESC LIMIT ?"
        args = ((kind,) if kind else ()) + (limit,)
        return [dict(r, data=json.loads(r["data"])) for r in self.c.execute(q, args)]

    def add_change(self, **kw):
        cols = ["ts", "key", "field", "old", "new", "source", "reason", "status", "eval_ts", "baseline", "result"]
        vals = [json.dumps(kw[c]) if c in ("old", "new", "baseline", "result") else kw.get(c) for c in cols]
        cur = self.c.execute(f"INSERT INTO changes({','.join(cols)}) VALUES({','.join('?'*len(cols))})", vals)
        self.c.commit()
        return cur.lastrowid

    def update_change(self, cid, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        vals = [json.dumps(v) if k in ("old", "new", "baseline", "result") else v for k, v in kw.items()]
        self.c.execute(f"UPDATE changes SET {sets} WHERE id=?", vals + [cid])
        self.c.commit()

    def changes(self, limit=50, status=None):
        q = "SELECT * FROM changes" + (" WHERE status=?" if status else "") + " ORDER BY ts DESC LIMIT ?"
        args = ((status,) if status else ()) + (limit,)
        out = []
        for r in self.c.execute(q, args):
            d = dict(r)
            for k in ("old", "new", "baseline", "result"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    def active_change(self):
        r = self.changes(1, "pending_eval")
        return r[0] if r else None

    def last_snapshot(self):
        r = self.c.execute("SELECT data FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        return json.loads(r["data"]) if r else None

    def add_snapshot(self, data):
        self.c.execute("INSERT INTO snapshots(ts,data) VALUES(?,?)", (time.time(), json.dumps(data)))
        self.c.commit()
