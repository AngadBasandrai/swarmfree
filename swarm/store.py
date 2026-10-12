import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


class Store:
    """Small durable record store. One process owns orchestration in this release."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.atomic_depth = 0
        self.db = sqlite3.connect(path, check_same_thread=False)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 5:
            self.db.close()
            raise ValueError("This workspace needs a newer Swarm Workbench version")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, value TEXT)")
        self.db.execute("PRAGMA user_version=5")
        self.db.commit()
        for operation in self.all("operation"):
            if operation.get("status") == "running":
                self.patch("operation", operation["id"], dict(status="interrupted", error="Engine restarted during the operation; inspect effects before retrying"))

    def get(self, kind, id, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM records WHERE kind=? AND id=?", (kind, id)).fetchone()
            return json.loads(row[0]) if row else default

    def all(self, kind):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT value FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def put(self, kind, id, value):
        with self.lock:
            self.db.execute("INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value", (kind, id, json.dumps(value)))
            if not self.atomic_depth: self.db.commit()
        return value

    def patch(self, kind, id, changes):
        with self.lock:
            value = self.get(kind, id)
            if value is None:
                raise KeyError(id)
            value.update(changes)
            return self.put(kind, id, value)

    def delete(self, kind, id):
        with self.lock:
            self.db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, id))
            if not self.atomic_depth: self.db.commit()

    def event(self, run_id, type, **data):
        with self.lock:
            event = dict(type=type, time=time.time(), **data)
            self.db.execute("INSERT INTO events(run_id,value) VALUES (?,?)", (run_id, json.dumps(event)))
            if not self.atomic_depth: self.db.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            name=uid("atomic_")
            self.db.execute("SAVEPOINT "+name);self.atomic_depth+=1
            try:
                yield
                self.db.execute("RELEASE SAVEPOINT "+name)
            except BaseException:
                self.db.execute("ROLLBACK TO SAVEPOINT "+name)
                self.db.execute("RELEASE SAVEPOINT "+name)
                raise
            finally: self.atomic_depth-=1

    def events(self, run_id, limit=100):
        with self.lock:
            rows = self.db.execute("SELECT seq,value FROM events WHERE run_id=? ORDER BY seq DESC LIMIT ?", (run_id, limit)).fetchall()
            return [dict(seq=r[0], **json.loads(r[1])) for r in reversed(rows)]

    def close(self):
        self.db.close()
