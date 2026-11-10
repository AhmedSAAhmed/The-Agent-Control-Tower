"""Event store + bus: every agent action and operator intervention is one hash-chained event.

The SQLite table is the audit log (append-only, each row carries the hash of the previous row),
and the bus fans the same event out to live SSE subscribers.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  tenant_id TEXT,
  agent_id TEXT,
  task_id TEXT,
  type TEXT NOT NULL,
  actor TEXT NOT NULL,
  severity TEXT NOT NULL,
  summary TEXT NOT NULL,
  data TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_agent ON events(agent_id, id);
CREATE INDEX IF NOT EXISTS ix_events_tenant ON events(tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_events_task ON events(task_id, id);
"""

FIELDS = ("id", "ts", "tenant_id", "agent_id", "task_id", "type", "actor", "severity", "summary", "data")


def _digest(prev_hash: str, row: dict[str, Any]) -> str:
    body = json.dumps({k: row[k] for k in FIELDS}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class EventStore:
    def __init__(self, path: str | Path, fresh: bool = True):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
            if fresh and path.exists():
                path.unlink()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        last = self._db.execute("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        self._last_id = last["id"] if last else 0
        self._last_hash = last["hash"] if last else GENESIS

    def append(self, *, type: str, summary: str, actor: str, severity: str = "info",
               tenant_id: str | None = None, agent_id: str | None = None,
               task_id: str | None = None, data: dict | None = None) -> dict:
        with self._lock:
            row = {
                "id": self._last_id + 1,
                "ts": round(time.time(), 3),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "type": type,
                "actor": actor,
                "severity": severity,
                "summary": summary,
                "data": json.dumps(data or {}, ensure_ascii=False, sort_keys=True, default=str),
            }
            row["prev_hash"] = self._last_hash
            row["hash"] = _digest(self._last_hash, row)
            self._db.execute(
                "INSERT INTO events VALUES (:id,:ts,:tenant_id,:agent_id,:task_id,:type,:actor,"
                ":severity,:summary,:data,:prev_hash,:hash)", row)
            self._db.commit()
            self._last_id, self._last_hash = row["id"], row["hash"]
        return self._hydrate(row)

    @staticmethod
    def _hydrate(row) -> dict:
        d = dict(row)
        d["data"] = json.loads(d["data"]) if isinstance(d["data"], str) else d["data"]
        return d

    def query(self, *, tenant_id: str | None = None, agent_id: str | None = None,
              task_id: str | None = None, types: list[str] | None = None,
              min_severity: str | None = None, limit: int = 200, desc: bool = True) -> list[dict]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if tenant_id:
            sql += " AND tenant_id = ?"; args.append(tenant_id)
        if agent_id:
            sql += " AND agent_id = ?"; args.append(agent_id)
        if task_id:
            sql += " AND task_id = ?"; args.append(task_id)
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"; args.extend(types)
        if min_severity == "warn":
            sql += " AND severity IN ('warn','critical')"
        elif min_severity == "critical":
            sql += " AND severity = 'critical'"
        sql += f" ORDER BY id {'DESC' if desc else 'ASC'} LIMIT ?"; args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._hydrate(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def verify_chain(self) -> dict:
        """Recompute every hash from genesis. Any edited, deleted or reordered row breaks it."""
        prev, checked = GENESIS, 0
        with self._lock:
            rows = self._db.execute("SELECT * FROM events ORDER BY id ASC").fetchall()
        for r in rows:
            row = dict(r)
            if row["prev_hash"] != prev or _digest(prev, row) != row["hash"]:
                return {"ok": False, "checked": checked, "broken_at": row["id"], "head": prev}
            prev, checked = row["hash"], checked + 1
        return {"ok": True, "checked": checked, "broken_at": None, "head": prev}

    def tamper_for_test(self, event_id: int, summary: str) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET summary = ? WHERE id = ?", (summary, event_id))
            self._db.commit()


class EventBus:
    """Persist-then-publish. Subscribers are SSE connections, each with its own bounded queue."""

    def __init__(self, store: EventStore):
        self.store = store
        self._subs: set[asyncio.Queue] = set()
        self.last_viewer_at = time.time()

    def emit(self, **kwargs) -> dict:
        event = self.store.append(**kwargs)
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a slow browser tab must never block the control plane
        return event

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        self.last_viewer_at = time.time()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        self.last_viewer_at = time.time()

    @property
    def viewers(self) -> int:
        return len(self._subs)
