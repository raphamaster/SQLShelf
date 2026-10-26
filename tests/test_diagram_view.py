"""Tests for ui/diagram_view.py: what a node shows, the scene and the pane."""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import (
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QShortcut,
    QTextDocument,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from test_query_graph import FILTERS
from test_sql_segments import LTV_ETL, SALES_BY_CUSTOMER, SYSTEM_HEALTH

from sqlshelf.core.graph_layout import FRAME_TITLE, Rect
from sqlshelf.core.i18n import set_language
from sqlshelf.core.query_graph import (
    GraphColumn,
    GraphNode,
    QueryGraph,
    Span,
    StatementInfo,
    build_query_graph,
)
from sqlshelf.ui import diagram_view as dv
from sqlshelf.ui import tokens as _tk
from sqlshelf.ui.diagram_view import (
    ANALYZING_DELAY_MS,
    CROWDED_NODES,
    HEADER_HEIGHT,
    LARGE_BODY,
    MAX_ROWS,
    MAX_WIDTH,
    MIN_WIDTH,
    ROW_HEIGHT,
    TIP_LINES,
    ZOOM_MAX,
    ZOOM_MIN,
    DiagramCounts,
    DiagramPane,
    DiagramScene,
    DiagramView,
    GraphCache,
    LabelItem,
    LineIndex,
    NodeItem,
    column_detail,
    column_tooltip,
    diagram_info,
    direct_route,
    edge_title,
    edge_tooltip,
    filter_count,
    filters_tooltip,
    graph_counts,
    is_large,
    main_role,
    measure,
    node_alias,
    node_badges,
    node_rows,
    node_title,
    node_tooltip,
    ordered_columns,
    preserved_table,
    qualified_name,
    regressed,
    split_width,
    statement_label,
    syntax_runs,
    without_results,
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


def _dispose(widget: QWidget) -> None:
    # Delete it now: a menu left for deleteLater holds the last reference to
    # it through its actions' slots, and would take it down mid-destruction.
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def view() -> Iterator[DiagramView]:
    widget = DiagramView()
    widget.resize(500, 300)
    widget.show()
    yield widget
    _dispose(widget)


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
    _dispose(widget)


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


# ---------------------------------------------------------------------------
# Interaction: statements, lines and tooltips
# ---------------------------------------------------------------------------

PARTIAL = "SELECT a FROM t;\nSELECT FROM WHERE ((;"


def _plain(html: str) -> str:
    document = QTextDocument()
    document.setHtml(html)
    return document.toPlainText()


@pytest.mark.parametrize(
    ("char", "count", "large"),
    [
        ("x", LARGE_BODY, False),
        ("x", LARGE_BODY + 1, True),
        ("é", LARGE_BODY // 2, False),  # two bytes each
        ("é", LARGE_BODY // 2 + 1, True),
    ],
)
def test_is_large_counts_utf8_bytes(char: str, count: int, large: bool) -> None:
    assert is_large(char * count) is large


def test_without_results_drops_the_results_and_their_flows() -> None:
    graph = without_results(SALES)
    assert {n.id for n in graph.nodes} == {
        "vendas.pedidos",
        "vendas.clientes",
        "vendas.pagamentos",
        "dbo.regiao",
    }
    assert [e.kind for e in graph.edges] == ["join"] * 3
    assert graph.statements == SALES.statements
    assert without_results(LTV) is LTV  # nothing to drop


def test_statement_label() -> None:
    info = StatementInfo(2, "SELECT", "SELECT … INTO #x", 4, Span(0, 10), "ok")
    assert statement_label(info) == "2 · SELECT … INTO #x"


_OK = build_query_graph("SELECT a FROM t")
_PARTIAL = build_query_graph(PARTIAL)
_FAILED = QueryGraph([], [], [], ["failed"], "Unexpected token")


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        (_OK, None, False),
        (_FAILED, _OK, True),
        (_PARTIAL, _OK, True),
        (_OK, _PARTIAL, False),
        (_PARTIAL, _PARTIAL, False),
        (_FAILED, _FAILED, False),  # nothing better to keep
        (_OK, _FAILED, False),
    ],
)
def test_regressed(new: QueryGraph, old: QueryGraph | None, expected: bool) -> None:
    assert regressed(new, old) is expected


@pytest.mark.parametrize(
    ("offset", "line"), [(0, 1), (1, 1), (2, 2), (4, 2), (5, 3), (6, 4)]
)
def test_line_index(offset: int, line: int) -> None:
    assert LineIndex("a\nbc\n\nd").line(offset) == line


@pytest.mark.parametrize(
    ("graph", "index", "title"),
    [
        (SALES, 0, "INNER JOIN"),
        (HEALTH, 3, "CROSS APPLY"),
        (HEALTH, 1, "INTO"),
        (SALES, 3, "SELECT"),
        (LTV, 8, "BETWEEN"),
        (LTV, 3, "MERGE"),
    ],
)
def test_edge_title(graph: QueryGraph, index: int, title: str) -> None:
    assert edge_title(graph.edges[index]) == title


@pytest.mark.parametrize(
    ("a", "b", "route"),
    [
        (Rect(0, 0, 100, 50), Rect(200, 0, 100, 50), [(100, 10), (200, 20)]),
        (Rect(200, 0, 100, 50), Rect(0, 0, 100, 50), [(200, 10), (100, 20)]),
        # Too close side by side: around the right of both.
        (
            Rect(0, 0, 100, 50),
            Rect(50, 100, 100, 50),
            [(100, 10), (170, 10), (170, 20), (150, 20)],
        ),
    ],
)
def test_direct_route(a: Rect, b: Rect, route: list[tuple[float, float]]) -> None:
    assert direct_route(a, 10, b, 20) == route


_JOINED = "SELECT p.PedidoID FROM vendas.Pedidos p {join} JOIN vendas.Pagamentos pg ON pg.PedidoID = p.PedidoID AND {cond}"


@pytest.mark.parametrize(
    ("sql", "nid", "column", "kept", "note"),
    [
        (
            _JOINED.format(join="LEFT", cond="pg.Status = 'OK'"),
            "vendas.pagamentos",
            "Status",
            "vendas.pedidos",
            "In the ON of the LEFT JOIN: does not remove rows from Pedidos",
        ),
        (
            _JOINED.format(join="RIGHT", cond="p.Status = 'C'"),
            "vendas.pedidos",
            "Status",
            "vendas.pagamentos",
            "In the ON of the RIGHT JOIN: does not remove rows from Pagamentos",
        ),
        (
            _JOINED.format(join="FULL", cond="pg.Status = 'OK'"),
            "vendas.pagamentos",
            "Status",
            None,
            "In the ON of the FULL JOIN: does not remove rows from either side",
        ),
        # The nearest join before the filter: B is preserved by the second.
        (
            "SELECT a.x FROM dbo.A a LEFT JOIN dbo.B b ON b.id = a.id "
            "LEFT JOIN dbo.C c ON c.id = b.id AND b.flag = 1",
            "dbo.b",
            "flag",
            "dbo.b",
            "In the ON of the LEFT JOIN: does not remove rows from B",
        ),
        (
            "SELECT t.a FROM dbo.T t WHERE t.x = 1 OR t.y = 2",
            "dbo.t",
            "x",
            None,
            "Part of a condition with OR",
        ),
    ],
)
def test_filter_notes(
    sql: str, nid: str, column: str, kept: str | None, note: str
) -> None:
    graph = build_query_graph(sql)
    node = _node(graph, nid)
    found = next(c for c in node.columns if c.name == column)
    preserved = preserved_table(graph, node, found.filters[0])
    assert (preserved.id if preserved else None) == kept
    tip = _plain(filters_tooltip(graph, node, found.filters, LineIndex(sql)))
    assert tip.startswith("Filter · ")
    assert note in tip


def test_qualified_name() -> None:
    assert qualified_name(_node(SALES, "vendas.pedidos")) == "vendas.Pedidos"
    assert qualified_name(GraphNode("t", "table", None, "T")) == "T"
    cte = _node(HEALTH, "cte:2:cte_healthsession")
    assert qualified_name(cte) == "CTE_HealthSession"


def test_edge_tooltip_shows_the_condition() -> None:
    tip = _plain(edge_tooltip(SALES, [0], LineIndex(SALES_BY_CUSTOMER)))
    assert tip.startswith("INNER JOIN · line 4\n")
    assert "c.ClienteID = p.ClienteID" in tip
    assert tip.rstrip().endswith("Double-click opens in SQL")
    assert "statement" not in tip  # one statement only


def test_edge_tooltip_names_the_statement_when_there_are_several() -> None:
    lines = LineIndex(SYSTEM_HEALTH)
    tip = _plain(edge_tooltip(HEALTH, [0], lines))
    assert tip.startswith("INNER JOIN · statement 1 · line 9\n")
    # A temp table is in two statements: named in full, not by an alias.
    assert "xet → #SystemHealthSessionData" in _plain(edge_tooltip(HEALTH, [1], lines))


def test_a_bundle_lists_its_flows(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = LineIndex(SALES_BY_CUSTOMER)
    tip = _plain(edge_tooltip(SALES, [3, 4, 5], lines))
    assert tip.startswith("SELECT · line 3\n")
    for pair in ("p → SELECT", "c → SELECT", "pg → SELECT"):
        assert pair in tip
    monkeypatch.setattr(dv, "TIP_MEMBERS", 2)
    tip = _plain(edge_tooltip(SALES, [3, 4, 5], lines))
    assert "pg → SELECT" not in tip
    assert "+1" in tip


def test_column_tooltip() -> None:
    lines = LineIndex(SALES_BY_CUSTOMER)
    pedidos = _node(SALES, "vendas.pedidos")
    column = next(c for c in pedidos.columns if c.name == "PedidoID")
    tip = _plain(column_tooltip(SALES, pedidos, column, lines))
    assert tip.startswith("PedidoID · lines 2, 5\njoin · select\n")
    assert tip.rstrip().endswith("Double-click opens in SQL")

    status = next(c for c in pedidos.columns if c.name == "Status")
    tip = _plain(column_tooltip(SALES, pedidos, status, lines))
    assert tip.startswith("Filter · WHERE · line 8\n")
    assert "p.Status <> 'C'" in tip  # escaped, and back

    result = _node(SALES, "result:1")
    count = next(c for c in result.columns if c.name == "Pedidos")
    tip = _plain(column_tooltip(SALES, result, count, lines))
    assert "COUNT(p.PedidoID)" in tip
    assert "Double-click" not in tip  # nothing in the text to open


def test_column_tooltip_lists_a_few_lines() -> None:
    text = "x\n" * (TIP_LINES + 3)
    spans = [Span(2 * i, 2 * i + 1) for i in range(TIP_LINES + 3)]
    column = GraphColumn("c", {"select"}, spans=spans)
    node = GraphNode("t", "table", None, "T", columns=[column])
    graph = QueryGraph([node], [], [], [])
    tip = _plain(column_tooltip(graph, node, column, LineIndex(text)))
    shown = ", ".join(str(n) for n in range(1, TIP_LINES + 1))
    assert tip.startswith(f"c · lines {shown}, …\n")


def test_node_tooltip() -> None:
    tip = _plain(node_tooltip(_node(SALES, "vendas.pedidos")))
    assert tip.startswith("vendas.Pedidos · p\n")
    assert tip.rstrip().endswith("Double-click opens in SQL")
    assert _plain(node_tooltip(_node(SALES, "result:1"))).strip() == "SELECT"


# ---------------------------------------------------------------------------
# Interaction: the scene
# ---------------------------------------------------------------------------


def _header(item: NodeItem) -> QPointF:
    rect = item.rect()
    return QPointF(rect.x() + 20, rect.y() + HEADER_HEIGHT / 2)


def _row(item: NodeItem, i: int) -> QPointF:
    rect = item.rect()
    return QPointF(rect.x() + 20, rect.y() + HEADER_HEIGHT + (i + 0.5) * ROW_HEIGHT)


def _scene(graph: QueryGraph, text: str = "") -> DiagramScene:
    scene = DiagramScene()
    scene.lines = LineIndex(text)
    scene.build(graph)
    return scene


def test_tooltip_at_rows() -> None:
    scene = _scene(FILTERED, FILTERS)
    result = scene.nodes["result:1"]
    assert isinstance(scene.hit(_header(result)), NodeItem)
    assert _plain(scene.tooltip_at(_header(result))).strip() == "SELECT"
    having = _plain(scene.tooltip_at(_row(result, 2)))
    assert having.startswith("Filter · HAVING")
    assert "SUM(pg.Valor) > 1000" in having
    assert scene.tooltip_at(QPointF(-30, -30)) == ""

    scene = _scene(QueryGraph([_wide_node(MAX_ROWS + 2)], [], [], []))
    more = _row(scene.nodes["t"], MAX_ROWS)
    assert _plain(scene.tooltip_at(more)) == "Click to show every column"


def test_span_at() -> None:
    scene = _scene(SALES, SALES_BY_CUSTOMER)
    pedidos = scene.nodes["vendas.pedidos"]
    assert scene.span_at(_header(pedidos)) == Span(110, 124)

    label = next(item for item in scene.labels if item.edge == 0)
    centre = label.sceneBoundingRect().center()
    assert isinstance(scene.hit(centre), LabelItem)
    assert scene.span_at(centre) == Span(159, 184)

    names = [r.column.name if r.column else "" for r in pedidos.shape_info.rows]
    data = next(c for c in pedidos.node.columns if c.name == "DataPedido")
    at = _row(pedidos, names.index("DataPedido"))
    assert scene.span_at(at) == data.filters[0].span  # its filter's

    bundle = next(e for e in scene.edges if e.edges == [3, 4, 5])
    assert scene.span_at(bundle.middle()) == Span(110, 124)  # the first flow's
    assert scene.span_at(QPointF(-30, -30)) is None


def test_selecting_a_node_dims_what_it_does_not_reach() -> None:
    scene = _scene(SALES)
    scene.select("dbo.regiao")
    assert scene.selected == "dbo.regiao"
    assert scene.nodes["dbo.regiao"].selected
    lit = {nid for nid, item in scene.nodes.items() if item.opacity() == 1.0}
    assert lit == {"dbo.regiao", "vendas.clientes", "result:1"}
    dim = _tk.opacity("dg_dim")
    assert scene.nodes["vendas.pedidos"].opacity() == pytest.approx(dim)
    assert sorted(e.edges for e in scene.edges if e.highlighted) == [[2], [6]]
    assert next(item for item in scene.labels if item.edge == 2).highlighted
    assert scene.neighbours("vendas.pedidos") == {
        "vendas.clientes",
        "vendas.pagamentos",
        "result:1",
    }

    scene.select(None)
    items = [*scene.nodes.values(), *scene.edges, *scene.labels]
    assert all(item.opacity() == 1.0 for item in items)
    scene.select("nowhere")
    assert scene.selected is None


def test_moving_a_node_grows_its_frame_and_reroutes_its_edges() -> None:
    scene = _scene(SALES)
    frame = scene.frame_of["schema:vendas"]
    before = frame.scene_rect()
    item = scene.nodes["vendas.pedidos"]
    start = item.rect().topLeft()
    scene.move_node("vendas.pedidos", QPointF(0, 500))
    assert item.rect().topLeft() == start + QPointF(0, 500)
    assert scene.moved == {"vendas.pedidos": start + QPointF(0, 500)}
    assert frame.scene_rect().contains(item.rect())
    assert frame.scene_rect().contains(before)  # grows, never shrinks
    assert scene.sceneRect().contains(item.sceneBoundingRect())
    # Under the node it joined: around the right of both.
    join = next(e for e in scene.edges if e.edges == [0])
    right = item.rect().right()
    assert len(join.points) == 4
    assert join.points[0] == (right, item.rect().y() + join.base[0][1] - start.y())
    assert join.points[1][0] > right

    scene.move_group("schema:vendas", QPointF(10, 0))  # the moved node follows
    assert item.rect().topLeft() == start + QPointF(10, 500)


def test_moving_a_frame_moves_its_nodes_and_edges() -> None:
    scene = _scene(SALES)
    frame = scene.frame_of["schema:vendas"]
    before = frame.scene_rect()
    rects = {nid: item.rect() for nid, item in scene.nodes.items()}
    join = next(e for e in scene.edges if e.edges == [0])
    points = list(join.points)
    scene.move_group("schema:vendas", QPointF(-50, 20))
    assert frame.scene_rect() == before.translated(-50, 20)
    for nid, item in scene.nodes.items():
        moved = nid.startswith("vendas.")
        expected = rects[nid].translated(-50, 20) if moved else rects[nid]
        assert item.rect() == expected
    assert join.points == [(x - 50, y + 20) for x, y in points]


def test_collapsing_a_frame_hides_its_nodes() -> None:
    scene = _scene(SALES)
    scene.select("vendas.pedidos")
    scene.toggle_group("schema:vendas")
    frame = scene.frame_of["schema:vendas"]
    assert frame.collapsed
    assert frame.scene_rect().height() == FRAME_TITLE
    inside = ("vendas.pedidos", "vendas.clientes", "vendas.pagamentos")
    assert not any(scene.nodes[nid].isVisible() for nid in inside)
    assert not any(e.isVisible() for e in scene.edges if e.edges in ([0], [1]))
    assert not any(item.isVisible() for item in scene.labels if item.edge in (0, 1))
    assert all(e.isVisible() for e in scene.edges if e.edges not in ([0], [1]))
    assert scene.selected is None  # hidden, so no longer selected

    scene.toggle_group("schema:vendas")
    assert all(scene.nodes[nid].isVisible() for nid in inside)


def test_rearrange_puts_everything_back() -> None:
    scene = _scene(SALES)
    rects = {nid: item.rect() for nid, item in scene.nodes.items()}
    scene.move_node("dbo.regiao", QPointF(30, 30))
    scene.move_group("schema:vendas", QPointF(-20, 0))
    scene.toggle_group("result")
    scene.rearrange()
    assert not (scene.moved or scene.shifted or scene.collapsed)
    assert not scene.frame_of["result"].collapsed
    assert {nid: item.rect() for nid, item in scene.nodes.items()} == rects
    assert all(item.isVisible() for item in scene.nodes.values())


def test_expand_shows_every_column() -> None:
    scene = _scene(QueryGraph([_wide_node(MAX_ROWS + 2)], [], [], []))
    assert scene.nodes["t"].shape_info.rows[-1].kind == "more"
    scene.expand("t")
    assert scene.expanded == {"t"}
    assert len(scene.nodes["t"].shape_info.rows) == MAX_ROWS + 2


def test_the_same_query_read_again_keeps_what_the_user_did(view: DiagramView) -> None:
    view.show_graph(SALES)
    scene = view.diagram()
    scene.move_node("dbo.regiao", QPointF(30, 30))
    scene.toggle_group("result")
    scene.select("vendas.pedidos")
    moved = scene.nodes["dbo.regiao"].rect()
    view.set_zoom(0.5)
    view.show_graph(SALES, keep=True)
    assert scene.nodes["dbo.regiao"].rect() == moved
    assert scene.collapsed == {"result"}
    assert scene.selected == "vendas.pedidos"
    assert view.zoom() == 0.5

    view.show_graph(SALES)  # another query
    assert not (scene.moved or scene.collapsed)
    assert scene.selected is None
    assert view.zoom() < 0.5


# ---------------------------------------------------------------------------
# Interaction: mouse, keys and menus
# ---------------------------------------------------------------------------


def _at(view: DiagramView, point: QPointF) -> QPoint:
    return view.mapFromScene(point)


def _mouse(view: DiagramView, kind: QEvent.Type, point: QPoint) -> None:
    left = Qt.MouseButton.LeftButton
    none = Qt.MouseButton.NoButton
    button = none if kind == QEvent.Type.MouseMove else left
    held = none if kind == QEvent.Type.MouseButtonRelease else left
    viewport = view.viewport()
    event = QMouseEvent(
        kind,
        QPointF(point),
        QPointF(viewport.mapToGlobal(point)),
        button,
        held,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)


def _click(view: DiagramView, point: QPoint) -> None:
    _mouse(view, QEvent.Type.MouseButtonPress, point)
    _mouse(view, QEvent.Type.MouseButtonRelease, point)


def _drag(view: DiagramView, start: QPoint, end: QPoint) -> None:
    _mouse(view, QEvent.Type.MouseButtonPress, start)
    _mouse(view, QEvent.Type.MouseMove, end)
    _mouse(view, QEvent.Type.MouseButtonRelease, end)


def test_a_click_selects_and_the_canvas_deselects(view: DiagramView) -> None:
    view.show_graph(SALES)
    scene = view.diagram()
    _click(view, _at(view, _header(scene.nodes["vendas.pedidos"])))
    assert scene.selected == "vendas.pedidos"
    _click(view, _at(view, QPointF(-30, -30)))
    assert scene.selected is None


def test_dragging_a_node_moves_it(view: DiagramView) -> None:
    view.show_graph(SALES)
    scene = view.diagram()
    item = scene.nodes["dbo.regiao"]
    before = item.rect().topLeft()
    start = _at(view, _header(item))
    _drag(view, start, start + QPoint(40, 30))
    moved = item.rect().topLeft() - before
    slack = 1.5 / view.zoom()  # a pixel of rounding
    assert moved.x() == pytest.approx(40 / view.zoom(), abs=slack)
    assert moved.y() == pytest.approx(30 / view.zoom(), abs=slack)
    assert scene.selected == "dbo.regiao"
    assert set(scene.moved) == {"dbo.regiao"}


def test_a_frame_title_deselects_and_its_chevron_collapses(view: DiagramView) -> None:
    view.show_graph(SALES)
    scene = view.diagram()
    frame = scene.frame_of["schema:vendas"].scene_rect()
    y = frame.y() + FRAME_TITLE / 2
    scene.select("vendas.pedidos")
    _click(view, _at(view, QPointF(frame.x() + 150, y)))
    assert scene.selected is None
    assert not scene.collapsed
    _click(view, _at(view, QPointF(frame.x() + 14, y)))
    assert scene.collapsed == {"schema:vendas"}


def test_a_frame_dragged_by_its_title_moves(view: DiagramView) -> None:
    view.show_graph(SALES)
    scene = view.diagram()
    frame = scene.frame_of["schema:vendas"]
    before = frame.scene_rect()
    start = _at(view, QPointF(before.x() + 150, before.y() + FRAME_TITLE / 2))
    _drag(view, start, start + QPoint(0, 40))
    assert frame.scene_rect().y() > before.y()
    assert set(scene.shifted) == {"schema:vendas"}
    assert not scene.collapsed


def test_clicking_more_expands_the_node(view: DiagramView) -> None:
    view.show_graph(QueryGraph([_wide_node(MAX_ROWS + 2)], [], [], []))
    scene = view.diagram()
    _click(view, _at(view, _row(scene.nodes["t"], MAX_ROWS)))
    assert scene.expanded == {"t"}


def test_a_double_click_opens_the_text(view: DiagramView) -> None:
    view.show_graph(SALES)
    spans: list[tuple[int, int]] = []
    view.span_requested.connect(lambda start, end: spans.append((start, end)))
    header = _header(view.diagram().nodes["vendas.pedidos"])
    _mouse(view, QEvent.Type.MouseButtonDblClick, _at(view, header))
    assert spans == [(110, 124)]
    _mouse(view, QEvent.Type.MouseButtonDblClick, _at(view, QPointF(-30, -30)))
    assert spans == [(110, 124)]


def test_escape_deselects_before_a_window_shortcut() -> None:
    host = QWidget()
    view = DiagramView()
    QVBoxLayout(host).addWidget(view)
    closed: list[bool] = []
    QShortcut(QKeySequence("Esc"), host).activated.connect(lambda: closed.append(True))
    host.resize(500, 300)
    host.show()
    host.activateWindow()
    assert QTest.qWaitForWindowActive(host)
    view.setFocus()
    try:
        view.show_graph(SALES)
        view.diagram().select("vendas.pedidos")
        QTest.keyClick(view, Qt.Key.Key_Escape)
        assert view.diagram().selected is None
        assert closed == []
        QTest.keyClick(view, Qt.Key.Key_Escape)
        assert closed == [True]
    finally:
        host.close()


def test_zoom_keys(view: DiagramView) -> None:
    view.show_graph(SALES)
    view.set_zoom(1.0)
    ctrl = Qt.KeyboardModifier.ControlModifier
    QTest.keyClick(view, Qt.Key.Key_Equal, ctrl)
    assert view.zoom_percent() == 110
    QTest.keyClick(view, Qt.Key.Key_Minus, ctrl)
    QTest.keyClick(view, Qt.Key.Key_Minus, ctrl)
    assert view.zoom_percent() == 90
    QTest.keyClick(view, Qt.Key.Key_0, ctrl)
    assert view.zoom_percent() < 90  # fitted


def test_node_menu(view: DiagramView) -> None:
    view.show_graph(SALES)
    spans: list[tuple[int, int]] = []
    searches: list[tuple[str, str]] = []
    view.span_requested.connect(lambda start, end: spans.append((start, end)))
    view.filter_requested.connect(lambda kind, name: searches.append((kind, name)))
    menu = view.node_menu("vendas.pedidos")
    actions = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == [
        "Go to SQL",
        "Search in all queries",
        "Copy name",
    ]
    for action in actions:
        action.trigger()
    assert spans == [(110, 124)]
    assert searches == [("table", "Pedidos")]
    assert QGuiApplication.clipboard().text() == "vendas.Pedidos"
    menu.deleteLater()


def test_a_result_has_no_name_to_search_or_copy(view: DiagramView) -> None:
    view.show_graph(SALES)
    menu = view.node_menu("result:1")
    actions = {a.text(): a.isEnabled() for a in menu.actions() if not a.isSeparator()}
    assert actions == {"Go to SQL": False, "Copy name": False}
    menu.deleteLater()


def test_only_tables_are_searched(view: DiagramView) -> None:
    view.show_graph(HEALTH)
    menu = view.node_menu("cte:2:cte_healthsession")
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Go to SQL", "Copy name"]
    menu.deleteLater()


def test_background_menu(view: DiagramView) -> None:
    view.show_graph(SALES)
    menu = view.background_menu()
    actions = menu.actions()
    assert [a.text() for a in actions] == ["Fit", "Rearrange"]
    assert actions[0].shortcut() == QKeySequence("Ctrl+0")
    view.diagram().move_node("dbo.regiao", QPointF(30, 30))
    actions[1].trigger()
    assert not view.diagram().moved
    menu.deleteLater()


# ---------------------------------------------------------------------------
# Interaction: the pane
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("graph", "index", "nodes"),
    [
        (
            HEALTH,
            1,
            {
                "#systemhealthsessiondata",
                "sys.dm_xe_session_targets",
                "sys.dm_xe_sessions",
            },
        ),
        (
            HEALTH,
            2,
            {
                "cte:2:cte_healthsession",
                "#systemhealthsessiondata",
                "derived:2:t",
                "result:2",
            },
        ),
        (
            LTV,
            3,
            {"#pedidos_validos", "cte:3:agg", "dim.cliente", "mart.clienteltv"},
        ),
        (LTV, 4, {"mart.clienteltv", "ref.faixasvalor"}),
    ],
)
def test_one_statement_at_a_time(
    pane: DiagramPane, graph: QueryGraph, index: int, nodes: set[str]
) -> None:
    shown: list[QueryGraph | None] = []
    pane.displayed_changed.connect(shown.append)
    pane.show_graph(graph)
    pane.set_statement(index)
    assert pane.statement() == index
    assert set(pane.view.diagram().nodes) == nodes
    drawn = pane.displayed()
    assert drawn is not None
    assert {e.statement for e in drawn.edges} == {index}
    assert shown[-1] is drawn
    assert pane.graph() is graph  # still the whole text's

    pane.set_statement(0)
    assert len(pane.view.diagram().nodes) == len(graph.nodes)


def test_hiding_the_results_keeps_what_was_moved(pane: DiagramPane) -> None:
    pane.show_graph(SALES)
    scene = pane.view.diagram()
    scene.move_node("dbo.regiao", QPointF(0, 80))
    moved = scene.nodes["dbo.regiao"].rect()
    pane.set_show_results(False)
    assert not pane.show_results()
    assert "result:1" not in scene.nodes
    assert scene.nodes["dbo.regiao"].rect() == moved
    pane.set_show_results(True)
    assert "result:1" in scene.nodes
    assert scene.nodes["dbo.regiao"].rect() == moved


def test_the_statement_is_kept_only_for_the_same_query(pane: DiagramPane) -> None:
    pane.show_graph(HEALTH, SYSTEM_HEALTH, "a.sql")
    pane.set_statement(2)
    pane.show_graph(HEALTH, SYSTEM_HEALTH, "a.sql", keep=True)
    assert pane.statement() == 2
    pane.show_graph(HEALTH, SYSTEM_HEALTH, "b.sql")
    assert pane.statement() == 0

    pane.set_statement(2)
    pane.show_graph(_PARTIAL, PARTIAL, "b.sql", keep=True)  # 2 is not read there
    assert pane.statement() == 0


def test_partial_notice(pane: DiagramPane) -> None:
    spans: list[tuple[int, int]] = []
    pane.span_requested.connect(lambda start, end: spans.append((start, end)))
    pane.show_graph(_PARTIAL, PARTIAL, "a.sql")
    assert pane.state() == "diagram"
    assert pane.notices() == ["partial"]
    assert pane._partial.label.text() == (
        "Partial analysis — 1 statement could not be fully read (line 2)"
    )
    assert pane._partial.button.text() == "Go to line"
    pane._partial.button.click()
    start = _PARTIAL.statements[1].span.start
    assert spans == [(start, start)]

    pane._partial._close.click()
    assert pane.notices() == []
    pane.show_graph(_PARTIAL, PARTIAL, "a.sql")
    assert pane.notices() == []  # dismissed for this query
    pane.show_graph(_PARTIAL, PARTIAL, "b.sql")
    assert pane.notices() == ["partial"]


def test_dynamic_sql_notice(pane: DiagramPane) -> None:
    pane.show_graph(build_query_graph("EXEC sp_executesql N'SELECT 1'"))
    assert pane.state() == "empty"
    assert pane.notices() == ["dynamic"]
    assert pane._dynamic.label.text() == (
        "Dynamic SQL (EXEC / sp_executesql) is not analyzed."
    )
    assert pane._dynamic.button.isHidden()


def _crowded() -> QueryGraph:
    nodes = [
        GraphNode(
            f"t{i}", "table", "dbo", f"T{i}", columns=[GraphColumn("a", {"select"})]
        )
        for i in range(CROWDED_NODES + 1)
    ]
    return QueryGraph(nodes, [], [], [])


def test_crowded_notice(pane: DiagramPane) -> None:
    pane.show_graph(_crowded())
    scene = pane.view.diagram()
    assert scene.headers_only
    assert pane.notices() == ["crowded"]
    assert not any(item.shape_info.rows for item in scene.nodes.values())
    pane._crowded.button.click()
    assert not scene.headers_only
    assert pane.notices() == []
    assert all(item.shape_info.rows for item in scene.nodes.values())


def test_a_large_text_waits_to_be_asked(pane: DiagramPane) -> None:
    pane.show_graph(SALES)
    asked: list[bool] = []
    shown: list[QueryGraph | None] = []
    pane.analyze_requested.connect(lambda: asked.append(True))
    pane.displayed_changed.connect(shown.append)
    pane.show_large("big.sql")
    assert pane.state() == "large"
    assert pane.displayed() is None
    assert shown == [None]
    assert pane._title.text() == "Large file for automatic analysis"
    assert pane._large_button.text() == "Analyze anyway"
    assert not pane._large_button.isHidden()
    assert pane.notices() == []
    pane._large_button.click()
    assert asked == [True]
