"""Local tamper-evident chain; not distributed, immutable, or a real blockchain."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

def digest(previous, payload):
    return hashlib.sha256((previous+payload).encode()).hexdigest()

class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, previous TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL UNIQUE)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def append(self, event):
        payload = json.dumps({**event, "timestamp": datetime.now(timezone.utc).isoformat()}, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
            previous = row[0] if row else "0"*64
            h = digest(previous, payload)
            cursor = db.execute("INSERT INTO ledger(previous,payload,hash) VALUES(?,?,?)", (previous, payload, h))
            return {"id": cursor.lastrowid, "previous": previous, "hash": h, **json.loads(payload)}

    def entries(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,previous,payload,hash FROM ledger ORDER BY id").fetchall()
        return [{"id": i, "previous": p, **json.loads(data), "hash": h} for i,p,data,h in rows]

    def verify(self, target=None):
        with self.connect() as db:
            rows = db.execute("SELECT id,previous,payload,hash FROM ledger ORDER BY id").fetchall()
        previous = "0"*64
        for n, (i,p,data,h) in enumerate(rows, 1):
            if i != n or p != previous or digest(p,data) != h:
                return {"valid": False, "checked": n, "reason": "Chain integrity failure"}
            previous = h
            if target == h:
                return {"valid": True, "checked": n, "hash": h}
        return {"valid": target is None, "checked": len(rows), "reason": "Hash not found" if target else "Chain verified"}
