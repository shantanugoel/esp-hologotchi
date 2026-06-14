"""Local, bounded, structured memory for Shiro (Phase 9c).

Memory is fully local and private. It lives in a versioned SQLite database and is
guarded by a single lock so the pet loop and the control-server request threads
never corrupt each other. Retrieval is deliberately simple and testable: tags,
recency, importance, and SQLite full-text search (FTS5 when available, a LIKE
fallback otherwise). Embeddings are intentionally left for later.

A salience rubric decides what is worth keeping so Shiro does not store every
idle tick. Importance decays over time and ticks up when a memory is recalled.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

SCHEMA_VERSION = 2

IMPORTANCE_THRESHOLD = 25.0
RECALL_BUMP = 3.0
RECALL_COOLDOWN_SECONDS = 300.0
TEXT_MATCH_BONUS = 60.0
RETENTION_HALFLIFE_SECONDS = 3 * 24 * 3600.0
DEDUPE_WINDOW_SECONDS = 6 * 3600.0
MAX_SUMMARY_LEN = 200
MAX_TAGS = 8
SPONTANEOUS_CALLBACK_MIN_AGE_SECONDS = 24 * 3600.0
SPONTANEOUS_CALLBACK_COOLDOWN_SECONDS = 6 * 3600.0

KIND_EPISODIC = "episodic"
KIND_SEMANTIC = "semantic"
KIND_AFFECT = "affect"
KINDS = frozenset({KIND_EPISODIC, KIND_SEMANTIC, KIND_AFFECT})

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "is", "are", "was", "to", "of", "in",
        "on", "it", "its", "for", "shiro", "owner", "this", "that", "with",
    }
)

Clock = Callable[[], float]


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    created_at: float
    source: str
    kind: str
    summary: str
    tags: tuple[str, ...]
    valence: int
    intensity: int
    importance: float
    last_recalled: float
    recall_count: int


@dataclass(frozen=True)
class MemorySummary:
    total: int
    by_kind: dict[str, int]
    by_source: dict[str, int]
    top: tuple[MemoryRecord, ...]
    writes_enabled: bool


@dataclass
class _StoredState:
    affect: dict[str, float] | None
    last_interaction: float | None
    body: dict[str, object] | None = None


class MemoryStore:
    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        now: Clock = time.time,
        writes_enabled: bool = True,
    ) -> None:
        self._now = now
        self._writes_enabled = writes_enabled
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._fts = False
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._migrate()
            self._fts = self._detect_fts()

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def writes_enabled(self) -> bool:
        return self._writes_enabled

    def set_writes_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._writes_enabled = enabled

    # -- schema -------------------------------------------------------------

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._migrate_v1()
            version = 1
        if version < 2:
            self._migrate_v2()
            version = 2
        self._conn.execute(f"PRAGMA user_version = {version}")
        self._conn.commit()

    def _migrate_v1(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                valence INTEGER NOT NULL DEFAULT 0,
                intensity INTEGER NOT NULL DEFAULT 0,
                importance REAL NOT NULL DEFAULT 0,
                last_recalled REAL NOT NULL DEFAULT 0,
                recall_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);
            CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
            CREATE TABLE IF NOT EXISTS pet_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                affect_json TEXT,
                last_interaction REAL,
                updated_at REAL
            );
            """
        )
        self._try_create_fts()

    def _migrate_v2(self) -> None:
        # Body-state continuity persistence (V2a): survive a host restart so a
        # long nap is not reset to "awake". Older databases created at v1 need the
        # column added; a fresh v1 table above lacks it too.
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(pet_state)").fetchall()
        }
        if "body_json" not in columns:
            self._conn.execute("ALTER TABLE pet_state ADD COLUMN body_json TEXT")

    def _try_create_fts(self) -> None:
        try:
            self._conn.executescript(
                """
                CREATE VIRTUAL TABLE memories_fts USING fts5(
                    summary, tags, content='memories', content_rowid='id'
                );
                CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, summary, tags)
                    VALUES (new.id, new.summary, new.tags);
                END;
                CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, summary, tags)
                    VALUES ('delete', old.id, old.summary, old.tags);
                END;
                CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, summary, tags)
                    VALUES ('delete', old.id, old.summary, old.tags);
                    INSERT INTO memories_fts(rowid, summary, tags)
                    VALUES (new.id, new.summary, new.tags);
                END;
                """
            )
        except sqlite3.OperationalError:
            # FTS5 is unavailable in this build; retrieval falls back to LIKE.
            pass

    def _detect_fts(self) -> bool:
        # Detect on every open, not just first migration, so a reopened
        # persistent DB keeps using its existing FTS index instead of silently
        # degrading to LIKE.
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
        ).fetchone()
        return row is not None

    # -- capture ------------------------------------------------------------

    def capture(
        self,
        source: str,
        summary: str,
        *,
        kind: str = KIND_EPISODIC,
        tags: list[str] | tuple[str, ...] | None = None,
        valence: int = 0,
        intensity: int = 0,
        owner_initiated: bool = False,
        alert: bool = False,
    ) -> int | None:
        """Score salience and store the moment only if it clears the threshold.

        Returns the row id of the stored (or bumped) memory, or ``None`` when the
        moment was not worth keeping or writes are disabled.
        """

        if not self._writes_enabled:
            return None
        summary = _clean_summary(summary)
        if not summary:
            return None
        if kind not in KINDS:
            raise ValueError(f"unknown memory kind: {kind!r}")
        valence = _clamp_signed(valence)
        intensity = _clamp_unsigned(intensity)
        tag_text = _encode_tags(tags)
        now = self._now()

        with self._lock:
            existing = self._find_recent_duplicate(source, summary, now)
            if existing is not None:
                return self._bump_existing(existing, intensity, now)

            novelty = 1.0
            repetition = self._count_source(source)
            importance = _score(
                valence=valence,
                intensity=intensity,
                owner_initiated=owner_initiated,
                alert=alert,
                novelty=novelty,
                repetition=repetition,
            )
            if importance < IMPORTANCE_THRESHOLD:
                return None

            cursor = self._conn.execute(
                """
                INSERT INTO memories
                    (created_at, source, kind, summary, tags, valence, intensity,
                     importance, last_recalled, recall_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (now, source, kind, summary, tag_text, valence, intensity, importance, now),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def _find_recent_duplicate(self, source: str, summary: str, now: float) -> sqlite3.Row | None:
        row = self._conn.execute(
            """
            SELECT * FROM memories
            WHERE source = ? AND summary = ? AND created_at >= ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (source, summary, now - DEDUPE_WINDOW_SECONDS),
        ).fetchone()
        return row

    def _bump_existing(self, row: sqlite3.Row, intensity: int, now: float) -> int:
        importance = min(100.0, float(row["importance"]) + RECALL_BUMP)
        self._conn.execute(
            """
            UPDATE memories
            SET importance = ?, intensity = MAX(intensity, ?), recall_count = recall_count + 1,
                last_recalled = ?
            WHERE id = ?
            """,
            (importance, intensity, now, row["id"]),
        )
        self._conn.commit()
        return int(row["id"])

    def _count_source(self, source: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE source = ?", (source,)
        ).fetchone()
        return int(row["n"])

    # -- retrieval ----------------------------------------------------------

    def retrieve(
        self,
        query: str | None = None,
        *,
        tags: list[str] | tuple[str, ...] | None = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        if limit < 1:
            return []
        now = self._now()
        tokens = _tokenize(query) if query else []
        tag_tokens = _normalize_tags(tags)
        pool = max(limit * 4, 20)

        with self._lock:
            text_rows = self._search_rows(tokens, pool) if tokens else []
            text_ids = {int(row["id"]) for row in text_rows}

            if tag_tokens:
                # An explicit tag is a hard filter, not just a ranking hint.
                base_rows: list[sqlite3.Row] = self._tag_rows(tag_tokens, pool)
            else:
                base_rows = list(self._recent_strong(pool)) + list(text_rows)

            candidates: dict[int, sqlite3.Row] = {
                int(row["id"]): row for row in base_rows
            }

            def score(row: sqlite3.Row) -> float:
                value = self._effective_importance(row, now)
                if int(row["id"]) in text_ids:
                    value += TEXT_MATCH_BONUS
                return value

            ranked = sorted(candidates.values(), key=score, reverse=True)[:limit]
            records = [_to_record(row) for row in ranked]
            if records and self._writes_enabled:
                self._mark_recalled([record.id for record in records], now)
            return records

    def _recent_strong(self, pool: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM memories ORDER BY importance DESC, created_at DESC LIMIT ?",
            (pool,),
        ).fetchall()

    def _tag_rows(self, tag_tokens: list[str], pool: int) -> list[sqlite3.Row]:
        clause = " AND ".join("tags LIKE ?" for _ in tag_tokens)
        params = [f"%{token}%" for token in tag_tokens]
        return self._conn.execute(
            f"SELECT * FROM memories WHERE {clause} "
            "ORDER BY importance DESC, created_at DESC LIMIT ?",
            (*params, pool),
        ).fetchall()

    def _search_rows(self, tokens: list[str], pool: int) -> list[sqlite3.Row]:
        if not tokens:
            return []
        if self._fts:
            match = " OR ".join(f"{token}*" for token in tokens)
            try:
                return self._conn.execute(
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                    "WHERE memories_fts MATCH ? ORDER BY m.importance DESC LIMIT ?",
                    (match, pool),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        clause = " OR ".join("summary LIKE ?" for _ in tokens)
        params = [f"%{token}%" for token in tokens]
        return self._conn.execute(
            f"SELECT * FROM memories WHERE {clause} ORDER BY importance DESC LIMIT ?",
            (*params, pool),
        ).fetchall()

    def _effective_importance(self, row: sqlite3.Row, now: float) -> float:
        anchor = max(float(row["created_at"]), float(row["last_recalled"]))
        age = max(0.0, now - anchor)
        decay = 0.5 ** (age / RETENTION_HALFLIFE_SECONDS)
        return float(row["importance"]) * decay

    def _mark_recalled(self, ids: list[int], now: float) -> None:
        # A genuine recall bumps importance and refreshes recency, but the loop
        # surfaces the strongest memories every tick; without a cooldown that
        # would pin them at max importance and never let them decay.
        placeholders = ",".join("?" for _ in ids)
        self._conn.execute(
            f"UPDATE memories SET recall_count = recall_count + 1, last_recalled = ?, "
            f"importance = MIN(100.0, importance + ?) WHERE id IN ({placeholders}) "
            f"AND (recall_count = 0 OR (? - last_recalled) >= ?)",
            (now, RECALL_BUMP, *ids, now, RECALL_COOLDOWN_SECONDS),
        )
        self._conn.commit()

    # -- inspection ---------------------------------------------------------

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"])

    def recent(self, limit: int = 10) -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_to_record(row) for row in rows]

    def count_by(
        self,
        *,
        source: str | None = None,
        tag: str | None = None,
        kind: str | None = None,
        after: float | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if tag is not None:
            token = _normalize_tag(tag)
            if not token:
                raise ValueError("tag must contain at least one usable character")
            clauses.append("tags LIKE ?")
            params.append(f"% {token} %")
        if kind is not None:
            if kind not in KINDS:
                raise ValueError(f"unknown memory kind: {kind!r}")
            clauses.append("kind = ?")
            params.append(kind)
        if after is not None:
            clauses.append("created_at >= ?")
            params.append(after)
        where = "" if not clauses else f" WHERE {' AND '.join(clauses)}"
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM memories{where}", params
            ).fetchone()
            return int(row["n"])

    def has_summary(self, summary: str, *, kind: str | None = None) -> bool:
        cleaned = _clean_summary(summary)
        if not cleaned:
            return False
        clauses = ["summary = ?"]
        params: list[object] = [cleaned]
        if kind is not None:
            if kind not in KINDS:
                raise ValueError(f"unknown memory kind: {kind!r}")
            clauses.append("kind = ?")
            params.append(kind)
        with self._lock:
            row = self._conn.execute(
                f"SELECT 1 FROM memories WHERE {' AND '.join(clauses)} LIMIT 1",
                params,
            ).fetchone()
            return row is not None

    def spontaneous_callback(
        self,
        *,
        min_age_seconds: float = SPONTANEOUS_CALLBACK_MIN_AGE_SECONDS,
        cooldown_seconds: float = SPONTANEOUS_CALLBACK_COOLDOWN_SECONDS,
    ) -> MemoryRecord | None:
        """Return one old, meaningful memory for a rare self-directed callback."""

        now = self._now()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM memories
                WHERE created_at <= ?
                  AND (? - last_recalled) >= ?
                  AND kind IN (?, ?)
                ORDER BY importance DESC, recall_count ASC, created_at ASC
                LIMIT 1
                """,
                (
                    now - max(0.0, min_age_seconds),
                    now,
                    max(0.0, cooldown_seconds),
                    KIND_EPISODIC,
                    KIND_SEMANTIC,
                ),
            ).fetchone()
            if row is None:
                return None
            record = _to_record(row)
            if self._writes_enabled:
                self._mark_recalled([record.id], now)
            return record

    def summary(self, top: int = 5) -> MemorySummary:
        with self._lock:
            total = int(self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"])
            by_kind = {
                str(row["kind"]): int(row["n"])
                for row in self._conn.execute(
                    "SELECT kind, COUNT(*) AS n FROM memories GROUP BY kind"
                ).fetchall()
            }
            by_source = {
                str(row["source"]): int(row["n"])
                for row in self._conn.execute(
                    "SELECT source, COUNT(*) AS n FROM memories GROUP BY source"
                ).fetchall()
            }
            top_rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY importance DESC, created_at DESC LIMIT ?",
                (top,),
            ).fetchall()
        return MemorySummary(
            total=total,
            by_kind=by_kind,
            by_source=by_source,
            top=tuple(_to_record(row) for row in top_rows),
            writes_enabled=self._writes_enabled,
        )

    # -- forget / reset -----------------------------------------------------

    def forget(self, memory_id: int) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            return cursor.rowcount

    def forget_by(
        self,
        *,
        tag: str | None = None,
        source: str | None = None,
        before: float | None = None,
        after: float | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if tag is not None:
            token = _normalize_tag(tag)
            if not token:
                raise ValueError("tag must contain at least one usable character")
            clauses.append("tags LIKE ?")
            params.append(f"%{token}%")
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if before is not None:
            clauses.append("created_at < ?")
            params.append(before)
        if after is not None:
            clauses.append("created_at > ?")
            params.append(after)
        if not clauses:
            raise ValueError("forget_by requires at least one filter")

        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM memories WHERE {' AND '.join(clauses)}", params
            )
            self._conn.commit()
            return cursor.rowcount

    def reset(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memories")
            self._conn.execute("DELETE FROM pet_state")
            if self._fts:
                self._conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
            self._conn.commit()

    # -- relationship / affect persistence ----------------------------------

    def save_affect(self, affect_row: dict[str, float], last_interaction: float) -> None:
        if not self._writes_enabled:
            return
        now = self._now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pet_state (id, affect_json, last_interaction, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    affect_json = excluded.affect_json,
                    last_interaction = excluded.last_interaction,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(affect_row), last_interaction, now),
            )
            self._conn.commit()

    def save_body(self, body_row: dict[str, object] | None) -> None:
        if not self._writes_enabled:
            return
        now = self._now()
        payload = json.dumps(body_row) if body_row is not None else None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pet_state (id, body_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    body_json = excluded.body_json,
                    updated_at = excluded.updated_at
                """,
                (payload, now),
            )
            self._conn.commit()

    def load_affect(self) -> _StoredState:
        with self._lock:
            row = self._conn.execute(
                "SELECT affect_json, last_interaction, body_json FROM pet_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return _StoredState(affect=None, last_interaction=None, body=None)
        affect = json.loads(row["affect_json"]) if row["affect_json"] else None
        last_interaction = (
            float(row["last_interaction"]) if row["last_interaction"] is not None else None
        )
        body = json.loads(row["body_json"]) if row["body_json"] else None
        return _StoredState(affect=affect, last_interaction=last_interaction, body=body)


def _score(
    *,
    valence: int,
    intensity: int,
    owner_initiated: bool,
    alert: bool,
    novelty: float,
    repetition: int,
) -> float:
    valence_mag = abs(valence) / 100.0
    components = (
        0.35 * valence_mag
        + 0.20 * (1.0 if owner_initiated else 0.0)
        + 0.20 * (1.0 if alert else 0.0)
        + 0.15 * novelty
        + 0.10 * (intensity / 100.0)
    )
    repetition_boost = min(0.10, max(0, repetition) * 0.01)
    return min(100.0, (components + repetition_boost) * 100.0)


def _to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=int(row["id"]),
        created_at=float(row["created_at"]),
        source=str(row["source"]),
        kind=str(row["kind"]),
        summary=str(row["summary"]),
        tags=_decode_tags(str(row["tags"])),
        valence=int(row["valence"]),
        intensity=int(row["intensity"]),
        importance=float(row["importance"]),
        last_recalled=float(row["last_recalled"]),
        recall_count=int(row["recall_count"]),
    )


def _clean_summary(summary: str) -> str:
    cleaned = " ".join(summary.split())
    return cleaned[:MAX_SUMMARY_LEN]


def _clamp_signed(value: int) -> int:
    return max(-100, min(100, int(value)))


def _clamp_unsigned(value: int) -> int:
    return max(0, min(100, int(value)))


def _normalize_tag(tag: str) -> str:
    return "_".join(_TOKEN_RE.findall(tag.lower()))


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    for tag in tags:
        token = _normalize_tag(tag)
        if token and token not in normalized:
            normalized.append(token)
    return normalized[:MAX_TAGS]


def _encode_tags(tags: list[str] | tuple[str, ...] | None) -> str:
    tokens = _normalize_tags(tags)
    if not tokens:
        return ""
    return " " + " ".join(tokens) + " "


def _decode_tags(text: str) -> tuple[str, ...]:
    return tuple(text.split())


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:8]
