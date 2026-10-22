"""Navigation states and back/forward history for the main window.

Pure Python: the window turns a :class:`NavState` into a search, and the
address bar turns :func:`chain` into a breadcrumb. Nothing here touches Qt.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

NavKind = Literal["all", "folder", "tag", "favorites", "recent", "search"]


@dataclass(frozen=True)
class NavState:
    """One place the user can be in the library.

    ``folder`` is the folder shown for ``"folder"`` and the scope for
    ``"tag"``, ``"recent"`` and ``"search"`` (None = every known folder).
    ``value`` is the tag name for ``"tag"`` and the query text for ``"search"``.
    Favorites always span every folder, so their ``folder`` is None.
    """

    kind: NavKind = "all"
    folder: Path | None = None
    value: str = ""

    @property
    def scope(self) -> NavState:
        """The plain browsing state this one is narrowed from."""
        if self.folder is None:
            return NavState("all")
        return NavState("folder", self.folder)


def root_of(folder: Path, roots: Iterable[Path]) -> Path | None:
    """The library folder among *roots* that holds *folder*, if any."""
    for root in roots:
        if folder == root or folder.is_relative_to(root):
            return root
    return None


def _folder_chain(folder: Path, roots: Iterable[Path]) -> list[NavState]:
    """States from the library root down to *folder*, subfolders included."""
    root = root_of(folder, roots)
    if root is None:
        return [NavState("folder", folder)]
    states = [NavState("folder", root)]
    current = root
    for part in folder.relative_to(root).parts:
        current = current / part
        states.append(NavState("folder", current))
    return states


def chain(state: NavState, roots: Iterable[Path] = ()) -> list[NavState]:
    """Every state from "all" down to *state*, in order — the breadcrumb.

    *roots* are the known library folders; a folder under one of them is shown
    as a path of subfolders instead of a single segment.
    """
    roots = list(roots)
    states = [NavState("all")]
    if state.kind == "all":
        return states
    if state.kind == "favorites":
        return states + [NavState("favorites")]
    if state.folder is not None:
        states += _folder_chain(state.folder, roots)
    if state.kind == "folder":
        return states
    return states + [state]


def parent(state: NavState, roots: Iterable[Path] = ()) -> NavState | None:
    """The state one level up from *state*, or None at the top."""
    states = chain(state, roots)
    return states[-2] if len(states) >= 2 else None


class NavHistory:
    """A browser-style back/forward stack of :class:`NavState`.

    Navigating somewhere new drops the "forward" entries, as a browser does.
    """

    def __init__(self, initial: NavState | None = None, limit: int = 100) -> None:
        self._states: list[NavState] = [initial or NavState()]
        self._index = 0
        self._limit = max(2, limit)

    @property
    def current(self) -> NavState:
        return self._states[self._index]

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return self._index < len(self._states) - 1

    def navigate(self, state: NavState) -> bool:
        """Go to *state*; return False if it is already the current one."""
        if state == self.current:
            return False
        del self._states[self._index + 1 :]
        self._states.append(state)
        if len(self._states) > self._limit:
            del self._states[0]
        self._index = len(self._states) - 1
        return True

    def replace(self, state: NavState) -> None:
        """Swap the current entry for *state* without touching back/forward.

        Used while typing a search, so every keystroke is not a history entry.
        """
        self._states[self._index] = state

    def back(self) -> NavState | None:
        if not self.can_back:
            return None
        self._index -= 1
        return self.current

    def forward(self) -> NavState | None:
        if not self.can_forward:
            return None
        self._index += 1
        return self.current

    def up(self, roots: Iterable[Path] = ()) -> NavState | None:
        """Navigate to the parent of the current state, if there is one."""
        target = parent(self.current, roots)
        if target is None:
            return None
        self.navigate(target)
        return target


def search_text(state: NavState) -> str:
    """The text the search field shows for *state*.

    A tag is browsed through its ``tag:`` filter, so the field shows it and
    the user can refine it by typing more.
    """
    if state.kind == "tag":
        return f"tag:{state.value}"
    if state.kind == "search":
        return state.value
    return ""


def with_search(state: NavState, text: str) -> NavState:
    """The state for searching *text* from *state* (or leaving search if empty)."""
    text = text.strip()
    if not text:
        return state.scope if state.kind in ("search", "tag") else state
    if text == search_text(state):
        return state
    if state.kind == "search":
        return replace(state, value=text)
    folder = None if state.kind == "favorites" else state.folder
    return NavState("search", folder, text)
