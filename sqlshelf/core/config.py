from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path.home() / ".sqlshelf"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_MAX_RECENT = 10


def _load() -> dict:
    if not _CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Recent projects (File menu history)
# ---------------------------------------------------------------------------


def get_recent_projects() -> list[Path]:
    """Return list of recently opened project folders (most-recent first)."""
    data = _load()
    paths = []
    for p in data.get("recent_projects", []):
        path = Path(p)
        if path.is_dir():
            paths.append(path)
    return paths


def add_recent_project(path: Path) -> None:
    """Register *path* as the most-recently-opened project."""
    data = _load()
    path_str = str(path.resolve())
    recent: list[str] = data.get("recent_projects", [])
    if path_str in recent:
        recent.remove(path_str)
    recent.insert(0, path_str)
    data["recent_projects"] = recent[:_MAX_RECENT]
    _save(data)


def get_last_project() -> Path | None:
    """Return the last opened project folder, or None."""
    recent = get_recent_projects()
    return recent[0] if recent else None


# ---------------------------------------------------------------------------
# Known folders (sidebar explorer)
# Each entry: {"path": str, "favorited": bool}
# ---------------------------------------------------------------------------


def get_known_folders() -> list[tuple[Path, bool]]:
    """Return (path, is_favorited) for each known folder that still exists on disk."""
    data = _load()
    result = []
    for entry in data.get("known_folders", []):
        path = Path(entry["path"])
        if path.is_dir():
            result.append((path, bool(entry.get("favorited", False))))
    return result


def add_known_folder(path: Path) -> None:
    """Add *path* to known folders (no-op if already present)."""
    data = _load()
    path_str = str(path.resolve())
    folders: list[dict] = data.get("known_folders", [])
    if not any(f["path"] == path_str for f in folders):
        folders.append({"path": path_str, "favorited": False})
        data["known_folders"] = folders
        _save(data)


def remove_known_folder(path: Path) -> None:
    """Remove *path* from known folders."""
    data = _load()
    path_str = str(path.resolve())
    data["known_folders"] = [
        f for f in data.get("known_folders", []) if f["path"] != path_str
    ]
    _save(data)


def toggle_folder_favorite(path: Path) -> bool:
    """Toggle favorite flag for *path*; return new flag value."""
    data = _load()
    path_str = str(path.resolve())
    for entry in data.get("known_folders", []):
        if entry["path"] == path_str:
            entry["favorited"] = not entry.get("favorited", False)
            _save(data)
            return bool(entry["favorited"])
    return False


# ---------------------------------------------------------------------------
# Theme preference
# ---------------------------------------------------------------------------


def get_theme() -> str:
    """Return the saved theme name; defaults to 'dark'."""
    return _load().get("theme", "dark")


def set_theme(name: str) -> None:
    """Persist *name* as the active theme."""
    data = _load()
    data["theme"] = name
    _save(data)


# ---------------------------------------------------------------------------
# Language preference
# ---------------------------------------------------------------------------


def get_language() -> str:
    """Return the saved language code; defaults to 'en'."""
    return _load().get("language", "en")


def set_language(lang: str) -> None:
    """Persist *lang* as the active UI language."""
    data = _load()
    data["language"] = lang
    _save(data)


# ---------------------------------------------------------------------------
# System tray / startup preference
# ---------------------------------------------------------------------------


def get_minimize_to_tray() -> bool:
    """Return whether closing the window should minimize to tray (default True)."""
    return bool(_load().get("minimize_to_tray", True))


def set_minimize_to_tray(enabled: bool) -> None:
    """Persist the minimize-to-tray preference."""
    data = _load()
    data["minimize_to_tray"] = enabled
    _save(data)


# ---------------------------------------------------------------------------
# Window layout (splitter, details panel, list view and grouping)
# ---------------------------------------------------------------------------

VIEW_MODES = ("details", "content")
GROUP_MODES = ("date", "folder", "none")
SORT_KEYS = ("name_asc", "name_desc", "mtime_desc", "mtime_asc")


def _set_key(key: str, value: object) -> None:
    data = _load()
    data[key] = value
    _save(data)


def get_splitter_sizes() -> list[int] | None:
    """Return the saved [sidebar, list, panel] widths, or None if never saved."""
    sizes = _load().get("splitter_sizes")
    if (
        isinstance(sizes, list)
        and len(sizes) == 3
        and all(isinstance(s, int) and s >= 0 for s in sizes)
    ):
        return sizes
    return None


def set_splitter_sizes(sizes: list[int]) -> None:
    """Persist the [sidebar, list, panel] widths."""
    _set_key("splitter_sizes", [int(s) for s in sizes])


def get_panel_visible() -> bool:
    """Return whether the details panel is shown (default True)."""
    return bool(_load().get("panel_visible", True))


def set_panel_visible(visible: bool) -> None:
    """Persist whether the details panel is shown."""
    _set_key("panel_visible", bool(visible))


def get_view_mode() -> str:
    """Return the query-list view: 'details' (default) or 'content'."""
    mode = _load().get("view_mode", "details")
    return mode if mode in VIEW_MODES else "details"


def set_view_mode(mode: str) -> None:
    """Persist the query-list view; unknown values are ignored."""
    if mode in VIEW_MODES:
        _set_key("view_mode", mode)


def get_group_mode() -> str:
    """Return the query-list grouping: 'date' (default), 'folder' or 'none'."""
    mode = _load().get("group_mode", "date")
    return mode if mode in GROUP_MODES else "date"


def set_group_mode(mode: str) -> None:
    """Persist the query-list grouping; unknown values are ignored."""
    if mode in GROUP_MODES:
        _set_key("group_mode", mode)


def get_search_group_mode() -> str:
    """Return the grouping while searching: 'folder' (default), 'date' or 'none'."""
    mode = _load().get("search_group_mode", "folder")
    return mode if mode in GROUP_MODES else "folder"


def set_search_group_mode(mode: str) -> None:
    """Persist the grouping used while searching; unknown values are ignored."""
    if mode in GROUP_MODES:
        _set_key("search_group_mode", mode)


def get_sort_key() -> str:
    """Return the query-list sort order (default: most recently modified first)."""
    key = _load().get("sort_key", "mtime_desc")
    return key if key in SORT_KEYS else "mtime_desc"


def set_sort_key(key: str) -> None:
    """Persist the query-list sort order; unknown values are ignored."""
    if key in SORT_KEYS:
        _set_key("sort_key", key)


def get_show_menu_bar() -> bool:
    """Return whether the classic menu bar is shown (default False)."""
    return bool(_load().get("show_menu_bar", False))


def set_show_menu_bar(visible: bool) -> None:
    """Persist whether the classic menu bar is shown."""
    _set_key("show_menu_bar", bool(visible))
