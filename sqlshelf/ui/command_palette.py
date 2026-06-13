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

        self._all_results = results

        hint = QLabel("Type to filter queries — Enter to open")
        hint.setStyleSheet("color: #888888; font-size: 11px; padding: 2px 4px;")

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search queries…")
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
        text = text.strip().lower()
        if not text:
            self._populate(self._all_results)
        else:
            filtered = [
                r for r in self._all_results
                if text in r.title.lower()
                or text in r.rel_path.lower()
                or any(text in t.lower() for t in r.tags)
            ]
            self._populate(filtered)
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
