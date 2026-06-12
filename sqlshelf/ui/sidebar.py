from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SidebarWidget(QWidget):
    """Left panel: Open Folder button and tag browser.

    Emits:
        open_folder_requested — user clicked Open Folder.
        tag_selected(str)     — user clicked a tag; empty string = all.
    """

    open_folder_requested = Signal()
    tag_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(140)

        app_title = QLabel("SQLSHELF")
        app_title.setStyleSheet("font-weight: bold; font-size: 14px; letter-spacing: 1px;")

        self._open_btn = QPushButton("Open Folder…")
        self._open_btn.clicked.connect(self.open_folder_requested)

        tags_label = QLabel("TAGS")
        tags_label.setStyleSheet("font-weight: bold; margin-top: 12px; color: #aaaaaa;")

        self._all_item = QListWidgetItem("All queries")
        self._tag_list = QListWidget()
        self._tag_list.addItem(self._all_item)
        self._tag_list.setCurrentItem(self._all_item)
        self._tag_list.currentItemChanged.connect(self._on_tag_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(app_title)
        layout.addWidget(self._open_btn)
        layout.addWidget(tags_label)
        layout.addWidget(self._tag_list)

    def set_tags(self, tags: list[str]) -> None:
        """Refresh the tag list. Preserves current selection if still valid."""
        current = self._selected_tag()
        self._tag_list.blockSignals(True)
        self._tag_list.clear()
        self._all_item = QListWidgetItem("All queries")
        self._tag_list.addItem(self._all_item)
        for tag in sorted(tags):
            self._tag_list.addItem(QListWidgetItem(tag))
        self._tag_list.blockSignals(False)
        # Restore selection
        if current:
            for i in range(self._tag_list.count()):
                if self._tag_list.item(i).text() == current:
                    self._tag_list.setCurrentRow(i)
                    return
        self._tag_list.setCurrentRow(0)

    def _selected_tag(self) -> str:
        item = self._tag_list.currentItem()
        if item is None or item is self._all_item or item.text() == "All queries":
            return ""
        return item.text()

    def _on_tag_changed(self, current: QListWidgetItem | None, _prev) -> None:
        if current is None:
            self.tag_selected.emit("")
        elif current is self._all_item or current.text() == "All queries":
            self.tag_selected.emit("")
        else:
            self.tag_selected.emit(current.text())
