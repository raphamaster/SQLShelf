"""The query diagram: the graph read from the SQL text alone, drawn on a canvas.

The graph comes from a worker (``build_query_graph`` never runs on the UI
thread); this module measures each node, asks ``graph_layout.layout`` where it
goes and draws the result as ``QGraphicsItem``s. The items read the palette
when they paint, so a theme switch is a repaint; a language switch rebuilds
the scene from the same graph.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QStackedLayout,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from ..core.graph_layout import (
    FRAME_TITLE,
    GroupBox,
    Layout,
    Point,
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
)
from ..core.sql_objects import pick_dialect
from . import icons
from . import tokens as _tk
from .theme import mono_font, qcolor, ui_font

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

PaneState = Literal["blank", "analyzing", "diagram", "empty", "failed"]

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


def node_rows(node: GraphNode, expanded: bool = False) -> list[Row]:
    """The rows under a node's header: up to ``MAX_ROWS`` columns, then
    "+N columns", then the HAVING footer."""
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
    graph: QueryGraph, expanded: set[str] | None = None
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
            rows=node_rows(node, node.id in expanded),
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
    """A group's frame: tinted body and a title bar in the group's colour."""

    def __init__(self, box: GroupBox) -> None:
        super().__init__()
        self.box = box
        self.color = _tk.diagram_group_color(box.kind, box.name)
        r = box.rect
        self.setPos(r.x, r.y)
        self._rect = QRectF(0, 0, r.w, r.h)
        self.setZValue(Z_FRAME)

    def title(self) -> tuple[str, str]:
        """The frame's name and the kind word after it."""
        if self.box.kind == "schema":
            return self.box.name, tr("diagram.group_schema")
        return tr(f"diagram.group_{self.box.kind}"), ""

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
        icons.paint(painter, "chevron_down", chevron, _tk.color("dg_ink"))
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
        self.setPos(x, y)
        self._rect = QRectF(0, 0, width, shape.height)
        self.setZValue(Z_NODE)

    @property
    def node(self) -> GraphNode:
        return self.shape_info.node

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        shape = self.shape_info
        radius = _tk.RADIUS
        rect = self._rect.adjusted(0.5, 0.5, -0.5, -0.5)
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

        painter.setPen(QPen(_color("border"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
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
        if keyed:
            fill = QPainterPath()
            fill.addRect(band)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("dg_row_key"))
            painter.drawPath(outline.intersected(fill))
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


EdgeKind = Literal["join", "flow"]


class EdgeItem(QGraphicsItem):
    """A join (solid, dots at the ends) or a flow (dashed, with an arrow)."""

    def __init__(
        self,
        kind: EdgeKind,
        points: list[Point],
        optional: str | None = None,
        edges: list[int] | None = None,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.points = points
        self.optional = optional  # "source", "target" or "both"
        self.edges = edges or []  # indexes in ``graph.edges``
        self._path = edge_path(points)
        self._bounds = self._path.boundingRect().adjusted(-8, -8, 8, 8)
        self.setZValue(Z_EDGE)

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "flow":
            color = _color("dg_flow", 0.85)
            pen = QPen(color, 1.5)
            pen.setDashPattern([4, 8 / 3])
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawPath(self._path)
            self._paint_arrow(painter, color)
            return
        color = _color("dg_join")
        painter.setPen(QPen(color, 1.5))
        painter.drawPath(self._path)
        for end, point in (("source", self.points[0]), ("target", self.points[-1])):
            hollow = self.optional in (end, "both")
            centre = QPointF(*point)
            if hollow:
                painter.setPen(QPen(color, 1.5))
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
        width = _width(fonts().mono, text) + 2 * LABEL_PAD
        self._rect = QRectF(-width / 2, -LABEL_HEIGHT / 2, width, LABEL_HEIGHT)
        self.setPos(*centre)
        self.setZValue(Z_LABEL)

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
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
    """The canvas: a dot grid under the frames, nodes and edges."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.graph: QueryGraph | None = None
        self.layout_info: Layout | None = None
        self.shapes: dict[str, NodeShape] = {}
        self.nodes: dict[str, NodeItem] = {}
        self.frames: list[FrameItem] = []
        self.edges: list[EdgeItem] = []
        self.labels: list[LabelItem] = []

    def build(self, graph: QueryGraph) -> None:
        """Measure, lay out and draw *graph*, replacing what was there."""
        self.clear()
        self.nodes, self.frames, self.edges, self.labels = {}, [], [], []
        self.graph = graph
        self.shapes = measure(graph)
        info = diagram_layout(graph, self.shapes)
        self.layout_info = info
        for box in info.groups:
            frame = FrameItem(box)
            self.addItem(frame)
            self.frames.append(frame)
        for node_id, rect in info.nodes.items():
            item = NodeItem(self.shapes[node_id], rect.x, rect.y, rect.w)
            self.addItem(item)
            self.nodes[node_id] = item
        for i, points in info.routes.items():
            edge = graph.edges[i]
            self._add_edge(edge, points, [i])
            if edge.label and i in info.labels:
                label = LabelItem(edge.kind, edge.label, info.labels[i], i)
                self.addItem(label)
                self.labels.append(label)
        for bundle in info.bundles:
            self._add_edge(None, bundle.points, list(bundle.edges))
        w, h = info.size
        self.setSceneRect(QRectF(-MARGIN, -MARGIN, w + 2 * MARGIN, h + 2 * MARGIN))

    def _add_edge(
        self, edge: GraphEdge | None, points: list[Point], members: list[int]
    ) -> None:
        if len(points) < 2:
            return
        if edge is None:
            item = EdgeItem("flow", points, edges=members)
        else:
            item = EdgeItem(edge.kind, points, edge.optional_side, members)
        self.addItem(item)
        self.edges.append(item)

    def reset(self) -> None:
        self.clear()
        self.graph = None
        self.layout_info = None
        self.shapes = {}
        self.nodes, self.frames, self.edges, self.labels = {}, [], [], []
        self.setSceneRect(QRectF())

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        rect = QRectF(rect)
        painter.fillRect(rect, _color("dg_canvas"))
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


class DiagramView(QGraphicsView):
    """Pans by dragging the canvas, zooms with Ctrl+wheel and the strip's
    buttons, and fits the diagram until the user zooms."""

    zoom_changed = Signal(int)
    select_all_requested = Signal()  # Ctrl+A acts on the SQL

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

    def diagram(self) -> DiagramScene:
        return self._scene

    def show_graph(self, graph: QueryGraph) -> None:
        """Draw *graph* and fit it, as a newly selected query starts."""
        self._scene.build(graph)
        self.fit()

    def rebuild(self) -> None:
        """Draw the same graph again (the language changed), keeping the zoom."""
        graph = self._scene.graph
        if graph is None:
            return
        self._scene.build(graph)
        if self._auto_fit:
            self.fit()

    def clear(self) -> None:
        self._scene.reset()
        self._auto_fit = True
        self._set_zoom(1.0)

    def zoom(self) -> float:
        return self.transform().m11()

    def zoom_percent(self) -> int:
        return round(self.zoom() * 100)

    def fit(self) -> None:
        """Show the whole diagram, never above 100 %."""
        self._auto_fit = True
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

    def keyPressEvent(self, event: QKeyEvent) -> None:
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
        for item in self._scene.items():
            item.update()
        self.viewport().update()

    def item_at(self, x: float, y: float) -> QGraphicsItem | None:
        """The topmost item at scene point (*x*, *y*), for tests."""
        return self._scene.itemAt(QPointF(x, y), QTransform())


# ---------------------------------------------------------------------------
# The pane under the details panel
# ---------------------------------------------------------------------------


class DiagramPane(QWidget):
    """The Diagram tab: the view, or a message while there is nothing to draw."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DiagramPane")
        self._state: PaneState = "blank"
        self._graph: QueryGraph | None = None

        self.view = DiagramView()
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
        message = QVBoxLayout(self._message)
        message.setContentsMargins(24, 24, 24, 24)
        message.setSpacing(8)
        message.addStretch(1)
        message.addWidget(self._icon)
        message.addWidget(self._title)
        message.addWidget(self._detail)
        message.addStretch(1)

        self._stack = QStackedLayout(self)
        self._stack.addWidget(self.view)
        self._stack.addWidget(self._message)

        self._analyzing = QTimer(self)
        self._analyzing.setSingleShot(True)
        self._analyzing.setInterval(ANALYZING_DELAY_MS)
        self._analyzing.timeout.connect(self._show_analyzing)
        self.clear()

    # -- state ----------------------------------------------------------------

    def state(self) -> PaneState:
        return self._state

    def graph(self) -> QueryGraph | None:
        return self._graph

    def clear(self) -> None:
        """Nothing selected: an empty canvas."""
        self._analyzing.stop()
        self._graph = None
        self.view.clear()
        self._set_state("blank")

    def begin_analysis(self) -> None:
        """A new text is being read: blank now, "Analyzing…" if it takes long."""
        self._graph = None
        self.view.clear()
        self._set_state("blank")
        self._analyzing.start()

    def show_graph(self, graph: QueryGraph) -> None:
        self._analyzing.stop()
        self._graph = graph
        if "failed" in graph.notes:
            self.view.clear()
            self._set_state("failed")
        elif not graph.nodes:
            self.view.clear()
            self._set_state("empty")
        else:
            self._set_state("diagram")
            self.view.show_graph(graph)

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
        if state == "empty":
            self._icon.setPixmap(icons.pixmap("diagram", "text3", 28))
            self._title.setText(tr("diagram.empty_title"))
            self._detail.setText(tr("diagram.empty_detail"))
        elif state == "failed":
            self._title.setText(tr("diagram.failed"))
            self._detail.setText(self._graph.error if self._graph else "")
        elif state == "analyzing":
            self._title.setText(tr("diagram.analyzing"))
        else:
            self._title.clear()
        self._title.setProperty("state", state)
        self._title.style().unpolish(self._title)
        self._title.style().polish(self._title)

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
        self._update_message()

    def retranslate_ui(self) -> None:
        self.view.rebuild()
        self._update_message()

    def focus_view(self) -> None:
        if self._state == "diagram":
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def map_point(self, x: float, y: float) -> QPoint:
        """Scene point (*x*, *y*) in the view's viewport, for tests."""
        return self.view.mapFromScene(QPointF(x, y))
