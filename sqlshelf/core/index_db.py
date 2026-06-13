from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Query, SearchResult

if TYPE_CHECKING:
    pass


class IndexDB:
    """SQLite + FTS5 index for a SQLShelf project folder.

    Lives at <project_root>/.sqlshelf/index.db — fully regenerable from disk.
    Thread-safe: a single Lock serialises all DB access so the UI thread and
    the file-watcher thread can share one instance safely.
    """

    SCHEMA_VERSION = "2"

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._lock = threading.Lock()
        index_dir = project_root / ".sqlshelf"
        index_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = index_dir / "index.db"
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._apply_schema()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _apply_schema(self) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if row is None:
                self._create_fresh_schema()
            else:
                version_row = self._conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                v = version_row[0] if version_row else "1"
                if not self._is_schema_compatible():
                    # Schema is too old or corrupted — rebuild cleanly
                    self._drop_all_tables()
                    self._create_fresh_schema()
                elif v == "1":
                    self._migrate_v1_to_v2()

    def _is_schema_compatible(self) -> bool:
        """Return True if the queries table has all required columns."""
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(queries)").fetchall()
        }
        required = {
            "id", "rel_path", "title", "description", "body",
            "file_mtime", "file_size", "content_hash",
            "has_frontmatter", "created_at", "updated_at",
        }
        return required.issubset(cols)

    def _drop_all_tables(self) -> None:
        """Wipe the DB by closing, deleting the file, and reopening it.

        The index is fully regenerable from disk — this is always safe.
        Deleting the file sidesteps ordering constraints (FK, FTS, triggers).
        """
        self._conn.close()
        if self._db_path.exists():
            self._db_path.unlink()
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _create_fresh_schema(self) -> None:
        schema_sql = (
            files("sqlshelf.core").joinpath("schema.sql").read_text(encoding="utf-8")
        )
        self._conn.executescript(schema_sql)
        self._conn.execute("BEGIN")
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (self.SCHEMA_VERSION,),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('project_root', ?)",
            (str(self._project_root),),
        )
        self._conn.execute("COMMIT")

    def _migrate_v1_to_v2(self) -> None:
        """Add favorites and recently_viewed tables (v1 → v2)."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS favorites (
                rel_path TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS recently_viewed (
                rel_path  TEXT NOT NULL PRIMARY KEY,
                viewed_at TEXT NOT NULL
            );
        """)
        self._conn.execute("BEGIN")
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '2')"
        )
        self._conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Public API — bulk operations
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def index_all(self, queries: list[Query]) -> None:
        """Full reindex: delete everything and insert all given queries."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM query_objects")
                self._conn.execute("DELETE FROM query_tags")
                self._conn.execute("DELETE FROM queries")
                self._conn.execute("DELETE FROM tags")
                for query in queries:
                    self._insert_query(query)
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_full_scan', ?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def index_incremental(self, queries: list[Query], progress_cb=None) -> int:
        """Smart reindex: skip files whose mtime+hash are unchanged.

        Returns total number of files inserted, updated, or deleted.
        *progress_cb*, if provided, is called as ``progress_cb(current, total)``
        after each file is processed (safe to emit Qt signals from here).
        """
        with self._lock:
            existing: dict[str, tuple[int, str]] = {
                row[0]: (row[1], row[2])
                for row in self._conn.execute(
                    "SELECT rel_path, file_mtime, content_hash FROM queries"
                ).fetchall()
            }

            changed = 0
            current_paths: set[str] = set()
            total = len(queries)

            self._conn.execute("BEGIN")
            try:
                for i, query in enumerate(queries):
                    try:
                        rel_path = query.path.relative_to(self._project_root).as_posix()
                    except ValueError:
                        if progress_cb:
                            progress_cb(i + 1, total)
                        continue
                    current_paths.add(rel_path)

                    try:
                        stat = query.path.stat()
                    except OSError:
                        if progress_cb:
                            progress_cb(i + 1, total)
                        continue
                    file_mtime = int(stat.st_mtime)

                    if rel_path in existing:
                        stored_mtime, stored_hash = existing[rel_path]
                        if file_mtime == stored_mtime:
                            if progress_cb:
                                progress_cb(i + 1, total)
                            continue
                        content_hash = hashlib.sha256(query.path.read_bytes()).hexdigest()
                        if content_hash == stored_hash:
                            self._conn.execute(
                                "UPDATE queries SET file_mtime=? WHERE rel_path=?",
                                (file_mtime, rel_path),
                            )
                            if progress_cb:
                                progress_cb(i + 1, total)
                            continue
                        self._conn.execute("DELETE FROM queries WHERE rel_path=?", (rel_path,))

                    self._insert_query(query)
                    changed += 1
                    if progress_cb:
                        progress_cb(i + 1, total)

                deleted = set(existing.keys()) - current_paths
                for rel_path in deleted:
                    self._conn.execute("DELETE FROM queries WHERE rel_path=?", (rel_path,))
                changed += len(deleted)

                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_full_scan', ?)",
                    (datetime.now(timezone.utc).isoformat(),),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        return changed

    # ------------------------------------------------------------------
    # Public API — single-file operations (used by watcher)
    # ------------------------------------------------------------------

    def upsert_query(self, query: Query) -> None:
        """Insert or replace a single query in the index."""
        with self._lock:
            try:
                rel_path = query.path.relative_to(self._project_root).as_posix()
            except ValueError:
                return
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM queries WHERE rel_path=?", (rel_path,))
                self._insert_query(query)
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
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]

    def get_all_tags(self) -> list[str]:
        with self._lock:
            return [
                r[0]
                for r in self._conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
            ]

    def get_objects(self, query_id: int) -> dict[str, list[str]]:
        """Return {object_type: [names]} for a query."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT object_type, object_name FROM query_objects"
                " WHERE query_id=? ORDER BY object_type, object_name",
                (query_id,),
            ).fetchall()
        result: dict[str, list[str]] = {
            "table": [], "column": [], "procedure": [], "function": []
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
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM queries WHERE rel_path=?", (rel_path,)
            ).fetchone()
        return row[0] if row else None

    def search(self, text: str) -> list[SearchResult]:
        """Full-text + relational search. Returns ranked results."""
        from .search import search as _search

        with self._lock:
            return _search(self._conn, text)

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
                self._conn.execute("DELETE FROM favorites WHERE rel_path=?", (rel_path,))
                return False
            else:
                self._conn.execute(
                    "INSERT INTO favorites (rel_path) VALUES (?)", (rel_path,)
                )
                return True

    def is_favorite(self, rel_path: str) -> bool:
        with self._lock:
            return bool(
                self._conn.execute(
                    "SELECT 1 FROM favorites WHERE rel_path=?", (rel_path,)
                ).fetchone()
            )

    def get_favorites(self) -> list[SearchResult]:
        from .search import _TABLES_SUBQ, _IS_FAV_SUBQ

        with self._lock:
            rows = self._conn.execute(
                f"SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''), '', 0.0,"
                f" q.updated_at, {_TABLES_SUBQ}, {_IS_FAV_SUBQ}"
                " FROM queries q"
                " JOIN favorites f ON f.rel_path = q.rel_path"
                " ORDER BY q.title"
            ).fetchall()
            return _rows_to_results_locked(self._conn, rows)

    # ------------------------------------------------------------------
    # Public API — recently viewed
    # ------------------------------------------------------------------

    def add_recently_viewed(self, rel_path: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO recently_viewed (rel_path, viewed_at)"
                " VALUES (?, ?)",
                (rel_path, now),
            )
            self._conn.execute(
                "DELETE FROM recently_viewed WHERE rel_path NOT IN ("
                "  SELECT rel_path FROM recently_viewed ORDER BY viewed_at DESC LIMIT 20"
                ")"
            )

    def get_recently_viewed(self, limit: int = 20) -> list[SearchResult]:
        from .search import _TABLES_SUBQ, _IS_FAV_SUBQ

        with self._lock:
            rows = self._conn.execute(
                f"SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''), '', 0.0,"
                f" q.updated_at, {_TABLES_SUBQ}, {_IS_FAV_SUBQ}"
                " FROM queries q"
                " JOIN recently_viewed rv ON rv.rel_path = q.rel_path"
                " ORDER BY rv.viewed_at DESC"
                " LIMIT ?",
                (limit,),
            ).fetchall()
            return _rows_to_results_locked(self._conn, rows)

    # ------------------------------------------------------------------
    # Internal helpers (called while _lock is held)
    # ------------------------------------------------------------------

    def _insert_query(self, query: Query) -> None:
        from .sql_objects import extract_objects, objects_to_text

        rel_path = query.path.relative_to(self._project_root).as_posix()
        stat = query.path.stat()
        file_mtime = int(stat.st_mtime)
        file_size = stat.st_size
        content_hash = hashlib.sha256(query.path.read_bytes()).hexdigest()

        cursor = self._conn.execute(
            """
            INSERT INTO queries
                (rel_path, title, description, body,
                 file_mtime, file_size, content_hash,
                 has_frontmatter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rel_path,
                query.title,
                query.description,
                query.body,
                file_mtime,
                file_size,
                content_hash,
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

        objects = extract_objects(query.body)
        all_names: list[str] = []
        for obj_type, names in objects.items():
            for name in sorted(names):
                if name:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO query_objects"
                        " (query_id, object_type, object_name) VALUES (?, ?, ?)",
                        (query_id, obj_type, name),
                    )
                    all_names.append(name)

        if all_names:
            objects_text = objects_to_text(objects)
            self._conn.execute("DELETE FROM queries_fts WHERE rowid=?", (query_id,))
            self._conn.execute(
                "INSERT INTO queries_fts(rowid, title, description, body, objects)"
                " VALUES (?, ?, ?, ?, ?)",
                (query_id, query.title, query.description, query.body, objects_text),
            )


def _rows_to_results_locked(
    conn: sqlite3.Connection, rows: list[tuple]
) -> list[SearchResult]:
    """Build SearchResult list from raw rows (caller must hold the DB lock)."""
    from .search import _rows_to_results

    return _rows_to_results(conn, rows)
