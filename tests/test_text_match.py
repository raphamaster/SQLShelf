from __future__ import annotations

import pytest

from sqlshelf.core.text_match import fold, highlight_terms, match_spans


@pytest.mark.parametrize(
    ("text", "terms", "spans"),
    [
        # Accents, either side
        ("Variação Diária", ["variacao"], [(0, 8)]),
        ("Variacao Diaria", ["variação"], [(0, 8)]),
        ("Vários", ["va"], [(0, 3)]),
        # Case
        ("PEDIDOS", ["pedidos"], [(0, 7)]),
        ("pedidos", ["PeDiDoS"], [(0, 7)]),
        ("Straße", ["STRASSE"], [(0, 6)]),
        # Several terms
        ("Variação Diária de Pedidos", ["variação", "pedidos"], [(0, 8), (19, 26)]),
        ("a pedido, pedidos", ["ped"], [(2, 5), (10, 13)]),
        # Overlapping matches merge
        ("Pedidos", ["ped", "pedidos"], [(0, 7)]),
        # Only where a word starts, like the FTS prefix search
        ("ItensPedido", ["pedido"], []),
        ("Itens Pedido", ["ped"], [(6, 9)]),
        ("Contas_PF", ["pf"], [(7, 9)]),
        # A term is split into words the way the tokenizer splits it
        ("Contas_PF_Corp", ["contas_pf"], [(0, 6), (7, 9)]),
        ('"Pedidos"', ['"pedidos"'], [(1, 8)]),
        # Nothing to look for
        ("Pedidos", [], []),
        ("Pedidos", ["--"], []),
        ("", ["x"], []),
    ],
)
def test_match_spans(text: str, terms: list[str], spans: list[tuple[int, int]]) -> None:
    assert match_spans(text, terms) == spans


def test_fold_drops_case_and_accents() -> None:
    assert fold("Ação Ñandú") == "acao nandu"


@pytest.mark.parametrize(
    ("search", "terms"),
    [
        ("variação", ["variação"]),
        ("table:Pedidos tag:relatorio variação", ["variação", "Pedidos"]),
        ("col:Id date:01/10/2026", []),
        ("", []),
    ],
)
def test_highlight_terms(search: str, terms: list[str]) -> None:
    assert highlight_terms(search) == terms
