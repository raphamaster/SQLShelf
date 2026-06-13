from __future__ import annotations

import contextlib
import io
import sqlglot
from sqlglot import exp


def extract_objects(sql_body: str, dialect: str = "tsql") -> dict[str, set[str]]:
    """Extract named database objects from a SQL body using sqlglot.

    Returns a dict with keys 'table', 'column', 'procedure', 'function'.
    CTEs are excluded from the table set.
    On any parse error, returns empty sets — never raises.
    """
    empty: dict[str, set[str]] = {
        "table": set(),
        "column": set(),
        "procedure": set(),
        "function": set(),
    }
    if not sql_body or not sql_body.strip():
        return empty

    try:
        # sqlglot prints fallback warnings to stderr even with IGNORE level; suppress them.
        with contextlib.redirect_stderr(io.StringIO()):
            tree = sqlglot.parse_one(
                sql_body, dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE
            )
    except Exception:
        return empty

    if tree is None:
        return empty

    tables: set[str] = {t.name for t in tree.find_all(exp.Table) if t.name}
    columns: set[str] = {c.name for c in tree.find_all(exp.Column) if c.name}
    ctes: set[str] = {c.alias for c in tree.find_all(exp.CTE) if c.alias}
    tables -= ctes

    return {"table": tables, "column": columns, "procedure": set(), "function": set()}


def objects_to_text(objects: dict[str, set[str]]) -> str:
    """Flatten all object names to a single space-separated string for FTS indexing."""
    all_names: list[str] = []
    for names in objects.values():
        all_names.extend(sorted(names))
    return " ".join(all_names)
