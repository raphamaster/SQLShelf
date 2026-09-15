from __future__ import annotations

import contextlib
import io
import re

import sqlglot
from sqlglot import exp

OBJECT_TYPES = ("table", "column", "alias", "procedure", "function")

# A name that survived parsing but is clearly parser debris (a stray backtick,
# an unbalanced quote, a fragment of punctuation) must never reach the index.
# Quoted identifiers may legitimately contain spaces ("[Order Details]"), so the
# rule is: no SQL punctuation, and at least one alphanumeric character.
_JUNK_CHARS_RE = re.compile(r"[`'\"();,\[\]]")
_HAS_WORD_RE = re.compile(r"\w")
_MAX_NAME_LEN = 128


def _empty() -> dict[str, set[str]]:
    return {t: set() for t in OBJECT_TYPES}


def _is_valid_name(name: str) -> bool:
    if not name or len(name) > _MAX_NAME_LEN:
        return False
    if name != name.strip():
        return False
    if _JUNK_CHARS_RE.search(name):
        return False
    return bool(_HAS_WORD_RE.search(name))


def _parse(sql_body: str, dialect: str, strict: bool):
    """Parse *sql_body*, returning the tree or None. Never raises."""
    level = sqlglot.ErrorLevel.RAISE if strict else sqlglot.ErrorLevel.IGNORE
    try:
        # sqlglot prints fallback warnings to stderr even with IGNORE level.
        with contextlib.redirect_stderr(io.StringIO()):
            return sqlglot.parse_one(sql_body, dialect=dialect, error_level=level)
    except Exception:
        return None


def _pick_dialect(sql_body: str, default: str) -> str:
    """Backticks mean BigQuery-style identifiers, which T-SQL cannot tokenise:
    parsing ``FROM `proj.dataset.tbl` u`` as tsql yields a table named '`'.
    Everything else stays on the configured dialect — BigQuery is a poor
    stand-in for T-SQL and silently drops real tables from DMV queries."""
    return "bigquery" if "`" in sql_body else default


def _is_variable(node) -> bool:
    """True for a T-SQL @variable standing where a name is expected.

    ``EXEC @rv = dbo.MyProc`` and ``INSERT INTO @results`` both give sqlglot a
    Table node wrapping a Parameter, and ``.name`` then yields the variable
    without its ``@``. Left alone, ``rv`` and ``results`` are indexed and listed
    as if they were real tables.
    """
    return isinstance(node.this, exp.Parameter)


def _collect(tree) -> dict[str, set[str]]:
    """Walk a parsed tree and collect named objects, discarding parser debris."""
    ctes = {c.alias for c in tree.find_all(exp.CTE) if c.alias}
    cte_keys = {c.casefold() for c in ctes}

    tables: set[str] = set()
    aliases: set[str] = set()
    for node in tree.find_all(exp.Table):
        if _is_variable(node):
            continue
        alias, name = node.alias, node.name
        if alias and _is_valid_name(alias) and alias.casefold() not in cte_keys:
            aliases.add(alias)
        if name and _is_valid_name(name) and name.casefold() not in cte_keys:
            tables.add(name)

    # `UPDATE t SET ... FROM RealTable t` and `DELETE t FROM RealTable t` are
    # everyday T-SQL: sqlglot models the leading `t` as a Table node even though
    # it only refers to the alias declared in the FROM clause. Without this the
    # alias shows up as a table name throughout the UI.
    alias_keys = {a.casefold() for a in aliases}
    tables = {t for t in tables if t.casefold() not in alias_keys}

    columns = {
        c.name
        for c in tree.find_all(exp.Column)
        if c.name and _is_valid_name(c.name) and not _is_variable(c)
    }

    objects = _empty()
    objects["table"] = tables
    objects["column"] = columns
    objects["alias"] = aliases
    return objects


def extract_objects(sql_body: str, dialect: str = "tsql") -> dict[str, set[str]]:
    """Extract named database objects from a SQL body using sqlglot.

    Returns a dict with keys 'table', 'column', 'alias', 'procedure', 'function'.
    CTEs are excluded from the table set, and table aliases never leak into it.
    On any parse error, returns empty sets — never raises.
    """
    if not sql_body or not sql_body.strip():
        return _empty()

    # Lenient on purpose: over half of real-world T-SQL scripts use batch
    # separators, CLR blocks or sqlcmd syntax and never parse cleanly, yet
    # still name plenty of real tables. _is_valid_name drops the debris that
    # error recovery leaves behind.
    tree = _parse(sql_body, _pick_dialect(sql_body, dialect), strict=False)
    if tree is None:
        return _empty()
    return _collect(tree)


def objects_to_text(objects: dict[str, set[str]]) -> str:
    """Flatten all object names to a single space-separated string for FTS indexing."""
    all_names: list[str] = []
    for names in objects.values():
        all_names.extend(sorted(names))
    return " ".join(all_names)
