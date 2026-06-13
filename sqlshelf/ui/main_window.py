from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
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
    QStatusBar,
    QStyle,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from ..core import config as cfg
from ..core.frontmatter import read_sql_file, write_sql_file
from ..core.i18n import available_languages, get_language, ntr, set_language, tr
from ..core.index_db import IndexDB
from ..core.models import SearchResult
from ..core.scanner import scan_folder
from ..core.snippets import list_templates
from ..core.watcher import FolderWatcher
from .code_editor import CodeEditor
from .theme import tokens as _tk
from .theme.tokens import ACCENT, ACCENT_BORDER, ACCENT_FILL, TEXT_SECONDARY, TEXT_TERTIARY
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
    progress = Signal(int, int)  # (current, total)


class _IndexWorker(QRunnable):
    def __init__(self, db: IndexDB, folder: Path) -> None:
        super().__init__()
        self.signals = _IndexWorkerSignals()
        self._db = db
        self._folder = folder

    def run(self) -> None:
        try:
            queries = scan_folder(self._folder)
            total = len(queries)
            if total:
                self.signals.progress.emit(0, total)

            def _cb(current: int, t: int) -> None:
                self.signals.progress.emit(current, t)

            self._db.index_incremental(queries, progress_cb=_cb)
            self.signals.finished.emit(self._db.count())
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Background search worker
# ---------------------------------------------------------------------------

class _SearchWorkerSignals(QObject):
    results_ready = Signal(list, int)   # (list[SearchResult], generation)


class _SearchWorker(QRunnable):
    def __init__(
        self,
        dbs: dict,
        text: str,
        browse_folder,
        gen: int,
    ) -> None:
        super().__init__()
        self.signals = _SearchWorkerSignals()
        self._dbs = dbs
        self._text = text
        self._browse_folder = browse_folder
        self._gen = gen

    def run(self) -> None:
        try:
            results = []
            if self._browse_folder is not None:
                db = self._dbs.get(self._browse_folder)
                if db:
                    results = db.search(self._text)
                    for r in results:
                        r.folder = self._browse_folder
            else:
                for folder, db in self._dbs.items():
                    try:
                        folder_results = db.search(self._text)
                        for r in folder_results:
                            r.folder = folder
                        results.extend(folder_results)
                    except Exception:
                        pass
                results.sort(key=lambda r: r.rank)
        except Exception:
            results = []
        self.signals.results_ready.emit(results, self._gen)


# ---------------------------------------------------------------------------
# Progress dialog shown while indexing a new folder
# ---------------------------------------------------------------------------

class _IndexProgressDialog(QDialog):
    def __init__(self, folder_name: str, parent=None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.WindowTitleHint | Qt.WindowType.CustomizeWindowHint,
        )
        self.setWindowTitle(tr("progress.title"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setFixedHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        self._title_label = QLabel(tr("progress.importing", name=folder_name))
        layout.addWidget(self._title_label)

        self._detail_label = QLabel(tr("progress.scanning"))
        layout.addWidget(self._detail_label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate until total is known
        layout.addWidget(self._bar)

        self._pct_label = QLabel("")
        self._pct_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._pct_label)

    def on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            return
        if self._bar.maximum() == 0:
            self._bar.setRange(0, total)
        self._bar.setValue(current)
        pct = int(current / total * 100)
        self._detail_label.setText(tr("progress.indexing_files", current=current, total=total))
        self._pct_label.setText(f"{pct}%")


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
        self._progress_dialog: _IndexProgressDialog | None = None

        # Multi-folder support
        self._known_dbs: dict[Path, IndexDB] = {}
        self._browse_folder: Path | None = None  # None = show all known folders

        # Async search state
        self._search_gen: int = 0      # incremented per dispatch; workers check this
        self._pending_select: str | None = None  # rel_path to auto-select after search

        self._build_menu()
        self._build_ui()
        self._build_shortcuts()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)  # ms to wait after last keystroke
        self._search_timer.timeout.connect(self._do_search)

        # Pre-open DBs for known folders so global view works on startup
        for folder, _ in cfg.get_known_folders():
            if folder.is_dir():
                try:
                    self._known_dbs[folder] = IndexDB(folder)
                except Exception:
                    pass
        if self._known_dbs:
            self._refresh_ui()
        else:
            self._update_content_view()
        self._refresh_status_bar()

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
            _icon(QStyle.StandardPixmap.SP_FileLinkIcon), tr("menu.duplicate_query"), self
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
            _icon(QStyle.StandardPixmap.SP_BrowserReload), tr("menu.force_reindex"), self
        )
        self._reindex_act.triggered.connect(self.force_reindex)
        self._file_menu.addAction(self._reindex_act)

        self._edit_menu = QMenu(tr("menu.edit"), self)
        mb.addMenu(self._edit_menu)

        self._copy_frontmatter_template_act = QAction(tr("menu.copy_frontmatter_template"), self)
        self._copy_frontmatter_template_act.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self._copy_frontmatter_template_act.triggered.connect(self.copy_frontmatter_template)
        self._edit_menu.addAction(self._copy_frontmatter_template_act)

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
        for key, label_key in [("dark", "menu.theme_dark"), ("light", "menu.theme_light")]:
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

        self._open_ssms_act = QAction(tr("menu.open_in_ssms"), self)
        self._open_ssms_act.triggered.connect(self.open_in_ssms)
        self._view_menu.addAction(self._open_ssms_act)

        self._copy_act = QAction(
            _icon(QStyle.StandardPixmap.SP_DialogSaveButton),
            tr("menu.copy_sql"),
            self,
        )
        self._copy_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._copy_act.triggered.connect(self.copy_sql)
        self._view_menu.addAction(self._copy_act)

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
        self._sidebar.folder_deindex_requested.connect(self._on_folder_deindex_requested)
        self._sidebar.folder_favorite_toggled.connect(self._on_folder_favorite_toggled)
        self._sidebar.tag_selected.connect(self._on_tag_selected)
        self._sidebar.favorites_selected.connect(self._on_favorites_selected)
        self._sidebar.recent_selected.connect(self._on_recent_selected)
        self._sidebar.set_folders(cfg.get_known_folders(), None)

        # Middle panel
        middle = QWidget()
        mid_layout = QVBoxLayout(middle)
        mid_layout.setContentsMargins(8, 10, 8, 4)
        mid_layout.setSpacing(8)

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
        right_layout.setContentsMargins(0, 0, 12, 0)
        right_layout.setSpacing(0)

        self._metadata_panel = MetadataPanel()
        self._metadata_panel.filter_requested.connect(self._on_filter_requested)
        self._metadata_panel.favorite_toggled.connect(self._toggle_favorite)
        self._metadata_panel.reveal_requested.connect(self.reveal_in_explorer)
        self._metadata_panel.command_palette_requested.connect(self.open_command_palette)

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

        tb_layout.addWidget(self._edit_toggle_btn)
        tb_layout.addWidget(self._save_btn)
        tb_layout.addWidget(self._cancel_btn)
        tb_layout.addStretch()
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)

        self._editor = CodeEditor()
        self._editor.setReadOnly(True)
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self._editor.setFont(font)
        self._highlighter = SqlHighlighter(self._editor.document())

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

        # ── Content stack: normal (middle + right) OR onboarding ─────────────
        self._inner_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._inner_splitter.addWidget(middle)
        self._inner_splitter.addWidget(right)
        self._inner_splitter.setSizes([320, 900])
        # Middle column keeps its user-set width; right panel absorbs all excess.
        self._inner_splitter.setStretchFactor(0, 0)
        self._inner_splitter.setStretchFactor(1, 1)
        self._inner_splitter.setCollapsible(0, False)
        self._inner_splitter.setCollapsible(1, False)
        middle.setMinimumWidth(200)

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._inner_splitter)  # index 0 — normal view
        self._content_stack.addWidget(self._onboarding)      # index 1 — onboarding

        # ── Outer splitter: sidebar | content ────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._content_stack)
        splitter.setSizes([220, 1180])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self.setCentralWidget(splitter)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

    def _build_shortcuts(self) -> None:
        from PySide6.QtGui import QShortcut

        from PySide6.QtCore import Qt as _Qt

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._search_bar.focus)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_current)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(
            lambda: self._edit_toggle_btn.click()
        )
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_query)
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self.open_command_palette)
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
        self._edit_menu.setTitle(tr("menu.edit"))
        self._copy_frontmatter_template_act.setText(tr("menu.copy_frontmatter_template"))
        self._reveal_act.setText(tr("menu.reveal_in_explorer"))
        self._open_ssms_act.setText(tr("menu.open_in_ssms"))
        self._copy_act.setText(tr("menu.copy_sql"))
        self._help_act.setText(tr("menu.help_action"))
        self._about_act.setText(tr("menu.about"))
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
        # Onboarding
        self._ob_title.setText(tr("onboarding.no_folder"))
        self._ob_sub.setText(tr("onboarding.subtitle"))
        self._ob_open_btn.setText(tr("onboarding.open_btn"))
        # Child widgets
        self._sidebar.retranslate_ui()
        self._search_bar.retranslate_ui()
        self._metadata_panel.retranslate_ui()
        # Status bar
        self._refresh_status_bar()

    # ------------------------------------------------------------------
    # Folder / indexing
    # ------------------------------------------------------------------

    def open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("dialog.open_folder"))
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
        self._status_bar.showMessage(tr("status.indexing"))
        self._reset_editor()

        self._progress_dialog = _IndexProgressDialog(folder.name, self)

        worker = _IndexWorker(db, folder)
        worker.signals.progress.connect(self._progress_dialog.on_progress)
        worker.signals.progress.connect(self._on_index_progress)
        worker.signals.finished.connect(self._on_index_finished)
        worker.signals.error.connect(self._on_index_error)
        QThreadPool.globalInstance().start(worker)

        self._progress_dialog.show()

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
        self._status_bar.showMessage(tr("status.folder", name=folder.name))
        self._refresh_ui()

    def _get_or_open_db(self, folder: Path) -> IndexDB:
        if folder not in self._known_dbs:
            self._known_dbs[folder] = IndexDB(folder)
        return self._known_dbs[folder]

    def _on_index_progress(self, current: int, total: int) -> None:
        if total > 0:
            pct = int(current / total * 100)
            self._status_bar.showMessage(
                tr("status.indexing_pct", pct=pct, current=current, total=total)
            )

    def _on_index_finished(self, count: int) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.accept()
            self._progress_dialog = None
        self._status_bar.showMessage(tr("status.indexed", count=count))
        self._refresh_ui()

    def _on_index_error(self, msg: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.reject()
            self._progress_dialog = None
        self._status_bar.showMessage(tr("status.index_error", msg=msg))

    def force_reindex(self) -> None:
        if self._db is None or self._folder is None:
            return
        self._status_bar.showMessage(tr("status.full_reindex"))
        try:
            queries = scan_folder(self._folder)
            self._db.index_all(queries)
            self._status_bar.showMessage(tr("status.indexed", count=self._db.count()))
            self._refresh_ui()
        except Exception as exc:
            self._status_bar.showMessage(tr("status.reindex_error", msg=str(exc)))

    def _stop_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

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
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._editor.clear()
        self._metadata_panel.clear()
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._editor_wrapper.setStyleSheet("")
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText(tr("editor.btn_edit"))
        self._current_result = None
        self._current_metadata = {}

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _update_content_view(self) -> None:
        """Switch between the normal split view and the onboarding empty state."""
        if self._known_dbs:
            self._content_stack.setCurrentIndex(0)
        else:
            self._content_stack.setCurrentIndex(1)

    def _refresh_status_bar(self) -> None:
        """Update the status bar to reflect the current index state."""
        if self._known_dbs:
            total = sum(db.count() for db in self._known_dbs.values())
            n = len(self._known_dbs)
            q_word = ntr("word.query", "word.queries", total)
            f_word = ntr("word.folder", "word.folders", n)
            self._status_bar.showMessage(
                tr("status.folders_loaded", total=total, query=q_word, n=n, folder=f_word)
            )
        else:
            self._status_bar.showMessage(tr("status.no_folder"))

    def _refresh_ui(self) -> None:
        self._update_content_view()
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
        self._do_search()

    def _do_search(self) -> None:
        """Dispatch a search to the thread pool; discard result if superseded."""
        self._search_timer.stop()   # cancel any still-pending debounce
        if not self._known_dbs:
            return
        self._search_gen += 1
        worker = _SearchWorker(
            dict(self._known_dbs),
            self._search_bar.text(),
            self._browse_folder,
            self._search_gen,
        )
        worker.signals.results_ready.connect(self._on_search_results)
        QThreadPool.globalInstance().start(worker)

    def _on_search_results(self, results: list, gen: int) -> None:
        if gen != self._search_gen:
            return  # stale result from a superseded search
        self._query_list.set_results(results)
        if self._pending_select is not None:
            self._query_list.select_by_rel_path(self._pending_select)
            self._pending_select = None

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
        self._metadata_panel.set_path(path)

        if self._db is not None:
            try:
                is_fav = self._db.is_favorite(result.rel_path)
                self._metadata_panel.set_favorite(is_fav)
            except Exception:
                self._metadata_panel.set_favorite(False)

    # ------------------------------------------------------------------
    # Search / tag / sidebar filtering
    # ------------------------------------------------------------------

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()   # reset countdown on each keystroke

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
        self._search_gen += 1   # discard any in-flight search worker
        self._search_timer.stop()
        self._search_bar.blockSignals(True)
        self._search_bar.clear()
        self._search_bar.blockSignals(False)
        results: list[SearchResult] = []
        for folder, db in self._known_dbs.items():
            try:
                for r in db.get_favorites():
                    r.folder = folder
                    results.append(r)
            except Exception:
                pass
        self._query_list.set_results(results)

    def _on_recent_selected(self) -> None:
        self._search_gen += 1   # discard any in-flight search worker
        self._search_timer.stop()
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

    def _on_filter_requested(self, kind: str, value: str) -> None:
        """Reverse search: clicking a table/column chip fills the search bar."""
        self._search_bar.blockSignals(True)
        self._search_bar._edit.setText(f"{kind}:{value}")
        self._search_bar.blockSignals(False)
        self._do_search()

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
            self._status_bar.showMessage(tr("status.copied", path=result.rel_path))
        elif action == "reveal":
            self._query_list.select_by_rel_path(result.rel_path)
            self.reveal_in_explorer()

    # ------------------------------------------------------------------
    # Sidebar folder explorer handlers
    # ------------------------------------------------------------------

    def _on_folder_remove_requested(self, folder: Path) -> None:
        cfg.remove_known_folder(folder)
        self._sidebar.set_folders(cfg.get_known_folders(), self._folder)
        self._refresh_status_bar()

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

        db = self._known_dbs.pop(folder, None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
            db_path = folder / ".sqlshelf" / "index.db"
            try:
                if db_path.exists():
                    db_path.unlink()
            except Exception:
                pass

        cfg.remove_known_folder(folder)
        self._rebuild_recent_menu()
        self._sidebar.set_folders(cfg.get_known_folders(), self._folder)

        # If the active folder was deindexed, reset the view
        if self._folder is not None and self._folder.resolve() == folder.resolve():
            self._folder = None
            self._db = None
            self._browse_folder = None
            self._reset_editor()
            self._query_list.set_results([])

        self._refresh_ui()
        self._refresh_status_bar()

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
        self._status_bar.showMessage(tr(key, path=result.rel_path))

    # ------------------------------------------------------------------
    # Command palette (Ctrl+K)
    # ------------------------------------------------------------------

    def open_command_palette(self) -> None:
        if not self._known_dbs:
            return
        results: list[SearchResult] = []
        if self._browse_folder is not None:
            db = self._known_dbs.get(self._browse_folder)
            if db:
                results = db.search("")
                for r in results:
                    r.folder = self._browse_folder
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
        if result.folder is not None and result.folder in self._known_dbs:
            self._browse_folder = result.folder
            self._db = self._known_dbs[result.folder]
            self._sidebar.set_folders(cfg.get_known_folders(), result.folder)
        self._search_bar.blockSignals(True)
        self._search_bar.clear()
        self._search_bar.blockSignals(False)
        self._pending_select = result.rel_path
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
        self._metadata_panel.set_edit_mode(True)
        self._save_btn.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._editor_wrapper.setStyleSheet(
            f"#EditorWrapper {{ border: 1px solid {_tk.ACCENT_BORDER}; border-radius: 4px; }}"
        )
        self._edit_toggle_btn.setText(tr("editor.btn_editing"))

    def _cancel_edit_mode(self) -> None:
        """Exit edit mode, discarding unsaved changes by reloading from disk."""
        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._editor_wrapper.setStyleSheet("")
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText(tr("editor.btn_edit"))
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
            QMessageBox.critical(self, tr("msg.save_error"), str(exc))
            return

        if self._db is not None:
            updated = [q for q in scan_folder(self._folder) if q.path == path]
            if updated:
                self._db.upsert_query(updated[0])

        self._edit_mode = False
        self._editor.setReadOnly(True)
        self._metadata_panel.set_edit_mode(False)
        self._save_btn.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._editor_wrapper.setStyleSheet("")
        self._edit_toggle_btn.setChecked(False)
        self._edit_toggle_btn.setText(tr("editor.btn_edit"))
        self._refresh_ui()
        self._status_bar.showMessage(tr("status.saved", path=self._current_result.rel_path))

    # ------------------------------------------------------------------
    # New query / duplicate / templates
    # ------------------------------------------------------------------

    def new_query(self) -> None:
        if self._folder is None:
            QMessageBox.information(
                self, tr("msg.no_folder.title"), tr("msg.no_folder.text")
            )
            return
        all_projects = [p for p, _ in cfg.get_known_folders()]
        if not all_projects:
            all_projects = [self._folder]
        dlg = NewQueryDialog(self._folder, all_projects, self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.created_path is not None:
            self._post_create(dlg.created_path)

    def new_from_template(self) -> None:
        if self._folder is None:
            QMessageBox.information(
                self, tr("msg.no_folder.title"), tr("msg.no_folder.text")
            )
            return
        if not list_templates():
            QMessageBox.information(
                self,
                tr("msg.no_templates.title"),
                tr("msg.no_templates.text", path=str(Path.home() / ".sqlshelf" / "templates")),
            )
            return
        all_projects = [p for p, _ in cfg.get_known_folders()]
        if not all_projects:
            all_projects = [self._folder]
        dlg = TemplateDialog(self._folder, all_projects, self)
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
            and not any(part.startswith(".") for part in p.relative_to(self._folder).parts)
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
            self._status_bar.showMessage(tr("status.created", name=path.name))
            return

        # Switch to the target project if different from the current one
        if target_project != self._folder:
            self._on_sidebar_folder_selected(target_project)

        if self._folder is None:
            return
        updated = [q for q in scan_folder(self._folder) if q.path == path]
        if updated and self._db is not None:
            self._db.upsert_query(updated[0])
        try:
            self._pending_select = path.relative_to(self._folder).as_posix()
        except ValueError:
            pass
        self._refresh_ui()
        self._status_bar.showMessage(tr("status.created", name=path.name))

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
        self._status_bar.showMessage(tr("status.indexed", count=self._db.count()))

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
                QMessageBox.warning(self, tr("msg.open_ssms.title"), str(exc))
        else:
            QMessageBox.information(
                self, tr("msg.open_ssms.title"), tr("msg.open_ssms.windows_only")
            )

    def copy_sql(self) -> None:
        body = self._editor.toPlainText()
        if body:
            QApplication.clipboard().setText(body)
            self._status_bar.showMessage(tr("status.sql_copied"))

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
        self._status_bar.showMessage(tr("status.frontmatter_template_copied"))

    # ------------------------------------------------------------------
    # Help / About
    # ------------------------------------------------------------------

    def _show_help(self) -> None:
        QMessageBox.information(self, tr("help.title"), tr("help.text"))

    def _show_about(self) -> None:
        QMessageBox.about(self, tr("about.title"), tr("about.text"))

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _change_theme(self, name: str) -> None:
        cfg.set_theme(name)
        self._apply_theme_live(name)

    def _apply_theme_live(self, name: str) -> None:
        from .theme.tokens import QT_MATERIAL_THEMES, app_stylesheet, set_active_palette

        set_active_palette(name)

        app = QApplication.instance()
        if app is None:
            return

        try:
            import qt_material
            qt_material.apply_stylesheet(
                app, theme=QT_MATERIAL_THEMES.get(name, "dark_teal.xml")
            )
        except ImportError:
            pass

        app.setStyleSheet(app.styleSheet() + app_stylesheet())

        # Refresh widgets that manage their own stylesheets
        self._search_bar.refresh_theme()
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
        self._stop_watcher()
        for db in self._known_dbs.values():
            try:
                db.close()
            except Exception:
                pass
        super().closeEvent(event)
