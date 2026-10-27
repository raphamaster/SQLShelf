from __future__ import annotations

import pytest
from test_sql_segments import (
    DBA_SCRIPT,
    IF_ELSE,
    LOAD_PROCEDURE,
    LTV_ETL,
    SALES_BY_CUSTOMER,
    SYSTEM_HEALTH,
)

from sqlshelf.core import query_graph
from sqlshelf.core.query_graph import (
    FilterInfo,
    GraphColumn,
    GraphEdge,
    GraphNode,
    QueryGraph,
    build_query_graph,
)

FILTERS = """\
SELECT c.Nome, SUM(pg.Valor) AS Pago
FROM vendas.Pedidos p
INNER JOIN vendas.Clientes c ON c.ClienteID = p.ClienteID AND c.Ativo = 1
LEFT JOIN vendas.Pagamentos pg ON pg.PedidoID = p.PedidoID
WHERE p.DataPedido >= @inicio
  AND p.Status NOT IN ('C', 'D')
GROUP BY c.Nome
HAVING SUM(pg.Valor) > 1000
"""


def _node(graph: QueryGraph, nid: str) -> GraphNode:
    return next(n for n in graph.nodes if n.id == nid)


def _column(node: GraphNode, name: str) -> GraphColumn:
    return next(c for c in node.columns if c.name == name)


def _joins(graph: QueryGraph) -> list[GraphEdge]:
    return [e for e in graph.edges if e.kind == "join"]


def _flows(graph: QueryGraph) -> set[tuple[str, str, str]]:
    return {(e.source, e.target, e.label) for e in graph.edges if e.kind == "flow"}


def _filters(graph: QueryGraph) -> list[tuple[str, str, FilterInfo]]:
    return [
        (n.id, c.name, f) for n in graph.nodes for c in n.columns for f in c.filters
    ]


def _text(body: str, info: FilterInfo | GraphEdge) -> str:
    assert info.span is not None
    return body[info.span.start : info.span.end]


# --- The scripts of screens 06 to 08 (cases 1 to 6) -------------------------


def test_select_with_joins_and_filters() -> None:
    graph = build_query_graph(SALES_BY_CUSTOMER)

    assert [(n.id, n.kind) for n in graph.nodes] == [
        ("vendas.pedidos", "table"),
        ("vendas.clientes", "table"),
        ("vendas.pagamentos", "table"),
        ("dbo.regiao", "table"),
        ("result:1", "result"),
    ]
    assert {
        (e.source, e.source_column, e.target, e.target_column, e.label, e.optional_side)
        for e in _joins(graph)
    } == {
        ("vendas.pedidos", "ClienteID", "vendas.clientes", "ClienteID", "INNER", None),
        (
            "vendas.pedidos",
            "PedidoID",
            "vendas.pagamentos",
            "PedidoID",
            "LEFT",
            "target",
        ),
        ("vendas.clientes", "RegiaoID", "dbo.regiao", "RegiaoID", "LEFT", "target"),
    }
    assert [(n, c, f.clause, f.predicate) for n, c, f in _filters(graph)] == [
        ("vendas.pedidos", "DataPedido", "where", ">= @inicio"),
        ("vendas.pedidos", "Status", "where", "<> 'C'"),
    ]
    pedidos = _node(graph, "vendas.pedidos")
    assert pedidos.is_base and pedidos.schema == "vendas" and pedidos.aliases == ["p"]
    assert graph.statements[0].base == "vendas.pedidos"
    assert graph.statements[0].status == "ok"
    result = _node(graph, "result:1")
    assert [(c.name, c.expression) for c in result.columns] == [
        ("ClienteID", "c.ClienteID"),
        ("Nome", "c.Nome"),
        ("Regiao", "r.Nome"),
        ("Pedidos", "COUNT(p.PedidoID)"),
        ("Pago", "SUM(pg.Valor)"),
    ]
    assert {e.target for e in graph.edges if e.kind == "flow"} == {"result:1"}


def test_system_health_script() -> None:
    graph = build_query_graph(SYSTEM_HEALTH)
    temp = "#systemhealthsessiondata"

    assert [(s.kind, s.status) for s in graph.statements] == [
        ("SELECT", "ok"),
        ("SELECT", "ok"),
    ]
    inner = [e for e in _joins(graph) if e.label == "INNER"]
    assert len(inner) == 1
    assert {
        (inner[0].source, inner[0].source_column),
        (inner[0].target, inner[0].target_column),
    } == {
        ("sys.dm_xe_sessions", "address"),
        ("sys.dm_xe_session_targets", "event_session_address"),
    }
    assert inner[0].condition == "xe.address = xet.event_session_address"
    assert {
        ("sys.dm_xe_session_targets", temp, "INTO"),
        ("sys.dm_xe_sessions", temp, "INTO"),
    } <= _flows(graph)
    node = _node(graph, temp)
    assert (node.kind, node.name, node.statements) == (
        "temp",
        "#SystemHealthSessionData",
        {1, 2},
    )
    assert _column(node, "XMLDATA").expression == "CAST(xet.target_data AS XML)"
    apply = next(e for e in _joins(graph) if e.label == "CROSS APPLY")
    derived = _node(graph, apply.target)
    assert (derived.kind, derived.name) == ("derived", "T")
    assert (apply.source, apply.source_column) == (temp, "XMLDATA")
    assert _text(SYSTEM_HEALTH, apply).startswith("CROSS APPLY a.XMLDATA.nodes(")
    assert ("cte:2:cte_healthsession", "result:2", "") in _flows(graph)


def test_etl_script_shares_nodes_between_statements() -> None:
    graph = build_query_graph(LTV_ETL)

    assert [s.kind for s in graph.statements] == ["SELECT", "MERGE", "INSERT", "UPDATE"]
    assert _node(graph, "#pedidos_validos").statements == {1, 3}
    assert ("stg.clientes", "dim.cliente", "MERGE") in _flows(graph)
    assert not [
        e
        for e in _joins(graph)
        if {e.source, e.target} == {"stg.clientes", "dim.cliente"}
    ]
    assert not [n for n in graph.nodes if n.name.casefold() == "t"]
    ltv = _node(graph, "mart.clienteltv")
    assert ltv.writes == ["INSERT", "UPDATE"]
    assert ltv.aliases == ["t"]
    between = next(e for e in _joins(graph) if e.label == "BETWEEN")
    assert (between.source, between.source_column) == ("mart.clienteltv", "ValorVida")
    assert (between.target, between.target_column) == ("ref.faixasvalor", "Minimo")
    assert _text(LTV_ETL, between) == "t.ValorVida BETWEEN f.Minimo AND f.Maximo"


def test_procedure_target_is_also_a_source() -> None:
    graph = build_query_graph(LOAD_PROCEDURE)

    assert [(s.kind, s.status) for s in graph.statements] == [
        ("SELECT", "ok"),
        ("INSERT", "ok"),
    ]
    fact = [n for n in graph.nodes if n.id == "dbo.fact"]
    assert len(fact) == 1 and fact[0].writes == ["INSERT"]
    assert any(e.target == "dbo.fact" and e.label == "LEFT" for e in _joins(graph))
    assert ("#t", "dbo.fact", "INSERT") in _flows(graph)


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        (IF_ELSE, ["UPDATE", "SELECT"]),
        (DBA_SCRIPT, ["SELECT", "SELECT"]),
    ],
)
def test_control_flow_and_ddl_leave_only_dml(body: str, kinds: list[str]) -> None:
    graph = build_query_graph(body)

    assert [s.kind for s in graph.statements] == kinds
    assert {s.status for s in graph.statements} == {"ok"}


# --- Short cases (7 to 16) ----------------------------------------------------


@pytest.mark.parametrize(
    ("body", "labels"),
    [
        ("SELECT a.x FROM t1 a, t2 b WHERE a.id = b.id", {("t1", "t2", "WHERE")}),
        (
            "SELECT c.Nome FROM dbo.C c"
            " WHERE EXISTS (SELECT 1 FROM dbo.O o WHERE o.cid = c.id)"
            " AND c.r IN (SELECT r.id FROM dbo.R r)",
            {("dbo.c", "dbo.o", "EXISTS"), ("dbo.c", "dbo.r", "IN")},
        ),
        (
            "SELECT a.x FROM dbo.A a"
            " WHERE NOT EXISTS (SELECT 1 FROM dbo.B b WHERE b.aid = a.id)",
            {("dbo.a", "dbo.b", "NOT EXISTS")},
        ),
        (
            "SELECT a.x FROM dbo.A a CROSS JOIN dbo.B b",
            {("dbo.a", "dbo.b", "CROSS")},
        ),
    ],
)
def test_join_edge_labels(body: str, labels: set[tuple[str, str, str]]) -> None:
    graph = build_query_graph(body)

    assert {(e.source, e.target, e.label) for e in _joins(graph)} == labels


def test_self_join_gives_two_nodes() -> None:
    graph = build_query_graph(
        "SELECT e.nome, m.nome FROM dbo.Funcionario e"
        " LEFT JOIN dbo.Funcionario m ON m.id = e.gerente_id"
    )

    tables = [n for n in graph.nodes if n.kind == "table"]
    assert [(n.name, n.aliases) for n in tables] == [
        ("Funcionario", ["e"]),
        ("Funcionario", ["m"]),
    ]
    (edge,) = _joins(graph)
    assert (edge.source, edge.target, edge.label) == (
        tables[0].id,
        tables[1].id,
        "LEFT",
    )


def test_apply_gives_derived_nodes() -> None:
    graph = build_query_graph(
        "SELECT a.id, rv.v, f.z FROM dbo.A a"
        " OUTER APPLY (SELECT TOP 1 v.v FROM dbo.V v WHERE v.aid = a.id"
        " ORDER BY v.d DESC) rv"
        " CROSS APPLY dbo.fn(a.id) f"
    )

    applies = {
        e.label: (e.source, e.source_column, _node(graph, e.target), e.optional_side)
        for e in _joins(graph)
    }
    assert set(applies) == {"OUTER APPLY", "CROSS APPLY"}
    source, column, rv, optional = applies["OUTER APPLY"]
    assert (source, column, rv.kind, rv.name, optional) == (
        "dbo.a",
        "id",
        "derived",
        "rv",
        "target",
    )
    source, column, fn, optional = applies["CROSS APPLY"]
    assert (source, column, fn.kind, fn.name, optional) == (
        "dbo.a",
        "id",
        "derived",
        "f",
        None,
    )
    assert ("dbo.v", rv.id, "") in _flows(graph)


def test_temp_tables_variables_and_brackets() -> None:
    graph = build_query_graph(
        "INSERT INTO ##g SELECT * FROM @t v JOIN [Order Details] od ON od.id = v.id"
    )

    assert [(n.name, n.kind) for n in graph.nodes] == [
        ("##g", "temp"),
        ("@t", "temp"),
        ("Order Details", "table"),
    ]
    assert [c.name for c in _node(graph, "##g").columns] == ["*"]
    assert _flows(graph) == {
        ("@t", "##g", "INSERT"),
        ("order details", "##g", "INSERT"),
    }


@pytest.mark.parametrize(
    "body",
    [
        "EXEC(@sql)",
        "EXECUTE ('SELECT 1')",
        "DECLARE @sql nvarchar(max) = N'SELECT 1'\nEXEC sp_executesql @sql",
        "EXEC dbo.sp_executesql @sql",
        "EXEC [sp_executesql] @sql",
    ],
)
def test_dynamic_sql_gives_a_note(body: str) -> None:
    graph = build_query_graph(body)

    assert graph.notes == ["dynamic_sql"]
    assert graph.nodes == []


def test_procedure_call_is_not_dynamic_sql() -> None:
    assert build_query_graph("EXEC dbo.usp_carga @data = '2026-01-01'").notes == []


def test_unreadable_statements_cost_only_themselves() -> None:
    body = (
        "SELECT x.a FROM dbo.X x\n"
        "GO\n"
        "INSERT INTO #r EXEC dbo.usp @p = 1\n"
        "GO\n"
        "\n"
        "SELECT FROM WHERE (((\n"
    )
    graph = build_query_graph(body)

    assert [(s.status, s.line) for s in graph.statements] == [
        ("ok", 1),
        ("approximate", 3),
        ("skipped", 6),
    ]
    assert _node(graph, "#r").writes == ["INSERT"]
    assert _node(graph, "dbo.x").columns[0].name == "a"
    assert graph.for_statement(3).nodes == []


def test_batch_the_tokenizer_rejects_is_skipped() -> None:
    body = "SELECT a.x FROM dbo.A a\nGO\nSELECT 'never closed FROM dbo.B\n"
    graph = build_query_graph(body)

    assert [(s.status, s.line, s.kind) for s in graph.statements] == [
        ("ok", 1, "SELECT"),
        ("skipped", 3, ""),
    ]
    assert [s.issue for s in graph.statements] == [None, None]  # no parser said where
    span = graph.statements[1].span
    assert body[span.start : span.end] == "SELECT 'never closed FROM dbo.B"


TYPO = (
    "select u.id, emp.nome from usuario u\n"
    "inner join signatario s on s.id = u.id\n"
    "inner join empresa e on e.id = u.empresacontextoid\n"
    "innerjoin signatario emp on emp.id = e.id\n"
)


@pytest.mark.parametrize(
    ("body", "near", "line"),
    [
        (TYPO, "innerjoin", 4),
        (TYPO.replace("\n", "\r\n"), "innerjoin", 4),
        ("SELECT 1 AS a INTO #x;\n\nGO\nUPDATE t\n  SET a =\nFROM x\n", "FROM", 6),
        ("SELECT a FROM t\nWHERE x = 'é' AND\n  y = = 2", "=", 3),
        ("SELECT a FROM t;\nSELECT FROM WHERE ((;", "WHERE", 2),
        ("SELECT a FROM t WHERE x = (1 + '" + "x" * 80 + "'", "'" + "x" * 38, 1),
    ],
)
def test_a_statement_not_fully_read_says_where_sqlglot_stopped(
    body: str, near: str, line: int
) -> None:
    unread = [s for s in build_query_graph(body).statements if s.status != "ok"]

    assert len(unread) == 1
    issue = unread[0].issue
    assert issue is not None
    assert issue.line == line
    assert issue.near.rstrip("…") == near  # a long token is clipped
    assert body[issue.span.start : issue.span.end].startswith(near)


@pytest.mark.parametrize(
    ("body", "columns"),
    [
        ("SELECT Nome FROM dbo.A", {"dbo.a": ["Nome"]}),
        ("SELECT Nome, Valor FROM dbo.A a JOIN dbo.B b ON a.id = b.id", {}),
        (
            "SELECT Nome FROM dbo.A a JOIN (SELECT id, Nome FROM dbo.B) b ON a.id = b.id",
            {"derived:1:b": ["Nome"], "dbo.b": ["id", "Nome"]},
        ),
    ],
)
def test_unqualified_columns(body: str, columns: dict[str, list[str]]) -> None:
    graph = build_query_graph(body)

    for node in graph.nodes:
        if node.kind == "result":
            continue
        found = [c.name for c in node.columns if "select" in c.roles]
        assert found == columns.get(node.id, []), node.id


def test_backticks_read_as_bigquery() -> None:
    graph = build_query_graph("SELECT o.id FROM `proj.ds.orders` o")

    node = _node(graph, "proj.ds.orders")
    assert (node.schema, node.name) == ("proj.ds", "orders")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "   \n\t",
        "-- just a comment\n/* and another */",
        "CREATE TABLE dbo.X (id int)\nDROP TABLE dbo.Y",
        "SELECT 'never closed",
        "SELECT )",
        "DELETE",
        "SELECT 1 +",
        "WITH x AS (SELECT 1)",
        "garbage ((( ''' [[[ ;;; GO",
        "\x00\x01 SELECT",
    ],
)
def test_nothing_readable_gives_an_empty_graph(body: str) -> None:
    graph = build_query_graph(body)

    assert graph.nodes == [] and graph.edges == []


def test_internal_failure_gives_failed_note(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(body: str, dialect: str) -> QueryGraph:
        raise RuntimeError("broken\nsecond line")

    monkeypatch.setattr(query_graph, "_build", boom)
    graph = build_query_graph("SELECT 1")

    assert (graph.nodes, graph.notes, graph.error) == ([], ["failed"], "broken")


# --- Filters (17 to 20) -------------------------------------------------------


def test_filters_of_screen_09() -> None:
    graph = build_query_graph(FILTERS)

    assert [
        (n, c, f.clause, f.predicate, f.in_or, f.join_label, _text(FILTERS, f))
        for n, c, f in _filters(graph)
    ] == [
        (
            "vendas.pedidos",
            "DataPedido",
            "where",
            ">= @inicio",
            False,
            None,
            "p.DataPedido >= @inicio",
        ),
        (
            "vendas.pedidos",
            "Status",
            "where",
            "NOT IN ('C', 'D')",
            False,
            None,
            "p.Status NOT IN ('C', 'D')",
        ),
        ("vendas.clientes", "Ativo", "on", "= 1", False, "INNER", "c.Ativo = 1"),
    ]
    (having,) = _node(graph, "result:1").filters
    assert (having.clause, having.predicate, _text(FILTERS, having)) == (
        "having",
        "SUM(pg.Valor) > 1000",
        "SUM(pg.Valor) > 1000",
    )
    assert not [
        e for e in _joins(graph) if "Ativo" in (e.source_column, e.target_column)
    ]
    assert _column(_node(graph, "vendas.clientes"), "Ativo").roles == {"on"}


@pytest.mark.parametrize(
    ("condition", "column", "predicate"),
    [
        ("p.DataPedido >= @inicio", "DataPedido", ">= @inicio"),
        ("@x <= p.Valor", "Valor", ">= @x"),
        ("10 > p.Valor", "Valor", "< 10"),
        ("p.Status NOT IN ('C', 'D')", "Status", "NOT IN ('C', 'D')"),
        ("p.T IN (1, 2, 3, 4, 5)", "T", "IN (1, 2, 3, …)"),
        ("p.Obs IS NOT NULL", "Obs", "IS NOT NULL"),
        ("p.Obs IS NULL", "Obs", "IS NULL"),
        ("p.Cod LIKE 'A%'", "Cod", "LIKE 'A%'"),
        ("p.Cod NOT LIKE 'A%'", "Cod", "NOT LIKE 'A%'"),
        ("p.N BETWEEN 1 AND 5", "N", "BETWEEN 1 AND 5"),
        ("p.N NOT BETWEEN 1 AND 5", "N", "NOT BETWEEN 1 AND 5"),
        ("YEAR(p.DataPedido) = 2026", "DataPedido", "YEAR(…) = 2026"),
        ("(p.Valor) > 0", "Valor", "> 0"),
        ("p.Valor * 2 > 10", "Valor", "p.Valor * 2 > 10"),
    ],
)
def test_short_form_of_a_filter(condition: str, column: str, predicate: str) -> None:
    body = f"SELECT p.Id FROM dbo.P p WHERE {condition} AND p.Id > 0"
    graph = build_query_graph(body)

    (found,) = [f for _, c, f in _filters(graph) if c == column]
    assert (found.clause, found.predicate, found.in_or) == ("where", predicate, False)
    assert _text(body, found) == condition


def test_filters_inside_or() -> None:
    body = (
        "SELECT p.Id FROM dbo.P p, dbo.C c"
        " WHERE (p.Status = 'A' OR c.Tipo = 'X') AND p.Z <> 0"
    )
    graph = build_query_graph(body)

    found = {c: f for _, c, f in _filters(graph)}
    assert (found["Status"].predicate, found["Status"].in_or) == ("= 'A'", True)
    assert (found["Tipo"].predicate, found["Tipo"].in_or) == ("= 'X'", True)
    assert (
        found["Status"].full == found["Tipo"].full == "p.Status = 'A' OR c.Tipo = 'X'"
    )
    assert _text(body, found["Tipo"]) == "c.Tipo = 'X'"
    assert (found["Z"].predicate, found["Z"].in_or) == ("<> 0", False)
    assert _joins(graph) == []


@pytest.mark.parametrize(
    ("body", "node", "column", "predicate", "write"),
    [
        (
            "UPDATE t SET t.Valor = 0 FROM mart.X t WHERE t.Ativo = 1",
            "mart.x",
            "Ativo",
            "= 1",
            "UPDATE",
        ),
        (
            "DELETE d FROM dbo.T d WHERE d.Data < @corte",
            "dbo.t",
            "Data",
            "< @corte",
            "DELETE",
        ),
        (
            "DELETE FROM dbo.T WHERE Data < @corte",
            "dbo.t",
            "Data",
            "< @corte",
            "DELETE",
        ),
    ],
)
def test_filters_on_update_and_delete_targets(
    body: str, node: str, column: str, predicate: str, write: str
) -> None:
    graph = build_query_graph(body)

    assert [n.id for n in graph.nodes] == [node]
    assert _node(graph, node).writes == [write]
    ((nid, name, found),) = _filters(graph)
    assert (nid, name, found.clause, found.predicate) == (
        node,
        column,
        "where",
        predicate,
    )


# --- Statements, spans and merging ---------------------------------------------


def test_for_statement_keeps_only_that_statement() -> None:
    graph = build_query_graph(LTV_ETL)
    first = graph.for_statement(1)

    assert [s.index for s in first.statements] == [1]
    assert {n.id for n in first.nodes} == {
        "#pedidos_validos",
        "stg.pedidos",
        "stg.pagamentos",
    }
    assert {e.statement for e in first.edges} == {1}
    temp = _node(first, "#pedidos_validos")
    assert temp.statements == {1}
    assert "group by" not in _column(temp, "ClienteID").roles
    assert "group by" in _column(_node(graph, "#pedidos_validos"), "ClienteID").roles
    assert graph.for_statement(9).nodes == []


def test_statement_summaries_and_lines() -> None:
    graph = build_query_graph(LTV_ETL)

    assert [(s.summary, s.line) for s in graph.statements] == [
        ("SELECT … INTO #pedidos_validos", 1),
        ("MERGE dim.Cliente", 8),
        ("INSERT INTO mart.ClienteLtv", 16),
        ("UPDATE mart.ClienteLtv", 27),
    ]
    for info in graph.statements:
        assert LTV_ETL[info.span.start : info.span.end].strip()


def test_spans_point_at_the_text() -> None:
    body = SALES_BY_CUSTOMER
    graph = build_query_graph(body)

    pedidos = _node(graph, "vendas.pedidos")
    assert [body[s.start : s.end] for s in pedidos.spans] == ["vendas.Pedidos"]
    assert [body[s.start : s.end] for s in _column(pedidos, "PedidoID").spans] == [
        "p.PedidoID",
        "p.PedidoID",
    ]
    edge = next(e for e in _joins(graph) if e.target == "dbo.regiao")
    assert _text(body, edge) == "r.RegiaoID = c.RegiaoID"
    temp = _node(build_query_graph(SYSTEM_HEALTH), "#systemhealthsessiondata")
    assert {SYSTEM_HEALTH[s.start : s.end] for s in temp.spans} == {
        "#SystemHealthSessionData"
    }


def test_unqualified_name_joins_the_qualified_one() -> None:
    graph = build_query_graph(
        "SELECT p.id FROM vendas.Pedidos p\nGO\nSELECT x.id FROM Pedidos x"
    )

    tables = [n for n in graph.nodes if n.kind == "table"]
    assert [(n.id, n.statements) for n in tables] == [("vendas.pedidos", {1, 2})]
    assert graph.statements[1].base == "vendas.pedidos"


def test_output_into_flows_on() -> None:
    graph = build_query_graph(
        "UPDATE dbo.F SET x = 1 OUTPUT inserted.id INTO @log WHERE id < 10\n"
        "GO\n"
        "DELETE FROM dbo.Fila OUTPUT deleted.id INTO dbo.Arquivo (id)"
    )

    assert {("dbo.f", "@log", "OUTPUT"), ("dbo.fila", "dbo.arquivo", "OUTPUT")} <= (
        _flows(graph)
    )
    assert _node(graph, "dbo.arquivo").writes == ["INSERT"]


def test_merge_marks_keys_without_join_edge() -> None:
    graph = build_query_graph(
        "MERGE dbo.T AS t USING dbo.S AS s ON t.id = s.id"
        " WHEN MATCHED THEN UPDATE SET t.v = s.v"
        " WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);"
    )

    assert _joins(graph) == []
    assert _flows(graph) == {("dbo.s", "dbo.t", "MERGE")}
    target = _node(graph, "dbo.t")
    assert _column(target, "id").is_key and "on" in _column(target, "id").roles
    assert {"set", "insert"} <= _column(target, "v").roles
    assert graph.statements[0].base == "dbo.s"


def test_cte_and_union_feed_the_result() -> None:
    graph = build_query_graph(
        "WITH a AS (SELECT x.id FROM dbo.X x UNION ALL SELECT y.id FROM dbo.Y y)"
        " SELECT a.id FROM a"
    )

    assert _flows(graph) == {
        ("dbo.x", "cte:1:a", ""),
        ("dbo.y", "cte:1:a", ""),
        ("cte:1:a", "result:1", ""),
    }
    assert _node(graph, "cte:1:a").kind == "cte"


def test_xmlnamespaces_is_ignored() -> None:
    body = "WITH XMLNAMESPACES ('urn:x' AS ns) SELECT t.c FROM dbo.T t"
    graph = build_query_graph(body)

    assert graph.statements[0].status == "ok"
    assert [body[s.start : s.end] for s in _node(graph, "dbo.t").spans] == ["dbo.T"]


def test_variable_assignment_has_no_result() -> None:
    graph = build_query_graph("SELECT @n = COUNT(p.id) FROM dbo.P p")

    assert [n.id for n in graph.nodes] == ["dbo.p"]
    assert _column(graph.nodes[0], "id").roles == {"select"}


# --- Dialects -------------------------------------------------------------------


def test_postgres_upsert_is_one_insert() -> None:
    body = (
        "INSERT INTO sales.totals (id, total)\n"
        "SELECT o.id, o.total::numeric FROM sales.orders o\n"
        "ON CONFLICT (id) DO UPDATE SET total = excluded.total;"
    )
    graph = build_query_graph(body, "postgres")

    assert [(s.kind, s.status) for s in graph.statements] == [("INSERT", "ok")]
    assert _flows(graph) == {("sales.orders", "sales.totals", "INSERT")}


def test_mysql_backticks_and_duplicate_key() -> None:
    body = (
        "INSERT INTO t (a) SELECT `x`.`a` FROM `shop`.`src` x\n"
        "ON DUPLICATE KEY UPDATE a = 2;"
    )
    graph = build_query_graph(body, "mysql")

    assert [(s.kind, s.status) for s in graph.statements] == [("INSERT", "ok")]
    assert _flows(graph) == {("shop.src", "t", "INSERT")}


def test_oracle_select_into_fills_a_variable() -> None:
    body = (
        "DECLARE\n"
        "  v NUMBER;\n"
        "BEGIN\n"
        "  SELECT COUNT(*) INTO v FROM hr.employees e WHERE e.dept_id = 10;\n"
        "END;\n"
        "/"
    )
    graph = build_query_graph(body, "oracle")

    assert [n.id for n in graph.nodes] == ["hr.employees"]
    assert [(s.kind, s.status) for s in graph.statements] == [("SELECT", "ok")]


def test_oracle_q_quote_keeps_spans() -> None:
    body = "SELECT q'[it's]' AS x, e.name FROM hr.employees e WHERE ROWNUM <= 10"
    graph = build_query_graph(body, "oracle")

    node = _node(graph, "hr.employees")
    assert [body[s.start : s.end] for s in node.spans] == ["hr.employees"]


def test_dialect_read_from_the_body() -> None:
    graph = build_query_graph("SELECT o.id::text FROM public.orders o")

    assert [n.id for n in graph.nodes] == ["public.orders", "result:1"]


@pytest.mark.parametrize(
    "body,dialect",
    [
        ("DO $$ BEGIN EXECUTE 'DELETE FROM ' || tbl; END $$;", "postgres"),
        ("BEGIN EXECUTE IMMEDIATE 'TRUNCATE TABLE t'; END;", "oracle"),
        ("BEGIN DBMS_SQL.PARSE(c, q, DBMS_SQL.NATIVE); END;", "oracle"),
        ("PREPARE s FROM @q; EXECUTE s;", "mysql"),
        ("PREPARE s FROM @q; EXECUTE s;", "mariadb"),
        ("EXECUTE IMMEDIATE 'SELECT 1';", "bigquery"),
    ],
)
def test_dynamic_sql_of_each_dialect(body: str, dialect: str) -> None:
    assert build_query_graph(body, dialect).notes == ["dynamic_sql"]


@pytest.mark.parametrize("dialect", ["postgres", "oracle", "mysql", "bigquery"])
def test_dynamic_sql_word_in_a_string_or_comment(dialect: str) -> None:
    body = "-- EXECUTE IMMEDIATE\nSELECT 'PREPARE s FROM x; EXECUTE s' AS word FROM t"
    assert build_query_graph(body, dialect).notes == []
