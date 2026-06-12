from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

# Callback type: (modified: set[Path], deleted: set[Path]) -> None
WatcherCallback = Callable[[set[Path], set[Path]], None]


class _SqlEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        project_root: Path,
        callback: WatcherCallback,
        debounce_ms: int,
    ) -> None:
        self._root = project_root
        self._callback = callback
        self._debounce_s = debounce_ms / 1000.0
        self._modified: set[Path] = set()
        self._deleted: set[Path] = set()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _is_sql(self, src: str) -> bool:
        p = Path(src)
        return p.suffix.lower() == ".sql" and ".sqlshelf" not in p.parts

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        t = threading.Timer(self._debounce_s, self._fire)
        t.daemon = True
        self._timer = t
        t.start()

    def _fire(self) -> None:
        with self._lock:
            modified = set(self._modified)
            deleted = set(self._deleted)
            self._modified.clear()
            self._deleted.clear()
        if modified or deleted:
            self._callback(modified, deleted)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_sql(event.src_path):
            with self._lock:
                self._modified.add(Path(event.src_path))
            self._schedule()

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_sql(event.src_path):
            with self._lock:
                self._modified.add(Path(event.src_path))
            self._schedule()

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_sql(event.src_path):
            with self._lock:
                self._modified.discard(Path(event.src_path))
                self._deleted.add(Path(event.src_path))
            self._schedule()

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        with self._lock:
            if self._is_sql(event.src_path):
                self._deleted.add(Path(event.src_path))
            if self._is_sql(event.dest_path):
                self._modified.add(Path(event.dest_path))
        self._schedule()


class FolderWatcher:
    """Watches a project folder for .sql changes with debouncing.

    Calls *callback*(modified: set[Path], deleted: set[Path]) from a
    background thread after the debounce delay expires.  The UI layer is
    responsible for marshalling these calls to the Qt main thread.
    """

    def __init__(
        self,
        project_root: Path,
        callback: WatcherCallback,
        debounce_ms: int = 500,
    ) -> None:
        self._handler = _SqlEventHandler(project_root, callback, debounce_ms)
        self._observer = Observer()
        self._observer.schedule(self._handler, str(project_root), recursive=True)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
