"""Status bar: list counts on the left; index and watcher state, then the
open query's dialect, on the right.

Indexing progress lives here instead of in a modal dialog, so the window
stays usable while a folder is indexed.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QStatusBar,
    QWidget,
)

from ..core.dialects import Resolved
from ..core.i18n import ntr, tr
from .editor_strip import dialect_tooltip

BAR_HEIGHT = 28
FLASH_MS = 5000


def _dot_separator() -> QLabel:
    sep = QLabel("·")
    sep.setObjectName("StatusSeparator")
    return sep


class StatusBar(QStatusBar):
    """Window status bar.

    ``flash()`` shows a transient message in place of the counts; the index
    state on the right is a permanent widget, so a message never hides the
    progress.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(BAR_HEIGHT)
        self.setSizeGripEnabled(False)
        self.setContentsMargins(10, 0, 8, 0)

        self._count = 0
        self._searching = False
        self._selected = 0
        self._has_library = False
        self._indexing: tuple[str, int, int] | None = None
        self._watching = 0
        self._dialect_shown: Resolved | None = None
        self._flash_text = ""
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        self._counts = QLabel()
        self._counts.setObjectName("StatusText")
        self.addWidget(self._counts)

        right = QWidget()
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 4, 0)
        right_layout.setSpacing(8)
        self._dot = QLabel()
        self._dot.setObjectName("StatusDot")
        self._dot.setFixedSize(8, 8)
        self._index = QLabel()
        self._index.setObjectName("StatusText")
        self._progress = QProgressBar()
        self._progress.setObjectName("StatusProgress")
        self._progress.setFixedSize(120, 4)
        self._progress.setTextVisible(False)
        self._progress_text = QLabel()
        self._progress_text.setObjectName("StatusText")
        self._watch_sep = _dot_separator()
        self._watch = QLabel()
        self._watch.setObjectName("StatusText")
        self._dialect_sep = _dot_separator()
        self._dialect = QLabel()
        self._dialect.setObjectName("StatusText")
        for widget in (
            self._dot,
            self._index,
            self._progress,
            self._progress_text,
            self._watch_sep,
            self._watch,
            self._dialect_sep,
            self._dialect,
        ):
            right_layout.addWidget(widget)
        self.addPermanentWidget(right)
        self._render()

    # ------------------------------------------------------------------

    def set_counts(self, count: int, *, searching: bool, selected: int) -> None:
        """Left side: how many queries are listed, and how many are selected."""
        self._has_library = True
        self._count = count
        self._searching = searching
        self._selected = selected
        self._render()

    def set_selected(self, selected: int) -> None:
        self._selected = selected
        self._render()

    def set_no_library(self) -> None:
        self._has_library = False
        self._render()

    def set_indexing(self, folder_name: str, current: int = 0, total: int = 0) -> None:
        """Show indexing progress; *total* 0 means the file count is unknown."""
        self._indexing = (folder_name, current, total)
        self._render()

    def set_index_ready(self) -> None:
        self._indexing = None
        self._render()

    def set_watching(self, folders: int) -> None:
        self._watching = folders
        self._render()

    def set_dialect(self, dialect: Resolved | None) -> None:
        """The open query's dialect; None, with no query open, hides it."""
        self._dialect_shown = dialect
        self._render()

    def flash(self, message: str, timeout: int = FLASH_MS) -> None:
        """Show *message* for a few seconds in place of the counts."""
        # Drawn by our own label, not showMessage(), so it lines up with the
        # counts it replaces.
        self._flash_text = message
        self._flash_timer.start(timeout)
        self._render()

    def _end_flash(self) -> None:
        self._flash_text = ""
        self._render()

    def retranslate_ui(self) -> None:
        self._render()

    # ------------------------------------------------------------------

    def _render(self) -> None:
        if self._flash_text:
            self._counts.setText(self._flash_text)
        elif not self._has_library:
            self._counts.setText(tr("status.no_folder"))
        else:
            key = "status.n_results" if self._searching else "status.n_queries"
            text = ntr(f"{key}_one", f"{key}_other", self._count)
            if self._selected:
                text += "  ·  " + tr("status.selected", count=self._selected)
            self._counts.setText(text)

        indexing = self._indexing
        show_index = indexing is not None or self._has_library
        self._dot.setVisible(indexing is None and self._has_library)
        self._index.setVisible(show_index)
        self._progress.setVisible(indexing is not None)
        self._progress_text.setVisible(indexing is not None and indexing[2] > 0)
        if indexing is None:
            self._index.setText(tr("status.index_ready"))
        else:
            name, current, total = indexing
            self._index.setText(tr("status.indexing_folder", name=name))
            # An unknown total shows Qt's busy animation.
            self._progress.setRange(0, max(total, 0))
            if total > 0:
                self._progress.setValue(min(current, total))
                self._progress_text.setText(
                    tr("status.progress_of", current=current, total=total)
                )

        watching = self._watching > 0
        self._watch_sep.setVisible(watching and show_index)
        self._watch.setVisible(watching)
        if watching:
            self._watch.setText(
                ntr("status.watching_one", "status.watching_other", self._watching)
            )
        dialect = self._dialect_shown
        self._dialect.setVisible(dialect is not None)
        self._dialect_sep.setVisible(dialect is not None and (watching or show_index))
        if dialect is not None:
            self._dialect.setText(dialect.dialect.label)
            self._dialect.setToolTip(dialect_tooltip(dialect))
