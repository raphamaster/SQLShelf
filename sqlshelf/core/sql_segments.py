"""Cut a SQL script into batches and DML statements before parsing.

sqlglot cannot take a whole script: it reads ``GO`` as a command that swallows
the rest, folds a procedure's body into its ``CREATE PROCEDURE`` and turns
``IF … BEGIN … END ELSE`` into a meaningless tree. So the script is cut first,
into batches and then each batch into the SELECT, INSERT, UPDATE, DELETE and
MERGE statements it holds, leaving out the control flow around them. Each
statement is then parsed on its own. Offsets are exact,
``body[s.start : s.end] == s.text``, which is how the diagram leads back to the
SQL.

Every function takes the dialect, a key of ``dialects.DIALECTS``, and cuts the
way that dialect does: batches end at ``GO`` lines in T-SQL, at ``/`` lines in
Oracle and at the delimiter a MySQL ``DELIMITER`` line sets. A PostgreSQL
function body between ``$$`` is read as code, and each dialect adds the
statements of its own procedural language to the words that end one.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from sqlglot.dialects.dialect import Dialect
from sqlglot.tokens import Token, Tokenizer

from .dialects import parser_for

# A line with only GO on it, plus an optional repeat count and comment. The \r?
# matters: files saved on Windows keep their \r\n, and $ stops before the \n.
_GO_LINE = re.compile(r"(?im)^[ \t]*GO(?:[ \t]+\d+)?[ \t]*(?:--[^\r\n]*)?\r?$")
# SQL*Plus runs the block above a line with only a slash on it.
_SLASH_LINE = re.compile(r"(?m)^[ \t]*/[ \t]*\r?$")
_DELIMITER_LINE = re.compile(r"(?im)^[ \t]*DELIMITER[ \t]+(\S+)[ \t]*\r?$")
# The $$ or $tag$ that opens a PostgreSQL function or DO body. Other
# dollar-quoted strings stay strings.
_BODY_OPEN = re.compile(r"(?i)\b(?:AS|DO)\s+(\$(?:[A-Za-z_]\w*)?\$)")
# Oracle's q'[…]', which no sqlglot tokenizer reads: its brackets let the
# string hold quotes.
_Q_QUOTE = re.compile(
    r"(?<!\w)([nN]?[qQ]')(?:\[.*?\]|\{.*?\}|\(.*?\)|<.*?>|(\S).*?\2)'", re.DOTALL
)

_DML = frozenset({"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE"})
# Words that end the statement in progress: control flow, and every statement
# that is not DML. sqlglot 30 reads UPDATE STATISTICS as one token.
_CONTROL = frozenset("""
    DECLARE SET IF ELSE WHILE BEGIN END RETURN PRINT EXEC EXECUTE DROP CREATE
    ALTER GO RAISERROR THROW COMMIT ROLLBACK SAVE TRUNCATE USE OPEN CLOSE FETCH
    DEALLOCATE BREAK CONTINUE GOTO WAITFOR BACKUP RESTORE DBCC GRANT REVOKE DENY
    CHECKPOINT KILL RECONFIGURE BULK ENABLE DISABLE REVERT
    """.split()) | {"UPDATE STATISTICS"}
# Each dialect's own statements that end the one in progress. Not shared: LOOP
# is a join hint in T-SQL, START WITH part of Oracle's CONNECT BY.
_CONTROL_OF = {
    "postgres": frozenset("""
        PERFORM RAISE LOOP ELSIF EXIT CALL COPY VACUUM ANALYZE EXPLAIN SHOW
        REFRESH COMMENT LOCK RESET DISCARD NOTIFY LISTEN LANGUAGE
        """.split()),
    "mysql": frozenset("""
        CALL LEAVE ITERATE ELSEIF LOOP START SHOW EXPLAIN LOCK UNLOCK
        """.split()),
    "oracle": frozenset("ELSIF LOOP EXIT EXCEPTION RAISE PRAGMA".split()),
    "bigquery": frozenset("""
        LOOP ELSEIF LEAVE ITERATE CALL ASSERT EXPORT RAISE
        """.split()),
}
_CONTROL_OF["mariadb"] = _CONTROL_OF["mysql"]
# Where IF( inside a statement is a function call, not an IF statement.
_IF_FUNCTION = frozenset({"mysql", "mariadb", "bigquery"})
_SET_OPERATORS = frozenset({"UNION", "ALL", "DISTINCT", "EXCEPT", "INTERSECT", "MINUS"})
# After these a DML word names an action, not a statement: GRANT SELECT, INSERT
# ON …; AFTER INSERT, UPDATE; INSTEAD OF DELETE; BULK INSERT; ON DUPLICATE KEY
# UPDATE.
_ACTION_AFTER = frozenset(
    {",", "GRANT", "DENY", "REVOKE", "AFTER", "OF", "BULK", "KEY"}
)


@dataclass(frozen=True)
class Segment:
    """One DML statement and where it sits in the body."""

    kind: str  # SELECT, INSERT, UPDATE, DELETE, MERGE; WITH if no DML follows
    start: int  # offset in the body
    end: int  # exclusive
    text: str


@dataclass
class _Open:
    """The statement being collected, and the parts it may still take in."""

    first: int  # index of its first token
    kind: str  # "" while a WITH waits for its DML
    has_source: bool = False  # INSERT already has its SELECT, EXEC or VALUES
    has_set: bool = False  # UPDATE already has its SET


def split_batches(sql: str, dialect: str = "tsql") -> list[tuple[int, str]]:
    """The batches of *sql*, each with its offset: between ``GO`` lines in
    T-SQL and plain SQL, ``/`` lines in Oracle and custom delimiters in MySQL
    and MariaDB; PostgreSQL and BigQuery scripts are one batch. Blank batches
    are left out."""
    if dialect in ("mysql", "mariadb"):
        batches = _delimited(sql)
    elif dialect in ("postgres", "bigquery"):
        batches = [(0, sql)]
    else:
        line = _SLASH_LINE if dialect == "oracle" else _GO_LINE
        batches = _around(sql, [m.span() for m in line.finditer(sql)], 0, len(sql))
    return [(offset, text) for offset, text in batches if text.strip()]


def _around(
    sql: str, cuts: list[tuple[int, int]], start: int, stop: int
) -> list[tuple[int, str]]:
    """The pieces of ``sql[start:stop]`` around *cuts*, each a span to leave
    out, with their offsets."""
    pieces: list[tuple[int, str]] = []
    pos = start
    for cut_start, cut_end in cuts:
        pieces.append((pos, sql[pos:cut_start]))
        pos = cut_end
    pieces.append((pos, sql[pos:stop]))
    return pieces


def _delimited(sql: str) -> list[tuple[int, str]]:
    """MySQL batches: after a ``DELIMITER $$`` line, ``$$`` ends a batch until
    the next ``DELIMITER`` line. The default ``;`` ends only a statement, which
    ``segments`` sees for itself."""
    pieces: list[tuple[int, str]] = []
    pos, delimiter = 0, ";"
    for match in [*_DELIMITER_LINE.finditer(sql), None]:
        stop = len(sql) if match is None else match.start()
        cuts: list[tuple[int, int]] = []
        if delimiter != ";":
            at = sql.find(delimiter, pos, stop)
            while at >= 0:
                cuts.append((at, at + len(delimiter)))
                at = sql.find(delimiter, at + len(delimiter), stop)
        pieces += _around(sql, cuts, pos, stop)
        if match is not None:
            pos, delimiter = match.end(), match.group(1)
    return pieces


def readable(text: str, dialect: str) -> str:
    """*text* with what sqlglot cannot tokenize rewritten at the same length:
    Oracle's ``q'[it's]'`` becomes ``'  it s '``. Every offset still holds."""
    if dialect != "oracle" or "'" not in text:
        return text
    return _Q_QUOTE.sub(_plain_string, text)


def _plain_string(match: re.Match[str]) -> str:
    text = match.group(0)
    lead = len(match.group(1))  # q' or Nq'
    inner = text[lead + 1 : -2].replace("'", " ")
    return " " * (lead - 1) + "' " + inner + " '"


def _open_bodies(text: str) -> str:
    """*text* with the ``$$`` around each PostgreSQL function or DO body
    blanked, so the tokenizer reads the body as code rather than as one
    string."""
    out = text
    pos = 0
    while (match := _BODY_OPEN.search(text, pos)) is not None:
        tag = match.group(1)
        close = text.find(tag, match.end())
        if close < 0:
            break
        for at in (match.start(1), close):
            out = out[:at] + " " * len(tag) + out[at + len(tag) :]
        pos = close + len(tag)
    return out


@functools.cache
def _tokenizer_class(parser: str) -> type[Tokenizer]:
    """The tokenizer of the sqlglot dialect *parser*, without its command
    words. sqlglot reads what follows one of those at the start of a statement
    up to the next ``;`` as a single string: T-SQL's ``END``, ``PRINT`` and
    ``FETCH``, PostgreSQL's ``DO``, BigQuery's ``BEGIN``, ``LOOP`` and
    ``ELSEIF``. The statements in there must show."""
    base = Dialect.get_or_raise(parser).tokenizer_class
    return type(f"Plain{base.__name__}", (base,), {"COMMANDS": frozenset()})


def tokenize(text: str, parser: str) -> list[Token]:
    """*text* as the sqlglot dialect *parser* tokenizes it, with every word
    kept a token. Raises when the tokenizer rejects the text."""
    dialect = Dialect.get_or_raise(parser)
    # One tokenizer per call: it keeps state, and indexing runs on threads.
    return _tokenizer_class(parser)(dialect=dialect).tokenize(text)


def _word(words: list[str], i: int) -> str:
    return words[i] if i < len(words) else ""


def _opens_cte(words: list[str], i: int) -> bool:
    """Whether the WITH at *i* starts a statement: ``WITH name AS (``, ``WITH
    name (cols) AS (`` or ``WITH XMLNAMESPACES``. Table hints, ``WITH CHECK``,
    ``WITH TIES`` and options such as ``WITH SCHEMABINDING AS`` use the word
    for something else."""
    name = _word(words, i + 1)
    if name == "XMLNAMESPACES":
        return True
    if name in ("", "("):
        return False
    j = i + 2
    if _word(words, j) == "(":  # the column list
        depth = 0
        while j < len(words):
            depth += {"(": 1, ")": -1}.get(words[j], 0)
            j += 1
            if depth == 0:
                break
    return _word(words, j) == "AS" and _word(words, j + 1) == "("


def _names_action(words: list[str], i: int, prev: str) -> bool:
    """Whether the DML word at *i* names an action instead of starting a
    statement: a permission, a trigger event (``FOR INSERT``, ``IF
    UPDATE(col)``), a cursor's ``FOR UPDATE``, a foreign key rule (``ON DELETE
    CASCADE``), ``BULK INSERT``, a partition's ``MERGE RANGE`` or what an
    INSERT does on a duplicate key or a conflict. ``WHILE … DO`` is followed
    by statements."""
    word, nxt = words[i], _word(words, i + 1)
    if prev in _ACTION_AFTER:
        return True
    if prev == "DO":
        return (word, nxt) == ("UPDATE", "SET")  # ON CONFLICT DO UPDATE SET
    if prev == "FOR":
        return word != "SELECT"  # DECLARE c CURSOR FOR SELECT is a query
    if prev == "ON" and nxt in ("CASCADE", "NO", "SET"):
        return True
    return (word, nxt) in (("UPDATE", "("), ("MERGE", "RANGE"))


def _role(
    words: list[str], i: int, prev: str, in_case: bool, control: frozenset[str]
) -> str:
    """What the word at *i*, outside parentheses, does to the statement in
    progress: ``"dml"`` may start one, ``"stop"`` ends it and ``""`` is part of
    it. *control* holds the words that end one."""
    word = words[i]
    if word == "UPDATE" and _word(words, i + 1) == "STATISTICS":
        return "stop"
    if word in _DML:
        if word == "WITH":
            return "dml" if _opens_cte(words, i) else ""
        return "" if _names_action(words, i, prev) else "dml"
    if word == "ELSE" and in_case:
        return ""
    if word == "FETCH" and prev in ("ROW", "ROWS"):
        return ""  # OFFSET … ROWS FETCH NEXT … ROWS ONLY
    return "stop" if word in control else ""


def _continues(stmt: _Open, word: str, prev: str, dialect: str) -> bool:
    """Whether *word*, which would start or end a statement, carries on *stmt*
    instead: the next SELECT of a UNION, the DML after the CTEs, the SELECT or
    EXEC of an INSERT, the SET of an UPDATE, the actions of a MERGE, the SET of
    an ON CONFLICT DO UPDATE and the SET of a MySQL INSERT."""
    if word == "SELECT" and prev in _SET_OPERATORS:
        return True
    if not stmt.kind:
        if word in _DML and word != "WITH":
            stmt.kind = word
            return True
        return False
    if stmt.kind == "INSERT" and word == "SET" and prev == "UPDATE":
        return True
    if stmt.kind == "INSERT" and not stmt.has_source:
        if word in ("SELECT", "EXEC", "EXECUTE") or (
            word == "SET" and dialect in ("mysql", "mariadb")
        ):
            stmt.has_source = True
            return True
    if stmt.kind == "UPDATE" and not stmt.has_set and word == "SET":
        stmt.has_set = True
        return True
    if stmt.kind == "MERGE":
        return (prev == "THEN" and word in ("UPDATE", "INSERT", "DELETE")) or (
            prev == "UPDATE" and word == "SET"
        )
    return False


def segments(batch: str, offset: int = 0, dialect: str = "tsql") -> list[Segment]:
    """The DML statements of one batch, at their offsets plus *offset*.

    Raises when the tokenizer rejects the batch, say for a string or comment
    left open; ``split_statements`` then skips that batch alone.
    """
    code = readable(batch, dialect)
    if dialect == "postgres" and "$" in code:
        code = _open_bodies(code)
    tokens = tokenize(code, parser_for(dialect))
    control = _CONTROL | _CONTROL_OF.get(dialect, frozenset())
    # Each token's source text rather than its value: the string 'END' and the
    # identifier [Select] keep their quotes and never read as keywords. Spacing
    # is evened out for the tokens that span words, like UPDATE STATISTICS.
    words = [" ".join(code[t.start : t.end + 1].upper().split()) for t in tokens]
    found: list[Segment] = []
    stmt: _Open | None = None
    depth = case = 0
    prev = ""

    def close(last: int) -> None:
        nonlocal stmt, case
        if stmt is not None:
            start, end = tokens[stmt.first].start, tokens[last].end + 1
            text = batch[start:end]
            found.append(
                Segment(stmt.kind or "WITH", offset + start, offset + end, text)
            )
        stmt = None
        # A CASE never spans statements: one left open while typing must not
        # take the END of a BEGIN … END block for its own.
        case = 0

    for i, word in enumerate(words):
        if word == "(":
            depth += 1
        elif word == ")":
            depth = max(0, depth - 1)
        elif word == "CASE":
            case += 1
        elif word == "END" and case:
            case -= 1
            prev = word
            continue
        if depth == 0 and word == ";":
            close(i - 1)
        elif depth == 0:
            role = _role(words, i, prev, case > 0, control)
            if (
                word == "IF"
                and stmt is not None
                and dialect in _IF_FUNCTION
                and _word(words, i + 1) == "("
            ):
                role = ""  # SELECT IF(a > 1, 'x', 'y') …
            if role and not (
                stmt is not None and _continues(stmt, word, prev, dialect)
            ):
                close(i - 1)
                if role == "dml":
                    stmt = _Open(i, "" if word == "WITH" else word)
            elif stmt is not None and stmt.kind == "INSERT" and word == "VALUES":
                stmt.has_source = True
        prev = word
    close(len(words) - 1)
    return found


def split_statements(
    sql: str, dialect: str = "tsql"
) -> tuple[list[Segment], list[int]]:
    """Every DML statement in *sql*, and the offsets of the batches the
    tokenizer rejected. A rejected batch is skipped; the others go on."""
    found: list[Segment] = []
    rejected: list[int] = []
    for offset, batch in split_batches(sql, dialect):
        try:
            found.extend(segments(batch, offset, dialect))
        except Exception:
            rejected.append(offset)
    return found, rejected
