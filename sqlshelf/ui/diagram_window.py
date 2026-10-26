"""The query diagram in a window of its own: "Diagram — <file> — SQLShelf".

It follows the query selected in the main window, which reads the graph and
hands it to ``pane`` as it does to its Diagram tab. The bar repeats the
strip's controls with their names; a legend explains the lines, and the
status bar says what is drawn and what is selected.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.i18n import ntr, tr
from ..core.query_graph import StatementInfo
from . import icons
from . import tokens as _tk
from .diagram_view import (
    DOT_RADIUS,
    DiagramPane,
    add_export_actions,
    diagram_info,
    graph_counts,
    node_title,
    retranslate_exports,
)
from .editor_strip import StatementSelector
from .status_bar import BAR_HEIGHT, FLASH_MS
from .theme import mono_font, qcolor, ui_font
from .widgets import BAR_BUTTON_HEIGHT, BarButton, BarSeparator, IconButton

WINDOW_SIZE = QSize(1200, 780)
LEGEND_MARGIN = 16  # from the canvas's bottom left corner
NAME_MIN = 48  # narrowest the title and the path get, then "…"
LEGEND_KEYS = (
    "diagram.legend_join",
    "diagram.legend_optional",
    "diagram.legend_flow",
    "diagram.legend_key",
)


def statement_list(numbers: list[int]) -> str:
    """ "statement 2", "statements 1 and 3", "statements 1, 2 and 4"."""
    names = [str(n) for n in numbers]
    if len(names) == 1:
        return tr("diagram_window.in_statement", list=names[0])
    listed = f"{', '.join(names[:-1])} {tr('diagram_window.and')} {names[-1]}"
    return tr("diagram_window.in_statements", list=listed)


def _color(key: str) -> str:
    return _tk.color(key)


class _NameLabel(QLabel):
    """One line that asks for its whole text but gives way to the buttons,
    cut with an ellipsis; the tooltip has it all."""

    def __init__(self, object_name: str, mode: Qt.TextElideMode) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self._full = ""
        self._mode = mode

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.updateGeometry()
        self._refresh()

    def sizeHint(self) -> QSize:
        self.ensurePolished()
        margins = self.contentsMargins()
        width = self.fontMetrics().horizontalAdvance(self._full) + 2
        width += margins.left() + margins.right()
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        hint = self.sizeHint()
        return QSize(min(hint.width(), NAME_MIN), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        self.ensurePolished()
        # QLabel lays text out a pixel or two narrower than its contents rect.
        width = self.contentsRect().width() - 2
        shown = self.fontMetrics().elidedText(self._full, self._mode, width)
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class DiagramLegend(QWidget):
    """What the lines and the key icon mean, over the canvas's bottom left
    corner. Clicks go through to the canvas."""

    PAD = 12
    ROW = 22
    SAMPLE = 44  # a line's sample
    GAP = 10  # between a sample and its text

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("DiagramLegend")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        font = ui_font()
        font.setPixelSize(_tk.FONT_PX_SMALL)
        self.setFont(font)
        parent.installEventFilter(self)
        self.retranslate_ui()

    def texts(self) -> list[str]:
        return [tr(key) for key in LEGEND_KEYS]

    def retranslate_ui(self) -> None:
        metrics = QFontMetricsF(self.font())
        text = max(metrics.horizontalAdvance(t) for t in self.texts())
        width = 2 * self.PAD + self.SAMPLE + self.GAP + text
        height = 2 * (self.PAD - 4) + self.ROW * len(LEGEND_KEYS)
        self.resize(math.ceil(width), height)
        self._place()
        self.update()

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            y = parent.height() - self.height() - LEGEND_MARGIN
            self.move(LEGEND_MARGIN, max(y, 0))
            self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(qcolor(_color("border")), 1))
        painter.setBrush(qcolor(_color("panel")))
        painter.drawRoundedRect(box, _tk.RADIUS, _tk.RADIUS)
        left = float(self.PAD)
        right = left + self.SAMPLE
        for i, text in enumerate(self.texts()):
            y = self.PAD - 4 + self.ROW * i + self.ROW / 2
            if i < 2:
                self._paint_join(painter, left, right, y, hollow=i == 1)
            elif i == 2:
                self._paint_flow(painter, left, right, y)
            else:
                rect = QRectF(left + 4, y - 7, 14, 14)
                icons.paint(painter, "link", rect, _color("text3"))
            painter.setPen(qcolor(_color("text2")))
            painter.drawText(
                QRectF(right + self.GAP, y - self.ROW / 2, self.width(), self.ROW),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        painter.end()

    @staticmethod
    def _paint_join(
        painter: QPainter, left: float, right: float, y: float, hollow: bool
    ) -> None:
        color = qcolor(_color("dg_join"))
        painter.setPen(QPen(color, 1.5))
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(left, y), DOT_RADIUS, DOT_RADIUS)
        if hollow:
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(qcolor(_color("panel")))
            radius = DOT_RADIUS + 0.5
        else:
            radius = DOT_RADIUS
        painter.drawEllipse(QPointF(right, y), radius, radius)

    @staticmethod
    def _paint_flow(painter: QPainter, left: float, right: float, y: float) -> None:
        color = qcolor(_color("dg_flow"))
        pen = QPen(color, 1.5)
        pen.setDashPattern([4, 8 / 3])  # in pen widths, as a flow edge
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(left, y), QPointF(right - 8, y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        tip = QPointF(right, y)
        painter.drawPolygon(
            QPolygonF([tip, QPointF(right - 8, y - 4), QPointF(right - 8, y + 4)])
        )

    def refresh_theme(self) -> None:
        self.update()


class DiagramWindow(QWidget):
    """One window, never modal; the main window keeps it and shows it again.

    Esc clears the selection on the canvas, then closes the window. Where it
    was is saved when it closes."""

    go_to_sql_requested = Signal()
    export_requested = Signal(str)  # "png", "svg" or "copy"

    def __init__(self) -> None:
        # No parent: the main window can come in front of it.
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("DiagramWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # The main window may be in the tray; closing this one is not quitting.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._file = ""
        self._stale = False
        self._flash_text = ""

        self.pane = DiagramPane()
        view = self.pane.view
        self.legend = DiagramLegend(view)
        self.legend.setVisible(cfg.get_diagram_legend())

        self._tile = QLabel()
        self._tile.setObjectName("PanelFileIcon")
        self._tile.setFixedSize(32, 32)
        self._tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = _NameLabel("DiagramWindowTitle", Qt.TextElideMode.ElideRight)
        # The file name is at the end: cut the folders.
        self._path = _NameLabel("DiagramWindowPath", Qt.TextElideMode.ElideMiddle)
        self._path.setFont(mono_font(_tk.FONT_PX_SMALL))
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        names.addWidget(self._title)
        names.addWidget(self._path)

        self._selector = StatementSelector(
            "diagram_window.statements_all", height=BAR_BUTTON_HEIGHT
        )
        self._selector.setProperty("bordered", True)
        self._selector.statement_changed.connect(self.pane.set_statement)
        self._rearrange = BarButton("grid", "")
        self._rearrange.clicked.connect(self.pane.rearrange)
        self._fit = BarButton("fit", "")
        self._fit.clicked.connect(self.pane.fit)
        self._zoom_out = IconButton("minus")
        self._zoom_out.clicked.connect(self.pane.zoom_out)
        self._zoom = QToolButton()
        self._zoom.setObjectName("StripZoom")
        self._zoom.setFixedSize(52, BAR_BUTTON_HEIGHT)
        self._zoom.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zoom.clicked.connect(self.pane.reset_zoom)
        self._zoom_in = IconButton("plus")
        self._zoom_in.clicked.connect(self.pane.zoom_in)
        self._export_menu = QMenu(self)
        self._exports = add_export_actions(
            self._export_menu, self.export_requested.emit
        )
        self._export = BarButton("download", "", menu=self._export_menu)
        self._go = BarButton("code", "")
        self._go.clicked.connect(lambda: self.go_to_sql_requested.emit())
        self._more = IconButton("more")
        self._more_menu = QMenu(self._more)
        self._results = self._more_menu.addAction("")
        self._results.setCheckable(True)
        self._results.setChecked(True)
        self._results.triggered.connect(self.pane.set_show_results)
        self._legend_action = self._more_menu.addAction("")
        self._legend_action.setCheckable(True)
        self._legend_action.setChecked(not self.legend.isHidden())
        self._legend_action.triggered.connect(self.set_legend_visible)
        self._more.set_menu(self._more_menu)

        bar = QWidget()
        bar.setObjectName("DiagramWindowBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(4)
        row.addWidget(self._tile)
        row.addSpacing(8)
        row.addLayout(names)
        row.addSpacing(20)
        row.addWidget(self._selector)
        row.addStretch(1)
        groups: list[list[QWidget]] = [
            [self._rearrange, self._fit],
            [self._zoom_out, self._zoom, self._zoom_in],
            [self._export, self._go, self._more],
        ]
        for i, group in enumerate(groups):
            if i:
                row.addSpacing(6)
                row.addWidget(BarSeparator())
                row.addSpacing(6)
            for widget in group:
                row.addWidget(widget)

        self._status = QStatusBar()
        self._status.setObjectName("StatusBar")
        self._status.setFixedHeight(BAR_HEIGHT)
        self._status.setSizeGripEnabled(False)
        self._status.setContentsMargins(10, 0, 8, 0)
        self._counts = QLabel()
        self._counts.setObjectName("StatusText")
        self._stale_label = QLabel()
        self._stale_label.setObjectName("DiagramWindowStale")
        self._selection = QLabel()
        self._selection.setObjectName("StatusText")
        self._status.addWidget(self._counts)
        self._status.addWidget(self._stale_label)
        self._status.addPermanentWidget(self._selection)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        layout.addWidget(self.pane, 1)
        layout.addWidget(self._status)

        view.zoom_changed.connect(self._show_zoom)
        self.pane.displayed_changed.connect(lambda _graph: self._refresh_status())
        view.diagram().node_selected.connect(lambda _node: self._refresh_status())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.close)

        self.set_query("", [])
        self.set_statements([], 0)
        self._show_zoom(self.pane.zoom_percent())
        self.refresh_theme()
        self.retranslate_ui()
        self._restore_geometry()

    # -- what it shows --------------------------------------------------------

    def set_query(self, title: str, path: list[str]) -> None:
        """The query drawn: its *title* and its *path* ("Reports", "etl",
        "load.sql"), or nothing."""
        self._file = path[-1] if path else ""
        self._title.set_full_text(title)
        self._path.set_full_text(" › ".join(path))
        self._title.setVisible(bool(title))
        self._path.setVisible(bool(path))
        self._update_title()

    def _update_title(self) -> None:
        if self._file:
            self.setWindowTitle(tr("diagram_window.title", file=self._file))
        else:
            self.setWindowTitle(f"{tr('editor.tab_diagram')} — SQLShelf")

    def title_text(self) -> str:
        return self._title.full_text()

    def path_text(self) -> str:
        return self._path.full_text()

    def set_statements(self, statements: list[StatementInfo], current: int) -> None:
        """The statements to pick from; the selector shows only with two or
        more."""
        self._selector.set_statements(statements, current)
        self._selector.setVisible(len(statements) > 1)

    def statement_selector(self) -> StatementSelector:
        return self._selector

    def set_stale(self, stale: bool) -> None:
        """ "outdated": the edited text reads worse than what is drawn."""
        self._stale = stale
        self._refresh_status()

    def is_stale(self) -> bool:
        return self._stale

    def set_legend_visible(self, visible: bool) -> None:
        self.legend.setVisible(visible)
        self._legend_action.setChecked(visible)
        cfg.set_diagram_legend(visible)

    def more_actions(self) -> dict[str, QAction]:
        return {"results": self._results, "legend": self._legend_action}

    def export_actions(self) -> dict[str, QAction]:
        return self._exports

    def _show_zoom(self, percent: int) -> None:
        self._zoom.setText(f"{percent}%")

    def zoom_text(self) -> str:
        return self._zoom.text()

    # -- status bar -----------------------------------------------------------

    def flash(self, message: str, timeout: int = FLASH_MS) -> None:
        """Show *message* for a few seconds in place of the counts."""
        self._flash_text = message
        self._flash_timer.start(timeout)
        self._refresh_status()

    def _end_flash(self) -> None:
        self._flash_text = ""
        self._refresh_status()

    def counts_text(self) -> str:
        return self._counts.text()

    def selection_text(self) -> str:
        return self._selection.text()

    def _refresh_status(self) -> None:
        shown = self.pane.displayed()
        whole = self.pane.graph()
        parts = []
        if shown is not None:
            info = diagram_info(graph_counts(shown))
            if info:
                parts.append(info)
            if whole is not None and len(whole.statements) > 1:
                count = len(whole.statements)
                parts.append(
                    ntr(
                        "diagram_window.statements_one",
                        "diagram_window.statements_other",
                        count,
                    )
                )
        self._counts.setText(self._flash_text or " · ".join(parts))
        self._stale_label.setVisible(
            self._stale and shown is not None and not self._flash_text
        )
        self._selection.setText(self._selection_status())
        exportable = shown is not None
        for action in self._exports.values():
            action.setEnabled(exportable)

    def _selection_status(self) -> str:
        """ "#orders · 3 connections · statements 1 and 3"."""
        scene = self.pane.view.diagram()
        node_id = scene.selected
        if node_id is None or node_id not in scene.nodes:
            return ""
        node = scene.nodes[node_id].node
        parts = [node_title(node)]
        linked = len(scene.neighbours(node_id))
        parts.append(
            ntr(
                "diagram_window.connections_one",
                "diagram_window.connections_other",
                linked,
            )
        )
        whole = self.pane.graph()
        if whole is not None and len(whole.statements) > 1 and node.statements:
            parts.append(statement_list(sorted(node.statements)))
        return " · ".join(parts)

    # -- window ---------------------------------------------------------------

    def _restore_geometry(self) -> None:
        saved = cfg.get_diagram_window_geometry()
        if saved and self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
            return
        self.resize(WINDOW_SIZE)

    def save_geometry(self) -> None:
        geometry = bytes(self.saveGeometry().toBase64().data()).decode("ascii")
        cfg.set_diagram_window_geometry(geometry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_geometry()
        super().closeEvent(event)

    def refresh_theme(self) -> None:
        self._tile.setPixmap(icons.pixmap("diagram", "accent_text", 18))
        self._selector.refresh_theme()
        for button in (self._rearrange, self._fit, self._export, self._go):
            button.refresh_theme()
        for icon_button in (self._zoom_out, self._zoom_in, self._more):
            icon_button.refresh_theme()
        self.pane.refresh_theme()
        self.legend.refresh_theme()

    def retranslate_ui(self) -> None:
        self._update_title()
        self._selector.retranslate_ui()
        self._rearrange.set_text(tr("diagram.rearrange"))
        self._fit.set_text(tr("editor.fit"))
        self._fit.setToolTip(tr("editor.fit_tooltip"))
        self._zoom_out.set_tooltip(tr("editor.zoom_out"))
        self._zoom_in.set_tooltip(tr("editor.zoom_in"))
        self._zoom.setToolTip(tr("editor.zoom_reset"))
        self._export.set_text(tr("diagram.export"))
        self._go.set_text(tr("diagram.go_to_sql"))
        self._more.set_tooltip(tr("editor.more_options"))
        self._results.setText(tr("diagram.show_result"))
        self._legend_action.setText(tr("diagram_window.show_legend"))
        self._stale_label.setText(tr("diagram.stale"))
        self._stale_label.setToolTip(tr("diagram.stale_tooltip"))
        retranslate_exports(self._exports)
        self.pane.retranslate_ui()
        self.legend.retranslate_ui()
        self._refresh_status()
