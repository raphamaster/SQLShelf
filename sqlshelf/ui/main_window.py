from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.frontmatter import read_sql_file, write_sql_file
from ..core.index_db import IndexDB
from ..core.models import SearchResult
from ..core.scanner import scan_folder
from ..core.snippets import list_templates
from ..core.watcher import FolderWatcher
from .code_editor import CodeEditor
from .command_palette import CommandPalette
from .highlighter import SqlHighlighter
from .metadata_panel import MetadataPanel
from .new_query_dialog import NewQueryDialog
from .query_list import QueryListWidget
from .search_bar import SearchBar
from .sidebar import SidebarWidget
from .template_dialog import TemplateDialog


def _icon(style_enum) -> QIcon:
    """Return a standard Qt style icon."""
    return QApplication.style().standardIcon(style_enum)


# ---------------------------------------------------------------------------
# Background indexing worker
# ---------------------------------------------------------------------------

class _IndexWorkerSignals(QObject):
    finished = Signal(int)
    error = Signal(str)


class _IndexWorker(QRunnable):
    def __init__(self, db: IndexDB, folder: Path) -> None:
        super().__init__()
        self.signals = _IndexWorkerSignals()
        self._db = db
        self._folder = folder

    def run(self) -> None:
        try:
            queries = scan_folder(self._folder)
            self._db.index_incremental(queries)
            self.signals.finished.emit(self._db.count())
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Watcher → Qt bridge
# ---------------------------------------------------------------------------

class _WatcherBridge(QObject):
    files_changed = Signal(object, object)

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

        self._current_result: SearchResult | None = None
        self._current_metadata: dict = {}
        self._edit_mode = False

        # Multi-folder support
        self._known_dbs: dict[Path, IndexDB] = {}
        self._browse_folder: Path | None = None  # None = show all known folders

        self._build_menu()
        self._build_ui()
        self._build_shortcuts()

        # Pre-open DBs for known folders so global view works on startup
        for folder, _ in cfg.get_known_folders():
            if folder.is_dir():
                try:
                    self._known_dbs[folder] = IndexDB(folder)
                except Exception:
                    pass
        if self._known_dbs:
            self._refresh_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = QMenuBar(self)
        self.setMenuBar(mb)

        file_menu = QMenu("&File", self)
        mb.addMenu(file_menu)

        open_act = QAction(
            _icon(QStyle.StandardPixmap.SP_DirOpenIcon), "Open Folder…", self
        )
        open_act.setShortcut(QKeySequence("Ctrl+O"))
        open_act.triggered.connect(self.open_folder)
        file_menu.addAction(open_act)

        new_act = QAction(
            _icon(QStyle.StandardPixmap.SP_FileIcon), "New Query…", self
        )
        new_act.setShortcut(QKeySequence("Ctrl+N"))
        new_act.triggered.connect(self.new_query)
        file_menu.addAction(new_act)

        self._new_template_act = QAction("New from Template…", self)
        self._new_template_act.triggered.connect(self.new_from_template)
        file_menu.addAction(self._new_template_act)

        self._duplicate_act = QAction(
            _icon(QStyle.StandardPixmap.SP_FileLinkIcon), "Duplicate Query…", self
        )
        self._duplicate_act.setShortcut(QKeySequence("Ctrl+D"))
        self._duplicate_act.triggered.connect(self.duplicate_current)
        file_menu.addAction(self._duplicate_act)

        file_menu.addSeparator()

        self._recent_menu = QMenu("Recent Projects", self)
        self._recent_menu.setIcon(_icon(QStyle.StandardPixmap.SP_FileDialogStart))
        file_menu.addMenu(self._recent_menu)
        self._rebuild_recent_menu()

        file_menu.addSeparator()

        reindex_act = QAction(
            _icon(QStyle.StandardPixmap.SP_BrowserReload), "Force Reindex", self
        )
        reindex_act.triggered.connect(self.force_reindex)
        file_menu.addAction(reindex_act)

        view_menu = QMenu("&View", self)
        mb.addMenu(view_menu)

        reveal_act = QAction(
            _icon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Reveal File in Explorer",
            self,
        )
        reveal_act.triggered.connect(self.reveal_in_explorer)
        view_menu.addAction(reveal_act)

        open_ssms_act = QAction("Open in SSMS", self)
        open_ssms_act.triggered.connect(self.open_in_ssms)
        view_menu.addAction(open_ssms_act)

        copy_act = QAction(
            _icon(QStyle.StandardPixmap.SP_DialogSaveButton),
            "Copy SQL (no frontmatter)",
            self,
        )
        copy_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
        copy_act.triggered.connect(self.copy_sql)
        view_menu.addAction(copy_act)

    def _build_ui(self) -> None:
        # Left panel
        self._sidebar = SidebarWidget()
        self._sidebar.open_folder_requested.connect(self.open_folder)
        self._sidebar.folder_selected.connect(self._on_sidebar_folder_selected)
        self._sidebar.folder_remove_requested.connect(self._on_folder_remove_requested)
        self._sidebar.folder_favorite_toggled.connect(self._on_folder_favorite_toggled)
        self._sidebar.tag_selected.connect(self._on_tag_selected)
        self._sidebar.favorites_selected.connect(self._on_favorites_selected)
        self._sidebar.recent_selected.connect(self._on_recent_selected)
        self._sidebar.set_folders(cfg.get_known_folders(), None)

        # Middle panel
        middle = QWidget()
        mid_layout = QVBoxLayout(middle)
        mid_layout.setContentsMargins(0, 4, 0, 0)
        mid_layout.setSpacing(4)

        self._search_bar = SearchBar()
        self._search_bar.search_changed.connect(self._on_search_changed)

        self._query_list = QueryListWidget()
        self._query_list.query_selected.connect(self._on_query_selected)
        self._query_list.context_action.connect(self._on_list_context_action)

        mid_layout.addWidget(self._search_bar)
        mid_layout.addWidget(self._query_list)

        # Right panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._metadata_panel = MetadataPanel()
        self._metadata_panel.table_clicked.connect(self._on_object_clicked)

        # Editor toolbar
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self._toolbar.setStyleSheet(
            "QToolBar { padding: 2px 4px; spacing: 2px; }"
            "QPushButton { padding: 1px 10px; font-size: 12px; max-height: 24px; }"
        )

        self._edit_toggle_btn = QPushButton("✏  Edit")
        self._edit_toggle_btn.setCheckable(True)
        self._edit_toggle_btn.setToolTip("Toggle edit mode (Ctrl+E)")
        self._edit_toggle_btn.clicked.connect(self._toggle_edit_mode)

        self._save_btn = QPushButton("💾  Save")
        self._save_btn.setToolTip("Save changes (Ctrl+S)")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save_current)

        self._cancel_btn = QPushButton("✕  Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_edit)

        self._fav_btn = QPushButton("☆  Favorite")
        self._fav_btn.setToolTip("Toggle favorite (click to star/unstar)")
        self._fav_btn.clicked.connect(self._toggle_favorite)

        self._toolbar.addWidget(self._edit_toggle_btn)
        self._toolbar.addWidget(self._save_btn)
        self._toolbar.addWidget(self._cancel_btn)
        self._toolbar.addSeparator()
        self._toolbar.addWidget(self._fav_btn)
        self._toolbar.addSeparator()
        self._path_label = QLabel("")
        self._path_label.setStyleSheet("color: #888888; padding-left: 4px; font-size: 11px;")
        self._toolbar.addWidget(self._path_label)

        self._editor = CodeEditor()
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
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self.open_command_palette)

    # ------------------------------------------------------------------
    # Folder / indexing
    # ------------------------------------------------------------------

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Queries Folder")
        if folder:
            self.load_folder(Path(folder))

    def load_folder(self, folder: Path) -> None:
        """Open *folder*, index it (incremental), and start watching it."""
        if not folder.is_dir():
            return
        self._stop_watcher()

        self._folder = folder
        self._browse_folder = folder
        db = self._get_or_open_db(folder)
        self._db = db
        cfg.add_recent_project(folder)
        cfg.add_known_folder(folder)
        self._rebuild_recent_menu()
        self._sidebar.set_folders(cfg.get_known_folders(), folder)

        self.setWindowTitle(f"SQLShelf — {folder.name}")
        self._status_bar.showMessage("Indexing…")
        self._reset_editor()

        worker = _IndexWorker(db, folder)
        worker.signals.finished.connect(self._on_index_finished)
        worker.signals.error.connect(
            lambda msg: self._status_bar.showMessage(f"Index error: {msg}")
        )
        QThreadPool.globalInstance().start(worker)

        self._watcher_bridge = _WatcherBridge()
        self._watcher_bridge.files_changed.connect(self._handle_files_changed)
        self._watcher = FolderWatcher(folder, self._watcher_bridge.on_changed)
        self._watcher.start()

    def _on_sidebar_folder_selected(self, folder: Path) -> None:
        """Switch the query list to *folder* without a full reindex."""
        if not folder.is_dir():
            return
        self._folder = folder
        self._browse_folder = folder
        self._db = self._get_or_open_db(folder)
        self._search_bar.blockSignals(True)
        self._search_bar.clear()
        self._search_bar.blockSignals(False)
        self._sidebar.set_folders(cfg.get_known_folders(), folder)
        self._status_bar.showMessage(f"Folder: {folder.name}")
        self._refresh_ui()

    def _get_or_open_db(self, folder: Path) -> IndexDB:
        if folder not in self._known_dbs:
            self._known_dbs[folder] = IndexDB(folder)
        return self._known_dbs[folder]

    def _on_index_finished(self, count: int) -> None:
        self._status_bar.showMessage(f"{count} queries indexed")
        self._refresh_ui()

    def force_reindex(self) -> None:
        if self._db is None or self._folder is None:
            return
        self._status_bar.showMessage("Full reindex…")
        try:
            queries = scan_folder(self._folder)
            self._db.index_all(queries)
            self._status_bar.showMessage(f"{self._db.count()} queries indexed")
            self._refresh_ui()
        except Exception as exc:
            self._status_bar.showMessage(f"Reindex error: {exc}")

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

    def _reset_editor(self) -> None:
        """Clear editor and reset state without reloading from disk."""
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._editor.clear()
        self._metadata_panel.clear()
        self._path_label.setText("")
        self._fav_btn.setText("☆  Favorite")
        self._save_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText("✏  Edit")
        self._current_result = None
        self._current_metadata = {}

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_ui(self) -> None:
        if not self._known_dbs:
            return
        if self._browse_folder is not None:
            db = self._known_dbs.get(self._browse_folder)
            tags = db.get_all_tags() if db else []
        else:
            tags_set: set[str] = set()
            for db in self._known_dbs.values():
                try:
                    tags_set.update(db.get_all_tags())
                except Exception:
                    pass
            tags = sorted(tags_set)
        self._sidebar.set_tags(tags)
        self._run_search()

    def _run_search(self) -> None:
        if not self._known_dbs:
            return
        text = self._search_bar.text()
        results: list[SearchResult] = []
        if self._browse_folder is not None:
            db = self._known_dbs.get(self._browse_folder)
            if db:
                results = db.search(text)
                for r in results:
                    r.folder = self._browse_folder
        else:
            for folder, db in self._known_dbs.items():
                try:
                    folder_results = db.search(text)
                    for r in folder_results:
                        r.folder = folder
                    results.extend(folder_results)
                except Exception:
                    pass
            results.sort(key=lambda r: r.rank)
        self._query_list.set_results(results)

    # ------------------------------------------------------------------
    # Query selection
    # ------------------------------------------------------------------

    def _on_query_selected(self, result: SearchResult) -> None:
        # Discard any unsaved edits without reloading (we're about to load new query)
        if self._edit_mode:
            self._edit_mode = False
            self._editor.setReadOnly(True)
            self._metadata_panel.set_edit_mode(False)
            self._save_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            self._edit_toggle_btn.setChecked(False)
            self._edit_toggle_btn.setText("✏  Edit")

        # Resolve the folder this result comes from (important in multi-folder mode)
        if result.folder is not None and result.folder.is_dir():
            self._folder = result.folder
            self._db = self._known_dbs.get(result.folder, self._db)

        self._current_result = result
        if self._folder is None:
            return
        path = self._folder / result.rel_path
        self._load_query_from_disk(path, result)

        if self._db is not None:
            try:
                self._db.add_recently_viewed(result.rel_path)
            except Exception:
                pass

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
            try:
                obj_map = self._db.get_objects(result.query_id)
                objects = obj_map
            except Exception:
                pass

        self._metadata_panel.set_query(
            title=result.title,
            description=result.description,
            tags=result.tags,
            tables=objects.get("table", []),
            columns=objects.get("column", []),
        )

        if self._db is not None:
            try:
                is_fav = self._db.is_favorite(result.rel_path)
                self._fav_btn.setText("★  Favorite" if is_fav else "☆  Favorite")
            except Exception:
                self._fav_btn.setText("☆  Favorite")

    # ------------------------------------------------------------------
    # Search / tag / sidebar filtering
    # ------------------------------------------------------------------

    def _on_search_changed(self, _text: str) -> None:
        self._run_search()

    def _on_tag_selected(self, tag: str) -> None:
        if not tag:
            # "All queries" clicked — switch to global multi-folder mode
            self._browse_folder = None
            self._sidebar.set_folders(cfg.get_known_folders(), None)
        self._search_bar.blockSignals(True)
        self._search_bar._edit.setText(f"tag:{tag}" if tag else "")
        self._search_bar.blockSignals(False)
        self._refresh_ui()

    def _on_favorites_selected(self) -> None:
        self._search_bar.blockSignals(True)
        self._search_bar.clear()
        self._search_bar.blockSignals(False)
        results: list[SearchResult] = []
        dbs = (
            {self._browse_folder: self._known_dbs[self._browse_folder]}
            if self._browse_folder and self._browse_folder in self._known_dbs
            else self._known_dbs
        )
        for folder, db in dbs.items():
            try:
                for r in db.get_favorites():
                    r.folder = folder
                    results.append(r)
            except Exception:
                pass
        self._query_list.set_results(results)

    def _on_recent_selected(self) -> None:
        self._search_bar.blockSignals(True)
        self._search_bar.clear()
        self._search_bar.blockSignals(False)
        results: list[SearchResult] = []
        dbs = (
            {self._browse_folder: self._known_dbs[self._browse_folder]}
            if self._browse_folder and self._browse_folder in self._known_dbs
            else self._known_dbs
        )
        for folder, db in dbs.items():
            try:
                for r in db.get_recently_viewed():
                    r.folder = folder
                    results.append(r)
            except Exception:
                pass
        self._query_list.set_results(results)

    def _on_object_clicked(self, href: str) -> None:
        """Reverse search: clicking a table/col name filters the query list."""
        self._search_bar.blockSignals(True)
        self._search_bar._edit.setText(href)
        self._search_bar.blockSignals(False)
        self._run_search()

    # ------------------------------------------------------------------
    # Context menu actions from QueryListWidget
    # ------------------------------------------------------------------

    def _on_list_context_action(self, action: str, result: SearchResult) -> None:
        if action == "favorite":
            self._query_list.select_by_rel_path(result.rel_path)
            self._toggle_favorite_for(result)
        elif action == "duplicate":
            self._query_list.select_by_rel_path(result.rel_path)
            self.duplicate_current()
        elif action == "copy":
            body = self._editor.toPlainText()
            if self._current_result and self._current_result.rel_path != result.rel_path:
                # Load body from disk for the right-clicked item
                if self._folder:
                    try:
                        _, body, _ = read_sql_file(self._folder / result.rel_path)
                    except Exception:
                        pass
            QApplication.clipboard().setText(body)
            self._status_bar.showMessage(f"Copied: {result.rel_path}")
        elif action == "reveal":
            self._query_list.select_by_rel_path(result.rel_path)
            self.reveal_in_explorer()

    # ------------------------------------------------------------------
    # Sidebar folder explorer handlers
    # ------------------------------------------------------------------

    def _on_folder_remove_requested(self, folder: Path) -> None:
        cfg.remove_known_folder(folder)
        self._sidebar.set_folders(cfg.get_known_folders(), self._folder)

    def _on_folder_favorite_toggled(self, folder: Path) -> None:
        cfg.toggle_folder_favorite(folder)
        self._sidebar.set_folders(cfg.get_known_folders(), self._folder)

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------

    def _toggle_favorite(self) -> None:
        if self._current_result is None:
            return
        self._toggle_favorite_for(self._current_result)

    def _toggle_favorite_for(self, result: SearchResult) -> None:
        if self._db is None:
            return
        try:
            is_fav = self._db.toggle_favorite(result.rel_path)
        except Exception:
            return
        label = "★  Favorite" if is_fav else "☆  Favorite"
        if self._current_result and self._current_result.rel_path == result.rel_path:
            self._fav_btn.setText(label)
        self._status_bar.showMessage(
            f"{'Added to' if is_fav else 'Removed from'} favorites: {result.rel_path}"
        )

    # ------------------------------------------------------------------
    # Command palette (Ctrl+P)
    # ------------------------------------------------------------------

    def open_command_palette(self) -> None:
        if self._db is None:
            return
        results = self._db.search("")
        palette = CommandPalette(results, self)
        palette.query_selected.connect(self._on_palette_selected)
        geo = self.geometry()
        pw, ph = palette.width(), palette.height()
        palette.move(geo.center().x() - pw // 2, geo.center().y() - ph // 2)
        palette.exec()

    def _on_palette_selected(self, result: SearchResult) -> None:
        self._sidebar.select_all()
        self._search_bar.blockSignals(True)
        self._search_bar.clear()
        self._search_bar.blockSignals(False)
        self._run_search()
        self._query_list.select_by_rel_path(result.rel_path)

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
        self._edit_toggle_btn.setText("✏  Editing…")

    def _cancel_edit_mode(self) -> None:
        """Exit edit mode, discarding unsaved changes by reloading from disk."""
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText("✏  Edit")
        # Reload from disk to discard unsaved changes
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

        if self._db is not None:
            updated = [q for q in scan_folder(self._folder) if q.path == path]
            if updated:
                self._db.upsert_query(updated[0])

        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText("✏  Edit")
        self._refresh_ui()
        self._status_bar.showMessage(f"Saved: {self._current_result.rel_path}")

    # ------------------------------------------------------------------
    # New query / duplicate / templates
    # ------------------------------------------------------------------

    def new_query(self) -> None:
        if self._folder is None:
            QMessageBox.information(self, "No Folder", "Open a folder first.")
            return
        subfolders = self._list_subfolders()
        dlg = NewQueryDialog(self._folder, subfolders, self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.created_path is not None:
            self._post_create(dlg.created_path)

    def new_from_template(self) -> None:
        if self._folder is None:
            QMessageBox.information(self, "No Folder", "Open a folder first.")
            return
        if not list_templates():
            QMessageBox.information(
                self,
                "No Templates",
                f"No templates found.\n\nCreate .sql files in:\n"
                f"{Path.home() / '.sqlshelf' / 'templates'}",
            )
            return
        dlg = TemplateDialog(self._folder, self._list_subfolders(), self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.created_path is not None:
            self._post_create(dlg.created_path)

    def duplicate_current(self) -> None:
        if self._current_result is None or self._folder is None:
            QMessageBox.information(self, "No Query", "Select a query to duplicate.")
            return

        src_path = self._folder / self._current_result.rel_path
        new_title, ok = QInputDialog.getText(
            self,
            "Duplicate Query",
            "New query title:",
            text=f"Copy of {self._current_result.title}",
        )
        if not ok or not new_title.strip():
            return

        new_title = new_title.strip()
        try:
            metadata, body, _has_fm = read_sql_file(src_path)
        except Exception as exc:
            QMessageBox.critical(self, "Duplicate Error", str(exc))
            return

        from .new_query_dialog import _safe_filename

        filename = _safe_filename(new_title) + ".sql"
        dest_path = src_path.parent / filename
        counter = 1
        while dest_path.exists():
            dest_path = src_path.parent / f"{_safe_filename(new_title)}_{counter}.sql"
            counter += 1

        from datetime import date

        new_meta = dict(metadata)
        new_meta["title"] = new_title
        today = date.today().isoformat()
        new_meta["created"] = today
        new_meta["updated"] = today

        try:
            write_sql_file(dest_path, new_meta, body)
        except Exception as exc:
            QMessageBox.critical(self, "Duplicate Error", str(exc))
            return

        self._post_create(dest_path)

    def _list_subfolders(self) -> list[str]:
        if self._folder is None:
            return []
        return [
            str(p.relative_to(self._folder))
            for p in self._folder.rglob("*")
            if p.is_dir() and ".sqlshelf" not in p.parts
        ]

    def _post_create(self, path: Path) -> None:
        if self._folder is None:
            return
        updated = [q for q in scan_folder(self._folder) if q.path == path]
        if updated and self._db is not None:
            self._db.upsert_query(updated[0])
        self._refresh_ui()
        self._query_list.select_by_rel_path(
            path.relative_to(self._folder).as_posix()
        )
        self._status_bar.showMessage(f"Created: {path.name}")

    # ------------------------------------------------------------------
    # Watcher handler
    # ------------------------------------------------------------------

    def _handle_files_changed(self, modified: set[Path], deleted: set[Path]) -> None:
        if self._db is None or self._folder is None:
            return
        for path in modified:
            if path.is_file():
                try:
                    hits = [q for q in scan_folder(self._folder) if q.path == path]
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
        body = self._editor.toPlainText()
        if body:
            QApplication.clipboard().setText(body)
            self._status_bar.showMessage("SQL body copied to clipboard")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._stop_watcher()
        for db in self._known_dbs.values():
            try:
                db.close()
            except Exception:
                pass
        super().closeEvent(event)
