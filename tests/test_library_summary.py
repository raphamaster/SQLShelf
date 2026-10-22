from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sqlshelf.core.index_db import IndexDB
from sqlshelf.core.library_summary import summarize_library
from sqlshelf.core.models import Query


def _index(root: Path, files: dict[str, list[str]]) -> IndexDB:
    """Index *files* (rel_path → tags) under *root*."""
    queries = []
    for rel_path, tags in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SELECT 1", encoding="utf-8")
        queries.append(Query(path=path, title=rel_path, body="SELECT 1", tags=tags))
    db = IndexDB(root)
    db.index_all(queries)
    return db


@pytest.fixture
def library(tmp_path: Path) -> Iterator[dict[Path, IndexDB]]:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    dbs = {
        a: _index(
            a,
            {
                "one.sql": ["bi"],
                "reports/two.sql": ["bi", "finance"],
                "reports/monthly/three.sql": ["finance"],
            },
        ),
        b: _index(b, {"four.sql": ["bi"]}),
    }
    yield dbs
    for db in dbs.values():
        db.close()


def test_totals_span_the_library(library: dict[Path, IndexDB]) -> None:
    a, b = list(library)
    library[a].toggle_favorite("one.sql")
    library[b].toggle_favorite("four.sql")

    summary = summarize_library(library, a / "reports")
    assert summary.total == 4
    assert summary.favorites == 2


def test_trees_are_built_per_library_folder(library: dict[Path, IndexDB]) -> None:
    a, b = list(library)
    summary = summarize_library(library, None)
    assert summary.trees is not None
    assert summary.trees[a].count == 3
    assert [child.name for child in summary.trees[a].children] == ["reports"]
    assert summary.trees[a].children[0].count == 2
    assert summary.trees[b].count == 1


def test_trees_can_be_skipped(library: dict[Path, IndexDB]) -> None:
    assert summarize_library(library, None, with_trees=False).trees is None


def test_tags_merge_across_the_library(library: dict[Path, IndexDB]) -> None:
    assert summarize_library(library, None).tags == [("bi", 3), ("finance", 2)]


@pytest.mark.parametrize(
    ("sub", "expected"),
    [
        ("", [("bi", 2), ("finance", 2)]),
        ("reports", [("finance", 2), ("bi", 1)]),
        ("reports/monthly", [("finance", 1)]),
    ],
)
def test_tags_follow_the_scope(
    library: dict[Path, IndexDB], sub: str, expected: list[tuple[str, int]]
) -> None:
    a = list(library)[0]
    scope = a / sub if sub else a
    assert summarize_library(library, scope).tags == expected


def test_recent_follows_the_scope(library: dict[Path, IndexDB]) -> None:
    a, b = list(library)
    library[a].add_recently_viewed("one.sql")
    library[a].add_recently_viewed("reports/two.sql")
    library[b].add_recently_viewed("four.sql")

    assert summarize_library(library, None).recent == 3
    assert summarize_library(library, a).recent == 2
    assert summarize_library(library, a / "reports").recent == 1
    assert summarize_library(library, b).recent == 1


def test_a_scope_outside_the_library_counts_nothing_scoped(
    library: dict[Path, IndexDB], tmp_path: Path
) -> None:
    summary = summarize_library(library, tmp_path / "elsewhere")
    assert summary.total == 4
    assert summary.tags == []
    assert summary.recent == 0


def test_a_closed_index_is_left_out(library: dict[Path, IndexDB]) -> None:
    a, b = list(library)
    library[b].close()
    summary = summarize_library(library, None)
    assert summary.total == 3
    assert summary.trees is not None and b not in summary.trees
