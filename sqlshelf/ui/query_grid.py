"""The Grid view of the query list: a card with a thumbnail for each query.

A QAbstractScrollArea that paints its own cards. Groups (by date or by folder)
are full-width headers that fold their cards, as in the other views. The grid
holds no model: the list widget hands it the sections to show and listens to
what the user does. Colours are read at paint time, so a theme switch only
needs a repaint.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QContextMenuEvent,
    QFont,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QToolTip, QWidget

from ..core.grouping import display_name, group_label, modified_label
from ..core.i18n import tr
from ..core.models import SearchResult
from ..core.text_match import match_spans
from . import icons
from . import tokens as _tk
from .paint import GROUP_HEIGHT, draw_group_header, draw_marked_text, sized_font
from .theme import qcolor
from .thumbnails import THUMB_HEIGHT, ThumbnailCache

# ── Geometry, in logical px ─────────────────────────────────────────────────
MARGIN = 12  # from each side of the view
GAP = 12  # between cards
CARD_MIN_WIDTH = 176
CARD_MAX_WIDTH = 260
_PAD = 8  # inside a card
_TITLE_HEIGHT = 18
_META_HEIGHT = 16
CARD_HEIGHT = _PAD + THUMB_HEIGHT + 8 + _TITLE_HEIGHT + 3 + _META_HEIGHT + 10
_TOP = 8  # above the first card of an ungrouped list
_STAR_SIZE = 24
_STAR_INSET = 6
_TAG_DOT = 6
_TAG_DOT_GAP = 4
_MAX_TAG_DOTS = 5
_NAV_KEYS = (
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
)


@dataclass
class GridSection:
    """A group of results under a header; *key* is None for an ungrouped list,
    which has no header."""

    key: str | None
    results: list[SearchResult]


@dataclass
class _Row:
    top: int  # in content coordinates
    height: int
    section: int
    header: bool
    first: int = 0  # index of its first card; a header row has none
    count: int = 0


def _key(result: SearchResult) -> tuple[object, str]:
    return result.folder, result.rel_path


def _thumb_rect(card: QRect) -> QRect:
    return QRect(
        card.left() + _PAD, card.top() + _PAD, card.width() - 2 * _PAD, THUMB_HEIGHT
    )


def _star_rect(thumb: QRect) -> QRect:
    return QRect(
        thumb.right() + 1 - _STAR_INSET - _STAR_SIZE,
        thumb.top() + _STAR_INSET,
        _STAR_SIZE,
        _STAR_SIZE,
    )


class QueryGrid(QAbstractScrollArea):
    """Cards with a thumbnail, in sections, with the keyboard moving over the
    cards the way arrow keys move over a grid."""

    current_changed = Signal(object)  # SearchResult, from the user (or reveal)
    activated = Signal(object)  # SearchResult, from Enter or a double click
    star_clicked = Signal(object)  # SearchResult
    context_requested = Signal(object, QPoint)  # (SearchResult, global position)
    group_toggled = Signal(str, bool)  # (group key, folded)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QueryGrid")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.terms: list[str] = []  # highlighted in the titles

        self._thumbnails = ThumbnailCache(self)
        self._thumbnails.ready.connect(self.viewport().update)

        self._sections: list[GridSection] = []
        self._collapsed: set[str] = set()
        self._by_key: dict[tuple[object, str], tuple[SearchResult, int]] = {}
        # Layout, rebuilt by _relayout():
        self._rows: list[_Row] = []
        self._tops: list[int] = []
        self._cards: list[SearchResult] = []  # the ones on show, in order
        self._card_row: list[int] = []  # index in _rows of each card's row
        self._index_of: dict[tuple[object, str], int] = {}
        self._cols = 1
        self._card_width = CARD_MIN_WIDTH
        self._content_height = 0

        self._current: SearchResult | None = None  # may be in a folded section
        self._current_index = -1  # in _cards; -1 if not on show
        self._hover = -1
        self._hover_star = False
        self._hand = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_sections(
        self,
        sections: list[GridSection],
        collapsed: set[str],
        current: tuple[object, str] | None,
    ) -> None:
        """Show *sections*, with the groups of *collapsed* folded, and make the
        result keyed *current* the current one, or the first card if there is
        none. Nothing is announced."""
        self._sections = sections
        self._collapsed = set(collapsed)
        self._by_key = {
            _key(result): (result, s)
            for s, section in enumerate(sections)
            for result in section.results
        }
        found = self._by_key.get(current) if current is not None else None
        self._current = found[0] if found is not None else None
        self._hover = -1
        self._relayout()
        if self._current is None and self._cards:
            self._current = self._cards[0]
            self._current_index = 0
        self._ensure_visible(self._current_index)
        self.viewport().update()

    def set_collapsed(self, collapsed: set[str]) -> None:
        """Fold exactly the groups of *collapsed*."""
        if collapsed == self._collapsed:
            return
        self._collapsed = set(collapsed)
        self._relayout()
        self.viewport().update()

    def current_result(self) -> SearchResult | None:
        return self._current

    def reveal(self, result: SearchResult) -> None:
        """Make *result* current, unfolding its group and scrolling to it."""
        found = self._by_key.get(_key(result))
        if found is None:
            return
        _result, section = found
        key = self._sections[section].key
        if key is not None and key in self._collapsed:
            self._toggle(section)
        self._select(self._index_of.get(_key(result), -1))

    def refresh_theme(self) -> None:
        self._thumbnails.refresh_theme()
        self.viewport().update()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _relayout(self) -> None:
        available = max(0, self.viewport().width() - 2 * MARGIN)
        cols = max(1, (available + GAP) // (CARD_MIN_WIDTH + GAP))
        self._cols = cols
        self._card_width = max(
            1, min(CARD_MAX_WIDTH, (available - GAP * (cols - 1)) // cols)
        )

        self._rows = []
        self._cards = []
        self._card_row = []
        self._index_of = {}
        grouped = bool(self._sections) and self._sections[0].key is not None
        y = 0 if grouped else _TOP
        for s, section in enumerate(self._sections):
            if section.key is not None:
                self._rows.append(_Row(y, GROUP_HEIGHT, s, header=True))
                y += GROUP_HEIGHT
                if section.key in self._collapsed:
                    continue
            for start in range(0, len(section.results), cols):
                chunk = section.results[start : start + cols]
                self._rows.append(
                    _Row(y, CARD_HEIGHT, s, False, len(self._cards), len(chunk))
                )
                self._card_row.extend([len(self._rows) - 1] * len(chunk))
                for result in chunk:
                    self._index_of[_key(result)] = len(self._cards)
                    self._cards.append(result)
                y += CARD_HEIGHT + GAP
        self._tops = [row.top for row in self._rows]
        self._content_height = y + (MARGIN - GAP if self._rows else 0)
        self._current_index = (
            self._index_of.get(_key(self._current), -1) if self._current else -1
        )
        if self._hover >= len(self._cards):
            self._hover = -1

        bar = self.verticalScrollBar()
        page = self.viewport().height()
        bar.setPageStep(page)
        bar.setSingleStep(CARD_HEIGHT // 3)
        bar.setRange(0, max(0, self._content_height - page))

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self._relayout()
        elif event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            text = self._tooltip(event.pos())
            if text:
                QToolTip.showText(event.globalPos(), text, self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def _card_rect(self, index: int) -> QRect:
        """Where card *index* is in the viewport."""
        row = self._rows[self._card_row[index]]
        col = index - row.first
        return QRect(
            MARGIN + col * (self._card_width + GAP),
            row.top - self.verticalScrollBar().value(),
            self._card_width,
            CARD_HEIGHT,
        )

    def _row_at(self, pos: QPoint) -> int:
        y = pos.y() + self.verticalScrollBar().value()
        index = bisect_right(self._tops, y) - 1
        if index < 0:
            return -1
        row = self._rows[index]
        return index if y < row.top + row.height else -1

    def _card_at(self, pos: QPoint) -> int:
        """The card under *pos*, or -1."""
        row_index = self._row_at(pos)
        if row_index < 0 or self._rows[row_index].header:
            return -1
        row = self._rows[row_index]
        step = self._card_width + GAP
        col, rest = divmod(pos.x() - MARGIN, step)
        if pos.x() < MARGIN or rest >= self._card_width or col >= row.count:
            return -1
        return row.first + col

    def _header_at(self, pos: QPoint) -> int:
        """The section whose header is under *pos*, or -1."""
        row_index = self._row_at(pos)
        if row_index < 0 or not self._rows[row_index].header:
            return -1
        return self._rows[row_index].section

    def _star_hit(self, index: int, pos: QPoint) -> bool:
        if index < 0:
            return False
        target = _star_rect(_thumb_rect(self._card_rect(index)))
        return target.adjusted(-4, -4, 4, 4).contains(pos)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _select(self, index: int) -> None:
        """Make card *index* current and show it; announce a change."""
        if not 0 <= index < len(self._cards):
            return
        changed = index != self._current_index
        self._current_index = index
        self._current = self._cards[index]
        self._ensure_visible(index)
        self.viewport().update()
        if changed:
            self.current_changed.emit(self._current)

    def _ensure_visible(self, index: int) -> None:
        if not 0 <= index < len(self._cards):
            return
        bar = self.verticalScrollBar()
        card = self._card_rect(index)
        if card.top() < 0:
            bar.setValue(bar.value() + card.top() - _PAD)
        elif card.bottom() >= self.viewport().height():
            bar.setValue(bar.value() + card.bottom() - self.viewport().height() + _PAD)

    def _toggle(self, section: int) -> None:
        key = self._sections[section].key
        if key is None:
            return
        if key in self._collapsed:
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)
        self._relayout()
        self.viewport().update()
        self.group_toggled.emit(key, key in self._collapsed)

    def _vertical(self, index: int, rows: int) -> int:
        """The card *rows* rows below (or above, if negative) card *index*, in
        the same column or the last one of a short row; headers are stepped
        over. Stays put at the first and last row."""
        step = 1 if rows > 0 else -1
        row_index = self._card_row[index]
        col = index - self._rows[row_index].first
        for _ in range(abs(rows)):
            probe = row_index + step
            while 0 <= probe < len(self._rows) and self._rows[probe].header:
                probe += step
            if not 0 <= probe < len(self._rows):
                break
            row_index = probe
        row = self._rows[row_index]
        return row.first + min(col, row.count - 1)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if not self._cards:
            super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._current is not None and self._current_index >= 0:
                self.activated.emit(self._current)
            return
        if self._current_index < 0:
            if key in _NAV_KEYS:
                self._select(0)
                return
            super().keyPressEvent(event)
            return
        index = self._current_index
        page = max(1, self.viewport().height() // (CARD_HEIGHT + GAP))
        target = {
            Qt.Key.Key_Left: index - 1,
            Qt.Key.Key_Right: index + 1,
            Qt.Key.Key_Up: self._vertical(index, -1),
            Qt.Key.Key_Down: self._vertical(index, 1),
            Qt.Key.Key_PageUp: self._vertical(index, -page),
            Qt.Key.Key_PageDown: self._vertical(index, page),
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: len(self._cards) - 1,
        }.get(
            key
        )  # type: ignore[call-overload]
        if target is None:
            super().keyPressEvent(event)
            return
        self._select(max(0, min(len(self._cards) - 1, target)))

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        section = self._header_at(pos)
        if section >= 0:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle(section)
            event.accept()
            return
        index = self._card_at(pos)
        if index >= 0:
            if event.button() == Qt.MouseButton.LeftButton and self._star_hit(
                index, pos
            ):
                self.star_clicked.emit(self._cards[index])
            else:
                self._select(index)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # The press of a double click already toggled the group or the star.
        pos = event.position().toPoint()
        index = self._card_at(pos)
        if index >= 0 and not self._star_hit(index, pos):
            self.activated.emit(self._cards[index])
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        hover = self._card_at(pos)
        hover_star = self._star_hit(hover, pos)
        if hover != self._hover or hover_star != self._hover_star:
            self._hover = hover
            self._hover_star = hover_star
            self.viewport().update()
        hand = hover_star or self._header_at(pos) >= 0
        if hand != self._hand:
            self._hand = hand
            self.viewport().setCursor(
                Qt.CursorShape.PointingHandCursor
                if hand
                else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self._hover_star = False
            self.viewport().update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            if self._current_index < 0:
                return
            index = self._current_index
            where = self.viewport().mapToGlobal(self._card_rect(index).center())
        else:
            index = self._card_at(self.viewport().mapFrom(self, event.pos()))
            where = event.globalPos()
        if index < 0:
            return
        self._select(index)
        self.context_requested.emit(self._cards[index], where)

    def _tooltip(self, pos: QPoint) -> str:
        index = self._card_at(pos)
        if index < 0:
            return ""
        result = self._cards[index]
        if self._star_hit(index, pos):
            key = "remove_favorite" if result.is_favorite else "add_favorite"
            return tr(f"context_menu.{key}")
        name, has_title = display_name(result)
        lines = [name, result.rel_path] if has_title else [result.rel_path]
        if result.tags:
            lines.append("  ".join(f"#{tag}" for tag in result.tags))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), qcolor(_tk.color("bg")))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        offset = self.verticalScrollBar().value()
        top = offset + event.rect().top()
        bottom = offset + event.rect().bottom()
        first = max(0, bisect_right(self._tops, top) - 1)
        for row in self._rows[first:]:
            if row.top > bottom:
                break
            if row.top + row.height < top:
                continue
            painter.save()
            if row.header:
                self._paint_header(painter, row, offset)
            else:
                for index in range(row.first, row.first + row.count):
                    self._paint_card(painter, index)
            painter.restore()
        painter.end()

    def _paint_header(self, painter: QPainter, row: _Row, offset: int) -> None:
        section = self._sections[row.section]
        key = section.key or ""
        draw_group_header(
            painter,
            QRect(0, row.top - offset, self.viewport().width(), GROUP_HEIGHT),
            self.font(),
            group_label(key),
            len(section.results),
            expanded=key not in self._collapsed,
        )

    def _paint_card(self, painter: QPainter, index: int) -> None:
        result = self._cards[index]
        card = self._card_rect(index)
        selected = index == self._current_index
        hovered = index == self._hover
        hot = selected or hovered

        painter.setBrush(
            QBrush(qcolor(_tk.color("sel" if selected else "hover")))
            if hot
            else Qt.BrushStyle.NoBrush
        )
        outline = "accent" if selected else "border_strong" if hovered else "border"
        painter.setPen(QPen(qcolor(_tk.color(outline)), 1))
        painter.drawRoundedRect(
            QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5), _tk.RADIUS, _tk.RADIUS
        )

        thumb = _thumb_rect(card)
        self._paint_thumbnail(painter, result, thumb)
        if result.is_favorite or hot:
            self._paint_star(painter, result, thumb, hover=self._hover_star and hovered)

        left = card.left() + _PAD
        width = card.width() - 2 * _PAD
        name, _has_title = display_name(result)
        painter.setFont(
            sized_font(self.font(), _tk.FONT_PX_BASE, QFont.Weight.DemiBold)
        )
        title = QRect(left, thumb.bottom() + 9, width, _TITLE_HEIGHT)
        spans = match_spans(name, self.terms) if self.terms else None
        draw_marked_text(painter, title, name, "text", spans)

        meta = QRect(left, title.bottom() + 4, width, _META_HEIGHT)
        dots = result.tags[:_MAX_TAG_DOTS]
        dots_width = len(dots) * (_TAG_DOT + _TAG_DOT_GAP) - _TAG_DOT_GAP
        painter.setFont(sized_font(self.font(), _tk.FONT_PX_SMALL))
        label = modified_label(result.file_mtime, datetime.now())
        label_rect = QRect(meta)
        label_rect.setWidth(width - (dots_width + 8 if dots else 0))
        draw_marked_text(painter, label_rect, label, "text3")
        painter.setPen(Qt.PenStyle.NoPen)
        x = meta.right() + 1 - dots_width
        for tag in dots:
            painter.setBrush(QBrush(qcolor(_tk.tag_color(tag))))
            painter.drawEllipse(
                QRectF(x, meta.center().y() - _TAG_DOT / 2 + 0.5, _TAG_DOT, _TAG_DOT)
            )
            x += _TAG_DOT + _TAG_DOT_GAP

    def _paint_thumbnail(
        self, painter: QPainter, result: SearchResult, thumb: QRect
    ) -> None:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(thumb), _tk.RADIUS_SM, _tk.RADIUS_SM)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(thumb, qcolor(_tk.color("code")))
        if result.folder is not None:
            pixmap = self._thumbnails.thumbnail(
                Path(result.folder) / result.rel_path,
                result.file_mtime,
                self.viewport().devicePixelRatioF(),
                result.dialect,
            )
            if pixmap is not None:
                # The page is wider than any card: the clip crops it.
                painter.drawPixmap(thumb.topLeft(), pixmap)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(qcolor(_tk.color("border")), 1))
        painter.drawRoundedRect(
            QRectF(thumb).adjusted(0.5, 0.5, -0.5, -0.5), _tk.RADIUS_SM, _tk.RADIUS_SM
        )

    @staticmethod
    def _paint_star(
        painter: QPainter, result: SearchResult, thumb: QRect, *, hover: bool
    ) -> None:
        box = _star_rect(thumb)
        backing = qcolor(_tk.color("bg"))
        backing.setAlpha(225)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(backing))
        painter.drawEllipse(QRectF(box))
        glyph = QRect(0, 0, 16, 16)
        glyph.moveCenter(box.center())
        if result.is_favorite:
            icons.icon("star_filled", "star", 16).paint(painter, glyph)
        else:
            colour = "text2" if hover else "text3"
            icons.icon("star", colour, 16).paint(painter, glyph)
