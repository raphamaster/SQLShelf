"""Tests for ui/diagram_window.py: the diagram in a window of its own."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from test_sql_segments import SALES_BY_CUSTOMER, SYSTEM_HEALTH

from sqlshelf.core import config as cfg
from sqlshelf.core.i18n import set_language
from sqlshelf.core.query_graph import build_query_graph
from sqlshelf.ui.diagram_view import diagram_info, graph_counts, node_title
from sqlshelf.ui.diagram_window import (
    LEGEND_MARGIN,
    NAME_MIN,
    WINDOW_SIZE,
    DiagramWindow,
    statement_list,
)
from sqlshelf.ui.theme import apply_theme

SALES = build_query_graph(SALES_BY_CUSTOMER)
HEALTH = build_query_graph(SYSTEM_HEALTH)


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _dispose(widget: QWidget) -> None:
    # Delete it now: a menu left for deleteLater holds the last reference to
    # it through its actions' slots, and would take it down mid-destruction.
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cfg, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfg, "_CONFIG_FILE", config_dir / "config.json")
    set_language("en")
    yield
    set_language("en")


@pytest.fixture
def window() -> Iterator[DiagramWindow]:
    apply_theme(_app(), "light")
    widget = DiagramWindow()
    widget.show()
    yield widget
    _dispose(widget)


@pytest.mark.parametrize(
    ("lang", "numbers", "expected"),
    [
        ("en", [2], "statement 2"),
        ("en", [1, 3], "statements 1 and 3"),
        ("en", [1, 2, 4], "statements 1, 2 and 4"),
        ("pt-BR", [1, 3], "instruções 1 e 3"),
        ("es", [2], "instrucción 2"),
    ],
)
def test_statement_list(lang: str, numbers: list[int], expected: str) -> None:
    set_language(lang)
    assert statement_list(numbers) == expected


def test_the_window_names_the_query(window: DiagramWindow) -> None:
    assert window.windowTitle() == "Diagram — SQLShelf"
    assert window.title_text() == ""

    window.set_query("Sales by customer", ["library", "reports", "sales.sql"])
    assert window.windowTitle() == "Diagram — sales.sql — SQLShelf"
    assert window.title_text() == "Sales by customer"
    assert window.path_text() == "library › reports › sales.sql"

    set_language("pt-BR")
    window.retranslate_ui()
    assert window.windowTitle() == "Diagrama — sales.sql — SQLShelf"
    assert window._export.accessibleName() == "Exportar"
    assert window._go.accessibleName() == "Ir para o SQL"
    assert window.more_actions()["legend"].text() == "Mostrar legenda"


def test_a_long_name_gives_way_to_the_buttons(window: DiagramWindow) -> None:
    assert window.statement_selector().isHidden()
    window.set_query("Sales", ["sales.sql"])
    short = window.minimumSizeHint().width()
    title = "Customer lifetime value by region and quarter " * 4
    path = ["library", "reports", "etl", "customer_lifetime_value.sql"]
    window.set_query(title, path)
    assert window.minimumSizeHint().width() <= short + NAME_MIN

    window.resize(window.minimumSizeHint())
    QApplication.processEvents()
    assert window.title_text() == title
    assert window._title.text().endswith("…")
    assert window._title.toolTip() == title
    shown = window._path.text()
    assert "…" in shown and not shown.endswith("…")  # cut in the middle
    assert window._path.toolTip() == window.path_text()


def test_closing_it_is_not_quitting(window: DiagramWindow) -> None:
    assert not window.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)
    assert window.parent() is None


def test_the_status_bar_counts_what_is_drawn(window: DiagramWindow) -> None:
    assert window.counts_text() == ""
    window.pane.show_graph(SALES)
    assert len(SALES.statements) == 1
    assert window.counts_text() == diagram_info(graph_counts(SALES))

    window.pane.show_graph(HEALTH)
    count = len(HEALTH.statements)
    assert count > 1
    shown = window.pane.displayed()
    assert shown is not None
    assert window.counts_text() == (
        f"{diagram_info(graph_counts(shown))} · {count} statements"
    )

    window.pane.clear()
    assert window.counts_text() == ""


def test_the_status_bar_names_the_selection(window: DiagramWindow) -> None:
    window.pane.show_graph(SALES)
    scene = window.pane.view.diagram()
    assert window.selection_text() == ""

    scene.select("vendas.pedidos")
    title = node_title(scene.nodes["vendas.pedidos"].node)
    assert window.selection_text() == f"{title} · 3 connections"
    scene.select(None)
    assert window.selection_text() == ""

    window.pane.show_graph(HEALTH)
    scene = window.pane.view.diagram()
    node_id = "cte:2:cte_healthsession"
    scene.select(node_id)
    node = scene.nodes[node_id].node
    assert window.selection_text().endswith(
        f" · {statement_list(sorted(node.statements))}"
    )


def test_the_statement_selector_shows_with_two_or_more(window: DiagramWindow) -> None:
    window.pane.show_graph(HEALTH)
    window.set_statements(HEALTH.statements, 0)
    selector = window.statement_selector()
    assert not selector.isHidden()
    assert selector.label() == f"All statements ({len(HEALTH.statements)})"

    actions = [a for a in selector.statement_menu().actions() if not a.isSeparator()]
    actions[1].trigger()
    assert window.pane.statement() == 1

    window.set_statements(SALES.statements, 0)
    assert selector.isHidden()


def test_the_bar_drives_the_pane(window: DiagramWindow) -> None:
    window.pane.show_graph(SALES)
    view = window.pane.view
    view.set_zoom(1.0)
    assert window.zoom_text() == "100%"
    window._zoom_in.click()
    assert window.zoom_text() == "110%"
    window._zoom.click()
    assert window.zoom_text() == "100%"

    view.diagram().move_node("dbo.regiao", QPointF(30, 30))
    window._rearrange.click()
    assert not view.diagram().moved

    toggled = window.more_actions()["results"]
    assert toggled.isChecked()
    toggled.trigger()
    shown = window.pane.displayed()
    assert shown is not None
    assert all(node.kind != "result" for node in shown.nodes)

    asked: list[bool] = []
    window.go_to_sql_requested.connect(lambda: asked.append(True))
    window._go.click()
    assert asked == [True]


def test_exports_wait_for_a_diagram(window: DiagramWindow) -> None:
    actions = window.export_actions()
    assert [a.text() for a in actions.values()] == [
        "Export PNG…",
        "Export SVG…",
        "Copy image",
    ]
    assert not any(a.isEnabled() for a in actions.values())

    window.pane.show_graph(SALES)
    assert all(a.isEnabled() for a in actions.values())
    kinds: list[str] = []
    window.export_requested.connect(kinds.append)
    actions["png"].trigger()
    actions["copy"].trigger()
    assert kinds == ["png", "copy"]

    window.pane.clear()
    assert not actions["svg"].isEnabled()


def test_a_flash_shows_in_place_of_the_counts(window: DiagramWindow) -> None:
    window.pane.show_graph(SALES)
    counts = window.counts_text()
    window.set_stale(True)
    assert window.is_stale()
    assert not window._stale_label.isHidden()
    assert window._stale_label.text() == "outdated"

    window.flash("Diagram saved: sales.diagram.png", timeout=50)
    assert window.counts_text() == "Diagram saved: sales.diagram.png"
    assert window._stale_label.isHidden()
    _pump(200)
    assert window.counts_text() == counts
    assert not window._stale_label.isHidden()


def test_the_legend_sits_on_the_canvas_corner(window: DiagramWindow) -> None:
    window.resize(900, 600)
    QApplication.processEvents()
    legend = window.legend
    view = window.pane.view
    assert legend.parentWidget() is view
    assert legend.texts() == [
        "Join (INNER, LEFT, APPLY…)",
        "Optional side (outer join)",
        "Data flow (INTO, INSERT, UPDATE, MERGE)",
        "Column used in a join",
    ]
    assert legend.x() == LEGEND_MARGIN
    assert legend.y() + legend.height() == view.height() - LEGEND_MARGIN
    assert legend.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_legend_can_be_hidden_and_stays_hidden(window: DiagramWindow) -> None:
    action = window.more_actions()["legend"]
    assert action.isChecked()
    assert not window.legend.isHidden()
    action.trigger()
    assert window.legend.isHidden()
    assert not cfg.get_diagram_legend()

    second = DiagramWindow()
    try:
        assert second.legend.isHidden()
        assert not second.more_actions()["legend"].isChecked()
    finally:
        _dispose(second)


def test_the_window_opens_where_it_was(window: DiagramWindow) -> None:
    assert cfg.get_diagram_window_geometry() is None
    window.resize(window.width(), 560)
    window.close()
    assert cfg.get_diagram_window_geometry() is not None

    second = DiagramWindow()
    try:
        # The test screen is narrower than the window: only the height is
        # sure to come back as it was.
        assert second.height() == 560 != WINDOW_SIZE.height()
    finally:
        _dispose(second)


def test_a_first_window_takes_the_default_size() -> None:
    cfg.set_diagram_window_geometry("not a geometry")
    window = DiagramWindow()
    try:
        assert window.size() == WINDOW_SIZE
    finally:
        _dispose(window)


def test_escape_deselects_then_closes(window: DiagramWindow) -> None:
    window.activateWindow()
    assert QTest.qWaitForWindowActive(window)
    window.pane.show_graph(SALES)
    view = window.pane.view
    view.setFocus()
    view.diagram().select("vendas.pedidos")

    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.diagram().selected is None
    assert window.isVisible()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert not window.isVisible()
    assert cfg.get_diagram_window_geometry() is not None


def test_theme_and_language_keep_the_drawing(window: DiagramWindow) -> None:
    window.pane.show_graph(SALES)
    graph = window.pane.graph()
    apply_theme(_app(), "dark")
    try:
        window.refresh_theme()
        set_language("es")
        window.retranslate_ui()
        assert window.pane.graph() is graph
        assert window.export_actions()["copy"].text() == "Copiar imagen"
    finally:
        apply_theme(_app(), "light")
