from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from .theme.tokens import TEXT_SECONDARY, TEXT_TERTIARY
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_ALL_ITEM_TEXT = "All queries"
_FAVORITES_ITEM_TEXT = "★  Favorites"
_RECENT_ITEM_TEXT = "⌛  Recent"

_ROLE_PATH = Qt.ItemDataRole.UserRole


class SidebarWidget(QWidget):
    """Left panel: folder explorer, Favorites/Recent shortcuts, and tag browser.

    Signals:
        open_folder_requested      — user clicked the top "Open Folder…" button.
        folder_selected(Path)      — user clicked a known folder to open/switch to it.
        folder_remove_requested(Path)   — user chose Remove from context menu.
        folder_favorite_toggled(Path)   — user chose Favorite/Unfavorite.
        tag_selected(str)          — user clicked a tag; empty string = show all.
        favorites_selected         — user clicked Favorites.
        recent_selected            — user clicked Recent.
    """

    open_folder_requested = Signal()
    folder_selected = Signal(object)         # Path
    folder_remove_requested = Signal(object) # Path
    folder_favorite_toggled = Signal(object) # Path
    tag_selected = Signal(str)
    favorites_selected = Signal()
    recent_selected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(150)

        self._active_folder: Path | None = None
        self._active_item: QListWidgetItem | None = None

        # ── Title ───────────────────────────────────────────────────────────
        app_title = QLabel("SQLSHELF")
        app_title.setStyleSheet(
            "font-weight: bold; font-size: 14px; letter-spacing: 1px;"
        )

        # ── Open / Add Folder button ─────────────────────────────────────────
        self._open_btn = QPushButton("📂  Open Folder…")
        self._open_btn.clicked.connect(self.open_folder_requested)

        # ── FOLDERS section ──────────────────────────────────────────────────
        folders_header = QHBoxLayout()
        folders_header.setContentsMargins(0, 12, 0, 2)

        folders_lbl = QLabel("FOLDERS")
        folders_lbl.setStyleSheet(f"font-weight: bold; color: {TEXT_SECONDARY};")

        folders_header.addWidget(folders_lbl)
        folders_header.addStretch()

        folders_header_widget = QWidget()
        folders_header_widget.setLayout(folders_header)

        self._empty_label = QLabel("No folders yet.\nClick 'Open Folder' to add one.")
        self._empty_label.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: 11px;")
        self._empty_label.setWordWrap(True)
        self._empty_label.setContentsMargins(4, 4, 4, 4)

        self._folders_list = QListWidget()
        self._folders_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._folders_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self._folders_list.setMaximumHeight(220)
        self._folders_list.itemClicked.connect(self._on_folder_item_clicked)
        self._folders_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._folders_list.customContextMenuRequested.connect(
            self._show_folder_context_menu
        )

        # ── BROWSE section ───────────────────────────────────────────────────
        nav_label = QLabel("BROWSE")
        nav_label.setStyleSheet(f"font-weight: bold; margin-top: 12px; color: {TEXT_SECONDARY};")

        self._nav_list = QListWidget()
        self._nav_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Let the list grow vertically to fit all items; never squeeze it
        self._nav_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._all_item = QListWidgetItem(_ALL_ITEM_TEXT)
        self._fav_item = QListWidgetItem(_FAVORITES_ITEM_TEXT)
        self._recent_item = QListWidgetItem(_RECENT_ITEM_TEXT)
        self._nav_list.addItem(self._all_item)
        self._nav_list.addItem(self._fav_item)
        self._nav_list.addItem(self._recent_item)
        self._nav_list.setCurrentItem(self._all_item)
        self._nav_list.itemClicked.connect(self._on_nav_item_clicked)

        # ── TAGS section ─────────────────────────────────────────────────────
        tags_label = QLabel("TAGS")
        tags_label.setStyleSheet(f"font-weight: bold; margin-top: 8px; color: {TEXT_SECONDARY};")

        self._tag_list = QListWidget()
        self._tag_list.itemClicked.connect(self._on_tag_item_clicked)

        # ── Layout ───────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)
        layout.addWidget(app_title)
        layout.addWidget(self._open_btn)
        layout.addWidget(folders_header_widget)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._folders_list)
        layout.addWidget(nav_label)
        layout.addWidget(self._nav_list)
        layout.addWidget(tags_label)
        layout.addWidget(self._tag_list, stretch=1)

        self._update_nav_height()
        self._set_folders_state([])

    def showEvent(self, event) -> None:
        """Recalculate nav height after the widget is fully rendered."""
        super().showEvent(event)
        self._update_nav_height()

    # ------------------------------------------------------------------
    # Folder explorer public API
    # ------------------------------------------------------------------

    def set_folders(
        self,
        entries: list[tuple[Path, bool]],
        active: Path | None,
    ) -> None:
        """Rebuild the folders list.

        *entries* is a list of (path, is_favorited).
        *active* is the currently loaded folder path (may be None).
        """
        self._active_folder = active
        self._active_item = None
        self._folders_list.blockSignals(True)
        self._folders_list.clear()

        bold = QFont()
        bold.setBold(True)

        for path, favorited in entries:
            prefix = "★  " if favorited else "    "
            item = QListWidgetItem(prefix + path.name)
            item.setToolTip(str(path))
            item.setData(_ROLE_PATH, path)
            if active is not None and path.resolve() == active.resolve():
                item.setFont(bold)
                self._active_item = item
            self._folders_list.addItem(item)

        # Keep the selection highlight visible even when focus is elsewhere
        if self._active_item is not None:
            self._folders_list.setCurrentItem(self._active_item)
        else:
            self._folders_list.clearSelection()

        self._folders_list.blockSignals(False)
        self._set_folders_state(entries)

    def set_active_folder(self, active: Path | None) -> None:
        """Update only the active-folder highlight without rebuilding the list."""
        self._active_folder = active
        self._active_item = None
        bold = QFont()
        bold.setBold(True)
        normal = QFont()
        self._folders_list.blockSignals(True)
        for i in range(self._folders_list.count()):
            item = self._folders_list.item(i)
            path: Path = item.data(_ROLE_PATH)
            is_active = active is not None and path.resolve() == active.resolve()
            item.setFont(bold if is_active else normal)
            if is_active:
                self._active_item = item
        if self._active_item is not None:
            self._folders_list.setCurrentItem(self._active_item)
        else:
            self._folders_list.clearSelection()
        self._folders_list.blockSignals(False)

    # ------------------------------------------------------------------
    # Folder explorer — internal
    # ------------------------------------------------------------------

    def _set_folders_state(self, entries: list) -> None:
        has = len(entries) > 0
        self._empty_label.setVisible(not has)
        self._folders_list.setVisible(has)

    def _on_folder_item_clicked(self, item: QListWidgetItem) -> None:
        path: Path = item.data(_ROLE_PATH)
        self.folder_selected.emit(path)

    def _show_folder_context_menu(self, pos) -> None:
        item = self._folders_list.itemAt(pos)
        if item is None:
            return
        path: Path = item.data(_ROLE_PATH)
        is_fav = "★" in item.text()

        menu = QMenu(self)
        open_act = menu.addAction(f'Open  "{path.name}"')
        menu.addSeparator()
        fav_act = menu.addAction("Unfavorite" if is_fav else "Favorite")
        menu.addSeparator()
        remove_act = menu.addAction("Remove from sidebar")

        chosen = menu.exec(self._folders_list.mapToGlobal(pos))
        if chosen == open_act:
            self.folder_selected.emit(path)
        elif chosen == fav_act:
            self.folder_favorite_toggled.emit(path)
        elif chosen == remove_act:
            self.folder_remove_requested.emit(path)

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Nav
    # ------------------------------------------------------------------

    def select_all(self) -> None:
        """Programmatically switch the nav selection back to 'All queries'."""
        self._nav_list.blockSignals(True)
        self._nav_list.setCurrentItem(self._all_item)
        self._nav_list.blockSignals(False)
        self._tag_list.clearSelection()

    def add_nav_item(self, text: str) -> QListWidgetItem:
        """Append an item to the BROWSE section and resize the list to fit."""
        item = QListWidgetItem(text)
        self._nav_list.addItem(item)
        self._update_nav_height()
        return item

    def _update_nav_height(self) -> None:
        """Fix the nav list height to exactly fit all items (no scrollbar).

        sizeHintForRow returns -1 before the widget is rendered, so we fall
        back to a 28 px estimate; showEvent calls this again with real values.
        """
        n = self._nav_list.count()
        if n == 0:
            return
        row_h = self._nav_list.sizeHintForRow(0)
        if row_h < 1:
            row_h = 28  # pre-render fallback
        self._nav_list.setFixedHeight(n * row_h + 4)

    def _selected_tag(self) -> str:
        item = self._tag_list.currentItem()
        return item.text() if item else ""

    def _on_nav_item_clicked(self, item: QListWidgetItem) -> None:
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
        self._nav_list.blockSignals(True)
        self._nav_list.setCurrentItem(self._all_item)
        self._nav_list.blockSignals(False)
        self.tag_selected.emit(item.text())
