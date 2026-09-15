from __future__ import annotations

import os
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

    @staticmethod
    def _to_path(src: str | bytes) -> Path:
        """watchdog types event paths as ``str | bytes`` — it hands back bytes
        when the watch was scheduled with a bytes path. os.fsdecode covers both
        and uses the filesystem encoding, so a non-ASCII name survives."""
        return Path(os.fsdecode(src))

    @staticmethod
    def _is_sql(path: Path) -> bool:
        return path.suffix.lower() == ".sql" and ".sqlshelf" not in path.parts

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
        if event.is_directory:
            return
        src = self._to_path(event.src_path)
        if self._is_sql(src):
            with self._lock:
                self._modified.add(src)
            self._schedule()

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = self._to_path(event.src_path)
        if self._is_sql(src):
            with self._lock:
                self._modified.add(src)
            self._schedule()

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = self._to_path(event.src_path)
        if self._is_sql(src):
            with self._lock:
                self._modified.discard(src)
                self._deleted.add(src)
            self._schedule()

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = self._to_path(event.src_path)
        dest = self._to_path(event.dest_path)
        with self._lock:
            if self._is_sql(src):
                self._deleted.add(src)
            if self._is_sql(dest):
                self._modified.add(dest)
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
