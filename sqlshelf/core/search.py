from __future__ import annotations

import re
import sqlite3

from .models import SearchResult

# Matches prefixes like table:Customers  col:OrderId  tag:finance
_PREFIX_RE = re.compile(r"(?:^|\s)(table|col|tag):(\S+)")

# Subqueries appended to every SELECT for the new display fields
_TABLES_SUBQ = (
    "COALESCE("
    "  (SELECT GROUP_CONCAT(qo.object_name, ',')"
    "   FROM query_objects qo"
    "   WHERE qo.query_id=q.id AND qo.object_type='table'), '')"
)
_IS_FAV_SUBQ = (
    "CASE WHEN EXISTS"
    "  (SELECT 1 FROM favorites f WHERE f.rel_path=q.rel_path)"
    " THEN 1 ELSE 0 END"
)


def parse_query(text: str) -> tuple[dict[str, list[str]], str]:
    """Split 'table:X col:Y free text' into (filters, free_text).

    Returns:
        filters: dict with keys 'table', 'col', 'tag', each a list of values.
        free_text: remaining text after stripping all prefix:value tokens.
    """
    filters: dict[str, list[str]] = {"table": [], "col": [], "tag": []}
    remaining = text
    for m in _PREFIX_RE.finditer(text):
        key = m.group(1)
        val = m.group(2)
        if key in filters:
            filters[key].append(val)
    remaining = _PREFIX_RE.sub("", text).strip()
    return filters, remaining


def _build_fts_query(text: str) -> str:
    """Convert free text to an FTS5 MATCH expression (prefix-match each token)."""
    tokens = [t for t in text.split() if t]
    if not tokens:
        return ""
    return " ".join(f'"{t}"*' for t in tokens)


def search(conn: sqlite3.Connection, text: str) -> list[SearchResult]:
    """Execute a search query and return ranked SearchResult objects.

    Supports:
    - Empty string → all queries, alphabetical.
    - Free text → FTS5 MATCH with bm25 ranking.
    - table:X, col:X, tag:X → relational filters.
    - Combinations of any of the above.
    """
    text = text.strip()

    if not text:
        return _all_queries(conn)

    filters, free_text = parse_query(text)

    where_parts: list[str] = []
    params: list[object] = []
    use_fts = bool(free_text)

    for tname in filters["table"]:
        where_parts.append(
            "q.id IN ("
            "SELECT query_id FROM query_objects"
            " WHERE object_type='table' AND object_name=? COLLATE NOCASE"
            ")"
        )
        params.append(tname)

    for cname in filters["col"]:
        where_parts.append(
            "q.id IN ("
            "SELECT query_id FROM query_objects"
            " WHERE object_type='column' AND object_name=? COLLATE NOCASE"
            ")"
        )
        params.append(cname)

    for tag in filters["tag"]:
        where_parts.append(
            "q.id IN ("
            "SELECT qt.query_id FROM query_tags qt"
            " JOIN tags t ON t.id=qt.tag_id WHERE t.name=? COLLATE NOCASE"
            ")"
        )
        params.append(tag)

    if use_fts:
        fts_expr = _build_fts_query(free_text)
        if not fts_expr:
            use_fts = False
        else:
            where_parts.append("queries_fts MATCH ?")
            params.append(fts_expr)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    if use_fts:
        sql = f"""
            SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''),
                   snippet(queries_fts, 2, '[', ']', '...', 20),
                   bm25(queries_fts),
                   q.updated_at,
                   {_TABLES_SUBQ},
                   {_IS_FAV_SUBQ}
            FROM queries q
            JOIN queries_fts ON queries_fts.rowid = q.id
            {where_clause}
            ORDER BY bm25(queries_fts)
        """
    else:
        sql = f"""
            SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''),
                   '', 0.0,
                   q.updated_at,
                   {_TABLES_SUBQ},
                   {_IS_FAV_SUBQ}
            FROM queries q
            {where_clause}
            ORDER BY q.title
        """

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []

    return _rows_to_results(conn, rows)


def _all_queries(conn: sqlite3.Connection) -> list[SearchResult]:
    rows = conn.execute(
        f"SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''), '', 0.0,"
        f" q.updated_at, {_TABLES_SUBQ}, {_IS_FAV_SUBQ}"
        " FROM queries q ORDER BY q.title"
    ).fetchall()
    return _rows_to_results(conn, rows)


def _rows_to_results(
    conn: sqlite3.Connection, rows: list[tuple]
) -> list[SearchResult]:
    results = []
    for row in rows:
        qid, rel_path, title, description, snippet, rank, updated_at, tables_str, is_fav = row
        tags = [
            r[0]
            for r in conn.execute(
                "SELECT t.name FROM tags t"
                " JOIN query_tags qt ON t.id=qt.tag_id WHERE qt.query_id=?",
                (qid,),
            ).fetchall()
        ]
        tables = [t for t in (tables_str or "").split(",") if t]
        results.append(
            SearchResult(
                query_id=qid,
                rel_path=rel_path,
                title=title,
                description=description,
                snippet=snippet,
                rank=rank,
                tags=tags,
                tables=tables,
                updated_at=updated_at,
                is_favorite=bool(is_fav),
            )
        )
    return results
