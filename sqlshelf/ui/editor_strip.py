"""The thin bar above the SQL editor: the SQL | Diagram tabs and what goes
with the active one.

On the SQL tab it shows the file's line count, encoding and dialect and,
while the metadata is edited, that the SQL is editable and where the cursor
is ("Ln 10, Col 27"). On the Diagram tab it shows what the diagram counts
("4 tables · 3 joins · 2 filters"), the statement drawn when the text has
several, and the fit, zoom and ⋯ controls.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
)

from ..core.dialects import Resolved
from ..core.i18n import ntr, tr
from ..core.query_graph import StatementInfo
from . import icons
from .diagram_view import (
    DiagramCounts,
    add_export_actions,
    diagram_info,
    retranslate_exports,
    statement_label,
)
from .widgets import BarSeparator, IconButton

STRIP_HEIGHT = 32
TABS = ("sql", "diagram")
_CONTROL = 24  # height of the diagram controls
_MENU_TEXT = 220  # widest text on a menu button, then "…"


def strip_info(lines: int, encoding: str, dialect: str) -> str:
    """ "12 lines · UTF-8 · T-SQL", leaving the encoding out when unknown."""
    parts = [ntr("editor.lines_one", "editor.lines_other", lines)]
    if encoding:
        parts.append(encoding)
    parts.append(dialect)
    return " · ".join(parts)


def dialect_tooltip(resolved: Resolved) -> str:
    """Where the dialect a query is read as comes from."""
    label = resolved.dialect.label
    if resolved.source == "frontmatter":
        return tr("dialect.declared", dialect=label)
    if resolved.source == "detected":
        evidence = ", ".join(resolved.evidence)
        return tr("dialect.detected", dialect=label, evidence=evidence)
    return tr("dialect.default")


def _tab_icon(name: str) -> QIcon:
    """*name* in ``text2``, and in ``text`` on the active tab."""
    result = QIcon()
    for state, key in ((QIcon.State.Off, "text2"), (QIcon.State.On, "text")):
        result.addPixmap(icons.pixmap(name, key, 16), QIcon.Mode.Normal, state)
    return result


class MenuButton(QToolButton):
    """An optional icon, a text and a chevron that open a menu, the text cut
    to ``_MENU_TEXT``.

    The text is a child label: a tool button lays its own text out right to
    left when the menu arrow is on, which reorders "2 · SELECT"."""

    def __init__(
        self, icon_name: str = "", height: int = _CONTROL, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StripButton")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        self._height = height
        self._text = ""
        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        self._icon.setVisible(bool(icon_name))
        self._label = QLabel()
        self._chevron = QLabel()
        self._chevron.setFixedSize(14, 14)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 6, 0)
        layout.setSpacing(4)
        for child in (self._icon, self._label, self._chevron):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout.addWidget(child)
        self.refresh_theme()

    def set_label(self, text: str) -> None:
        self._text = text
        self._label.ensurePolished()
        metrics = self._label.fontMetrics()
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, _MENU_TEXT)
        self._label.setText(elided)
        self.updateGeometry()

    def label(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        layout = self.layout()
        width = layout.sizeHint().width() if layout is not None else 0
        return QSize(width, self._height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def refresh_theme(self) -> None:
        if self._icon_name:
            self._icon.setPixmap(icons.pixmap(self._icon_name, "text2", 16))
        self._chevron.setPixmap(icons.pixmap("chevron_down", "text2", 14))


class StatementSelector(MenuButton):
    """ "All (3)", "1 · SELECT … INTO #x", …: the statement a diagram draws.

    *all_key* names the first entry ("All ({count})" on the strip, longer in
    the diagram window)."""

    statement_changed = Signal(int)  # 0: every statement

    def __init__(
        self,
        all_key: str = "diagram.statements_all",
        height: int = _CONTROL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(height=height, parent=parent)
        self._all_key = all_key
        self._statements: list[StatementInfo] = []
        self._current = 0
        self._menu = QMenu(self)
        self.setMenu(self._menu)

    def set_statements(self, statements: list[StatementInfo], current: int) -> None:
        self._statements = list(statements)
        self._current = current
        menu = self._menu
        menu.clear()
        entries = [(0, tr(self._all_key, count=len(statements)), True)]
        entries += [
            (s.index, statement_label(s), s.status != "skipped") for s in statements
        ]
        for index, text, enabled in entries:
            action = menu.addAction(text.replace("&", "&&"))
            action.setData(index)
            action.setCheckable(True)
            action.setChecked(index == current)
            action.setEnabled(enabled)
            action.triggered.connect(lambda _=False, i=index: self._pick(i))
            if index == 0 and statements:
                menu.addSeparator()
        self._update_label()

    def statements(self) -> list[StatementInfo]:
        return self._statements

    def current(self) -> int:
        return self._current

    def statement_menu(self) -> QMenu:
        return self._menu

    def _pick(self, index: int) -> None:
        for action in self._menu.actions():
            action.setChecked(action.data() == index)
        if index == self._current:
            return
        self._current = index
        self._update_label()
        self.statement_changed.emit(index)

    def _update_label(self) -> None:
        info = next((s for s in self._statements if s.index == self._current), None)
        if info is None:
            text = tr(self._all_key, count=len(self._statements))
        else:
            text = statement_label(info)
        self.set_label(text)

    def retranslate_ui(self) -> None:
        self.setToolTip(tr("editor.statement_tooltip"))
        self.set_statements(self._statements, self._current)


class EditorStrip(QWidget):
    """Tabs, file facts and diagram controls; empty until a query loads."""

    tab_changed = Signal(str)  # only when the user picks a tab
    fit_requested = Signal()
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()
    zoom_reset_requested = Signal()
    statement_changed = Signal(int)  # 0: every statement
    rearrange_requested = Signal()
    results_toggled = Signal(bool)
    export_requested = Signal(str)  # "png", "svg" or "copy"
    expand_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditorStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(STRIP_HEIGHT)
        self._tab = "sql"
        self._lines: int | None = None
        self._encoding = ""
        self._dialect: Resolved | None = None
        self._editing = False
        self._cursor_pos: tuple[int, int] | None = None
        self._counts: DiagramCounts | None = None
        self._stale = False

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
        self._stale_label = QLabel()
        self._stale_label.setObjectName("EditorStripStale")

        self._statement_button = StatementSelector()
        self._statement_button.statement_changed.connect(self.statement_changed)

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
        self._more = IconButton("more", size=_CONTROL)
        self._more_menu = QMenu(self._more)
        self._rearrange = self._more_menu.addAction("")
        self._rearrange.triggered.connect(lambda: self.rearrange_requested.emit())
        self._results = self._more_menu.addAction("")
        self._results.setCheckable(True)
        self._results.setChecked(True)
        self._results.triggered.connect(self.results_toggled)
        self._more_menu.addSeparator()
        self._exports = add_export_actions(self._more_menu, self.export_requested.emit)
        self._more.set_menu(self._more_menu)
        self._expand = IconButton("expand", size=_CONTROL)
        self._expand.clicked.connect(self.expand_requested)
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
        controls.addSpacing(4)
        controls.addWidget(self._more)
        controls.addWidget(self._expand)

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
        layout.addWidget(self._stale_label)
        layout.addWidget(self._mode)
        layout.addStretch(1)
        layout.addWidget(self._cursor)
        layout.addWidget(self._statement_button)
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
        self._stale_label.setVisible(not sql and self._stale)
        many = len(self._statement_button.statements()) > 1
        self._statement_button.setVisible(not sql and many)
        self._controls.setVisible(not sql)

    # -- SQL tab --------------------------------------------------------------

    def set_info(self, lines: int, encoding: str, dialect: Resolved) -> None:
        self._lines = lines
        self._encoding = encoding
        self._dialect = dialect
        self._render_info()

    def set_lines(self, lines: int) -> None:
        """The line count changed while editing; the encoding and the dialect
        stay as loaded."""
        self._lines = lines
        self._render_info()

    def _render_info(self) -> None:
        if self._lines is None or self._dialect is None:
            return
        label = self._dialect.dialect.label
        self._info.setText(strip_info(self._lines, self._encoding, label))
        self._info.setToolTip(dialect_tooltip(self._dialect))

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
        self._dialect = None
        self._info.clear()
        self._info.setToolTip("")
        self.set_counts(None)
        self.set_stale(False)
        self.set_statements([], 0)
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
        for action in self._exports.values():
            action.setEnabled(counts is not None)

    def counts_text(self) -> str:
        return self._counts_label.text()

    def set_zoom(self, percent: int) -> None:
        self._zoom.setText(f"{percent}%")

    def zoom_text(self) -> str:
        return self._zoom.text()

    def set_stale(self, stale: bool) -> None:
        """ "outdated": the edited text reads worse than what is drawn."""
        self._stale = stale
        self._update_visibility()

    def is_stale(self) -> bool:
        return self._stale

    def set_statements(self, statements: list[StatementInfo], current: int) -> None:
        """The statements to pick from; shown only when there are two or more."""
        self._statement_button.set_statements(statements, current)
        self._update_visibility()

    def current_statement(self) -> int:
        return self._statement_button.current()

    def statement_menu(self) -> QMenu:
        return self._statement_button.statement_menu()

    def statement_text(self) -> str:
        return self._statement_button.label()

    def more_menu(self) -> QMenu:
        return self._more_menu

    def set_show_results(self, show: bool) -> None:
        self._results.setChecked(show)

    def more_actions(self) -> dict[str, QAction]:
        return {"rearrange": self._rearrange, "results": self._results, **self._exports}

    # -- theme and language ---------------------------------------------------

    def refresh_theme(self) -> None:
        for button in (self._sql_tab, self._diagram_tab):
            button.setIcon(_tab_icon(button.property("icon_name")))
        self._fit.setIcon(icons.icon("fit", "text2", 14))
        self._zoom_out.refresh_theme()
        self._zoom_in.refresh_theme()
        self._more.refresh_theme()
        self._expand.refresh_theme()
        self._statement_button.refresh_theme()

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
        self._stale_label.setText(tr("diagram.stale"))
        self._stale_label.setToolTip(tr("diagram.stale_tooltip"))
        self._statement_button.retranslate_ui()
        self._more.set_tooltip(tr("editor.more_options"))
        self._expand.set_tooltip(tr("editor.expand"))
        self._rearrange.setText(tr("diagram.rearrange"))
        self._results.setText(tr("diagram.show_result"))
        retranslate_exports(self._exports)
        self._render_info()
        if self._cursor_pos is not None:
            self.set_cursor(*self._cursor_pos)
        self.set_counts(self._counts)
