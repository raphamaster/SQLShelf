"""Model a query as the objects it uses, read from its text alone.

The diagram never reaches the database: no catalog, no keys, no types. It shows
what the SQL says: the tables each statement reads and writes, how they are
joined, what filters them and where the rows go. The script is cut into
statements first (``sql_segments``) and each one is parsed on its own, so a
statement sqlglot cannot read costs only itself. A statement is then walked
block by block (the main query, its CTEs, its subqueries), each block with the
aliases it can see, which is how a column finds its table. Last, the graphs of
the statements are merged by node id: the same table in two statements is one
node, and that is what ties a script's lineage together.

``sqlglot.optimizer.scope`` is not used. It leaves out the SET of an UPDATE,
the ON and WHEN of a MERGE and the joins sqlglot nests inside the FROM of an
UPDATE or DELETE, which is most of what an ETL script does.

Nothing here raises. A statement that cannot be read is marked ``skipped`` and
a script that cannot be read at all gives an empty graph with a note.
"""

from __future__ import annotations

import bisect
import contextlib
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from .sql_objects import pick_dialect
from .sql_segments import Segment, split_batches, split_statements

NodeKind = Literal["table", "temp", "cte", "derived", "result"]
EdgeKind = Literal["join", "flow"]
Clause = Literal["where", "on", "having"]
OptionalSide = Literal["source", "target", "both"]
Status = Literal["ok", "approximate", "skipped"]

# The roles a column can play, the most telling first: a node shows each column
# with the first role it has.
ROLE_ORDER = (
    "join",
    "on",
    "set",
    "insert",
    "into",
    "where",
    "group by",
    "having",
    "order by",
    "select",
    "apply",
)


@dataclass(frozen=True)
class Span:
    """Offsets in the body, without the frontmatter; end exclusive."""

    start: int
    end: int


@dataclass
class FilterInfo:
    clause: Clause
    predicate: str  # short form without the column: ">= @inicio", "YEAR(…) = 2026"
    full: str  # the whole condition it came from (the whole OR), up to 300 chars
    in_or: bool  # part of a condition with OR
    join_label: str | None  # only for clause "on": "INNER", "LEFT", "MERGE"…
    statement: int
    span: Span


@dataclass
class GraphColumn:
    name: str
    roles: set[str] = field(default_factory=set)
    is_key: bool = False  # used in a join condition
    spans: list[Span] = field(default_factory=list)
    expression: str | None = None  # output columns: "COUNT(p.PedidoID)"
    filters: list[FilterInfo] = field(default_factory=list)


@dataclass
class GraphNode:
    id: str
    kind: NodeKind
    schema: str | None  # "dbo", "db.dbo", "srv.db.dbo"
    name: str  # as shown: "Pedidos", "#pedidos_validos", "@t"; "" for (subquery)
    aliases: list[str] = field(default_factory=list)
    columns: list[GraphColumn] = field(default_factory=list)
    statements: set[int] = field(default_factory=set)
    writes: list[str] = field(default_factory=list)  # INTO INSERT UPDATE DELETE MERGE
    is_base: bool = False  # first source in the FROM of a statement
    spans: list[Span] = field(default_factory=list)
    filters: list[FilterInfo] = field(default_factory=list)  # HAVING, shown below


@dataclass
class GraphEdge:
    kind: EdgeKind
    source: str
    target: str
    source_column: str | None = None
    target_column: str | None = None
    label: str = ""  # "INNER", "LEFT", "CROSS APPLY", "BETWEEN", "WHERE", "INTO"…
    condition: str = ""  # the condition's text, up to 200 chars
    optional_side: OptionalSide | None = None
    statement: int = 0
    span: Span | None = None


@dataclass
class StatementInfo:
    index: int  # 1, 2, 3… in file order
    kind: str  # SELECT, INSERT, UPDATE, DELETE, MERGE; "" for an unreadable batch
    summary: str  # "SELECT … INTO #SystemHealthSessionData"
    line: int
    span: Span
    status: Status
    base: str | None = None  # id of the first source in its FROM


@dataclass
class QueryGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    statements: list[StatementInfo]
    notes: list[str]  # i18n keys: "dynamic_sql", "failed"
    error: str = ""  # first line of the error when the whole analysis failed
    _parts: dict[int, QueryGraph] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def for_statement(self, index: int) -> QueryGraph:
        """The graph of statement *index* alone: its nodes carry only the
        columns, filters and writes of that statement."""
        infos = [s for s in self.statements if s.index == index]
        part = self._parts.get(index)
        if part is None:
            return QueryGraph([], [], infos, list(self.notes), self.error)
        return QueryGraph(
            list(part.nodes), list(part.edges), infos, list(self.notes), self.error
        )


def build_query_graph(body: str, dialect: str = "tsql") -> QueryGraph:
    """The graph of *body*, the SQL without its frontmatter. Never raises."""
    try:
        return _build(body, dialect)
    except Exception as error:
        lines = str(error).strip().splitlines()
        return QueryGraph([], [], [], ["failed"], (lines or [type(error).__name__])[0])


# --- Statements ---------------------------------------------------------------

_SET_OPS: tuple[type[exp.Expression], ...] = (
    (exp.SetOperation,) if hasattr(exp, "SetOperation") else (exp.Union,)
)
_QUERIES: tuple[type[exp.Expression], ...] = (exp.Subquery, exp.Select, *_SET_OPS)
_EXPECTED: dict[str, tuple[type[exp.Expression], ...]] = {
    "SELECT": (exp.Select, *_SET_OPS),
    "INSERT": (exp.Insert,),
    "UPDATE": (exp.Update,),
    "DELETE": (exp.Delete,),
    "MERGE": (exp.Merge,),
}
_EXEC = re.compile(r"(?i)\bEXEC(?:UTE)?\b")


def _build(body: str, dialect: str) -> QueryGraph:
    dialect = pick_dialect(body, dialect)
    found, rejected = split_statements(body)
    batches = dict(split_batches(body)) if rejected else {}
    items: list[tuple[int, Segment | str]] = [(s.start, s) for s in found]
    items += [(offset, batches.get(offset, "")) for offset in rejected]
    items.sort(key=lambda item: item[0])

    infos: list[StatementInfo] = []
    parts: dict[int, _Builder] = {}
    for index, (start, item) in enumerate(items, 1):
        if isinstance(item, str):
            lead = len(item) - len(item.lstrip())
            infos.append(
                StatementInfo(
                    index,
                    "",
                    _first_line(item),
                    body.count("\n", 0, start + lead) + 1,
                    Span(start, start + len(item)),
                    "skipped",
                )
            )
            continue
        info, builder = _statement(index, item, body, dialect)
        infos.append(info)
        if builder is not None:
            parts[index] = builder
    notes = ["dynamic_sql"] if _has_dynamic_sql(body) else []
    return _finish(infos, parts, notes)


def _statement(
    index: int, seg: Segment, body: str, dialect: str
) -> tuple[StatementInfo, _Builder | None]:
    """Parse and walk one statement. The builder is None when it is skipped."""
    line = body.count("\n", 0, seg.start) + 1
    span = Span(seg.start, seg.end)
    text = _blank_xmlnamespaces(seg.text)
    kind = seg.kind
    tree, status = _parse(text, dialect, kind)
    skipped = StatementInfo(index, kind, _first_line(seg.text), line, span, "skipped")
    if tree is None:
        return skipped, None
    if kind == "WITH":
        kind = next(k for k, types in _EXPECTED.items() if isinstance(tree, types))
    builder = _Builder(index, _Text(text, seg.start, dialect), dialect)
    try:
        builder.run(tree)
    except Exception:
        return skipped, None
    if status == "approximate" and not builder.nodes:
        return skipped, None  # nothing could be read from what sqlglot made of it
    summary = builder.summary or _first_line(seg.text)
    return (
        StatementInfo(index, kind, summary, line, span, status, builder.base),
        builder,
    )


def _parse(text: str, dialect: str, kind: str) -> tuple[exp.Expression | None, Status]:
    """Parse strictly; failing that, take what the lenient parser makes of the
    statement when it is still the kind of statement expected."""
    expected = _EXPECTED.get(kind) or tuple(t for ts in _EXPECTED.values() for t in ts)
    attempts: tuple[tuple[sqlglot.ErrorLevel, Status], ...] = (
        (sqlglot.ErrorLevel.RAISE, "ok"),
        (sqlglot.ErrorLevel.IGNORE, "approximate"),
    )
    for level, status in attempts:
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                tree = sqlglot.parse_one(text, read=dialect, error_level=level)
        except Exception:
            continue
        if isinstance(tree, expected):
            return tree, status
    return None, "skipped"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return _clip(" ".join(line.split()), 60)
    return ""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _words(text: str, tokens: list) -> list[str]:
    """Each token's source text, upper-cased with its spacing evened out."""
    return [" ".join(text[t.start : t.end + 1].upper().split()) for t in tokens]


def _has_dynamic_sql(body: str) -> bool:
    """``EXEC(@sql)``, ``EXECUTE ('…')`` or ``EXEC sp_executesql``: SQL built at
    run time, which the diagram cannot see."""
    if not _EXEC.search(body):
        return False
    tokenizer = Dialect.get_or_raise("tsql")
    for _, batch in split_batches(body):
        try:
            words = _words(batch, tokenizer.tokenize(batch))
        except Exception:
            continue
        for i, word in enumerate(words):
            if word not in ("EXEC", "EXECUTE"):
                continue
            if words[i + 1 : i + 2] == ["("]:
                return True
            for name in words[i + 1 : i + 8]:
                if name.strip("[]") == "SP_EXECUTESQL":
                    return True
                if name != "." and not re.fullmatch(r"\[?[\w ]+\]?", name):
                    break
    return False


def _blank_xmlnamespaces(text: str) -> str:
    """Spaces over the ``XMLNAMESPACES (…)`` clause of a statement that opens
    with it, which sqlglot cannot read. The length stays the same, so every
    offset still points at the original text."""
    if "XMLNAMESPACES" not in text.upper():
        return text
    try:
        tokens = Dialect.get_or_raise("tsql").tokenize(text)
    except Exception:
        return text
    words = _words(text, tokens)
    if words[:3] != ["WITH", "XMLNAMESPACES", "("]:
        return text
    depth, j = 0, 2
    while j < len(words):
        depth += {"(": 1, ")": -1}.get(words[j], 0)
        if depth == 0:
            break
        j += 1
    if j >= len(words):
        return text
    if j + 1 < len(words) and words[j + 1] == ",":  # CTEs follow: keep the WITH
        start, end = tokens[1].start, tokens[j + 1].end + 1
    else:
        start, end = tokens[0].start, tokens[j].end + 1
    blank = "".join(c if c in "\r\n" else " " for c in text[start:end])
    return text[:start] + blank + text[end:]


# --- Text and spans -----------------------------------------------------------

# Words that end a condition term. WHEN, THEN and ELSE end one only outside a
# CASE, and never on the way left: a term never starts inside a CASE.
_STOP = frozenset("""
    AND OR HAVING WHERE UNION EXCEPT INTERSECT OPTION WINDOW FOR JOIN INNER LEFT
    RIGHT FULL CROSS OUTER APPLY WHEN THEN ELSE ; , OUTPUT SET FROM SELECT INTO
    ON USING
    """.split()) | {"GROUP BY", "ORDER BY"}
_LEFT_STOP = _STOP - {"WHEN", "THEN", "ELSE"}
_PAREN = {"(": 1, ")": -1}


def _is_word(word: str) -> bool:
    return bool(word) and (word[0].isalpha() or word[0] in '_[@#"`')


class _Text:
    """One statement's text, its tokens and its offset in the body. Turns the
    positions sqlglot keeps on identifiers and literals into spans."""

    def __init__(self, text: str, offset: int, dialect: str) -> None:
        self.text = text
        self.offset = offset
        self.dialect = dialect
        try:
            self.tokens = list(Dialect.get_or_raise(dialect).tokenize(text))
        except Exception:
            self.tokens = []
        self.words = _words(text, self.tokens)
        self.starts = [t.start for t in self.tokens]

    def core(self, *nodes: exp.Expression | None) -> tuple[int, int] | None:
        """The local range, end exclusive, from the first to the last
        identifier, literal or function name under *nodes*."""
        lo = hi = -1
        for node in nodes:
            if not isinstance(node, exp.Expression):
                continue
            for sub in node.walk():
                meta = getattr(sub, "_meta", None)
                if not meta or "start" not in meta or "end" not in meta:
                    continue
                if lo < 0 or meta["start"] < lo:
                    lo = meta["start"]
                hi = max(hi, meta["end"] + 1)
        if lo < 0:
            return None
        while lo > 0 and self.text[lo - 1] == "#":  # sqlglot leaves out the #
            lo -= 1
        return lo, hi

    def span(self, *nodes: exp.Expression | None) -> Span | None:
        found = self.core(*nodes)
        if found is None:
            return None
        return self._absolute(*self._balance(*found))

    def keyword(self, word: str, node: exp.Expression) -> Span | None:
        """From the *word* before *node*, with the CROSS or OUTER before it,
        to the end of *node*: ``CROSS APPLY a.x.nodes('/e') AS T(C)``."""
        found = self.core(node)
        if found is None:
            return None
        start, end = found
        k = bisect.bisect_right(self.starts, start) - 1
        while k > 0 and self.words[k] != word:
            k -= 1
        if k < 0 or self.words[k] != word:
            return self._absolute(*self._balance(start, end))
        if k > 0 and self.words[k - 1] in ("CROSS", "OUTER"):
            k -= 1
        start = self.tokens[k].start
        return self._absolute(*self._balance(start, end))

    def find(self, pattern: str) -> Span | None:
        """The first match of *pattern*, for what sqlglot keeps no position
        of, such as an ``@variable``."""
        match = re.search(pattern, self.text, re.IGNORECASE)
        return None if match is None else self._absolute(match.start(), match.end())

    def term(self, node: exp.Expression) -> Span | None:
        """The span of the whole condition term *node*: its leaves widened to
        the parentheses, functions and operators around them."""
        found = self.core(node)
        if found is None:
            return None
        if not self.tokens:
            return self._absolute(*found)
        return self._absolute(*self._widen(*found))

    def source(self, node: exp.Expression, limit: int) -> str:
        """The text of *node* as written, on one line and without the
        parentheses around it."""
        if not isinstance(node, exp.Expression):
            return ""
        span = self.term(node)
        if span is None:
            return _clip(node.sql(self.dialect), limit)
        return self.snippet(span, limit)

    def snippet(self, span: Span, limit: int) -> str:
        """The text under *span*, on one line and without the parentheses
        around it."""
        text = self.text[span.start - self.offset : span.end - self.offset]
        return _clip(_strip_parens(" ".join(text.split())), limit)

    def dotted(self, span: Span) -> tuple[list[str], Span]:
        """The ``.name`` parts written right after *span*, and the span
        widened over them."""
        names: list[str] = []
        k = bisect.bisect_left(self.starts, span.end - self.offset)
        end = span.end
        while k + 1 < len(self.words) and self.words[k] == ".":
            if not _is_word(self.words[k + 1]):
                break
            names.append(self.tokens[k + 1].text)
            end = self.offset + self.tokens[k + 1].end + 1
            k += 2
        return names, Span(span.start, end)

    def _absolute(self, start: int, end: int) -> Span:
        return Span(self.offset + start, self.offset + end)

    def _balance(self, start: int, end: int) -> tuple[int, int]:
        """*start* and *end* moved out until the parentheses between them pair
        up: ``(p.Valor) > 0``, ``dbo.fn(a.id)``."""
        k = bisect.bisect_left(self.starts, start)
        depth = low = 0
        i = k
        while i < len(self.tokens) and self.tokens[i].start < end:
            depth += _PAREN.get(self.words[i], 0)
            low = min(low, depth)
            i += 1
        owed = -low
        while low < 0 and k > 0:
            k -= 1
            low += _PAREN.get(self.words[k], 0)
        depth += owed + low  # the opening parentheses taken on the left
        while depth > 0 and i < len(self.tokens):
            depth += _PAREN.get(self.words[i], 0)
            end = self.tokens[i].end + 1
            i += 1
        if k < len(self.tokens) and self.tokens[k].start < start:
            start = self.tokens[k].start
        return start, end

    def _widen(self, start: int, end: int) -> tuple[int, int]:
        words, tokens = self.words, self.tokens
        k = max(0, bisect.bisect_right(self.starts, start) - 1)
        depth = case = 0
        while k > 0:
            word = words[k - 1]
            if word == ")":
                depth += 1
            elif word == "(":
                if depth:
                    depth -= 1
                else:
                    before = words[k - 2] if k >= 2 else ""
                    if before in ("LEFT", "RIGHT"):  # the functions
                        k -= 2
                        continue
                    if not _is_word(before) or before in _STOP:
                        break
            elif word == "END":
                case += 1
            elif word == "CASE":
                case = max(0, case - 1)
            elif depth == 0 and case == 0 and word in _LEFT_STOP:
                break
            k -= 1
        last = k
        depth = case = 0
        between = False
        for i in range(k, len(words)):
            word = words[i]
            if word == "(":
                depth += 1
            elif word == ")":
                if depth == 0:
                    break
                depth -= 1
            elif word == "CASE":
                case += 1
            elif word == "END" and case:
                case -= 1
            elif depth == 0 and case == 0:
                if word == "BETWEEN":
                    between = True
                elif word == "AND" and between:
                    between = False
                elif word in _STOP and tokens[i].start >= end:
                    break
            last = i
        return self._balance(tokens[k].start, max(end, tokens[last].end + 1))


def _strip_parens(text: str) -> str:
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for i, char in enumerate(text):
            depth += {"(": 1, ")": -1}.get(char, 0)
            if depth == 0 and i < len(text) - 1:
                return text  # the first parenthesis closes before the end
        text = text[1:-1].strip()
    return text


# --- Walking a statement ------------------------------------------------------

_XML_METHODS = frozenset({"value", "query", "nodes", "exist", "modify"})
_OPS: dict[type[exp.Expression], str] = {
    exp.EQ: "=",
    exp.NEQ: "<>",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
}
_FLIP = {"=": "=", "<>": "<>", ">": "<", "<": ">", ">=": "<=", "<=": ">="}


@dataclass
class _Apply:
    """The derived node of an APPLY, which outer columns used in its body
    link to."""

    node: str
    label: str
    optional: OptionalSide | None
    condition: str
    span: Span | None
    found: bool = False
    seen: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class _Block:
    """One SELECT (or the FROM of an UPDATE, DELETE or MERGE) and the sources
    its columns can name."""

    parent: _Block | None = None
    sources: dict[str, str] = field(default_factory=dict)  # casefold name → node
    order: list[str] = field(default_factory=list)  # nodes in FROM order
    spans: dict[str, Span | None] = field(default_factory=dict)  # node → reference
    base: str | None = None
    corr: str = ""  # label of edges to outer columns: EXISTS, IN, WHERE
    apply: _Apply | None = None
    sink: str | None = None  # where its rows go
    aliases: frozenset[str] = frozenset()  # output aliases, for ORDER BY


@dataclass
class _Ref:
    """A column reference resolved to its node."""

    node: str
    column: str
    span: Span | None
    expr: exp.Expression
    outer: bool = False
    apply: _Apply | None = None


def _arg(node: exp.Expression, *names: str) -> exp.Expression | None:
    """The first of *names* set on *node*: sqlglot renamed ``from`` and
    ``with`` to ``from_`` and ``with_``."""
    for name in names:
        value = node.args.get(name)
        if value is not None:
            return value
    return None


def _ident(identifier: exp.Expression) -> str:
    """A name with the # or ## that sqlglot keeps apart as flags."""
    name = identifier.name
    if identifier.args.get("global_") or identifier.args.get("global"):
        return "##" + name
    if identifier.args.get("temporary"):
        return "#" + name
    return name


def _leftmost(query: exp.Expression | None) -> exp.Select | None:
    while query is not None and isinstance(query, (exp.Subquery, *_SET_OPS)):
        query = query.args.get("this")
    return query if isinstance(query, exp.Select) else None


def _names(table: exp.Table) -> list[str] | None:
    """The parts of a table's name with their # marks; None when a part is
    not a plain name."""
    names: list[str] = []
    for part in table.parts:
        if not isinstance(part, exp.Identifier):
            return None
        names.append(_ident(part))
    return names or None


def _is_assignment(node: exp.Expression) -> bool:
    return isinstance(node, exp.EQ) and isinstance(node.this, exp.Parameter)


def _outputs(query: exp.Expression, dialect: str) -> list[tuple[str, str]]:
    """Name and expression of each column the query returns."""
    select = _leftmost(query)
    if select is None:
        return []
    found: list[tuple[str, str]] = []
    for item in select.expressions:
        if _is_assignment(item):
            continue
        if isinstance(item, exp.Star) or (
            isinstance(item, exp.Column) and isinstance(item.this, exp.Star)
        ):
            found.append(("*", item.sql(dialect)))
            continue
        inner = item.this if isinstance(item, exp.Alias) else item
        if not isinstance(inner, exp.Expression):
            continue
        expression = inner.sql(dialect)
        try:
            name = item.alias_or_name
        except AttributeError:  # a function the lenient parser left empty
            name = ""
        found.append((name or expression, expression))
    return found


def _alias_columns(node: exp.Expression) -> list[str]:
    alias = node.args.get("alias")
    if isinstance(alias, exp.TableAlias):
        return [c.name for c in alias.columns]
    return []


def _alias_name(node: exp.Expression) -> exp.Expression | None:
    """The name in an alias, without the column list of ``T(C)``."""
    alias = node.args.get("alias")
    if isinstance(alias, exp.TableAlias):
        return alias.args.get("this")
    return alias


def _collect(
    node: exp.Expression,
) -> tuple[list[exp.Expression], list[exp.Expression]]:
    """The column references under *node*, in order, and the subqueries it
    holds, which are walked as blocks of their own. An XML method call like
    ``a.XMLDATA.nodes('/x')`` holds no Column: it counts as a reference to
    ``a.XMLDATA``."""
    columns: list[exp.Expression] = []
    queries: list[exp.Expression] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, exp.Expression):
            continue  # what the lenient parser leaves in broken SQL
        if isinstance(current, _QUERIES):
            queries.append(current)
            continue
        if isinstance(current, exp.Column):
            columns.append(current)
            continue
        children = list(current.iter_expressions())
        if isinstance(current, exp.Dot):
            method = current.expression
            if isinstance(method, exp.Func) and method.name.lower() in _XML_METHODS:
                columns.append(current)
                children = [
                    c
                    for c in method.iter_expressions()
                    if isinstance(c, exp.Expression)
                ]
        stack.extend(reversed(children))
    return columns, queries


def _conjuncts(condition: exp.Expression) -> list[exp.Expression]:
    found: list[exp.Expression] = []
    stack = [condition]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Paren):
            stack.append(node.this)
        elif isinstance(node, exp.And):
            stack.extend((node.expression, node.this))
        else:
            found.append(node)
    return found


def _disjuncts(condition: exp.Expression) -> list[exp.Expression]:
    found: list[exp.Expression] = []
    stack = [condition]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Paren) and isinstance(node.this, exp.Or):
            stack.append(node.this)
        elif isinstance(node, exp.Or):
            stack.extend((node.expression, node.this))
        else:
            found.append(node.this if isinstance(node, exp.Paren) else node)
    return found


def _unwrap(term: exp.Expression) -> tuple[bool, exp.Expression]:
    """The term without NOT and parentheses, and whether it was negated."""
    negated = False
    while isinstance(term, (exp.Not, exp.Paren)):
        negated ^= isinstance(term, exp.Not)
        term = term.this
    return negated, term


def _side(node: exp.Expression, term: exp.Expression) -> str | None:
    """Which argument of *term* holds *node*: "this", "expression"…"""
    while isinstance(node.parent, exp.Expression) and node.parent is not term:
        node = node.parent
    return node.arg_key if node.parent is term else None


def _predicate(term: exp.Expression, column: exp.Expression, dialect: str) -> str:
    """The short form of a filter on *column*, without the column: ``>=
    @inicio``, ``NOT IN ('C', 'D')``, ``YEAR(…) = 2026``. Empty when the term
    has no short form."""
    negated, core = _unwrap(term)
    if core.args.get("negate"):
        negated = not negated
    side = _side(column, core)
    no = "NOT " if negated else ""
    if isinstance(core, exp.Is) and side == "this":
        if isinstance(core.expression, exp.Null):
            return f"IS {no}NULL"
        return ""
    if isinstance(core, exp.In) and side == "this" and not core.args.get("query"):
        items = [i.sql(dialect) for i in core.expressions[:3]]
        if len(core.expressions) > 3:
            items.append("…")
        return f"{no}IN ({', '.join(items)})"
    if isinstance(core, exp.Between) and side == "this":
        low, high = core.args.get("low"), core.args.get("high")
        if low is None or high is None:
            return ""
        return f"{no}BETWEEN {low.sql(dialect)} AND {high.sql(dialect)}"
    op = _OPS.get(type(core))
    if op is None or side not in ("this", "expression"):
        return ""
    if op == "LIKE":
        if side != "this":
            return ""
        op = f"{no}LIKE"
    elif negated:
        return ""
    elif side == "expression":
        op = _FLIP[op]
    mine = core.this if side == "this" else core.expression
    other = core.expression if side == "this" else core.this
    if not isinstance(other, exp.Expression):
        return ""
    while isinstance(mine, exp.Paren):
        mine = mine.this
    if mine is column:
        return f"{op} {other.sql(dialect)}"
    if isinstance(mine, exp.Func) and not isinstance(mine, exp.Dot):
        name = mine.sql(dialect).split("(")[0]
        return f"{name}(…) {op} {other.sql(dialect)}"
    return ""


class _Builder:
    """Walks one statement into nodes and edges."""

    def __init__(self, index: int, text: _Text, dialect: str) -> None:
        self.index = index
        self.text = text
        self.dialect = dialect
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.flows: set[tuple[str, str, str]] = set()
        self.compared: set[tuple[str, str, str]] = set()
        self.ctes: dict[str, str] = {}  # casefold name → node
        self.declared: dict[str, set[str]] = {}  # node → casefold column names
        self.seq: dict[str, int] = {}  # node → when it was first a source
        self.base: str | None = None
        self.summary = ""

    # Nodes and edges

    def node(
        self, nid: str, kind: NodeKind, name: str, schema: str | None = None
    ) -> GraphNode:
        node = self.nodes.get(nid)
        if node is None:
            node = self.nodes[nid] = GraphNode(nid, kind, schema, name)
        node.statements.add(self.index)
        return node

    @staticmethod
    def column(node: GraphNode, name: str) -> GraphColumn:
        folded = name.casefold()
        for column in node.columns:
            if column.name.casefold() == folded:
                return column
        column = GraphColumn(name)
        node.columns.append(column)
        return column

    def edge(
        self,
        source: str,
        target: str,
        source_column: str | None,
        target_column: str | None,
        label: str,
        condition: str,
        optional: OptionalSide | None,
        span: Span | None,
    ) -> None:
        if source != target:
            self.edges.append(
                GraphEdge(
                    "join",
                    source,
                    target,
                    source_column,
                    target_column,
                    label,
                    condition,
                    optional,
                    self.index,
                    span,
                )
            )

    def flow(
        self,
        source: str,
        target: str,
        label: str,
        span: Span | None,
        condition: str = "",
    ) -> None:
        key = (source, target, label)
        if source != target and key not in self.flows:
            self.flows.add(key)
            self.edges.append(
                GraphEdge(
                    "flow",
                    source,
                    target,
                    label=label,
                    condition=condition,
                    statement=self.index,
                    span=span,
                )
            )

    def write(self, nid: str, what: str) -> None:
        writes = self.nodes[nid].writes
        if what not in writes:
            writes.append(what)

    def touch(self, ref: _Ref, role: str, key: bool = False) -> None:
        """Record that *ref*'s column plays *role*. A column of an outer query
        used inside an APPLY body plays "apply" instead, and links to the
        APPLY's node."""
        if ref.apply is not None:
            apply = ref.apply
            role, key = "apply", False
            apply.found = True
            mark = (ref.node, ref.column.casefold())
            if mark not in apply.seen:
                apply.seen.add(mark)
                self.edge(
                    ref.node,
                    apply.node,
                    ref.column,
                    None,
                    apply.label,
                    apply.condition,
                    apply.optional,
                    apply.span,
                )
        column = self.column(self.nodes[ref.node], ref.column)
        column.roles.add(role)
        column.is_key = column.is_key or key
        if ref.span is not None and ref.span not in column.spans:
            column.spans.append(ref.span)

    def display(self, nid: str | None) -> str:
        node = self.nodes.get(nid or "")
        if node is None:
            return ""
        name = node.name or "(subquery)"
        return f"{node.schema}.{name}" if node.schema else name

    # Sources

    def table_node(self, table: exp.Table) -> str | None:
        """The node a table name stands for: a table, a temporary table, a
        table variable or a CTE. None for a table-valued function."""
        this = table.this
        if isinstance(this, exp.Parameter):
            return self.variable(this.name)
        if isinstance(this, exp.Func) or (
            isinstance(this, exp.Dot) and isinstance(this.expression, exp.Func)
        ):
            return None
        names = _names(table)
        return None if names is None else self.named_node(names)

    def variable(self, name: str) -> str:
        """A table variable, ``@t``."""
        return self.node(f"@{name}".casefold(), "temp", f"@{name}").id

    def named_node(self, names: list[str]) -> str:
        display = names[-1]
        if display.startswith("#"):
            return self.node(display.casefold(), "temp", display).id
        if len(names) == 1 and display.casefold() in self.ctes:
            return self.node(self.ctes[display.casefold()], "cte", display).id
        nid = ".".join(names).casefold()
        return self.node(nid, "table", display, ".".join(names[:-1]) or None).id

    def table_span(self, table: exp.Table) -> Span | None:
        if isinstance(table.this, exp.Parameter):
            return self.text.find(rf"@{re.escape(table.this.name)}\b")
        return self.text.span(
            table.this, table.args.get("db"), table.args.get("catalog")
        )

    def derived(self, alias: str) -> str:
        label = alias.casefold() if alias else "(subquery)"
        nid = f"derived:{self.index}:{label}"
        n = 2
        while nid in self.nodes:
            nid = f"derived:{self.index}:{label}#{n}"
            n += 1
        return self.node(nid, "derived", alias).id

    def enter(
        self,
        block: _Block,
        nid: str,
        names: list[str],
        alias: str,
        span: Span | None,
    ) -> None:
        """Make *nid* a source of *block*, named by its alias or else by its
        names."""
        for name in [alias] if alias else names:
            if name:
                block.sources[name.casefold()] = nid
        block.order.append(nid)
        block.spans.setdefault(nid, span)
        if block.base is None:
            block.base = nid
        self.seq.setdefault(nid, len(self.seq))
        node = self.nodes[nid]
        if span is not None and span not in node.spans:
            node.spans.append(span)
        if alias and node.name.casefold() != alias.casefold():
            if alias.casefold() not in (a.casefold() for a in node.aliases):
                node.aliases.append(alias)

    def add_source(self, source: exp.Expression | None, block: _Block) -> str | None:
        """Register a FROM or JOIN item in *block* and walk what it holds."""
        nid: str | None = None
        if isinstance(source, exp.Table):
            nid = self.table_source(source, block)
        elif isinstance(source, exp.Lateral):
            nid = self.apply(source, block)
        elif isinstance(source, (exp.Subquery, exp.Values)):
            alias = source.alias
            nid = self.derived(alias)
            outputs: list[str] = []
            if isinstance(source, exp.Subquery):
                self.query(source.this, block.parent, nid, "")
                outputs = [name for name, _ in _outputs(source.this, self.dialect)]
            declared = _alias_columns(source) or outputs
            self.declared[nid] = {c.casefold() for c in declared}
            self.enter(block, nid, [], alias, self.text.span(_alias_name(source)))
        if source is not None:
            for join in source.args.get("joins") or []:
                self.join(join, block)
        return nid

    def table_source(self, table: exp.Table, block: _Block) -> str | None:
        if table.this is None:  # a lenient parse left an empty FROM
            return None
        alias = table.alias
        span = self.table_span(table)
        nid = self.table_node(table)
        if nid is None:  # a table-valued function
            func = table.this
            if isinstance(func, exp.Dot):
                func = func.expression
            name = func.name if isinstance(func, exp.Expression) else ""
            nid = self.derived(alias or name)
            self.declared[nid] = {c.casefold() for c in _alias_columns(table)}
            self.enter(block, nid, [], alias, span)
            return nid
        if alias and nid in block.order:  # a self-join: one node per alias
            node = self.nodes[nid]
            nid = self.node(
                f"{nid} as {alias.casefold()}", node.kind, node.name, node.schema
            ).id
        names = [] if alias else [self.nodes[nid].name, ".".join(_names(table) or [])]
        self.enter(block, nid, names, alias, span)
        return nid

    def apply(self, lateral: exp.Lateral, block: _Block) -> str:
        """A CROSS or OUTER APPLY: a derived node, linked to the outer columns
        its body or function uses."""
        cross = bool(lateral.args.get("cross_apply"))
        label = "CROSS APPLY" if cross else "OUTER APPLY"
        alias = lateral.alias
        inner = lateral.this
        name = alias
        if not name and isinstance(inner, exp.Dot):
            name = inner.expression.name
        elif not name and isinstance(inner, exp.Func):
            name = inner.name
        nid = self.derived(name)
        span = self.text.keyword("APPLY", lateral)
        condition = (
            self.text.snippet(span, 200)
            if span is not None
            else _clip(lateral.sql(self.dialect), 200)
        )
        apply = _Apply(nid, label, None if cross else "target", condition, span)
        declared = _alias_columns(lateral)
        if isinstance(inner, exp.Subquery):
            self.query(inner.this, block, nid, "", apply=apply)
            declared = declared or [n for n, _ in _outputs(inner.this, self.dialect)]
        elif inner is not None:
            columns, queries = _collect(inner)
            for found in columns:
                ref = self.resolve(found, block)
                if ref is not None:
                    ref.apply = apply
                    self.touch(ref, "apply")
            for query in queries:
                self.query(query, block, None, "", corr="WHERE")
        if not apply.found and block.base is not None:
            self.edge(
                block.base,
                nid,
                None,
                None,
                label,
                apply.condition,
                apply.optional,
                span,
            )
        self.declared[nid] = {c.casefold() for c in declared}
        self.enter(block, nid, [], alias, self.text.span(_alias_name(lateral)) or span)
        return nid

    def join(self, join: exp.Join, block: _Block) -> None:
        target = join.this
        nid = self.add_source(target, block)
        if nid is None or isinstance(target, exp.Lateral):
            return
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        label, optional = _join_label(side, kind)
        on = join.args.get("on")
        if on is not None:
            condition = self.text.source(on, 200)
            self.condition(on, block, "on", label, optional, nid, condition)
        elif (side or kind) and block.base is not None:
            span = block.spans.get(nid)
            self.edge(block.base, nid, None, None, label, "", optional, span)
        # A comma join has no ON: its WHERE links the tables, labelled WHERE.

    # Columns

    def lookup(self, key: str, block: _Block) -> tuple[str, bool, _Apply | None] | None:
        """The node a qualifier names, searching outward from *block*."""
        current: _Block | None = block
        apply: _Apply | None = None
        outer = False
        while current is not None:
            nid = current.sources.get(key)
            if nid is not None:
                return nid, outer, apply
            if current.apply is not None:
                apply = current.apply
            current = current.parent
            outer = True
        return None

    def owner(self, name: str, block: _Block) -> str | None:
        """The node an unqualified column belongs to: the block's only source,
        or the only one known to have that column."""
        sources = list(dict.fromkeys(block.order))
        if len(sources) == 1:
            return sources[0]
        folded = name.casefold()
        hits = [s for s in sources if folded in self.declared.get(s, ())]
        return hits[0] if len(hits) == 1 else None

    def resolve(self, ref: exp.Expression, block: _Block) -> _Ref | None:
        qualifier: list[str]
        if isinstance(ref, exp.Dot):  # an XML method on a column
            target = ref.this
            if (
                isinstance(target, exp.Dot)
                and isinstance(target.this, exp.Identifier)
                and isinstance(target.expression, exp.Identifier)
            ):
                qualifier, name = [_ident(target.this)], target.expression.name
            elif isinstance(target, exp.Identifier):
                qualifier, name = [], target.name
            elif isinstance(target, exp.Column):
                qualifier = [_ident(p) for p in target.parts[:-1]]
                name = target.name
            else:
                return None
            span = self.text.span(target)
        elif isinstance(ref, exp.Column):
            qualifier = [_ident(p) for p in ref.parts[:-1]]
            name = "*" if isinstance(ref.this, exp.Star) else ref.name
            span = self.text.span(ref)
        else:
            return None
        if not name:
            return None
        if not qualifier:
            nid = self.owner(name, block)
            return None if nid is None else _Ref(nid, name, span, ref)
        found = self.lookup(".".join(qualifier).casefold(), block)
        if found is None and len(qualifier) > 1:
            found = self.lookup(qualifier[-1].casefold(), block)
        if found is None:
            return None
        nid, outer, apply = found
        return _Ref(nid, name, span, ref, outer, apply)

    def walk(
        self,
        node: exp.Expression,
        block: _Block,
        role: str,
        sink: str | None = None,
        label: str = "",
        skip: frozenset[str] = frozenset(),
    ) -> None:
        """Give every column under *node* the role *role*; subqueries are
        walked as blocks that feed *sink*."""
        columns, queries = _collect(node)
        for found in columns:
            if (
                skip
                and isinstance(found, exp.Column)
                and not found.table
                and found.name.casefold() in skip
            ):
                continue  # ORDER BY naming an output alias
            ref = self.resolve(found, block)
            if ref is not None:
                self.touch(ref, role)
        for query in queries:
            self.query(query, block, sink, label, corr="WHERE")

    def refs(self, node: exp.Expression, block: _Block) -> list[_Ref]:
        columns, queries = _collect(node)
        for query in queries:
            self.query(query, block, None, "", corr="WHERE")
        found = (self.resolve(c, block) for c in columns)
        return [r for r in found if r is not None]

    # Queries

    def with_ctes(self, node: exp.Expression) -> None:
        with_ = _arg(node, "with_", "with")
        if with_ is None:
            return
        for cte in with_.expressions:
            name = cte.alias
            if not name:
                continue
            nid = f"cte:{self.index}:{name.casefold()}"
            self.node(nid, "cte", name)
            self.ctes[name.casefold()] = nid  # before the body: it may recurse
            span = self.text.span(_alias_name(cte))
            if span is not None:
                self.nodes[nid].spans.append(span)
            declared = _alias_columns(cte) or [
                n for n, _ in _outputs(cte.this, self.dialect)
            ]
            self.declared[nid] = {c.casefold() for c in declared}
            self.query(cte.this, None, nid, "")

    def query(
        self,
        query: exp.Expression,
        parent: _Block | None,
        sink: str | None,
        label: str,
        corr: str = "",
        apply: _Apply | None = None,
    ) -> _Block | None:
        """Walk a query whose rows go to *sink*. Returns the block of its first
        SELECT."""
        if isinstance(query, exp.Subquery):
            return self.query(query.this, parent, sink, label, corr, apply)
        self.with_ctes(query)
        if isinstance(query, _SET_OPS):
            first = self.query(query.this, parent, sink, label, corr, apply)
            self.query(query.expression, parent, sink, label, corr, apply)
            return first
        if isinstance(query, exp.Select):
            return self.select(query, parent, sink, label, corr, apply)
        return None

    def select(
        self,
        select: exp.Select,
        parent: _Block | None,
        sink: str | None,
        label: str,
        corr: str,
        apply: _Apply | None,
    ) -> _Block:
        block = _Block(parent=parent, corr=corr, apply=apply, sink=sink)
        source = _arg(select, "from_", "from")
        if source is not None:
            self.add_source(source.this, block)
        for join in select.args.get("joins") or []:
            self.join(join, block)
        block.aliases = frozenset(
            e.alias.casefold() for e in select.expressions if isinstance(e, exp.Alias)
        )
        for item in select.expressions:
            if _is_assignment(item):  # SELECT @x = col
                item = item.expression
            self.walk(item, block, "select", sink, label)
        where = select.args.get("where")
        if where is not None:
            self.condition(where.this, block, "where", "WHERE", None, None)
        group = select.args.get("group")
        if group is not None:
            for item in group.expressions:
                self.walk(item, block, "group by", sink, label)
        having = select.args.get("having")
        if having is not None:
            self.condition(having.this, block, "having", "", None, None)
        order = select.args.get("order")
        if order is not None:
            for item in order.expressions:
                self.walk(item, block, "order by", sink, label, block.aliases)
        if sink is not None:
            for nid in dict.fromkeys(block.order):
                self.flow(nid, sink, label, block.spans.get(nid))
        return block

    # Conditions

    def condition(
        self,
        condition: exp.Expression,
        block: _Block,
        clause: Clause,
        label: str,
        optional: OptionalSide | None,
        joined: str | None,
        text: str = "",
        merge: bool = False,
    ) -> None:
        """Split a WHERE, ON or HAVING into its AND terms: the ones that link
        two nodes become edges, the others filters."""
        if not isinstance(condition, exp.Expression):
            return
        for term in _conjuncts(condition):
            if clause == "having":
                self.having(term, block)
                continue
            negated, core = _unwrap(term)
            if isinstance(core, exp.Exists):
                corr = "NOT EXISTS" if negated else "EXISTS"
                self.query(core.this, block, None, "", corr=corr)
            elif isinstance(core, exp.In) and core.args.get("query") is not None:
                self.in_query(core, "NOT IN" if negated else "IN", term, block)
            elif isinstance(term, exp.Or):
                full = self.text.source(term, 300)
                join_label = label if clause == "on" else None
                for part in _disjuncts(term):
                    refs = self.refs(part, block)
                    self.filters(part, refs, clause, join_label, True, full)
            else:
                refs = self.refs(term, block)
                if not refs:
                    continue
                if any(r.apply is not None for r in refs):
                    for ref in refs:  # inside an APPLY: the link is the APPLY
                        self.touch(ref, "join" if clause == "on" else clause)
                    continue
                if self.link(
                    term, refs, block, clause, label, optional, joined, text, merge
                ):
                    continue
                self.filters(term, refs, clause, label if clause == "on" else None)

    def link(
        self,
        term: exp.Expression,
        refs: list[_Ref],
        block: _Block,
        clause: Clause,
        label: str,
        optional: OptionalSide | None,
        joined: str | None,
        text: str,
        merge: bool,
    ) -> bool:
        """Make *term* an edge when it compares the columns of two nodes, one
        on each side. False when it is a filter instead."""
        if len({r.node for r in refs}) != 2:
            return False
        negated, core = _unwrap(term)
        if negated and not isinstance(core, exp.Like):
            return False
        left = [r for r in refs if _side(r.expr, core) == "this"]
        right = [r for r in refs if r not in left]
        if not left or not right:
            return False
        a, b = {r.node for r in left}, {r.node for r in right}
        if len(a) != 1 or len(b) != 1 or a == b:
            return False
        if merge:  # MERGE's ON: no edge, the MERGE flow carries it
            if not isinstance(core, exp.EQ):
                return False
            for ref in refs:
                self.touch(ref, "on", key=True)
            return True
        if isinstance(core, exp.EQ):
            op = ""
        elif isinstance(core, exp.Between):
            op = "NOT BETWEEN" if negated else "BETWEEN"
        elif isinstance(core, exp.Like):
            op = "NOT LIKE" if negated or core.args.get("negate") else "LIKE"
        elif type(core) in _OPS:
            op = _OPS[type(core)]
        else:
            return False
        source, target = self.orient(left, right, joined)
        if any(r.outer for r in refs):
            label = block.corr or label
        condition = text or self.text.source(term, 200)
        span = self.text.term(term)
        for ref in refs:
            self.touch(ref, "join", key=True)
        if op:
            if (source[0].node, target[0].node, op) not in self.compared:
                self.compared.add((source[0].node, target[0].node, op))
                self.edge(
                    source[0].node,
                    target[0].node,
                    source[0].column,
                    target[0].column,
                    op,
                    condition,
                    optional,
                    span,
                )
            return True
        for s in source:
            for t in target:
                self.edge(
                    s.node, t.node, s.column, t.column, label, condition, optional, span
                )
        return True

    def orient(
        self, left: list[_Ref], right: list[_Ref], joined: str | None
    ) -> tuple[list[_Ref], list[_Ref]]:
        """Edges point at the joined table, from an outer query into a
        subquery, or else from the table met first."""
        if joined == left[0].node:
            return right, left
        if joined == right[0].node:
            return left, right
        left_outer, right_outer = any(r.outer for r in left), any(
            r.outer for r in right
        )
        if left_outer != right_outer:
            return (left, right) if left_outer else (right, left)
        if self.seq.get(left[0].node, 0) <= self.seq.get(right[0].node, 0):
            return left, right
        return right, left

    def in_query(
        self, core: exp.In, label: str, term: exp.Expression, block: _Block
    ) -> None:
        """``x IN (SELECT y FROM t)``: an edge from x to t.y."""
        query = core.args["query"]
        inner = self.query(query, block, None, "", corr=label)
        refs = self.refs(core.this, block)
        for ref in refs:
            self.touch(ref, "where", key=True)
        if not refs or inner is None:
            return
        first: _Ref | None = None
        select = _leftmost(query)
        if select is not None and select.expressions:
            columns, _ = _collect(select.expressions[0])
            found = (self.resolve(c, inner) for c in columns)
            first = next((r for r in found if r is not None), None)
        if first is not None:
            self.touch(first, "select", key=True)
        target = first.node if first is not None else inner.base
        if target is not None:
            self.edge(
                refs[0].node,
                target,
                refs[0].column,
                first.column if first is not None else None,
                label,
                self.text.source(term, 200),
                None,
                self.text.term(term),
            )

    def filters(
        self,
        term: exp.Expression,
        refs: list[_Ref],
        clause: Clause,
        join_label: str | None,
        in_or: bool = False,
        full: str = "",
    ) -> None:
        """One filter on each column *term* cites."""
        text = self.text.source(term, 300)
        span = self.text.term(term)
        seen: set[tuple[str, str]] = set()
        for ref in refs:
            self.touch(ref, clause)
            key = (ref.node, ref.column.casefold())
            if key in seen:
                continue
            seen.add(key)
            predicate = _predicate(term, ref.expr, self.dialect) or _clip(text, 60)
            where = span or ref.span or Span(self.text.offset, self.text.offset)
            self.column(self.nodes[ref.node], ref.column).filters.append(
                FilterInfo(
                    clause,
                    predicate,
                    full or text,
                    in_or,
                    join_label,
                    self.index,
                    where,
                )
            )

    def having(self, term: exp.Expression, block: _Block) -> None:
        """A HAVING term filters groups, not a column: it goes below the node
        the rows go to."""
        for ref in self.refs(term, block):
            self.touch(ref, "having")
        if block.sink is None:
            return
        text = self.text.source(term, 300)
        span = self.text.term(term) or Span(self.text.offset, self.text.offset)
        self.nodes[block.sink].filters.append(
            FilterInfo("having", text, text, False, None, self.index, span)
        )

    # Statements

    def run(self, tree: exp.Expression) -> None:
        if isinstance(tree, (exp.Select, *_SET_OPS)):
            self.select_statement(tree)
        elif isinstance(tree, exp.Insert):
            self.insert(tree)
        elif isinstance(tree, exp.Update):
            self.update(tree)
        elif isinstance(tree, exp.Delete):
            self.delete(tree)
        elif isinstance(tree, exp.Merge):
            self.merge(tree)
        if self.base is not None and self.base in self.nodes:
            self.nodes[self.base].is_base = True

    def target(self, table: exp.Expression | None, what: str) -> str | None:
        if not isinstance(table, exp.Table):
            return None
        nid = self.table_node(table)
        if nid is None:
            return None
        self.write(nid, what)
        span = self.table_span(table)
        node = self.nodes[nid]
        if span is not None and span not in node.spans:
            node.spans.append(span)
        return nid

    def select_statement(self, query: exp.Expression) -> None:
        first = _leftmost(query)
        into = first.args.get("into") if first is not None else None
        target = self.target(into.this if into is not None else None, "INTO")
        result: str | None = None
        if target is not None:
            sink, label = target, "INTO"
        elif first is not None and any(_is_assignment(e) for e in first.expressions):
            sink, label = None, ""  # SELECT @x = …: no rows come out
        else:
            result = sink = f"result:{self.index}"
            node = self.node(result, "result", "SELECT")
            for name, expression in _outputs(query, self.dialect):
                node.columns.append(
                    GraphColumn(name, {"select"}, expression=expression)
                )
            label = ""
        block = self.query(query, None, sink, label)
        self.base = block.base if block is not None else None
        if target is not None:
            for name, expression in _outputs(query, self.dialect):
                column = self.column(self.nodes[target], name)
                column.roles.add("into")
                column.expression = column.expression or expression
            self.summary = f"SELECT … INTO {self.display(target)}"
        else:
            base = self.display(self.base)
            self.summary = f"SELECT … FROM {base}" if base else "SELECT …"
        if result is not None:
            node = self.nodes.pop(result)
            if any(e.target == result for e in self.edges):
                self.nodes[result] = node  # last in order

    def insert(self, insert: exp.Insert) -> None:
        self.with_ctes(insert)
        table = insert.this
        names: list[exp.Expression] = []
        if isinstance(table, exp.Schema):
            names = list(table.expressions)
            table = table.this
        target = self.target(table, "INSERT")
        if target is None:
            return
        self.summary = f"INSERT INTO {self.display(target)}"
        for column in names:
            ref = _Ref(target, column.name, self.text.span(column), column)
            self.touch(ref, "insert")
        source = insert.args.get("expression")
        block: _Block | None = None
        if isinstance(source, _QUERIES):
            block = self.query(source, None, target, "INSERT")
            if not names:
                for name, _ in _outputs(source, self.dialect):
                    self.column(self.nodes[target], name).roles.add("insert")
        self.base = block.base if block is not None else None
        self.output(insert, target, block)

    def update(self, update: exp.Update) -> None:
        self.with_ctes(update)
        block = _Block()
        source = _arg(update, "from_", "from")
        if source is not None:
            self.add_source(source.this, block)
        for join in update.args.get("joins") or []:
            self.join(join, block)
        target = self.find(update.this, block)
        if target is None:
            target = self.add_source(update.this, block)
        if target is None:
            return
        self.write(target, "UPDATE")
        self.summary = f"UPDATE {self.display(target)}"
        self.mark(target, update.this)
        for item in update.expressions:
            if isinstance(item, exp.EQ):
                self.set_column(item.this, target, block)
                self.walk(item.expression, block, "select", target, "UPDATE")
        where = update.args.get("where")
        if where is not None:
            self.condition(where.this, block, "where", "WHERE", None, None)
        for nid in dict.fromkeys(block.order):
            self.flow(nid, target, "UPDATE", block.spans.get(nid))
        self.base = block.base
        self.output(update, target, block)

    def delete(self, delete: exp.Delete) -> None:
        self.with_ctes(delete)
        block = _Block()
        self.add_source(delete.this, block)
        source = _arg(delete, "from_", "from")
        if source is not None:
            self.add_source(source.this, block)
        for join in delete.args.get("joins") or []:
            self.join(join, block)
        tables = delete.args.get("tables") or []
        target = self.find(tables[0], block) if tables else None
        if target is None and tables:
            target = self.add_source(tables[0], block)
        if target is None and block.order:
            target = block.order[0]
        if target is None:
            return
        self.write(target, "DELETE")
        self.summary = f"DELETE FROM {self.display(target)}"
        if tables:
            self.mark(target, tables[0])
        where = delete.args.get("where")
        if where is not None:
            self.condition(where.this, block, "where", "WHERE", None, None)
        for nid in dict.fromkeys(block.order):
            self.flow(nid, target, "DELETE", block.spans.get(nid))
        self.base = block.base
        self.output(delete, target, block)

    def merge(self, merge: exp.Merge) -> None:
        self.with_ctes(merge)
        block = _Block()
        target = self.add_source(merge.this, block)
        using = self.add_source(merge.args.get("using"), block)
        if target is None:
            return
        self.write(target, "MERGE")
        self.summary = f"MERGE {self.display(target)}"
        block.base = using or target
        block.sources.setdefault("inserted", target)
        block.sources.setdefault("deleted", target)
        on = merge.args.get("on")
        text = self.text.source(on, 200) if on is not None else ""
        if on is not None:
            self.condition(on, block, "on", "MERGE", None, None, text, merge=True)
        if using is not None:
            span = self.text.term(on) if on is not None else block.spans.get(using)
            self.flow(using, target, "MERGE", span, text)
        whens = merge.args.get("whens")
        items = whens.expressions if isinstance(whens, exp.Expression) else whens or []
        for when in items:
            then = when.args.get("then")
            if isinstance(then, exp.Update):
                for item in then.expressions:
                    if isinstance(item, exp.EQ):
                        self.set_column(item.this, target, block)
                        self.walk(item.expression, block, "select", target, "MERGE")
            elif isinstance(then, exp.Insert):
                columns = then.this
                if isinstance(columns, exp.Tuple):
                    for column in columns.expressions:
                        self.set_column(column, target, block, "insert")
                if then.expression is not None:
                    self.walk(then.expression, block, "select", target, "MERGE")
        self.base = block.base
        self.output(merge, target, block)

    def find(self, table: exp.Expression | None, block: _Block) -> str | None:
        """The source of *block* an UPDATE or DELETE names as its target, by
        alias or by name."""
        names = _names(table) if isinstance(table, exp.Table) else None
        if names is None:
            return None
        return block.sources.get(".".join(names).casefold()) or (
            block.sources.get(names[-1].casefold()) if len(names) == 1 else None
        )

    def mark(self, nid: str, table: exp.Expression) -> None:
        span = self.table_span(table) if isinstance(table, exp.Table) else None
        node = self.nodes[nid]
        if span is not None and span not in node.spans:
            node.spans.append(span)

    def set_column(
        self, column: exp.Expression, target: str, block: _Block, role: str = "set"
    ) -> None:
        """A column the statement writes: unqualified, it is the target's."""
        if isinstance(column, exp.Column) and column.table:
            ref = self.resolve(column, block)
        elif isinstance(column, (exp.Column, exp.Identifier)):
            ref = _Ref(target, column.name, self.text.span(column), column)
        else:
            ref = None
        if ref is not None:
            self.touch(ref, role)

    def output(
        self, statement: exp.Expression, target: str, block: _Block | None
    ) -> None:
        """``OUTPUT inserted.x INTO #log``: the target's rows flow on."""
        returning = statement.args.get("returning")
        if returning is None:
            return
        block = block or _Block()
        block.sources.setdefault("inserted", target)
        block.sources.setdefault("deleted", target)
        for item in returning.expressions:
            self.walk(item, block, "select")
        into = returning.args.get("into")
        nid: str | None = None
        span: Span | None = None
        if isinstance(into, exp.Table):
            nid = self.table_node(into)
            span = self.table_span(into)
        elif isinstance(into, exp.Parameter):
            nid = self.variable(into.name)
            span = self.text.find(rf"@{re.escape(into.name)}\b")
        elif isinstance(into, exp.Identifier):
            # sqlglot keeps only the first part of a qualified name here.
            span = self.text.span(into)
            names = [_ident(into)]
            if span is not None:
                rest, span = self.text.dotted(span)
                names += rest
            nid = self.named_node(names)
        if nid is None:
            return
        self.write(nid, "INSERT")
        node = self.nodes[nid]
        if span is not None and span not in node.spans:
            node.spans.append(span)
        self.flow(target, nid, "OUTPUT", span)


def _join_label(side: str, kind: str) -> tuple[str, OptionalSide | None]:
    if side == "LEFT":
        return "LEFT", "target"
    if side == "RIGHT":
        return "RIGHT", "source"
    if side == "FULL":
        return "FULL", "both"
    if kind == "CROSS":
        return "CROSS", None
    return "INNER", None


# --- Merging ------------------------------------------------------------------


def _finish(
    infos: list[StatementInfo], builders: dict[int, _Builder], notes: list[str]
) -> QueryGraph:
    every = [n for b in builders.values() for n in b.nodes.values()]
    renames = _qualify(every)
    parts: dict[int, QueryGraph] = {}
    for index, builder in builders.items():
        # A statement that names both Pedidos and vendas.Pedidos keeps both.
        mine = {k: v for k, v in renames.items() if v[0] not in builder.nodes}
        nodes = _merge_nodes(builder.nodes.values(), mine)
        edges = [
            replace(
                e,
                source=mine.get(e.source, (e.source, None))[0],
                target=mine.get(e.target, (e.target, None))[0],
            )
            for e in builder.edges
        ]
        info = [i for i in infos if i.index == index]
        for i in info:
            if i.base is not None and i.base in mine:
                i.base = mine[i.base][0]
        parts[index] = QueryGraph(nodes, edges, info, list(notes))
    graph = QueryGraph(
        _merge_nodes((n for p in parts.values() for n in p.nodes), {}),
        [replace(e) for p in parts.values() for e in p.edges],
        infos,
        notes,
    )
    graph._parts = parts
    return graph


def _qualify(nodes: list[GraphNode]) -> dict[str, tuple[str, str | None]]:
    """New ids for the tables named without a schema when the script names
    exactly one table of that name with a schema: ``Pedidos`` is
    ``vendas.Pedidos`` then."""
    qualified: dict[str, dict[str, str]] = {}
    for node in nodes:
        if node.kind == "table" and node.schema:
            base = node.id.partition(" as ")[0]
            qualified.setdefault(node.name.casefold(), {}).setdefault(base, node.schema)
    renames: dict[str, tuple[str, str | None]] = {}
    for node in nodes:
        if node.kind != "table" or node.schema:
            continue
        hits = qualified.get(node.name.casefold(), {})
        if len(hits) == 1:
            base, schema = next(iter(hits.items()))
            _, sep, alias = node.id.partition(" as ")
            renames[node.id] = (base + sep + alias, schema)
    return renames


def _merge_nodes(
    nodes: Iterable[GraphNode], renames: dict[str, tuple[str, str | None]]
) -> list[GraphNode]:
    """Fresh copies of *nodes*, one per id, in the order ids first appear."""
    merged: dict[str, GraphNode] = {}
    for node in nodes:
        nid, schema = renames.get(node.id, (node.id, node.schema))
        mine = merged.get(nid)
        if mine is None:
            merged[nid] = GraphNode(
                nid,
                node.kind,
                schema or node.schema,
                node.name,
                list(node.aliases),
                [_copy_column(c) for c in node.columns],
                set(node.statements),
                list(node.writes),
                node.is_base,
                list(node.spans),
                list(node.filters),
            )
            continue
        known = {a.casefold() for a in mine.aliases}
        mine.aliases += [a for a in node.aliases if a.casefold() not in known]
        for column in node.columns:
            if mine.kind == "result":
                mine.columns.append(_copy_column(column))
                continue
            into = _Builder.column(mine, column.name)
            into.roles |= column.roles
            into.is_key = into.is_key or column.is_key
            into.spans += [s for s in column.spans if s not in into.spans]
            into.filters += column.filters
            into.expression = into.expression or column.expression
        mine.statements |= node.statements
        mine.writes += [w for w in node.writes if w not in mine.writes]
        mine.is_base = mine.is_base or node.is_base
        mine.spans += [s for s in node.spans if s not in mine.spans]
        mine.filters += node.filters
        mine.schema = mine.schema or schema
    return list(merged.values())


def _copy_column(column: GraphColumn) -> GraphColumn:
    return GraphColumn(
        column.name,
        set(column.roles),
        column.is_key,
        list(column.spans),
        column.expression,
        list(column.filters),
    )
