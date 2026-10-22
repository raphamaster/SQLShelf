from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.navigation import (
    NavHistory,
    NavState,
    chain,
    parent,
    root_of,
    search_text,
    with_search,
)

ROOT = Path("/lib/Querys")
OTHER = Path("/lib/scripts")
SUB = ROOT / "relatorios" / "mensal"

ALL = NavState("all")
FOLDER = NavState("folder", ROOT)
FAVS = NavState("favorites")


class TestHistory:
    def test_starts_at_all_with_nowhere_to_go(self) -> None:
        history = NavHistory()
        assert history.current == ALL
        assert not history.can_back
        assert not history.can_forward
        assert history.back() is None
        assert history.forward() is None

    def test_back_and_forward_walk_the_stack(self) -> None:
        history = NavHistory()
        history.navigate(FOLDER)
        history.navigate(FAVS)

        assert history.back() == FOLDER
        assert history.back() == ALL
        assert not history.can_back
        assert history.forward() == FOLDER
        assert history.forward() == FAVS
        assert not history.can_forward

    def test_navigating_discards_the_future(self) -> None:
        history = NavHistory()
        history.navigate(FOLDER)
        history.navigate(FAVS)
        history.back()
        history.back()

        history.navigate(NavState("recent"))

        assert not history.can_forward
        assert history.back() == ALL

    def test_navigating_to_the_current_state_is_a_no_op(self) -> None:
        history = NavHistory()
        history.navigate(FOLDER)
        assert history.navigate(NavState("folder", ROOT)) is False
        assert history.back() == ALL
        assert not history.can_back

    def test_replace_keeps_back_and_forward(self) -> None:
        history = NavHistory()
        history.navigate(NavState("search", ROOT, "sel"))
        history.replace(NavState("search", ROOT, "select"))

        assert history.current.value == "select"
        assert history.back() == ALL
        assert history.forward() == NavState("search", ROOT, "select")

    def test_limit_drops_the_oldest_entries(self) -> None:
        history = NavHistory(limit=3)
        for name in ("a", "b", "c", "d"):
            history.navigate(NavState("tag", None, name))
        assert history.back() == NavState("tag", None, "c")
        assert history.back() == NavState("tag", None, "b")
        assert history.back() is None

    def test_up_navigates_to_the_parent(self) -> None:
        history = NavHistory()
        history.navigate(NavState("folder", SUB))

        assert history.up([ROOT]) == NavState("folder", ROOT / "relatorios")
        assert history.up([ROOT]) == FOLDER
        assert history.up([ROOT]) == ALL
        assert history.up([ROOT]) is None
        # Every step up was a navigation, so back retraces them.
        assert history.back() == FOLDER


class TestChain:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (ALL, [ALL]),
            (FAVS, [ALL, FAVS]),
            (FOLDER, [ALL, FOLDER]),
            (
                NavState("folder", SUB),
                [
                    ALL,
                    FOLDER,
                    NavState("folder", ROOT / "relatorios"),
                    NavState("folder", SUB),
                ],
            ),
            (NavState("tag", None, "bi"), [ALL, NavState("tag", None, "bi")]),
            (
                NavState("recent", ROOT),
                [ALL, FOLDER, NavState("recent", ROOT)],
            ),
            (
                NavState("search", ROOT, "x"),
                [ALL, FOLDER, NavState("search", ROOT, "x")],
            ),
        ],
    )
    def test_chain(self, state: NavState, expected: list[NavState]) -> None:
        assert chain(state, [ROOT, OTHER]) == expected

    def test_folder_outside_every_root_is_one_segment(self) -> None:
        stray = Path("/elsewhere/x")
        assert chain(NavState("folder", stray), [ROOT]) == [
            ALL,
            NavState("folder", stray),
        ]

    @pytest.mark.parametrize(
        ("folder", "expected"),
        [(ROOT, ROOT), (SUB, ROOT), (OTHER / "x", OTHER), (Path("/elsewhere"), None)],
    )
    def test_root_of(self, folder: Path, expected: Path | None) -> None:
        assert root_of(folder, [ROOT, OTHER]) == expected

    def test_parent_of_all_is_none(self) -> None:
        assert parent(ALL, [ROOT]) is None

    def test_parent_of_search_is_its_scope(self) -> None:
        assert parent(NavState("search", ROOT, "x"), [ROOT]) == FOLDER
        assert parent(NavState("search", None, "x"), [ROOT]) == ALL


class TestWithSearch:
    def test_typing_starts_a_search_in_the_current_folder(self) -> None:
        assert with_search(FOLDER, "vendas") == NavState("search", ROOT, "vendas")

    def test_typing_more_updates_the_search(self) -> None:
        state = NavState("search", ROOT, "ven")
        assert with_search(state, "vendas ") == NavState("search", ROOT, "vendas")

    def test_clearing_returns_to_the_scope(self) -> None:
        assert with_search(NavState("search", ROOT, "x"), "  ") == FOLDER

    def test_clearing_outside_a_search_changes_nothing(self) -> None:
        assert with_search(FAVS, "") == FAVS

    def test_search_from_favorites_spans_the_library(self) -> None:
        assert with_search(FAVS, "x") == NavState("search", None, "x")

    def test_the_tag_filter_text_keeps_the_tag_state(self) -> None:
        tag = NavState("tag", ROOT, "bi")
        assert with_search(tag, "tag:bi") == tag

    def test_refining_a_tag_starts_a_search(self) -> None:
        tag = NavState("tag", ROOT, "bi")
        assert with_search(tag, "tag:bi vendas") == NavState(
            "search", ROOT, "tag:bi vendas"
        )

    def test_clearing_a_tag_returns_to_the_scope(self) -> None:
        assert with_search(NavState("tag", ROOT, "bi"), "") == FOLDER


class TestSearchText:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (ALL, ""),
            (FOLDER, ""),
            (FAVS, ""),
            (NavState("recent", ROOT), ""),
            (NavState("tag", None, "bi"), "tag:bi"),
            (NavState("search", ROOT, "select"), "select"),
        ],
    )
    def test_search_text(self, state: NavState, expected: str) -> None:
        assert search_text(state) == expected
