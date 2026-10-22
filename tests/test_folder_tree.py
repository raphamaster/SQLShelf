from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.folder_tree import FolderNode, build_folder_tree, folder_prefix

ROOT = Path("C:/lib/Querys")


def _shape(node: FolderNode) -> tuple:
    return (node.name, node.count, [_shape(child) for child in node.children])


def test_empty_folder_is_a_lone_root() -> None:
    tree = build_folder_tree(ROOT, [])
    assert _shape(tree) == ("Querys", 0, [])
    assert tree.path == ROOT


def test_files_at_the_root_count_without_children() -> None:
    tree = build_folder_tree(ROOT, ["a.sql", "b.sql"])
    assert _shape(tree) == ("Querys", 2, [])


def test_counts_include_every_subfolder() -> None:
    tree = build_folder_tree(
        ROOT,
        [
            "top.sql",
            "relatorios/a.sql",
            "relatorios/b.sql",
            "relatorios/mensal/c.sql",
            "testes/d.sql",
        ],
    )
    assert _shape(tree) == (
        "Querys",
        5,
        [
            ("relatorios", 3, [("mensal", 1, [])]),
            ("testes", 1, []),
        ],
    )


def test_folders_with_only_subfolders_still_appear() -> None:
    tree = build_folder_tree(ROOT, ["a/b/c/deep.sql"])
    assert _shape(tree) == ("Querys", 1, [("a", 1, [("b", 1, [("c", 1, [])])])])


def test_child_paths_are_absolute_under_the_root() -> None:
    tree = build_folder_tree(ROOT, ["reports/monthly/x.sql"])
    reports = tree.children[0]
    assert reports.path == ROOT / "reports"
    assert reports.children[0].path == ROOT / "reports" / "monthly"


@pytest.mark.parametrize(
    ("rel_paths", "expected"),
    [
        (["b/x.sql", "a/x.sql", "C/x.sql"], ["a", "b", "C"]),
        (["Zeta/x.sql", "alpha/x.sql", "Beta/x.sql"], ["alpha", "Beta", "Zeta"]),
        (["relatórios/x.sql", "rel/x.sql"], ["rel", "relatórios"]),
    ],
)
def test_children_are_sorted_by_name_ignoring_case(
    rel_paths: list[str], expected: list[str]
) -> None:
    tree = build_folder_tree(ROOT, rel_paths)
    assert [child.name for child in tree.children] == expected


def test_order_of_input_does_not_matter() -> None:
    paths = ["b/1.sql", "a/2.sql", "b/c/3.sql", "4.sql"]
    assert _shape(build_folder_tree(ROOT, paths)) == _shape(
        build_folder_tree(ROOT, list(reversed(paths)))
    )


def test_empty_rel_paths_are_ignored() -> None:
    assert build_folder_tree(ROOT, ["", "a.sql"]).count == 1


def test_find_returns_the_node_for_a_path() -> None:
    tree = build_folder_tree(ROOT, ["reports/monthly/x.sql", "other/y.sql"])
    found = tree.find(ROOT / "reports" / "monthly")
    assert found is not None and found.name == "monthly"
    assert tree.find(ROOT) is tree
    assert tree.find(ROOT / "missing") is None
    assert tree.find(Path("C:/elsewhere")) is None


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        (ROOT, ""),
        (ROOT / "reports", "reports/"),
        (ROOT / "reports" / "monthly", "reports/monthly/"),
    ],
)
def test_folder_prefix(folder: Path, expected: str) -> None:
    assert folder_prefix(folder, ROOT) == expected


def test_folder_prefix_outside_the_root_raises() -> None:
    with pytest.raises(ValueError):
        folder_prefix(Path("C:/elsewhere"), ROOT)


def test_prefix_selects_exactly_the_subtree() -> None:
    # The prefix ends in "/", so "reports" does not also match "reports_old".
    paths = ["reports/a.sql", "reports_old/b.sql", "reports/sub/c.sql"]
    prefix = folder_prefix(ROOT / "reports", ROOT)
    assert [p for p in paths if p.startswith(prefix)] == [
        "reports/a.sql",
        "reports/sub/c.sql",
    ]
