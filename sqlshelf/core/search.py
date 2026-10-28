from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from .dialects import normalize
from .models import SearchResult

# Matches prefixes like table:Customers  col:OrderId  tag:finance  date:15/06/2026
# dialect:oracle
_PREFIX_RE = re.compile(r"(?:^|\s)(table|col|tag|date|dialect):(\S+)")

# Subqueries appended to every SELECT for the new display fields.
# GROUP_CONCAT has no defined order, and the list shows only the first table —
# the inner ORDER BY keeps that choice stable between refreshes.
_TABLES_SUBQ = (
    "COALESCE("
    "  (SELECT GROUP_CONCAT(name, ',') FROM ("
    "     SELECT qo.object_name AS name FROM query_objects qo"
    "     WHERE qo.query_id=q.id AND qo.object_type='table'"
    "     ORDER BY qo.object_name"
    "  )), '')"
)
_IS_FAV_SUBQ = (
    "CASE WHEN EXISTS"
    "  (SELECT 1 FROM favorites f WHERE f.rel_path=q.rel_path)"
    " THEN 1 ELSE 0 END"
)
# The columns after the first six of every result row, in _rows_to_results order.
_ROW_TAIL = f"q.updated_at, {_TABLES_SUBQ}, {_IS_FAV_SUBQ}, q.file_mtime, q.dialect"

# bm25() weighted per queries_fts column: title, description, body, objects.
# Unweighted, a script that repeats an alias like `p_cdc` thirty times
# outranked one named "Pedidos_CDC".
_BM25 = "bm25(queries_fts, 10.0, 4.0, 1.0, 2.0)"
# A free-text match ranks first by the column holding every searched word, in
# this order. bm25 alone cannot do it: term frequency saturates, so a body
# full of `p_cdc` still scores about as high as one word in the title.
_TIER_COLUMNS = ("title", "description")


OPERATORS = ("table", "col", "tag", "date", "dialect")


def split_tokens(text: str) -> tuple[list[tuple[str, str]], str]:
    """Split a search into its ``op:value`` tokens, in order, and the free text.

    The search field shows each token as a chip. A token is exactly what
    :func:`parse_query` filters on, so a value runs to the next whitespace —
    quotes included — and ``Table:x`` or ``xtable:y`` stay free text.
    """
    tokens = [(m.group(1), m.group(2)) for m in _PREFIX_RE.finditer(text)]
    return tokens, _PREFIX_RE.sub("", text).strip()


def join_tokens(tokens: list[tuple[str, str]], free_text: str = "") -> str:
    """The search string for *tokens* followed by *free_text*.

    The inverse of :func:`split_tokens`: the result parses to the same filters
    and free text. *free_text* is kept as typed, trailing space included.
    """
    parts = [f"{op}:{value}" for op, value in tokens]
    if free_text:
        parts.append(free_text)
    return " ".join(parts)


def parse_query(text: str) -> tuple[dict[str, list[str]], str]:
    """Split 'table:X col:Y date:DD/MM/YYYY free text' into (filters, free_text).

    Returns:
        filters: dict with keys 'table', 'col', 'tag', 'date', 'dialect', each a
            list of values.
        free_text: remaining text after stripping all prefix:value tokens.
    """
    filters: dict[str, list[str]] = {op: [] for op in OPERATORS}
    tokens, remaining = split_tokens(text)
    for key, val in tokens:
        filters[key].append(val)
    return filters, remaining


def _parse_date_filter(date_str: str) -> tuple[int, int] | None:
    """Parse DD/MM/YYYY → (start_unix, end_unix) covering the full day in local time."""
    try:
        d = datetime.strptime(date_str, "%d/%m/%Y")
        start = int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        end = int(
            d.replace(hour=23, minute=59, second=59, microsecond=999999).timestamp()
        )
        return start, end
    except ValueError:
        return None


def _dialect_value(text: str) -> str:
    """The dialect key ``dialect:text`` matches: ``plsql`` and ``PL/SQL`` both
    find Oracle. A name no dialect answers to matches nothing."""
    return normalize(text) or text.casefold()


def _build_fts_query(text: str) -> str:
    """Convert free text to an FTS5 MATCH expression (prefix-match each token).

    Each token becomes an FTS5 string literal, so operators the user happens to
    type are matched literally instead of parsed. Inside a literal the only
    special character is the double quote, which FTS5 escapes by doubling —
    without that, typing a single `"` raises "unterminated string" and the whole
    search silently returns nothing until the character is deleted.
    """
    tokens = []
    for raw in text.split():
        token = raw.replace('"', '""')
        if token.strip('"'):  # a token of nothing but quotes matches nothing
            tokens.append(f'"{token}"*')
    return " ".join(tokens)


def relevance_key(result: SearchResult) -> tuple[int, float, int]:
    """Sort key that puts the closest matches of a search first.

    Queries whose name holds every searched word come first, then those whose
    description does, then the rest, each tier by bm25 rank (lower is better).
    Without free text every rank is 0, so the most recently modified come first.
    """
    return (result.match_tier, result.rank, -result.file_mtime)


def _column_matches(conn: sqlite3.Connection, column: str, fts_expr: str) -> set[int]:
    """Ids of the queries whose *column* matches every phrase of *fts_expr*."""
    try:
        rows = conn.execute(
            "SELECT rowid FROM queries_fts WHERE queries_fts MATCH ?",
            (f"{column} : ({fts_expr})",),
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {rowid for (rowid,) in rows}


def search(conn: sqlite3.Connection, text: str) -> list[SearchResult]:
    """Execute a search query and return ranked SearchResult objects.

    Supports:
    - Empty string → all queries, alphabetical.
    - Free text → FTS5 MATCH, ordered by :func:`relevance_key`.
    - table:X, col:X, tag:X, dialect:X → relational filters.
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

    for date_str in filters["date"]:
        ts = _parse_date_filter(date_str)
        if ts is not None:
            start_ts, end_ts = ts
            where_parts.append("q.file_mtime BETWEEN ? AND ?")
            params.extend([start_ts, end_ts])

    for name in filters["dialect"]:
        where_parts.append("q.dialect = ?")
        params.append(_dialect_value(name))

    if use_fts:
        fts_expr = _build_fts_query(free_text)
        if not fts_expr:
            use_fts = False
        else:
            where_parts.append("queries_fts MATCH ?")
            params.append(fts_expr)

    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    if use_fts:
        # No snippet() here on purpose. It has to re-tokenise the whole body of
        # every matching row, and measured on a real 109-file folder it was 87
        # of the 92 ms a text search took — for a string no part of the UI ever
        # displays. SearchResult.snippet stays in the model, always empty.
        sql = f"""
            SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''),
                   '',
                   {_BM25},
                   {_ROW_TAIL}
            FROM queries q
            JOIN queries_fts ON queries_fts.rowid = q.id
            {where_clause}
            ORDER BY {_BM25}
        """
    else:
        sql = f"""
            SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''),
                   '', 0.0,
                   {_ROW_TAIL}
            FROM queries q
            {where_clause}
            ORDER BY q.file_mtime DESC, q.title
        """

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []

    results = _rows_to_results(conn, rows)
    if use_fts and results:
        tiers = [_column_matches(conn, column, fts_expr) for column in _TIER_COLUMNS]
        for result in results:
            result.match_tier = next(
                (i for i, ids in enumerate(tiers) if result.query_id in ids),
                len(tiers),
            )
        results.sort(key=relevance_key)
    return results


def _all_queries(conn: sqlite3.Connection) -> list[SearchResult]:
    rows = conn.execute(
        f"SELECT q.id, q.rel_path, q.title, COALESCE(q.description, ''), '', 0.0,"
        f" {_ROW_TAIL}"
        " FROM queries q ORDER BY q.file_mtime DESC, q.title"
    ).fetchall()
    return _rows_to_results(conn, rows)


def _rows_to_results(conn: sqlite3.Connection, rows: list[tuple]) -> list[SearchResult]:
    if not rows:
        return []

    # Batch-fetch all tags in one query to avoid N+1 per-row round-trips.
    qids = [row[0] for row in rows]
    tag_map: dict[int, list[str]] = {qid: [] for qid in qids}
    _CHUNK = 900  # stay under SQLite's default variable limit of 999
    for i in range(0, len(qids), _CHUNK):
        chunk = qids[i : i + _CHUNK]
        placeholders = ",".join("?" * len(chunk))
        for qid, tname in conn.execute(
            f"SELECT qt.query_id, t.name FROM tags t"
            f" JOIN query_tags qt ON t.id=qt.tag_id"
            f" WHERE qt.query_id IN ({placeholders})",
            chunk,
        ).fetchall():
            tag_map[qid].append(tname)

    results = []
    for row in rows:
        (
            qid,
            rel_path,
            title,
            description,
            snippet,
            rank,
            updated_at,
            tables_str,
            is_fav,
            file_mtime,
            dialect,
        ) = row
        tables = [t for t in (tables_str or "").split(",") if t]
        results.append(
            SearchResult(
                query_id=qid,
                rel_path=rel_path,
                title=title,
                description=description,
                snippet=snippet,
                rank=rank,
                tags=tag_map[qid],
                tables=tables,
                updated_at=updated_at,
                is_favorite=bool(is_fav),
                file_mtime=file_mtime or 0,
                dialect=dialect or "sql",
            )
        )
    return results
