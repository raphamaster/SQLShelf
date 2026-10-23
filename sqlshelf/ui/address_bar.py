"""Address bar: back / forward / up, the breadcrumb and the search field."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from ..core.i18n import tr
from ..core.navigation import NavState, chain
from . import icons
from .search_bar import FIELD_HEIGHT, SearchBar
from .widgets import IconButton

BAR_HEIGHT = 52
_CRUMB_MAX_WIDTH = 260


def location_label(state: NavState) -> str:
    """Short name of *state*, as the breadcrumb and the placeholder show it."""
    if state.kind == "all":
        return tr("nav.library")
    if state.kind == "favorites":
        return tr("nav.favorites")
    if state.kind == "recent":
        return tr("nav.recent")
    if state.kind == "tag":
        return f"#{state.value}"
    if state.kind == "search":
        return location_label(state.scope)
    folder = state.folder or Path()
    return folder.name or str(folder)


class AddressBar(QWidget):
    """Navigation row on top of the window.

    It only shows a navigation state and reports clicks; the window owns the
    :class:`NavHistory` and decides what each request does.
    """

    navigate_requested = Signal(object)  # NavState
    back_requested = Signal()
    forward_requested = Signal()
    up_requested = Signal()
    reindex_requested = Signal()

    def __init__(self, search_bar: SearchBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AddressBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(BAR_HEIGHT)
        self._state = NavState()
        self._roots: list[Path] = []

        self._back = IconButton("back")
        self._back.clicked.connect(self.back_requested)
        self._forward = IconButton("forward")
        self._forward.clicked.connect(self.forward_requested)
        self._up = IconButton("up")
        self._up.clicked.connect(self.up_requested)

        self._crumb_frame = QFrame()
        self._crumb_frame.setObjectName("Breadcrumb")
        self._crumb_frame.setFixedHeight(FIELD_HEIGHT)
        frame_layout = QHBoxLayout(self._crumb_frame)
        frame_layout.setContentsMargins(10, 0, 4, 0)
        frame_layout.setSpacing(2)
        self._library_icon = QLabel()
        self._library_icon.setFixedSize(16, 16)
        frame_layout.addWidget(self._library_icon)
        frame_layout.addSpacing(4)
        self._crumbs = QHBoxLayout()
        self._crumbs.setContentsMargins(0, 0, 0, 0)
        self._crumbs.setSpacing(0)
        frame_layout.addLayout(self._crumbs)
        frame_layout.addStretch(1)
        self._reindex = IconButton("refresh", size=28)
        self._reindex.clicked.connect(self.reindex_requested)
        frame_layout.addWidget(self._reindex)

        self._search_bar = search_bar

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        layout.addWidget(self._back)
        layout.addWidget(self._forward)
        layout.addWidget(self._up)
        layout.addSpacing(8)
        layout.addWidget(self._crumb_frame, 1)
        layout.addSpacing(8)
        layout.addWidget(search_bar)

        self.retranslate_ui()
        self.refresh_theme()

    # ------------------------------------------------------------------

    def set_state(
        self,
        state: NavState,
        roots: Iterable[Path],
        *,
        can_back: bool,
        can_forward: bool,
    ) -> None:
        """Show *state*: rebuild the breadcrumb and enable the arrows."""
        self._state = state
        self._roots = list(roots)
        self._back.setEnabled(can_back)
        self._forward.setEnabled(can_forward)
        self._up.setEnabled(state.kind != "all")
        self._search_bar.set_active(state.kind in ("search", "tag"))
        self._search_bar.set_location(location_label(state.scope))
        self._rebuild_crumbs()

    def retranslate_ui(self) -> None:
        self._back.set_tooltip(tr("nav.back"))
        self._forward.set_tooltip(tr("nav.forward"))
        self._up.set_tooltip(tr("nav.up"))
        self._reindex.set_tooltip(tr("nav.reindex"))
        self._search_bar.retranslate_ui()
        self.set_state(
            self._state,
            self._roots,
            can_back=self._back.isEnabled(),
            can_forward=self._forward.isEnabled(),
        )

    def refresh_theme(self) -> None:
        for button in (self._back, self._forward, self._up, self._reindex):
            button.refresh_theme()
        self._library_icon.setPixmap(icons.pixmap("library", "text2"))
        self._search_bar.refresh_theme()
        self._rebuild_crumbs()

    # ------------------------------------------------------------------

    def _clear_crumbs(self) -> None:
        while self._crumbs.count():
            item = self._crumbs.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _add_crumb(self, state: NavState, current: bool) -> None:
        button = QToolButton()
        button.setObjectName("Crumb")
        button.setProperty("current", current)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoRaise(True)
        label = location_label(state)
        elided = button.fontMetrics().elidedText(
            label, Qt.TextElideMode.ElideMiddle, _CRUMB_MAX_WIDTH
        )
        button.setText(elided)
        if elided != label:
            button.setToolTip(label)
        button.clicked.connect(lambda: self.navigate_requested.emit(state))
        self._crumbs.addWidget(button)

    def _add_separator(self) -> None:
        sep = QLabel()
        sep.setObjectName("CrumbSeparator")
        sep.setPixmap(icons.pixmap("chevron_right", "text3", 14))
        sep.setContentsMargins(2, 0, 2, 0)
        self._crumbs.addWidget(sep)

    def _rebuild_crumbs(self) -> None:
        self._clear_crumbs()
        if self._state.kind == "search":
            # One segment, as in a file manager: "Search results in <scope>".
            prefix = QLabel(tr("nav.search_results_in"))
            prefix.setObjectName("CrumbPrefix")
            prefix.setContentsMargins(0, 0, 2, 0)
            self._crumbs.addWidget(prefix)
            self._add_crumb(self._state.scope, current=True)
            return
        states = chain(self._state, self._roots)
        for index, state in enumerate(states):
            if index:
                self._add_separator()
            self._add_crumb(state, current=index == len(states) - 1)
