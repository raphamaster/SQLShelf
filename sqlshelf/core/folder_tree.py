"""Subfolder tree of a library folder, built from the paths already indexed.

The index knows every file's path relative to its library folder, so the
sidebar shows subfolders without walking the disk. A subfolder is browsed by
filtering those paths on a prefix (:func:`folder_prefix`).

Pure Python: nothing here touches Qt or the file system.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass
class FolderNode:
    """One folder of the tree; ``count`` includes every subfolder below it."""

    path: Path
    name: str
    count: int = 0
    children: list[FolderNode] = field(default_factory=list)

    def find(self, path: Path) -> FolderNode | None:
        """The node for *path* in this subtree, if there is one."""
        if path == self.path:
            return self
        if not path.is_relative_to(self.path):
            return None
        for child in self.children:
            found = child.find(path)
            if found is not None:
                return found
        return None


def build_folder_tree(root: Path, rel_paths: Iterable[str]) -> FolderNode:
    """The tree of folders under *root* that hold at least one indexed file.

    *rel_paths* are POSIX paths relative to *root*, as stored in the index.
    Children are sorted by name, ignoring case.
    """
    tree = FolderNode(root, root.name)
    nodes: dict[tuple[str, ...], FolderNode] = {(): tree}
    for rel_path in rel_paths:
        if not rel_path:
            continue
        parts = PurePosixPath(rel_path).parts[:-1]
        tree.count += 1
        node = tree
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            child = nodes.get(key)
            if child is None:
                child = FolderNode(node.path / parts[depth - 1], parts[depth - 1])
                node.children.append(child)
                nodes[key] = child
            child.count += 1
            node = child
    _sort(tree)
    return tree


def _sort(node: FolderNode) -> None:
    node.children.sort(key=lambda child: (child.name.casefold(), child.name))
    for child in node.children:
        _sort(child)


def folder_prefix(folder: Path, root: Path) -> str:
    """The ``rel_path`` prefix of the files under *folder* ("" for *root*).

    Raises ValueError when *folder* is not inside *root*.
    """
    if folder == root:
        return ""
    return folder.relative_to(root).as_posix() + "/"
