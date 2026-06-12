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

    SCHEMA_VERSION = "1"

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
            if row is not None:
                return
            schema_sql = (
                files("sqlshelf.core").joinpath("schema.sql").read_text(encoding="utf-8")
            )
            self._conn.executescript(schema_sql)
            self._conn.execute("BEGIN")
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (self.SCHEMA_VERSION,),
            )
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('project_root', ?)",
                (str(self._project_root),),
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

    def index_incremental(self, queries: list[Query]) -> int:
        """Smart reindex: skip files whose mtime+hash are unchanged.

        Returns total number of files inserted, updated, or deleted.
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

            self._conn.execute("BEGIN")
            try:
                for query in queries:
                    try:
                        rel_path = query.path.relative_to(self._project_root).as_posix()
                    except ValueError:
                        continue
                    current_paths.add(rel_path)

                    try:
                        stat = query.path.stat()
                    except OSError:
                        continue
                    file_mtime = int(stat.st_mtime)

                    if rel_path in existing:
                        stored_mtime, stored_hash = existing[rel_path]
                        if file_mtime == stored_mtime:
                            continue
                        content_hash = hashlib.sha256(query.path.read_bytes()).hexdigest()
                        if content_hash == stored_hash:
                            self._conn.execute(
                                "UPDATE queries SET file_mtime=? WHERE rel_path=?",
                                (file_mtime, rel_path),
                            )
                            continue
                        self._conn.execute("DELETE FROM queries WHERE rel_path=?", (rel_path,))

                    self._insert_query(query)
                    changed += 1

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
