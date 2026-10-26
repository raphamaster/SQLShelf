"""Tests for ui/diagram_view.py: what a node shows, the scene and the pane."""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from test_query_graph import FILTERS
from test_sql_segments import LTV_ETL, SALES_BY_CUSTOMER, SYSTEM_HEALTH

from sqlshelf.core.i18n import set_language
from sqlshelf.core.query_graph import (
    GraphColumn,
    GraphNode,
    QueryGraph,
    Span,
    build_query_graph,
)
from sqlshelf.ui import tokens as _tk
from sqlshelf.ui.diagram_view import (
    ANALYZING_DELAY_MS,
    HEADER_HEIGHT,
    MAX_ROWS,
    MAX_WIDTH,
    MIN_WIDTH,
    ROW_HEIGHT,
    ZOOM_MAX,
    ZOOM_MIN,
    DiagramCounts,
    DiagramPane,
    DiagramScene,
    DiagramView,
    GraphCache,
    column_detail,
    diagram_info,
    filter_count,
    graph_counts,
    main_role,
    measure,
    node_alias,
    node_badges,
    node_rows,
    node_title,
    ordered_columns,
    split_width,
    syntax_runs,
)
from sqlshelf.ui.theme import apply_theme


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    _app()
    set_language("en")
    yield
    set_language("en")


SALES = build_query_graph(SALES_BY_CUSTOMER)
HEALTH = build_query_graph(SYSTEM_HEALTH)
FILTERED = build_query_graph(FILTERS)
LTV = build_query_graph(LTV_ETL)


def _node(graph: QueryGraph, nid: str) -> GraphNode:
    return next(n for n in graph.nodes if n.id == nid)


def _names(columns: list[GraphColumn]) -> list[str]:
    return [c.name for c in columns]


# ---------------------------------------------------------------------------
# What a node shows
# ---------------------------------------------------------------------------


def test_keys_come_first_then_the_order_of_the_text() -> None:
    pedidos = _node(SALES, "vendas.pedidos")
    assert _names(ordered_columns(pedidos)) == [
        "PedidoID",
        "ClienteID",
        "DataPedido",
        "Status",
    ]


def test_a_result_keeps_its_select_order() -> None:
    result = _node(SALES, "result:1")
    assert _names(ordered_columns(result)) == [
        "ClienteID",
        "Nome",
        "Regiao",
        "Pedidos",
        "Pago",
    ]


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ({"select", "join"}, "join"),
        ({"where", "select"}, "where"),
        ({"select", "group by"}, "group by"),
        ({"on"}, "on"),
        ({"select"}, "select"),
        (set(), ""),
        ({"zzz", "aaa"}, "aaa"),
    ],
)
def test_main_role(roles: set[str], expected: str) -> None:
    assert main_role(GraphColumn("x", roles=roles)) == expected


@pytest.mark.parametrize(
    ("graph", "nid", "column", "expected"),
    [
        (SALES, "result:1", "Pedidos", "COUNT(p.PedidoID)"),
        (SALES, "result:1", "Regiao", "r.Nome"),
        (SALES, "vendas.pedidos", "DataPedido", "where"),
        (SALES, "vendas.clientes", "Nome", "group by"),
        (HEALTH, "#systemhealthsessiondata", "XMLDATA", "CAST(xet.target_data AS XML)"),
        (LTV, "#pedidos_validos", "Total", "o.Total"),
        (LTV, "stg.pedidos", "Total", "select"),
    ],
)
def test_column_detail(graph: QueryGraph, nid: str, column: str, expected: str) -> None:
    node = _node(graph, nid)
    found = next(c for c in node.columns if c.name == column)
    assert column_detail(node, found) == expected


@pytest.mark.parametrize(
    ("graph", "nid", "expected"),
    [
        (SALES, "vendas.pedidos", ["FROM"]),
        (SALES, "vendas.clientes", []),
        (SALES, "result:1", ["5 columns"]),
        (HEALTH, "#systemhealthsessiondata", ["INTO"]),
        (HEALTH, "cte:2:cte_healthsession", ["FROM", "CTE"]),
        (HEALTH, "derived:2:t", ["APPLY"]),
        (HEALTH, "result:2", ["1 column"]),
        (LTV, "stg.clientes", ["USING"]),
        (LTV, "dim.cliente", ["MERGE"]),
        (LTV, "mart.clienteltv", ["INSERT · UPDATE"]),
    ],
)
def test_node_badges(graph: QueryGraph, nid: str, expected: list[str]) -> None:
    assert node_badges(_node(graph, nid), graph) == expected


def test_badge_count_is_translated_but_sql_words_are_not() -> None:
    set_language("pt-BR")
    assert node_badges(_node(SALES, "result:1"), SALES) == ["5 colunas"]
    assert node_badges(_node(SALES, "vendas.pedidos"), SALES) == ["FROM"]


def test_title_and_alias() -> None:
    pedidos = _node(SALES, "vendas.pedidos")
    assert (node_title(pedidos), node_alias(pedidos)) == ("Pedidos", "p")
    same = GraphNode("t", "table", None, "Orders", aliases=["orders", "o"])
    assert node_alias(same) == "o"
    derived = GraphNode("derived:1:", "derived", None, "")
    assert node_title(derived) == "(subquery)"
    assert node_alias(derived) == ""


@pytest.mark.parametrize(
    ("graph", "nid", "expected"),
    [
        (SALES, "vendas.pedidos", 2),
        (SALES, "vendas.clientes", 0),
        (FILTERED, "vendas.clientes", 1),  # the ON's c.Ativo = 1
        (FILTERED, "result:1", 1),  # the HAVING footer
    ],
)
def test_filter_count(graph: QueryGraph, nid: str, expected: int) -> None:
    assert filter_count(_node(graph, nid)) == expected


@pytest.mark.parametrize(
    ("graph", "counts", "info"),
    [
        (SALES, DiagramCounts(4, 3, 2, True), "4 tables · 3 joins · 2 filters"),
        (HEALTH, DiagramCounts(5, 2, 2, False), "5 objects · 2 joins · 2 filters"),
        (FILTERED, DiagramCounts(3, 2, 4, True), "3 tables · 2 joins · 4 filters"),
    ],
)
def test_graph_counts(graph: QueryGraph, counts: DiagramCounts, info: str) -> None:
    assert graph_counts(graph) == counts
    assert diagram_info(counts) == info


def test_counts_in_portuguese() -> None:
    set_language("pt-BR")
    assert diagram_info(graph_counts(SALES)) == "4 tabelas · 3 junções · 2 filtros"
    assert (
        diagram_info(DiagramCounts(1, 1, 1, False)) == "1 objeto · 1 junção · 1 filtro"
    )


@pytest.mark.parametrize(
    ("text", "runs"),
    [
        (
            ">= @inicio",
            [(">=", "syn_operator"), (" ", "text2"), ("@inicio", "syn_function")],
        ),
        (
            "NOT IN ('C', 'D')",
            [
                ("NOT", "syn_keyword"),
                (" ", "text2"),
                ("IN", "syn_keyword"),
                (" (", "text2"),
                ("'C'", "syn_string"),
                (", ", "text2"),
                ("'D'", "syn_string"),
                (")", "text2"),
            ],
        ),
        (
            "YEAR(d) = 2026",
            [
                ("YEAR", "syn_function"),
                ("(d) ", "text2"),
                ("=", "syn_operator"),
                (" ", "text2"),
                ("2026", "syn_number"),
            ],
        ),
        ("", []),
    ],
)
def test_syntax_runs(text: str, runs: list[tuple[str, str]]) -> None:
    assert syntax_runs(text) == runs
    assert "".join(piece for piece, _ in syntax_runs(text)) == text


def test_graph_cache_keeps_the_most_recent() -> None:
    cache = GraphCache(size=2)
    a, b, c = (GraphCache.key(t) for t in ("SELECT 1", "SELECT 2", "SELECT 3"))
    cache.put(a, SALES)
    cache.put(b, HEALTH)
    assert cache.get(a) is SALES  # a is now the most recent
    cache.put(c, FILTERED)
    assert len(cache) == 2
    assert cache.get(b) is None
    assert cache.get(a) is SALES
    assert cache.get(c) is FILTERED


def test_cache_key_follows_text_and_dialect() -> None:
    assert GraphCache.key("SELECT 1") == GraphCache.key("SELECT 1")
    assert GraphCache.key("SELECT 1") != GraphCache.key("SELECT 1 ")
    assert GraphCache.key("SELECT `a` FROM t")[1] == "bigquery"


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def _wide_node(columns: int) -> GraphNode:
    return GraphNode(
        "t",
        "table",
        "dbo",
        "T",
        columns=[
            GraphColumn(f"c{i}", {"select"}, spans=[Span(i, i + 1)])
            for i in range(columns)
        ],
    )


def test_more_than_max_rows_collapse_into_more() -> None:
    node = _wide_node(MAX_ROWS + 3)
    rows = node_rows(node)
    assert [r.kind for r in rows] == ["column"] * MAX_ROWS + ["more"]
    assert rows[-1].hidden == 3
    assert len(node_rows(node, expanded=True)) == MAX_ROWS + 3
    assert len(node_rows(_wide_node(MAX_ROWS))) == MAX_ROWS


def test_having_is_a_footer_row() -> None:
    rows = node_rows(_node(FILTERED, "result:1"))
    assert [r.kind for r in rows] == ["column", "column", "having"]
    assert rows[-1].filters is not None
    assert rows[-1].filters[0].clause == "having"


def test_anchors_are_row_middles_and_hidden_columns_meet_more() -> None:
    graph = QueryGraph([_wide_node(MAX_ROWS + 2)], [], [], [])
    shape = measure(graph)["t"]
    assert shape.height == HEADER_HEIGHT + ROW_HEIGHT * (MAX_ROWS + 1)
    assert shape.anchor(None) == HEADER_HEIGHT / 2
    assert shape.anchor("unknown") == HEADER_HEIGHT / 2
    assert shape.anchor("C0") == HEADER_HEIGHT + ROW_HEIGHT / 2
    assert shape.anchor("c2") == HEADER_HEIGHT + ROW_HEIGHT * 2.5
    hidden = HEADER_HEIGHT + ROW_HEIGHT * MAX_ROWS + ROW_HEIGHT / 2
    assert shape.anchor(f"c{MAX_ROWS + 1}") == hidden


@pytest.mark.parametrize("graph", [SALES, HEALTH, FILTERED, LTV])
def test_widths_are_bounded_and_shared_in_a_frame(graph: QueryGraph) -> None:
    shapes = measure(graph)
    groups: dict[str, set[float]] = {}
    for shape in shapes.values():
        assert MIN_WIDTH <= shape.width <= MAX_WIDTH
        groups.setdefault(shape.group, set()).add(shape.width)
    assert all(len(widths) == 1 for widths in groups.values())


def test_a_narrow_node_keeps_the_minimum_width() -> None:
    node = GraphNode("t", "table", None, "T", columns=[GraphColumn("a", {"select"})])
    assert measure(QueryGraph([node], [], [], []))["t"].width == MIN_WIDTH


@pytest.mark.parametrize(
    ("inner", "left", "right", "expected"),
    [
        (200, 50, 80, (50, 80)),  # both fit
        (200, 150, 80, (108, 80)),  # the name gives way
        (100, 80, 80, (28, 60)),  # the right part keeps 60 %
        (100, 10, 200, (10, 78)),  # a short name leaves it more
        (100, 20, 70, (20, 68)),
    ],
)
def test_split_width(
    inner: float, left: float, right: float, expected: tuple[float, float]
) -> None:
    got = split_width(inner, left, right)
    assert got == pytest.approx(expected)
    assert got[0] + got[1] <= inner


# ---------------------------------------------------------------------------
# Scene and view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("graph", "frames", "nodes", "edges", "labels"),
    [
        (SALES, 3, 5, 5, 3),  # 3 joins and one flow per frame into the result
        (HEALTH, 4, 6, 7, 4),
        (LTV, 6, 8, 10, 9),
    ],
)
def test_scene_items(
    graph: QueryGraph, frames: int, nodes: int, edges: int, labels: int
) -> None:
    scene = DiagramScene()
    scene.build(graph)
    assert len(scene.frames) == frames
    assert len(scene.nodes) == nodes
    assert len(scene.edges) == edges
    assert len(scene.labels) == labels
    rect = scene.sceneRect()
    for item in scene.nodes.values():
        assert rect.contains(item.sceneBoundingRect())


def test_scene_reset_and_rebuild() -> None:
    scene = DiagramScene()
    scene.build(SALES)
    scene.build(FILTERED)
    assert len(scene.nodes) == len(FILTERED.nodes)
    assert len(scene.items()) == (
        len(scene.frames) + len(scene.nodes) + len(scene.edges) + len(scene.labels)
    )
    scene.reset()
    assert scene.graph is None
    assert scene.items() == []


@pytest.fixture
def view() -> Iterator[DiagramView]:
    widget = DiagramView()
    widget.resize(500, 300)
    widget.show()
    yield widget
    widget.close()


def test_fit_shows_everything_but_never_above_100(view: DiagramView) -> None:
    zooms: list[int] = []
    view.zoom_changed.connect(zooms.append)
    view.show_graph(LTV)
    assert ZOOM_MIN <= view.zoom() < 1.0
    assert zooms[-1] == view.zoom_percent()
    shown = view.mapToScene(view.viewport().rect()).boundingRect()
    assert shown.contains(view.diagram().sceneRect().adjusted(1, 1, -1, -1))

    view.resize(3000, 2000)
    view.show_graph(FILTERED)
    assert view.zoom() == 1.0


def test_zoom_steps_and_clamps(view: DiagramView) -> None:
    view.show_graph(SALES)
    view.set_zoom(1.0)
    view.zoom_in()
    assert view.zoom_percent() == 110
    view.zoom_out()
    view.zoom_out()
    assert view.zoom_percent() == 90
    view.set_zoom(10)
    assert view.zoom() == ZOOM_MAX
    view.zoom_in()
    assert view.zoom() == ZOOM_MAX
    view.set_zoom(0.01)
    assert view.zoom() == ZOOM_MIN
    view.zoom_out()
    assert view.zoom() == ZOOM_MIN


def test_a_resize_refits_until_the_user_zooms(view: DiagramView) -> None:
    view.show_graph(LTV)
    before = view.zoom()
    view.resize(900, 500)
    assert view.zoom() > before
    view.set_zoom(0.5)
    view.resize(400, 250)
    assert view.zoom() == 0.5


# ---------------------------------------------------------------------------
# The pane
# ---------------------------------------------------------------------------


@pytest.fixture
def pane() -> Iterator[DiagramPane]:
    widget = DiagramPane()
    widget.resize(500, 300)
    widget.show()
    yield widget
    widget.close()


def test_pane_starts_blank(pane: DiagramPane) -> None:
    assert pane.state() == "blank"
    assert pane.graph() is None


def test_analyzing_shows_only_after_a_delay(pane: DiagramPane) -> None:
    pane.begin_analysis()
    assert pane.state() == "blank"
    _pump(ANALYZING_DELAY_MS + 100)
    assert pane.state() == "analyzing"
    assert pane._title.text() == "Analyzing…"
    pane.show_graph(SALES)
    assert pane.state() == "diagram"


def test_a_quick_analysis_never_says_analyzing(pane: DiagramPane) -> None:
    pane.begin_analysis()
    pane.show_graph(SALES)
    _pump(ANALYZING_DELAY_MS + 100)
    assert pane.state() == "diagram"


def test_empty_and_failed(pane: DiagramPane) -> None:
    pane.show_graph(build_query_graph("DECLARE @x INT = 1;"))
    assert pane.state() == "empty"
    assert pane._title.text() == "No tables found in this query"
    assert "SELECT, INSERT, UPDATE, DELETE and MERGE" in pane._detail.text()

    failed = QueryGraph([], [], [], ["failed"], "Unexpected token")
    pane.show_graph(failed)
    assert pane.state() == "failed"
    assert pane._title.text() == "Could not analyze this query."
    assert pane._detail.text() == "Unexpected token"

    pane.clear()
    assert pane.state() == "blank"
    assert pane._title.text() == ""


def test_language_switch_redraws_the_same_graph(pane: DiagramPane) -> None:
    pane.show_graph(SALES)
    pane.view.set_zoom(0.8)
    scene = pane.view.diagram()
    result = scene.nodes["result:1"]
    set_language("pt-BR")
    pane.retranslate_ui()
    assert scene.graph is SALES
    assert scene.nodes["result:1"] is not result  # rebuilt
    assert scene.shapes["result:1"].badges == ["5 colunas"]
    assert pane.view.zoom_percent() == 80


def test_theme_switch_repaints_without_rebuilding(pane: DiagramPane) -> None:
    app = _app()
    pane.show_graph(FILTERED)
    scene = pane.view.diagram()
    node = scene.nodes["vendas.pedidos"]
    try:
        apply_theme(app, "light")
        pane.refresh_theme()
        light = pane.view.grab().toImage().pixelColor(2, 2)
        apply_theme(app, "dark")
        pane.refresh_theme()
        dark = pane.view.grab().toImage().pixelColor(2, 2)
    finally:
        apply_theme(app, "light")
    assert scene.nodes["vendas.pedidos"] is node
    assert light.name() == _tk.LIGHT["dg_canvas"]
    assert dark.name() == _tk.DARK["dg_canvas"]


def test_reset_zoom(pane: DiagramPane) -> None:
    pane.show_graph(LTV)
    pane.reset_zoom()
    assert pane.zoom_percent() == 100
    pane.fit()
    assert pane.zoom_percent() < 100
