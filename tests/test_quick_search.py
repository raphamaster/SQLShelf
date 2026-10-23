from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from sqlshelf.core.models import SearchResult
from sqlshelf.core.quick_search import (
    MAX_RESULTS,
    action_matches,
    cycle_operator,
    filter_results,
    name_spans,
)


def _result(
    rel_path: str,
    title: str = "",
    *,
    tags: list[str] | None = None,
    tables: list[str] | None = None,
    mtime: int = 0,
) -> SearchResult:
    return SearchResult(
        query_id=0,
        rel_path=rel_path,
        title=title,
        description="",
        snippet="",
        rank=0.0,
        tags=tags or [],
        folder=Path("D:/Querys"),
        tables=tables or [],
        file_mtime=mtime,
    )


EVOLUTION = _result(
    "relatorios/evolucao.sql",
    "Evolução Mensal de Vendas",
    tags=["bi", "relatório"],
    tables=["Pedidos", "ItensPedido"],
    mtime=300,
)
BALANCE = _result(
    "financeiro/saldo.sql",
    "Saldo — Contas a Receber",
    tags=["contas"],
    tables=["Titulos"],
    mtime=200,
)
ACCESS = _result(
    "auditoria/acessos.sql",
    "Acessos por Usuário",
    tags=["auditoria"],
    tables=["Logins"],
    mtime=100,
)
UNTITLED = _result("testes/teste_005.sql", "teste_005", mtime=400)
ALL = [ACCESS, EVOLUTION, UNTITLED, BALANCE]


def _paths(results: list[SearchResult]) -> list[str]:
    return [r.rel_path for r in results]


# ── filter_results ──────────────────────────────────────────────────────────


def test_no_text_lists_everything_newest_first() -> None:
    assert filter_results(ALL, "") == [UNTITLED, EVOLUTION, BALANCE, ACCESS]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("evol", [EVOLUTION]),
        ("EVOLUCAO", [EVOLUTION]),  # case and accents ignored
        ("usuario", [ACCESS]),
        ("ensa", [EVOLUTION]),  # inside a word
        ("vendas mensal", [EVOLUTION]),  # every word, in any order
        ("vendas saldo", []),
        ("financeiro", [BALANCE]),  # the path
        ("relatorio", [EVOLUTION]),  # a tag, without its accent
        ("teste_005", [UNTITLED]),  # the file name of an untitled query
        ("  evol  ", [EVOLUTION]),
    ],
)
def test_words_filter_the_name_path_and_tags(
    text: str, expected: list[SearchResult]
) -> None:
    assert filter_results(ALL, text) == expected


def test_name_matches_come_before_other_matches() -> None:
    # "auditoria" is a tag and a folder of ACCESS, and in the title of AUDIT.
    audit = _result("x/audit.sql", "Auditoria de Pedidos", mtime=50)
    assert _paths(filter_results([ACCESS, audit], "auditoria")) == [
        "x/audit.sql",
        "auditoria/acessos.sql",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("table:pedidos", [EVOLUTION]),
        ("table:Pedido", []),  # a whole table name
        ("tag:relatorio", [EVOLUTION]),
        ("tag:contas table:titulos", [BALANCE]),
        ("tag:bi saldo", []),
        ("col:Id evol", [EVOLUTION]),  # no columns here: col: is ignored
    ],
)
def test_operators_narrow_the_list(text: str, expected: list[SearchResult]) -> None:
    assert filter_results(ALL, text) == expected


def test_date_keeps_the_queries_of_that_day() -> None:
    day = datetime(2026, 9, 25, 14, 30)
    other = datetime(2026, 9, 26, 0, 0, 1)
    on_day = _result("a.sql", "A", mtime=int(day.timestamp()))
    next_day = _result("b.sql", "B", mtime=int(other.timestamp()))
    assert filter_results([on_day, next_day], "date:25/09/2026") == [on_day]
    # A date that does not parse filters nothing, as in the search box.
    assert len(filter_results([on_day, next_day], "date:yesterday")) == 2


def test_the_list_is_capped() -> None:
    many = [_result(f"q{i}.sql", f"Query {i}", mtime=i) for i in range(80)]
    shown = filter_results(many, "query")
    assert len(shown) == MAX_RESULTS
    assert shown[0].rel_path == "q79.sql"
    assert len(filter_results(many, "", limit=5)) == 5


# ── name_spans ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "text", "spans"),
    [
        ("Evolução Mensal", "evol", [(0, 4)]),
        ("Evolução Mensal", "EVOLUCAO", [(0, 8)]),
        ("Evolução Mensal", "mensal evol", [(0, 4), (9, 15)]),
        ("Evolução Mensal", "volu evol", [(0, 5)]),  # overlapping, merged
        ("Evolução Mensal", "ensa", [(10, 14)]),
        ("Evolução Mensal", "table:evol", []),  # operator values are not marked
        ("Evolução Mensal", "xyz", []),
        ("Evolução Mensal", "", []),
    ],
)
def test_name_spans(name: str, text: str, spans: list[tuple[int, int]]) -> None:
    assert name_spans(name, text) == spans


# ── action_matches ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "matches"),
    [
        ("", True),
        ("nova", True),
        ("CONSULTA nova", True),
        ("onsul", True),
        ("pasta", False),
        ("tag:bi", False),
        ("tag:bi nova", False),
        ("table:", False),  # free text that is not in the label
    ],
)
def test_actions_follow_the_plain_words(text: str, matches: bool) -> None:
    assert action_matches("Nova consulta", text) is matches


def test_actions_ignore_accents() -> None:
    assert action_matches("Reindexar índice", "indice")


# ── cycle_operator ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Pedidos", "table:Pedidos"),
        ("table:Pedidos", "tag:Pedidos"),
        ("tag:Pedidos", "Pedidos"),
        ("evol Pedidos", "evol table:Pedidos"),
        ("tag:bi Pedidos", "tag:bi table:Pedidos"),
        ("Pedidos ", "Pedidos "),  # nothing being typed
        ("", ""),
        ("col:Id", "col:Id"),
        ("date:25/09/2026", "date:25/09/2026"),
        ("table:", "table:"),
    ],
)
def test_tab_cycles_the_last_word(text: str, expected: str) -> None:
    assert cycle_operator(text) == expected


def test_three_tabs_come_back_to_the_word() -> None:
    text = "vendas Pedidos"
    for _ in range(3):
        text = cycle_operator(text)
    assert text == "vendas Pedidos"
