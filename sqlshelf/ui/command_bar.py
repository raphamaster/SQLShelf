"""Command bar: new query, open folder, sort, group, quick search and view."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QMenu, QWidget

from ..core import config as cfg
from ..core.i18n import tr
from .widgets import BarButton, BarSeparator, IconButton

BAR_HEIGHT = 46

# Each sort key has a long label for the menu and a short one for the button.
_SORT_LABELS = {
    "name_asc": ("sort.name_asc", "sort.short_name_asc"),
    "name_desc": ("sort.name_desc", "sort.short_name_desc"),
    "mtime_desc": ("sort.mtime_desc", "sort.short_mtime_desc"),
    "mtime_asc": ("sort.mtime_asc", "sort.short_mtime_asc"),
}
_GROUP_LABELS = {
    "date": "toolbar.group_date",
    "folder": "toolbar.group_folder",
    "none": "toolbar.group_none",
}


class CommandBar(QWidget):
    """Row of commands under the address bar.

    The buttons report what was asked; the window performs it and calls the
    ``set_*`` methods back, so the bar never holds state of its own.
    """

    new_query_requested = Signal()
    open_folder_requested = Signal()
    quick_search_requested = Signal()
    sort_requested = Signal(str)
    group_requested = Signal(str)
    view_requested = Signal(str)
    panel_toggled = Signal(bool)

    def __init__(self, more_menu: QMenu, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CommandBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(BAR_HEIGHT)
        self._sort = cfg.get_sort_key()
        self._group = cfg.get_group_mode()

        self._new = BarButton("plus", "", primary=True)
        self._new.clicked.connect(self.new_query_requested)
        self._open = BarButton("folder_plus", "")
        self._open.clicked.connect(self.open_folder_requested)

        self._sort_menu = QMenu(self)
        self._sort_group = QActionGroup(self)
        self._sort_acts: dict[str, QAction] = {}
        for index, key in enumerate(_SORT_LABELS):
            if index == 2:
                self._sort_menu.addSeparator()
            act = self._sort_menu.addAction("")
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, k=key: self.sort_requested.emit(k))
            self._sort_group.addAction(act)
            self._sort_acts[key] = act
        self._sort_btn = BarButton("sort", "", value="", menu=self._sort_menu)

        self._group_menu = QMenu(self)
        self._group_group = QActionGroup(self)
        self._group_acts: dict[str, QAction] = {}
        for mode in _GROUP_LABELS:
            act = self._group_menu.addAction("")
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, m=mode: self.group_requested.emit(m))
            self._group_group.addAction(act)
            self._group_acts[mode] = act
        self._group_btn = BarButton("group", "", value="", menu=self._group_menu)

        self._quick = BarButton("zap", "", kbd="Ctrl P")
        self._quick.clicked.connect(self.quick_search_requested)

        self._details = IconButton("list", checkable=True)
        self._details.clicked.connect(lambda: self.view_requested.emit("details"))
        self._content = IconButton("rows", checkable=True)
        self._content.clicked.connect(lambda: self.view_requested.emit("content"))
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self._details)
        self._view_group.addButton(self._content)

        self._panel = IconButton("panel", checkable=True)
        self._panel.toggled.connect(self.panel_toggled)

        self._more = IconButton("more")
        self._more.set_menu(more_menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        layout.addWidget(self._new)
        layout.addSpacing(4)
        layout.addWidget(self._open)
        layout.addSpacing(6)
        layout.addWidget(BarSeparator())
        layout.addSpacing(6)
        layout.addWidget(self._sort_btn)
        layout.addWidget(self._group_btn)
        layout.addStretch(1)
        layout.addWidget(self._quick)
        layout.addSpacing(6)
        layout.addWidget(BarSeparator())
        layout.addSpacing(6)
        layout.addWidget(self._details)
        layout.addWidget(self._content)
        layout.addSpacing(6)
        layout.addWidget(BarSeparator())
        layout.addSpacing(6)
        layout.addWidget(self._panel)
        layout.addWidget(self._more)

        self.set_sort(self._sort)
        self.set_group(self._group)
        self.set_view(cfg.get_view_mode())
        self.retranslate_ui()

    # ------------------------------------------------------------------

    def set_sort(self, key: str) -> None:
        if key not in self._sort_acts:
            return
        self._sort = key
        self._sort_acts[key].setChecked(True)
        self._sort_btn.set_value(tr(_SORT_LABELS[key][1]))

    def set_group(self, mode: str) -> None:
        if mode not in self._group_acts:
            return
        self._group = mode
        self._group_acts[mode].setChecked(True)
        self._group_btn.set_value(tr(_GROUP_LABELS[mode]))

    def set_view(self, mode: str) -> None:
        button = self._content if mode == "content" else self._details
        button.setChecked(True)

    def set_panel_visible(self, visible: bool) -> None:
        self._panel.blockSignals(True)
        self._panel.setChecked(visible)
        self._panel.blockSignals(False)

    def retranslate_ui(self) -> None:
        self._new.set_text(tr("toolbar.new_query"))
        self._new.setToolTip(tr("toolbar.new_query_tip"))
        self._open.set_text(tr("toolbar.open_folder"))
        self._open.setToolTip(tr("toolbar.open_folder_tip"))
        self._sort_btn.set_text(tr("toolbar.sort"))
        for key, act in self._sort_acts.items():
            act.setText(tr(_SORT_LABELS[key][0]))
        self._group_btn.set_text(tr("toolbar.group"))
        for mode, act in self._group_acts.items():
            act.setText(tr(_GROUP_LABELS[mode]))
        self._quick.set_text(tr("toolbar.quick_search"))
        self._details.set_tooltip(tr("toolbar.view_details"))
        self._content.set_tooltip(tr("toolbar.view_content"))
        self._panel.set_tooltip(tr("toolbar.toggle_panel"))
        self._more.set_tooltip(tr("toolbar.more"))
        self.set_sort(self._sort)
        self.set_group(self._group)

    def refresh_theme(self) -> None:
        for button in (self._new, self._open, self._sort_btn, self._group_btn):
            button.refresh_theme()
        self._quick.refresh_theme()
        for icon_button in (self._details, self._content, self._panel, self._more):
            icon_button.refresh_theme()
