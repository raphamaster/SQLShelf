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
    _CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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


def get_pinned_folders() -> list[Path]:
    """Return list of pinned project folders (in order pinned)."""
    data = _load()
    paths = []
    for p in data.get("pinned_folders", []):
        path = Path(p)
        if path.is_dir():
            paths.append(path)
    return paths


def pin_folder(path: Path) -> None:
    """Add *path* to pinned folders (no-op if already pinned)."""
    data = _load()
    path_str = str(path.resolve())
    pinned: list[str] = data.get("pinned_folders", [])
    if path_str not in pinned:
        pinned.append(path_str)
    data["pinned_folders"] = pinned
    _save(data)


def unpin_folder(path: Path) -> None:
    """Remove *path* from pinned folders (no-op if not pinned)."""
    data = _load()
    path_str = str(path.resolve())
    pinned: list[str] = data.get("pinned_folders", [])
    if path_str in pinned:
        pinned.remove(path_str)
    data["pinned_folders"] = pinned
    _save(data)


def is_folder_pinned(path: Path) -> bool:
    """Return True if *path* is currently pinned."""
    data = _load()
    return str(path.resolve()) in data.get("pinned_folders", [])
