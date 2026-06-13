from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ..core.models import SearchResult


class QueryListWidget(QWidget):
    """Middle panel: displays search results and emits query_selected."""

    query_selected = Signal(object)   # SearchResult
    context_action = Signal(str, object)  # (action_name, SearchResult)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = QListWidget()
        self._results: list[SearchResult] = []
        self._list.currentRowChanged.connect(self._on_row_changed)

        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)

    def set_results(self, results: list[SearchResult]) -> None:
        current_path = self._current_rel_path()
        self._results = results
        self._list.blockSignals(True)
        self._list.clear()
        restore_row = -1
        for i, r in enumerate(results):
            item = QListWidgetItem(r.title)
            tags_str = "  ".join(f"#{t}" for t in r.tags)
            tooltip = f"{r.rel_path}\n{tags_str}" if tags_str else r.rel_path
            item.setToolTip(tooltip)
            self._list.addItem(item)
            if r.rel_path == current_path:
                restore_row = i
        self._list.blockSignals(False)
        if restore_row >= 0:
            self._list.setCurrentRow(restore_row)
        elif results:
            self._list.setCurrentRow(0)

    def select_by_rel_path(self, rel_path: str) -> None:
        """Select the item matching rel_path, if present."""
        for i, r in enumerate(self._results):
            if r.rel_path == rel_path:
                self._list.setCurrentRow(i)
                return

    def _current_rel_path(self) -> str | None:
        row = self._list.currentRow()
        if 0 <= row < len(self._results):
            return self._results[row].rel_path
        return None

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._results):
            self.query_selected.emit(self._results[row])

    def _show_context_menu(self, pos) -> None:
        row = self._list.currentRow()
        item = self._list.itemAt(pos)
        if item is None:
            return
        # Resolve which result was right-clicked (may differ from currentRow)
        clicked_row = self._list.row(item)
        if 0 <= clicked_row < len(self._results):
            result = self._results[clicked_row]
        else:
            return

        menu = QMenu(self._list)

        fav_act = menu.addAction("☆  Toggle Favorite")
        dup_act = menu.addAction("⎘  Duplicate Query…")
        copy_act = menu.addAction("📋  Copy SQL")
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

    def count(self) -> int:
        return len(self._results)
