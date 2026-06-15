from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import SearchResult
from .theme import tokens as _tk


class CommandPalette(QDialog):
    """Ctrl+P floating quick-search overlay.

    Shows all indexed queries; as the user types the list narrows.
    Pressing Enter (or double-clicking) selects the highlighted query
    and emits query_selected.

    Usage::
        palette = CommandPalette(results, parent)
        palette.query_selected.connect(handler)
        palette.exec()
    """

    query_selected = Signal(object)  # SearchResult

    def __init__(self, results: list[SearchResult], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(560, 400)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {_tk.CARD};
                border: 1px solid {_tk.BORDER_EMPH};
            }}
            QLineEdit {{
                background-color: {_tk.SURFACE};
                border: 1px solid {_tk.BORDER_EMPH};
                color: {_tk.TEXT_PRIMARY};
                border-radius: 4px;
                padding: 5px 8px;
            }}
            QLineEdit:focus {{
                border-color: {_tk.ACCENT_BORDER};
            }}
            QListWidget {{
                background-color: {_tk.SURFACE};
                border: 1px solid {_tk.BORDER_EMPH};
            }}
            QListWidget::item {{
                color: {_tk.TEXT_PRIMARY};
                padding: 6px 8px;
            }}
            QListWidget::item:hover {{
                background-color: {_tk.CARD};
            }}
            QListWidget::item:selected {{
                background-color: {_tk.CARD};
                color: {_tk.TEXT_PRIMARY};
            }}
        """)

        self._all_results = results

        hint = QLabel("Type to filter — Enter to open — Esc to close")
        hint.setStyleSheet(f"color: {_tk.TEXT_TERTIARY}; font-size: 11px; padding: 2px 4px;")

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search queries… (table:X  col:X  tag:X  date:DD/MM/YYYY  or name)"
        )
        self._search.textChanged.connect(self._filter)
        self._search.installEventFilter(self)

        self._list = QListWidget()
        self._list.itemActivated.connect(self._on_activated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(hint)
        layout.addWidget(self._search)
        layout.addWidget(self._list)

        self._populate(results)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    # ------------------------------------------------------------------

    def _populate(self, results: list[SearchResult]) -> None:
        self._list.clear()
        for r in results:
            item = QListWidgetItem(r.title)
            item.setData(Qt.ItemDataRole.UserRole, r)
            tags_str = "  ".join(f"#{t}" for t in r.tags)
            item.setToolTip(f"{r.rel_path}\n{tags_str}" if tags_str else r.rel_path)
            self._list.addItem(item)

    def _filter(self, text: str) -> None:
        from ..core.search import _parse_date_filter, parse_query

        raw = text.strip()
        if not raw:
            self._populate(self._all_results)
            if self._list.count() > 0:
                self._list.setCurrentRow(0)
            return

        filters, free_text = parse_query(raw)
        results: list = self._all_results

        for tname in filters["table"]:
            tl = tname.lower()
            results = [r for r in results if any(tl == t.lower() for t in r.tables)]

        for tag in filters["tag"]:
            tl = tag.lower()
            results = [r for r in results if any(tl == t.lower() for t in r.tags)]

        for date_str in filters["date"]:
            ts = _parse_date_filter(date_str)
            if ts is not None:
                start_ts, end_ts = ts
                results = [r for r in results if start_ts <= r.file_mtime <= end_ts]

        if free_text:
            needle = free_text.lower()
            results = [
                r for r in results
                if needle in r.title.lower()
                or needle in r.rel_path.lower()
                or any(needle in t.lower() for t in r.tags)
            ]

        self._populate(results)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_activated(self, item: QListWidgetItem) -> None:
        result: SearchResult | None = item.data(Qt.ItemDataRole.UserRole)
        if result is not None:
            self.query_selected.emit(result)
            self.accept()

    # ------------------------------------------------------------------
    # Keyboard navigation: Up/Down in search box moves list selection.
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if obj is self._search and isinstance(event, QKeyEvent):
            key = event.key()
            if key == Qt.Key.Key_Down:
                row = min(self._list.currentRow() + 1, self._list.count() - 1)
                self._list.setCurrentRow(row)
                return True
            if key == Qt.Key.Key_Up:
                row = max(self._list.currentRow() - 1, 0)
                self._list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self._list.currentItem()
                if item:
                    self._on_activated(item)
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)
