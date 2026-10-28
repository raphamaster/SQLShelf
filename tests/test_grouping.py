from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from sqlshelf.core.grouping import (
    date_bucket,
    display_name,
    folder_label,
    group_by_date,
    group_by_folder,
    group_label,
    modified_label,
)
from sqlshelf.core.i18n import set_language
from sqlshelf.core.models import SearchResult

MONDAY = 1
SUNDAY = 7

# 2026-10-22 is a Thursday.
THURSDAY = datetime(2026, 10, 22, 10, 0)


def _ts(
    year: int, month: int, day: int, hour: int, minute: int, second: int = 0
) -> float:
    return datetime(year, month, day, hour, minute, second).timestamp()


def _result(rel_path: str, mtime: float = 0, *, title: str = "", root: str = ""):
    return SearchResult(
        query_id=0,
        rel_path=rel_path,
        title=title or Path(rel_path).stem,
        description="",
        snippet="",
        rank=0.0,
        folder=Path(root) if root else None,
        file_mtime=int(mtime),
    )


@pytest.mark.parametrize(
    ("now", "mtime", "bucket"),
    [
        # Midnight is the boundary between today and yesterday.
        (datetime(2026, 10, 22, 0, 0, 30), _ts(2026, 10, 22, 0, 0, 0), "today"),
        (datetime(2026, 10, 22, 0, 0, 30), _ts(2026, 10, 21, 23, 59, 59), "yesterday"),
        (THURSDAY, _ts(2026, 10, 20, 23, 59), "this_week"),
        # A clock ahead of ours still lands in today.
        (THURSDAY, _ts(2026, 10, 23, 8, 0), "today"),
    ],
)
def test_date_bucket_around_midnight(now: datetime, mtime: float, bucket: str) -> None:
    assert date_bucket(mtime, now, MONDAY) == bucket


@pytest.mark.parametrize(
    ("first_weekday", "mtime", "bucket"),
    [
        (MONDAY, _ts(2026, 10, 19, 0, 0), "this_week"),
        (MONDAY, _ts(2026, 10, 18, 23, 59), "this_month"),
        (SUNDAY, _ts(2026, 10, 18, 0, 0), "this_week"),
        (SUNDAY, _ts(2026, 10, 17, 23, 59), "this_month"),
    ],
)
def test_date_bucket_follows_the_first_day_of_the_week(
    first_weekday: int, mtime: float, bucket: str
) -> None:
    assert date_bucket(mtime, THURSDAY, first_weekday) == bucket


def test_yesterday_wins_over_this_week_on_the_first_weekday() -> None:
    monday = datetime(2026, 10, 19, 9, 0)
    assert date_bucket(_ts(2026, 10, 18, 12, 0), monday, MONDAY) == "yesterday"
    assert date_bucket(_ts(2026, 10, 17, 12, 0), monday, MONDAY) == "this_month"


@pytest.mark.parametrize(
    ("first_weekday", "mtime", "bucket"),
    [
        (MONDAY, _ts(2026, 9, 30, 12, 0), "yesterday"),
        # The week began in September: still this week, not "older".
        (MONDAY, _ts(2026, 9, 28, 0, 0), "this_week"),
        (MONDAY, _ts(2026, 9, 27, 12, 0), "older"),
        (SUNDAY, _ts(2026, 9, 27, 12, 0), "this_week"),
        (SUNDAY, _ts(2026, 9, 26, 12, 0), "older"),
    ],
)
def test_date_bucket_across_a_month_change(
    first_weekday: int, mtime: float, bucket: str
) -> None:
    first_of_october = datetime(2026, 10, 1, 9, 0)  # a Thursday
    assert date_bucket(mtime, first_of_october, first_weekday) == bucket


def test_this_month_and_older() -> None:
    assert date_bucket(_ts(2026, 10, 2, 12, 0), THURSDAY, MONDAY) == "this_month"
    assert date_bucket(_ts(2026, 9, 30, 12, 0), THURSDAY, MONDAY) == "older"
    assert date_bucket(_ts(2025, 10, 22, 12, 0), THURSDAY, MONDAY) == "older"


def test_an_unknown_mtime_is_older() -> None:
    assert date_bucket(0, THURSDAY, MONDAY) == "older"


def test_group_by_date_skips_empty_buckets_and_keeps_order() -> None:
    results = [
        _result("a.sql", _ts(2026, 10, 22, 9, 0)),
        _result("b.sql", _ts(2026, 10, 22, 8, 0)),
        _result("c.sql", _ts(2026, 9, 1, 8, 0)),
    ]
    groups = group_by_date(results, THURSDAY, MONDAY)
    assert [(key, [r.rel_path for r in items]) for key, items in groups] == [
        ("today", ["a.sql", "b.sql"]),
        ("older", ["c.sql"]),
    ]
    reversed_groups = group_by_date(results, THURSDAY, MONDAY, oldest_first=True)
    assert [key for key, _ in reversed_groups] == ["older", "today"]


def test_ranked_date_groups_follow_their_best_result() -> None:
    # Sorted by relevance: the best match is old, so "older" leads.
    results = [
        _result("best.sql", _ts(2026, 9, 1, 8, 0)),
        _result("next.sql", _ts(2026, 10, 22, 9, 0)),
        _result("last.sql", _ts(2026, 8, 1, 8, 0)),
    ]
    groups = group_by_date(results, THURSDAY, MONDAY, ranked=True)
    assert [(key, [r.rel_path for r in items]) for key, items in groups] == [
        ("older", ["best.sql", "last.sql"]),
        ("today", ["next.sql"]),
    ]


@pytest.mark.parametrize(
    ("root", "rel_path", "label"),
    [
        ("D:/Querys", "relatorios/vendas.sql", "Querys › relatorios"),
        ("D:/Querys", "vendas.sql", "Querys"),
        ("D:/Querys", "a/b/c.sql", "Querys › a › b"),
        ("D:/Querys", "a\\b\\c.sql", "Querys › a › b"),
        ("", "relatorios/vendas.sql", "relatorios"),
    ],
)
def test_folder_label(root: str, rel_path: str, label: str) -> None:
    assert folder_label(Path(root) if root else None, rel_path) == label


def test_group_by_folder_follows_the_first_appearance() -> None:
    results = [
        _result("relatorios/a.sql", root="D:/Querys"),
        _result("vendas/b.sql", root="D:/sqlserver-lib"),
        _result("relatorios/c.sql", root="D:/Querys"),
        _result("d.sql", root="D:/SCRIPTS"),
    ]
    groups = group_by_folder(results)
    assert [(key, [r.rel_path for r in items]) for key, items in groups] == [
        ("Querys › relatorios", ["relatorios/a.sql", "relatorios/c.sql"]),
        ("sqlserver-lib › vendas", ["vendas/b.sql"]),
        ("SCRIPTS", ["d.sql"]),
    ]


@pytest.mark.parametrize(
    ("rel_path", "title", "expected"),
    [
        ("relatorios/vendas.sql", "Vendas por cliente", ("Vendas por cliente", True)),
        ("teste_005.sql", "", ("teste_005.sql", False)),
        ("sub/teste_004.sql", "teste_004", ("teste_004.sql", False)),
    ],
)
def test_display_name(rel_path: str, title: str, expected: tuple[str, bool]) -> None:
    assert display_name(_result(rel_path, title=title)) == expected


def test_modified_label() -> None:
    set_language("en")
    assert modified_label(_ts(2026, 10, 22, 9, 42), THURSDAY) == "Today 09:42"
    assert modified_label(_ts(2026, 10, 21, 19, 31), THURSDAY) == "Yesterday 19:31"
    assert modified_label(_ts(2026, 10, 17, 8, 0), THURSDAY) == "17/10/2026"
    assert modified_label(0, THURSDAY) == ""


@pytest.mark.parametrize(
    ("language", "key", "label"),
    [
        ("en", "date:today", "Today"),
        ("en", "date:this_week", "This week"),
        ("pt-BR", "date:older", "Mais antigos"),
        ("en", "folder:Querys › reports", "Querys › reports"),
    ],
)
def test_group_label(language: str, key: str, label: str) -> None:
    set_language(language)
    try:
        assert group_label(key) == label
    finally:
        set_language("en")
