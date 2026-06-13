from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme.tokens import ACCENT, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY

_ROLE_PATH = Qt.ItemDataRole.UserRole


class CollapsibleSection(QWidget):
    """Header QToolButton with a chevron arrow that toggles content visibility."""

    def __init__(
        self,
        title: str,
        content_widget: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._content = content_widget

        self._header = QToolButton()
        self._header.setObjectName("SectionHeader")
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.DownArrow)
        self._header.setText(title)
        self._header.setCheckable(True)
        self._header.setChecked(True)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.toggled.connect(self._on_toggled)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._header)
        layout.addWidget(content_widget)

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._header.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )


class SidebarWidget(QWidget):
    """Left panel: Browse shortcuts (top), collapsible Folders, collapsible Tags.

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
    folder_selected = Signal(object)          # Path
    folder_remove_requested = Signal(object)  # Path
    folder_favorite_toggled = Signal(object)  # Path
    tag_selected = Signal(str)
    favorites_selected = Signal()
    recent_selected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(150)
        self.setObjectName("SidebarWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._active_folder: Path | None = None
        self._active_item: QListWidgetItem | None = None
        self._active_nav_btn: QPushButton | None = None

        # ── App icon + title row ──────────────────────────────────────────────
        title_row = QWidget()
        title_row.setObjectName("TitleRow")
        title_row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        tr_layout = QHBoxLayout(title_row)
        tr_layout.setContentsMargins(0, 2, 0, 4)
        tr_layout.setSpacing(6)

        icon_lbl = QLabel("≡")
        icon_lbl.setStyleSheet(
            f"color: {ACCENT}; font-size: 18px; font-weight: bold; background: transparent;"
        )
        icon_lbl.setFixedWidth(18)

        name_lbl = QLabel("SQLShelf")
        name_lbl.setStyleSheet(
            f"font-weight: 600; font-size: 14px; color: {TEXT_PRIMARY};"
            " background: transparent; letter-spacing: 0.3px;"
        )

        tr_layout.addWidget(icon_lbl)
        tr_layout.addWidget(name_lbl)
        tr_layout.addStretch()

        # ── Open Folder button ────────────────────────────────────────────────
        self._open_btn = QPushButton("📂  Open Folder")
        self._open_btn.setObjectName("OpenFolderBtn")
        self._open_btn.clicked.connect(self.open_folder_requested)

        # ── BROWSE section ───────────────────────────────────────────────────
        browse_label = QLabel("BROWSE")
        browse_label.setObjectName("SectionLabel")
        browse_label.setContentsMargins(4, 10, 0, 2)

        self._btn_all = self._make_nav_btn("☰  All queries")
        self._btn_fav = self._make_nav_btn("☆  Favorites")
        self._btn_recent = self._make_nav_btn("⌚  Recent")

        self._btn_all.clicked.connect(self._on_all_clicked)
        self._btn_fav.clicked.connect(self._on_fav_clicked)
        self._btn_recent.clicked.connect(self._on_recent_clicked)

        browse_widget = QWidget()
        browse_widget.setObjectName("BrowseWidget")
        browse_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        browse_layout = QVBoxLayout(browse_widget)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.setSpacing(1)
        browse_layout.addWidget(self._btn_all)
        browse_layout.addWidget(self._btn_fav)
        browse_layout.addWidget(self._btn_recent)

        # ── FOLDERS section content ──────────────────────────────────────────
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

        folders_content = QWidget()
        folders_inner = QVBoxLayout(folders_content)
        folders_inner.setContentsMargins(0, 0, 0, 0)
        folders_inner.setSpacing(0)
        folders_inner.addWidget(self._empty_label)
        folders_inner.addWidget(self._folders_list)

        self._folders_section = CollapsibleSection("FOLDERS", folders_content)

        # ── TAGS section content ─────────────────────────────────────────────
        self._tag_list = QListWidget()
        self._tag_list.itemClicked.connect(self._on_tag_item_clicked)

        self._tags_section = CollapsibleSection("TAGS", self._tag_list)

        # ── Layout ───────────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(2)
        layout.addWidget(title_row)
        layout.addSpacing(6)
        layout.addWidget(self._open_btn)
        layout.addWidget(browse_label)
        layout.addWidget(browse_widget)
        layout.addWidget(self._folders_section)
        layout.addWidget(self._tags_section, stretch=1)

        self._set_active_btn(self._btn_all)
        self._set_folders_state([])

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

    def _deactivate_nav_btn(self) -> None:
        if self._active_nav_btn is not None:
            self._active_nav_btn.setProperty("active", False)
            self._active_nav_btn.style().unpolish(self._active_nav_btn)
            self._active_nav_btn.style().polish(self._active_nav_btn)
            self._active_nav_btn = None

    def _on_folder_item_clicked(self, item: QListWidgetItem) -> None:
        self._deactivate_nav_btn()
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
        self._set_active_btn(self._btn_all)
        self._tag_list.clearSelection()

    # ------------------------------------------------------------------
    # Nav — internal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_nav_btn(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("NavButton")
        btn.setProperty("active", False)
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return btn

    def _set_active_btn(self, btn: QPushButton) -> None:
        if self._active_nav_btn is btn:
            return
        if self._active_nav_btn is not None:
            self._active_nav_btn.setProperty("active", False)
            self._active_nav_btn.style().unpolish(self._active_nav_btn)
            self._active_nav_btn.style().polish(self._active_nav_btn)
        self._active_nav_btn = btn
        btn.setProperty("active", True)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _on_all_clicked(self) -> None:
        self._set_active_btn(self._btn_all)
        self._tag_list.clearSelection()
        self.tag_selected.emit("")

    def _on_fav_clicked(self) -> None:
        self._set_active_btn(self._btn_fav)
        self._tag_list.clearSelection()
        self.favorites_selected.emit()

    def _on_recent_clicked(self) -> None:
        self._set_active_btn(self._btn_recent)
        self._tag_list.clearSelection()
        self.recent_selected.emit()

    def _on_tag_item_clicked(self, item: QListWidgetItem) -> None:
        self._set_active_btn(self._btn_all)
        self.tag_selected.emit(item.text())

    def _selected_tag(self) -> str:
        item = self._tag_list.currentItem()
        return item.text() if item else ""
