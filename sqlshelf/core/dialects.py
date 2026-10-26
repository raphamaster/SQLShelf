"""Which SQL dialect a query is written in.

A query can say so in its frontmatter (``dialect: postgres``); that always
wins. Otherwise the dialect is read from the syntax: ``GO`` lines and
``[brackets]`` are T-SQL, ``::`` casts and ``$$`` bodies PostgreSQL, ```a`.`b```
and ``LIMIT 5, 10`` MySQL, ``DUAL`` and ``ROWNUM`` Oracle, ```proj.ds.t``` and
``SAFE_CAST`` BigQuery. Each sign has a weight and counts once, and the dialect
with the highest total wins when it reaches ``_THRESHOLD`` and no other ties
it. A query with no sign at all, or with signs that cancel out, is plain
``SQL`` and is read as T-SQL, the dialect every query was read as before.

MariaDB is MySQL to the parser: its own signs only tell the two apart once the
MySQL family has won.

Comments and string literals are blanked before most signs are looked for, so
``-- LIMIT 5, 10`` or ``'GO'`` say nothing. Nothing here raises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["frontmatter", "detected", "default"]


@dataclass(frozen=True)
class Dialect:
    key: str  # what the frontmatter and the index store
    label: str  # what the UI shows
    parser: str  # the sqlglot dialect that reads it


DIALECTS: dict[str, Dialect] = {
    d.key: d
    for d in (
        Dialect("tsql", "T-SQL", "tsql"),
        Dialect("postgres", "PostgreSQL", "postgres"),
        Dialect("mysql", "MySQL", "mysql"),
        Dialect("mariadb", "MariaDB", "mysql"),
        Dialect("oracle", "Oracle", "oracle"),
        Dialect("bigquery", "BigQuery", "bigquery"),
    )
}
# No sign of any dialect: read as T-SQL, shown as plain SQL.
GENERIC = Dialect("sql", "SQL", "tsql")

# The names a frontmatter may use, compared without case, spaces, "-", "_" or
# "/": "PL/SQL", "SQL Server" and "t-sql" all name one of ours.
_ALIASES = {
    "tsql": "tsql",
    "mssql": "tsql",
    "sqlserver": "tsql",
    "mssqlserver": "tsql",
    "azuresql": "tsql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "pg": "postgres",
    "pgsql": "postgres",
    "psql": "postgres",
    "plpgsql": "postgres",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "maria": "mariadb",
    "oracle": "oracle",
    "plsql": "oracle",
    "oraclesql": "oracle",
    "bigquery": "bigquery",
    "bq": "bigquery",
    "googlesql": "bigquery",
    "sql": "sql",
    "ansi": "sql",
    "generic": "sql",
}
_NAME_NOISE = re.compile(r"[\s\-_/]+")

_THRESHOLD = 2
# Signs past the first 200 KB change nothing a reader would notice, and a
# generated script of several megabytes must not stall indexing.
_SCAN_LIMIT = 200_000


@dataclass(frozen=True)
class Detection:
    """What the syntax says: the dialect and the signs that named it."""

    dialect: Dialect
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolved:
    """The dialect a query is read as, and where that came from."""

    dialect: Dialect
    source: Source
    evidence: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.dialect.key

    @property
    def parser(self) -> str:
        return self.dialect.parser


def get(key: str) -> Dialect:
    """The dialect stored as *key*; plain SQL for anything else."""
    return DIALECTS.get(key, GENERIC)


def normalize(value: Any) -> str | None:
    """The key a frontmatter value names, or None when it names none of ours:
    ``"PL/SQL"`` gives ``"oracle"``, ``"SQL Server"`` gives ``"tsql"``."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(_NAME_NOISE.sub("", value.casefold()))


def key_for(dialect: str | None, body: str) -> str:
    """*dialect* as given, or the key of the dialect *body* reads as when None."""
    return detect(body).dialect.key if dialect is None else dialect


def parser_for(dialect: str | None, body: str = "") -> str:
    """The sqlglot dialect for *dialect*, one of our keys or a sqlglot name.
    None reads the dialect from *body*."""
    if dialect is None:
        return detect(body).dialect.parser
    if dialect in DIALECTS:
        return DIALECTS[dialect].parser
    if dialect in ("", GENERIC.key):
        return GENERIC.parser
    return dialect


# --- Detection ----------------------------------------------------------------

_I = re.IGNORECASE
_M = re.IGNORECASE | re.MULTILINE


@dataclass(frozen=True)
class _Sign:
    dialect: str
    weight: int
    label: str
    pattern: re.Pattern[str]
    raw: bool = False  # looked for in the text as written, comments included


def _sign(
    dialect: str, weight: int, label: str, pattern: str, flags: int = _I
) -> _Sign:
    return _Sign(dialect, weight, label, re.compile(pattern, flags))


def _raw(dialect: str, weight: int, label: str, pattern: str, flags: int = _I) -> _Sign:
    return _Sign(dialect, weight, label, re.compile(pattern, flags), raw=True)


def _calls(names: str) -> str:
    """A pattern for a call to any of the space-separated *names*."""
    return r"\b(?:" + "|".join(names.split()) + r")\s*\("


_LIMIT = r"\bLIMIT\s+\d"
_HASH_COMMENT = r"^[ \t]*#(?:[ \t]|\r?$)"  # never #temp: no space after its #

_TSQL_CALLS = _calls(
    "GETDATE GETUTCDATE SYSDATETIME SYSUTCDATETIME DATEADD DATEPART "
    "DATENAME DATEDIFF_BIG LEN CHARINDEX PATINDEX NEWID SCOPE_IDENTITY "
    "OBJECT_ID IIF EOMONTH TRY_CONVERT DATEFROMPARTS QUOTENAME"
)
_MYSQL_CALLS = _calls(
    "GROUP_CONCAT CURDATE CURTIME DATE_FORMAT STR_TO_DATE UNIX_TIMESTAMP "
    "FROM_UNIXTIME LAST_INSERT_ID FIND_IN_SET"
)
_BIGQUERY_CALLS = _calls(
    "DATE_DIFF TIMESTAMP_DIFF DATETIME_DIFF TIMESTAMP_TRUNC DATETIME_TRUNC "
    "FORMAT_DATE FORMAT_TIMESTAMP PARSE_DATE PARSE_TIMESTAMP "
    "GENERATE_DATE_ARRAY GENERATE_TIMESTAMP_ARRAY CURRENT_DATETIME"
)

# One sign per line reads as a table; black would spread each over six.
# fmt: off
_SIGNS: tuple[_Sign, ...] = (
    # T-SQL
    _sign("tsql", 3, "GO", r"^[ \t]*GO(?:[ \t]+\d+)?[ \t]*\r?$", _M),
    _sign("tsql", 2, "[identifiers]", r"(?<![\w)\]])\[[A-Za-z_@#][^\]\r\n]*\]"),
    _sign("tsql", 3, "DECLARE @", r"\bDECLARE\s+@"),
    _sign("tsql", 2, "@@", r"@@(?:ROWCOUNT|ERROR|TRANCOUNT|FETCH_STATUS|SPID|SERVERNAME)\b"),
    _sign("tsql", 3, "TOP", r"\b(?:SELECT(?:\s+DISTINCT)?|UPDATE|DELETE|INSERT)\s+TOP\s*\(?\s*[\d@]"),
    _sign("tsql", 3, "#temp", r"\b(?:FROM|JOIN|INTO|TABLE|UPDATE)\s+##?[A-Za-z_]"),
    _sign("tsql", 3, "NOLOCK", r"\bNOLOCK\b"),
    _sign("tsql", 2, "dbo.", r"\bdbo\s*\."),
    _sign("tsql", 2, "T-SQL functions", _TSQL_CALLS),
    _sign("tsql", 2, "CONVERT(type", r"\bCONVERT\s*\(\s*(?:N?VARCHAR|N?CHAR|INT|BIGINT|DATE|DATETIME2?|DECIMAL|NUMERIC|FLOAT|MONEY|BIT|TIME)\b"),
    _sign("tsql", 3, "APPLY", r"\b(?:CROSS|OUTER)\s+APPLY\b"),
    _sign("tsql", 3, "OPTION (…)", r"\bOPTION\s*\(\s*(?:RECOMPILE|MAXDOP|MAXRECURSION|OPTIMIZE|FORCE|HASH|LOOP|MERGE)\b"),
    _sign("tsql", 3, "SET NOCOUNT", r"\bSET\s+(?:NOCOUNT|ANSI_NULLS|QUOTED_IDENTIFIER|XACT_ABORT|ANSI_WARNINGS|ARITHABORT)\b"),
    _sign("tsql", 3, "(MAX)", r"\bN?VARCHAR\s*\(\s*MAX\s*\)"),
    _sign("tsql", 2, "T-SQL types", r"\b(?:DATETIME2|UNIQUEIDENTIFIER|SMALLDATETIME|NTEXT|MONEY)\b"),
    _sign("tsql", 2, "EXEC", r"\bEXEC\s"),
    _sign("tsql", 3, "sp_executesql", r"\bsp_executesql\b"),
    _sign("tsql", 2, "PRINT", r"^[ \t]*PRINT\b", _M),
    _sign("tsql", 2, "RAISERROR", r"\bRAISERROR\b"),
    _sign("tsql", 3, "OUTPUT inserted", r"\bOUTPUT\s+(?:INSERTED|DELETED)\s*\."),
    _sign("tsql", 2, "sys.", r"\bsys\s*\.\s*(?:objects|tables|columns|indexes|schemas|databases|procedures|views|types|partitions|sql_modules|foreign_keys|dm_\w+)\b"),
    _sign("tsql", 2, "IDENTITY(", r"\bIDENTITY\s*\(\s*\d"),
    _sign("tsql", 1, "N'", r"(?<!\w)N'"),
    # PostgreSQL
    _sign("postgres", 3, "::", r"::\s*[A-Za-z_\"]"),
    _sign("postgres", 3, "$$", r"(?:\bAS|\bDO)\s+\$\w*\$|\$\w*\$\s*LANGUAGE\b"),
    _sign("postgres", 3, "plpgsql", r"\bLANGUAGE\s+'?plpgsql\b"),
    _sign("postgres", 3, "ILIKE", r"\bILIKE\b"),
    _sign("postgres", 3, "JSONB", r"\bJSONB\b"),
    _sign("postgres", 3, "TIMESTAMPTZ", r"\bTIMESTAMPTZ\b"),
    _sign("postgres", 3, "SERIAL", r"\b(?:BIG|SMALL)?SERIAL\b"),
    _sign("postgres", 3, "DISTINCT ON", r"\bDISTINCT\s+ON\s*\("),
    _sign("postgres", 3, "ON CONFLICT", r"\bON\s+CONFLICT\b"),
    _sign("postgres", 3, "generate_series", _calls("GENERATE_SERIES")),
    _sign("postgres", 2, "pg_", r"\bpg_[a-z_]+"),
    _sign("postgres", 2, "nextval(", r"\bNEXTVAL\s*\(\s*'"),
    _sign("postgres", 2, "RAISE NOTICE", r"\bRAISE\s+(?:NOTICE|EXCEPTION|INFO|WARNING|DEBUG)\b"),
    _sign("postgres", 1, "RETURNING", r"\bRETURNING\b"),
    _sign("postgres", 1, "LIMIT", _LIMIT),
    _sign("postgres", 1, "TO_CHAR", _calls("TO_DATE TO_CHAR TO_NUMBER")),
    _sign("postgres", 1, "ELSIF", r"\bELSIF\b"),
    _sign("postgres", 1, "UNNEST", _calls("UNNEST")),
    # MySQL
    _sign("mysql", 3, "`a`.`b`", r"`[^`.\r\n]+`\s*\.\s*`"),
    _sign("mysql", 2, "`identifier`", r"`[^`.\-\s]+`"),
    _sign("mysql", 3, "DELIMITER", r"^[ \t]*DELIMITER[ \t]+\S", _M),
    _sign("mysql", 3, "LIMIT n, m", r"\bLIMIT\s+\d+\s*,\s*\d+"),
    _sign("mysql", 3, "AUTO_INCREMENT", r"\bAUTO_INCREMENT\b"),
    _sign("mysql", 3, "ENGINE=", r"\bENGINE\s*=\s*\w"),
    _sign("mysql", 3, "MySQL functions", _MYSQL_CALLS),
    _sign("mysql", 3, "ON DUPLICATE KEY", r"\bON\s+DUPLICATE\s+KEY\s+UPDATE\b"),
    _sign("mysql", 3, "INSERT IGNORE", r"\bINSERT\s+IGNORE\b|\bREPLACE\s+INTO\b"),
    _sign("mysql", 3, "UNSIGNED", r"\bUNSIGNED\b"),
    _sign("mysql", 3, "SHOW", r"^[ \t]*SHOW\s+(?:FULL\s+)?(?:TABLES|DATABASES|COLUMNS|CREATE|PROCESSLIST|VARIABLES|STATUS|INDEX)\b", _M),
    _raw("mysql", 2, "# comment", _HASH_COMMENT, _M),
    _sign("mysql", 1, "IFNULL", _calls("IFNULL")),
    _sign("mysql", 1, "DATE_ADD", _calls("DATE_ADD DATE_SUB")),
    _sign("mysql", 1, "LIMIT", _LIMIT),
    # MariaDB, only to tell it from MySQL
    _sign("mariadb", 3, "SYSTEM VERSIONING", r"\bWITH\s+SYSTEM\s+VERSIONING\b"),
    _sign("mariadb", 3, "ENGINE=Aria", r"\bENGINE\s*=\s*(?:Aria|ColumnStore|Spider|S3|CONNECT)\b"),
    _sign("mariadb", 3, "MariaDB functions", _calls("JSON_DETAILED COLUMN_CREATE COLUMN_GET")),
    _raw("mariadb", 3, "/*M!", r"/\*M!"),
    # Oracle
    _sign("oracle", 3, "DUAL", r"\bFROM\s+DUAL\b"),
    _sign("oracle", 3, "ROWNUM", r"\bROWNUM\b"),
    _sign("oracle", 3, "SYSDATE", r"\bSYS(?:DATE|TIMESTAMP)\b(?!\s*\()"),
    _sign("oracle", 3, "NVL", _calls("NVL NVL2")),
    _sign("oracle", 2, "DECODE", _calls("DECODE")),
    _sign("oracle", 3, "VARCHAR2", r"\bN?VARCHAR2\b"),
    _sign("oracle", 2, "NUMBER(", r"\bNUMBER\s*\("),
    _sign("oracle", 3, "CONNECT BY", r"\bCONNECT\s+BY\b"),
    _sign("oracle", 3, "(+)", r"\(\s*\+\s*\)"),
    _sign("oracle", 3, "/", r"^[ \t]*/[ \t]*\r?$", _M),
    _sign("oracle", 3, "DBMS_", r"\b(?:DBMS|UTL)_\w+"),
    _sign("oracle", 3, "MINUS", r"\bMINUS\b"),
    _raw("oracle", 3, "q'[…]'", r"(?<!\w)[nN]?[qQ]'[\[{(<]"),
    _sign("oracle", 3, "%TYPE", r"%(?:ROW)?TYPE\b"),
    _sign("oracle", 3, "PACKAGE", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\b"),
    _sign("oracle", 3, "BULK COLLECT", r"\bBULK\s+COLLECT\b"),
    _sign("oracle", 3, ".NEXTVAL", r"\.\s*(?:NEXTVAL|CURRVAL)\b"),
    _sign("oracle", 3, "data dictionary", r"\b(?:ALL|DBA|USER)_(?:TABLES|TAB_COLUMNS|OBJECTS|VIEWS|INDEXES|CONSTRAINTS|SOURCE|USERS|SEGMENTS|SEQUENCES)\b"),
    _sign("oracle", 2, "LISTAGG", _calls("LISTAGG")),
    _sign("oracle", 2, "EXECUTE IMMEDIATE", r"\bEXECUTE\s+IMMEDIATE\b"),
    _sign("oracle", 1, "TO_CHAR", _calls("TO_DATE TO_CHAR TO_NUMBER")),
    _sign("oracle", 1, "ELSIF", r"\bELSIF\b"),
    # BigQuery
    _sign("bigquery", 3, "`project.dataset`", r"`[^`\r\n]*[.\-][^`\r\n]*`"),
    _sign("bigquery", 3, "SAFE_CAST", r"\bSAFE_(?:CAST|DIVIDE)\s*\(|\bSAFE\s*\.\s*\w+\s*\("),
    _sign("bigquery", 3, "STRUCT<", r"\b(?:STRUCT|ARRAY)\s*<"),
    _sign("bigquery", 3, "AS STRUCT", r"\bSELECT\s+AS\s+(?:STRUCT|VALUE)\b"),
    _sign("bigquery", 3, "* EXCEPT", r"\*\s*(?:EXCEPT|REPLACE)\s*\("),
    _sign("bigquery", 3, "_TABLE_SUFFIX", r"\b_(?:TABLE_SUFFIX|PARTITIONTIME|PARTITIONDATE)\b"),
    _sign("bigquery", 3, "INT64", r"\b(?:INT64|FLOAT64|BIGNUMERIC)\b"),
    _sign("bigquery", 3, "AS STRING", r"\bAS\s+STRING\b"),
    _sign("bigquery", 3, "BigQuery functions", _BIGQUERY_CALLS),
    _sign("bigquery", 3, "[OFFSET(n)]", r"\[\s*(?:SAFE_)?(?:OFFSET|ORDINAL)\s*\("),
    _sign("bigquery", 2, "QUALIFY", r"\bQUALIFY\b"),
    _sign("bigquery", 2, "EXECUTE IMMEDIATE", r"\bEXECUTE\s+IMMEDIATE\b"),
    _raw("bigquery", 1, "# comment", _HASH_COMMENT, _M),
    _sign("bigquery", 1, "IFNULL", _calls("IFNULL")),
    _sign("bigquery", 1, "DATE_ADD", _calls("DATE_ADD DATE_SUB")),
    _sign("bigquery", 1, "UNNEST", _calls("UNNEST")),
    _sign("bigquery", 1, "LIMIT", _LIMIT),
)
# fmt: on

# What most signs are looked for in: comments blanked, string literals emptied
# with their quotes kept, so N'…' still shows. Oracle's q'[…]' comes first: its
# brackets let it hold quotes.
_NOISE = re.compile(
    r"""(?<!\w)[nN]?[qQ]'(?:\[.*?\]|\{.*?\}|\(.*?\)|<.*?>|(?P<d>\S).*?(?P=d))'"""
    r"""|--[^\r\n]*"""
    r"""|/\*.*?(?:\*/|\Z)"""
    r"""|'(?:[^']|'')*(?:'|\Z)""",
    re.DOTALL,
)
_BLANK = re.compile(r"[^\r\n]")


def _quiet(match: re.Match[str]) -> str:
    text = match.group(0)
    if text.startswith(("--", "/*")):
        return _BLANK.sub(" ", text)
    first = text.index("'") + 1
    last = len(text) - 1 if len(text) > first and text.endswith("'") else len(text)
    return text[:first] + _BLANK.sub(" ", text[first:last]) + text[last:]


def code_view(text: str) -> str:
    """*text* with comments blanked and string literals emptied, at the same
    length, so every offset still holds."""
    return _NOISE.sub(_quiet, text)


def detect(body: str) -> Detection:
    """The dialect *body* reads as, with the signs that named it."""
    raw = body[:_SCAN_LIMIT]
    code = code_view(raw)
    scores: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for sign in _SIGNS:
        if sign.pattern.search(raw if sign.raw else code):
            scores[sign.dialect] = scores.get(sign.dialect, 0) + sign.weight
            evidence.setdefault(sign.dialect, []).append(sign.label)
    # MySQL and MariaDB compete as one family; MariaDB's own signs pick it.
    maria = scores.pop("mariadb", 0)
    family = scores.pop("mysql", 0) + maria
    if family:
        member = "mariadb" if maria else "mysql"
        scores[member] = family
        evidence[member] = evidence.get("mysql", []) + evidence.get("mariadb", [])
    ranked = sorted(scores.values(), reverse=True)
    if not ranked or ranked[0] < _THRESHOLD or ranked[1:2] == ranked[:1]:
        return Detection(GENERIC)
    key = max(scores, key=lambda k: scores[k])
    return Detection(DIALECTS[key], tuple(evidence[key]))


def resolve(declared: Any, body: str) -> Resolved:
    """The dialect a query is read as: the one its frontmatter names, if it
    names one of ours, or else the one its syntax shows."""
    key = normalize(declared)
    if key is not None:
        return Resolved(get(key), "frontmatter")
    found = detect(body)
    source: Source = "default" if found.dialect is GENERIC else "detected"
    return Resolved(found.dialect, source, found.evidence)
