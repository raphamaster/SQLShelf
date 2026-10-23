"""The bar above the results while searching: where to search, and how many.

"Search in" offers the whole library or the folder the search started from;
the right side counts the results and clears the search. Colours come from
``QWidget#ScopeBar`` in the global QSS.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ..core.i18n import ntr, tr

HEIGHT = 40
OPTION_HEIGHT = 26


class ScopeBar(QWidget):
    """Scope options for the current search, its result count and "Clear search".

    Signals:
        scope_selected(object) — the folder to search in, or None for the library.
        clear_requested() — leave the search.
    """

    scope_selected = Signal(object)
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScopeBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        self._folder: Path | None = None
        self._option: Path | None = None
        self._count: int | None = None

        self._label = QLabel()
        self._library_btn = self._make_option()
        self._library_btn.clicked.connect(lambda: self._select(None))
        self._folder_btn = self._make_option()
        self._folder_btn.clicked.connect(lambda: self._select(self._option))
        group = QButtonGroup(self)
        group.addButton(self._library_btn)
        group.addButton(self._folder_btn)

        self._count_label = QLabel()
        self._clear_btn = QPushButton()
        self._clear_btn.setObjectName("ScopeClear")
        self._clear_btn.setFixedHeight(OPTION_HEIGHT)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self.clear_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(4)
        layout.addWidget(self._label)
        layout.addSpacing(6)
        layout.addWidget(self._library_btn)
        layout.addWidget(self._folder_btn)
        layout.addStretch(1)
        layout.addWidget(self._count_label)
        layout.addSpacing(12)
        layout.addWidget(self._clear_btn)

        self.set_scope(None, None)
        self.retranslate_ui()

    # ------------------------------------------------------------------

    def set_scope(self, folder: Path | None, option: Path | None) -> None:
        """Mark *folder* (None: the library) as searched; offer *option* beside it.

        *option* is the folder the search can be narrowed to, None for none.
        """
        self._folder = folder
        self._option = option if option is not None else folder
        self._folder_btn.setVisible(self._option is not None)
        if self._option is not None:
            self._folder_btn.setText(self._option.name or str(self._option))
            self._folder_btn.setToolTip(str(self._option))
        self._library_btn.setChecked(folder is None)
        self._folder_btn.setChecked(folder is not None)

    def set_count(self, count: int | None) -> None:
        """Show how many results the search found; None while it runs."""
        self._count = count
        self._render_count()

    def count_text(self) -> str:
        return self._count_label.text()

    def options(self) -> list[QPushButton]:
        """The visible scope options, the library first."""
        return [b for b in (self._library_btn, self._folder_btn) if not b.isHidden()]

    def retranslate_ui(self) -> None:
        self._label.setText(tr("search.scope_in"))
        self._library_btn.setText(tr("search.scope_library"))
        self._clear_btn.setText(tr("search.clear"))
        self._render_count()

    # ------------------------------------------------------------------

    def _make_option(self) -> QPushButton:
        button = QPushButton()
        button.setObjectName("ScopeOption")
        button.setCheckable(True)
        button.setFixedHeight(OPTION_HEIGHT)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _select(self, folder: Path | None) -> None:
        # The group keeps the clicked option checked; the window confirms it
        # through set_scope once the search has moved.
        self._library_btn.setChecked(self._folder is None)
        self._folder_btn.setChecked(self._folder is not None)
        if folder != self._folder:
            self.scope_selected.emit(folder)

    def _render_count(self) -> None:
        count = self._count
        self._count_label.setVisible(count is not None)
        if count is not None:
            self._count_label.setText(
                ntr("status.n_results_one", "status.n_results_other", count)
            )
