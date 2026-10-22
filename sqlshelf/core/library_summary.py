"""Counts and folder trees the sidebar shows for the whole library.

Every number comes from the indexes' read connections, so the window gathers
a summary on a worker thread and never waits on the database to draw it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .folder_tree import FolderNode, build_folder_tree, folder_prefix
from .index_db import IndexDB
from .navigation import root_of


@dataclass
class LibrarySummary:
    """What the sidebar needs, for one library and one scope.

    ``total`` and ``favorites`` always span the library, as the views they
    count do. ``recent`` and ``tags`` follow the scope, because Recent and a
    tag are browsed inside the folder in view. ``trees`` is None when it was
    not gathered (navigation alone does not change it).
    """

    total: int = 0
    favorites: int = 0
    recent: int = 0
    tags: list[tuple[str, int]] = field(default_factory=list)
    trees: dict[Path, FolderNode] | None = None


def summarize_library(
    dbs: Mapping[Path, IndexDB],
    scope: Path | None,
    *,
    with_trees: bool = True,
) -> LibrarySummary:
    """Gather a :class:`LibrarySummary` from every index in *dbs*.

    *scope* is the folder in view (a library folder or a subfolder of one),
    or None for the whole library. An index that fails to answer, such as one
    being closed while the folder is removed, is left out rather than raised.
    """
    summary = LibrarySummary(trees={} if with_trees else None)
    for root, db in dbs.items():
        try:
            summary.total += db.count()
            summary.favorites += db.count_favorites()
            if summary.trees is not None:
                summary.trees[root] = build_folder_tree(root, db.get_rel_paths())
        except Exception:
            continue

    if scope is None:
        scoped = [(db, "") for db in dbs.values()]
    else:
        scope_root = root_of(scope, dbs)
        scoped = (
            []
            if scope_root is None
            else [(dbs[scope_root], folder_prefix(scope, scope_root))]
        )

    tags: Counter[str] = Counter()
    for db, prefix in scoped:
        try:
            summary.recent += db.count_recent(prefix=prefix)
            tags.update(dict(db.get_tag_counts(prefix)))
        except Exception:
            continue
    summary.tags = sorted(tags.items(), key=lambda item: (-item[1], item[0]))
    return summary
