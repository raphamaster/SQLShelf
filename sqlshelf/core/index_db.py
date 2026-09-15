from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from .models import AccessStat, Query, SearchResult

# Caps on one write transaction while (re)indexing. The lock is released between
# batches so a search or a selection never waits for a whole folder to finish.
#
# A row count alone is not enough: indexing a folder of large scripts, 100 rows
# of FTS text took over a second to commit. Whichever cap is reached first ends
# the batch, which keeps the worst-case wait bounded regardless of file size.
# Measured on a real 109-file folder on synced storage: 20 rows per batch holds
# the lock for a median of 17 ms, against 500 ms for the whole folder at once.
_WRITE_BATCH = 20
_BATCH_BUDGET_MS = 40.0


@dataclass
class IndexStats:
    """What one project index holds. Returned by IndexDB.get_stats().

    A dataclass rather than a dict so callers that add or count these fields
    are checked; the dict this replaced was typed dict[str, object], which made
    every len() and += on its values invisible to the type checker.
    """

    queries: int
    favorites: int
    tag_names: set[str]
    table_names: set[str]
    column_names: set[str]

    def merge(self, other: "IndexStats") -> None:
        """Fold *other* into this one, for totals across several folders."""
        self.queries += other.queries
        self.favorites += other.favorites
        self.tag_names |= other.tag_names
        self.table_names |= other.table_names
        self.column_names |= other.column_names

    @classmethod
    def empty(cls) -> "IndexStats":
        return cls(
            queries=0,
            favorites=0,
            tag_names=set(),
            table_names=set(),
            column_names=set(),
        )


@dataclass
class _PreparedQuery:
    """A Query with everything expensive already computed off the DB lock.

    Hashing and sqlglot parsing dominate indexing time; doing them while the
    lock is held froze every other thread — including the GUI — for seconds.
    """

    query: Query
    rel_path: str
    file_mtime: int
    file_size: int
    content_hash: str
    objects: dict[str, set[str]] = field(default_factory=dict)


class IndexDB:
    """SQLite + FTS5 index for a SQLShelf project folder.

    Lives at <project_root>/.sqlshelf/index.db — fully regenerable from disk.

    Thread-safe, and built so the UI never waits on the indexer:

    * Two connections. Writes go through ``_conn`` under ``_lock``; reads go
      through ``_read_conn`` under ``_read_lock``. In WAL mode a reader on its
      own connection is not blocked by an open write transaction.
    * Neither lock ever covers file I/O, hashing or SQL parsing — see
      ``_prepare`` — and writes are committed in small batches.
    """

    SCHEMA_VERSION = "5"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._lock = threading.Lock()
        index_dir = project_root / ".sqlshelf"
        index_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = index_dir / "index.db"
        self._conn = self._connect()
        self._apply_schema()

        # Readers get their own connection. WAL allows a reader to work while a
        # write transaction is open, but only across connections — sharing one
        # connection (and one lock) made every search queue behind the whole
        # reindex, which is what made the window stop responding.
        self._read_lock = threading.Lock()
        self._read_conn = self._connect()
        # If WAL was refused (a network share, say) the reader falls back to
        # waiting for the writer instead of failing with "database is locked".
        self._read_conn.execute("PRAGMA busy_timeout = 5000")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error:
            pass  # a folder on a network share may refuse WAL; plain mode is fine
        return conn

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _apply_schema(self) -> None:
        """Create the schema, or rebuild it whenever the version moved on.

        The index holds nothing that is not derivable from the .sql files, so
        "delete and reindex" is the migration strategy for every schema change.
        It also clears data produced by older, buggier extraction passes.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if row is None:
                self._create_fresh_schema()
                return

            version_row = self._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            version = version_row[0] if version_row else ""
            if version != self.SCHEMA_VERSION or not self._is_schema_compatible():
                self._drop_all_tables()
                self._create_fresh_schema()

    def _is_schema_compatible(self) -> bool:
        """Return True if the queries table has all required columns."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(queries)").fetchall()
        }
        required = {
            "id",
            "rel_path",
            "title",
            "description",
            "body",
            "file_mtime",
            "file_size",
            "content_hash",
            "has_frontmatter",
            "created_at",
            "updated_at",
        }
        return required.issubset(cols)

    def _drop_all_tables(self) -> None:
        """Wipe the DB by closing, deleting the file, and reopening it.

        The index is fully regenerable from disk — this is always safe.
        Deleting the file sidesteps ordering constraints (FK, FTS, triggers).
        """
        self._conn.close()
        for suffix in ("", "-wal", "-shm"):
            stale = self._db_path.with_name(self._db_path.name + suffix)
            if stale.exists():
                try:
                    stale.unlink()
                except OSError:
                    pass
        self._conn = self._connect()

    def _create_fresh_schema(self) -> None:
        schema_sql = (
            files("sqlshelf.core").joinpath("schema.sql").read_text(encoding="utf-8")
        )
        self._conn.executescript(schema_sql)
        self._conn.execute("BEGIN")
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (self.SCHEMA_VERSION,),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('project_root', ?)",
            (str(self._project_root),),
        )
        self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Public API — bulk operations
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._read_lock:
            self._read_conn.close()
        with self._lock:
            self._conn.close()

    def index_all(self, queries: list[Query], progress_cb=None) -> None:
        """Full reindex: delete everything and insert all given queries.

        *progress_cb*, if provided, is called as ``progress_cb(current, total)``
        as each file is prepared.
        """
        prepared = self._prepare_all(queries, progress_cb)

        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM query_objects")
                self._conn.execute("DELETE FROM query_tags")
                self._conn.execute("DELETE FROM queries")
                self._conn.execute("DELETE FROM tags")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        self._write_batches(prepared)
        self._stamp_last_scan()

    def index_incremental(self, queries: list[Query], progress_cb=None) -> int:
        """Smart reindex: skip files whose mtime+hash are unchanged.

        Returns total number of files inserted, updated, or deleted.
        *progress_cb*, if provided, is called as ``progress_cb(current, total)``
        after each file is processed (safe to emit Qt signals from here).

        Only the writes take the lock, in small batches. Stat calls, hashing and
        SQL parsing all happen outside it, so reindexing a large folder never
        blocks a concurrent search or selection.
        """
        with self._lock:
            existing: dict[str, tuple[int, str]] = {
                row[0]: (row[1], row[2])
                for row in self._conn.execute(
                    "SELECT rel_path, file_mtime, content_hash FROM queries"
                ).fetchall()
            }

        total = len(queries)
        current_paths: set[str] = set()
        to_insert: list[_PreparedQuery] = []
        to_touch: list[tuple[int, str]] = []

        for i, query in enumerate(queries, 1):
            try:
                rel_path = query.path.relative_to(self._project_root).as_posix()
            except ValueError:
                if progress_cb:
                    progress_cb(i, total)
                continue
            current_paths.add(rel_path)

            try:
                file_mtime = int(query.path.stat().st_mtime)
            except OSError:
                if progress_cb:
                    progress_cb(i, total)
                continue

            if rel_path in existing:
                stored_mtime, stored_hash = existing[rel_path]
                if file_mtime == stored_mtime:
                    if progress_cb:
                        progress_cb(i, total)
                    continue
                try:
                    content_hash = hashlib.sha256(query.path.read_bytes()).hexdigest()
                except OSError:
                    if progress_cb:
                        progress_cb(i, total)
                    continue
                if content_hash == stored_hash:
                    # Same bytes, new timestamp — a cloud-sync touch. Record the
                    # mtime and leave the indexed content (and its FTS row) alone.
                    to_touch.append((file_mtime, rel_path))
                    if progress_cb:
                        progress_cb(i, total)
                    continue

            prepared = self._prepare(query)
            if prepared is not None:
                to_insert.append(prepared)
            if progress_cb:
                progress_cb(i, total)

        to_delete = sorted(set(existing) - current_paths)

        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for file_mtime, rel_path in to_touch:
                    self._conn.execute(
                        "UPDATE queries SET file_mtime=? WHERE rel_path=?",
                        (file_mtime, rel_path),
                    )
                for rel_path in to_delete:
                    self._conn.execute(
                        "DELETE FROM queries WHERE rel_path=?", (rel_path,)
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        self._write_batches(to_insert, replace=True)
        self._stamp_last_scan()

        return len(to_insert) + len(to_delete)

    # ------------------------------------------------------------------
    # Indexing internals
    # ------------------------------------------------------------------

    def _prepare(self, query: Query) -> _PreparedQuery | None:
        """Do every expensive part of indexing one file, with no lock held."""
        from .sql_objects import extract_objects

        try:
            rel_path = query.path.relative_to(self._project_root).as_posix()
        except ValueError:
            return None
        try:
            stat = query.path.stat()
            content_hash = hashlib.sha256(query.path.read_bytes()).hexdigest()
        except OSError:
            return None

        return _PreparedQuery(
            query=query,
            rel_path=rel_path,
            file_mtime=int(stat.st_mtime),
            file_size=stat.st_size,
            content_hash=content_hash,
            objects=extract_objects(query.body),
        )

    def _prepare_all(
        self, queries: list[Query], progress_cb=None
    ) -> list[_PreparedQuery]:
        prepared: list[_PreparedQuery] = []
        total = len(queries)
        for i, query in enumerate(queries, 1):
            item = self._prepare(query)
            if item is not None:
                prepared.append(item)
            if progress_cb is not None:
                progress_cb(i, total)
        return prepared

    def _write_batches(
        self, prepared: list[_PreparedQuery], replace: bool = False
    ) -> None:
        """Insert prepared rows, releasing the lock between batches."""
        pos = 0
        total = len(prepared)
        while pos < total:
            with self._lock:
                deadline = time.monotonic() + _BATCH_BUDGET_MS / 1000.0
                self._conn.execute("BEGIN")
                try:
                    written = 0
                    while pos < total and written < _WRITE_BATCH:
                        item = prepared[pos]
                        if replace:
                            self._conn.execute(
                                "DELETE FROM queries WHERE rel_path=?", (item.rel_path,)
                            )
                        self._insert_prepared(item)
                        pos += 1
                        written += 1
                        if time.monotonic() >= deadline:
                            break
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            # Hand the lock to any other writer before claiming it again. A
            # bare yield is not enough on Windows: measured against a competing
            # thread, time.sleep(0) let the indexer re-acquire immediately and
            # starve it for half a second at a stretch.
            time.sleep(0.001)

    def _stamp_last_scan(self) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_full_scan', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )

    # ------------------------------------------------------------------
    # Public API — single-file operations (used by watcher)
    # ------------------------------------------------------------------

    def upsert_query(self, query: Query) -> None:
        """Insert or replace a single query in the index."""
        prepared = self._prepare(query)
        if prepared is None:
            return
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "DELETE FROM queries WHERE rel_path=?", (prepared.rel_path,)
                )
                self._insert_prepared(prepared)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def remove_file(self, path: Path) -> None:
        """Remove a file from the index by absolute path."""
        with self._lock:
            try:
                rel_path = path.relative_to(self._project_root).as_posix()
            except ValueError:
                return
            self._conn.execute("DELETE FROM queries WHERE rel_path=?", (rel_path,))

    # ------------------------------------------------------------------
    # Public API — queries
    # ------------------------------------------------------------------

    def count(self) -> int:
        with self._read_lock:
            return self._read_conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]

    def get_all_tags(self) -> list[str]:
        with self._read_lock:
            return [
                r[0]
                for r in self._read_conn.execute(
                    "SELECT name FROM tags ORDER BY name"
                ).fetchall()
            ]

    def get_stats(self) -> IndexStats:
        """Return aggregated statistics for this project index."""
        with self._read_lock:
            queries = self._read_conn.execute(
                "SELECT COUNT(*) FROM queries"
            ).fetchone()[0]
            tag_names = {
                r[0]
                for r in self._read_conn.execute("SELECT name FROM tags").fetchall()
            }
            favorites = self._read_conn.execute(
                "SELECT COUNT(*) FROM favorites"
            ).fetchone()[0]
            table_names = {
                r[0]
                for r in self._read_conn.execute(
                    "SELECT DISTINCT object_name FROM query_objects WHERE object_type='table'"
                ).fetchall()
            }
            column_names = {
                r[0]
                for r in self._read_conn.execute(
                    "SELECT DISTINCT object_name FROM query_objects WHERE object_type='column'"
                ).fetchall()
            }
        return IndexStats(
            queries=queries,
            tag_names=tag_names,
            favorites=favorites,
            table_names=table_names,
            column_names=column_names,
        )

    def get_objects(self, query_id: int) -> dict[str, list[str]]:
        """Return {object_type: [names]} for a query."""
        with self._read_lock:
            rows = self._read_conn.execute(
                "SELECT object_type, object_name FROM query_objects"
                " WHERE query_id=? ORDER BY object_type, object_name",
                (query_id,),
            ).fetchall()
        result: dict[str, list[str]] = {
            "table": [],
            "column": [],
            "procedure": [],
            "function": [],
            "alias": [],
        }
        for obj_type, obj_name in rows:
            if obj_type in result:
                result[obj_type].append(obj_name)
        return result

    def get_query_id(self, path: Path) -> int | None:
        """Return the index id for an absolute file path, or None."""
        try:
            rel_path = path.relative_to(self._project_root).as_posix()
        except ValueError:
            return None
        with self._read_lock:
            row = self._read_conn.execute(
                "SELECT id FROM queries WHERE rel_path=?", (rel_path,)
            ).fetchone()
        return row[0] if row else None

    def search(self, text: str) -> list[SearchResult]:
        """Full-text + relational search. Returns ranked results."""
        from .search import search as _search

        with self._read_lock:
            return _search(self._read_conn, text)

    # ------------------------------------------------------------------
    # Public API — favorites
    # ------------------------------------------------------------------

    def toggle_favorite(self, rel_path: str) -> bool:
        """Toggle favorite. Returns True if the query is now favorited."""
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM favorites WHERE rel_path=?", (rel_path,)
            ).fetchone()
            if exists:
                self._conn.execute(
                    "DELETE FROM favorites WHERE rel_path=?", (rel_path,)
                )
                return False
            else:
                self._conn.execute(
                    "INSERT INTO favorites (rel_path) VALUES (?)", (rel_path,)
                )
                return True

    def is_favorite(self, rel_path: str) -> bool:
        with self._read_lock:
            return bool(
                self._read_conn.execute(
                    "SELECT 1 FROM favorites WHERE rel_path=?", (rel_path,)
                ).fetchone()
            )

    def get_favorites(self) -> list[SearchResult]:
        from .search import _IS_FAV_SUBQ, _TABLES_SUBQ

        with self._read_lock:
            rows = self._read_conn.execute(
                f"SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''), '', 0.0,"
                f" q.updated_at, {_TABLES_SUBQ}, {_IS_FAV_SUBQ}, q.file_mtime"
                " FROM queries q"
                " JOIN favorites f ON f.rel_path = q.rel_path"
                " ORDER BY q.title"
            ).fetchall()
            return _rows_to_results_locked(self._read_conn, rows)

    # ------------------------------------------------------------------
    # Public API — recently viewed
    # ------------------------------------------------------------------

    def add_recently_viewed(self, rel_path: str) -> None:
        # The system clock is coarse enough (~16 ms on Windows) that two views in
        # quick succession can share a timestamp, which left their order up to
        # SQLite. REPLACE always assigns a fresh, higher rowid, so it breaks the
        # tie in insertion order.
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO recently_viewed (rel_path, viewed_at)"
                " VALUES (?, ?)",
                (rel_path, now),
            )
            self._conn.execute(
                "DELETE FROM recently_viewed WHERE rel_path NOT IN ("
                "  SELECT rel_path FROM recently_viewed"
                "  ORDER BY viewed_at DESC, rowid DESC LIMIT 20"
                ")"
            )

    def get_recently_viewed(self, limit: int = 20) -> list[SearchResult]:
        from .search import _IS_FAV_SUBQ, _TABLES_SUBQ

        with self._read_lock:
            rows = self._read_conn.execute(
                f"SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''), '', 0.0,"
                f" q.updated_at, {_TABLES_SUBQ}, {_IS_FAV_SUBQ}, q.file_mtime"
                " FROM queries q"
                " JOIN recently_viewed rv ON rv.rel_path = q.rel_path"
                " ORDER BY rv.viewed_at DESC, rv.rowid DESC"
                " LIMIT ?",
                (limit,),
            ).fetchall()
            return _rows_to_results_locked(self._read_conn, rows)

    # ------------------------------------------------------------------
    # Public API — access log / reports
    # ------------------------------------------------------------------

    def record_access(self, rel_path: str, action: str) -> None:
        """Log an access event for reporting (action: open/copy/open_in_ssms)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO access_log (rel_path, action, accessed_at) VALUES (?, ?, ?)",
                (rel_path, action, now),
            )

    def get_access_total(self) -> int:
        with self._read_lock:
            return self._read_conn.execute(
                "SELECT COUNT(*) FROM access_log"
            ).fetchone()[0]

    def get_top_queries(self, limit: int = 10) -> list[AccessStat]:
        """Return the most-accessed queries (all action types), ranked desc."""
        with self._read_lock:
            rows = self._read_conn.execute(
                "SELECT q.rel_path, q.title, COUNT(*) AS cnt"
                " FROM access_log a"
                " JOIN queries q ON q.rel_path = a.rel_path"
                " GROUP BY a.rel_path"
                " ORDER BY cnt DESC, q.title"
                " LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AccessStat(label=title, count=cnt, rel_path=rel_path)
            for rel_path, title, cnt in rows
        ]

    def get_top_tags(self, limit: int = 10) -> list[AccessStat]:
        """Return the most-accessed tags, ranked by total accesses of their queries."""
        with self._read_lock:
            rows = self._read_conn.execute(
                "SELECT t.name, COUNT(*) AS cnt"
                " FROM access_log a"
                " JOIN queries q ON q.rel_path = a.rel_path"
                " JOIN query_tags qt ON qt.query_id = q.id"
                " JOIN tags t ON t.id = qt.tag_id"
                " GROUP BY t.name"
                " ORDER BY cnt DESC, t.name"
                " LIMIT ?",
                (limit,),
            ).fetchall()
        return [AccessStat(label=name, count=cnt) for name, cnt in rows]

    # ------------------------------------------------------------------
    # Internal helpers (called while _lock is held)
    # ------------------------------------------------------------------

    def _insert_prepared(self, item: _PreparedQuery) -> None:
        """Write one prepared row. Caller holds the lock and an open transaction.

        Everything costly (stat, hashing, sqlglot) already happened in _prepare,
        so this is pure SQL and finishes in well under a millisecond.
        """
        from .sql_objects import objects_to_text

        query = item.query
        cursor = self._conn.execute(
            """
            INSERT INTO queries
                (rel_path, title, description, body,
                 file_mtime, file_size, content_hash,
                 has_frontmatter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.rel_path,
                query.title,
                query.description,
                query.body,
                item.file_mtime,
                item.file_size,
                item.content_hash,
                int(query.has_frontmatter),
                query.created_at,
                query.updated_at,
            ),
        )
        query_id: int = cursor.lastrowid  # type: ignore[assignment]

        for raw_tag in query.tags:
            tag = raw_tag.strip().lower()
            if not tag:
                continue
            self._conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            tag_id: int = self._conn.execute(
                "SELECT id FROM tags WHERE name=?", (tag,)
            ).fetchone()[0]
            self._conn.execute(
                "INSERT OR IGNORE INTO query_tags (query_id, tag_id) VALUES (?, ?)",
                (query_id, tag_id),
            )

        for obj_type, names in item.objects.items():
            for name in sorted(names):
                if name:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO query_objects"
                        " (query_id, object_type, object_name) VALUES (?, ?, ?)",
                        (query_id, obj_type, name),
                    )

        # Always write the FTS row, even when no object name was extracted:
        # skipping it used to make unparseable files invisible to free-text
        # search entirely, which is how search "stopped working" for whole
        # folders of scripts that sqlglot cannot parse.
        self._conn.execute("DELETE FROM queries_fts WHERE rowid=?", (query_id,))
        self._conn.execute(
            "INSERT INTO queries_fts(rowid, title, description, body, objects)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                query_id,
                query.title,
                query.description,
                query.body,
                objects_to_text(item.objects),
            ),
        )


def _rows_to_results_locked(
    conn: sqlite3.Connection, rows: list[tuple]
) -> list[SearchResult]:
    """Build SearchResult list from raw rows (caller must hold the DB lock)."""
    from .search import _rows_to_results

    return _rows_to_results(conn, rows)
