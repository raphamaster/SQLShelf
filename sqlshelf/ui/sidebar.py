from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_ALL_ITEM_TEXT = "All queries"
_FAVORITES_ITEM_TEXT = "★  Favorites"
_RECENT_ITEM_TEXT = "⌛  Recent"


class SidebarWidget(QWidget):
    """Left panel: Open Folder button, Favorites/Recent shortcuts, and tag browser.

    Signals:
        open_folder_requested  — user clicked Open Folder.
        tag_selected(str)      — user clicked a tag; empty string = all queries.
        favorites_selected     — user clicked Favorites.
        recent_selected        — user clicked Recent.
    """

    open_folder_requested = Signal()
    tag_selected = Signal(str)
    favorites_selected = Signal()
    recent_selected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(140)

        app_title = QLabel("SQLSHELF")
        app_title.setStyleSheet("font-weight: bold; font-size: 14px; letter-spacing: 1px;")

        self._open_btn = QPushButton("📂  Open Folder…")
        self._open_btn.clicked.connect(self.open_folder_requested)

        nav_label = QLabel("BROWSE")
        nav_label.setStyleSheet("font-weight: bold; margin-top: 12px; color: #aaaaaa;")

        self._nav_list = QListWidget()
        self._nav_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Disable scrollbars — the 3 nav items must always be fully visible
        self._nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._all_item = QListWidgetItem(_ALL_ITEM_TEXT)
        self._fav_item = QListWidgetItem(_FAVORITES_ITEM_TEXT)
        self._recent_item = QListWidgetItem(_RECENT_ITEM_TEXT)
        self._nav_list.addItem(self._all_item)
        self._nav_list.addItem(self._fav_item)
        self._nav_list.addItem(self._recent_item)
        self._nav_list.setCurrentItem(self._all_item)

        # Use itemClicked so re-clicking the already-selected item also fires
        self._nav_list.itemClicked.connect(self._on_nav_item_clicked)

        tags_label = QLabel("TAGS")
        tags_label.setStyleSheet("font-weight: bold; margin-top: 8px; color: #aaaaaa;")

        self._tag_list = QListWidget()
        self._tag_list.itemClicked.connect(self._on_tag_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(app_title)
        layout.addWidget(self._open_btn)
        layout.addWidget(nav_label)
        layout.addWidget(self._nav_list)
        layout.addWidget(tags_label)
        layout.addWidget(self._tag_list, stretch=1)

        self._update_nav_height()

    def _update_nav_height(self) -> None:
        """Size the nav list to exactly fit its 3 items with no scrollbar."""
        self._nav_list.adjustSize()
        total = sum(
            self._nav_list.sizeHintForRow(i) for i in range(self._nav_list.count())
        )
        self._nav_list.setFixedHeight(total + 6)

    def set_tags(self, tags: list[str]) -> None:
        """Refresh the tag list. Preserves current selection if still valid."""
        current = self._selected_tag()
        self._tag_list.blockSignals(True)
        self._tag_list.clear()
        for tag in sorted(tags):
            self._tag_list.addItem(QListWidgetItem(tag))
        self._tag_list.blockSignals(False)
        if current:
            for i in range(self._tag_list.count()):
                if self._tag_list.item(i).text() == current:
                    self._tag_list.setCurrentRow(i)
                    return
        self._tag_list.clearSelection()

    def select_all(self) -> None:
        """Programmatically switch the nav selection back to 'All queries'."""
        self._nav_list.blockSignals(True)
        self._nav_list.setCurrentItem(self._all_item)
        self._nav_list.blockSignals(False)
        self._tag_list.clearSelection()

    def _selected_tag(self) -> str:
        item = self._tag_list.currentItem()
        return item.text() if item else ""

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
        # Deselect tag list when a nav item is clicked
        self._tag_list.blockSignals(True)
        self._tag_list.clearSelection()
        self._tag_list.blockSignals(False)
        text = item.text()
        if text == _FAVORITES_ITEM_TEXT:
            self.favorites_selected.emit()
        elif text == _RECENT_ITEM_TEXT:
            self.recent_selected.emit()
        else:
            self.tag_selected.emit("")

    def _on_tag_item_clicked(self, item: QListWidgetItem) -> None:
        # Switch nav to "All" silently when a tag is clicked
        self._nav_list.blockSignals(True)
        self._nav_list.setCurrentItem(self._all_item)
        self._nav_list.blockSignals(False)
        self.tag_selected.emit(item.text())
