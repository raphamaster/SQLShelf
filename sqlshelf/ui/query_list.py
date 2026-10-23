"""Middle panel: the query list, in the Details or the Content view.

A QTreeView over a QStandardItemModel. Each query is a single item in column 0
that holds its SearchResult; the other columns are painted from it, so a long
result list costs one item per row. Groups (by date or by folder) are parent
rows spanning every column. The view paints row backgrounds, and the delegate
of the current view mode paints cell contents, reading colours at paint time,
so a theme switch only needs a repaint.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QLocale,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QFont,
    QHelpEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QMenu,
    QStackedLayout,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.grouping import (
    display_name,
    group_by_date,
    group_by_folder,
    modified_label,
)
from ..core.i18n import tr
from ..core.models import SearchResult
from ..core.text_match import highlight_terms, match_spans
from . import icons
from . import tokens as _tk
from .paint import draw_marked_text
from .theme import mono_font, qcolor, qss

# ── Sort keys ───────────────────────────────────────────────────────────────
_SORT_NAME_ASC = "name_asc"
_SORT_NAME_DESC = "name_desc"
_SORT_MTIME_DESC = "mtime_desc"
_SORT_MTIME_ASC = "mtime_asc"
# Menu order; the separator in the Sort menu goes between the two pairs.
SORT_KEYS = (_SORT_NAME_ASC, _SORT_NAME_DESC, _SORT_MTIME_DESC, _SORT_MTIME_ASC)

GROUP_MODES = ("date", "folder", "none")
VIEW_MODES = ("details", "content")

# ── Columns ─────────────────────────────────────────────────────────────────
COL_STAR, COL_NAME, COL_TABLES, COL_TAGS, COL_MODIFIED = range(5)
_COLUMNS = 5
_COLUMN_LABELS = (
    "",
    "list.col_name",
    "list.col_tables",
    "list.col_tags",
    "list.col_modified",
)

# ── Item data roles ─────────────────────────────────────────────────────────
_ROLE_RESULT = Qt.ItemDataRole.UserRole  # SearchResult, on a query row
_ROLE_GROUP = Qt.ItemDataRole.UserRole + 1  # "date:<bucket>" | "folder:<label>"
_ROLE_COUNT = Qt.ItemDataRole.UserRole + 2  # queries in a group

# ── Geometry, in logical px (docs/redesign-ui.md, reference screens) ────────
ROW_HEIGHT = 34
CONTENT_ROW_HEIGHT = 76
GROUP_HEIGHT = 36  # a 32 px band under a 4 px gap
_GROUP_BAND = 32
HEADER_HEIGHT = 32
_INSET = 8  # row background, from each side of the view
_CELL_PAD = 8
_STAR_W = 36
_TABLES_W = 132
_TAGS_W = 150
_MODIFIED_W = 128  # includes the row inset on the right
_NAME_TEXT_X = 30  # name text, from the start of the Name column
_GROUP_CHEVRON_X = 14
_GROUP_TEXT_X = 35
# Below these list widths the Tags, then the Tables column make room for Name.
_HIDE_TAGS_BELOW = 620
_HIDE_TABLES_BELOW = 480
_PILL_H = 20
_PILL_PAD = 8
_PILL_DOT = 6
_PILL_DOT_GAP = 5
_PILL_GAP = 4
_CHIP_H = 18
_CHIP_PAD = 6
_CONTENT_TOP = 12  # first line of a Content row, from the top of the row
_CONTENT_RIGHT = 20  # Content row text, from the right edge of the view


def _lead(index: QModelIndex | QPersistentModelIndex) -> QModelIndex:
    """Column 0 of the row of *index*: the item that holds the row's data."""
    return index.sibling(index.row(), 0)


def _result_key(result: SearchResult | None) -> tuple[object, str] | None:
    if result is None:
        return None
    return result.folder, result.rel_path


def _font(base: QFont, pixel_size: int, weight: QFont.Weight | None = None) -> QFont:
    font = QFont(base)
    font.setPixelSize(pixel_size)
    if weight is not None:
        font.setWeight(weight)
    return font


def _tabular(font: QFont) -> QFont:
    """*font* with tabular figures, so dates line up (Qt 6.7 and later)."""
    try:
        font.setFeature(QFont.Tag("tnum"), 1)
    except AttributeError:
        pass
    return font


def _star_rect(cell: QRect, content: bool) -> QRect:
    """The star glyph in a star *cell*: centred on a Details row, on the title
    line of a Content row."""
    top = cell.top() + _CONTENT_TOP + 1 if content else cell.center().y() - 8
    return QRect(cell.left() + 13, top, 16, 16)


def _draw_text(
    painter: QPainter,
    rect: QRect,
    text: str,
    color_key: str,
    terms: list[str] | None = None,
) -> int:
    """Draw *text* elided into *rect*, the parts that match *terms* marked.
    Return the drawn width."""
    spans = match_spans(text, terms) if terms else None
    return draw_marked_text(painter, rect, text, color_key, spans)


def _paint_tags(
    painter: QPainter, rect: QRect, tags: list[str], font: QFont, height: int
) -> None:
    """Tag pills from the left of *rect*, as many as fit, then "+N"."""
    painter.setFont(font)
    metrics = painter.fontMetrics()
    top = rect.top() + (rect.height() - height) // 2
    x = rect.left()
    for i, tag in enumerate(tags):
        text_w = metrics.horizontalAdvance(tag)
        width = _PILL_PAD + _PILL_DOT + _PILL_DOT_GAP + text_w + _PILL_PAD
        rest = len(tags) - i - 1
        more_w = _PILL_GAP + metrics.horizontalAdvance(f"+{rest}") if rest else 0
        if x + width + more_w > rect.right() + 1:
            more = QRect(x, rect.top(), rect.right() - x + 1, rect.height())
            _draw_text(painter, more, f"+{len(tags) - i}", "text3")
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(qcolor(_tk.color("sel_soft"))))
        painter.drawRoundedRect(QRectF(x, top, width, height), height / 2, height / 2)
        painter.setBrush(QBrush(qcolor(_tk.tag_color(tag))))
        painter.drawEllipse(
            QRectF(x + _PILL_PAD, top + (height - _PILL_DOT) / 2, _PILL_DOT, _PILL_DOT)
        )
        text_x = x + _PILL_PAD + _PILL_DOT + _PILL_DOT_GAP
        _draw_text(painter, QRect(text_x, top, text_w + 1, height), tag, "text2")
        x += width + _PILL_GAP


def _paint_table_chips(
    painter: QPainter,
    rect: QRect,
    tables: list[str],
    font: QFont,
    terms: list[str],
) -> int:
    """Outlined mono chips, one per table, as many as fit, then "+N".
    Return the x where the next element can start."""
    painter.setFont(font)
    metrics = painter.fontMetrics()
    top = rect.top() + (rect.height() - _CHIP_H) // 2
    x = rect.left()
    for i, table in enumerate(tables):
        text_w = metrics.horizontalAdvance(table)
        width = _CHIP_PAD + text_w + _CHIP_PAD
        rest = len(tables) - i - 1
        more_w = _PILL_GAP + metrics.horizontalAdvance(f"+{rest}") if rest else 0
        if x + width + more_w > rect.right() + 1:
            more = QRect(x, rect.top(), rect.right() - x + 1, rect.height())
            return x + _draw_text(painter, more, f"+{len(tables) - i}", "text3") + 8
        painter.setPen(QPen(qcolor(_tk.color("border_strong")), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            QRectF(x + 0.5, top + 0.5, width - 1, _CHIP_H - 1),
            _tk.RADIUS_SM,
            _tk.RADIUS_SM,
        )
        text_rect = QRect(x + _CHIP_PAD, top, text_w + 1, _CHIP_H)
        _draw_text(painter, text_rect, table, "text2", terms)
        x += width + _PILL_GAP
    return x + (4 if tables else 0)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


class _ListHeader(QHeaderView):
    """Column labels in ``text3``; the sorted column in ``text``, semibold,
    with a chevron for the direction. Only Name and Modified sort."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("QueryListHeader")
        self.setSectionsClickable(True)
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(False)
        self.setFixedHeight(HEADER_HEIGHT)
        self.sort_key = _SORT_MTIME_DESC

    def _sorted_column(self) -> int:
        return COL_NAME if self.sort_key.startswith("name") else COL_MODIFIED

    def paintSection(  # noqa: N802
        self, painter: QPainter, rect: QRect, logicalIndex: int  # noqa: N803
    ) -> None:
        painter.save()
        painter.fillRect(rect, qcolor(_tk.color("bg")))
        painter.setPen(QPen(qcolor(_tk.color("border")), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        model = self.model()
        label = (
            model.headerData(logicalIndex, Qt.Orientation.Horizontal) if model else ""
        )
        if label:
            is_sorted = logicalIndex == self._sorted_column()
            font = _font(
                self.font(),
                _tk.FONT_PX_SMALL,
                QFont.Weight.DemiBold if is_sorted else None,
            )
            painter.setFont(font)
            text_rect = QRect(
                rect.left() + _CELL_PAD,
                rect.top(),
                rect.width() - _CELL_PAD * 2 - 16,
                rect.height(),
            )
            width = _draw_text(
                painter, text_rect, str(label), "text" if is_sorted else "text3"
            )
            if is_sorted:
                chevron = (
                    "chevron_up" if self.sort_key.endswith("asc") else "chevron_down"
                )
                icons.icon(chevron, "text2", 12).paint(
                    painter,
                    QRect(text_rect.left() + width + 4, rect.center().y() - 6, 12, 12),
                )
        painter.restore()


# ---------------------------------------------------------------------------
# Tree view
# ---------------------------------------------------------------------------


class _QueryTree(QTreeView):
    """The list's item view: rounded row backgrounds, clickable group rows and
    stars, and keyboard moves that step over group rows."""

    star_clicked = Signal(object)  # SearchResult

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QueryList")
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(False)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(False)
        self.setAllColumnsShowFocus(True)
        self.setSortingEnabled(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.terms: list[str] = []  # highlighted in Name and Tables
        self.content_mode = False
        self._hover: SearchResult | None = None
        self._hover_star = False
        self._hand = False

    # ── Lookups ─────────────────────────────────────────────────────────

    @staticmethod
    def result_at(index: QModelIndex | QPersistentModelIndex) -> SearchResult | None:
        if not index.isValid():
            return None
        result = _lead(index).data(_ROLE_RESULT)
        return result if isinstance(result, SearchResult) else None

    @staticmethod
    def is_group(index: QModelIndex | QPersistentModelIndex) -> bool:
        return index.isValid() and _lead(index).data(_ROLE_GROUP) is not None

    def is_hot(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        """Whether the row of *index* is hovered or selected, so its star shows."""
        result = self.result_at(index)
        if result is None:
            return False
        if result is self._hover:
            return True
        selection = self.selectionModel()
        return selection is not None and selection.isSelected(_lead(index))

    def star_hovered(self, result: SearchResult) -> bool:
        return self._hover_star and result is self._hover

    def _star_hit(self, pos: QPoint) -> SearchResult | None:
        index = self.indexAt(pos)
        result = self.result_at(index)
        if result is None or self.isColumnHidden(COL_STAR):
            return None
        cell = self.visualRect(index.sibling(index.row(), COL_STAR))
        target = _star_rect(cell, self.content_mode).adjusted(-4, -4, 4, 4)
        return result if target.contains(pos) else None

    # ── Painting ────────────────────────────────────────────────────────

    def drawRow(  # noqa: N802
        self,
        painter: QPainter,
        options: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        result = self.result_at(index)
        if result is not None:
            selection = self.selectionModel()
            selected = selection is not None and selection.isSelected(_lead(index))
            if selected or result is self._hover:
                band = QRectF(
                    _INSET,
                    options.rect.top() + 1,
                    self.viewport().width() - 2 * _INSET,
                    options.rect.height() - 2,
                )
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(
                    QBrush(qcolor(_tk.color("sel" if selected else "hover")))
                )
                painter.drawRoundedRect(band, _tk.RADIUS, _tk.RADIUS)
                painter.restore()
        super().drawRow(painter, options, index)

    def drawBranches(self, painter, rect, index) -> None:  # noqa: N802
        """Group chevrons are drawn by the delegate."""

    # ── Mouse ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        if self.is_group(index):
            if event.button() == Qt.MouseButton.LeftButton:
                lead = _lead(index)
                self.setExpanded(lead, not self.isExpanded(lead))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            star = self._star_hit(pos)
            if star is not None:
                self.star_clicked.emit(star)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # The press of a double click already toggled the group or the star.
        pos = event.position().toPoint()
        if self.is_group(self.indexAt(pos)) or self._star_hit(pos) is not None:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        hover = self.result_at(index)
        hover_star = self._star_hit(pos) is not None
        if hover is not self._hover or hover_star != self._hover_star:
            self._hover = hover
            self._hover_star = hover_star
            self.viewport().update()
        hand = hover_star or self.is_group(index)
        if hand != self._hand:
            self._hand = hand
            self.viewport().setCursor(
                Qt.CursorShape.PointingHandCursor
                if hand
                else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._hover is not None:
            self._hover = None
            self._hover_star = False
            self.viewport().update()
        super().leaveEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            text = self._tooltip(event.pos())
            if text:
                QToolTip.showText(event.globalPos(), text, self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def _tooltip(self, pos: QPoint) -> str:
        index = self.indexAt(pos)
        result = self.result_at(index)
        if result is None:
            return ""
        if self._star_hit(pos) is not None:
            key = "remove_favorite" if result.is_favorite else "add_favorite"
            return tr(f"context_menu.{key}")
        column = index.column()
        if column == COL_TABLES and not self.content_mode:
            return "\n".join(result.tables)
        if column == COL_TAGS and not self.content_mode:
            return "  ".join(f"#{tag}" for tag in result.tags)
        if column == COL_MODIFIED and not self.content_mode:
            return modified_label(result.file_mtime, datetime.now())
        name, has_title = display_name(result)
        return f"{name}\n{result.rel_path}" if has_title else result.rel_path

    # ── Keyboard ────────────────────────────────────────────────────────

    def moveCursor(  # noqa: N802
        self,
        cursorAction: QAbstractItemView.CursorAction,  # noqa: N803
        modifiers: Qt.KeyboardModifier,
    ) -> QModelIndex:
        action = QAbstractItemView.CursorAction
        current = self.currentIndex()
        if cursorAction in (action.MoveLeft, action.MoveRight):
            return current
        index = super().moveCursor(cursorAction, modifiers)
        if not self.is_group(index):
            return index
        forward = cursorAction in (
            action.MoveDown,
            action.MoveNext,
            action.MovePageDown,
            action.MoveHome,
        )
        found = self._step_to_query(index, forward)
        if not found.isValid():
            found = self._step_to_query(index, not forward)
        return found if found.isValid() else current

    def _step_to_query(self, index: QModelIndex, forward: bool) -> QModelIndex:
        while index.isValid() and self.is_group(index):
            index = self.indexBelow(index) if forward else self.indexAbove(index)
        return index


# ---------------------------------------------------------------------------
# Delegates
# ---------------------------------------------------------------------------


class _Delegate(QStyledItemDelegate):
    """What both view modes share: group rows and the star column."""

    row_height = ROW_HEIGHT

    def __init__(self, view: _QueryTree) -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        if self._view.is_group(index):
            return QSize(0, GROUP_HEIGHT)
        return QSize(0, self.row_height)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._view.is_group(index):
            if index.column() == 0:
                self._paint_group(painter, option, index)
        else:
            result = self._view.result_at(index)
            if result is not None:
                if index.column() == COL_STAR:
                    self._paint_star(painter, option.rect, index, result)
                else:
                    self.paint_cell(painter, option, index, result)
        painter.restore()

    def paint_cell(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        result: SearchResult,
    ) -> None:
        raise NotImplementedError

    def _paint_group(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        rect = option.rect
        band = QRect(
            rect.left(), rect.bottom() - _GROUP_BAND + 1, rect.width(), _GROUP_BAND
        )
        centre_y = band.center().y()
        expanded = self._view.isExpanded(_lead(index))
        chevron = "chevron_down" if expanded else "chevron_right"
        icons.icon(chevron, "text3", 14).paint(
            painter, QRect(rect.left() + _GROUP_CHEVRON_X, centre_y - 7, 14, 14)
        )

        kind, _, key = str(index.data(_ROLE_GROUP)).partition(":")
        label = tr(f"list.group_{key}") if kind == "date" else key
        right = band.right() - 16
        x = rect.left() + _GROUP_TEXT_X
        painter.setFont(_font(option.font, _tk.FONT_PX_BASE, QFont.Weight.DemiBold))
        x += _draw_text(
            painter, QRect(x, band.top(), right - x - 48, _GROUP_BAND), label, "text"
        )
        x += 8
        painter.setFont(_font(option.font, _tk.FONT_PX_SMALL))
        count = str(index.data(_ROLE_COUNT))
        x += _draw_text(painter, QRect(x, band.top(), 40, _GROUP_BAND), count, "text3")
        x += 12
        if x < right:
            painter.setPen(QPen(qcolor(_tk.color("border")), 1))
            painter.drawLine(x, centre_y, right, centre_y)

    def _paint_star(
        self,
        painter: QPainter,
        cell: QRect,
        index: QModelIndex | QPersistentModelIndex,
        result: SearchResult,
    ) -> None:
        glyph = _star_rect(cell, self._view.content_mode)
        if result.is_favorite:
            icons.icon("star_filled", "star", 16).paint(painter, glyph)
        elif self._view.is_hot(index):
            key = "text2" if self._view.star_hovered(result) else "text3"
            icons.icon("star", key, 16).paint(painter, glyph)


class _DetailsDelegate(_Delegate):
    """Details view: ``[star] Name | Tables | Tags | Modified`` on 34 px rows."""

    def paint_cell(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        result: SearchResult,
    ) -> None:
        rect = option.rect
        column = index.column()
        terms = self._view.terms
        if column == COL_NAME:
            name, has_title = display_name(result)
            icons.icon("file", "accent_text" if has_title else "text3", 16).paint(
                painter, QRect(rect.left() + _CELL_PAD, rect.center().y() - 8, 16, 16)
            )
            painter.setFont(_font(option.font, _tk.FONT_PX_BASE))
            text_rect = rect.adjusted(_NAME_TEXT_X, 0, -_CELL_PAD, 0)
            _draw_text(painter, text_rect, name, "text", terms)
        elif column == COL_TABLES:
            painter.setFont(_font(option.font, _tk.FONT_PX_BASE))
            text_rect = rect.adjusted(_CELL_PAD, 0, -_CELL_PAD, 0)
            if result.tables:
                _draw_text(painter, text_rect, ", ".join(result.tables), "text2", terms)
            else:
                _draw_text(painter, text_rect, "—", "text3")
        elif column == COL_TAGS:
            _paint_tags(
                painter,
                rect.adjusted(_CELL_PAD, 0, -_CELL_PAD, 0),
                result.tags,
                _font(option.font, _tk.FONT_PX_SMALL),
                _PILL_H,
            )
        elif column == COL_MODIFIED:
            painter.setFont(_tabular(_font(option.font, _tk.FONT_PX_BASE)))
            text_rect = rect.adjusted(_CELL_PAD, 0, -_CELL_PAD - _INSET, 0)
            label = modified_label(result.file_mtime, datetime.now())
            _draw_text(painter, text_rect, label, "text2")


class _ContentDelegate(_Delegate):
    """Content view: title and date, the description, then table chips and
    every tag, on 76 px rows. Only the star and Name columns are shown."""

    row_height = CONTENT_ROW_HEIGHT

    def paint_cell(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        result: SearchResult,
    ) -> None:
        if index.column() != COL_NAME:
            return
        rect = option.rect
        terms = self._view.terms
        left = rect.left() + _NAME_TEXT_X
        right = self._view.viewport().width() - _CONTENT_RIGHT
        top = rect.top() + _CONTENT_TOP

        name, has_title = display_name(result)
        icons.icon("file", "accent_text" if has_title else "text3", 16).paint(
            painter, QRect(rect.left() + _CELL_PAD, top + 1, 16, 16)
        )
        painter.setFont(_tabular(_font(option.font, _tk.FONT_PX_SMALL)))
        date = modified_label(result.file_mtime, datetime.now())
        date_w = painter.fontMetrics().horizontalAdvance(date)
        _draw_text(painter, QRect(right - date_w, top, date_w + 1, 18), date, "text3")
        painter.setFont(_font(option.font, _tk.FONT_PX_BASE, QFont.Weight.DemiBold))
        title_rect = QRect(left, top, right - date_w - 12 - left, 18)
        _draw_text(painter, title_rect, name, "text", terms)

        painter.setFont(_font(option.font, _tk.FONT_PX_SMALL))
        description = " ".join(result.description.split())
        line = QRect(left, top + 21, right - left, 18)
        if description:
            _draw_text(painter, line, description, "text2")
        else:
            _draw_text(painter, line, tr("list.no_description"), "text3")

        chips = QRect(left, top + 43, right - left, _CHIP_H)
        mono = mono_font(_tk.FONT_PX_KBD)
        x = _paint_table_chips(painter, chips, result.tables, mono, terms)
        if result.tags and x < chips.right():
            chips.setLeft(x)
            _paint_tags(
                painter,
                chips,
                result.tags,
                _font(option.font, _tk.FONT_PX_SMALL),
                _CHIP_H,
            )


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


class _EmptyState(QWidget):
    """Shown instead of the list when there is nothing to list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QueryListEmpty")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = QLabel()
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = QLabel()
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(0)
        layout.addStretch(2)
        layout.addWidget(self._icon)
        layout.addSpacing(12)
        layout.addWidget(self._title)
        layout.addSpacing(6)
        layout.addWidget(self._hint)
        layout.addStretch(3)
        self.retranslate_ui()
        self.refresh_theme()

    def retranslate_ui(self) -> None:
        self._title.setText(tr("list.empty_title"))
        self._hint.setText(tr("list.empty_hint"))

    def refresh_theme(self) -> None:
        self._icon.setPixmap(icons.pixmap("search", "text3", 32))
        self._title.setStyleSheet(
            f"color: {qss('text')}; font-size: 14px; font-weight: 600;"
        )
        self._hint.setStyleSheet(
            f"color: {qss('text3')}; font-size: {_tk.FONT_PX_SMALL}px;"
        )


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class QueryListWidget(QWidget):
    """Middle panel: the results of the current view or search, sorted,
    optionally grouped, in the Details or the Content view."""

    query_selected = Signal(object)  # SearchResult
    # (action, SearchResult); action is one of "copy", "edit", "favorite",
    # "ssms", "copy_path", "reveal", "duplicate"
    context_action = Signal(str, object)
    favorite_toggled = Signal(object)  # SearchResult, from a click on its star
    sort_requested = Signal(str)  # sort key, from a click on a column header

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: list[SearchResult] = []
        self._sort = _SORT_MTIME_DESC
        self._group = "date"
        self._view_mode = "details"
        self._collapsed: set[str] = set()  # group keys the user folded
        self._populating = False

        self._model = QStandardItemModel(0, _COLUMNS, self)
        self._view = _QueryTree()
        self._header = _ListHeader(self._view)
        self._view.setHeader(self._header)
        self._view.setModel(self._model)
        self._details_delegate = _DetailsDelegate(self._view)
        self._content_delegate = _ContentDelegate(self._view)
        self._view.setItemDelegate(self._details_delegate)

        # The tree resets this while taking the header and the model.
        self._header.setSectionsClickable(True)
        self._header.setStretchLastSection(False)
        self._header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column, width in (
            (COL_STAR, _STAR_W),
            (COL_TABLES, _TABLES_W),
            (COL_TAGS, _TAGS_W),
            (COL_MODIFIED, _MODIFIED_W),
        ):
            self._header.resizeSection(column, width)
        self._header.sectionClicked.connect(self._on_header_clicked)

        selection = self._view.selectionModel()
        selection.currentChanged.connect(self._on_current_changed)
        self._view.activated.connect(self._on_activated)
        self._view.expanded.connect(self._on_expanded)
        self._view.collapsed.connect(self._on_collapsed)
        self._view.star_clicked.connect(self.favorite_toggled)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_context_menu)

        self._empty = _EmptyState()
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._empty)

        self.retranslate_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_results(self, results: list[SearchResult], *, highlight: str = "") -> None:
        """Show *results*, keeping the selected query selected if it is still
        listed. *highlight* is the search text whose terms get marked."""
        current_key = _result_key(self._current_result())
        self._results = list(results)
        self._view.terms = highlight_terms(highlight) if highlight else []
        self._sort_results()
        self._populate(current_key)

    def select_by_rel_path(self, rel_path: str, folder: Path | None = None) -> None:
        for index, result in self._iter_queries():
            if result.rel_path == rel_path and (
                folder is None or result.folder == folder
            ):
                parent = index.parent()
                if parent.isValid() and not self._view.isExpanded(parent):
                    self._view.setExpanded(parent, True)
                self._view.setCurrentIndex(index)
                self._view.scrollTo(index)
                return

    def update_favorite(
        self, rel_path: str, is_fav: bool, folder: Path | None = None
    ) -> None:
        """Update the star of the query at *rel_path* in place."""
        for result in self._results:
            if result.rel_path == rel_path and (
                folder is None or result.folder == folder
            ):
                result.is_favorite = is_fav
        self._view.viewport().update()

    def count(self) -> int:
        return len(self._results)

    def sort_key(self) -> str:
        return self._sort

    def set_sort(self, key: str) -> None:
        """Reorder the list by *key*, keeping the selected query selected."""
        if key not in SORT_KEYS or key == self._sort:
            return
        self._sort = key
        self._header.sort_key = key
        self._header.viewport().update()
        self._sort_results()
        self._populate(_result_key(self._current_result()))

    def group_mode(self) -> str:
        return self._group

    def set_group(self, mode: str) -> None:
        """Group by "date", "folder" or "none", keeping the selection."""
        if mode not in GROUP_MODES or mode == self._group:
            return
        self._group = mode
        self._populate(_result_key(self._current_result()))

    def view_mode(self) -> str:
        return self._view_mode

    def set_view(self, mode: str) -> None:
        """Switch between the "details" and the "content" view."""
        if mode not in VIEW_MODES or mode == self._view_mode:
            return
        self._view_mode = mode
        content = mode == "content"
        self._view.content_mode = content
        self._view.setItemDelegate(
            self._content_delegate if content else self._details_delegate
        )
        self._view.setHeaderHidden(content)
        self._apply_columns()
        current = self._view.currentIndex()
        if current.isValid():
            self._view.scrollTo(current)

    def refresh_theme(self) -> None:
        self._empty.refresh_theme()
        self._header.viewport().update()
        self._view.viewport().update()

    def retranslate_ui(self) -> None:
        for column, key in enumerate(_COLUMN_LABELS):
            self._model.setHeaderData(
                column, Qt.Orientation.Horizontal, tr(key) if key else ""
            )
        self._empty.retranslate_ui()
        self._view.viewport().update()

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------

    def _sort_results(self) -> None:
        if self._sort == _SORT_NAME_ASC:
            self._results.sort(key=lambda r: r.title.lower())
        elif self._sort == _SORT_NAME_DESC:
            self._results.sort(key=lambda r: r.title.lower(), reverse=True)
        elif self._sort == _SORT_MTIME_DESC:
            self._results.sort(key=lambda r: r.file_mtime, reverse=True)
        elif self._sort == _SORT_MTIME_ASC:
            self._results.sort(key=lambda r: r.file_mtime)

    def _groups(self) -> list[tuple[str, list[SearchResult]]] | None:
        """The groups to show, keyed "date:<bucket>" or "folder:<label>";
        None when the list is not grouped."""
        if self._group == "date":
            first_weekday = QLocale().firstDayOfWeek().value
            groups = group_by_date(
                self._results,
                datetime.now(),
                first_weekday,
                oldest_first=self._sort == _SORT_MTIME_ASC,
            )
            return [(f"date:{key}", items) for key, items in groups]
        if self._group == "folder":
            return [
                (f"folder:{key}", items)
                for key, items in group_by_folder(self._results)
            ]
        return None

    @staticmethod
    def _query_item(result: SearchResult) -> QStandardItem:
        item = QStandardItem(display_name(result)[0])  # text for type-ahead
        item.setData(result, _ROLE_RESULT)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    def _populate(self, restore_key: tuple[object, str] | None) -> None:
        """Rebuild the model from ``self._results`` and select the query keyed
        *restore_key*, or the first one; announce the selection only if it
        changed, so an unchanged query is not loaded again."""
        self._populating = True
        try:
            self._model.setRowCount(0)
            root = self._model.invisibleRootItem()
            groups = self._groups()
            target: tuple[int, int] | None = None  # (group row or -1, row)
            if groups is None:
                items = []
                for row, result in enumerate(self._results):
                    items.append(self._query_item(result))
                    if target is None and _result_key(result) == restore_key:
                        target = (-1, row)
                root.appendRows(items)
            else:
                group_items = []
                for group_row, (key, members) in enumerate(groups):
                    group = QStandardItem()
                    group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    group.setData(key, _ROLE_GROUP)
                    group.setData(len(members), _ROLE_COUNT)
                    children = []
                    for row, result in enumerate(members):
                        children.append(self._query_item(result))
                        if target is None and _result_key(result) == restore_key:
                            target = (group_row, row)
                    group.appendRows(children)
                    group.setColumnCount(_COLUMNS)
                    group_items.append(group)
                root.appendRows(group_items)
                for group_row, (key, _members) in enumerate(groups):
                    self._view.setFirstColumnSpanned(group_row, QModelIndex(), True)
                    if key not in self._collapsed:
                        self._view.setExpanded(self._model.index(group_row, 0), True)

            index = self._index_at(target) if target is not None else QModelIndex()
            if not index.isValid():
                index = self._first_visible_query()
            if index.isValid():
                self._view.setCurrentIndex(index)
            else:
                self._view.setCurrentIndex(QModelIndex())
                self._view.clearSelection()
        finally:
            self._populating = False

        self._stack.setCurrentWidget(self._empty if not self._results else self._view)
        current = self._view.currentIndex()
        if current.isValid():
            self._view.scrollTo(current)
        selected = self._current_result()
        if selected is not None and _result_key(selected) != restore_key:
            self.query_selected.emit(selected)

    def _index_at(self, target: tuple[int, int]) -> QModelIndex:
        group_row, row = target
        if group_row < 0:
            return self._model.index(row, 0)
        return self._model.index(row, 0, self._model.index(group_row, 0))

    def _first_visible_query(self) -> QModelIndex:
        index = self._model.index(0, 0)
        while index.isValid() and self._view.is_group(index):
            index = self._view.indexBelow(index)
        return index

    def _iter_queries(self) -> Iterator[tuple[QModelIndex, SearchResult]]:
        model = self._model
        for row in range(model.rowCount()):
            top = model.index(row, 0)
            if top.data(_ROLE_GROUP) is None:
                parents = [top]
            else:
                parents = [model.index(r, 0, top) for r in range(model.rowCount(top))]
            for index in parents:
                result = index.data(_ROLE_RESULT)
                if isinstance(result, SearchResult):
                    yield index, result

    def _current_result(self) -> SearchResult | None:
        return self._view.result_at(self._view.currentIndex())

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_columns()

    def _apply_columns(self) -> None:
        content = self._view_mode == "content"
        width = self.width()
        self._view.setColumnHidden(COL_TABLES, content or width < _HIDE_TABLES_BELOW)
        self._view.setColumnHidden(COL_TAGS, content or width < _HIDE_TAGS_BELOW)
        self._view.setColumnHidden(COL_MODIFIED, content)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._populating:
            return
        result = self._view.result_at(current)
        if result is not None:
            self.query_selected.emit(result)

    def _on_activated(self, index: QModelIndex) -> None:
        result = self._view.result_at(index)
        if result is not None:
            self.query_selected.emit(result)

    def _on_expanded(self, index: QModelIndex) -> None:
        key = index.data(_ROLE_GROUP)
        if key is not None:
            self._collapsed.discard(key)

    def _on_collapsed(self, index: QModelIndex) -> None:
        key = index.data(_ROLE_GROUP)
        if key is not None:
            self._collapsed.add(key)

    def _on_header_clicked(self, column: int) -> None:
        if column == COL_NAME:
            key = _SORT_NAME_DESC if self._sort == _SORT_NAME_ASC else _SORT_NAME_ASC
        elif column == COL_MODIFIED:
            key = (
                _SORT_MTIME_ASC if self._sort == _SORT_MTIME_DESC else _SORT_MTIME_DESC
            )
        else:
            return
        self.set_sort(key)
        self.sort_requested.emit(key)

    def _show_context_menu(self, pos: QPoint) -> None:
        result = self._view.result_at(self._view.indexAt(pos))
        if result is None:
            return
        favorite = "remove_favorite" if result.is_favorite else "add_favorite"
        entries = (
            ("copy", "copy_sql"),
            ("edit", "edit_metadata"),
            ("favorite", favorite),
            None,
            ("ssms", "open_in_ssms"),
            ("copy_path", "copy_path"),
            ("reveal", "reveal"),
            None,
            ("duplicate", "duplicate"),
        )
        menu = QMenu(self._view)
        for entry in entries:
            if entry is None:
                menu.addSeparator()
                continue
            action, label = entry
            menu.addAction(tr(f"context_menu.{label}")).setData(action)
        chosen = menu.exec(self._view.viewport().mapToGlobal(pos))
        if chosen is not None and chosen.data():
            self.context_action.emit(str(chosen.data()), result)
