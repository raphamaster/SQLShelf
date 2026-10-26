"""Tests for core/graph_layout.py: frames, layers, order, routes."""

from __future__ import annotations

import random
import time

import pytest
from test_sql_segments import (
    DBA_SCRIPT,
    IF_ELSE,
    LOAD_PROCEDURE,
    LTV_ETL,
    SALES_BY_CUSTOMER,
    SYSTEM_HEALTH,
)

from sqlshelf.core.graph_layout import (
    LANE,
    STACK,
    Layout,
    Point,
    Rect,
    layout,
    node_group,
)
from sqlshelf.core.query_graph import (
    GraphColumn,
    GraphEdge,
    GraphNode,
    QueryGraph,
    Span,
    StatementInfo,
    build_query_graph,
)


def _node(nid: str, base: bool = False, columns: int = 2) -> GraphNode:
    if nid.startswith("result:"):
        kind, schema = "result", None
    elif nid.startswith(("cte:", "derived:")):
        kind, schema = "cte", None
    elif nid.startswith(("#", "@")):
        kind, schema = "temp", None
    elif "." in nid:
        kind, schema = "table", nid.split(".")[0]
    else:
        kind, schema = "table", None
    return GraphNode(
        nid,
        kind,  # type: ignore[arg-type]
        schema,
        nid.rsplit(".", 1)[-1],
        columns=[GraphColumn(f"c{i}") for i in range(columns)],
        is_base=base,
    )


def _graph(
    nodes: list[GraphNode],
    edges: list[tuple[str, str, str]],
    bases: dict[int, str] | None = None,
) -> QueryGraph:
    """*edges* as (kind, source, target); a kind like "join@2" puts the edge
    in statement 2 (the default is 1). *bases* maps statements to bases."""
    built = []
    for kind, source, target in edges:
        statement = 1
        if "@" in kind:
            kind, number = kind.split("@")
            statement = int(number)
        built.append(
            GraphEdge(
                kind,  # type: ignore[arg-type]
                source,
                target,
                "c0",
                "c1",
                label="INNER" if kind == "join" else "INTO",
                statement=statement,
            )
        )
    statements = [
        StatementInfo(i, "SELECT", "", 1, Span(0, 0), "ok", base)
        for i, base in sorted((bases or {}).items())
    ]
    return QueryGraph(nodes, built, statements, [])


def _sizes(graph: QueryGraph) -> dict[str, tuple[float, float]]:
    return {
        n.id: (180.0 + 7 * len(n.name), 30.0 + 26 * len(n.columns)) for n in graph.nodes
    }


def _anchor(node: GraphNode, column: str | None) -> float:
    names = [c.name.casefold() for c in node.columns]
    if column is None or column.casefold() not in names:
        return 15.0
    return 43.0 + 26 * names.index(column.casefold())


def _random_graph(seed: int, size: int = 50) -> QueryGraph:
    """Tables in five schemas, temp tables, CTEs and results, with joins and
    flows; a flow never goes back to an earlier frame, so flows have no cycle."""
    rng = random.Random(seed)
    ids = []
    for i in range(size - 10):
        ids.append(f"s{i % 5}.t{i}")
    ids += [f"#tmp{i}" for i in range(5)] + [f"cte:{i}:c" for i in range(3)]
    rng.shuffle(ids)
    ids += ["result:1", "result:2"]
    nodes = [_node(n, columns=rng.randint(1, 8)) for n in ids]
    edges = []
    bases = {}
    for statement in (1, 2, 3, 4):
        picked = sorted(rng.sample(range(size - 2), 8))
        bases[statement] = ids[picked[0]]
        for a, b in zip(picked, picked[1:]):
            edges.append((f"join@{statement}", ids[picked[0]], ids[b]))
            if rng.random() < 0.5:
                if _rank(ids[a]) > _rank(ids[b]):
                    a, b = b, a
                edges.append((f"flow@{statement}", ids[a], ids[b]))
        if statement <= 2:
            for a in picked[:3]:
                edges.append((f"flow@{statement}", ids[a], f"result:{statement}"))
    return _graph(nodes, edges, bases)


def _rank(nid: str) -> int:
    if nid.startswith("#"):
        return 5
    if nid.startswith("cte:"):
        return 6
    return int(nid[1])


def _lay(graph: QueryGraph) -> Layout:
    return layout(graph, _sizes(graph), _anchor)


SCRIPTS = {
    "sales": SALES_BY_CUSTOMER,
    "system_health": SYSTEM_HEALTH,
    "ltv_etl": LTV_ETL,
    "load_procedure": LOAD_PROCEDURE,
    "if_else": IF_ELSE,
    "dba": DBA_SCRIPT,
}

GRAPHS = [
    *(pytest.param(build_query_graph(s), id=k) for k, s in SCRIPTS.items()),
    *(pytest.param(_random_graph(seed), id=f"random{seed}") for seed in range(8)),
    pytest.param(_random_graph(99, size=20), id="random_small"),
]


def _overlap(a: Rect, b: Rect) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.bottom and b.y < a.bottom


def _crosses(p: Point, q: Point, r: Rect) -> bool:
    """Whether segment p-q enters the inside of *r* (touching is fine)."""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    low, high = 0.0, 1.0
    eps = 0.01
    for step, room in (
        (-dx, x0 - (r.x + eps)),
        (dx, r.right - eps - x0),
        (-dy, y0 - (r.y + eps)),
        (dy, r.bottom - eps - y0),
    ):
        if step == 0:
            if room < 0:
                return False
        elif step < 0:
            low = max(low, room / step)
        else:
            high = min(high, room / step)
    return low < high


@pytest.mark.parametrize("graph", GRAPHS)
def test_no_node_overlaps_another(graph: QueryGraph) -> None:
    rects = list(_lay(graph).nodes.values())
    assert len(rects) == len(graph.nodes)
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not _overlap(a, b)


@pytest.mark.parametrize("graph", GRAPHS)
def test_frames_hold_their_nodes_and_no_other(graph: QueryGraph) -> None:
    result = _lay(graph)
    for box in result.groups:
        for nid, rect in result.nodes.items():
            if nid in box.nodes:
                assert box.rect.x <= rect.x and rect.right <= box.rect.right
                assert box.rect.y <= rect.y and rect.bottom <= box.rect.bottom
            else:
                assert not _overlap(box.rect, rect)
        for other in result.groups:
            if other is not box:
                assert not _overlap(box.rect, other.rect)


@pytest.mark.parametrize("graph", GRAPHS)
def test_flows_go_from_a_lower_layer_to_a_higher_one(graph: QueryGraph) -> None:
    result = _lay(graph)
    layer = {n: box.layer for box in result.groups for n in box.nodes}
    group = {n.id: node_group(n)[0] for n in graph.nodes}
    flows = [e for e in graph.edges if e.kind == "flow"]
    for e in flows:
        if group[e.source] != group[e.target]:
            assert layer[e.source] < layer[e.target], e
            assert result.nodes[e.source].right < result.nodes[e.target].x


@pytest.mark.parametrize("graph", GRAPHS)
def test_routes_never_cross_a_node(graph: QueryGraph) -> None:
    result = _lay(graph)
    lines = [
        (graph.edges[i].source, graph.edges[i].target, points)
        for i, points in result.routes.items()
    ]
    lines += [(b.group, b.target, b.points) for b in result.bundles]
    for source, target, points in lines:
        for nid, rect in result.nodes.items():
            if nid in (source, target):
                continue
            for p, q in zip(points, points[1:]):
                assert not _crosses(p, q, rect), (source, target, nid)


@pytest.mark.parametrize("graph", GRAPHS)
def test_same_input_same_output(graph: QueryGraph) -> None:
    sizes = _sizes(graph)
    first = layout(graph, sizes, _anchor)
    assert layout(graph, sizes, _anchor) == first
    shuffled = dict(reversed(list(sizes.items())))
    assert layout(graph, shuffled, _anchor) == first


def test_fifty_nodes_in_under_10_ms() -> None:
    graph = _random_graph(3)
    assert len(graph.nodes) == 50
    sizes = _sizes(graph)
    best = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        layout(graph, sizes, _anchor)
        best = min(best, time.perf_counter() - start)
    assert best < 0.010


def test_layers_of_screen_07() -> None:
    result = _lay(build_query_graph(LTV_ETL))
    layers = {box.key: box.layer for box in result.groups}
    assert layers == {
        "schema:stg": 0,
        "temp": 1,
        "derived": 2,
        "schema:dim": 3,
        "schema:ref": 3,  # only feeds mart: moved next to it
        "schema:mart": 4,
    }
    # the join mart → ref would close a cycle with the UPDATE flow: left out
    assert result.nodes["ref.faixasvalor"].right < result.nodes["mart.clienteltv"].x
    # the MERGE crosses the temp and CTE layers through their gaps
    graph = build_query_graph(LTV_ETL)
    merge = next(i for i, e in enumerate(graph.edges) if e.label == "MERGE")
    assert len(result.routes[merge]) == 6


def test_joins_point_away_from_the_base() -> None:
    result = _lay(build_query_graph(SALES_BY_CUSTOMER))
    layers = {box.key: box.layer for box in result.groups}
    assert layers == {"schema:vendas": 0, "schema:dbo": 1, "result": 2}
    vendas = next(box for box in result.groups if box.key == "schema:vendas")
    assert vendas.nodes[0] == "vendas.pedidos"  # the base


def test_join_from_another_base_turns_around() -> None:
    nodes = [_node("a.x"), _node("b.y")]
    for base, first in (("a.x", "schema:a"), ("b.y", "schema:b")):
        graph = _graph(nodes, [("join", "a.x", "b.y")], {1: base})
        layers = {box.key: box.layer for box in _lay(graph).groups}
        assert layers[first] == 0 and len(set(layers.values())) == 2


def test_cycle_of_flows_is_broken() -> None:
    nodes = [_node("a.x"), _node("#t"), _node("b.y")]
    edges = [("flow", "a.x", "#t"), ("flow", "#t", "b.y"), ("flow", "b.y", "a.x")]
    result = _lay(_graph(nodes, edges))
    assert sorted(box.layer for box in result.groups) == [0, 1, 2]
    assert len(result.routes) == 3


def test_more_than_six_nodes_split_into_columns() -> None:
    names = [f"dbo.t{i}" for i in range(STACK + 2)]
    graph = _graph([_node(n) for n in names], [])
    result = _lay(graph)
    xs = sorted({result.nodes[n].x for n in names})
    assert len(xs) == 2
    assert sum(result.nodes[n].x == xs[0] for n in names) == STACK


def test_widths_are_equal_in_a_column() -> None:
    graph = build_query_graph(SALES_BY_CUSTOMER)
    rects = _lay(graph).nodes
    widths = {
        rects[n].w for n in ("vendas.pedidos", "vendas.clientes", "vendas.pagamentos")
    }
    assert len(widths) == 1


def test_brackets_in_a_frame() -> None:
    nodes = [_node("dbo.a", base=True), _node("dbo.b"), _node("dbo.c")]
    edges = [
        ("join", "dbo.a", "dbo.b"),
        ("join", "dbo.a", "dbo.c"),
        ("flow", "dbo.b", "dbo.c"),
    ]
    graph = _graph(nodes, edges, {1: "dbo.a"})
    result = _lay(graph)
    a, b, c = (result.nodes[n] for n in ("dbo.a", "dbo.b", "dbo.c"))
    ab, ac, bc = (result.routes[i] for i in range(3))
    # joins leave on the right, each in its own lane, the shorter one inside
    assert ab[0] == (a.right, a.y + 43) and ab[-1] == (b.right, b.y + 69)
    assert ab[1][0] == a.right + LANE and ac[1][0] == a.right + 2 * LANE
    # flows on the left
    assert bc[1][0] == b.x - LANE
    # the labels sit in the gaps between nodes, not on them
    assert a.bottom < result.labels[0][1] < b.y
    assert b.bottom < result.labels[1][1] < c.y
    frame = result.groups[0].rect
    assert frame.right >= ac[1][0] and frame.x <= bc[1][0]


def test_flows_into_a_result_leave_once_per_frame() -> None:
    graph = build_query_graph(SALES_BY_CUSTOMER)
    result = _lay(graph)
    bundles = {b.group: b for b in result.bundles}
    assert set(bundles) == {"schema:vendas", "schema:dbo"}
    assert len(bundles["schema:vendas"].edges) == 3
    frame = next(box.rect for box in result.groups if box.key == "schema:vendas")
    assert bundles["schema:vendas"].points[0] == (frame.right, frame.y + 14)
    target = result.nodes["result:1"]
    assert bundles["schema:vendas"].points[-1] == (target.x, target.y + 15)
    bundled = {i for b in result.bundles for i in b.edges}
    assert not bundled & set(result.routes)
    assert set(result.routes) | bundled == set(range(len(graph.edges)))


def test_edges_meet_the_anchored_rows() -> None:
    nodes = [_node("a.x", columns=4), _node("b.y", columns=4)]
    graph = _graph(nodes, [("join", "a.x", "b.y")], {1: "a.x"})
    graph.edges[0].source_column = "C3"
    result = _lay(graph)
    x, y = result.nodes["a.x"], result.nodes["b.y"]
    assert result.routes[0][0] == (x.right, x.y + 43 + 26 * 3)
    assert result.routes[0][-1] == (y.x, y.y + 43 + 26)
    plain = layout(graph, _sizes(graph))
    x = plain.nodes["a.x"]
    assert plain.routes[0][0] == (x.right, x.y + x.h / 2)


def test_starts_at_the_origin_and_holds_everything() -> None:
    result = _lay(_random_graph(5))
    rects = [*result.nodes.values(), *(box.rect for box in result.groups)]
    points = [p for r in result.routes.values() for p in r]
    assert min(r.x for r in rects) == 0
    assert min([r.y for r in rects] + [y for _, y in points]) == 0
    width, height = result.size
    assert max(r.right for r in rects) <= width
    assert max([r.bottom for r in rects] + [y for _, y in points]) <= height


def test_missing_sizes_and_empty_graph() -> None:
    graph = _graph([_node("a.x"), _node("result:1")], [("flow", "a.x", "result:1")])
    result = layout(graph, {})
    assert result.nodes["a.x"].w == 168 and result.nodes["a.x"].h == 56
    empty = layout(QueryGraph([], [], [], []), {})
    assert empty.nodes == {} and empty.size == (0.0, 0.0)


@pytest.mark.parametrize(
    "node, key",
    [
        ("vendas.pedidos", "schema:vendas"),
        ("#t", "temp"),
        ("cte:1:a", "derived"),
        ("result:1", "result"),
        ("Clientes", "tables"),
    ],
)
def test_node_groups(node: str, key: str) -> None:
    assert node_group(_node(node))[0] == key
