"""SQLite-backed memory + pet-state persistence.

`memories` carries notable events the model flagged via the Intent `remember`
field; an FTS5 virtual table powers keyword retrieval. `pet_state` is a single
row so the pet's energy/mood/bond/level survive restarts.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from ..log import get
from ..types import MemoryRecord, PetState

log = get("memory.store")

_STOPWORDS = {
    "the", "a", "an", "is", "was", "to", "of", "and", "or", "in", "on", "at",
    "i", "he", "she", "it", "they", "you", "my", "me", "his", "her", "this",
    "that", "with", "for", "be", "are", "his", "had", "has",
}


def _keywords(text: str) -> str:
    toks = re.findall(r"[a-zA-Z0-9]+", text.lower())
    kw = [t for t in toks if t not in _STOPWORDS and len(t) > 2]
    return " ".join(dict.fromkeys(kw))  # de-dup, preserve order


class MemoryStore:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.db.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL NOT NULL,
                text     TEXT NOT NULL,
                kind     TEXT NOT NULL DEFAULT 'event',
                keywords TEXT NOT NULL DEFAULT '',
                salience REAL NOT NULL DEFAULT 0.5
            );
            CREATE TABLE IF NOT EXISTS pet_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                energy REAL, mood REAL, bond REAL, level INTEGER, xp INTEGER,
                last_app TEXT, time_in_app_s REAL, updated_at REAL
            );
            """
        )
        # FTS5 is optional — degrade gracefully if the build lacks it.
        try:
            cur.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(text, keywords, content='memories', content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, text, keywords)
                    VALUES (new.id, new.text, new.keywords);
                END;
                """
            )
            self.has_fts = True
        except sqlite3.OperationalError:
            log.info("SQLite FTS5 unavailable; using LIKE-based keyword search")
            self.has_fts = False
        self.db.commit()

    # ---- memories ---------------------------------------------------------- #
    def add(self, text: str, kind: str = "event", salience: float = 0.5) -> int:
        text = text.strip()
        if not text:
            return -1
        # de-duplicate: the same fact (e.g. an app habit or a poke) recurs every
        # session — refresh the existing row's recency/salience instead of piling
        # up identical copies that crowd out retrieval.
        existing = self.db.execute(
            "SELECT id, salience FROM memories WHERE text = ? LIMIT 1", (text,)
        ).fetchone()
        if existing:
            self.db.execute(
                "UPDATE memories SET ts = ?, salience = ? WHERE id = ?",
                (time.time(), max(float(salience), float(existing["salience"])), existing["id"]),
            )
            self.db.commit()
            return int(existing["id"])
        cur = self.db.execute(
            "INSERT INTO memories (ts, text, kind, keywords, salience) VALUES (?,?,?,?,?)",
            (time.time(), text, kind, _keywords(text), float(salience)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def recent(self, n: int = 10) -> list[MemoryRecord]:
        rows = self.db.execute(
            "SELECT * FROM memories ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def search(self, query: str, k: int = 10) -> list[MemoryRecord]:
        q = _keywords(query)
        if not q:
            return []
        if self.has_fts:
            try:
                match = " OR ".join(q.split())
                rows = self.db.execute(
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                    "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match, k),
                ).fetchall()
                return [self._row(r) for r in rows]
            except sqlite3.OperationalError:
                pass
        # LIKE fallback
        terms = q.split()
        clause = " OR ".join(["keywords LIKE ?"] * len(terms))
        params = [f"%{t}%" for t in terms] + [k]
        rows = self.db.execute(
            f"SELECT * FROM memories WHERE {clause} ORDER BY ts DESC LIMIT ?", params
        ).fetchall()
        return [self._row(r) for r in rows]

    def prune(self, max_rows: int) -> None:
        n = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if n <= max_rows:
            return
        # keep the most salient + most recent
        self.db.execute(
            "DELETE FROM memories WHERE id IN ("
            "  SELECT id FROM memories ORDER BY salience ASC, ts ASC LIMIT ?)",
            (n - max_rows,),
        )
        self.db.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=r["id"], ts=r["ts"], text=r["text"], kind=r["kind"],
            keywords=r["keywords"], salience=r["salience"],
        )

    # ---- pet state --------------------------------------------------------- #
    def load_pet_state(self) -> Optional[PetState]:
        r = self.db.execute("SELECT * FROM pet_state WHERE id = 1").fetchone()
        if not r:
            return None
        return PetState(
            energy=r["energy"], mood=r["mood"], bond=r["bond"], level=r["level"],
            xp=r["xp"], last_app=r["last_app"], time_in_app_s=r["time_in_app_s"],
            updated_at=r["updated_at"],
        )

    def save_pet_state(self, s: PetState) -> None:
        self.db.execute(
            "INSERT INTO pet_state (id, energy, mood, bond, level, xp, last_app, "
            "time_in_app_s, updated_at) VALUES (1,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET energy=excluded.energy, mood=excluded.mood, "
            "bond=excluded.bond, level=excluded.level, xp=excluded.xp, "
            "last_app=excluded.last_app, time_in_app_s=excluded.time_in_app_s, "
            "updated_at=excluded.updated_at",
            (s.energy, s.mood, s.bond, s.level, s.xp, s.last_app, s.time_in_app_s, s.updated_at),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
