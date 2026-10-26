from __future__ import annotations

import contextlib
import io
import re

import sqlglot
from sqlglot import exp

from .dialects import key_for, parser_for
from .sql_segments import readable, split_batches, split_statements

OBJECT_TYPES = ("table", "column", "alias", "procedure", "function")

# A name that survived parsing but is clearly parser debris (a stray backtick,
# an unbalanced quote, a fragment of punctuation) must never reach the index.
# Quoted identifiers may legitimately contain spaces ("[Order Details]"), so the
# rule is: no SQL punctuation, and at least one alphanumeric character.
_JUNK_CHARS_RE = re.compile(r"[`'\"();,\[\]]")
_HAS_WORD_RE = re.compile(r"\w")
_MAX_NAME_LEN = 128

# Reserved words of a procedural block that error recovery turns into names:
# from ``DECLARE v NUMBER`` or ``END LOOP`` sqlglot makes a column aliased by
# the next word. No unquoted name can be one of them.
_BLOCK_WORDS = frozenset(
    "BEGIN DECLARE ELSE ELSEIF ELSIF END FOR IF RETURN THEN WHILE".split()
)

# Parsers of the dialects where ``SELECT … INTO x`` fills variables rather
# than creating a table.
_INTO_VARIABLES = frozenset({"postgres", "mysql", "oracle", "bigquery"})


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


def _is_variable(node) -> bool:
    """True for a T-SQL @variable standing where a name is expected.

    ``EXEC @rv = dbo.MyProc`` and ``INSERT INTO @results`` both give sqlglot a
    Table node wrapping a Parameter, and ``.name`` then yields the variable
    without its ``@``. Left alone, ``rv`` and ``results`` are indexed and listed
    as if they were real tables.
    """
    return isinstance(node.this, exp.Parameter)


def _is_block_word(node) -> bool:
    """True for an unquoted ``END``, ``DECLARE``… taken for a name."""
    ident = node.this
    quoted = isinstance(ident, exp.Identifier) and ident.quoted
    return not quoted and node.name.upper() in _BLOCK_WORDS


def _collect(tree, parser: str = "tsql") -> dict[str, set[str]]:
    """Walk a tree parsed by the sqlglot *parser* and collect named objects,
    discarding parser debris."""
    ctes = {c.alias for c in tree.find_all(exp.CTE) if c.alias}
    cte_keys = {c.casefold() for c in ctes}

    # SELECT … INTO v names variables in most dialects; T-SQL makes a table.
    skipped: set[int] = set()
    if parser in _INTO_VARIABLES:
        for into in tree.find_all(exp.Into):
            skipped |= {id(node) for node in into.find_all(exp.Table)}

    # CREATE FUNCTION f / CREATE PROCEDURE p name a routine, not a table.
    routines: dict[str, set[str]] = {"procedure": set(), "function": set()}
    routine_ids: set[int] = set()
    for create in tree.find_all(exp.Create):
        kind = str(create.args.get("kind") or "").lower()
        if kind in routines and isinstance(create.this, exp.Expression):
            for node in create.this.find_all(exp.Table):
                routine_ids.add(id(node))
                if node.name and _is_valid_name(node.name):
                    routines[kind].add(node.name)

    tables: set[str] = set()
    aliases: set[str] = set()
    for node in tree.find_all(exp.Table):
        if _is_variable(node) or id(node) in routine_ids | skipped:
            continue
        if _is_block_word(node):
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
        if c.name
        and _is_valid_name(c.name)
        and not _is_variable(c)
        and not _is_block_word(c)
    }

    objects = _empty()
    objects["table"] = tables
    objects["column"] = columns
    objects["alias"] = aliases
    objects.update(routines)
    return objects


def _merge(into: dict[str, set[str]], objects: dict[str, set[str]]) -> None:
    for kind, names in objects.items():
        into[kind] |= names


def extract_objects(sql_body: str, dialect: str | None = None) -> dict[str, set[str]]:
    """Extract named database objects from a SQL body using sqlglot.

    *dialect* is a key of ``dialects.DIALECTS``; None reads it from the body.
    Returns a dict with keys 'table', 'column', 'alias', 'procedure', 'function'.
    CTEs are excluded from the table set, and table aliases never leak into it.
    On any parse error, returns empty sets — never raises.
    """
    if not sql_body or not sql_body.strip():
        return _empty()
    key = key_for(dialect, sql_body)
    parser = parser_for(key)

    # Lenient on purpose: over half of real-world T-SQL scripts use batch
    # separators, CLR blocks or sqlcmd syntax and never parse cleanly, yet
    # still name plenty of real tables. _is_valid_name drops the debris that
    # error recovery leaves behind. Each batch is read on its own: a GO, a
    # slash or a MySQL delimiter would otherwise end the reading early.
    objects = _empty()
    for _, batch in split_batches(sql_body, key):
        tree = _parse(readable(batch, key), parser, strict=False)
        if tree is not None:
            _merge(objects, _collect(tree, parser))

    # Procedural code escapes that reading: sqlglot takes all that follows an
    # END, PRINT or EXEC up to the next ";" as one command, and a PL/SQL block,
    # a PL/pgSQL body between $$ or a BigQuery script hides the statements it
    # holds. Each DML statement is read again on its own.
    try:
        found, _ = split_statements(sql_body, key)
    except Exception:
        found = []
    for seg in found:
        tree = _parse(readable(seg.text, key), parser, strict=False)
        if tree is not None:
            _merge(objects, _collect(tree, parser))
    return objects


def objects_to_text(objects: dict[str, set[str]]) -> str:
    """Flatten all object names to a single space-separated string for FTS indexing."""
    all_names: list[str] = []
    for names in objects.values():
        all_names.extend(sorted(names))
    return " ".join(all_names)
