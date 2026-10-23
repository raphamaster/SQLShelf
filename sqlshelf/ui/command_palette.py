"""Quick search (Ctrl+P): open a query or run an action from the keyboard.

A frameless card over the darkened window. It lists the library's queries,
filtered as you type (``core.quick_search``), then the actions New query,
Open folder and Reindex library, filtered by the same words. Up and Down move
the selection, Enter or a click opens it, Ctrl+C copies the selected query's
SQL, Tab moves the last word through ``table:`` and ``tag:``, and Esc or a
click outside the card closes it.

The palette is a child of the window rather than a dialog: it is the
darkening itself, so nothing behind it can be clicked. A window shortcut that
moves the focus elsewhere (Ctrl+F, Ctrl+N…) closes it. The window loads the
queries off the UI thread and hands them over with ``set_results``.
"""

from __future__ import annotations

from datetime import datetime

import shiboken6
from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QFont,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..core.grouping import display_name, folder_label, modified_label
from ..core.i18n import tr
from ..core.models import SearchResult
from ..core.quick_search import (
    action_matches,
    cycle_operator,
    filter_results,
    name_spans,
)
from . import icons
from . import tokens as _tk
from .paint import KBD_HEIGHT, draw_kbd, draw_marked_text
from .theme import qcolor
from .widgets import KbdHint

# ── Geometry, in logical px (docs/redesign-ui.md, reference screen 05) ──────
WIDTH = 640
HEADER_HEIGHT = 54
FOOTER_HEIGHT = 38
SECTION_HEIGHT = 30
QUERY_HEIGHT = 46
ACTION_HEIGHT = 36
LIST_MAX_HEIGHT = 420
_TOP = 88  # card top, from the window top, when the window is tall enough
_MARGIN = 16  # least gap between the card and the window's edges
_LIST_PAD = 6  # rows, from the card's sides and the list's ends
_ICON = 16
_ICON_X = 12  # row icon, from the left of the row
_TEXT_X = 40  # row text, from the left of the row
_HINT_RIGHT = 10  # shortcut key, from the right of the row

# (id, icon, label key, shortcut shown); main_window runs them by id.
ACTIONS = (
    ("new_query", "plus", "toolbar.new_query", "Ctrl N"),
    ("open_folder", "folder_plus", "toolbar.open_folder", "Ctrl O"),
    ("reindex", "refresh", "command_palette.reindex", ""),
)

# ── Item data roles ─────────────────────────────────────────────────────────
_ROLE_KIND = Qt.ItemDataRole.UserRole  # "section" | "query" | "action" | "empty"
_ROLE_VALUE = Qt.ItemDataRole.UserRole + 1  # SearchResult or action id
# (text, spans, detail, icon, icon colour key, shortcut) for a query or action
_ROLE_VIEW = Qt.ItemDataRole.UserRole + 2

_PICKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


def _font(base: QFont, pixel_size: int, weight: QFont.Weight) -> QFont:
    font = QFont(base)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def _add_kbd(row: QHBoxLayout, keys: str) -> None:
    """Add a key hint the size of the ones the list paints, centred in *row*."""
    kbd = KbdHint(keys)
    kbd.setFixedHeight(KBD_HEIGHT)
    kbd.setMinimumWidth(KBD_HEIGHT)
    row.addWidget(kbd, 0, Qt.AlignmentFlag.AlignVCenter)


class _Delegate(QStyledItemDelegate):
    """Paints section titles and the two kinds of rows; the selected query
    shows the Enter key, actions their shortcut."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        kind = index.data(_ROLE_KIND)
        rect = option.rect.adjusted(_LIST_PAD, 0, -_LIST_PAD, 0)
        base = option.font
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind in ("section", "empty"):
            section = kind == "section"
            weight = QFont.Weight.DemiBold if section else QFont.Weight.Normal
            painter.setFont(_font(base, _tk.FONT_PX_SMALL, weight))
            painter.setPen(qcolor(_tk.color("text3")))
            align = (
                Qt.AlignmentFlag.AlignBottom
                if section
                else Qt.AlignmentFlag.AlignVCenter
            )
            painter.drawText(
                rect.adjusted(8, 0, -8, -6 if section else 0),
                Qt.AlignmentFlag.AlignLeft | align,
                str(index.data(Qt.ItemDataRole.DisplayRole)),
            )
            painter.restore()
            return

        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        if selected or state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(_tk.color("sel" if selected else "hover")))
            painter.drawRoundedRect(QRectF(rect), _tk.RADIUS, _tk.RADIUS)

        text, spans, detail, icon_name, icon_key, keys = index.data(_ROLE_VIEW)
        center_y = rect.center().y()
        glyph = QRect(rect.left() + _ICON_X, center_y - _ICON // 2 + 1, _ICON, _ICON)
        icons.icon(icon_name, icon_key, _ICON).paint(painter, glyph)
        right = rect.right() - _HINT_RIGHT
        hint = "Enter" if kind == "query" and selected else keys
        if hint:
            right = draw_kbd(painter, right, center_y + 1, hint) - 10
        left = rect.left() + _TEXT_X
        painter.setFont(_font(base, _tk.FONT_PX_BASE, QFont.Weight.Normal))
        if kind == "query":
            title = QRect(left, rect.top() + 5, right - left, 20)
            draw_marked_text(painter, title, text, "text", spans)
            painter.setFont(_font(base, _tk.FONT_PX_SMALL, QFont.Weight.Normal))
            line = QRect(left, rect.top() + 24, right - left, 17)
            draw_marked_text(painter, line, detail, "text3")
        else:
            line = QRect(left, rect.top(), right - left, rect.height())
            draw_marked_text(painter, line, text, "text", spans)
        painter.restore()


class _List(QListWidget):
    """The rows; never takes the focus, so typing stays in the input."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QuickSearchList")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, _LIST_PAD, 0, _LIST_PAD)
        self.setItemDelegate(_Delegate(self))


class CommandPalette(QWidget):
    """Quick search over *host*: call ``open()``, then ``set_results()``.

    Signals:
        query_selected(object) — the SearchResult to open.
        copy_requested(object) — the SearchResult whose SQL to copy.
        action_requested(str) — an id from ACTIONS.
        closed() — the palette is gone; it deletes itself.
    """

    query_selected = Signal(object)
    copy_requested = Signal(object)
    action_requested = Signal(str)
    closed = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("QuickSearchOverlay")
        self.hide()
        # The host is reached through parentWidget(), never kept here: a
        # reference from the palette would let deleting the palette delete
        # the window too, from inside the palette's own destructor.
        self._results: list[SearchResult] = []
        self._loaded = False
        self._closed = False
        self._previous_focus: QWidget | None = None

        self._card = QFrame(self)
        self._card.setObjectName("QuickSearch")
        self._shadow = QGraphicsDropShadowEffect(self._card)
        self._shadow.setBlurRadius(40)
        self._shadow.setOffset(0, 12)
        self._card.setGraphicsEffect(self._shadow)

        self._icon = QLabel()
        self._input = QLineEdit()
        self._input.setObjectName("QuickSearchInput")
        self._input.setFrame(False)
        self._input.textChanged.connect(self._refresh)
        self._input.installEventFilter(self)
        header = QWidget()
        header.setFixedHeight(HEADER_HEIGHT)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(18, 0, 16, 0)
        header_row.setSpacing(10)
        header_row.addWidget(self._icon)
        header_row.addWidget(self._input, 1)
        _add_kbd(header_row, "Esc")

        rule = QFrame()
        rule.setObjectName("QuickSearchRule")
        rule.setFixedHeight(1)

        self._list = _List()
        self._list.itemClicked.connect(self._activate)

        self._footer = QWidget()
        self._footer.setObjectName("QuickSearchFooter")
        self._footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._footer.setFixedHeight(FOOTER_HEIGHT)
        self._footer_labels: list[tuple[QLabel, str]] = []
        footer_row = QHBoxLayout(self._footer)
        footer_row.setContentsMargins(16, 0, 16, 0)
        footer_row.setSpacing(6)
        for keys, label_key in (
            (("↑", "↓"), "command_palette.navigate"),
            (("Enter",), "command_palette.open"),
            (("Ctrl C",), "command_palette.copy_sql"),
            (("Tab",), "command_palette.filter"),
        ):
            for key in keys:
                _add_kbd(footer_row, key)
            label = QLabel()
            label.setObjectName("QuickSearchHint")
            footer_row.addWidget(label)
            footer_row.addSpacing(12)
            self._footer_labels.append((label, label_key))
        footer_row.addStretch(1)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        card_layout.addWidget(header)
        card_layout.addWidget(rule)
        card_layout.addWidget(self._list)
        card_layout.addWidget(self._footer)

        self.retranslate_ui()
        self.refresh_theme()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Cover the host window and take the keyboard."""
        host = self._host()
        self._previous_focus = QApplication.focusWidget()
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self._refresh()
        self.show()
        self.raise_()
        self._input.setFocus()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.focusChanged.connect(self._on_focus_changed)

    def close_palette(self, restore_focus: bool = True) -> None:
        """Hide and delete the palette; give the focus back to where it was."""
        if self._closed:
            return
        self._closed = True
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.focusChanged.disconnect(self._on_focus_changed)
        self._host().removeEventFilter(self)
        self.hide()
        previous, self._previous_focus = self._previous_focus, None
        if (
            restore_focus
            and previous is not None
            and shiboken6.isValid(previous)
            and previous.isVisible()
        ):
            previous.setFocus()
        self.closed.emit()
        self.deleteLater()

    def is_open(self) -> bool:
        return not self._closed and self.isVisible()

    def focus(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    def set_results(self, results: list[SearchResult]) -> None:
        """The queries to pick from, as the window loaded them."""
        self._results = results
        self._loaded = True
        self._refresh()

    def text(self) -> str:
        return self._input.text()

    def rows(self) -> list[tuple[str, object]]:
        """(kind, value) of every row, top to bottom: the section title for a
        section, the SearchResult for a query, the id for an action."""
        rows: list[tuple[str, object]] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            kind = str(item.data(_ROLE_KIND))
            value = item.data(_ROLE_VALUE)
            if kind in ("section", "empty"):
                value = item.text()
            rows.append((kind, value))
        return rows

    def current(self) -> tuple[str, object] | None:
        """(kind, value) of the selected row, None without one."""
        item = self._list.currentItem()
        if item is None:
            return None
        return str(item.data(_ROLE_KIND)), item.data(_ROLE_VALUE)

    def _host(self) -> QWidget:
        host = self.parentWidget()
        assert host is not None  # created with one
        return host

    def card_geometry(self) -> QRect:
        return self._card.geometry()

    def retranslate_ui(self) -> None:
        self._input.setPlaceholderText(tr("command_palette.placeholder"))
        for label, key in self._footer_labels:
            label.setText(tr(key))
        if self._list.count():
            self._refresh()

    def refresh_theme(self) -> None:
        self._icon.setPixmap(icons.pixmap("search", "text2", 18))
        self._shadow.setColor(qcolor(_tk.color("shadow")))
        self.update()

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        text = self._input.text()
        self._list.clear()
        now = datetime.now()
        queries = filter_results(self._results, text)
        if queries:
            self._add_label("section", tr("command_palette.queries"))
        for result in queries:
            name, titled = display_name(result)
            detail = folder_label(result.folder, result.rel_path)
            when = modified_label(result.file_mtime, now)
            if when:
                detail = f"{detail} · {when}" if detail else when
            item = self._add_row("query", result, QUERY_HEIGHT)
            item.setData(
                _ROLE_VIEW,
                (
                    name,
                    name_spans(name, text),
                    detail,
                    "file",
                    "accent_text" if titled else "text3",
                    "",
                ),
            )
            item.setToolTip(f"{name}\n{result.rel_path}")

        actions = [a for a in ACTIONS if action_matches(tr(a[2]), text)]
        if actions:
            self._add_label("section", tr("command_palette.actions"))
        for action_id, icon_name, label_key, keys in actions:
            label = tr(label_key)
            item = self._add_row("action", action_id, ACTION_HEIGHT)
            item.setData(
                _ROLE_VIEW,
                (label, name_spans(label, text), "", icon_name, "text2", keys),
            )

        if not queries and not actions and (self._loaded or text.strip()):
            self._add_label("empty", tr("command_palette.no_results"))
        self._move(1, from_row=-1)
        self._fit()

    def _add_label(self, kind: str, text: str) -> None:
        item = QListWidgetItem(text)
        item.setData(_ROLE_KIND, kind)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        height = SECTION_HEIGHT if kind == "section" else ACTION_HEIGHT
        item.setSizeHint(QSize(0, height))
        self._list.addItem(item)

    def _add_row(self, kind: str, value: object, height: int) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(_ROLE_KIND, kind)
        item.setData(_ROLE_VALUE, value)
        item.setFlags(_PICKABLE)
        item.setSizeHint(QSize(0, height))
        self._list.addItem(item)
        return item

    def _move(self, step: int, from_row: int | None = None) -> None:
        """Select the next pickable row in the direction of *step*."""
        row = self._list.currentRow() if from_row is None else from_row
        row += step
        while 0 <= row < self._list.count():
            item = self._list.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                return
            row += step

    def _activate(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        kind = item.data(_ROLE_KIND)
        value = item.data(_ROLE_VALUE)
        if kind == "query":
            self.close_palette()
            self.query_selected.emit(value)
        elif kind == "action":
            self.close_palette()
            self.action_requested.emit(str(value))

    def _copy(self) -> bool:
        item = self._list.currentItem()
        if item is None or item.data(_ROLE_KIND) != "query":
            return False
        result = item.data(_ROLE_VALUE)
        self.close_palette()
        self.copy_requested.emit(result)
        return True

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _fit(self) -> None:
        """Size the list to its rows, within the window, and place the card."""
        rows = sum(
            self._list.item(i).sizeHint().height() for i in range(self._list.count())
        )
        width = min(WIDTH, max(0, self.width() - 2 * _MARGIN))
        top = max(_MARGIN, min(_TOP, self.height() // 8))
        chrome = HEADER_HEIGHT + 1 + FOOTER_HEIGHT
        room = self.height() - top - _MARGIN - chrome
        list_height = max(0, min(rows + 2 * _LIST_PAD, LIST_MAX_HEIGHT, room))
        self._list.setFixedHeight(list_height)
        self._card.setGeometry(
            (self.width() - width) // 2, top, width, chrome + list_height
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), qcolor(_tk.color("overlay")))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._card.geometry().contains(event.position().toPoint()):
            self.close_palette()
        event.accept()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        host = self.parentWidget()
        if obj is host and event.type() == QEvent.Type.Resize:
            self.setGeometry(host.rect())
            self._fit()
        elif obj is host and event.type() == QEvent.Type.Close:
            # Let go of the application's focus signal before the window goes.
            self.close_palette(restore_focus=False)
        elif obj is self._input and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.ShortcutOverride:
                # Esc closes the palette, not whatever the window binds it to.
                if event.key() == Qt.Key.Key_Escape:
                    event.accept()
                    return True
            elif event.type() == QEvent.Type.KeyPress:
                return self._on_key(event)
        return super().eventFilter(obj, event)

    def _on_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        if key == Qt.Key.Key_Down:
            self._move(1)
        elif key == Qt.Key.Key_Up:
            self._move(-1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate(self._list.currentItem())
        elif key == Qt.Key.Key_Escape:
            self.close_palette()
        elif key == Qt.Key.Key_Tab and not event.modifiers():
            text = self._input.text()
            changed = cycle_operator(text)
            if changed != text:
                self._input.setText(changed)
        elif key == Qt.Key.Key_Backtab:
            pass  # keep the focus in the palette
        elif event.matches(QKeySequence.StandardKey.Copy):
            if self._input.hasSelectedText():
                return False
            self._copy()
        else:
            return False
        return True

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        # None: the window lost the focus to another application; stay open.
        if new is not None and new is not self and not self.isAncestorOf(new):
            self.close_palette(restore_focus=False)
