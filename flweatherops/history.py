"""Local briefing history in SQLite, so you can compare conditions over time.

Each briefing logs one row with the current conditions and the verdicts. The
database is ``$XDG_DATA_HOME/flweatherops/history.db``, which defaults to
``~/.local/share/flweatherops/history.db``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at TEXT NOT NULL,          -- local ISO time the briefing was generated
    location_key TEXT NOT NULL,
    location_name TEXT NOT NULL,
    lat REAL, lon REAL,
    drone_status TEXT, marine_status TEXT, tropical_status TEXT,
    temp_f REAL, heat_index_f REAL,
    wind_mph REAL, gust_mph REAL, wind_aloft_mph REAL, wind_dir REAL,
    precip_prob_pct REAL, visibility_mi REAL, cape REAL,
    wave_ft REAL, wave_period_s REAL,
    reasons TEXT                       -- JSON: {"drone": [...], "marine": [...]}
);
CREATE INDEX IF NOT EXISTS idx_briefings_loc_time ON briefings (location_key, logged_at);
"""

COLUMNS = [
    "logged_at", "location_key", "location_name", "lat", "lon",
    "drone_status", "marine_status", "tropical_status",
    "temp_f", "heat_index_f", "wind_mph", "gust_mph", "wind_aloft_mph", "wind_dir",
    "precip_prob_pct", "visibility_mi", "cape", "wave_ft", "wave_period_s", "reasons",
]


def db_path() -> Path:
    """Location of the history database."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "flweatherops" / "history.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@dataclass
class HistoryRow:
    """A logged briefing (subset used for display)."""

    logged_at: datetime
    location_key: str
    location_name: str
    drone_status: str | None
    marine_status: str | None
    tropical_status: str | None
    wind_mph: float | None
    gust_mph: float | None
    wind_aloft_mph: float | None
    wave_ft: float | None
    precip_prob_pct: float | None
    temp_f: float | None
    reasons: dict[str, list[str]]


def log_briefing(record: dict[str, Any], *, min_interval_min: float = 30, path: Path | None = None) -> bool:
    """Insert a briefing record. Skips (returns False) if the same location was
    logged less than ``min_interval_min`` minutes earlier. Never raises."""
    try:
        with _connect(path) as conn:
            last = conn.execute(
                "SELECT logged_at FROM briefings WHERE location_key = ? ORDER BY logged_at DESC LIMIT 1",
                (record["location_key"],),
            ).fetchone()
            if last:
                prev = datetime.fromisoformat(last["logged_at"])
                now = datetime.fromisoformat(record["logged_at"])
                if abs(now - prev) < timedelta(minutes=min_interval_min):
                    return False
            values = [
                json.dumps(record.get(c, {})) if c == "reasons" else record.get(c) for c in COLUMNS
            ]
            conn.execute(
                f"INSERT INTO briefings ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                values,
            )
        return True
    except (sqlite3.Error, OSError, KeyError, ValueError):
        return False


def recent(limit: int = 20, location_key: str | None = None, path: Path | None = None) -> list[HistoryRow]:
    """Most recent logged briefings, newest first (empty list on any error)."""
    try:
        with _connect(path) as conn:
            q = "SELECT * FROM briefings"
            args: list[Any] = []
            if location_key:
                q += " WHERE location_key = ?"
                args.append(location_key)
            q += " ORDER BY logged_at DESC LIMIT ?"
            args.append(int(limit))
            rows = conn.execute(q, args).fetchall()
    except (sqlite3.Error, OSError):
        return []
    out = []
    for r in rows:
        try:
            reasons = json.loads(r["reasons"] or "{}")
        except ValueError:
            reasons = {}
        out.append(HistoryRow(
            logged_at=datetime.fromisoformat(r["logged_at"]),
            location_key=r["location_key"],
            location_name=r["location_name"],
            drone_status=r["drone_status"],
            marine_status=r["marine_status"],
            tropical_status=r["tropical_status"],
            wind_mph=r["wind_mph"],
            gust_mph=r["gust_mph"],
            wind_aloft_mph=r["wind_aloft_mph"],
            wave_ft=r["wave_ft"],
            precip_prob_pct=r["precip_prob_pct"],
            temp_f=r["temp_f"],
            reasons=reasons if isinstance(reasons, dict) else {},
        ))
    return out
