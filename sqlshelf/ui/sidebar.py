from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_ALL_ITEM_TEXT = "All queries"
_FAVORITES_ITEM_TEXT = "★  Favorites"
_RECENT_ITEM_TEXT = "⌛  Recent"


class SidebarWidget(QWidget):
    """Left panel: Open Folder button, pinned folders, Favorites/Recent, tag browser.

    Signals:
        open_folder_requested  — user clicked Open Folder.
        tag_selected(str)      — user clicked a tag; empty string = all queries.
        favorites_selected     — user clicked Favorites.
        recent_selected        — user clicked Recent.
        pinned_folder_selected(Path) — user clicked a pinned folder.
        unpin_folder_requested(Path) — user chose Unpin from context menu.
    """

    open_folder_requested = Signal()
    tag_selected = Signal(str)
    favorites_selected = Signal()
    recent_selected = Signal()
    pinned_folder_selected = Signal(object)   # Path
    unpin_folder_requested = Signal(object)   # Path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(140)

        app_title = QLabel("SQLSHELF")
        app_title.setStyleSheet("font-weight: bold; font-size: 14px; letter-spacing: 1px;")

        self._open_btn = QPushButton("📂  Open Folder…")
        self._open_btn.clicked.connect(self.open_folder_requested)

        # --- Pinned folders section ---
        self._pinned_label = QLabel("PINNED")
        self._pinned_label.setStyleSheet("font-weight: bold; margin-top: 12px; color: #aaaaaa;")

        self._pinned_list = QListWidget()
        self._pinned_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._pinned_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pinned_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pinned_list.itemClicked.connect(self._on_pinned_item_clicked)
        self._pinned_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._pinned_list.customContextMenuRequested.connect(self._show_pinned_context_menu)

        # --- Browse section ---
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
        layout.addWidget(self._pinned_label)
        layout.addWidget(self._pinned_list)
        layout.addWidget(nav_label)
        layout.addWidget(self._nav_list)
        layout.addWidget(tags_label)
        layout.addWidget(self._tag_list, stretch=1)

        self._update_nav_height()
        self._set_pinned_visible(False)

    # ------------------------------------------------------------------
    # Pinned folders
    # ------------------------------------------------------------------

    def set_pinned_folders(self, folders: list[Path]) -> None:
        """Rebuild the pinned folders section."""
        self._pinned_list.clear()
        for folder in folders:
            item = QListWidgetItem(f"📌  {folder.name}")
            item.setToolTip(str(folder))
            item.setData(Qt.ItemDataRole.UserRole, folder)
            self._pinned_list.addItem(item)
        visible = len(folders) > 0
        self._set_pinned_visible(visible)
        if visible:
            self._update_pinned_height()

    def _set_pinned_visible(self, visible: bool) -> None:
        self._pinned_label.setVisible(visible)
        self._pinned_list.setVisible(visible)

    def _update_pinned_height(self) -> None:
        self._pinned_list.adjustSize()
        total = sum(
            self._pinned_list.sizeHintForRow(i) for i in range(self._pinned_list.count())
        )
        self._pinned_list.setFixedHeight(total + 6)

    def _on_pinned_item_clicked(self, item: QListWidgetItem) -> None:
        folder: Path = item.data(Qt.ItemDataRole.UserRole)
        self._pinned_list.clearSelection()
        self.pinned_folder_selected.emit(folder)

    def _show_pinned_context_menu(self, pos) -> None:
        item = self._pinned_list.itemAt(pos)
        if item is None:
            return
        folder: Path = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        open_act = menu.addAction(f"Open  {folder.name}")
        menu.addSeparator()
        unpin_act = menu.addAction("Unpin")
        chosen = menu.exec(self._pinned_list.mapToGlobal(pos))
        if chosen == open_act:
            self.pinned_folder_selected.emit(folder)
        elif chosen == unpin_act:
            self.unpin_folder_requested.emit(folder)

    # ------------------------------------------------------------------
    # Nav / tags
    # ------------------------------------------------------------------

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
