"""Page-view / unique-visitor counting for the admin portal.

Deliberately minimal and privacy-preserving: no raw IP or user-agent is ever
stored. Each view is recorded against a per-day salted hash of (ip, user
agent) — enough to dedupe "unique visitors" for a calendar day without
building a persistent cross-day fingerprint of any one visitor. Uses the
same SQLite/Postgres :class:`~pbicompass.service.db._Connection` wrapper as
``AccountStore``/``JobStore``, so it works unmodified against either backend.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from .db import _Connection


def _today() -> str:
    return date.today().isoformat()


def visitor_hash(salt: str, ip: str, user_agent: str) -> str:
    """Rotates daily (the day is part of the hashed material) so no stored
    value can be used to track a visitor across days."""
    material = f"{salt}|{_today()}|{ip}|{user_agent}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@dataclass
class DayCount:
    day: str
    views: int
    unique_visitors: int


class VisitStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = _Connection(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS visits (
                    id TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    path TEXT NOT NULL,
                    visitor_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            self._conn.commit()

    def record(self, path: str, visitor_hash_value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO visits (id, day, path, visitor_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, _today(), path[:200], visitor_hash_value, time.time()),
            )
            self._conn.commit()

    def views_today(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM visits WHERE day = ?", (_today(),)
            ).fetchone()
        return int(row["c"])

    def unique_visitors_today(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT visitor_hash) AS c FROM visits WHERE day = ?", (_today(),)
            ).fetchone()
        return int(row["c"])

    def views_all_time(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM visits").fetchone()
        return int(row["c"])

    def unique_visitors_all_time(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(DISTINCT visitor_hash) AS c FROM visits"
            ).fetchone()
        return int(row["c"])

    def daily_breakdown(self, days: int = 14) -> list[DayCount]:
        """Last ``days`` calendar days (oldest first), including days with no
        visits at all, for a simple sparkline on the admin overview."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, COUNT(*) AS views, COUNT(DISTINCT visitor_hash) AS uniques "
                "FROM visits WHERE day >= ? GROUP BY day",
                ((date.today() - timedelta(days=days - 1)).isoformat(),),
            ).fetchall()
        by_day = {r["day"]: (int(r["views"]), int(r["uniques"])) for r in rows}
        out = []
        for i in range(days - 1, -1, -1):
            day = (date.today() - timedelta(days=i)).isoformat()
            views, uniques = by_day.get(day, (0, 0))
            out.append(DayCount(day=day, views=views, unique_visitors=uniques))
        return out
