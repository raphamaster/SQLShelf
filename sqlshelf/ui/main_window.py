from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.frontmatter import read_sql_file, write_sql_file
from ..core.index_db import IndexDB
from ..core.models import SearchResult
from ..core.scanner import scan_folder
from ..core.watcher import FolderWatcher
from .highlighter import SqlHighlighter
from .metadata_panel import MetadataPanel
from .new_query_dialog import NewQueryDialog
from .query_list import QueryListWidget
from .search_bar import SearchBar
from .sidebar import SidebarWidget


# ---------------------------------------------------------------------------
# Background indexing worker
# ---------------------------------------------------------------------------

class _IndexWorkerSignals(QObject):
    finished = Signal(int)  # count indexed


class _IndexWorker(QRunnable):
    def __init__(self, db: IndexDB, folder: Path) -> None:
        super().__init__()
        self.signals = _IndexWorkerSignals()
        self._db = db
        self._folder = folder

    def run(self) -> None:
        queries = scan_folder(self._folder)
        self._db.index_incremental(queries)
        self.signals.finished.emit(self._db.count())


# ---------------------------------------------------------------------------
# Watcher → Qt bridge
# ---------------------------------------------------------------------------

class _WatcherBridge(QObject):
    files_changed = Signal(object, object)  # (set[Path], set[Path])

    def on_changed(self, modified: set[Path], deleted: set[Path]) -> None:
        self.files_changed.emit(modified, deleted)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SQLShelf")
        self.resize(1400, 860)

        self._folder: Path | None = None
        self._db: IndexDB | None = None
        self._watcher: FolderWatcher | None = None
        self._watcher_bridge = _WatcherBridge()
        self._watcher_bridge.files_changed.connect(self._handle_files_changed)

        # Current query state
        self._current_result: SearchResult | None = None
        self._current_metadata: dict = {}
        self._edit_mode = False

        self._build_menu()
        self._build_ui()
        self._build_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = QMenuBar(self)
        self.setMenuBar(mb)

        file_menu = QMenu("&File", self)
        mb.addMenu(file_menu)

        open_act = QAction("Open Folder…", self)
        open_act.setShortcut(QKeySequence("Ctrl+O"))
        open_act.triggered.connect(self.open_folder)
        file_menu.addAction(open_act)

        new_act = QAction("New Query…", self)
        new_act.setShortcut(QKeySequence("Ctrl+N"))
        new_act.triggered.connect(self.new_query)
        file_menu.addAction(new_act)

        file_menu.addSeparator()

        self._recent_menu = QMenu("Recent Projects", self)
        file_menu.addMenu(self._recent_menu)
        self._rebuild_recent_menu()

        file_menu.addSeparator()

        reindex_act = QAction("Force Reindex", self)
        reindex_act.triggered.connect(self.force_reindex)
        file_menu.addAction(reindex_act)

        view_menu = QMenu("&View", self)
        mb.addMenu(view_menu)

        reveal_act = QAction("Reveal File in Explorer", self)
        reveal_act.triggered.connect(self.reveal_in_explorer)
        view_menu.addAction(reveal_act)

        open_ssms_act = QAction("Open in SSMS", self)
        open_ssms_act.triggered.connect(self.open_in_ssms)
        view_menu.addAction(open_ssms_act)

        copy_act = QAction("Copy SQL Body", self)
        copy_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
        copy_act.triggered.connect(self.copy_sql)
        view_menu.addAction(copy_act)

    def _build_ui(self) -> None:
        # Left panel
        self._sidebar = SidebarWidget()
        self._sidebar.open_folder_requested.connect(self.open_folder)
        self._sidebar.tag_selected.connect(self._on_tag_selected)

        # Middle panel
        middle = QWidget()
        mid_layout = QVBoxLayout(middle)
        mid_layout.setContentsMargins(0, 4, 0, 0)
        mid_layout.setSpacing(4)

        self._search_bar = SearchBar()
        self._search_bar.search_changed.connect(self._on_search_changed)

        self._query_list = QueryListWidget()
        self._query_list.query_selected.connect(self._on_query_selected)

        mid_layout.addWidget(self._search_bar)
        mid_layout.addWidget(self._query_list)

        # Right panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._metadata_panel = MetadataPanel()

        # Editor toolbar
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._edit_toggle_btn = QPushButton("Edit")
        self._edit_toggle_btn.setCheckable(True)
        self._edit_toggle_btn.setToolTip("Toggle edit mode (Ctrl+E)")
        self._edit_toggle_btn.clicked.connect(self._toggle_edit_mode)

        self._save_btn = QPushButton("Save")
        self._save_btn.setToolTip("Save changes (Ctrl+S)")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save_current)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_edit)

        self._toolbar.addWidget(self._edit_toggle_btn)
        self._toolbar.addWidget(self._save_btn)
        self._toolbar.addWidget(self._cancel_btn)
        self._toolbar.addSeparator()
        self._path_label = QLabel("")
        self._path_label.setStyleSheet("color: #888888; padding-left: 4px;")
        self._toolbar.addWidget(self._path_label)

        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self._editor.setFont(font)
        self._highlighter = SqlHighlighter(self._editor.document())

        right_layout.addWidget(self._metadata_panel)
        right_layout.addWidget(self._toolbar)
        right_layout.addWidget(self._editor, stretch=1)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._sidebar)
        splitter.addWidget(middle)
        splitter.addWidget(right)
        splitter.setSizes([180, 320, 900])
        self.setCentralWidget(splitter)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("No folder loaded — File → Open Folder…")

    def _build_shortcuts(self) -> None:
        from PySide6.QtGui import QShortcut

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._search_bar.focus)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_current)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(
            lambda: self._edit_toggle_btn.click()
        )
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_query)

    # ------------------------------------------------------------------
    # Folder / indexing
    # ------------------------------------------------------------------

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Queries Folder")
        if folder:
            self.load_folder(Path(folder))

    def load_folder(self, folder: Path) -> None:
        if not folder.is_dir():
            return
        self._stop_watcher()
        if self._db is not None:
            self._db.close()

        self._folder = folder
        self._db = IndexDB(folder)
        cfg.add_recent_project(folder)
        self._rebuild_recent_menu()

        self.setWindowTitle(f"SQLShelf — {folder.name}")
        self._status_bar.showMessage("Indexing…")
        self._cancel_edit_mode()

        worker = _IndexWorker(self._db, folder)
        worker.signals.finished.connect(self._on_index_finished)
        QThreadPool.globalInstance().start(worker)

        self._watcher_bridge = _WatcherBridge()
        self._watcher_bridge.files_changed.connect(self._handle_files_changed)
        self._watcher = FolderWatcher(folder, self._watcher_bridge.on_changed)
        self._watcher.start()

    def _on_index_finished(self, count: int) -> None:
        self._status_bar.showMessage(f"{count} queries indexed")
        self._refresh_ui()

    def force_reindex(self) -> None:
        if self._db is None or self._folder is None:
            return
        self._status_bar.showMessage("Full reindex…")
        queries = scan_folder(self._folder)
        self._db.index_all(queries)
        self._status_bar.showMessage(f"{self._db.count()} queries indexed")
        self._refresh_ui()

    def _stop_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = cfg.get_recent_projects()
        if not recents:
            self._recent_menu.addAction("(none)").setEnabled(False)
            return
        for path in recents:
            action = QAction(str(path), self)
            action.triggered.connect(lambda checked, p=path: self.load_folder(p))
            self._recent_menu.addAction(action)

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_ui(self) -> None:
        if self._db is None:
            return
        tags = self._db.get_all_tags()
        self._sidebar.set_tags(tags)
        self._run_search()

    def _run_search(self) -> None:
        if self._db is None:
            return
        text = self._search_bar.text()
        results = self._db.search(text)
        self._query_list.set_results(results)

    # ------------------------------------------------------------------
    # Query selection
    # ------------------------------------------------------------------

    def _on_query_selected(self, result: SearchResult) -> None:
        self._cancel_edit_mode()
        self._current_result = result
        if self._folder is None:
            return
        path = self._folder / result.rel_path
        self._load_query_from_disk(path, result)

    def _load_query_from_disk(self, path: Path, result: SearchResult) -> None:
        try:
            metadata, body, _has_fm = read_sql_file(path)
        except Exception:
            self._editor.setPlainText(f"-- Error reading {path}")
            return

        self._current_metadata = metadata
        self._path_label.setText(result.rel_path)
        self._editor.setPlainText(body)

        objects: dict[str, list[str]] = {"table": [], "column": []}
        if self._db is not None:
            obj_map = self._db.get_objects(result.query_id)
            objects = obj_map

        self._metadata_panel.set_query(
            title=result.title,
            description=result.description,
            tags=result.tags,
            tables=objects.get("table", []),
            columns=objects.get("column", []),
        )

    # ------------------------------------------------------------------
    # Search / tag filtering
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._run_search()

    def _on_tag_selected(self, tag: str) -> None:
        if tag:
            self._search_bar._edit.setText(f"tag:{tag}")
        else:
            self._search_bar.clear()

    # ------------------------------------------------------------------
    # Edit / Save
    # ------------------------------------------------------------------

    def _toggle_edit_mode(self) -> None:
        if self._edit_mode:
            self._cancel_edit_mode()
        else:
            self._enter_edit_mode()

    def _enter_edit_mode(self) -> None:
        if self._current_result is None:
            self._edit_toggle_btn.setChecked(False)
            return
        self._edit_mode = True
        self._editor.setReadOnly(False)
        self._metadata_panel.set_edit_mode(True)
        self._save_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)
        self._edit_toggle_btn.setText("Editing…")

    def _cancel_edit_mode(self) -> None:
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText("Edit")
        # Re-load from disk to discard unsaved changes
        if self._current_result is not None and self._folder is not None:
            path = self._folder / self._current_result.rel_path
            self._load_query_from_disk(path, self._current_result)

    def _cancel_edit(self) -> None:
        self._cancel_edit_mode()

    def save_current(self) -> None:
        if not self._edit_mode or self._current_result is None or self._folder is None:
            return
        path = self._folder / self._current_result.rel_path

        title, description, tags = self._metadata_panel.get_edited_values()
        body = self._editor.toPlainText()

        meta = dict(self._current_metadata)
        meta["title"] = title or path.stem
        meta["description"] = description
        meta["tags"] = tags

        try:
            write_sql_file(path, meta, body)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return

        # Reindex the saved file
        if self._db is not None:
            from ..core.scanner import scan_folder as _scan

            updated = [q for q in _scan(self._folder) if q.path == path]
            if updated:
                self._db.upsert_query(updated[0])

        self._cancel_edit_mode()
        self._refresh_ui()
        self._status_bar.showMessage(f"Saved: {self._current_result.rel_path}")

    # ------------------------------------------------------------------
    # New query
    # ------------------------------------------------------------------

    def new_query(self) -> None:
        if self._folder is None:
            QMessageBox.information(self, "No Folder", "Open a folder first.")
            return

        subfolders = [
            str(p.relative_to(self._folder))
            for p in self._folder.rglob("*")
            if p.is_dir() and ".sqlshelf" not in p.parts
        ]

        dlg = NewQueryDialog(self._folder, subfolders, self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.created_path is not None:
            path = dlg.created_path
            from ..core.scanner import scan_folder as _scan

            updated = [q for q in _scan(self._folder) if q.path == path]
            if updated and self._db is not None:
                self._db.upsert_query(updated[0])
            self._refresh_ui()
            self._status_bar.showMessage(f"Created: {path.name}")

    # ------------------------------------------------------------------
    # Watcher handler
    # ------------------------------------------------------------------

    def _handle_files_changed(self, modified: set[Path], deleted: set[Path]) -> None:
        if self._db is None or self._folder is None:
            return

        from ..core.scanner import scan_folder as _scan

        for path in modified:
            if path.is_file():
                try:
                    hits = [q for q in _scan(self._folder) if q.path == path]
                    if hits:
                        self._db.upsert_query(hits[0])
                except Exception:
                    pass

        for path in deleted:
            try:
                self._db.remove_file(path)
            except Exception:
                pass

        self._refresh_ui()
        self._status_bar.showMessage(f"{self._db.count()} queries indexed")

    # ------------------------------------------------------------------
    # System actions
    # ------------------------------------------------------------------

    def reveal_in_explorer(self) -> None:
        if self._current_result is None or self._folder is None:
            return
        path = self._folder / self._current_result.rel_path
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)

    def open_in_ssms(self) -> None:
        if self._current_result is None or self._folder is None:
            return
        path = self._folder / self._current_result.rel_path
        if sys.platform == "win32":
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except Exception as exc:
                QMessageBox.warning(self, "Open in SSMS", str(exc))
        else:
            QMessageBox.information(self, "Open in SSMS", "Only available on Windows.")

    def copy_sql(self) -> None:
        from PySide6.QtWidgets import QApplication

        body = self._editor.toPlainText()
        if body:
            QApplication.clipboard().setText(body)
            self._status_bar.showMessage("SQL body copied to clipboard")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._stop_watcher()
        if self._db is not None:
            self._db.close()
        super().closeEvent(event)
