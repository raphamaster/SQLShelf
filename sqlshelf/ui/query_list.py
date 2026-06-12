from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ..core.models import SearchResult


class QueryListWidget(QWidget):
    """Middle panel: displays search results and emits query_selected."""

    query_selected = Signal(object)  # SearchResult

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = QListWidget()
        self._results: list[SearchResult] = []
        self._list.currentRowChanged.connect(self._on_row_changed)

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

    def _current_rel_path(self) -> str | None:
        row = self._list.currentRow()
        if 0 <= row < len(self._results):
            return self._results[row].rel_path
        return None

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._results):
            self.query_selected.emit(self._results[row])

    def count(self) -> int:
        return len(self._results)
