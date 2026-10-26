"""The thin bar above the SQL editor: the SQL | Diagram tabs and what goes
with the active one.

On the SQL tab it shows the file's line count and encoding and, while the
metadata is edited, that the SQL is editable and where the cursor is
("Ln 10, Col 27"). On the Diagram tab it shows what the diagram counts
("4 tables · 3 joins · 2 filters") and the fit and zoom controls.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from ..core.i18n import ntr, tr
from . import icons
from .diagram_view import DiagramCounts, diagram_info
from .widgets import BarSeparator, IconButton

STRIP_HEIGHT = 32
DIALECT = "T-SQL"
TABS = ("sql", "diagram")
_CONTROL = 24  # height of the diagram controls


def strip_info(lines: int, encoding: str) -> str:
    """ "12 lines · UTF-8 · T-SQL", leaving the encoding out when unknown."""
    parts = [ntr("editor.lines_one", "editor.lines_other", lines)]
    if encoding:
        parts.append(encoding)
    parts.append(DIALECT)
    return " · ".join(parts)


def _tab_icon(name: str) -> QIcon:
    """*name* in ``text2``, and in ``text`` on the active tab."""
    result = QIcon()
    for state, key in ((QIcon.State.Off, "text2"), (QIcon.State.On, "text")):
        result.addPixmap(icons.pixmap(name, key, 16), QIcon.Mode.Normal, state)
    return result


class EditorStrip(QWidget):
    """Tabs, file facts and diagram controls; empty until a query loads."""

    tab_changed = Signal(str)  # only when the user picks a tab
    fit_requested = Signal()
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()
    zoom_reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(STRIP_HEIGHT)
        self._tab = "sql"
        self._lines: int | None = None
        self._encoding = ""
        self._editing = False
        self._cursor_pos: tuple[int, int] | None = None
        self._counts: DiagramCounts | None = None

        self._sql_tab = self._make_tab("code")
        self._diagram_tab = self._make_tab("diagram")
        self._sql_tab.setChecked(True)
        self._tabs = QButtonGroup(self)
        self._tabs.setExclusive(True)
        for button in (self._sql_tab, self._diagram_tab):
            self._tabs.addButton(button)
        self._tabs.buttonClicked.connect(self._on_tab_clicked)

        self._info = QLabel()
        self._info.setObjectName("EditorStripInfo")
        self._counts_label = QLabel()
        self._counts_label.setObjectName("EditorStripInfo")
        self._mode = QLabel()
        self._mode.setObjectName("EditorStripMode")
        self._cursor = QLabel()
        self._cursor.setObjectName("EditorStripInfo")

        self._fit = QToolButton()
        self._fit.setObjectName("StripButton")
        self._fit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._fit.setIconSize(icons.icon_size(14))
        self._fit.setFixedHeight(_CONTROL)
        self._fit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fit.clicked.connect(self.fit_requested)
        self._zoom_out = IconButton("minus", size=_CONTROL)
        self._zoom_out.clicked.connect(self.zoom_out_requested)
        self._zoom = QToolButton()
        self._zoom.setObjectName("StripZoom")
        self._zoom.setFixedSize(48, _CONTROL)
        self._zoom.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zoom.clicked.connect(self.zoom_reset_requested)
        self._zoom_in = IconButton("plus", size=_CONTROL)
        self._zoom_in.clicked.connect(self.zoom_in_requested)
        self._controls = QWidget()
        controls = QHBoxLayout(self._controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        controls.addWidget(self._fit)
        controls.addSpacing(6)
        controls.addWidget(BarSeparator())
        controls.addSpacing(6)
        controls.addWidget(self._zoom_out)
        controls.addWidget(self._zoom)
        controls.addWidget(self._zoom_in)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(12)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        tabs.addWidget(self._sql_tab)
        tabs.addWidget(self._diagram_tab)
        layout.addLayout(tabs)
        layout.addWidget(self._info)
        layout.addWidget(self._counts_label)
        layout.addWidget(self._mode)
        layout.addStretch(1)
        layout.addWidget(self._cursor)
        layout.addWidget(self._controls)

        self.set_zoom(100)
        self.refresh_theme()
        self.retranslate_ui()
        self._update_visibility()

    def _make_tab(self, icon_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("EditorTab")
        button.setProperty("icon_name", icon_name)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(icons.icon_size(16))
        button.setFixedHeight(STRIP_HEIGHT)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    # -- tabs -----------------------------------------------------------------

    def tab(self) -> str:
        return self._tab

    def set_tab(self, tab: str) -> None:
        """Show *tab* ("sql" or "diagram") without emitting ``tab_changed``."""
        if tab not in TABS:
            return
        self._tab = tab
        button = self._sql_tab if tab == "sql" else self._diagram_tab
        button.setChecked(True)
        self._update_visibility()

    def toggle_tab(self) -> None:
        """Ctrl+Shift+D: the other tab, as if the user had clicked it."""
        self._pick("diagram" if self._tab == "sql" else "sql")

    def _on_tab_clicked(self, button: QToolButton) -> None:
        self._pick("sql" if button is self._sql_tab else "diagram")

    def _pick(self, tab: str) -> None:
        if tab == self._tab:
            return
        self.set_tab(tab)
        self.tab_changed.emit(tab)

    def _update_visibility(self) -> None:
        sql = self._tab == "sql"
        self._info.setVisible(sql)
        self._mode.setVisible(sql and self._editing)
        self._cursor.setVisible(sql and self._editing)
        self._counts_label.setVisible(not sql)
        self._controls.setVisible(not sql)

    # -- SQL tab --------------------------------------------------------------

    def set_info(self, lines: int, encoding: str) -> None:
        self._lines = lines
        self._encoding = encoding
        self._info.setText(strip_info(lines, encoding))

    def set_lines(self, lines: int) -> None:
        """The line count changed while editing; the encoding stays."""
        self.set_info(lines, self._encoding)

    def set_editing(self, editing: bool) -> None:
        """Show "editable" and the cursor position, or hide them again."""
        self._editing = editing
        if not editing:
            self._cursor_pos = None
            self._cursor.clear()
        self._update_visibility()

    def is_editing(self) -> bool:
        return self._editing

    def set_cursor(self, line: int, column: int) -> None:
        self._cursor_pos = (line, column)
        self._cursor.setText(tr("editor.cursor", line=line, col=column))

    def clear(self) -> None:
        self._lines = None
        self._encoding = ""
        self._info.clear()
        self.set_counts(None)
        self.set_editing(False)

    def info_text(self) -> str:
        return self._info.text()

    def mode_text(self) -> str:
        return self._mode.text() if self._editing else ""

    def cursor_text(self) -> str:
        return self._cursor.text() if self._editing else ""

    # -- Diagram tab ----------------------------------------------------------

    def set_counts(self, counts: DiagramCounts | None) -> None:
        self._counts = counts
        self._counts_label.setText(diagram_info(counts) if counts else "")

    def counts_text(self) -> str:
        return self._counts_label.text()

    def set_zoom(self, percent: int) -> None:
        self._zoom.setText(f"{percent}%")

    def zoom_text(self) -> str:
        return self._zoom.text()

    # -- theme and language ---------------------------------------------------

    def refresh_theme(self) -> None:
        for button in (self._sql_tab, self._diagram_tab):
            button.setIcon(_tab_icon(button.property("icon_name")))
        self._fit.setIcon(icons.icon("fit", "text2", 14))
        self._zoom_out.refresh_theme()
        self._zoom_in.refresh_theme()

    def retranslate_ui(self) -> None:
        self._sql_tab.setText(tr("editor.strip_title"))
        self._diagram_tab.setText(tr("editor.tab_diagram"))
        for button in (self._sql_tab, self._diagram_tab):
            button.setToolTip(tr("editor.tab_tooltip"))
        self._fit.setText(tr("editor.fit"))
        self._fit.setToolTip(tr("editor.fit_tooltip"))
        self._zoom_out.set_tooltip(tr("editor.zoom_out"))
        self._zoom_in.set_tooltip(tr("editor.zoom_in"))
        self._zoom.setToolTip(tr("editor.zoom_reset"))
        self._mode.setText(tr("editor.editable"))
        if self._lines is not None:
            self._info.setText(strip_info(self._lines, self._encoding))
        if self._cursor_pos is not None:
            self.set_cursor(*self._cursor_pos)
        self.set_counts(self._counts)
