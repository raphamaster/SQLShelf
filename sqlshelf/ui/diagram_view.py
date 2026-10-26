"""The query diagram: the graph read from the SQL text alone, drawn on a canvas.

The graph comes from a worker (``build_query_graph`` never runs on the UI
thread); this module measures each node, asks ``graph_layout.layout`` where it
goes and draws the result as ``QGraphicsItem``s. The items read the palette
when they paint, so a theme switch is a repaint; a language switch rebuilds
the scene from the same graph. What the user did on the canvas (the node
selected, nodes and frames dragged, frames collapsed) is kept by node id, so
the graph read again from an edited text comes back the same.
"""

from __future__ import annotations

import bisect
import hashlib
import html
import math
import re
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import (
    QBuffer,
    QEvent,
    QIODevice,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneHelpEvent,
    QGraphicsSceneHoverEvent,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedLayout,
    QStyleOptionGraphicsItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core.graph_layout import (
    FRAME_PAD,
    FRAME_TITLE,
    LANE,
    GroupBox,
    Layout,
    Point,
    Rect,
    layout,
    node_group,
)
from ..core.i18n import ntr, tr
from ..core.query_graph import (
    ROLE_ORDER,
    FilterInfo,
    GraphColumn,
    GraphEdge,
    GraphNode,
    QueryGraph,
    Span,
    StatementInfo,
)
from ..core.sql_objects import pick_dialect
from . import icons
from . import tokens as _tk
from .theme import mono_font, qcolor, ui_font
from .widgets import IconButton

HEADER_HEIGHT = 30
ROW_HEIGHT = 26
MAX_ROWS = 8  # column rows before "+N columns"
MIN_WIDTH = 168
MAX_WIDTH = 270
PAD = 10  # inside a node, left and right
GAP = 12  # between a row's name and what is at its right
KEY_ICON = 12
ICON_GAP = 5
BADGE_HEIGHT = 16
BADGE_PAD = 5
LABEL_HEIGHT = 18  # an edge's label
LABEL_PAD = 6
DOT_RADIUS = 3.0
CORNER = 6.0  # rounding of an edge's right angles
MARGIN = 40  # canvas around the diagram
DOT_STEP = 18
ZOOM_MIN = 0.25
ZOOM_MAX = 2.0
ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
ANALYZING_DELAY_MS = 150
CACHE_SIZE = 32
CROWDED_NODES = 60  # above this, nodes start with their headers only
LARGE_BODY = 400 * 1024  # bytes; above this, analyze only when asked
TIP_MEMBERS = 6  # column pairs listed in a bundle's tooltip
TIP_LINES = 12  # line numbers listed in a column's tooltip
HALO = 5  # around a node, room for the selection's halo
CHEVRON_HIT = 28  # a frame's title bar, from its left: the chevron
JOIN_KINDS = frozenset({"INNER", "LEFT", "RIGHT", "FULL", "CROSS"})
EXPORT_KINDS = ("png", "svg", "copy")
EXPORT_SCALE = 2.0  # a PNG's pixels per canvas pixel

PaneState = Literal["blank", "analyzing", "diagram", "empty", "failed", "large"]

# ---------------------------------------------------------------------------
# What a node shows
# ---------------------------------------------------------------------------


def ordered_columns(node: GraphNode) -> list[GraphColumn]:
    """Join keys first, then the rest, each in the order the text uses them;
    a result keeps the order of its SELECT list."""
    if node.kind == "result":
        return list(node.columns)

    def first(item: tuple[int, GraphColumn]) -> tuple[bool, float, int]:
        i, column = item
        start = min((s.start for s in column.spans), default=math.inf)
        return (not column.is_key, start, i)

    return [column for _, column in sorted(enumerate(node.columns), key=first)]


def main_role(column: GraphColumn) -> str:
    """The most telling role of *column*, as ``ROLE_ORDER`` ranks them."""
    for role in ROLE_ORDER:
        if role in column.roles:
            return role
    return min(column.roles, default="")


def filter_count(node: GraphNode) -> int:
    """The filters on *node*: on its columns and in its HAVING footer."""
    return sum(len(c.filters) for c in node.columns) + len(node.filters)


def node_badges(node: GraphNode, graph: QueryGraph) -> list[str]:
    """The labels at the right of a node's header, before the filter count.

    SQL words stay in English; only the result's column count is translated.
    """
    badges = []
    using = any(
        e.kind == "flow" and e.source == node.id and e.label == "MERGE"
        for e in graph.edges
    )
    if using:
        badges.append("USING")
    elif node.is_base and not node.writes:
        badges.append("FROM")
    if node.writes:
        badges.append(" · ".join(node.writes))
    if node.kind == "cte":
        badges.append("CTE")
    if any(
        e.kind == "join" and e.target == node.id and "APPLY" in e.label
        for e in graph.edges
    ):
        badges.append("APPLY")
    if node.kind == "result":
        badges.append(
            ntr("diagram.columns_one", "diagram.columns_other", len(node.columns))
        )
    return badges


def node_title(node: GraphNode) -> str:
    return node.name or tr("diagram.subquery")


def node_alias(node: GraphNode) -> str:
    """The first alias, unless it only repeats the name."""
    for alias in node.aliases:
        if alias.casefold() != node.name.casefold():
            return alias
    return ""


def column_name(column: GraphColumn) -> str:
    return tr("diagram.all_columns") if column.name == "*" else column.name


def column_detail(node: GraphNode, column: GraphColumn) -> str:
    """What a row without a filter shows at its right: the expression of an
    output column (a result's, or one an INTO or INSERT writes), else the
    column's main role."""
    role = main_role(column)
    if column.expression and (
        node.kind == "result" or role in ("set", "insert", "into")
    ):
        return column.expression
    return role


@dataclass(frozen=True)
class DiagramCounts:
    objects: int
    joins: int
    filters: int
    tables_only: bool  # every object is a table: "N tables", not "N objects"


def graph_counts(graph: QueryGraph) -> DiagramCounts:
    """What the strip says about *graph*; the results are not counted."""
    sources = [n for n in graph.nodes if n.kind != "result"]
    return DiagramCounts(
        objects=len(sources),
        joins=sum(1 for e in graph.edges if e.kind == "join"),
        filters=sum(filter_count(n) for n in graph.nodes),
        tables_only=all(n.kind == "table" for n in sources),
    )


def diagram_info(counts: DiagramCounts) -> str:
    """ "4 tables · 3 joins · 2 filters", each part left out when zero."""
    parts = []
    if counts.objects:
        if counts.tables_only:
            keys = ("diagram.tables_one", "diagram.tables_other")
        else:
            keys = ("diagram.objects_one", "diagram.objects_other")
        parts.append(ntr(*keys, counts.objects))
    if counts.joins:
        parts.append(ntr("diagram.joins_one", "diagram.joins_other", counts.joins))
    if counts.filters:
        parts.append(
            ntr("diagram.filters_one", "diagram.filters_other", counts.filters)
        )
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Statements, lines and edits
# ---------------------------------------------------------------------------


def is_large(body: str) -> bool:
    """Too large to read on its own: the pane waits for "Analyze anyway"."""
    return len(body.encode("utf-8", "surrogatepass")) > LARGE_BODY


def without_results(graph: QueryGraph) -> QueryGraph:
    """*graph* without its result nodes and the flows into them."""
    results = {n.id for n in graph.nodes if n.kind == "result"}
    if not results:
        return graph
    return QueryGraph(
        [n for n in graph.nodes if n.id not in results],
        [e for e in graph.edges if not {e.source, e.target} & results],
        list(graph.statements),
        list(graph.notes),
        graph.error,
    )


def statement_label(info: StatementInfo) -> str:
    """ "2 · SELECT … INTO #x", as the statement selector lists it."""
    return f"{info.index} · {info.summary}"


def add_export_actions(
    menu: QMenu, slot: Callable[[str], object]
) -> dict[str, QAction]:
    """Export PNG…, Export SVG… and Copy image on *menu*, each calling *slot*
    with its kind."""
    actions: dict[str, QAction] = {}
    for kind in EXPORT_KINDS:
        action = menu.addAction("")
        action.triggered.connect(lambda _=False, k=kind: slot(k))
        actions[kind] = action
    retranslate_exports(actions)
    return actions


def retranslate_exports(actions: dict[str, QAction]) -> None:
    for kind, action in actions.items():
        action.setText(tr(f"diagram.export_{kind}"))


def regressed(new: QueryGraph, old: QueryGraph | None) -> bool:
    """Whether *new*, read while the text is edited, reads less of it than
    *old*: the pane then keeps *old* and the strip says it is outdated."""
    if old is None or "failed" in old.notes:
        return False
    if "failed" in new.notes:
        return True

    def unread(graph: QueryGraph) -> int:
        return sum(1 for s in graph.statements if s.status != "ok")

    return unread(new) > unread(old)


class LineIndex:
    """Line numbers (from 1) of offsets in a text."""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self._starts: list[int] | None = None

    def line(self, offset: int) -> int:
        if self._starts is None:
            self._starts = [m.end() for m in re.finditer("\n", self._text)]
        return bisect.bisect_right(self._starts, offset) + 1


def edge_title(edge: GraphEdge) -> str:
    """ "INNER JOIN", "CROSS APPLY", "WHERE", "INTO"…: what heads its tooltip."""
    if edge.kind == "join" and edge.label in JOIN_KINDS:
        return f"{edge.label} JOIN"
    return edge.label or ("JOIN" if edge.kind == "join" else "SELECT")


def preserved_table(
    graph: QueryGraph, node: GraphNode, flt: FilterInfo
) -> GraphNode | None:
    """For a filter in the ON of a LEFT or RIGHT join, the table whose rows it
    does not remove: the preserved side of that join (the nearest one before
    the filter), else the statement's first table for a LEFT join."""
    by_id = {n.id: n for n in graph.nodes}
    joins = [
        e
        for e in graph.edges
        if e.kind == "join"
        and e.statement == flt.statement
        and e.label == flt.join_label
        and e.optional_side in ("source", "target")
        and node.id in (e.source, e.target)
    ]
    before = [e for e in joins if e.span and e.span.start <= flt.span.start]
    if before:
        joins = [max(before, key=lambda e: e.span.start if e.span else 0)]
    if joins:
        edge = joins[0]
        kept = edge.source if edge.optional_side == "target" else edge.target
        return by_id.get(kept)
    info = next((s for s in graph.statements if s.index == flt.statement), None)
    if flt.join_label == "LEFT" and info is not None and info.base:
        return by_id.get(info.base)
    return None


def qualified_name(node: GraphNode) -> str:
    """ "vendas.Pedidos", or the name alone without a schema."""
    return f"{node.schema}.{node.name}" if node.schema and node.name else node.name


def _first_span(spans: Iterable[Span | None]) -> Span | None:
    return min((s for s in spans if s is not None), key=lambda s: s.start, default=None)


# ---------------------------------------------------------------------------
# Predicates, coloured as the editor does
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"(?P<string>N?'(?:[^']|'')*'?)"
    r"|(?P<variable>@@?\w+)"
    r"|(?P<number>\b\d+(?:\.\d+)?\b)"
    r"|(?P<word>[A-Za-z_][\w$#]*)"
    r"|(?P<operator>[<>=!]+|[-+*/%])"
)
_KEYWORDS = frozenset("""
    AND OR NOT IN IS NULL LIKE BETWEEN EXISTS CASE WHEN THEN ELSE END AS ALL
    ANY SOME ESCAPE COLLATE
    """.split())


def syntax_runs(text: str) -> list[tuple[str, str]]:
    """Split *text* into (piece, colour token) runs, the editor's colours."""
    runs: list[tuple[str, str]] = []

    def add(piece: str, token: str) -> None:
        if runs and runs[-1][1] == token:
            runs[-1] = (runs[-1][0] + piece, token)
        elif piece:
            runs.append((piece, token))

    pos = 0
    for m in _TOKEN_RE.finditer(text):
        add(text[pos : m.start()], "text2")
        kind = m.lastgroup
        piece = m.group()
        if kind == "string":
            token = "syn_string"
        elif kind == "variable":
            token = "syn_function"
        elif kind == "number":
            token = "syn_number"
        elif kind == "operator":
            token = "syn_operator"
        elif piece.upper() in _KEYWORDS:
            token = "syn_keyword"
        elif text[m.end() : m.end() + 1] == "(":
            token = "syn_function"
        else:
            token = "text2"
        add(piece, token)
        pos = m.end()
    add(text[pos:], "text2")
    return runs


# ---------------------------------------------------------------------------
# Tooltips
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _html_color(key: str) -> str:
    return qcolor(_tk.color(key)).name()


def _code_html(text: str) -> str:
    """*text* in a code block, coloured as the editor colours SQL."""
    families = ", ".join(f"'{family}'" for family in _tk.MONO_FONT_FAMILIES)
    runs = "".join(
        f'<span style="color:{_html_color("text" if token == "text2" else token)}">'
        f"{_esc(piece)}</span>"
        for piece, token in syntax_runs(text)
    )
    return (
        f'<table width="100%" cellpadding="5" cellspacing="0" border="1" '
        f'bgcolor="{_html_color("code")}" style="margin-top:4px; margin-bottom:4px; '
        f'border-style:solid; border-color:{_html_color("border")}; '
        'border-collapse:collapse"><tr><td>'
        f'<div style="font-family:{families}; white-space:pre-wrap">{runs}</div>'
        "</td></tr></table>"
    )


def _head(title: str, *facts: str) -> str:
    """The first line: *title* in bold, then " · fact" in ``text2``."""
    tail = "".join(
        f'<span style="color:{_html_color("text2")}"> · {_esc(fact)}</span>'
        for fact in facts
        if fact
    )
    return f"<div><b>{_esc(title)}</b>{tail}</div>"


def _note(text: str, key: str = "text2") -> str:
    return f'<div style="color:{_html_color(key)}">{_esc(text)}</div>'


def _tip(*parts: str, hint: bool = True) -> str:
    """The tooltip, ending with "Double-click opens in SQL" when it does."""
    body = "".join(parts)
    if hint:
        body += _note(tr("diagram.tip_open"), "text3")
    return f"<qt>{body}</qt>"


def _statement_fact(graph: QueryGraph, statement: int) -> str:
    """ "statement 2", only when the diagram shows more than one."""
    if len(graph.statements) > 1:
        return tr("diagram.tip_statement", n=statement)
    return ""


def _line_fact(lines: LineIndex, span: Span | None) -> str:
    return tr("diagram.tip_line", line=lines.line(span.start)) if span else ""


def _end_name(graph: QueryGraph, node_id: str, column: str | None) -> str:
    """ "p.ClienteID": by alias, unless the node is in several statements,
    where the alias may be another statement's."""
    node = next((n for n in graph.nodes if n.id == node_id), None)
    if node is None:
        name = node_id
    elif len(node.statements) > 1:
        name = node_title(node)
    else:
        name = node_alias(node) or node_title(node)
    return f"{name}.{column}" if column else name


def edge_tooltip(graph: QueryGraph, members: list[int], lines: LineIndex) -> str:
    """An edge (or a bundle of flows): its kind, statement and line, then the
    condition, or the columns it links."""
    edges = [graph.edges[i] for i in members]
    first = edges[0]
    span = _first_span(e.span for e in edges)
    parts = [
        _head(
            edge_title(first),
            _statement_fact(graph, first.statement),
            _line_fact(lines, span),
        )
    ]
    if len(edges) == 1 and first.condition:
        parts.append(_code_html(first.condition))
    else:
        arrow = " → " if first.kind == "flow" else " – "
        pairs = list(
            dict.fromkeys(
                _end_name(graph, e.source, e.source_column)
                + arrow
                + _end_name(graph, e.target, e.target_column)
                for e in edges
            )
        )
        parts.append(_code_html("\n".join(pairs[:TIP_MEMBERS])))
        if len(pairs) > TIP_MEMBERS:
            parts.append(_note(f"+{len(pairs) - TIP_MEMBERS}"))
    return _tip(*parts, hint=span is not None)


def filters_tooltip(
    graph: QueryGraph, node: GraphNode, filters: list[FilterInfo], lines: LineIndex
) -> str:
    """One block per filter: "Filter · WHERE · line 9", the whole condition,
    and a note when it is part of an OR or sits in the ON of an outer join."""
    parts = []
    for flt in filters:
        parts.append(
            _head(
                tr("diagram.tip_filter"),
                flt.clause.upper(),
                _statement_fact(graph, flt.statement),
                _line_fact(lines, flt.span),
            )
        )
        parts.append(_code_html(flt.full or flt.predicate))
        if flt.in_or:
            parts.append(_note(tr("diagram.tip_in_or")))
        if flt.clause != "on":
            continue
        if flt.join_label == "FULL":
            parts.append(_note(tr("diagram.tip_outer_on_both")))
        elif flt.join_label in ("LEFT", "RIGHT"):
            kept = preserved_table(graph, node, flt)
            if kept is not None:
                text = tr(
                    "diagram.tip_outer_on", join=flt.join_label, table=node_title(kept)
                )
                parts.append(_note(text))
    return _tip(*parts)


def column_tooltip(
    graph: QueryGraph, node: GraphNode, column: GraphColumn, lines: LineIndex
) -> str:
    """A column: its filters when it has any; else its name, the lines that
    use it, every role it plays and its expression."""
    if column.filters:
        return filters_tooltip(graph, node, column.filters, lines)
    numbers = sorted({lines.line(s.start) for s in column.spans})
    facts = []
    if numbers:
        shown = ", ".join(str(n) for n in numbers[:TIP_LINES])
        if len(numbers) > TIP_LINES:
            shown += ", …"
        facts.append(
            ntr(
                "diagram.tip_lines_one",
                "diagram.tip_lines_other",
                len(numbers),
                lines=shown,
            )
        )
    parts = [_head(column_name(column), *facts)]
    roles = [r for r in ROLE_ORDER if r in column.roles]
    roles += sorted(column.roles - set(ROLE_ORDER))
    if roles:
        parts.append(_note(" · ".join(roles)))
    if column.expression:
        parts.append(_code_html(column.expression))
    return _tip(*parts, hint=bool(column.spans))


def node_tooltip(node: GraphNode) -> str:
    """A node's header: the whole name, with its schema and aliases."""
    aliases = [a for a in node.aliases if a.casefold() != node.name.casefold()]
    head = _head(qualified_name(node) or node_title(node), ", ".join(aliases))
    return _tip(head, hint=bool(node.spans))


# ---------------------------------------------------------------------------
# Graph cache
# ---------------------------------------------------------------------------


class GraphCache:
    """The last ``CACHE_SIZE`` graphs, by the text and dialect they came from,
    so going back to a query draws it at once."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._graphs: OrderedDict[tuple[str, str], QueryGraph] = OrderedDict()

    @staticmethod
    def key(body: str, dialect: str = "tsql") -> tuple[str, str]:
        digest = hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()
        return digest, pick_dialect(body, dialect)

    def get(self, key: tuple[str, str]) -> QueryGraph | None:
        graph = self._graphs.get(key)
        if graph is not None:
            self._graphs.move_to_end(key)
        return graph

    def put(self, key: tuple[str, str], graph: QueryGraph) -> None:
        self._graphs[key] = graph
        self._graphs.move_to_end(key)
        while len(self._graphs) > self._size:
            self._graphs.popitem(last=False)

    def __len__(self) -> int:
        return len(self._graphs)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Fonts:
    title: QFont
    row: QFont
    key: QFont
    frame: QFont
    frame_kind: QFont
    mono: QFont
    predicate: QFont
    mark: QFont


_fonts: _Fonts | None = None


def fonts() -> _Fonts:
    """The diagram's fonts, unhinted so text keeps its width at any zoom."""
    global _fonts
    if _fonts is None:
        base = ui_font()
        mono = mono_font(_tk.DG_FONT_PX_MONO)

        def sized(font: QFont, px: int, weight: QFont.Weight | None = None) -> QFont:
            result = QFont(font)
            result.setPixelSize(px)
            if weight is not None:
                result.setWeight(weight)
            result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            return result

        semibold = QFont.Weight.DemiBold
        _fonts = _Fonts(
            title=sized(base, _tk.FONT_PX_BASE, semibold),
            row=sized(base, _tk.FONT_PX_SMALL),
            key=sized(base, _tk.FONT_PX_SMALL, semibold),
            frame=sized(base, _tk.FONT_PX_SMALL, semibold),
            frame_kind=sized(base, _tk.DG_FONT_PX_MONO),
            mono=sized(mono, _tk.DG_FONT_PX_MONO),
            predicate=sized(mono, _tk.DG_FONT_PX_PREDICATE),
            mark=sized(mono, _tk.DG_FONT_PX_MARK),
        )
    return _fonts


def _width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)


# ---------------------------------------------------------------------------
# Node shapes
# ---------------------------------------------------------------------------

RowKind = Literal["column", "more", "having"]


@dataclass
class Row:
    kind: RowKind
    column: GraphColumn | None = None
    filters: list[FilterInfo] | None = None  # the HAVING footer's
    hidden: int = 0  # columns behind a "more" row


def node_rows(
    node: GraphNode, expanded: bool = False, headers_only: bool = False
) -> list[Row]:
    """The rows under a node's header: up to ``MAX_ROWS`` columns, then
    "+N columns", then the HAVING footer; none in a crowded diagram until
    the node is expanded."""
    if headers_only and not expanded:
        return []
    columns = ordered_columns(node)
    shown = columns if expanded or len(columns) <= MAX_ROWS else columns[:MAX_ROWS]
    rows = [Row("column", column) for column in shown]
    if len(shown) < len(columns):
        rows.append(Row("more", hidden=len(columns) - len(shown)))
    if node.filters:
        rows.append(Row("having", filters=list(node.filters)))
    return rows


@dataclass
class NodeShape:
    node: GraphNode
    group: str  # the frame's key
    color: str
    title: str
    alias: str
    badges: list[str]
    filters: int
    rows: list[Row]
    width: float = MIN_WIDTH

    @property
    def height(self) -> float:
        return HEADER_HEIGHT + ROW_HEIGHT * len(self.rows)

    def row_of(self, column: str | None) -> int | None:
        """The row that shows *column*: a hidden one is behind "+N", and an
        unknown one (or ``None``) is the header's."""
        if column is None:
            return None
        key = column.casefold()
        for i, row in enumerate(self.rows):
            if row.column is not None and row.column.name.casefold() == key:
                return i
        if any(c.name.casefold() == key for c in self.node.columns):
            for i, row in enumerate(self.rows):
                if row.kind == "more":
                    return i
        return None

    def anchor(self, column: str | None) -> float:
        """Where an edge meets the node, from its top."""
        row = self.row_of(column)
        if row is None:
            return HEADER_HEIGHT / 2
        return HEADER_HEIGHT + ROW_HEIGHT * row + ROW_HEIGHT / 2


def _badge_width(text: str) -> float:
    return _width(fonts().mark, text) + 2 * BADGE_PAD


def _count_width(count: int) -> float:
    return KEY_ICON + 3 + _width(fonts().mark, str(count)) + 2 * BADGE_PAD - 2


def _header_width(shape: NodeShape) -> float:
    f = fonts()
    width = PAD + _width(f.title, shape.title)
    if shape.alias:
        width += ICON_GAP + _width(f.mono, shape.alias)
    extras = [_badge_width(b) for b in shape.badges]
    if shape.filters:
        extras.append(_count_width(shape.filters))
    if extras:
        width += GAP + sum(extras) + 4 * (len(extras) - 1)
    return width + PAD


def _filters_width(filters: list[FilterInfo]) -> float:
    f = fonts()
    first = filters[0]
    width = KEY_ICON + 4 + _width(f.predicate, first.predicate)
    if first.clause == "on":
        width += _mark_width() + 4
    if len(filters) > 1:
        width += 4 + _width(f.mono, f"+{len(filters) - 1}")
    return width


def _mark_width() -> float:
    return _width(fonts().mark, "on") + 8


def _row_widths(shape: NodeShape, row: Row) -> tuple[float, float]:
    """Natural widths of a row's left part and right part."""
    f = fonts()
    if row.kind == "more":
        text = ntr("diagram.more_one", "diagram.more_other", row.hidden)
        return _width(f.row, text), 0.0
    if row.kind == "having":
        return _width(f.mono, "HAVING"), _filters_width(row.filters or [])
    column = row.column
    assert column is not None
    left = _width(f.key if column.is_key else f.row, column_name(column))
    if column.is_key:
        left += KEY_ICON + ICON_GAP
    if column.filters:
        right = _filters_width(column.filters)
    else:
        right = _width(f.mono, column_detail(shape.node, column))
    return left, right


def natural_width(shape: NodeShape) -> float:
    width = _header_width(shape)
    for row in shape.rows:
        left, right = _row_widths(shape, row)
        width = max(width, PAD + left + (GAP + right if right else 0) + PAD)
    return width


def measure(
    graph: QueryGraph,
    expanded: set[str] | None = None,
    headers_only: bool = False,
) -> dict[str, NodeShape]:
    """A shape for every node; nodes in one frame share the widest width,
    between ``MIN_WIDTH`` and ``MAX_WIDTH``."""
    expanded = expanded or set()
    shapes: dict[str, NodeShape] = {}
    widest: dict[str, float] = {}
    for node in graph.nodes:
        key, kind, name = node_group(node)
        shape = NodeShape(
            node=node,
            group=key,
            color=_tk.diagram_group_color(kind, name),
            title=node_title(node),
            alias=node_alias(node),
            badges=node_badges(node, graph),
            filters=filter_count(node),
            rows=node_rows(node, node.id in expanded, headers_only),
        )
        width = min(max(natural_width(shape), MIN_WIDTH), MAX_WIDTH)
        widest[key] = max(widest.get(key, 0.0), width)
        shapes[node.id] = shape
    for shape in shapes.values():
        shape.width = widest[shape.group]
    return shapes


def split_width(inner: float, left: float, right: float) -> tuple[float, float]:
    """Share *inner* between a row's name and its right part: when both do
    not fit, the right part keeps up to 60 % and the name is elided."""
    if left + GAP + right <= inner:
        return left, right
    right_room = max(inner - GAP - left, min(right, inner * 0.6))
    return max(inner - GAP - right_room, 0.0), right_room


def diagram_layout(graph: QueryGraph, shapes: dict[str, NodeShape]) -> Layout:
    sizes = {n: (s.width, s.height) for n, s in shapes.items()}
    return layout(graph, sizes, lambda node, column: shapes[node.id].anchor(column))


# ---------------------------------------------------------------------------
# Painting helpers
# ---------------------------------------------------------------------------


def _color(key: str, alpha: float = 1.0) -> QColor:
    result = qcolor(_tk.color(key))
    if alpha < 1.0:
        result.setAlphaF(result.alphaF() * alpha)
    return result


def _tinted(hex_color: str, alpha: float) -> QColor:
    result = QColor(hex_color)
    result.setAlphaF(alpha)
    return result


def _text(
    painter: QPainter,
    font: QFont,
    x: float,
    rect: QRectF,
    text: str,
    color: QColor,
    max_width: float | None = None,
) -> float:
    """Draw *text* from *x*, centred on *rect*'s height, elided to
    *max_width*; returns the width drawn."""
    metrics = QFontMetricsF(font)
    if max_width is not None and metrics.horizontalAdvance(text) > max_width:
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(max_width, 0))
    width = metrics.horizontalAdvance(text)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(
        QRectF(x, rect.y(), width + 1, rect.height()),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        text,
    )
    return width


def _runs(
    painter: QPainter,
    font: QFont,
    x: float,
    rect: QRectF,
    runs: list[tuple[str, str]],
    max_width: float,
) -> float:
    """Draw coloured *runs* from *x*, the last one elided to fit."""
    metrics = QFontMetricsF(font)
    used = 0.0
    for piece, token in runs:
        room = max_width - used
        if room <= 0:
            break
        if metrics.horizontalAdvance(piece) > room:
            used += _text(painter, font, x + used, rect, piece, _color(token), room)
            break
        used += _text(painter, font, x + used, rect, piece, _color(token))
    return used


def _runs_width(font: QFont, runs: list[tuple[str, str]], max_width: float) -> float:
    natural = sum(_width(font, piece) for piece, _ in runs)
    return min(natural, max_width)


def _draw_filters(
    painter: QPainter,
    rect: QRectF,
    right: float,
    room: float,
    filters: list[FilterInfo],
) -> None:
    """A filter at the right of a row, ending at *right*: the "on" mark, the
    funnel, the predicate and "+N" for the others."""
    f = fonts()
    first = filters[0]
    more = f"+{len(filters) - 1}" if len(filters) > 1 else ""
    fixed: float = KEY_ICON + 4
    if first.clause == "on":
        fixed += _mark_width() + 4
    if more:
        fixed += 4 + _width(f.mono, more)
    runs = syntax_runs(first.predicate)
    predicate = _runs_width(f.predicate, runs, max(room - fixed, 0))
    x = right - fixed - predicate
    if first.clause == "on":
        mark = QRectF(x, rect.center().y() - 8, _mark_width(), 16)
        painter.setPen(QPen(_color("border_strong"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            mark.adjusted(0.5, 0.5, -0.5, -0.5), _tk.DG_RADIUS_MARK, _tk.DG_RADIUS_MARK
        )
        painter.setFont(f.mark)
        painter.setPen(_color("text3"))
        painter.drawText(mark, Qt.AlignmentFlag.AlignCenter, "on")
        x += _mark_width() + 4
    icon = QRectF(x, rect.center().y() - KEY_ICON / 2, KEY_ICON, KEY_ICON)
    icons.paint(painter, "funnel", icon, _tk.color("text3"))
    x += KEY_ICON + 4
    x += _runs(painter, f.predicate, x, rect, runs, predicate)
    if more:
        _text(painter, f.mono, x + 4, rect, more, _color("text3"))


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

Z_FRAME, Z_NODE, Z_EDGE, Z_LABEL = 0.0, 1.0, 2.0, 3.0


class FrameItem(QGraphicsItem):
    """A group's frame: tinted body and a title bar in the group's colour;
    collapsed, only the title bar."""

    def __init__(self, box: GroupBox) -> None:
        super().__init__()
        self.box = box
        self.color = _tk.diagram_group_color(box.kind, box.name)
        self.collapsed = False
        r = box.rect
        self.setPos(r.x, r.y)
        self._rect = QRectF(0, 0, r.w, r.h)
        self.setZValue(Z_FRAME)

    def title(self) -> tuple[str, str]:
        """The frame's name and the kind word after it."""
        if self.box.kind == "schema":
            return self.box.name, tr("diagram.group_schema")
        return tr(f"diagram.group_{self.box.kind}"), ""

    def set_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self.setPos(rect.topLeft())
        self._rect = QRectF(0, 0, rect.width(), rect.height())

    def scene_rect(self) -> QRectF:
        return self._rect.translated(self.pos())

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        radius = _tk.DG_RADIUS_FRAME
        rect = self._rect.adjusted(0.5, 0.5, -0.5, -0.5)
        outline = QPainterPath()
        outline.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_tinted(self.color, _tk.opacity("dg_frame_fill")))
        painter.drawPath(outline)

        bar = QPainterPath()
        bar.addRect(QRectF(rect.x(), rect.y(), rect.width(), FRAME_TITLE))
        painter.setBrush(QColor(self.color))
        painter.drawPath(outline.intersected(bar))

        painter.setPen(QPen(_tinted(self.color, _tk.opacity("dg_frame_border")), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)

        f = fonts()
        band = QRectF(0, 0, self._rect.width(), FRAME_TITLE)
        chevron = QRectF(10, (FRAME_TITLE - 12) / 2, 12, 12)
        icon = "chevron_right" if self.collapsed else "chevron_down"
        icons.paint(painter, icon, chevron, _tk.color("dg_ink"))
        name, kind = self.title()
        room = self._rect.width() - 30 - PAD
        x = 28 + _text(painter, f.frame, 28, band, name, _color("dg_ink"), room)
        if kind:
            ink = _color("dg_ink")
            ink.setAlphaF(0.7)
            _text(painter, f.frame_kind, x + 5, band, kind, ink, room - (x - 28) - 5)


class NodeItem(QGraphicsItem):
    """A table, temp table, CTE, derived table or result: header and rows."""

    def __init__(self, shape: NodeShape, x: float, y: float, width: float) -> None:
        super().__init__()
        self.shape_info = shape
        self.selected = False
        self._hover: int | None = None  # the row under the mouse
        self.setPos(x, y)
        self._rect = QRectF(0, 0, width, shape.height)
        self.setZValue(Z_NODE)
        self.setAcceptHoverEvents(True)

    @property
    def node(self) -> GraphNode:
        return self.shape_info.node

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-HALO, -HALO, HALO, HALO)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self._rect)
        return path

    def rect(self) -> QRectF:
        """Where the node is, in the scene."""
        return self._rect.translated(self.pos())

    def row_at(self, y: float) -> int | None:
        """The row at local *y*; ``None`` on the header."""
        if y < HEADER_HEIGHT:
            return None
        i = int((y - HEADER_HEIGHT) // ROW_HEIGHT)
        return i if i < len(self.shape_info.rows) else None

    def hover_row(self) -> int | None:
        return self._hover

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._set_hover(self.row_at(event.pos().y()))

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._set_hover(self.row_at(event.pos().y()))

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._set_hover(None)

    def _set_hover(self, row: int | None) -> None:
        if row == self._hover:
            return
        self._hover = row
        if row is not None and self.shape_info.rows[row].kind == "more":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        self.update()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        shape = self.shape_info
        radius = _tk.RADIUS
        rect = self._rect.adjusted(0.5, 0.5, -0.5, -0.5)
        if self.selected:
            halo = QPainterPath()
            halo.addRoundedRect(
                self._rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2
            )
            painter.setPen(QPen(_color("accent", 0.28), 4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(halo)
        outline = QPainterPath()
        outline.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_color("dg_node"))
        painter.drawPath(outline)

        header = QPainterPath()
        header.addRect(QRectF(rect.x(), rect.y(), rect.width(), HEADER_HEIGHT))
        painter.setBrush(QColor(shape.color))
        painter.drawPath(outline.intersected(header))

        width = self._rect.width()
        for i, row in enumerate(shape.rows):
            top = HEADER_HEIGHT + ROW_HEIGHT * i
            band = QRectF(0, top, width, ROW_HEIGHT)
            self._paint_row(painter, outline, band, row, i)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.selected:
            painter.setPen(QPen(_color("accent"), 2))
            inner = self._rect.adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(inner, radius - 0.5, radius - 0.5)
        else:
            painter.setPen(QPen(_color("border"), 1))
            painter.drawPath(outline)
        self._paint_header(painter)

    def _paint_header(self, painter: QPainter) -> None:
        shape = self.shape_info
        f = fonts()
        width = self._rect.width()
        band = QRectF(0, 0, width, HEADER_HEIGHT)
        extras = [_badge_width(b) for b in shape.badges]
        if shape.filters:
            extras.append(_count_width(shape.filters))
        right = width - PAD
        x = right - sum(extras) - 4 * max(len(extras) - 1, 0)
        ink = _color("dg_ink")
        fill = _color("dg_ink", 0.14)
        for text, badge_width in zip(shape.badges, extras):
            badge = QRectF(
                x, (HEADER_HEIGHT - BADGE_HEIGHT) / 2, badge_width, BADGE_HEIGHT
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, _tk.DG_RADIUS_MARK, _tk.DG_RADIUS_MARK)
            painter.setFont(f.mark)
            painter.setPen(ink)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
            x += badge_width + 4
        if shape.filters:
            badge = QRectF(
                x, (HEADER_HEIGHT - BADGE_HEIGHT) / 2, extras[-1], BADGE_HEIGHT
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, _tk.DG_RADIUS_MARK, _tk.DG_RADIUS_MARK)
            icon = QRectF(
                x + BADGE_PAD - 1, badge.center().y() - KEY_ICON / 2, KEY_ICON, KEY_ICON
            )
            icons.paint(painter, "funnel", icon, _tk.color("dg_ink"))
            _text(painter, f.mark, icon.right() + 3, badge, str(shape.filters), ink)
        start = right - sum(extras) - 4 * max(len(extras) - 1, 0)
        room = (start - GAP if extras else right) - PAD
        alias_width = _width(f.mono, shape.alias) + ICON_GAP if shape.alias else 0.0
        title_room = room - min(alias_width, room * 0.4)
        used = _text(painter, f.title, PAD, band, shape.title, ink, title_room)
        if shape.alias and room - used - ICON_GAP > 8:
            faded = _color("dg_ink", 0.6)
            _text(
                painter,
                f.mono,
                PAD + used + ICON_GAP,
                band,
                shape.alias,
                faded,
                room - used - ICON_GAP,
            )

    def _paint_row(
        self, painter: QPainter, outline: QPainterPath, band: QRectF, row: Row, i: int
    ) -> None:
        shape = self.shape_info
        f = fonts()
        width = band.width()
        keyed = row.kind == "having" or (row.column is not None and row.column.is_key)
        hovered = i == self._hover
        if keyed or hovered:
            fill = QPainterPath()
            fill.addRect(band)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("dg_row_key"))
            if keyed:
                painter.drawPath(outline.intersected(fill))
            if hovered:
                painter.setBrush(_color("hover"))
                painter.drawPath(outline.intersected(fill))
                bar = QPainterPath()
                bar.addRect(QRectF(0, band.y(), 2, band.height()))
                painter.setBrush(_color("accent"))
                painter.drawPath(outline.intersected(bar))
        if row.kind == "having":
            pen = QPen(_color("border_strong"), 1, Qt.PenStyle.DashLine)
            pen.setDashPattern([3, 3])
        else:
            pen = QPen(_color("border"), 1)
        painter.setPen(pen)
        painter.drawLine(QPointF(1, band.y() + 0.5), QPointF(width - 1, band.y() + 0.5))

        inner = width - 2 * PAD
        left, right = _row_widths(shape, row)
        left_room, right_room = split_width(inner, left, right)
        x = PAD
        if row.kind == "more":
            text = ntr("diagram.more_one", "diagram.more_other", row.hidden)
            _text(painter, f.row, x, band, text, _color("text3"), inner)
            return
        if row.kind == "having":
            _text(painter, f.mono, x, band, "HAVING", _color("text3"), left_room)
            if row.filters:
                _draw_filters(painter, band, width - PAD, right_room, row.filters)
            return
        column = row.column
        assert column is not None
        if column.is_key:
            icon = QRectF(x, band.center().y() - KEY_ICON / 2, KEY_ICON, KEY_ICON)
            icons.paint(painter, "link", icon, _tk.color("text3"))
            x += KEY_ICON + ICON_GAP
            left_room -= KEY_ICON + ICON_GAP
        font = f.key if column.is_key else f.row
        _text(painter, font, x, band, column_name(column), _color("text"), left_room)
        if column.filters:
            _draw_filters(painter, band, width - PAD, right_room, column.filters)
            return
        text = column_detail(shape.node, column)
        metrics = QFontMetricsF(f.mono)
        shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, right_room)
        drawn = metrics.horizontalAdvance(shown)
        _text(painter, f.mono, width - PAD - drawn, band, shown, _color("text3"))


def edge_path(points: list[Point]) -> QPainterPath:
    """An edge through *points*: straight along an axis, with rounded right
    angles, and an S-curve between two points that differ in both."""
    path = QPainterPath(QPointF(*points[0]))
    for k in range(1, len(points)):
        (ax, ay), (bx, by) = points[k - 1], points[k]
        if ax != bx and ay != by:
            mid = (ax + bx) / 2
            path.cubicTo(QPointF(mid, ay), QPointF(mid, by), QPointF(bx, by))
            continue
        after = points[k + 1] if k + 1 < len(points) else None
        radius = _corner(points[k - 1], points[k], after)
        if not radius or after is None:
            path.lineTo(bx, by)
            continue
        ux, uy = _unit(points[k - 1], points[k])
        vx, vy = _unit(points[k], after)
        path.lineTo(bx - ux * radius, by - uy * radius)
        path.quadTo(QPointF(bx, by), QPointF(bx + vx * radius, by + vy * radius))
    return path


def _unit(a: Point, b: Point) -> Point:
    length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    return ((b[0] - a[0]) / length, (b[1] - a[1]) / length)


def _corner(a: Point, b: Point, c: Point | None) -> float:
    """The rounding at *b* when a→b and b→c turn at a right angle."""
    if c is None:
        return 0.0
    first = (a[0] == b[0]) != (a[1] == b[1])
    second = (b[0] == c[0]) != (b[1] == c[1])
    if not (first and second) or (a[0] == b[0]) == (b[0] == c[0]):
        return 0.0
    ab = abs(b[0] - a[0]) + abs(b[1] - a[1])
    bc = abs(c[0] - b[0]) + abs(c[1] - b[1])
    return min(CORNER, ab / 2, bc / 2)


def direct_route(a: Rect, ay: float, b: Rect, by: float) -> list[Point]:
    """A route from *a* (at height *ay*) to *b* (at *by*) once the user moved
    one of them: side to side when they stand apart, else around the right."""
    if b.x - a.right >= 2 * LANE:
        return [(a.right, ay), (b.x, by)]
    if a.x - b.right >= 2 * LANE:
        return [(a.x, ay), (b.right, by)]
    x = max(a.right, b.right) + 2 * LANE
    return [(a.right, ay), (x, ay), (x, by), (b.right, by)]


EdgeKind = Literal["join", "flow"]
End = tuple[str, str]  # ("node", node id) or ("frame", group key)


class EdgeItem(QGraphicsItem):
    """A join (solid, dots at the ends) or a flow (dashed, with an arrow)."""

    def __init__(
        self,
        kind: EdgeKind,
        points: list[Point],
        optional: str | None = None,
        edges: list[int] | None = None,
        ends: tuple[End, End] = (("node", ""), ("node", "")),
    ) -> None:
        super().__init__()
        self.kind = kind
        self.optional = optional  # "source", "target" or "both"
        self.edges = edges or []  # indexes in ``graph.edges``
        self.ends = ends
        self.base = list(points)  # the layout's route
        self.label: LabelItem | None = None
        self.highlighted = False
        self.points = points
        self._path = edge_path(points)
        self._bounds = self._path.boundingRect().adjusted(-8, -8, 8, 8)
        self._shape: QPainterPath | None = None
        self.setZValue(Z_EDGE)

    def set_points(self, points: list[Point]) -> None:
        self.prepareGeometryChange()
        self.points = points
        self._path = edge_path(points)
        self._bounds = self._path.boundingRect().adjusted(-8, -8, 8, 8)
        self._shape = None

    def boundingRect(self) -> QRectF:
        return self._bounds

    def shape(self) -> QPainterPath:
        """A band along the line, wide enough to point at."""
        if self._shape is None:
            stroker = QPainterPathStroker()
            stroker.setWidth(10)
            self._shape = stroker.createStroke(self._path)
        return self._shape

    def middle(self) -> QPointF:
        return self._path.pointAtPercent(0.5)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        lit = self.highlighted
        if self.kind == "flow":
            color = _color("accent_text") if lit else _color("dg_flow", 0.85)
            pen = QPen(color, 2.25 if lit else 1.5)
            pen.setDashPattern([4 / pen.widthF() * 1.5, 8 / 3 / pen.widthF() * 1.5])
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawPath(self._path)
            self._paint_arrow(painter, color)
            return
        color = _color("accent_text") if lit else _color("dg_join")
        width = 2.0 if lit else 1.5
        painter.setPen(QPen(color, width))
        painter.drawPath(self._path)
        for end, point in (("source", self.points[0]), ("target", self.points[-1])):
            hollow = self.optional in (end, "both")
            centre = QPointF(*point)
            if hollow:
                painter.setPen(QPen(color, width))
                painter.setBrush(_color("dg_node"))
                painter.drawEllipse(centre, DOT_RADIUS + 0.5, DOT_RADIUS + 0.5)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(centre, DOT_RADIUS, DOT_RADIUS)

    def _paint_arrow(self, painter: QPainter, color: QColor) -> None:
        tip = QPointF(*self.points[-1])
        angle = math.radians(self._path.angleAtPercent(1.0))
        # angleAtPercent counts counter-clockwise, with y up
        dx, dy = math.cos(angle), -math.sin(angle)
        back = QPointF(tip.x() - dx * 8, tip.y() - dy * 8)
        side = QPointF(-dy * 4, dx * 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, back + side, back - side]))


class LabelItem(QGraphicsItem):
    """An edge's label in a small pill: INNER, LEFT, INTO…"""

    def __init__(self, kind: EdgeKind, text: str, centre: Point, edge: int) -> None:
        super().__init__()
        self.kind = kind
        self.text = text
        self.edge = edge
        self.base_centre = QPointF(*centre)  # where the layout put it
        self.highlighted = False
        width = _width(fonts().mono, text) + 2 * LABEL_PAD
        self._rect = QRectF(-width / 2, -LABEL_HEIGHT / 2, width, LABEL_HEIGHT)
        self.setPos(self.base_centre)
        self.setZValue(Z_LABEL)

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        if self.highlighted:
            line, ink = _color("accent"), _color("accent_text")
        else:
            line = _color("dg_flow" if self.kind == "flow" else "dg_join")
            ink = _color("dg_flow" if self.kind == "flow" else "text2")
        painter.setPen(QPen(line, 1))
        painter.setBrush(_color("dg_node"))
        rect = self._rect.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, _tk.RADIUS_SM, _tk.RADIUS_SM)
        painter.setFont(fonts().mono)
        painter.setPen(ink)
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, self.text)


# ---------------------------------------------------------------------------
# Scene and view
# ---------------------------------------------------------------------------


class DiagramScene(QGraphicsScene):
    """The canvas: a dot grid under the frames, nodes and edges.

    What the user did (the node selected, nodes and frames dragged, frames
    collapsed, nodes expanded) is kept by node id and group key, so drawing a
    graph read again from the edited text brings it all back.
    """

    node_selected = Signal(object)  # the node id, or None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.graph: QueryGraph | None = None
        self.layout_info: Layout | None = None
        self.shapes: dict[str, NodeShape] = {}
        self.nodes: dict[str, NodeItem] = {}
        self.frames: list[FrameItem] = []
        self.frame_of: dict[str, FrameItem] = {}
        self.edges: list[EdgeItem] = []
        self.labels: list[LabelItem] = []
        self.lines = LineIndex()
        self.selected: str | None = None
        self.moved: dict[str, QPointF] = {}  # node → where it was dropped
        self.shifted: dict[str, QPointF] = {}  # group → how far it was dragged
        self.collapsed: set[str] = set()  # groups
        self.expanded: set[str] = set()  # nodes showing every column
        self.headers_only = False
        self.grid = True  # the dots under the diagram

    def build(self, graph: QueryGraph) -> None:
        """Measure, lay out and draw *graph*, replacing what was there."""
        self.clear()
        self.nodes, self.frames, self.edges, self.labels = {}, [], [], []
        self.frame_of = {}
        self.graph = graph
        self.shapes = measure(graph, self.expanded, self.headers_only)
        info = diagram_layout(graph, self.shapes)
        self.layout_info = info
        for box in info.groups:
            frame = FrameItem(box)
            frame.collapsed = box.key in self.collapsed
            self.addItem(frame)
            self.frames.append(frame)
            self.frame_of[box.key] = frame
        for node_id, rect in info.nodes.items():
            item = NodeItem(self.shapes[node_id], rect.x, rect.y, rect.w)
            self.addItem(item)
            self.nodes[node_id] = item
        for i, points in info.routes.items():
            edge = graph.edges[i]
            ends = (("node", edge.source), ("node", edge.target))
            added = self._add_edge(edge, points, [i], ends)
            if added is not None and edge.label and i in info.labels:
                label = LabelItem(edge.kind, edge.label, info.labels[i], i)
                added.label = label
                self.addItem(label)
                self.labels.append(label)
        for bundle in info.bundles:
            ends = (("frame", bundle.group), ("node", bundle.target))
            self._add_edge(None, bundle.points, list(bundle.edges), ends)
        if self.selected is not None and (
            self.selected not in self.nodes or self._folded(self.selected)
        ):
            self.selected = None
        self._update_geometry()
        self._apply_selection()

    def _add_edge(
        self,
        edge: GraphEdge | None,
        points: list[Point],
        members: list[int],
        ends: tuple[End, End],
    ) -> EdgeItem | None:
        if len(points) < 2:
            return None
        if edge is None:
            item = EdgeItem("flow", points, edges=members, ends=ends)
        else:
            item = EdgeItem(edge.kind, points, edge.optional_side, members, ends)
        self.addItem(item)
        self.edges.append(item)
        return item

    def reset(self) -> None:
        self.clear()
        self.graph = None
        self.layout_info = None
        self.shapes = {}
        self.nodes, self.frames, self.edges, self.labels = {}, [], [], []
        self.frame_of = {}
        self.lines = LineIndex()
        self.forget()
        self.setSceneRect(QRectF())
        self.node_selected.emit(None)

    def forget(self) -> None:
        """Another query: nothing selected, moved, collapsed or expanded."""
        self.selected = None
        self.moved, self.shifted = {}, {}
        self.collapsed, self.expanded = set(), set()
        self.headers_only = False

    # -- geometry -------------------------------------------------------------

    def _folded(self, node_id: str) -> bool:
        """Whether *node_id* is hidden in a collapsed frame."""
        return self.shapes[node_id].group in self.collapsed

    def node_pos(self, node_id: str) -> QPointF:
        """Where *node_id* is: where it was dropped, else where the layout put
        it, moved with its group."""
        moved = self.moved.get(node_id)
        if moved is not None:
            return QPointF(moved)
        assert self.layout_info is not None
        rect = self.layout_info.nodes[node_id]
        shift = self.shifted.get(self.shapes[node_id].group, QPointF())
        return QPointF(rect.x, rect.y) + shift

    def _frame_rect(self, frame: FrameItem) -> QRectF:
        """The layout's frame, moved with its group and grown around the
        nodes dropped elsewhere; only the title bar when collapsed."""
        r = frame.box.rect
        rect = QRectF(r.x, r.y, r.w, r.h).translated(
            self.shifted.get(frame.box.key, QPointF())
        )
        if frame.collapsed:
            return QRectF(rect.x(), rect.y(), rect.width(), FRAME_TITLE)
        for node_id in frame.box.nodes:
            item = self.nodes.get(node_id)
            if item is not None and node_id in self.moved:
                rect = rect.united(
                    item.rect().adjusted(
                        -FRAME_PAD, -FRAME_TITLE - FRAME_PAD, FRAME_PAD, FRAME_PAD
                    )
                )
        return rect

    def _end(self, end: End) -> tuple[QRectF, QRectF, bool, str]:
        """An edge's end: the layout's rect, the current one, whether it is a
        collapsed frame, and its group."""
        kind, key = end
        if kind == "frame":
            own = self.frame_of[key]
            r = own.box.rect
            return QRectF(r.x, r.y, r.w, r.h), own.scene_rect(), own.collapsed, key
        assert self.layout_info is not None
        r = self.layout_info.nodes[key]
        base = QRectF(r.x, r.y, r.w, r.h)
        group = self.shapes[key].group
        frame = self.frame_of.get(group)
        if frame is not None and frame.collapsed:
            return base, frame.scene_rect(), True, group
        return base, self.nodes[key].rect(), False, group

    def _place_edge(self, item: EdgeItem) -> None:
        """Follow the ends: the layout's route moved along when both ends moved
        alike, else a direct route; hidden inside a collapsed frame."""
        base_a, rect_a, folded_a, group_a = self._end(item.ends[0])
        base_b, rect_b, folded_b, group_b = self._end(item.ends[1])
        hidden = folded_a and folded_b and group_a == group_b
        item.setVisible(not hidden)
        if item.label is not None:
            item.label.setVisible(not hidden)
        if hidden:
            return
        shift = rect_a.topLeft() - base_a.topLeft()
        if (
            not folded_a
            and not folded_b
            and rect_a.size() == base_a.size()
            and rect_b.size() == base_b.size()
            and rect_b.topLeft() - base_b.topLeft() == shift
        ):
            dx, dy = shift.x(), shift.y()
            item.set_points([(x + dx, y + dy) for x, y in item.base])
            if item.label is not None:
                item.label.setPos(item.label.base_centre + shift)
            return

        def end_y(y: float, base: QRectF, rect: QRectF, folded: bool) -> float:
            if folded:
                return rect.y() + FRAME_TITLE / 2
            return rect.y() + min(max(y - base.y(), 0.0), rect.height())

        ay = end_y(item.base[0][1], base_a, rect_a, folded_a)
        by = end_y(item.base[-1][1], base_b, rect_b, folded_b)
        a = Rect(rect_a.x(), rect_a.y(), rect_a.width(), rect_a.height())
        b = Rect(rect_b.x(), rect_b.y(), rect_b.width(), rect_b.height())
        item.set_points(direct_route(a, ay, b, by))
        if item.label is not None:
            item.label.setPos(item.middle())

    def _update_geometry(self) -> None:
        for node_id, item in self.nodes.items():
            item.setVisible(not self._folded(node_id))
            item.setPos(self.node_pos(node_id))
        for frame in self.frames:
            frame.set_rect(self._frame_rect(frame))
        for edge in self.edges:
            self._place_edge(edge)
        self.setSceneRect(self.content_rect())

    def content_rect(self) -> QRectF:
        """The diagram and a margin around it."""
        info = self.layout_info
        if info is None:
            return QRectF()
        if not (self.moved or self.shifted or self.collapsed):
            w, h = info.size
            return QRectF(-MARGIN, -MARGIN, w + 2 * MARGIN, h + 2 * MARGIN)
        rect = QRectF()
        items: list[QGraphicsItem] = [*self.frames, *self.nodes.values()]
        for item in [*items, *self.edges, *self.labels]:
            if item.isVisible():
                rect = rect.united(item.sceneBoundingRect())
        return rect.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)

    # -- what the user does ---------------------------------------------------

    def move_node(self, node_id: str, delta: QPointF) -> None:
        self.moved[node_id] = self.node_pos(node_id) + delta
        self._update_geometry()

    def move_group(self, key: str, delta: QPointF) -> None:
        """Drag a frame by its title, with every node in it."""
        self.shifted[key] = self.shifted.get(key, QPointF()) + delta
        for node_id, item in self.nodes.items():
            if item.shape_info.group == key and node_id in self.moved:
                self.moved[node_id] += delta
        self._update_geometry()

    def toggle_group(self, key: str) -> None:
        """Collapse a frame to its title bar, or open it again."""
        if key in self.collapsed:
            self.collapsed.discard(key)
        else:
            self.collapsed.add(key)
        frame = self.frame_of.get(key)
        if frame is not None:
            frame.collapsed = key in self.collapsed
            frame.update()
        if self.selected is not None and self._folded(self.selected):
            self.selected = None
        self._update_geometry()
        self._apply_selection()

    def rearrange(self) -> None:
        """Everything back where the layout puts it, every frame open."""
        self.moved, self.shifted = {}, {}
        self.collapsed = set()
        for frame in self.frames:
            frame.collapsed = False
            frame.update()
        self._update_geometry()

    def expand(self, node_id: str) -> None:
        """Show every column of *node_id* ("+N columns" was clicked)."""
        self.expanded.add(node_id)
        if self.graph is not None:
            self.build(self.graph)

    # -- selection ------------------------------------------------------------

    def select(self, node_id: str | None) -> None:
        if node_id is not None and node_id not in self.nodes:
            node_id = None
        if node_id != self.selected:
            self.selected = node_id
            self._apply_selection()

    def refresh_selection(self) -> None:
        self._apply_selection()

    def _apply_selection(self) -> None:
        """Light the selected node's edges and dim what they do not reach;
        frames never dim."""
        selected = self.selected
        graph = self.graph
        linked = {selected} if selected is not None else set()
        for edge in self.edges:
            pairs = []
            if selected is not None and graph is not None:
                pairs = [
                    (e.source, e.target)
                    for e in (graph.edges[i] for i in edge.edges)
                    if selected in (e.source, e.target)
                ]
            edge.highlighted = bool(pairs)
            for pair in pairs:
                linked.update(pair)
        dim = _tk.opacity("dg_dim")
        for node_id, item in self.nodes.items():
            item.selected = node_id == selected
            item.setOpacity(1.0 if selected is None or node_id in linked else dim)
            item.update()
        for edge in self.edges:
            opacity = 1.0 if selected is None or edge.highlighted else dim
            edge.setOpacity(opacity)
            edge.update()
            if edge.label is not None:
                edge.label.highlighted = edge.highlighted
                edge.label.setOpacity(opacity)
                edge.label.update()
        self.node_selected.emit(selected)

    @contextmanager
    def exporting(self, grid: bool) -> Iterator[None]:
        """Nothing selected and, unless *grid*, no dots, while the diagram is
        drawn for an export; then as it was."""
        selected, self.selected = self.selected, None
        self.grid = grid
        self._apply_selection()
        try:
            yield
        finally:
            self.selected = selected
            self.grid = True
            self._apply_selection()

    def neighbours(self, node_id: str) -> set[str]:
        """The nodes an edge links to *node_id*."""
        found: set[str] = set()
        for e in self.graph.edges if self.graph is not None else []:
            if node_id == e.source:
                found.add(e.target)
            elif node_id == e.target:
                found.add(e.source)
        return found

    # -- what is under the mouse ----------------------------------------------

    def hit(self, pos: QPointF) -> QGraphicsItem | None:
        """What a press or a tooltip at *pos* is about: a label, else a node,
        an edge, a frame."""
        found = [item for item in self.items(pos) if item.isVisible()]
        for kind in (LabelItem, NodeItem, EdgeItem, FrameItem):
            for item in found:
                if isinstance(item, kind):
                    return item
        return None

    def tooltip_at(self, pos: QPointF) -> str:
        graph = self.graph
        item = self.hit(pos)
        if graph is None:
            return ""
        if isinstance(item, LabelItem):
            return edge_tooltip(graph, [item.edge], self.lines)
        if isinstance(item, EdgeItem):
            return edge_tooltip(graph, item.edges, self.lines)
        if not isinstance(item, NodeItem):
            return ""
        node = item.node
        i = item.row_at(item.mapFromScene(pos).y())
        if i is None:
            return node_tooltip(node)
        row = item.shape_info.rows[i]
        if row.kind == "more":
            return f"<qt>{_esc(tr('diagram.tip_more'))}</qt>"
        if row.kind == "having":
            return filters_tooltip(graph, node, row.filters or [], self.lines)
        assert row.column is not None
        return column_tooltip(graph, node, row.column, self.lines)

    def span_at(self, pos: QPointF) -> Span | None:
        """The text a double click at *pos* opens: a node's first mention, a
        column's (or its filter's) or an edge's."""
        graph = self.graph
        item = self.hit(pos)
        if graph is None:
            return None
        if isinstance(item, LabelItem):
            return graph.edges[item.edge].span
        if isinstance(item, EdgeItem):
            return _first_span(graph.edges[i].span for i in item.edges)
        if not isinstance(item, NodeItem):
            return None
        node = item.node
        i = item.row_at(item.mapFromScene(pos).y())
        if i is None:
            return _first_span(node.spans)
        row = item.shape_info.rows[i]
        if row.kind == "column" and row.column is not None:
            if row.column.filters:
                return row.column.filters[0].span
            return _first_span(row.column.spans) or _first_span(node.spans)
        if row.kind == "having" and row.filters:
            return row.filters[0].span
        return None

    def selected_span(self) -> Span | None:
        """The selected node's first mention, for "Go to SQL"."""
        node_id = self.selected
        if node_id is None or node_id not in self.nodes:
            return None
        return _first_span(self.nodes[node_id].node.spans)

    def helpEvent(self, event: QGraphicsSceneHelpEvent) -> None:
        text = self.tooltip_at(event.scenePos())
        if text:
            QToolTip.showText(event.screenPos(), text, event.widget())
        else:
            QToolTip.hideText()
        event.accept()

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        rect = QRectF(rect)
        painter.fillRect(rect, _color("dg_canvas"))
        if not self.grid:
            return
        scale = abs(painter.transform().m11()) or 1.0
        step = float(DOT_STEP)
        while step * scale < 9:
            step *= 2
        left = math.floor(rect.left() / step) * step
        top = math.floor(rect.top() / step) * step
        points = [
            QPointF(left + i * step, top + j * step)
            for i in range(int((rect.right() - left) / step) + 1)
            for j in range(int((rect.bottom() - top) / step) + 1)
        ]
        pen = QPen(_color("dg_dot"), 1.6)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.save()
        painter.setPen(pen)
        painter.drawPoints(QPolygonF(points))
        painter.restore()


@dataclass
class _Press:
    """A left press on a node or on a frame's title, until the release."""

    kind: Literal["node", "group"]
    key: str  # node id or group key
    last: QPointF  # in the scene, where the mouse was last
    origin: QPoint  # in the viewport, where it went down
    row: int | None = None
    chevron: bool = False
    dragging: bool = False


class DiagramView(QGraphicsView):
    """Pans by dragging the canvas, zooms with Ctrl+wheel and the strip's
    buttons, and fits the diagram until the user zooms. A click selects a
    node, a drag moves it (or a frame by its title), a double click opens
    the text in the SQL tab."""

    zoom_changed = Signal(int)
    select_all_requested = Signal()  # Ctrl+A acts on the SQL
    span_requested = Signal(int, int)  # select that text in the SQL tab
    filter_requested = Signal(str, str)  # search every query: ("table", name)
    export_requested = Signal(str)  # "png", "svg" or "copy"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = DiagramScene(self)
        self.setScene(self._scene)
        self.setObjectName("DiagramView")
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self._auto_fit = True
        self._press: _Press | None = None
        self._pan_from: QPoint | None = None  # a press on the canvas

    def diagram(self) -> DiagramScene:
        return self._scene

    def show_graph(
        self, graph: QueryGraph, keep: bool = False, lines: LineIndex | None = None
    ) -> None:
        """Draw *graph*, with *lines* numbering its text. A new query starts
        afresh and fitted; *keep* (the same query read again while it is
        edited) keeps what the user moved and where the view is."""
        scene = self._scene
        if lines is not None:
            scene.lines = lines
        if not keep:
            scene.forget()
            scene.headers_only = len(graph.nodes) > CROWDED_NODES
            self._auto_fit = True
        self._redraw(graph)

    def rebuild(self) -> None:
        """Draw the same graph again (the language changed), keeping the zoom."""
        graph = self._scene.graph
        if graph is not None:
            self._redraw(graph)

    def show_columns(self) -> None:
        """Every node with its columns again, after a crowded start."""
        self._scene.headers_only = False
        graph = self._scene.graph
        if graph is not None:
            self.setSceneRect(QRectF())
            self._scene.build(graph)
            self.fit()

    def rearrange(self) -> None:
        self._scene.rearrange()
        self.fit()

    def _redraw(self, graph: QueryGraph) -> None:
        if self._auto_fit:
            self.setSceneRect(QRectF())
            self._scene.build(graph)
            self.fit()
        else:
            self._hold()
            self._scene.build(graph)
            self._hold()

    def _hold(self) -> None:
        """Keep the view where it is while the scene changes: the view's
        rect also covers what is shown, so nothing recentres it."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        self.setSceneRect(self._scene.sceneRect().united(visible))
        self.centerOn(visible.center())

    def clear(self) -> None:
        self._press = None
        self._scene.reset()
        self.setSceneRect(QRectF())
        self._auto_fit = True
        self._set_zoom(1.0)

    def zoom(self) -> float:
        return self.transform().m11()

    def zoom_percent(self) -> int:
        return round(self.zoom() * 100)

    def fit(self) -> None:
        """Show the whole diagram, never above 100 %."""
        self._auto_fit = True
        self.setSceneRect(QRectF())
        scene = self._scene.sceneRect()
        viewport = self.viewport().rect()
        if scene.isEmpty() or viewport.isEmpty():
            self._set_zoom(1.0)
            return
        zoom = min(viewport.width() / scene.width(), viewport.height() / scene.height())
        self._set_zoom(max(min(zoom, 1.0), ZOOM_MIN))
        self.centerOn(scene.center())

    def zoom_in(self) -> None:
        current = self.zoom()
        self.set_zoom(next((z for z in ZOOM_STEPS if z > current + 1e-3), ZOOM_MAX))

    def zoom_out(self) -> None:
        current = self.zoom()
        lower = [z for z in ZOOM_STEPS if z < current - 1e-3]
        self.set_zoom(lower[-1] if lower else ZOOM_MIN)

    def set_zoom(self, zoom: float) -> None:
        """Zoom around the middle of the view; the fit is off from now on."""
        self._auto_fit = False
        centre = self.mapToScene(self.viewport().rect().center())
        self._set_zoom(zoom)
        self.centerOn(centre)

    def _set_zoom(self, zoom: float) -> None:
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        before = self.zoom_percent()
        self.setTransform(QTransform.fromScale(zoom, zoom))
        if self.zoom_percent() != before:
            self.zoom_changed.emit(self.zoom_percent())

    # -- mouse ----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = event.position().toPoint()
        pos = self.mapToScene(point)
        item = self._scene.hit(pos)
        if isinstance(item, NodeItem):
            node_id = item.node.id
            self._scene.select(node_id)
            row = item.row_at(item.mapFromScene(pos).y())
            self._press = _Press("node", node_id, pos, point, row)
            event.accept()
            return
        if isinstance(item, FrameItem):
            local = item.mapFromScene(pos)
            if local.y() < FRAME_TITLE:
                chevron = local.x() < CHEVRON_HIT
                self._press = _Press("group", item.box.key, pos, point, None, chevron)
                event.accept()
                return
        self._pan_from = point
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        press = self._press
        if press is None:
            super().mouseMoveEvent(event)
            return
        point = event.position().toPoint()
        if not press.dragging:
            if (
                point - press.origin
            ).manhattanLength() < QApplication.startDragDistance():
                return
            press.dragging = True
            self._hold()
        pos = self.mapToScene(point)
        delta = pos - press.last
        press.last = pos
        if press.kind == "node":
            self._scene.move_node(press.key, delta)
        else:
            self._scene.move_group(press.key, delta)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        press, self._press = self._press, None
        pan_from, self._pan_from = self._pan_from, None
        if press is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            moved = pan_from is not None and (
                (event.position().toPoint() - pan_from).manhattanLength()
                >= QApplication.startDragDistance()
            )
            if pan_from is not None and not moved:
                self._scene.select(None)
            return
        event.accept()
        if press.dragging:
            self._hold()
        elif press.kind == "group" and press.chevron:
            self._hold()
            self._scene.toggle_group(press.key)
            self._hold()
        elif press.kind == "group":
            self._scene.select(None)
        elif press.row is not None:
            item = self._scene.nodes.get(press.key)
            rows = item.shape_info.rows if item is not None else []
            if press.row < len(rows) and rows[press.row].kind == "more":
                self._hold()
                self._scene.expand(press.key)
                self._hold()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            span = self._scene.span_at(self.mapToScene(event.position().toPoint()))
            if span is not None:
                self._press = None
                self.span_requested.emit(span.start, span.end)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        self._auto_fit = False
        pos = event.position().toPoint()
        before = self.mapToScene(pos)
        self._set_zoom(self.zoom() * 1.0015**delta)
        after = self.mapToScene(pos)
        centre = self.mapToScene(self.viewport().rect().center())
        self.centerOn(centre + (before - after))
        event.accept()

    # -- menus ----------------------------------------------------------------

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        item = self._scene.hit(self.mapToScene(event.pos()))
        if isinstance(item, NodeItem):
            self._scene.select(item.node.id)
            menu = self.node_menu(item.node.id)
        else:
            menu = self.background_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()
        event.accept()

    def node_menu(self, node_id: str) -> QMenu:
        """Go to SQL, Search in all queries (tables only) and Copy name."""
        node = self._scene.nodes[node_id].node
        menu = QMenu(self)
        go = menu.addAction(tr("diagram.go_to_sql"))
        span = _first_span(node.spans)
        go.setEnabled(span is not None)
        if span is not None:
            go.triggered.connect(lambda: self.span_requested.emit(span.start, span.end))
        menu.addSeparator()
        # The search takes one word after "table:"; a name with spaces has none.
        if node.kind == "table" and node.name and not re.search(r"\s", node.name):
            search = menu.addAction(tr("diagram.search_all"))
            search.triggered.connect(
                lambda: self.filter_requested.emit("table", node.name)
            )
        # A result is named after its statement ("SELECT"), not an object.
        name = qualified_name(node) if node.kind != "result" else ""
        copy = menu.addAction(tr("diagram.copy_name"))
        copy.setEnabled(bool(name))
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(name))
        return menu

    def background_menu(self) -> QMenu:
        """Fit, Rearrange and Export."""
        menu = QMenu(self)
        fit = menu.addAction(tr("editor.fit"))
        fit.setShortcut(QKeySequence("Ctrl+0"))  # shown; keyPressEvent handles it
        fit.setShortcutVisibleInContextMenu(True)
        fit.triggered.connect(self.fit)
        rearrange = menu.addAction(tr("diagram.rearrange"))
        rearrange.triggered.connect(self.rearrange)
        menu.addSeparator()
        export = menu.addMenu(tr("diagram.export"))
        add_export_actions(export, self.export_requested.emit)
        export.setEnabled(self._scene.graph is not None)
        return menu

    # -- export ---------------------------------------------------------------

    def render_image(self, scale: float = EXPORT_SCALE) -> QImage:
        """The whole diagram on its canvas, *scale* pixels per canvas pixel,
        with nothing selected."""
        rect = self._scene.content_rect()
        width = max(1, math.ceil(rect.width() * scale))
        height = max(1, math.ceil(rect.height() * scale))
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        # Drawn in canvas pixels, so the cosmetic dots grow with the scale.
        image.setDevicePixelRatio(scale)
        image.fill(_color("dg_canvas"))
        painter = QPainter(image)
        painter.setRenderHints(self.renderHints())
        target = QRectF(0, 0, width / scale, height / scale)
        with self._scene.exporting(grid=True):
            self._scene.render(painter, target, rect)
        painter.end()
        image.setDevicePixelRatio(1.0)
        return image

    def render_svg(self, title: str = "") -> bytes:
        """The whole diagram as an SVG document, on its canvas colour but
        without the dots."""
        rect = self._scene.content_rect()
        size = QSize(max(1, math.ceil(rect.width())), max(1, math.ceil(rect.height())))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        generator = QSvgGenerator()
        generator.setOutputDevice(buffer)
        generator.setSize(size)
        generator.setViewBox(QRectF(0, 0, size.width(), size.height()))
        generator.setTitle(title)
        painter = QPainter(generator)
        painter.setRenderHints(self.renderHints())
        target = QRectF(0, 0, size.width(), size.height())
        with self._scene.exporting(grid=False):
            self._scene.render(painter, target, rect)
        painter.end()
        buffer.close()
        return bytes(buffer.data().data())

    # -- keyboard -------------------------------------------------------------

    def event(self, event: QEvent) -> bool:
        # Esc clears the selection before any window shortcut sees it.
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
            and self._scene.selected is not None
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._scene.selected is not None:
            self._scene.select(None)
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all_requested.emit()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if key == Qt.Key.Key_0:
                self.fit()
                return
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                self.zoom_in()
                return
            if key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self.zoom_out()
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit()

    def refresh_theme(self) -> None:
        self._scene.refresh_selection()
        for item in self._scene.items():
            item.update()
        self.viewport().update()

    def item_at(self, x: float, y: float) -> QGraphicsItem | None:
        """The topmost item at scene point (*x*, *y*), for tests."""
        return self._scene.itemAt(QPointF(x, y), QTransform())


# ---------------------------------------------------------------------------
# The pane under the details panel
# ---------------------------------------------------------------------------


class DiagramNotice(QFrame):
    """A notice above the canvas: an icon, a text, maybe an action, and ✕."""

    dismissed = Signal()

    def __init__(
        self, icon_name: str, color_key: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DiagramNotice")
        self._icon_name = icon_name
        self._color_key = color_key
        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.button = QToolButton()
        self.button.setObjectName("DiagramNoticeButton")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.hide()
        self._close = IconButton("close", size=20)
        self._close.clicked.connect(lambda: self.dismissed.emit())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 5, 5)
        layout.setSpacing(8)
        layout.addWidget(self._icon)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.button)
        layout.addWidget(self._close)
        self.refresh_theme()

    def set_action(self, text: str) -> None:
        self.button.setText(text)
        self.button.show()

    def set_close_tooltip(self, text: str) -> None:
        self._close.set_tooltip(text)

    def refresh_theme(self) -> None:
        self._icon.setPixmap(icons.pixmap(self._icon_name, self._color_key, 16))
        self._close.refresh_theme()


NoticeKind = Literal["partial", "dynamic", "crowded"]


class DiagramPane(QWidget):
    """The Diagram tab: notices over the view, or a message while there is
    nothing to draw. It draws the whole graph or one statement of it, with or
    without the results."""

    span_requested = Signal(int, int)
    filter_requested = Signal(str, str)
    displayed_changed = Signal(object)  # the graph drawn, or None
    analyze_requested = Signal()  # "Analyze anyway" on a large text
    export_requested = Signal(str)  # "png", "svg" or "copy"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DiagramPane")
        self._state: PaneState = "blank"
        self._graph: QueryGraph | None = None
        self._shown: QueryGraph | None = None
        self._statement = 0  # 0: every statement
        self._show_results = True
        self._lines = LineIndex()
        self._query = ""
        self._dismissed: set[tuple[str, NoticeKind]] = set()  # for this session

        self.view = DiagramView()
        self.view.span_requested.connect(self.span_requested)
        self.view.filter_requested.connect(self.filter_requested)
        self.view.export_requested.connect(self.export_requested)
        self._message = QWidget()
        self._message.setObjectName("DiagramMessage")
        self._message.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = QLabel()
        self._title.setObjectName("DiagramMessageTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setWordWrap(True)
        self._detail = QLabel()
        self._detail.setObjectName("DiagramMessageDetail")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)
        self._large_button = QPushButton()
        self._large_button.setObjectName("PanelButton")
        self._large_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._large_button.clicked.connect(lambda: self.analyze_requested.emit())
        message = QVBoxLayout(self._message)
        message.setContentsMargins(24, 24, 24, 24)
        message.setSpacing(8)
        message.addStretch(1)
        message.addWidget(self._icon)
        message.addWidget(self._title)
        message.addWidget(self._detail)
        message.addWidget(self._large_button, 0, Qt.AlignmentFlag.AlignHCenter)
        message.addStretch(1)

        self._partial = DiagramNotice("alert", "warning")
        self._partial.button.clicked.connect(self._go_to_partial)
        self._dynamic = DiagramNotice("info", "text2")
        self._crowded = DiagramNotice("info", "text2")
        self._crowded.button.clicked.connect(self._show_columns)
        self._notice_of: dict[NoticeKind, DiagramNotice] = {
            "partial": self._partial,
            "dynamic": self._dynamic,
            "crowded": self._crowded,
        }
        self._notices = QWidget()
        self._notices.setObjectName("DiagramNotices")
        self._notices.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        notices = QVBoxLayout(self._notices)
        notices.setContentsMargins(12, 10, 12, 0)
        notices.setSpacing(6)
        for kind, notice in self._notice_of.items():
            notice.dismissed.connect(lambda kind=kind: self._dismiss(kind))
            notices.addWidget(notice)

        body = QWidget()
        self._stack = QStackedLayout(body)
        self._stack.addWidget(self.view)
        self._stack.addWidget(self._message)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._notices)
        layout.addWidget(body, 1)

        self._analyzing = QTimer(self)
        self._analyzing.setSingleShot(True)
        self._analyzing.setInterval(ANALYZING_DELAY_MS)
        self._analyzing.timeout.connect(self._show_analyzing)
        self.clear()

    # -- state ----------------------------------------------------------------

    def state(self) -> PaneState:
        return self._state

    def graph(self) -> QueryGraph | None:
        """The graph of the whole text."""
        return self._graph

    def displayed(self) -> QueryGraph | None:
        """What is drawn: one statement's graph, without the results when they
        are hidden; ``None`` without a diagram."""
        return self._shown if self._state == "diagram" else None

    def clear(self) -> None:
        """Nothing selected: an empty canvas."""
        self._analyzing.stop()
        self._graph = self._shown = None
        self.view.clear()
        self._set_state("blank")
        self._update_notices()
        self.displayed_changed.emit(None)

    def begin_analysis(self) -> None:
        """A new text is being read: blank now, "Analyzing…" if it takes long."""
        self._graph = self._shown = None
        self.view.clear()
        self._set_state("blank")
        self._update_notices()
        self._analyzing.start()
        self.displayed_changed.emit(None)

    def show_graph(
        self, graph: QueryGraph, text: str = "", query: str = "", keep: bool = False
    ) -> None:
        """Draw *graph*, read from *text* of *query*. A new query starts with
        every statement; *keep* is the same query read again while edited."""
        self._analyzing.stop()
        self._graph = graph
        self._lines = LineIndex(text)
        self._query = query
        if not keep:
            self._statement = 0
        if self._statement and not any(
            s.index == self._statement and s.status != "skipped"
            for s in graph.statements
        ):
            self._statement = 0
        self._display(keep)

    def show_large(self, query: str = "") -> None:
        """A text too large to read on its own: wait for "Analyze anyway"."""
        self._analyzing.stop()
        self._graph = self._shown = None
        self._query = query
        self.view.clear()
        self._set_state("large")
        self._update_notices()
        self.displayed_changed.emit(None)

    def _display(self, keep: bool) -> None:
        graph = self._graph
        if graph is None:
            return
        shown = graph.for_statement(self._statement) if self._statement else graph
        if not self._show_results:
            shown = without_results(shown)
        self._shown = shown
        if "failed" in graph.notes:
            self.view.clear()
            self._set_state("failed")
        elif not shown.nodes:
            self.view.clear()
            self._set_state("empty")
        else:
            keep = keep and self._state == "diagram"
            self._set_state("diagram")
            self.view.show_graph(shown, keep, self._lines)
        self._update_notices()
        self.displayed_changed.emit(self.displayed())

    def statement(self) -> int:
        return self._statement

    def set_statement(self, index: int) -> None:
        """Draw statement *index* alone; 0 draws them all."""
        if index == self._statement:
            return
        self._statement = index
        self._display(keep=False)

    def show_results(self) -> bool:
        return self._show_results

    def set_show_results(self, show: bool) -> None:
        if show == self._show_results:
            return
        self._show_results = show
        self._display(keep=True)

    def rearrange(self) -> None:
        if self._state == "diagram":
            self.view.rearrange()

    def _show_analyzing(self) -> None:
        if self._state == "blank":
            self._set_state("analyzing")

    def _set_state(self, state: PaneState) -> None:
        self._state = state
        self._stack.setCurrentWidget(self.view if state == "diagram" else self._message)
        self._update_message()

    def _update_message(self) -> None:
        state = self._state
        self._icon.setVisible(state == "empty")
        self._detail.setVisible(state in ("empty", "failed"))
        self._large_button.setVisible(state == "large")
        if state == "empty":
            self._icon.setPixmap(icons.pixmap("diagram", "text3", 28))
            self._title.setText(tr("diagram.empty_title"))
            self._detail.setText(tr("diagram.empty_detail"))
        elif state == "failed":
            self._title.setText(tr("diagram.failed"))
            self._detail.setText(self._graph.error if self._graph else "")
        elif state == "analyzing":
            self._title.setText(tr("diagram.analyzing"))
        elif state == "large":
            self._title.setText(tr("diagram.large_title"))
            self._large_button.setText(tr("diagram.large_button"))
        else:
            self._title.clear()
        self._title.setProperty("state", state)
        self._title.style().unpolish(self._title)
        self._title.style().polish(self._title)

    # -- notices --------------------------------------------------------------

    def notices(self) -> list[NoticeKind]:
        """The notices shown, top to bottom."""
        if self._notices.isHidden():
            return []
        return [k for k, n in self._notice_of.items() if not n.isHidden()]

    def _update_notices(self) -> None:
        graph = self._graph
        unread = [s for s in graph.statements if s.status != "ok"] if graph else []
        wanted: dict[NoticeKind, bool] = {
            "partial": bool(unread),
            "dynamic": graph is not None and "dynamic_sql" in graph.notes,
            "crowded": self._state == "diagram" and self.view.diagram().headers_only,
        }
        active = graph is not None and self._state in ("diagram", "empty")
        if unread:
            first, issue = unread[0], unread[0].issue
            if issue is None:
                text = ntr(
                    "diagram.partial_one",
                    "diagram.partial_other",
                    len(unread),
                    line=first.line,
                )
            else:
                text = ntr(
                    "diagram.partial_near_one",
                    "diagram.partial_near_other",
                    len(unread),
                    near=issue.near,
                    line=issue.line,
                )
            self._partial.label.setText(text)
            self._partial.set_action(tr("diagram.go_to_line"))
        self._dynamic.label.setText(tr("diagram.dynamic_sql"))
        self._crowded.label.setText(tr("diagram.crowded"))
        self._crowded.set_action(tr("diagram.show_columns"))
        shown = False
        for kind, notice in self._notice_of.items():
            show = (
                active and wanted[kind] and (self._query, kind) not in self._dismissed
            )
            notice.setVisible(show)
            notice.set_close_tooltip(tr("diagram.dismiss"))
            shown = shown or show
        self._notices.setVisible(shown)

    def _dismiss(self, kind: NoticeKind) -> None:
        """✕: that notice stays away for this query until the app closes."""
        self._dismissed.add((self._query, kind))
        self._update_notices()

    def _go_to_partial(self) -> None:
        """Selects the token sqlglot stopped at, or else goes to the start of
        the first statement it could not fully read."""
        graph = self._graph
        unread = [s for s in graph.statements if s.status != "ok"] if graph else []
        if not unread:
            return
        issue = unread[0].issue
        if issue is None:
            start = unread[0].span.start
            self.span_requested.emit(start, start)
        else:
            self.span_requested.emit(issue.span.start, issue.span.end)

    def _show_columns(self) -> None:
        self.view.show_columns()
        self._update_notices()

    # -- view -----------------------------------------------------------------

    def fit(self) -> None:
        self.view.fit()

    def zoom_in(self) -> None:
        self.view.zoom_in()

    def zoom_out(self) -> None:
        self.view.zoom_out()

    def reset_zoom(self) -> None:
        self.view.set_zoom(1.0)

    def zoom_percent(self) -> int:
        return self.view.zoom_percent()

    def refresh_theme(self) -> None:
        self.view.refresh_theme()
        for notice in self._notice_of.values():
            notice.refresh_theme()
        self._update_message()

    def retranslate_ui(self) -> None:
        self.view.rebuild()
        self._update_message()
        self._update_notices()

    def focus_view(self) -> None:
        if self._state == "diagram":
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def map_point(self, x: float, y: float) -> QPoint:
        """Scene point (*x*, *y*) in the view's viewport, for tests."""
        return self.view.mapFromScene(QPointF(x, y))
