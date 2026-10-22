from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.frontmatter import read_sql_file, write_sql_file
from ..core.i18n import available_languages, get_language, set_language, tr
from ..core.index_db import IndexDB, IndexStats
from ..core.models import SearchResult
from ..core.navigation import NavHistory, NavState, root_of, search_text, with_search
from ..core.scanner import scan_file, scan_folder
from ..core.snippets import list_templates
from ..core.watcher import FolderWatcher
from . import tokens as _tk
from .address_bar import AddressBar
from .code_editor import CodeEditor
from .command_bar import CommandBar
from .command_palette import CommandPalette
from .highlighter import SqlHighlighter
from .metadata_panel import MetadataPanel
from .new_query_dialog import NewQueryDialog
from .query_list import QueryListWidget
from .search_bar import SearchBar
from .sidebar import SidebarWidget
from .status_bar import StatusBar
from .template_dialog import TemplateDialog
from .theme import apply_theme, mono_font
from .tokens import (
    ACCENT,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
)


def _icon(style_enum) -> QIcon:
    """Return a standard Qt style icon."""
    return QApplication.style().standardIcon(style_enum)


# ---------------------------------------------------------------------------
# Background indexing worker
# ---------------------------------------------------------------------------

# Indexing runs on its own pool. On the global pool a long reindex occupied the
# only available threads, so searches and query loads queued behind it and the
# window looked frozen even though nothing was blocking the GUI thread itself.
_INDEX_POOL = QThreadPool()
_INDEX_POOL.setMaxThreadCount(1)

# Emitting one progress signal per file floods the GUI event loop: each one is a
# queued call that relabels the dialog and repaints it. Files arrive far faster
# than a human can read them, so coalesce to a readable rate.
_PROGRESS_INTERVAL_MS = 60


class _IndexWorkerSignals(QObject):
    started = Signal(object)  # the folder; jobs queue on a one-thread pool
    finished = Signal(int)
    error = Signal(str)
    progress = Signal(int, int)  # (current, total)


def _throttled(emit) -> "object":
    """Wrap a progress emitter so it fires at most every _PROGRESS_INTERVAL_MS.

    The final call always gets through, so the bar never stops short of 100%.
    """
    last = [0.0]

    def _cb(current: int, total: int) -> None:
        now = time.monotonic() * 1000.0
        if current >= total or now - last[0] >= _PROGRESS_INTERVAL_MS:
            last[0] = now
            emit(current, total)

    return _cb


class _IndexWorker(QRunnable):
    def __init__(self, db: IndexDB, folder: Path) -> None:
        super().__init__()
        self.signals = _IndexWorkerSignals()
        self._db = db
        self._folder = folder

    def run(self) -> None:
        self.signals.started.emit(self._folder)
        try:
            queries = scan_folder(self._folder)
            total = len(queries)
            if total:
                self.signals.progress.emit(0, total)

            _cb = _throttled(self.signals.progress.emit)

            self._db.index_incremental(queries, progress_cb=_cb)
            self.signals.finished.emit(self._db.count())
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _WatchSyncWorker(QRunnable):
    """Apply file-watcher events to the index, off the GUI thread.

    This used to run inline in the watcher slot, calling scan_folder() once per
    changed file — seconds of frozen window every time a folder was synced.
    """

    def __init__(self, db: IndexDB, modified: set[Path], deleted: set[Path]) -> None:
        super().__init__()
        self.signals = _IndexWorkerSignals()
        self._db = db
        self._modified = modified
        self._deleted = deleted

    def run(self) -> None:
        for path in self._modified:
            try:
                query = scan_file(path)
                if query is not None:
                    self._db.upsert_query(query)
            except Exception:
                continue
        for path in self._deleted:
            try:
                self._db.remove_file(path)
            except Exception:
                continue
        try:
            self.signals.finished.emit(self._db.count())
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _ForceReindexWorker(QRunnable):
    def __init__(self, db: IndexDB, folder: Path) -> None:
        super().__init__()
        self.signals = _IndexWorkerSignals()
        self._db = db
        self._folder = folder

    def run(self) -> None:
        self.signals.started.emit(self._folder)
        try:
            queries = scan_folder(self._folder)
            total = len(queries)
            if total:
                self.signals.progress.emit(0, total)

            _cb = _throttled(self.signals.progress.emit)

            self._db.index_all(queries, progress_cb=_cb)
            self.signals.finished.emit(self._db.count())
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Background search worker
# ---------------------------------------------------------------------------


class _SearchWorkerSignals(QObject):
    results_ready = Signal(list, list, int)  # (results, sidebar tags, generation)


class _SearchWorker(QRunnable):
    def __init__(
        self,
        dbs: dict,
        text: str,
        browse_folder,
        mode: str,
        gen: int,
    ) -> None:
        super().__init__()
        self.signals = _SearchWorkerSignals()
        self._dbs = dbs
        self._text = text
        self._browse_folder = browse_folder
        self._mode = mode
        self._gen = gen

    def run(self) -> None:
        try:
            results: list[SearchResult] = []
            scoped_dbs = (
                {self._browse_folder: self._dbs[self._browse_folder]}
                if self._browse_folder is not None and self._browse_folder in self._dbs
                else self._dbs
            )

            if self._mode == "favorites":
                # Favorites intentionally span every known folder.
                for folder, db in self._dbs.items():
                    try:
                        for result in db.get_favorites():
                            result.folder = folder
                            results.append(result)
                    except Exception:
                        continue
            elif self._mode == "recent":
                for folder, db in scoped_dbs.items():
                    try:
                        for result in db.get_recently_viewed():
                            result.folder = folder
                            results.append(result)
                    except Exception:
                        continue
            else:
                if self._browse_folder is not None:
                    db = self._dbs.get(self._browse_folder)
                    if db:
                        results = db.search(self._text)
                        for result in results:
                            result.folder = self._browse_folder
                else:
                    for folder, db in self._dbs.items():
                        try:
                            folder_results = db.search(self._text)
                        except Exception:
                            continue
                        for result in folder_results:
                            result.folder = folder
                        results.extend(folder_results)
                    # FTS results: rank by relevance; otherwise most recently modified.
                    if any(result.rank != 0.0 for result in results):
                        results.sort(key=lambda result: result.rank)
                    else:
                        results.sort(key=lambda result: result.file_mtime, reverse=True)

            tags_set: set[str] = set()
            for db in scoped_dbs.values():
                try:
                    tags_set.update(db.get_all_tags())
                except Exception:
                    continue
            tags = sorted(tags_set)
        except Exception:
            results = []
            tags = []
        self.signals.results_ready.emit(results, tags, self._gen)


# ---------------------------------------------------------------------------
# Background query loader. File reads (especially cloud-backed paths) and
# IndexDB calls can both block, so none of them may run on the GUI thread.
# ---------------------------------------------------------------------------


class _LoadQueryWorkerSignals(QObject):
    finished = Signal(object, int)  # (payload, generation)


class _LoadQueryWorker(QRunnable):
    def __init__(
        self,
        db: IndexDB | None,
        path: Path,
        result: SearchResult,
        gen: int,
        track_access: bool = True,
    ) -> None:
        super().__init__()
        self.signals = _LoadQueryWorkerSignals()
        self._db = db
        self._path = path
        self._result = result
        self._gen = gen
        self._track_access = track_access

    def run(self) -> None:
        metadata, body, _has_frontmatter = read_sql_file(self._path)
        objects: dict[str, list[str]] = {"table": [], "column": [], "alias": []}
        is_favorite = False
        try:
            if self._db is not None:
                objects = self._db.get_objects(self._result.query_id)
                is_favorite = self._db.is_favorite(self._result.rel_path)
                if self._track_access:
                    self._db.add_recently_viewed(self._result.rel_path)
                    self._db.record_access(self._result.rel_path, "open")
        except Exception:
            pass
        payload = (
            self._path,
            self._result,
            metadata,
            body,
            objects,
            is_favorite,
        )
        self.signals.finished.emit(payload, self._gen)


class _RecordAccessWorker(QRunnable):
    """Best-effort access logging that never waits on the GUI thread."""

    def __init__(self, db: IndexDB, rel_path: str, action: str) -> None:
        super().__init__()
        self._db = db
        self._rel_path = rel_path
        self._action = action

    def run(self) -> None:
        try:
            self._db.record_access(self._rel_path, self._action)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background update-check worker
# ---------------------------------------------------------------------------


class _UpdateWorkerSignals(QObject):
    finished = Signal(object)  # UpdateInfo
    error = Signal(str)


class _UpdateWorker(QRunnable):
    def __init__(self, current_version: str) -> None:
        super().__init__()
        self.signals = _UpdateWorkerSignals()
        self._version = current_version

    def run(self) -> None:
        try:
            from ..core.updater import check_for_updates

            info = check_for_updates(self._version)
            self.signals.finished.emit(info)
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Update dialog
# ---------------------------------------------------------------------------


class _UpdateDialog(QDialog):
    """Shows update-check progress, then result (found / up-to-date / error)."""

    def __init__(self, current_version: str, parent=None) -> None:
        super().__init__(parent)
        self._current_version = current_version
        self.setWindowTitle(tr("update.dialog_title"))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setFixedWidth(400)
        self._setup_ui()
        self._start_check()

    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24, 24, 24, 20)
        self._layout.setSpacing(16)

        self._title_label = QLabel(tr("update.checking"))
        font = self._title_label.font()
        font.setPointSize(font.pointSize() + 1)
        font.setBold(True)
        self._title_label.setFont(font)
        self._title_label.setWordWrap(True)
        self._layout.addWidget(self._title_label)

        self._body_label = QLabel("")
        self._body_label.setWordWrap(True)
        self._body_label.setOpenExternalLinks(True)
        self._body_label.setTextFormat(Qt.TextFormat.RichText)
        self._body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._body_label.hide()
        self._layout.addWidget(self._body_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._layout.addWidget(self._progress)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._download_btn = QPushButton(tr("update.download_btn"))
        self._download_btn.hide()
        self._download_btn.clicked.connect(self._on_download)
        btn_row.addWidget(self._download_btn)

        self._releases_btn = QPushButton(tr("update.open_releases_btn"))
        self._releases_btn.hide()
        self._releases_btn.clicked.connect(self._on_open_releases)
        btn_row.addWidget(self._releases_btn)

        self._close_btn = QPushButton(tr("update.close_btn"))
        self._close_btn.setFixedWidth(80)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)

        self._layout.addLayout(btn_row)

        self._accent = ACCENT
        self._release_url: str = ""
        self._installer_url: str | None = None

    def _start_check(self) -> None:
        worker = _UpdateWorker(self._current_version)
        worker.signals.finished.connect(self._on_result)
        worker.signals.error.connect(self._on_error)
        QThreadPool.globalInstance().start(worker)

    # ------------------------------------------------------------------

    def _on_result(self, info) -> None:
        self._progress.hide()
        self._release_url = info.release_url
        self._installer_url = info.installer_asset_url()

        if info.has_update:
            self._title_label.setText(tr("update.available_title"))
            body = tr(
                "update.available_body",
                latest=info.latest_version,
                current=info.current_version,
            )
            self._body_label.setText(body)
            self._body_label.show()
            if self._installer_url:
                self._download_btn.show()
            self._releases_btn.show()
        else:
            self._title_label.setText(
                tr("update.up_to_date", version=info.current_version)
            )

        self.adjustSize()

    def _on_error(self, msg: str) -> None:
        self._progress.hide()
        self._title_label.setText(tr("update.error", msg=msg))
        self.adjustSize()

    def _on_download(self) -> None:
        import subprocess

        if self._installer_url:
            import tempfile
            import urllib.request

            suffix = self._installer_url.split("/")[-1]
            dest = Path(tempfile.gettempdir()) / suffix
            try:
                urllib.request.urlretrieve(self._installer_url, dest)
                subprocess.Popen([str(dest)])
                self.accept()
            except Exception as exc:
                self._title_label.setText(tr("update.error", msg=str(exc)))
                self._progress.hide()

    def _on_open_releases(self) -> None:
        import webbrowser

        webbrowser.open(self._release_url)
        self.accept()


# ---------------------------------------------------------------------------
# Statistics dialog
# ---------------------------------------------------------------------------


class _StatsDialog(QDialog):
    """Aggregated statistics across all loaded project folders."""

    def __init__(self, known_dbs: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("stats.title"))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setFixedWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(20)

        if not known_dbs:
            lbl = QLabel(tr("stats.no_data"))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)
        else:
            combined = IndexStats.empty()
            for db in known_dbs.values():
                try:
                    combined.merge(db.get_stats())
                except Exception:
                    pass

            grid = QVBoxLayout()
            grid.setSpacing(10)

            rows = [
                (tr("stats.folders"), str(len(known_dbs))),
                (tr("stats.queries"), str(combined.queries)),
                (tr("stats.tags"), str(len(combined.tag_names))),
                (tr("stats.favorites"), str(combined.favorites)),
                (tr("stats.tables"), str(len(combined.table_names))),
                (tr("stats.columns"), str(len(combined.column_names))),
            ]

            for label_text, value_text in rows:
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                lbl = QLabel(label_text)
                val = QLabel(value_text)
                val.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                font = val.font()
                font.setBold(True)
                val.setFont(font)
                row_layout.addWidget(lbl)
                row_layout.addStretch()
                row_layout.addWidget(val)
                grid.addWidget(row_widget)

            layout.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(tr("stats.close_btn"))
        close_btn.setFixedWidth(80)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


# ---------------------------------------------------------------------------
# Reports dialog
# ---------------------------------------------------------------------------


class _ReportsDialog(QDialog):
    """Access-log based usage reports (most accessed queries and tags)."""

    def __init__(self, known_dbs: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("reports.title"))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(20)

        if not known_dbs:
            lbl = QLabel(tr("reports.no_data"))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)
        else:
            multi_folder = len(known_dbs) > 1
            top_queries: list[tuple[str, int]] = []
            tag_counts: dict[str, int] = {}
            total_accesses = 0

            for folder, db in known_dbs.items():
                try:
                    total_accesses += db.get_access_total()
                    for stat in db.get_top_queries(limit=10):
                        label = (
                            f"{stat.label}  ({folder.name})"
                            if multi_folder
                            else stat.label
                        )
                        top_queries.append((label, stat.count))
                    for stat in db.get_top_tags(limit=10):
                        tag_counts[stat.label] = (
                            tag_counts.get(stat.label, 0) + stat.count
                        )
                except Exception:
                    pass

            top_queries.sort(key=lambda row: row[1], reverse=True)
            top_queries = top_queries[:10]
            top_tags = sorted(tag_counts.items(), key=lambda row: row[1], reverse=True)[
                :10
            ]

            total_row = QHBoxLayout()
            total_row.setContentsMargins(0, 0, 0, 0)
            total_lbl = QLabel(tr("reports.total_accesses"))
            total_val = QLabel(str(total_accesses))
            total_val.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            font = total_val.font()
            font.setBold(True)
            total_val.setFont(font)
            total_row.addWidget(total_lbl)
            total_row.addStretch()
            total_row.addWidget(total_val)
            layout.addLayout(total_row)

            layout.addWidget(self._section(tr("reports.top_queries"), top_queries))
            layout.addWidget(self._section(tr("reports.top_tags"), top_tags))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(tr("stats.close_btn"))
        close_btn.setFixedWidth(80)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    @staticmethod
    def _section(title: str, rows: list[tuple[str, int]]) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        header = QLabel(title)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        v.addWidget(header)

        if not rows:
            empty = QLabel(tr("reports.no_activity"))
            empty.setStyleSheet("color: gray;")
            v.addWidget(empty)
        else:
            for label_text, count in rows:
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                lbl = QLabel(label_text)
                lbl.setWordWrap(True)
                val = QLabel(str(count))
                val.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                row_layout.addWidget(lbl)
                row_layout.addStretch()
                row_layout.addWidget(val)
                v.addWidget(row_widget)

        return box


# ---------------------------------------------------------------------------
# Watcher → Qt bridge
# ---------------------------------------------------------------------------


class _WatcherBridge(QObject):
    """Carries one folder's watchdog callbacks onto the GUI thread.

    The callback runs on the observer thread; emitting a signal from there
    queues the slot on the GUI thread, tagged with the folder it came from.
    """

    files_changed = Signal(object, object, object)  # (folder, modified, deleted)

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self._folder = folder

    def on_changed(self, modified: set[Path], deleted: set[Path]) -> None:
        self.files_changed.emit(self._folder, modified, deleted)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

# Sidebar | list | details panel, until the user drags the splitter.
_DEFAULT_SPLITTER_SIZES = (236, 644, 520)
_SIDEBAR_MIN_WIDTH = 200
_LIST_MIN_WIDTH = 360
_PANEL_MIN_WIDTH = 420


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SQLShelf")
        self.setMinimumSize(1100, 700)
        self.resize(1400, 860)

        # The library folder of the query in view, and its index.
        self._folder: Path | None = None
        self._db: IndexDB | None = None
        # One watcher per library folder, so every index stays current.
        self._watchers: dict[Path, tuple[FolderWatcher, _WatcherBridge]] = {}

        self._current_result: SearchResult | None = None
        self._current_metadata: dict = {}
        self._edit_mode = False
        self._quitting = False

        # Multi-folder support
        self._known_dbs: dict[Path, IndexDB] = {}

        # Where the user is (folder, tag, search…), with back/forward history.
        self._nav = NavHistory()

        # Indexing jobs queued or running, for the status bar progress.
        self._index_jobs = 0
        self._indexing_name = ""

        # Details panel: the user's choice, and its width while hidden.
        self._panel_visible = cfg.get_panel_visible()
        saved_sizes = cfg.get_splitter_sizes()
        self._panel_width = (saved_sizes or _DEFAULT_SPLITTER_SIZES)[2]

        # Async search state
        self._search_gen: int = 0  # incremented per dispatch; workers check this
        self._pending_select: str | None = None  # rel_path to auto-select after search

        # Async query loading state. Rapid list navigation is debounced so only
        # the row where the user settles performs disk and database I/O.
        self._load_gen: int = 0
        self._pending_load: (
            tuple[IndexDB | None, Path, SearchResult, int, bool] | None
        ) = None
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(75)
        self._load_timer.timeout.connect(self._dispatch_query_load)

        self._build_menu()
        self._build_ui()
        self._build_shortcuts()
        self._setup_tray()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)  # ms to wait after last keystroke
        self._search_timer.timeout.connect(self._on_search_timeout)

        # Pre-open DBs for known folders so global view works on startup
        for folder, _ in cfg.get_known_folders():
            if folder.is_dir():
                try:
                    self._known_dbs[folder] = IndexDB(folder)
                except Exception:
                    pass
        self._sync_watchers()
        self._apply_nav()

        # Silent startup update check — runs after 5 s to avoid slowing launch
        self._startup_update_timer = QTimer(self)
        self._startup_update_timer.setSingleShot(True)
        self._startup_update_timer.setInterval(5000)
        self._startup_update_timer.timeout.connect(self._silent_update_check)
        self._startup_update_timer.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = QMenuBar(self)
        self.setMenuBar(mb)

        self._file_menu = QMenu(tr("menu.file"), self)
        mb.addMenu(self._file_menu)

        self._open_act = QAction(
            _icon(QStyle.StandardPixmap.SP_DirOpenIcon), tr("menu.open_folder"), self
        )
        self._open_act.setShortcut(QKeySequence("Ctrl+O"))
        self._open_act.triggered.connect(self.open_folder)
        self._file_menu.addAction(self._open_act)

        self._new_act = QAction(
            _icon(QStyle.StandardPixmap.SP_FileIcon), tr("menu.new_query"), self
        )
        self._new_act.setShortcut(QKeySequence("Ctrl+N"))
        self._new_act.triggered.connect(self.new_query)
        self._file_menu.addAction(self._new_act)

        self._duplicate_act = QAction(
            _icon(QStyle.StandardPixmap.SP_FileLinkIcon),
            tr("menu.duplicate_query"),
            self,
        )
        self._duplicate_act.setShortcut(QKeySequence("Ctrl+D"))
        self._duplicate_act.triggered.connect(self.duplicate_current)
        self._file_menu.addAction(self._duplicate_act)

        self._file_menu.addSeparator()

        self._recent_menu = QMenu(tr("menu.recent_projects"), self)
        self._recent_menu.setIcon(_icon(QStyle.StandardPixmap.SP_FileDialogStart))
        self._file_menu.addMenu(self._recent_menu)
        self._rebuild_recent_menu()

        self._file_menu.addSeparator()

        self._reindex_act = QAction(
            _icon(QStyle.StandardPixmap.SP_BrowserReload),
            tr("menu.force_reindex"),
            self,
        )
        self._reindex_act.triggered.connect(self.force_reindex)
        self._file_menu.addAction(self._reindex_act)

        self._file_menu.addSeparator()

        self._quit_act = QAction(
            _icon(QStyle.StandardPixmap.SP_DialogCloseButton), tr("menu.quit"), self
        )
        self._quit_act.setShortcut(QKeySequence("Alt+F4"))
        self._quit_act.triggered.connect(self._quit_app)
        self._file_menu.addAction(self._quit_act)

        self._edit_menu = QMenu(tr("menu.edit"), self)
        mb.addMenu(self._edit_menu)

        self._copy_frontmatter_template_act = QAction(
            tr("menu.copy_frontmatter_template"), self
        )
        self._copy_frontmatter_template_act.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self._copy_frontmatter_template_act.triggered.connect(
            self.copy_frontmatter_template
        )
        self._edit_menu.addAction(self._copy_frontmatter_template_act)

        self._edit_menu.addSeparator()

        self._preferences_act = QAction(tr("menu.preferences"), self)
        self._preferences_act.triggered.connect(self._show_preferences)
        self._edit_menu.addAction(self._preferences_act)

        self._view_menu = QMenu(tr("menu.view"), self)
        mb.addMenu(self._view_menu)

        self._settings_menu = QMenu(tr("menu.settings"), self)
        mb.addMenu(self._settings_menu)

        # Theme submenu
        self._theme_menu = QMenu(tr("menu.theme"), self)
        self._settings_menu.addMenu(self._theme_menu)

        current_theme = cfg.get_theme()
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self._theme_acts: dict[str, QAction] = {}
        for key, label_key in [
            ("dark", "menu.theme_dark"),
            ("light", "menu.theme_light"),
        ]:
            act = QAction(tr(label_key), self)
            act.setCheckable(True)
            act.setChecked(key == current_theme)
            act.triggered.connect(lambda checked, k=key: self._change_theme(k))
            theme_group.addAction(act)
            self._theme_menu.addAction(act)
            self._theme_acts[key] = act

        self._settings_menu.addSeparator()

        # Language submenu
        self._language_menu = QMenu(tr("menu.language"), self)
        self._settings_menu.addMenu(self._language_menu)
        self._rebuild_language_menu()

        self._help_menu = QMenu(tr("menu.help"), self)
        mb.addMenu(self._help_menu)
        self._help_act = QAction(tr("menu.help_action"), self)
        self._help_act.setShortcut(QKeySequence("F1"))
        self._help_act.triggered.connect(self._show_help)
        self._help_menu.addAction(self._help_act)
        self._stats_act = QAction(tr("menu.statistics"), self)
        self._stats_act.triggered.connect(self._show_statistics)
        self._help_menu.addAction(self._stats_act)
        self._reports_act = QAction(tr("menu.reports"), self)
        self._reports_act.triggered.connect(self._show_reports)
        self._help_menu.addAction(self._reports_act)
        self._help_menu.addSeparator()
        self._check_updates_act = QAction(tr("menu.check_for_updates"), self)
        self._check_updates_act.triggered.connect(self._check_for_updates)
        self._help_menu.addAction(self._check_updates_act)
        self._help_menu.addSeparator()
        self._about_act = QAction(tr("menu.about"), self)
        self._about_act.triggered.connect(self._show_about)
        self._help_menu.addAction(self._about_act)

        self._reveal_act = QAction(
            _icon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            tr("menu.reveal_in_explorer"),
            self,
        )
        self._reveal_act.triggered.connect(self.reveal_in_explorer)
        self._view_menu.addAction(self._reveal_act)

        self._copy_act = QAction(
            _icon(QStyle.StandardPixmap.SP_DialogSaveButton),
            tr("menu.copy_sql"),
            self,
        )
        self._copy_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._copy_act.triggered.connect(self.copy_sql)
        self._view_menu.addAction(self._copy_act)

        self._view_menu.addSeparator()

        self._back_act = QAction(tr("menu.go_back"), self)
        self._back_act.setShortcut(QKeySequence("Alt+Left"))
        self._back_act.triggered.connect(self.go_back)
        self._view_menu.addAction(self._back_act)

        self._forward_act = QAction(tr("menu.go_forward"), self)
        self._forward_act.setShortcut(QKeySequence("Alt+Right"))
        self._forward_act.triggered.connect(self.go_forward)
        self._view_menu.addAction(self._forward_act)

        self._up_act = QAction(tr("menu.go_up"), self)
        self._up_act.setShortcut(QKeySequence("Alt+Up"))
        self._up_act.triggered.connect(self.go_up)
        self._view_menu.addAction(self._up_act)

        self._view_menu.addSeparator()

        self._panel_act = QAction(tr("menu.toggle_panel"), self)
        self._panel_act.setShortcut(QKeySequence("Alt+P"))
        self._panel_act.setCheckable(True)
        self._panel_act.setChecked(self._panel_visible)
        self._panel_act.toggled.connect(self._set_panel_visible)
        self._view_menu.addAction(self._panel_act)

        self._menu_bar_act = QAction(tr("menu.show_menu_bar"), self)
        self._menu_bar_act.setCheckable(True)
        self._menu_bar_act.setChecked(cfg.get_show_menu_bar())
        self._menu_bar_act.toggled.connect(self._set_menu_bar_visible)
        self._view_menu.addAction(self._menu_bar_act)

        # The ⋯ button in the command bar holds the same menus, so the menu
        # bar can stay hidden without losing anything.
        self._more_menu = QMenu(self)
        for menu in (
            self._file_menu,
            self._edit_menu,
            self._view_menu,
            self._settings_menu,
            self._help_menu,
        ):
            self._more_menu.addMenu(menu)
            self._register_shortcuts(menu)

        mb.setVisible(cfg.get_show_menu_bar())

    def _register_shortcuts(self, menu: QMenu) -> None:
        """Add *menu*'s shortcut actions to the window itself.

        An action reachable only through a hidden menu bar never fires its
        shortcut; one added to the window works whatever menu shows it.
        """
        for act in menu.actions():
            submenu = act.menu()
            if isinstance(submenu, QMenu):
                self._register_shortcuts(submenu)
            elif not act.shortcut().isEmpty():
                self.addAction(act)

    def _set_menu_bar_visible(self, visible: bool) -> None:
        self.menuBar().setVisible(visible)
        cfg.set_show_menu_bar(visible)

    def _rebuild_language_menu(self) -> None:
        self._language_menu.clear()
        current = get_language()
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        for code, name in available_languages():
            act = QAction(name, self)
            act.setCheckable(True)
            act.setChecked(code == current)
            act.triggered.connect(lambda checked, c=code: self._change_language(c))
            lang_group.addAction(act)
            self._language_menu.addAction(act)

    def _build_ui(self) -> None:
        # Left panel
        self._sidebar = SidebarWidget()
        self._sidebar.open_folder_requested.connect(self.open_folder)
        self._sidebar.folder_selected.connect(self._on_sidebar_folder_selected)
        self._sidebar.folder_remove_requested.connect(self._on_folder_remove_requested)
        self._sidebar.folder_deindex_requested.connect(
            self._on_folder_deindex_requested
        )
        self._sidebar.folder_favorite_toggled.connect(self._on_folder_favorite_toggled)
        self._sidebar.tag_selected.connect(self._on_tag_selected)
        self._sidebar.favorites_selected.connect(self._on_favorites_selected)
        self._sidebar.recent_selected.connect(self._on_recent_selected)
        self._sidebar.set_folders(cfg.get_known_folders(), None)

        # Address and command bars span the window above the three panels.
        self._search_bar = SearchBar()
        self._search_bar.search_changed.connect(self._on_search_changed)

        self._address_bar = AddressBar(self._search_bar)
        self._address_bar.navigate_requested.connect(self._navigate)
        self._address_bar.back_requested.connect(self.go_back)
        self._address_bar.forward_requested.connect(self.go_forward)
        self._address_bar.up_requested.connect(self.go_up)
        self._address_bar.reindex_requested.connect(self.force_reindex)

        self._command_bar = CommandBar(self._more_menu)
        self._command_bar.new_query_requested.connect(self.new_query)
        self._command_bar.open_folder_requested.connect(self.open_folder)
        self._command_bar.quick_search_requested.connect(self.open_command_palette)
        self._command_bar.sort_requested.connect(self._on_sort_requested)
        self._command_bar.group_requested.connect(self._on_group_requested)
        self._command_bar.view_requested.connect(self._on_view_requested)
        self._command_bar.panel_toggled.connect(self._set_panel_visible)
        self._command_bar.set_panel_visible(self._panel_visible)

        # Middle panel — the query list
        middle = QWidget()
        mid_layout = QVBoxLayout(middle)
        mid_layout.setContentsMargins(8, 8, 8, 4)
        mid_layout.setSpacing(0)

        self._query_list = QueryListWidget()
        self._query_list.set_sort(cfg.get_sort_key())
        self._query_list.query_selected.connect(self._on_query_selected)
        self._query_list.context_action.connect(self._on_list_context_action)
        mid_layout.addWidget(self._query_list)

        # Right panel
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 12, 0)
        right_layout.setSpacing(0)

        self._metadata_panel = MetadataPanel()
        self._metadata_panel.navigate_requested.connect(self._on_navigate_requested)
        self._metadata_panel.filter_requested.connect(self._on_filter_requested)
        self._metadata_panel.favorite_toggled.connect(self._toggle_favorite)
        self._metadata_panel.reveal_requested.connect(self.reveal_in_explorer)
        self._metadata_panel.command_palette_requested.connect(
            self.open_command_palette
        )

        # Editor toolbar
        self._toolbar = QWidget()
        self._toolbar.setObjectName("EditorToolBar")
        self._toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb_layout = QHBoxLayout(self._toolbar)
        tb_layout.setContentsMargins(10, 6, 8, 6)
        tb_layout.setSpacing(6)

        self._edit_toggle_btn = QPushButton(tr("editor.btn_edit"))
        self._edit_toggle_btn.setCheckable(True)
        self._edit_toggle_btn.setToolTip(tr("editor.tooltip_edit"))
        self._edit_toggle_btn.clicked.connect(self._toggle_edit_mode)

        self._save_btn = QPushButton(tr("editor.btn_save"))
        self._save_btn.setToolTip(tr("editor.tooltip_save"))
        self._save_btn.clicked.connect(self.save_current)

        self._cancel_btn = QPushButton(tr("editor.btn_cancel"))
        self._cancel_btn.clicked.connect(self._cancel_edit)

        self._select_all_btn = QPushButton(tr("editor.btn_select_all"))
        self._select_all_btn.setToolTip(tr("editor.tooltip_select_all"))
        self._select_all_btn.clicked.connect(self._editor_select_all)

        self._copy_query_btn = QPushButton(tr("editor.btn_copy"))
        self._copy_query_btn.setToolTip(tr("editor.tooltip_copy"))
        self._copy_query_btn.clicked.connect(self._editor_copy_query)

        tb_layout.addWidget(self._edit_toggle_btn)
        tb_layout.addWidget(self._save_btn)
        tb_layout.addWidget(self._cancel_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self._select_all_btn)
        tb_layout.addWidget(self._copy_query_btn)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._select_all_btn.setVisible(False)
        self._copy_query_btn.setVisible(False)

        self._editor = CodeEditor()
        self._editor.setReadOnly(True)
        self._editor.setFont(mono_font())
        self._highlighter = SqlHighlighter(self._editor.document())
        self._editor.set_syntax_highlighter(self._highlighter)

        # Top section — metadata + toolbar share a dark card background
        top_section = QWidget()
        top_section.setObjectName("EditorTopSection")
        top_section.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ts_layout = QVBoxLayout(top_section)
        ts_layout.setContentsMargins(0, 0, 0, 0)
        ts_layout.setSpacing(0)
        ts_layout.addWidget(self._metadata_panel)
        ts_layout.addWidget(self._toolbar)

        # Editor wrapper — top/bottom breathing room (right margin comes from right_layout)
        self._editor_wrapper = QWidget()
        self._editor_wrapper.setObjectName("EditorWrapper")
        ew_layout = QVBoxLayout(self._editor_wrapper)
        ew_layout.setContentsMargins(0, 4, 0, 6)
        ew_layout.setSpacing(0)
        ew_layout.addWidget(self._editor)

        # Outer wrapper gives the editor the same 10 px left indent as the
        # metadata panel content, so the border and buttons align visually.
        editor_outer = QWidget()
        eo_layout = QVBoxLayout(editor_outer)
        eo_layout.setContentsMargins(10, 0, 0, 0)
        eo_layout.setSpacing(0)
        eo_layout.addWidget(self._editor_wrapper)

        right_layout.addWidget(top_section)
        right_layout.addWidget(editor_outer, stretch=1)

        # ── Empty / onboarding state ─────────────────────────────────────────
        self._onboarding = QWidget()
        self._onboarding.setObjectName("OnboardingPane")
        ob_layout = QVBoxLayout(self._onboarding)
        ob_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ob_layout.setSpacing(10)

        ob_icon = QLabel("📂")
        ob_icon.setStyleSheet(f"font-size: 56px; color: {TEXT_TERTIARY};")
        ob_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ob_icon = ob_icon

        self._ob_title = QLabel(tr("onboarding.no_folder"))
        self._ob_title.setStyleSheet(
            f"font-size: 17px; font-weight: bold; color: {TEXT_SECONDARY};"
        )
        self._ob_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._ob_sub = QLabel(tr("onboarding.subtitle"))
        self._ob_sub.setStyleSheet(f"font-size: 12px; color: {TEXT_TERTIARY};")
        self._ob_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._ob_open_btn = QPushButton(tr("onboarding.open_btn"))
        self._ob_open_btn.setObjectName("OpenFolderBtn")
        self._ob_open_btn.setFixedWidth(160)
        self._ob_open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ob_open_btn.clicked.connect(self.open_folder)

        ob_layout.addWidget(ob_icon)
        ob_layout.addSpacing(4)
        ob_layout.addWidget(self._ob_title)
        ob_layout.addWidget(self._ob_sub)
        ob_layout.addSpacing(12)
        ob_layout.addWidget(self._ob_open_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── List stack: the query list, or onboarding while the library is empty
        self._list_stack = QStackedWidget()
        self._list_stack.setMinimumWidth(_LIST_MIN_WIDTH)
        self._list_stack.addWidget(middle)  # index 0 — query list
        self._list_stack.addWidget(self._onboarding)  # index 1 — onboarding

        # ── Three panels: sidebar | list | details ──────────────────────────
        self._sidebar.setMinimumWidth(_SIDEBAR_MIN_WIDTH)
        self._panel = right
        self._panel.setMinimumWidth(_PANEL_MIN_WIDTH)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._sidebar)
        self._splitter.addWidget(self._list_stack)
        self._splitter.addWidget(self._panel)
        # The list absorbs window resizes; sidebar and panel keep their width.
        for index, stretch in enumerate((0, 1, 0)):
            self._splitter.setStretchFactor(index, stretch)
            self._splitter.setCollapsible(index, False)
        self._splitter.setSizes(
            list(cfg.get_splitter_sizes() or _DEFAULT_SPLITTER_SIZES)
        )
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(500)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._address_bar)
        central_layout.addWidget(self._command_bar)
        central_layout.addWidget(self._splitter, 1)
        self.setCentralWidget(central)

        self._status_bar = StatusBar()
        self.setStatusBar(self._status_bar)

    def _build_shortcuts(self) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QShortcut

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._search_bar.focus
        )
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_current)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(
            lambda: self._edit_toggle_btn.click()
        )
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(
            self.open_command_palette
        )
        esc = QShortcut(QKeySequence(_Qt.Key.Key_Escape), self)
        esc.setContext(_Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self._cancel_edit_mode)

    # ------------------------------------------------------------------
    # i18n — live language switching
    # ------------------------------------------------------------------

    def _change_language(self, lang: str) -> None:
        cfg.set_language(lang)
        set_language(lang)
        self._rebuild_language_menu()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """Re-apply all translatable strings after a language change."""
        # Menus
        self._file_menu.setTitle(tr("menu.file"))
        self._view_menu.setTitle(tr("menu.view"))
        self._settings_menu.setTitle(tr("menu.settings"))
        self._theme_menu.setTitle(tr("menu.theme"))
        self._language_menu.setTitle(tr("menu.language"))
        self._help_menu.setTitle(tr("menu.help"))
        # Actions
        self._open_act.setText(tr("menu.open_folder"))
        self._new_act.setText(tr("menu.new_query"))
        self._duplicate_act.setText(tr("menu.duplicate_query"))
        self._recent_menu.setTitle(tr("menu.recent_projects"))
        self._reindex_act.setText(tr("menu.force_reindex"))
        self._quit_act.setText(tr("menu.quit"))
        self._edit_menu.setTitle(tr("menu.edit"))
        self._copy_frontmatter_template_act.setText(
            tr("menu.copy_frontmatter_template")
        )
        self._reveal_act.setText(tr("menu.reveal_in_explorer"))

        self._copy_act.setText(tr("menu.copy_sql"))
        self._back_act.setText(tr("menu.go_back"))
        self._forward_act.setText(tr("menu.go_forward"))
        self._up_act.setText(tr("menu.go_up"))
        self._panel_act.setText(tr("menu.toggle_panel"))
        self._menu_bar_act.setText(tr("menu.show_menu_bar"))
        self._help_act.setText(tr("menu.help_action"))
        self._stats_act.setText(tr("menu.statistics"))
        self._reports_act.setText(tr("menu.reports"))
        self._check_updates_act.setText(tr("menu.check_for_updates"))
        self._about_act.setText(tr("menu.about"))
        self._preferences_act.setText(tr("menu.preferences"))
        self._show_tray_act.setText(tr("tray.show"))
        self._quit_tray_act.setText(tr("tray.quit"))
        for key, act in self._theme_acts.items():
            label_key = "menu.theme_dark" if key == "dark" else "menu.theme_light"
            act.setText(tr(label_key))
        # Rebuild the recent menu "(none)" label if needed
        self._rebuild_recent_menu()
        # Toolbar buttons
        if not self._edit_mode:
            self._edit_toggle_btn.setText(tr("editor.btn_edit"))
        else:
            self._edit_toggle_btn.setText(tr("editor.btn_editing"))
        self._edit_toggle_btn.setToolTip(tr("editor.tooltip_edit"))
        self._save_btn.setText(tr("editor.btn_save"))
        self._save_btn.setToolTip(tr("editor.tooltip_save"))
        self._cancel_btn.setText(tr("editor.btn_cancel"))
        self._select_all_btn.setText(tr("editor.btn_select_all"))
        self._select_all_btn.setToolTip(tr("editor.tooltip_select_all"))
        self._copy_query_btn.setText(tr("editor.btn_copy"))
        self._copy_query_btn.setToolTip(tr("editor.tooltip_copy"))
        # Onboarding
        self._ob_title.setText(tr("onboarding.no_folder"))
        self._ob_sub.setText(tr("onboarding.subtitle"))
        self._ob_open_btn.setText(tr("onboarding.open_btn"))
        # Child widgets; the address bar retranslates the search field too.
        self._sidebar.retranslate_ui()
        self._address_bar.retranslate_ui()
        self._command_bar.retranslate_ui()
        self._metadata_panel.retranslate_ui()
        self._status_bar.retranslate_ui()

    # ------------------------------------------------------------------
    # Folder / indexing
    # ------------------------------------------------------------------

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("dialog.open_folder"))
        if folder:
            self.load_folder(Path(folder))

    def load_folder(self, folder: Path) -> None:
        """Add *folder* to the library, index it (incremental) and show it.

        Indexing runs in the background with its progress in the status bar,
        so the window stays usable meanwhile.
        """
        if not folder.is_dir():
            return
        db = self._get_or_open_db(folder)
        cfg.add_recent_project(folder)
        cfg.add_known_folder(folder)
        self._rebuild_recent_menu()
        self._sidebar.set_folders(cfg.get_known_folders(), folder)
        self._reset_editor()
        self._start_index_job(_IndexWorker(db, folder), folder)
        self._sync_watchers()
        self._navigate(NavState("folder", folder))

    def _sync_watchers(self) -> None:
        """Watch every library folder, and stop watching removed ones."""
        wanted = {folder for folder in self._known_dbs if folder.is_dir()}
        for folder in [f for f in self._watchers if f not in wanted]:
            watcher, _bridge = self._watchers.pop(folder)
            watcher.stop()
        for folder in wanted - self._watchers.keys():
            bridge = _WatcherBridge(folder)
            bridge.files_changed.connect(self._handle_files_changed)
            try:
                watcher = FolderWatcher(folder, bridge.on_changed)
                watcher.start()
            except Exception:
                continue  # an unwatchable folder is still browsable
            self._watchers[folder] = (watcher, bridge)
        self._status_bar.set_watching(len(self._watchers))

    def _stop_watchers(self) -> None:
        for watcher, _bridge in self._watchers.values():
            try:
                watcher.stop()
            except Exception:
                pass
        self._watchers.clear()

    def _on_sidebar_folder_selected(self, folder: Path) -> None:
        """Show *folder* without a full reindex."""
        if not folder.is_dir():
            return
        if folder not in self._known_dbs:
            self._get_or_open_db(folder)
            self._sync_watchers()
        self._navigate(NavState("folder", folder))

    def _get_or_open_db(self, folder: Path) -> IndexDB:
        if folder not in self._known_dbs:
            self._known_dbs[folder] = IndexDB(folder)
        return self._known_dbs[folder]

    def _start_index_job(
        self, worker: _IndexWorker | _ForceReindexWorker, folder: Path
    ) -> None:
        """Queue *worker*; the status bar shows progress until all jobs end."""
        if not self._index_jobs:
            self._indexing_name = folder.name
            self._status_bar.set_indexing(folder.name)
        self._index_jobs += 1
        worker.signals.started.connect(self._on_index_started)
        worker.signals.progress.connect(self._on_index_progress)
        worker.signals.finished.connect(self._on_index_finished)
        worker.signals.error.connect(self._on_index_error)
        _INDEX_POOL.start(worker)

    def _on_index_started(self, folder: Path) -> None:
        self._indexing_name = folder.name
        self._status_bar.set_indexing(folder.name)

    def _on_index_progress(self, current: int, total: int) -> None:
        self._status_bar.set_indexing(self._indexing_name, current, total)

    def _on_index_finished(self, _count: int) -> None:
        self._end_index_job()
        self._refresh_ui()

    def _on_index_error(self, msg: str) -> None:
        self._end_index_job()
        self._status_bar.flash(tr("status.index_error", msg=msg))

    def _end_index_job(self) -> None:
        self._index_jobs = max(0, self._index_jobs - 1)
        if not self._index_jobs:
            self._status_bar.set_index_ready()

    def force_reindex(self) -> None:
        """Rebuild the index of the folder in view, or of the whole library."""
        folder = self._nav.current.folder
        root = root_of(folder, self._known_dbs) if folder is not None else None
        targets = [root] if root is not None else list(self._known_dbs)
        for target in targets:
            if target.is_dir():
                worker = _ForceReindexWorker(self._known_dbs[target], target)
                self._start_index_job(worker, target)

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = cfg.get_recent_projects()
        if not recents:
            self._recent_menu.addAction(tr("menu.recent_none")).setEnabled(False)
            return
        for path in recents:
            action = QAction(str(path), self)
            action.triggered.connect(lambda checked, p=path: self.load_folder(p))
            self._recent_menu.addAction(action)

    def _reset_editor(self) -> None:
        """Clear editor and reset state without reloading from disk."""
        self._load_gen += 1
        self._load_timer.stop()
        self._pending_load = None
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._editor.clear()
        self._edit_toggle_btn.setEnabled(False)
        self._metadata_panel.clear()
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._editor_wrapper.setStyleSheet("")
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText(tr("editor.btn_edit"))
        self._current_result = None
        self._current_metadata = {}
        self._status_bar.set_selected(0)

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _update_content_view(self) -> None:
        """Show the query list, or onboarding while the library is empty."""
        has_library = bool(self._known_dbs)
        self._list_stack.setCurrentIndex(0 if has_library else 1)
        self._apply_panel_visibility()
        if not has_library:
            self._status_bar.set_no_library()

    def _refresh_ui(self) -> None:
        self._update_content_view()
        if self._known_dbs:
            self._do_search()

    def _do_search(self) -> None:
        """Dispatch the current state's search; discard results if superseded."""
        self._search_timer.stop()  # cancel any still-pending debounce
        if not self._known_dbs:
            return
        state = self._nav.current
        mode = state.kind if state.kind in ("favorites", "recent") else "search"
        self._search_gen += 1
        worker = _SearchWorker(
            dict(self._known_dbs),
            search_text(state),
            state.folder,
            mode,
            self._search_gen,
        )
        worker.signals.results_ready.connect(self._on_search_results)
        QThreadPool.globalInstance().start(worker)

    def _on_search_results(self, results: list, tags: list[str], gen: int) -> None:
        if gen != self._search_gen:
            return  # stale result from a superseded search
        self._sidebar.set_tags(tags)
        # The tag list was just rebuilt; highlight the current tag again.
        self._sidebar.show_state(self._nav.current)
        self._query_list.set_results(results)
        if not results:
            self._reset_editor()
        if self._pending_select is not None:
            self._query_list.select_by_rel_path(self._pending_select)
            self._pending_select = None
        self._status_bar.set_counts(
            len(results),
            searching=self._nav.current.kind == "search",
            selected=1 if self._current_result is not None else 0,
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate(self, state: NavState) -> None:
        """Go to *state*, recording it in the back/forward history."""
        self._nav.navigate(state)
        self._apply_nav()

    def go_back(self) -> None:
        if self._nav.back() is not None:
            self._apply_nav()

    def go_forward(self) -> None:
        if self._nav.forward() is not None:
            self._apply_nav()

    def go_up(self) -> None:
        if self._nav.up(self._known_dbs) is not None:
            self._apply_nav()

    def _apply_nav(self) -> None:
        """Bring the whole window in line with the current navigation state."""
        state = self._nav.current
        roots = list(self._known_dbs)
        root = root_of(state.folder, roots) if state.folder is not None else None
        if root is not None:
            self._folder = root
            self._db = self._known_dbs[root]
        self.setWindowTitle(f"SQLShelf — {root.name}" if root else "SQLShelf")
        # Leave the field alone while it already says this, trailing spaces
        # included, or typing would lose the space before the next word.
        if self._search_bar.text().strip() != search_text(state):
            self._search_bar.set_text_silently(search_text(state))
        self._sidebar.show_state(state)
        self._address_bar.set_state(
            state,
            roots,
            can_back=self._nav.can_back,
            can_forward=self._nav.can_forward,
        )
        self._back_act.setEnabled(self._nav.can_back)
        self._forward_act.setEnabled(self._nav.can_forward)
        self._up_act.setEnabled(state.kind != "all")
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Panels
    # ------------------------------------------------------------------

    def _set_panel_visible(self, visible: bool) -> None:
        """Show or hide the details panel (Alt+P or the command bar)."""
        self._panel_visible = visible
        cfg.set_panel_visible(visible)
        self._command_bar.set_panel_visible(visible)
        self._panel_act.blockSignals(True)
        self._panel_act.setChecked(visible)
        self._panel_act.blockSignals(False)
        self._apply_panel_visibility()

    def _apply_panel_visibility(self) -> None:
        """The panel shows when the user wants it and there is a library."""
        show = self._panel_visible and bool(self._known_dbs)
        if show == (not self._panel.isHidden()):
            return
        if show:
            sizes = self._splitter.sizes()
            self._panel.show()
            # Give the panel its old width back, taken from the list.
            sizes[1] = max(_LIST_MIN_WIDTH, sizes[1] - self._panel_width)
            sizes[2] = self._panel_width
            self._splitter.setSizes(sizes)
        else:
            # Before the window is first shown the sizes are not real yet.
            if self.isVisible() and self._splitter.sizes()[2] > 0:
                self._panel_width = self._splitter.sizes()[2]
            self._panel.hide()

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        self._splitter_save_timer.start()

    def _save_splitter_sizes(self) -> None:
        sizes = self._splitter.sizes()
        if self._panel.isHidden():
            sizes[2] = self._panel_width
        elif sizes[2] > 0:
            self._panel_width = sizes[2]
        if sizes[0] > 0 and sizes[1] > 0:
            cfg.set_splitter_sizes(sizes)

    # ------------------------------------------------------------------
    # Command bar
    # ------------------------------------------------------------------

    def _on_sort_requested(self, key: str) -> None:
        self._query_list.set_sort(key)
        cfg.set_sort_key(key)
        self._command_bar.set_sort(key)

    def _on_group_requested(self, mode: str) -> None:
        cfg.set_group_mode(mode)
        self._command_bar.set_group(mode)

    def _on_view_requested(self, mode: str) -> None:
        cfg.set_view_mode(mode)
        self._command_bar.set_view(mode)

    # ------------------------------------------------------------------
    # Query selection
    # ------------------------------------------------------------------

    def _on_query_selected(self, result: SearchResult) -> None:
        # Discard any unsaved edits without reloading (we're about to load new query)
        if self._edit_mode:
            self._edit_mode = False
            self._editor.setReadOnly(True)
            self._metadata_panel.set_edit_mode(False)
            self._save_btn.setVisible(False)
            self._cancel_btn.setVisible(False)
            self._editor_wrapper.setStyleSheet("")
            self._edit_toggle_btn.setChecked(False)
            self._edit_toggle_btn.setText(tr("editor.btn_edit"))

        # Resolve the folder this result comes from (important in multi-folder mode)
        if result.folder is not None and result.folder.is_dir():
            self._folder = result.folder
            self._db = self._known_dbs.get(result.folder, self._db)

        self._current_result = result
        if self._folder is None:
            return
        path = self._folder / result.rel_path
        self._load_gen += 1
        self._pending_load = (self._db, path, result, self._load_gen, True)
        self._load_timer.start()

        # Do not expose actions for the previous document while this selection
        # is being loaded in the background.
        self._current_metadata = {}
        self._editor.clear()
        self._edit_toggle_btn.setEnabled(False)
        self._select_all_btn.setVisible(False)
        self._copy_query_btn.setVisible(False)
        self._metadata_panel.set_query(
            title=result.title,
            description=result.description,
            tags=result.tags,
            tables=[],
        )
        self._metadata_panel.set_path(path)
        self._status_bar.set_selected(1)

    def _record_access(self, action: str) -> None:
        """Log an access event (open/copy/open_in_ssms) for the current query."""
        if self._db is None or self._current_result is None:
            return
        worker = _RecordAccessWorker(self._db, self._current_result.rel_path, action)
        QThreadPool.globalInstance().start(worker)

    def _dispatch_query_load(self) -> None:
        pending = self._pending_load
        if pending is None:
            return
        self._pending_load = None
        db, path, result, gen, track_access = pending
        worker = _LoadQueryWorker(
            db,
            path,
            result,
            gen,
            track_access,
        )
        worker.signals.finished.connect(self._on_query_loaded)
        QThreadPool.globalInstance().start(worker)

    def _on_query_loaded(self, payload: tuple, gen: int) -> None:
        if gen != self._load_gen:
            return
        path, result, metadata, body, objects, is_favorite = payload
        self._current_metadata = metadata
        self._editor.setPlainText(body)
        self._metadata_panel.set_query(
            title=result.title,
            description=result.description,
            tags=result.tags,
            tables=objects.get("table", []),
        )
        self._metadata_panel.set_path(path)

        if not self._edit_mode:
            self._edit_toggle_btn.setEnabled(True)
            self._select_all_btn.setVisible(True)
            self._copy_query_btn.setVisible(True)

        self._metadata_panel.set_favorite(is_favorite)

    # ------------------------------------------------------------------
    # Search / tag / sidebar filtering
    # ------------------------------------------------------------------

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()  # reset countdown on each keystroke

    def _on_search_timeout(self) -> None:
        """Turn the typed text into a navigation state once typing pauses."""
        current = self._nav.current
        state = with_search(current, self._search_bar.text())
        if state == current:
            return
        if current.kind == "search" and state.kind == "search":
            # Refining a search replaces it, so Back does not replay keystrokes.
            self._nav.replace(state)
            self._apply_nav()
        else:
            self._navigate(state)

    def _on_tag_selected(self, tag: str) -> None:
        if not tag:
            # "All queries" — every folder in the library
            self._navigate(NavState("all"))
        else:
            self._navigate(NavState("tag", self._nav.current.folder, tag))

    def _on_favorites_selected(self) -> None:
        self._navigate(NavState("favorites"))

    def _on_recent_selected(self) -> None:
        self._navigate(NavState("recent", self._nav.current.folder))

    def _on_navigate_requested(self, token: str) -> None:
        """Jump to the first occurrence of *token* in the current SQL editor."""
        self._editor.navigate_to_token(token)

    def _on_filter_requested(self, kind: str, value: str) -> None:
        """Reverse search: right-click chip → search the scope for table/col."""
        folder = self._nav.current.folder
        self._navigate(NavState("search", folder, f"{kind}:{value}"))

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
            if (
                self._current_result
                and self._current_result.rel_path != result.rel_path
            ):
                # Load body from disk for the right-clicked item
                if self._folder:
                    try:
                        _, body, _ = read_sql_file(self._folder / result.rel_path)
                    except Exception:
                        pass
            QApplication.clipboard().setText(body)
            self._status_bar.flash(tr("status.copied", path=result.rel_path))
        elif action == "reveal":
            self._query_list.select_by_rel_path(result.rel_path)
            self.reveal_in_explorer()

    # ------------------------------------------------------------------
    # Sidebar folder explorer handlers
    # ------------------------------------------------------------------

    def _on_folder_remove_requested(self, folder: Path) -> None:
        self._forget_folder(folder)

    def _on_folder_deindex_requested(self, folder: Path) -> None:
        reply = QMessageBox.question(
            self,
            tr("msg.deindex.title"),
            tr("msg.deindex.text", path=str(folder)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._forget_folder(folder, delete_index=True)

    def _forget_folder(self, folder: Path, *, delete_index: bool = False) -> None:
        """Take *folder* out of the library, closing its index and watcher."""
        cfg.remove_known_folder(folder)
        db = self._known_dbs.pop(folder, None)
        self._sync_watchers()
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        if delete_index:
            db_path = folder / ".sqlshelf" / "index.db"
            try:
                if db_path.exists():
                    db_path.unlink()
            except Exception:
                pass

        self._rebuild_recent_menu()
        self._sidebar.set_folders(cfg.get_known_folders(), None)
        if self._folder == folder:
            self._folder = None
            self._db = None
            self._reset_editor()
        if not self._known_dbs:
            self._query_list.set_results([])

        current = self._nav.current.folder
        if current is not None and root_of(current, [folder]) is not None:
            self._navigate(NavState("all"))
        else:
            self._apply_nav()

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
        self._query_list.update_favorite(result.rel_path, is_fav)
        if self._current_result and self._current_result.rel_path == result.rel_path:
            self._metadata_panel.set_favorite(is_fav)
        key = "status.added_favorite" if is_fav else "status.removed_favorite"
        self._status_bar.flash(tr(key, path=result.rel_path))

    # ------------------------------------------------------------------
    # Command palette (Ctrl+K)
    # ------------------------------------------------------------------

    def open_command_palette(self) -> None:
        if not self._known_dbs:
            return
        results: list[SearchResult] = []
        scope = self._nav.current.folder
        root = root_of(scope, self._known_dbs) if scope is not None else None
        if root is not None:
            results = self._known_dbs[root].search("")
            for r in results:
                r.folder = root
        else:
            for folder, db in self._known_dbs.items():
                try:
                    folder_results = db.search("")
                    for r in folder_results:
                        r.folder = folder
                    results.extend(folder_results)
                except Exception:
                    pass
        palette = CommandPalette(results, self)
        palette.query_selected.connect(self._on_palette_selected)
        geo = self.geometry()
        pw, ph = palette.width(), palette.height()
        palette.move(geo.center().x() - pw // 2, geo.center().y() - ph // 2)
        palette.exec()

    def _on_palette_selected(self, result: SearchResult) -> None:
        self._pending_select = result.rel_path
        if result.folder is not None and result.folder in self._known_dbs:
            self._navigate(NavState("folder", result.folder))
        else:
            self._do_search()

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
        if self._db is not None:
            self._metadata_panel.set_available_tags(self._db.get_all_tags())
        self._metadata_panel.set_edit_mode(True)
        self._save_btn.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._select_all_btn.setVisible(False)
        self._copy_query_btn.setVisible(False)
        self._editor_wrapper.setStyleSheet(
            f"#EditorWrapper {{ border: 1px solid {_tk.color('accent')}; border-radius: 4px; }}"
        )
        self._edit_toggle_btn.setText(tr("editor.btn_editing"))

    def _cancel_edit_mode(self) -> None:
        """Exit edit mode, discarding unsaved changes by reloading from disk."""
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        has_query = self._current_result is not None
        self._select_all_btn.setVisible(has_query)
        self._copy_query_btn.setVisible(has_query)
        self._editor_wrapper.setStyleSheet("")
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText(tr("editor.btn_edit"))
        # Reload from disk to discard unsaved changes
        if self._current_result is not None and self._folder is not None:
            path = self._folder / self._current_result.rel_path
            self._load_gen += 1
            self._pending_load = (
                self._db,
                path,
                self._current_result,
                self._load_gen,
                False,
            )
            self._load_timer.start()

    def _cancel_edit(self) -> None:
        self._cancel_edit_mode()

    def _editor_select_all(self) -> None:
        self._editor.selectAll()
        self._editor.setFocus()

    def _editor_copy_query(self) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(self._editor.toPlainText())
        self._record_access("copy")

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
            QMessageBox.critical(self, tr("msg.save_error"), str(exc))
            return

        if self._db is not None:
            # scan_file, never scan_folder: re-scanning the whole folder to find
            # one known file took seconds on real folders, all of it on the GUI
            # thread.
            updated = scan_file(path)
            if updated is not None:
                self._db.upsert_query(updated)

        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._editor_wrapper.setStyleSheet("")
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText(tr("editor.btn_edit"))
        self._refresh_ui()
        self._status_bar.flash(tr("status.saved", path=self._current_result.rel_path))

    # ------------------------------------------------------------------
    # New query / duplicate / templates
    # ------------------------------------------------------------------

    def new_query(self) -> None:
        # In the library view, new queries go to the first folder.
        folder = self._folder or next(iter(self._known_dbs), None)
        if folder is None:
            QMessageBox.information(
                self, tr("msg.no_folder.title"), tr("msg.no_folder.text")
            )
            return
        all_projects = [p for p, _ in cfg.get_known_folders()]
        if not all_projects:
            all_projects = [folder]
        db = self._known_dbs.get(folder)
        available_tags = db.get_all_tags() if db is not None else []
        dlg = NewQueryDialog(folder, all_projects, self, available_tags)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.created_path is not None:
            self._post_create(dlg.created_path)

    def new_from_template(self) -> None:
        # In the library view, new queries go to the first folder.
        folder = self._folder or next(iter(self._known_dbs), None)
        if folder is None:
            QMessageBox.information(
                self, tr("msg.no_folder.title"), tr("msg.no_folder.text")
            )
            return
        if not list_templates():
            QMessageBox.information(
                self,
                tr("msg.no_templates.title"),
                tr(
                    "msg.no_templates.text",
                    path=str(Path.home() / ".sqlshelf" / "templates"),
                ),
            )
            return
        all_projects = [p for p, _ in cfg.get_known_folders()]
        if not all_projects:
            all_projects = [folder]
        dlg = TemplateDialog(folder, all_projects, self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.created_path is not None:
            self._post_create(dlg.created_path)

    def duplicate_current(self) -> None:
        if self._current_result is None or self._folder is None:
            QMessageBox.information(
                self, tr("msg.no_query.title"), tr("msg.no_query.text")
            )
            return

        src_path = self._folder / self._current_result.rel_path
        new_title, ok = QInputDialog.getText(
            self,
            tr("dialog.duplicate.title"),
            tr("dialog.duplicate.label"),
            text=tr("dialog.duplicate.prefix", title=self._current_result.title),
        )
        if not ok or not new_title.strip():
            return

        new_title = new_title.strip()
        try:
            metadata, body, _has_fm = read_sql_file(src_path)
        except Exception as exc:
            QMessageBox.critical(self, tr("msg.duplicate_error"), str(exc))
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
            QMessageBox.critical(self, tr("msg.duplicate_error"), str(exc))
            return

        self._post_create(dest_path)

    def _list_subfolders(self) -> list[str]:
        if self._folder is None:
            return []
        return [
            str(p.relative_to(self._folder))
            for p in self._folder.rglob("*")
            if p.is_dir()
            and not any(
                part.startswith(".") for part in p.relative_to(self._folder).parts
            )
        ]

    def _post_create(self, path: Path) -> None:
        # Determine which known project the created file belongs to
        target_project: Path | None = None
        for proj, _ in cfg.get_known_folders():
            try:
                path.relative_to(proj)
                target_project = proj
                break
            except ValueError:
                continue

        if target_project is None:
            self._status_bar.flash(tr("status.created", name=path.name))
            return

        db = self._get_or_open_db(target_project)
        updated = scan_file(path)
        if updated is not None:
            db.upsert_query(updated)
        self._pending_select = path.relative_to(target_project).as_posix()
        if self._nav.current.kind == "all":
            self._refresh_ui()
        else:
            self._navigate(NavState("folder", target_project))
        self._status_bar.flash(tr("status.created", name=path.name))

    # ------------------------------------------------------------------
    # Watcher handler
    # ------------------------------------------------------------------

    def _handle_files_changed(
        self, folder: Path, modified: set[Path], deleted: set[Path]
    ) -> None:
        """Reindex what a folder's watcher reported, off the GUI thread."""
        db = self._known_dbs.get(folder)
        if db is None or (not modified and not deleted):
            return
        worker = _WatchSyncWorker(db, set(modified), set(deleted))
        worker.signals.finished.connect(self._on_watch_sync_finished)
        _INDEX_POOL.start(worker)

    def _on_watch_sync_finished(self, _count: int) -> None:
        self._refresh_ui()

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
                os.startfile(str(path))
                self._record_access("open_in_ssms")
            except Exception as exc:
                QMessageBox.warning(self, tr("msg.open_ssms.title"), str(exc))
        else:
            QMessageBox.information(
                self, tr("msg.open_ssms.title"), tr("msg.open_ssms.windows_only")
            )

    def copy_sql(self) -> None:
        body = self._editor.toPlainText()
        if body:
            QApplication.clipboard().setText(body)
            self._status_bar.flash(tr("status.sql_copied"))
            self._record_access("copy")

    def copy_frontmatter_template(self) -> None:
        from datetime import date

        today = date.today().isoformat()
        block = (
            "/* ---\n"
            "title: \n"
            "description: \n"
            "tags:\n"
            "  - \n"
            f"created: {today}\n"
            f"updated: {today}\n"
            "--- */"
        )
        QApplication.clipboard().setText(block)
        self._status_bar.flash(tr("status.frontmatter_template_copied"))

    # ------------------------------------------------------------------
    # Help / About
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        QMessageBox.information(self, tr("help.title"), tr("help.text"))

    def _show_statistics(self) -> None:
        dlg = _StatsDialog(self._known_dbs, self)
        dlg.exec()

    def _show_reports(self) -> None:
        dlg = _ReportsDialog(self._known_dbs, self)
        dlg.exec()

    def _check_for_updates(self) -> None:
        from sqlshelf import __version__

        dlg = _UpdateDialog(__version__, self)
        dlg.exec()

    def _silent_update_check(self) -> None:
        from sqlshelf import __version__

        worker = _UpdateWorker(__version__)
        worker.signals.finished.connect(self._on_silent_update_result)
        QThreadPool.globalInstance().start(worker)

    def _on_silent_update_result(self, info) -> None:
        if info.has_update:
            msg = tr("update.startup_notify", version=info.latest_version)
            self._status_bar.flash(msg, 20000)

    def _show_about(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("about.title"))
        dlg.setWindowFlags(
            dlg.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        dlg.setFixedWidth(420)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        # Logo + app name row
        header = QHBoxLayout()
        header.setSpacing(14)
        logo_label = QLabel()
        logo_path = Path(__file__).parent.parent.parent / "images" / "logo_sqlshelf.png"
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaledToHeight(
                48, Qt.TransformationMode.SmoothTransformation
            )
            logo_label.setPixmap(pix)
        header.addWidget(logo_label)
        header.addStretch()
        layout.addLayout(header)

        # Body text with clickable links, in the accent colour.
        from sqlshelf import __version__

        version_line = f'<span style="color:{ACCENT};font-size:12px;">v{__version__}</span><br><br>'
        about_html = (
            tr("about.text")
            .replace(
                "<b>SQLShelf</b><br><br>",
                f"<b>SQLShelf</b><br>{version_line}",
            )
            .replace("<a href=", f'<a style="color:{ACCENT};" href=')
        )
        body = QLabel(about_html)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(body)

        # OK button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        dlg.exec()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _change_theme(self, name: str) -> None:
        cfg.set_theme(name)
        self._apply_theme_live(name)

    def _apply_theme_live(self, name: str) -> None:
        # instance() is typed as the QCoreApplication base, which knows nothing
        # about stylesheets; a headless run would genuinely have no QApplication.
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            _tk.set_active_palette(name)
            return

        apply_theme(app, name)

        # Refresh widgets that manage their own stylesheets
        self._address_bar.refresh_theme()  # and the search field
        self._command_bar.refresh_theme()
        self._query_list.refresh_theme()
        self._sidebar.refresh_theme()
        self._metadata_panel.refresh_theme()
        self._editor.refresh_theme()
        self._highlighter.refresh_theme()

        # Refresh onboarding labels
        self._ob_icon.setStyleSheet(f"font-size: 56px; color: {_tk.TEXT_TERTIARY};")
        self._ob_title.setStyleSheet(
            f"font-size: 17px; font-weight: bold; color: {_tk.TEXT_SECONDARY};"
        )
        self._ob_sub.setStyleSheet(f"font-size: 12px; color: {_tk.TEXT_TERTIARY};")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if cfg.get_minimize_to_tray() and not self._quitting:
            event.ignore()
            self.hide()
            return
        self._save_splitter_sizes()
        self._stop_watchers()
        # Let running workers finish before the connections they hold go away.
        # Bounded, so a wedged worker can never block the app from exiting.
        _INDEX_POOL.waitForDone(3000)
        QThreadPool.globalInstance().waitForDone(3000)
        for db in self._known_dbs.values():
            try:
                db.close()
            except Exception:
                pass
        self._tray.hide()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # System tray
    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        icon_path = Path(__file__).parent.parent.parent / "images" / "FavIcon.png"
        icon = (
            QIcon(str(icon_path))
            if icon_path.exists()
            else QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_ComputerIcon
            )
        )

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("SQLShelf")

        tray_menu = QMenu()
        self._show_tray_act = QAction(tr("tray.show"), self)
        self._show_tray_act.triggered.connect(self._tray_restore)
        tray_menu.addAction(self._show_tray_act)
        tray_menu.addSeparator()
        self._quit_tray_act = QAction(tr("tray.quit"), self)
        self._quit_tray_act.triggered.connect(self._quit_app)
        tray_menu.addAction(self._quit_tray_act)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)

        if cfg.get_minimize_to_tray():
            self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_restore()

    def _tray_restore(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _quit_app(self) -> None:
        self._quitting = True
        self.close()
        QApplication.quit()

    # ------------------------------------------------------------------
    # Preferences dialog
    # ------------------------------------------------------------------

    def _show_preferences(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("prefs.title"))
        dlg.setWindowFlags(
            dlg.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        dlg.setFixedWidth(360)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        tray_cb = QCheckBox(tr("prefs.minimize_to_tray"))
        tray_cb.setChecked(cfg.get_minimize_to_tray())
        layout.addWidget(tray_cb)

        autostart_cb = QCheckBox(tr("prefs.start_with_windows"))
        if sys.platform == "win32":
            from ..core.autostart import is_autostart_enabled

            autostart_cb.setChecked(is_autostart_enabled())
        else:
            autostart_cb.setEnabled(False)
        layout.addWidget(autostart_cb)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(tr("editor.btn_cancel"))
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setFixedWidth(80)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_minimize = tray_cb.isChecked()
        cfg.set_minimize_to_tray(new_minimize)
        if new_minimize:
            self._tray.show()
        else:
            self._tray.hide()

        if sys.platform == "win32":
            from ..core.autostart import disable_autostart, enable_autostart

            if autostart_cb.isChecked():
                enable_autostart()
            else:
                disable_autostart()
