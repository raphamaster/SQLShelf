from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sqlshelf.core.index_db import IndexDB
from sqlshelf.core.models import Query
from sqlshelf.core.scanner import scan_folder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sql_file(folder: Path, name: str, content: str = "SELECT 1") -> Path:
    p = folder / name
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# Schema / initialisation
# ---------------------------------------------------------------------------


class TestSchemaInit:
    def test_creates_db_file(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        db.close()
        assert (project_dir / ".sqlshelf" / "index.db").exists()

    def test_schema_version_stored(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        row = db._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        db.close()
        assert row is not None and row[0] == IndexDB.SCHEMA_VERSION

    def test_all_tables_exist(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        names = {
            r[0]
            for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        db.close()
        assert {"meta", "queries", "tags", "query_tags", "query_objects"}.issubset(names)

    def test_fts_virtual_table_exists(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        row = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'queries_fts'"
        ).fetchone()
        db.close()
        assert row is not None

    def test_project_root_in_meta(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        row = db._conn.execute(
            "SELECT value FROM meta WHERE key = 'project_root'"
        ).fetchone()
        db.close()
        assert row is not None and row[0] == str(project_dir)

    def test_idempotent_reopen(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        db.close()
        db2 = IndexDB(project_dir)  # must not raise or reapply schema
        db2.close()


# ---------------------------------------------------------------------------
# index_all — basic count and data
# ---------------------------------------------------------------------------


class TestIndexAll:
    def test_empty_list_gives_zero_count(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        db.index_all([])
        assert db.count() == 0
        db.close()

    def test_single_query_stored(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "a.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="A", body="SELECT 1", has_frontmatter=False)])
        assert db.count() == 1
        db.close()

    def test_multiple_queries(self, project_dir: Path) -> None:
        queries = [
            Query(path=make_sql_file(project_dir, f"q{i}.sql", f"SELECT {i}"), title=f"Q{i}", body=f"SELECT {i}")
            for i in range(5)
        ]
        db = IndexDB(project_dir)
        db.index_all(queries)
        assert db.count() == 5
        db.close()

    def test_all_fields_persisted(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql", "SELECT x FROM t")
        q = Query(
            path=p,
            title="My Title",
            description="My description",
            body="SELECT x FROM t",
            has_frontmatter=True,
            created_at="2026-01-01",
            updated_at="2026-06-11",
        )
        db = IndexDB(project_dir)
        db.index_all([q])
        row = db._conn.execute(
            "SELECT title, description, has_frontmatter, created_at, updated_at FROM queries"
        ).fetchone()
        db.close()
        assert row == ("My Title", "My description", 1, "2026-01-01", "2026-06-11")

    def test_file_stat_and_hash_stored(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql", "SELECT 42")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 42")])
        row = db._conn.execute(
            "SELECT file_mtime, file_size, content_hash FROM queries"
        ).fetchone()
        db.close()
        stat = p.stat()
        assert row[0] == int(stat.st_mtime)
        assert row[1] == stat.st_size
        assert row[2] == hashlib.sha256(p.read_bytes()).hexdigest()

    def test_rel_path_uses_posix_separators(self, project_dir: Path) -> None:
        sub = project_dir / "sub"
        sub.mkdir()
        p = make_sql_file(sub, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 1")])
        row = db._conn.execute("SELECT rel_path FROM queries").fetchone()
        db.close()
        assert row[0] == "sub/q.sql"
        assert "\\" not in row[0]


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTags:
    def test_tags_indexed(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 1", tags=["finance", "report"])])
        assert sorted(db.get_all_tags()) == ["finance", "report"]
        db.close()

    def test_tags_normalised_to_lowercase(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 1", tags=["Finance", "REPORT"])])
        assert sorted(db.get_all_tags()) == ["finance", "report"]
        db.close()

    def test_query_tags_join_table_populated(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 1", tags=["a", "b", "c"])])
        count = db._conn.execute("SELECT COUNT(*) FROM query_tags").fetchone()[0]
        db.close()
        assert count == 3

    def test_shared_tag_stored_once(self, project_dir: Path) -> None:
        q1 = Query(path=make_sql_file(project_dir, "q1.sql", "SELECT 1"), title="Q1", body="SELECT 1", tags=["shared", "only-q1"])
        q2 = Query(path=make_sql_file(project_dir, "q2.sql", "SELECT 2"), title="Q2", body="SELECT 2", tags=["shared", "only-q2"])
        db = IndexDB(project_dir)
        db.index_all([q1, q2])
        tag_count = db._conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        link_count = db._conn.execute("SELECT COUNT(*) FROM query_tags").fetchone()[0]
        db.close()
        assert tag_count == 3   # shared, only-q1, only-q2
        assert link_count == 4  # 2 per query

    def test_no_tags_leaves_tables_empty(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 1", tags=[])])
        assert db.get_all_tags() == []
        assert db._conn.execute("SELECT COUNT(*) FROM query_tags").fetchone()[0] == 0
        db.close()


# ---------------------------------------------------------------------------
# Reindex idempotency
# ---------------------------------------------------------------------------


class TestReindex:
    def test_reindex_replaces_all_data(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="Old", body="SELECT 1", tags=["old"])])
        db.index_all([Query(path=p, title="New", body="SELECT 2", tags=["new"])])
        assert db.count() == 1
        assert db._conn.execute("SELECT title FROM queries").fetchone()[0] == "New"
        assert db.get_all_tags() == ["new"]
        db.close()

    def test_reindex_clears_orphan_tags(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="T", body="SELECT 1", tags=["stale"])])
        db.index_all([Query(path=p, title="T", body="SELECT 1", tags=[])])
        assert db.get_all_tags() == []
        db.close()

    def test_last_full_scan_recorded(self, project_dir: Path) -> None:
        db = IndexDB(project_dir)
        db.index_all([])
        row = db._conn.execute(
            "SELECT value FROM meta WHERE key = 'last_full_scan'"
        ).fetchone()
        db.close()
        assert row is not None and row[0]  # non-empty ISO timestamp


# ---------------------------------------------------------------------------
# FTS5
# ---------------------------------------------------------------------------


class TestFTS:
    def test_fts_row_created_on_insert(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql", "SELECT amount FROM invoices")
        db = IndexDB(project_dir)
        db.index_all([Query(path=p, title="Invoices", body="SELECT amount FROM invoices")])
        count = db._conn.execute("SELECT COUNT(*) FROM queries_fts").fetchone()[0]
        db.close()
        assert count == 1

    def test_fts_count_matches_queries_count(self, project_dir: Path) -> None:
        queries = [
            Query(path=make_sql_file(project_dir, f"q{i}.sql", f"SELECT {i}"), title=f"Q{i}", body=f"SELECT {i}")
            for i in range(3)
        ]
        db = IndexDB(project_dir)
        db.index_all(queries)
        fts_count = db._conn.execute("SELECT COUNT(*) FROM queries_fts").fetchone()[0]
        db.close()
        assert fts_count == 3

    def test_fts_stays_consistent_after_reindex(self, project_dir: Path) -> None:
        p = make_sql_file(project_dir, "q.sql")
        q = Query(path=p, title="T", body="SELECT 1")
        db = IndexDB(project_dir)
        db.index_all([q])
        db.index_all([q])  # second full reindex
        count = db._conn.execute("SELECT COUNT(*) FROM queries_fts").fetchone()[0]
        db.close()
        assert count == 1


# ---------------------------------------------------------------------------
# Integration: scanner -> index_all
# ---------------------------------------------------------------------------


class TestScannerIntegration:
    def test_full_pipeline(self, project_dir: Path) -> None:
        make_sql_file(
            project_dir,
            "invoice.sql",
            "/* ---\ntitle: Invoice Query\ntags: [finance, report]\ncreated: 2026-01-01\n--- */\nSELECT 1",
        )
        queries = scan_folder(project_dir)
        db = IndexDB(project_dir)
        db.index_all(queries)
        assert db.count() == 1
        assert sorted(db.get_all_tags()) == ["finance", "report"]
        row = db._conn.execute("SELECT created_at FROM queries").fetchone()
        db.close()
        assert row[0] == "2026-01-01"

    def test_sqlshelf_dir_excluded_from_scan(self, project_dir: Path) -> None:
        make_sql_file(project_dir, "real.sql")
        sqlshelf_dir = project_dir / ".sqlshelf"
        sqlshelf_dir.mkdir()
        make_sql_file(sqlshelf_dir, "should_be_ignored.sql")
        queries = scan_folder(project_dir)
        assert len(queries) == 1
        assert queries[0].title == "real"

    def test_query_without_frontmatter_indexed(self, project_dir: Path) -> None:
        make_sql_file(project_dir, "plain.sql", "SELECT top 10 * FROM dbo.Orders")
        queries = scan_folder(project_dir)
        db = IndexDB(project_dir)
        db.index_all(queries)
        row = db._conn.execute("SELECT title, has_frontmatter FROM queries").fetchone()
        db.close()
        assert row[0] == "plain"
        assert row[1] == 0
