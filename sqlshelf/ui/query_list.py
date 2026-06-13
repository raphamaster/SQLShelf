from __future__ import annotations

import re
from datetime import date, datetime

from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.models import SearchResult
from .theme.tokens import (
    ACCENT,
    BORDER,
    CARD,
    STAR_ACTIVE,
    SURFACE,
    TAG_BG,
    TAG_RADIUS,
    TAG_TEXT,
    TEXT_PRIMARY,
    TEXT_TERTIARY,
)

# ── Item data roles ─────────────────────────────────────────────────────────
_ROLE_RESULT   = Qt.ItemDataRole.UserRole        # SearchResult
_ROLE_TAGS     = Qt.ItemDataRole.UserRole + 1    # list[str]
_ROLE_TABLES   = Qt.ItemDataRole.UserRole + 2    # list[str]
_ROLE_UPDATED  = Qt.ItemDataRole.UserRole + 3    # str | None (ISO date)
_ROLE_FAVORITE = Qt.ItemDataRole.UserRole + 4    # bool

# ── Layout constants ────────────────────────────────────────────────────────
_ITEM_H    = 60
_PAD_H     = 12
_PAD_V     = 10
_LINE_GAP  = 4
_CHIP_H    = 18
_CHIP_PAD  = 6    # horizontal padding inside chip
_CHIP_GAP  = 5    # gap between chips
_ACCENT_W  = 2    # selected accent bar width
_MAX_CHIPS = 2
_STAR_W    = 18

# ── Color parsing ───────────────────────────────────────────────────────────
_RGBA_RE = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)")


def _qc(css: str) -> QColor:
    m = _RGBA_RE.match(css.strip())
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = round(float(m.group(4)) * 255)
        return QColor(r, g, b, a)
    return QColor(css)


def _relative_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso).date()
        delta = (date.today() - d).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "yesterday"
        if delta < 7:
            return f"{delta}d ago"
        if delta < 30:
            return f"{delta // 7}w ago"
        if delta < 365:
            return f"{delta // 30}mo ago"
        return f"{delta // 365}y ago"
    except Exception:
        return ""


# ── Delegate ────────────────────────────────────────────────────────────────

class QueryItemDelegate(QStyledItemDelegate):
    """Two-line item: title (line 1) + tag chips + meta label (line 2)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._f_title = QFont()
        self._f_title.setPointSize(12)
        self._f_title.setWeight(QFont.Weight.Medium)

        self._f_small = QFont()
        self._f_small.setPointSize(9)

        self._c_sel_bg   = _qc(CARD)
        self._c_hov_bg   = QColor(14, 20, 32)   # midpoint between SURFACE and CARD
        self._c_accent   = _qc(ACCENT)
        self._c_primary  = _qc(TEXT_PRIMARY)
        self._c_tertiary = _qc(TEXT_TERTIARY)
        self._c_tag_bg   = _qc(TAG_BG)
        self._c_tag_txt  = _qc(TAG_TEXT)
        self._c_star     = _qc(STAR_ACTIVE)

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        return QSize(option.rect.width(), _ITEM_H)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = bool(option.state & QStyle.StateFlag.State_MouseOver)

        title: str  = index.data(Qt.ItemDataRole.DisplayRole) or ""
        tags: list  = index.data(_ROLE_TAGS) or []
        tables: list = index.data(_ROLE_TABLES) or []
        updated: str = index.data(_ROLE_UPDATED) or ""
        fav: bool   = bool(index.data(_ROLE_FAVORITE))

        # ── Background ──────────────────────────────────────────────────────
        if sel:
            painter.fillRect(rect, self._c_sel_bg)
        elif hov:
            painter.fillRect(rect, self._c_hov_bg)

        # ── Accent bar (selected only) ──────────────────────────────────────
        if sel:
            painter.fillRect(
                rect.left(), rect.top(), _ACCENT_W, rect.height(), self._c_accent
            )

        # ── Content origin ──────────────────────────────────────────────────
        x_off = _ACCENT_W if sel else 0
        x  = rect.left() + _PAD_H + x_off
        y1 = rect.top()  + _PAD_V
        y2 = y1 + 18 + _LINE_GAP
        cw = rect.width() - _PAD_H * 2 - x_off

        # ── Favourite star (right of title row) ─────────────────────────────
        star_taken = 0
        if fav:
            star_taken = _STAR_W + 4
            painter.setFont(self._f_title)
            painter.setPen(self._c_star)
            painter.drawText(
                QRectF(rect.right() - _PAD_H - _STAR_W, y1, _STAR_W, 18),
                Qt.AlignmentFlag.AlignCenter,
                "★",
            )

        # ── Title ───────────────────────────────────────────────────────────
        title_w = cw - star_taken
        fm = QFontMetrics(self._f_title)
        elided = fm.elidedText(title, Qt.TextElideMode.ElideRight, title_w)
        painter.setFont(self._f_title)
        painter.setPen(self._c_primary)
        painter.drawText(
            QRectF(x, y1, title_w, 18),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )

        # ── Tag chips ───────────────────────────────────────────────────────
        fm_s = QFontMetrics(self._f_small)
        chip_x = float(x)
        for tag in tags[:_MAX_CHIPS]:
            tw = fm_s.horizontalAdvance(tag) + _CHIP_PAD * 2
            cr = QRectF(chip_x, y2, tw, _CHIP_H)
            path = QPainterPath()
            path.addRoundedRect(cr, TAG_RADIUS, TAG_RADIUS)
            painter.fillPath(path, self._c_tag_bg)
            painter.setFont(self._f_small)
            painter.setPen(self._c_tag_txt)
            painter.drawText(cr, Qt.AlignmentFlag.AlignCenter, tag)
            chip_x += tw + _CHIP_GAP

        # ── Meta label: first table or relative date ─────────────────────────
        meta = tables[0] if tables else _relative_date(updated)
        if meta:
            painter.setFont(self._f_small)
            painter.setPen(self._c_tertiary)
            painter.drawText(
                QRectF(x, y2, cw, _CHIP_H),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                meta,
            )

        painter.restore()


# ── Widget ──────────────────────────────────────────────────────────────────

class QueryListWidget(QWidget):
    """Middle panel: displays search results with a custom two-line delegate."""

    query_selected = Signal(object)       # SearchResult
    context_action = Signal(str, object)  # (action_name, SearchResult)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: list[SearchResult] = []

        self._model = QStandardItemModel(self)

        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setItemDelegate(QueryItemDelegate(self._list))
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setUniformItemSizes(True)
        self._list.setMouseTracking(True)
        self._list.viewport().setMouseTracking(True)
        self._list.setStyleSheet(
            f"QListView {{ background-color: {SURFACE}; border: 1px solid {BORDER};"
            f"  outline: none; }}"
            f"QListView::item {{ border: none; }}"
        )

        self._list.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._list.activated.connect(self._on_activated)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_results(self, results: list[SearchResult]) -> None:
        current_path = self._current_rel_path()
        self._results = results

        sel = self._list.selectionModel()
        sel.blockSignals(True)
        self._model.clear()

        restore_row = -1
        for i, r in enumerate(results):
            item = QStandardItem(r.title)
            tooltip = r.rel_path
            if r.tags:
                tooltip += "\n" + "  ".join(f"#{t}" for t in r.tags)
            item.setToolTip(tooltip)
            item.setEditable(False)
            item.setData(r,            _ROLE_RESULT)
            item.setData(r.tags,       _ROLE_TAGS)
            item.setData(r.tables,     _ROLE_TABLES)
            item.setData(r.updated_at, _ROLE_UPDATED)
            item.setData(r.is_favorite, _ROLE_FAVORITE)
            self._model.appendRow(item)
            if r.rel_path == current_path:
                restore_row = i

        sel.blockSignals(False)

        if restore_row >= 0:
            self._list.setCurrentIndex(self._model.index(restore_row, 0))
        elif results:
            self._list.setCurrentIndex(self._model.index(0, 0))

    def select_by_rel_path(self, rel_path: str) -> None:
        for i, r in enumerate(self._results):
            if r.rel_path == rel_path:
                self._list.setCurrentIndex(self._model.index(i, 0))
                return

    def count(self) -> int:
        return len(self._results)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _current_rel_path(self) -> str | None:
        row = self._list.currentIndex().row()
        if 0 <= row < len(self._results):
            return self._results[row].rel_path
        return None

    def _on_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        row = current.row()
        if 0 <= row < len(self._results):
            self.query_selected.emit(self._results[row])

    def _on_activated(self, index: QModelIndex) -> None:
        row = index.row()
        if 0 <= row < len(self._results):
            self.query_selected.emit(self._results[row])

    def _show_context_menu(self, pos) -> None:
        idx = self._list.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        if not (0 <= row < len(self._results)):
            return
        result = self._results[row]

        menu = QMenu(self._list)
        fav_act    = menu.addAction("☆  Toggle Favorite")
        dup_act    = menu.addAction("⎘  Duplicate Query…")
        copy_act   = menu.addAction("📋  Copy SQL")
        menu.addSeparator()
        reveal_act = menu.addAction("📂  Reveal in Explorer")

        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is fav_act:
            self.context_action.emit("favorite", result)
        elif chosen is dup_act:
            self.context_action.emit("duplicate", result)
        elif chosen is copy_act:
            self.context_action.emit("copy", result)
        elif chosen is reveal_act:
            self.context_action.emit("reveal", result)
