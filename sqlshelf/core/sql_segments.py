"""Cut a T-SQL script into batches and DML statements before parsing.

sqlglot cannot take a whole script: it reads ``GO`` as a command that swallows
the rest, folds a procedure's body into its ``CREATE PROCEDURE`` and turns
``IF … BEGIN … END ELSE`` into a meaningless tree. So the script is cut first,
into batches at ``GO`` lines and then each batch into the SELECT, INSERT,
UPDATE, DELETE and MERGE statements it holds, leaving out the control flow
around them. Each statement is then parsed on its own. Offsets are exact,
``body[s.start : s.end] == s.text``, which is how the diagram leads back to the
SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlglot.dialects.dialect import Dialect

# A line with only GO on it, plus an optional repeat count and comment. The \r?
# matters: files saved on Windows keep their \r\n, and $ stops before the \n.
_GO_LINE = re.compile(r"(?im)^[ \t]*GO(?:[ \t]+\d+)?[ \t]*(?:--[^\r\n]*)?\r?$")

_DML = frozenset({"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE"})
# Words that end the statement in progress: control flow, and every statement
# that is not DML. sqlglot 30 reads UPDATE STATISTICS as one token.
_CONTROL = frozenset("""
    DECLARE SET IF ELSE WHILE BEGIN END RETURN PRINT EXEC EXECUTE DROP CREATE
    ALTER GO RAISERROR THROW COMMIT ROLLBACK SAVE TRUNCATE USE OPEN CLOSE FETCH
    DEALLOCATE BREAK CONTINUE GOTO WAITFOR BACKUP RESTORE DBCC GRANT REVOKE DENY
    CHECKPOINT KILL RECONFIGURE BULK ENABLE DISABLE REVERT
    """.split()) | {"UPDATE STATISTICS"}
_SET_OPERATORS = frozenset({"UNION", "ALL", "EXCEPT", "INTERSECT"})
# After these a DML word names an action, not a statement: GRANT SELECT, INSERT
# ON …; AFTER INSERT, UPDATE; INSTEAD OF DELETE; BULK INSERT.
_ACTION_AFTER = frozenset({",", "GRANT", "DENY", "REVOKE", "AFTER", "OF", "BULK"})


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


def split_batches(sql: str) -> list[tuple[int, str]]:
    """The batches of *sql* between ``GO`` lines, each with its offset. Blank
    batches are left out."""
    batches: list[tuple[int, str]] = []
    pos = 0
    for match in _GO_LINE.finditer(sql):
        batches.append((pos, sql[pos : match.start()]))
        pos = match.end()
    batches.append((pos, sql[pos:]))
    return [(offset, text) for offset, text in batches if text.strip()]


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
    CASCADE``), ``BULK INSERT`` or a partition's ``MERGE RANGE``."""
    word, nxt = words[i], _word(words, i + 1)
    if prev in _ACTION_AFTER:
        return True
    if prev == "FOR":
        return word != "SELECT"  # DECLARE c CURSOR FOR SELECT is a query
    if prev == "ON" and nxt in ("CASCADE", "NO", "SET"):
        return True
    return (word, nxt) in (("UPDATE", "("), ("MERGE", "RANGE"))


def _role(words: list[str], i: int, prev: str, in_case: bool) -> str:
    """What the word at *i*, outside parentheses, does to the statement in
    progress: ``"dml"`` may start one, ``"stop"`` ends it and ``""`` is part of
    it."""
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
    return "stop" if word in _CONTROL else ""


def _continues(stmt: _Open, word: str, prev: str) -> bool:
    """Whether *word*, which would start or end a statement, carries on *stmt*
    instead: the next SELECT of a UNION, the DML after the CTEs, the SELECT or
    EXEC of an INSERT, the SET of an UPDATE and the actions of a MERGE."""
    if word == "SELECT" and prev in _SET_OPERATORS:
        return True
    if not stmt.kind:
        if word in _DML and word != "WITH":
            stmt.kind = word
            return True
        return False
    if stmt.kind == "INSERT" and not stmt.has_source:
        if word in ("SELECT", "EXEC", "EXECUTE"):
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


def segments(batch: str, offset: int = 0) -> list[Segment]:
    """The DML statements of one batch, at their offsets plus *offset*.

    Raises when the tokenizer rejects the batch, say for a string or comment
    left open; ``split_statements`` then skips that batch alone.
    """
    tokens = Dialect.get_or_raise("tsql").tokenize(batch)
    # Each token's source text rather than its value: the string 'END' and the
    # identifier [Select] keep their quotes and never read as keywords. Spacing
    # is evened out for the tokens that span words, like UPDATE STATISTICS.
    words = [" ".join(batch[t.start : t.end + 1].upper().split()) for t in tokens]
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
            role = _role(words, i, prev, case > 0)
            if role and not (stmt is not None and _continues(stmt, word, prev)):
                close(i - 1)
                if role == "dml":
                    stmt = _Open(i, "" if word == "WITH" else word)
            elif stmt is not None and stmt.kind == "INSERT" and word == "VALUES":
                stmt.has_source = True
        prev = word
    close(len(words) - 1)
    return found


def split_statements(sql: str) -> tuple[list[Segment], list[int]]:
    """Every DML statement in *sql*, and the offsets of the batches the
    tokenizer rejected. A rejected batch is skipped; the others go on."""
    found: list[Segment] = []
    rejected: list[int] = []
    for offset, batch in split_batches(sql):
        try:
            found.extend(segments(batch, offset))
        except Exception:
            rejected.append(offset)
    return found, rejected
