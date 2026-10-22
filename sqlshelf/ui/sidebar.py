"""Left panel: the library views, the folder tree and the most used tags.

Everything is one QTreeWidget painted by :class:`_SidebarDelegate`, so views,
folders and tags share one geometry: 30 px rows, counts on the right, and the
active row on a rounded ``sel_soft`` background. Subfolders and counts come
from a :class:`LibrarySummary` the window gathers off the UI thread.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QFont,
    QFontMetrics,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.folder_tree import FolderNode
from ..core.i18n import tr
from ..core.library_summary import LibrarySummary
from ..core.navigation import NavState
from . import icons
from . import tokens as _tk
from .theme import qcolor

# Item data roles
_KIND = (
    Qt.ItemDataRole.UserRole
)  # "view" | "header" | "hint" | "folder" | "tag" | "more"
_VALUE = Qt.ItemDataRole.UserRole + 1  # view key, header key, folder Path, tag name
_COUNT = Qt.ItemDataRole.UserRole + 2  # int, or None while unknown
_DEPTH = Qt.ItemDataRole.UserRole + 3  # folder nesting, 0 for a library folder
_ACTIVE = Qt.ItemDataRole.UserRole + 4
_FAVORITE = Qt.ItemDataRole.UserRole + 5  # library folder marked as favorite

_CLICKABLE = {"view", "folder", "tag", "more"}
_TOP_TAGS = 7

# Geometry, in logical px (docs/redesign-ui.md, reference screens)
ROW_HEIGHT = 30
_ROW_GAP = 1
_HEADER_GAP = 12  # space above a section header
_HINT_HEIGHT = 40
_INSET = 8  # active/hover background, from each side
_HEADER_X = 19
_ICON_X = 19  # view icon and tag dot column (centre 27)
_TEXT_X = 45
_CHEVRON_X = 18
_FOLDER_ICON_X = 37
_FOLDER_TEXT_X = 63
_DEPTH_STEP = 18
_COUNT_RIGHT = 18  # count text ends this far from the right edge
_PLUS_SIZE = 22
_TAG_DOT = 8

_VIEWS = (
    ("all", "sidebar.all_queries", "library"),
    ("favorites", "sidebar.favorites", "star"),
    ("recent", "sidebar.recent", "clock"),
)
_VIEW_ICONS = {key: icon for key, _label, icon in _VIEWS}


def _band(row: QRect, kind: str) -> QRect:
    """The 30 px strip of *row* that holds its content."""
    top = row.top() + (_HEADER_GAP if kind == "header" else 0)
    return QRect(row.left(), top, row.width(), ROW_HEIGHT)


def _plus_rect(row: QRect) -> QRect:
    """The + (Open folder) button on the Folders header row."""
    band = _band(row, "header")
    centre_x = row.right() - _COUNT_RIGHT - 5
    return QRect(
        centre_x - _PLUS_SIZE // 2,
        band.center().y() - _PLUS_SIZE // 2 + 1,
        _PLUS_SIZE,
        _PLUS_SIZE,
    )


def _chevron_rect(row: QRect, depth: int) -> QRect:
    """Click target of a folder's expand chevron (wider than the glyph)."""
    band = _band(row, "folder")
    return QRect(_CHEVRON_X + depth * _DEPTH_STEP - 4, band.top(), 22, band.height())


def _active_key(state: NavState) -> tuple[str, object] | None:
    """The sidebar row that stands for *state*."""
    if state.kind in ("favorites", "recent"):
        return ("view", state.kind)
    if state.kind == "tag":
        return ("tag", state.value)
    if state.folder is not None:
        return ("folder", state.folder)
    return ("view", "all")


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _folder_label(path: Path) -> str:
    return path.name or str(path)  # a drive root has no name


# ---------------------------------------------------------------------------
# Tree view
# ---------------------------------------------------------------------------


class _SidebarTree(QTreeWidget):
    """The sidebar's item view. Clicks are reported with their position, so the
    widget can tell a click on a chevron or on the + from one on the row."""

    row_clicked = Signal(object, QPoint)  # (QTreeWidgetItem, viewport pos)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarTree")
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setIndentation(0)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(False)
        self.setAnimated(False)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Keyboard users reach it with Tab; a click does not leave a focus ring.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.plus_hovered = False

    def drawBranches(self, painter, rect, index) -> None:  # noqa: N802
        """Chevrons are drawn by the delegate."""

    def _plus_at(self, pos: QPoint) -> QTreeWidgetItem | None:
        item = self.itemAt(pos)
        if (
            item is not None
            and item.data(0, _KIND) == "header"
            and item.data(0, _VALUE) == "folders"
            and _plus_rect(self.visualItemRect(item)).contains(pos)
        ):
            return item
        return None

    def _set_plus_hovered(self, hovered: bool) -> None:
        if hovered != self.plus_hovered:
            self.plus_hovered = hovered
            self.viewport().update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item is not None:
            self.setCurrentItem(item)
            self.row_clicked.emit(item, pos)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is not None and item.data(0, _KIND) == "folder" and item.childCount():
            item.setExpanded(not item.isExpanded())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._set_plus_hovered(self._plus_at(event.position().toPoint()) is not None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._set_plus_hovered(False)
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        item = self.currentItem()
        if item is not None and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.row_clicked.emit(item, QPoint(-1, -1))
            event.accept()
            return
        super().keyPressEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            if self._plus_at(event.pos()) is not None:
                QToolTip.showText(event.globalPos(), tr("toolbar.open_folder_tip"))
                return True
        return super().viewportEvent(event)


# ---------------------------------------------------------------------------
# Delegate
# ---------------------------------------------------------------------------


class _SidebarDelegate(QStyledItemDelegate):
    """Paints every sidebar row; colours are read at paint time, so a theme
    switch only needs a repaint."""

    def __init__(self, tree: _SidebarTree) -> None:
        super().__init__(tree)
        self._tree = tree

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        kind = index.data(_KIND)
        if kind == "header":
            return QSize(0, _HEADER_GAP + ROW_HEIGHT)
        if kind == "hint":
            return QSize(0, _HINT_HEIGHT)
        return QSize(0, ROW_HEIGHT + _ROW_GAP)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        kind = index.data(_KIND)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "header":
            self._paint_header(painter, option, index)
        elif kind == "hint":
            self._paint_hint(painter, option, index)
        else:
            self._paint_row(painter, option, index, kind)
        painter.restore()

    # ------------------------------------------------------------------

    @staticmethod
    def _small_font(option: QStyleOptionViewItem) -> QFont:
        font = QFont(option.font)
        font.setPixelSize(_tk.FONT_PX_SMALL)
        return font

    def _paint_header(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        band = _band(option.rect, "header")
        font = self._small_font(option)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(qcolor(_tk.color("text3")))
        text_rect = QRect(
            _HEADER_X, band.top(), band.width() - _HEADER_X - 48, ROW_HEIGHT
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            index.data(Qt.ItemDataRole.DisplayRole),
        )
        if index.data(_VALUE) == "folders":
            plus = _plus_rect(option.rect)
            hovered = self._tree.plus_hovered
            if hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(qcolor(_tk.color("hover"))))
                painter.drawRoundedRect(QRectF(plus), _tk.RADIUS_SM, _tk.RADIUS_SM)
            glyph = QRect(0, 0, 16, 16)
            glyph.moveCenter(plus.center())
            icons.icon("plus", "text" if hovered else "text3", 16).paint(painter, glyph)

    def _paint_hint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        rect = option.rect
        painter.setFont(self._small_font(option))
        painter.setPen(qcolor(_tk.color("text3")))
        painter.drawText(
            QRect(
                _HEADER_X,
                rect.top() + 4,
                rect.width() - _HEADER_X - _COUNT_RIGHT,
                rect.height() - 8,
            ),
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap,
            index.data(Qt.ItemDataRole.DisplayRole),
        )

    def _paint_row(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
        kind: str,
    ) -> None:
        band = _band(option.rect, kind)
        centre_y = band.center().y()
        active = bool(index.data(_ACTIVE))
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)

        background = QRectF(band).adjusted(_INSET, 0, -_INSET, 0)
        if active or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QBrush(qcolor(_tk.color("sel_soft" if active else "hover")))
            )
            painter.drawRoundedRect(background, _tk.RADIUS, _tk.RADIUS)
        if focused and self._tree.hasFocus():
            painter.setPen(QPen(qcolor(_tk.color("accent")), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                background.adjusted(0.5, 0.5, -0.5, -0.5), _tk.RADIUS, _tk.RADIUS
            )

        icon_key = "accent_text" if active else "text2"
        text_key = "text3" if kind == "more" else "text"
        text_x = _TEXT_X
        if kind == "view":
            glyph = QRect(_ICON_X, centre_y - 8, 16, 16)
            icons.icon(_VIEW_ICONS[index.data(_VALUE)], icon_key, 16).paint(
                painter, glyph
            )
        elif kind == "folder":
            shift = (index.data(_DEPTH) or 0) * _DEPTH_STEP
            if index.model().rowCount(index) > 0:
                chevron = (
                    "chevron_down" if self._tree.isExpanded(index) else "chevron_right"
                )
                icons.icon(chevron, "text3", 14).paint(
                    painter, QRect(_CHEVRON_X + shift, centre_y - 7, 14, 14)
                )
            icons.icon("folder", icon_key, 16).paint(
                painter, QRect(_FOLDER_ICON_X + shift, centre_y - 8, 16, 16)
            )
            text_x = _FOLDER_TEXT_X + shift
        elif kind == "tag":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(qcolor(_tk.tag_color(str(index.data(_VALUE))))))
            dot = QRectF(0, 0, _TAG_DOT, _TAG_DOT)
            dot.moveCenter(QRectF(_ICON_X, band.top(), 16, ROW_HEIGHT).center())
            painter.drawEllipse(dot)

        right = band.right() - _COUNT_RIGHT + 1
        count = index.data(_COUNT)
        if count is not None:
            small = self._small_font(option)
            count_text = str(count)
            width = QFontMetrics(small).horizontalAdvance(count_text)
            painter.setFont(small)
            painter.setPen(qcolor(_tk.color("text3")))
            painter.drawText(
                QRect(right - width, band.top(), width, ROW_HEIGHT),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                count_text,
            )
            right -= width + 8

        font = QFont(option.font)
        if active:
            font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        favorite = bool(index.data(_FAVORITE))
        star_room = 18 if favorite else 0
        text = metrics.elidedText(
            index.data(Qt.ItemDataRole.DisplayRole),
            Qt.TextElideMode.ElideRight,
            max(0, right - text_x - star_room),
        )
        painter.setFont(font)
        painter.setPen(qcolor(_tk.color(text_key)))
        painter.drawText(
            QRect(text_x, band.top(), max(0, right - text_x), ROW_HEIGHT),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        if favorite:
            star_x = text_x + metrics.horizontalAdvance(text) + 6
            icons.icon("star_filled", "star", 12).paint(
                painter, QRect(star_x, centre_y - 6, 12, 12)
            )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


class SidebarWidget(QWidget):
    """Left panel: All queries / Favorites / Recent, Folders and Tags.

    Signals:
        open_folder_requested          — the + on the Folders header.
        folder_selected(Path)          — a library folder or one of its subfolders.
        folder_remove_requested(Path)  — Remove, from a library folder's menu.
        folder_deindex_requested(Path) — Deindex, from the same menu.
        folder_favorite_toggled(Path)  — Favorite/Unfavorite, from the same menu.
        tag_selected(str)              — a tag; "" means All queries.
        favorites_selected             — Favorites.
        recent_selected                — Recent.
    """

    open_folder_requested = Signal()
    folder_selected = Signal(object)  # Path
    folder_remove_requested = Signal(object)  # Path
    folder_deindex_requested = Signal(object)  # Path
    folder_favorite_toggled = Signal(object)  # Path
    tag_selected = Signal(str)
    favorites_selected = Signal()
    recent_selected = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setObjectName("SidebarWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._entries: list[tuple[Path, bool]] = []
        self._summary: LibrarySummary | None = None
        self._trees: dict[Path, FolderNode] = {}
        self._state = NavState()
        self._expanded: set[Path] = set()
        self._tags_expanded = False
        self._items: dict[tuple[str, object], QTreeWidgetItem] = {}
        self._tag_items: list[QTreeWidgetItem] = []
        self._more_item: QTreeWidgetItem | None = None
        self._active_item: QTreeWidgetItem | None = None

        self._tree = _SidebarTree(self)
        self._tree.setItemDelegate(_SidebarDelegate(self._tree))
        self._tree.row_clicked.connect(self._on_row_clicked)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemCollapsed.connect(self._on_item_collapsed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)
        layout.addWidget(self._tree)

        self._rebuild()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_folders(self, entries: list[tuple[Path, bool]]) -> None:
        """The library folders, as (path, is_favorited), in display order."""
        self._entries = list(entries)
        self._rebuild()

    def set_summary(self, summary: LibrarySummary | None) -> None:
        """Counts, tags and subfolders; None while there is no library.

        A summary gathered without trees keeps the subfolders already shown.
        """
        if summary is None:
            trees: dict[Path, FolderNode] = {}
        elif summary.trees is None:
            trees = self._trees
        else:
            trees = summary.trees
        if (
            summary is not None
            and self._summary is not None
            and summary.total == self._summary.total
            and summary.favorites == self._summary.favorites
            and summary.recent == self._summary.recent
            and summary.tags == self._summary.tags
            and trees == self._trees
        ):
            return
        self._summary = summary
        self._trees = trees
        self._rebuild()

    def show_state(self, state: NavState) -> None:
        """Mark the row for *state* as active, without emitting any signal.

        Called after every navigation, back/forward included, so the sidebar
        always agrees with the address bar.
        """
        self._state = state
        self._apply_active()
        self._tree.viewport().update()

    def retranslate_ui(self) -> None:
        self._rebuild()

    def refresh_theme(self) -> None:
        # The delegate reads the palette while painting.
        self._tree.viewport().update()

    # ------------------------------------------------------------------
    # Building the tree
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        tree = self._tree
        scroll = tree.verticalScrollBar().value()
        tree.setUpdatesEnabled(False)
        tree.blockSignals(True)
        tree.clear()
        self._items.clear()
        self._tag_items = []
        self._more_item = None
        self._active_item = None

        summary = self._summary
        counts = {
            "all": summary.total if summary else None,
            "favorites": summary.favorites if summary else None,
            "recent": summary.recent if summary else None,
        }
        for key, label, _icon in _VIEWS:
            self._add_item(None, "view", key, tr(label), counts[key])

        self._add_item(None, "header", "folders", tr("sidebar.folders_section"))
        if not self._entries:
            self._add_item(None, "hint", None, tr("sidebar.no_folders"))
        for path, favorited in self._entries:
            node = self._tree_for(path)
            root = node.path if node is not None else path
            item = self._add_item(
                None,
                "folder",
                root,
                _folder_label(root),
                node.count if node is not None else None,
            )
            item.setData(0, _FAVORITE, favorited)
            item.setToolTip(0, str(root))
            if node is not None:
                self._add_subfolders(item, node, 1)

        tags = summary.tags if summary else []
        if tags:
            self._add_item(None, "header", "tags", tr("sidebar.tags_section"))
            for name, count in tags:
                self._tag_items.append(self._add_item(None, "tag", name, name, count))
            if len(tags) > _TOP_TAGS:
                self._more_item = self._add_item(None, "more", None, "")
            self._apply_tag_visibility()

        for (kind, value), item in self._items.items():
            if kind == "folder" and value in self._expanded:
                item.setExpanded(True)
        tree.blockSignals(False)
        self._apply_active()
        tree.setUpdatesEnabled(True)
        tree.verticalScrollBar().setValue(scroll)

    def _tree_for(self, path: Path) -> FolderNode | None:
        node = self._trees.get(path)
        if node is not None:
            return node
        for root, candidate in self._trees.items():
            if _same_folder(root, path):
                return candidate
        return None

    def _add_subfolders(
        self, parent: QTreeWidgetItem, node: FolderNode, depth: int
    ) -> None:
        for child in node.children:
            item = self._add_item(
                parent, "folder", child.path, child.name, child.count, depth
            )
            item.setToolTip(0, str(child.path))
            self._add_subfolders(item, child, depth + 1)

    def _add_item(
        self,
        parent: QTreeWidgetItem | None,
        kind: str,
        value: object,
        text: str,
        count: int | None = None,
        depth: int = 0,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, _KIND, kind)
        item.setData(0, _VALUE, value)
        item.setData(0, _COUNT, count)
        item.setData(0, _DEPTH, depth)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled if kind in _CLICKABLE else Qt.ItemFlag.NoItemFlags
        )
        if parent is None:
            self._tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        if kind in ("view", "folder", "tag"):
            self._items[(kind, value)] = item
        return item

    def _apply_tag_visibility(self) -> None:
        for item in self._tag_items[_TOP_TAGS:]:
            item.setHidden(not self._tags_expanded)
        if self._more_item is not None:
            self._more_item.setText(
                0,
                (
                    tr("sidebar.fewer_tags")
                    if self._tags_expanded
                    else tr("sidebar.all_tags", count=len(self._tag_items))
                ),
            )

    def _apply_active(self) -> None:
        if self._active_item is not None:
            self._active_item.setData(0, _ACTIVE, False)
        key = _active_key(self._state)
        item = self._items.get(key) if key is not None else None
        if item is None and key is not None and key[0] == "folder":
            # A library folder may be spelled differently in the config
            # (resolved) and in the window (as opened); only roots can differ.
            folder = key[1]
            for (kind, value), candidate in self._items.items():
                if (
                    kind == "folder"
                    and candidate.parent() is None
                    and isinstance(value, Path)
                    and isinstance(folder, Path)
                    and _same_folder(value, folder)
                ):
                    item = candidate
                    break
        self._active_item = item
        if item is None:
            return
        item.setData(0, _ACTIVE, True)
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        if item.isHidden() and item in self._tag_items:
            self._tags_expanded = True
            self._apply_tag_visibility()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_row_clicked(self, item: QTreeWidgetItem, pos: QPoint) -> None:
        kind = item.data(0, _KIND)
        value = item.data(0, _VALUE)
        row = self._tree.visualItemRect(item)
        if kind == "view":
            if value == "favorites":
                self.favorites_selected.emit()
            elif value == "recent":
                self.recent_selected.emit()
            else:
                self.tag_selected.emit("")
        elif kind == "header":
            if value == "folders" and _plus_rect(row).contains(pos):
                self.open_folder_requested.emit()
        elif kind == "folder":
            if item.childCount():
                depth = item.data(0, _DEPTH) or 0
                if _chevron_rect(row, depth).contains(pos):
                    item.setExpanded(not item.isExpanded())
                    return
                item.setExpanded(True)
            self.folder_selected.emit(value)
        elif kind == "tag":
            self.tag_selected.emit(value)
        elif kind == "more":
            self._tags_expanded = not self._tags_expanded
            self._apply_tag_visibility()

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _KIND) == "folder":
            self._expanded.add(item.data(0, _VALUE))

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _KIND) == "folder":
            self._expanded.discard(item.data(0, _VALUE))

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self._tree.itemAt(pos)
        if item is None or item.data(0, _KIND) != "folder" or item.data(0, _DEPTH):
            return  # only library folders have a menu
        path: Path = item.data(0, _VALUE)
        is_fav = bool(item.data(0, _FAVORITE))

        menu = QMenu(self)
        open_act = menu.addAction(tr("sidebar.ctx_open", name=_folder_label(path)))
        menu.addSeparator()
        fav_act = menu.addAction(
            tr("sidebar.ctx_unfavorite") if is_fav else tr("sidebar.ctx_favorite")
        )
        menu.addSeparator()
        remove_act = menu.addAction(tr("sidebar.ctx_remove"))
        deindex_act = menu.addAction(tr("sidebar.ctx_deindex"))

        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen == open_act:
            self.folder_selected.emit(path)
        elif chosen == fav_act:
            self.folder_favorite_toggled.emit(path)
        elif chosen == remove_act:
            self.folder_remove_requested.emit(path)
        elif chosen == deindex_act:
            self.folder_deindex_requested.emit(path)
