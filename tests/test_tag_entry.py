from __future__ import annotations

import pytest

from sqlshelf.core.tag_entry import normalize_tag, split_tags, suggest_tags

COUNTS = [
    ("relatorio", 4),
    ("bi", 2),
    ("cte", 2),
    ("mensal", 1),
    ("relatorio-mensal", 1),
    ("dimensão", 3),
]


@pytest.mark.parametrize(
    ("raw", "tag"),
    [
        ("BI", "bi"),
        ("  #Vendas ", "vendas"),
        ("# cte", "cte"),
        ("", ""),
        ("#", ""),
    ],
)
def test_normalize_tag(raw: str, tag: str) -> None:
    assert normalize_tag(raw) == tag


@pytest.mark.parametrize(
    ("raw", "tags"),
    [
        ("bi, cte", ["bi", "cte"]),
        ("bi cte  #Vendas", ["bi", "cte", "vendas"]),
        ("bi,,BI, bi", ["bi"]),
        (" , ", []),
        ("", []),
    ],
)
def test_split_tags(raw: str, tags: list[str]) -> None:
    assert split_tags(raw) == tags


def test_suggestions_contain_the_text_and_prefixes_come_first() -> None:
    assert suggest_tags(COUNTS, "mens", []) == [
        ("mensal", 1),
        ("dimensão", 3),
        ("relatorio-mensal", 1),
    ]


def test_suggestions_ignore_case_and_accents() -> None:
    assert suggest_tags(COUNTS, "DIMENSAO", []) == [("dimensão", 3)]
    assert suggest_tags(COUNTS, "#Rel", []) == [
        ("relatorio", 4),
        ("relatorio-mensal", 1),
    ]


def test_suggestions_leave_out_the_chosen_tags() -> None:
    assert suggest_tags(COUNTS, "rel", ["Relatorio"]) == [("relatorio-mensal", 1)]


def test_nothing_typed_suggests_the_most_used_tags() -> None:
    assert suggest_tags(COUNTS, "", ["bi"], limit=3) == [
        ("relatorio", 4),
        ("dimensão", 3),
        ("cte", 2),
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_below_one_suggests_nothing(limit: int) -> None:
    assert suggest_tags(COUNTS, "", [], limit=limit) == []


def test_no_match_suggests_nothing() -> None:
    assert suggest_tags(COUNTS, "xyz", []) == []
