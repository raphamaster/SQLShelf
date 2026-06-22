from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QClipboard, QGuiApplication
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from .tag_widget import FlowLayout, TagDisplayWidget, TagInputWidget
from .theme import tokens as _tk
from .theme.tokens import (
    ACCENT,
    ACCENT_FILL,
    BORDER_EMPH,
    STAR_ACTIVE,
    STAR_HOVER,
    TAG_BG,
    TAG_RADIUS,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
)


def _palette_btn_style() -> str:
    return (
        f"QPushButton {{ background-color: {_tk.ACCENT_FILL}; color: {_tk.ACCENT}; "
        f"border: 1px solid {_tk.ACCENT_BORDER}; border-radius: {_tk.RADIUS}px; "
        f"padding: 2px 10px; font-size: 11px; font-weight: 500; }} "
        f"QPushButton:hover {{ background-color: {_tk.ACCENT_FOCUS_BG}; border-color: {_tk.ACCENT}; }}"
    )


def _chip_style() -> str:
    return (
        f"QPushButton {{ background-color: {_tk.TAG_BG}; color: {_tk.TEXT_SECONDARY}; "
        f"border: 1px solid {_tk.BORDER_EMPH}; border-radius: {_tk.TAG_RADIUS}px; "
        f"padding: 1px 8px; font-size: 11px; max-height: 20px; }} "
        f"QPushButton:hover {{ background-color: {_tk.BORDER_EMPH}; color: {_tk.TEXT_PRIMARY}; }}"
    )


def _section_label_style() -> str:
    return f"color: {_tk.TEXT_TERTIARY}; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"


# Height that shows ~2 rows of chips (chip ≈ 20px, v_gap = 4px, +8px breathing room)
_CHIP_SCROLL_MAX_H = 52


def _make_chip_scroll(content: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(content)
    sa.setMaximumHeight(_CHIP_SCROLL_MAX_H)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sa.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    sa.viewport().setAutoFillBackground(False)
    return sa


class MetadataPanel(QWidget):
    """Right-panel top section — query title, description, tags, SQL objects.

    Read-only: displays information on a canvas card.
    Edit mode: title / description / tags are editable.

    Signals:
        navigate_requested(token)     — user left-clicked a table/column/alias chip.
        filter_requested(kind, value) — user chose "Search in all queries" on a chip.
        favorite_toggled              — star button pressed.
    """

    navigate_requested = Signal(str)
    filter_requested = Signal(str, str)
    favorite_toggled = Signal()
    reveal_requested = Signal()
    command_palette_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit_mode = False
        self._tables: list[str] = []
        self._columns: list[str] = []
        self._aliases: list[str] = []

        self.setObjectName("MetadataPanel")
        self.setAutoFillBackground(True)
        self._file_path: Path | None = None

        # ── Star / title / Ctrl+K hint ─────────────────────────────────────
        self._star_btn = QPushButton("☆")
        self._star_btn.setFixedSize(22, 22)
        self._star_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; font-size: 15px; "
            f"color: {TEXT_TERTIARY}; padding: 0; }} "
            f"QPushButton:hover {{ color: {STAR_HOVER}; }}"
        )
        self._star_btn.setToolTip(tr("metadata.toggle_favorite"))
        self._star_btn.clicked.connect(self.favorite_toggled)

        self._title_label = QLabel()
        self._title_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {TEXT_PRIMARY};"
        )
        self._title_label.setWordWrap(True)

        self._shortcut_hint = QPushButton("⌘  Ctrl+P")
        self._shortcut_hint.setToolTip(tr("metadata.command_palette"))
        self._shortcut_hint.setStyleSheet(_palette_btn_style())
        self._shortcut_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shortcut_hint.clicked.connect(self.command_palette_requested)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        title_row.addWidget(self._star_btn)
        title_row.addWidget(self._title_label, stretch=1)
        title_row.addStretch()
        title_row.addWidget(self._shortcut_hint)

        # ── Description ────────────────────────────────────────────────────
        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")

        # ── Tags section (mint chips) ──────────────────────────────────────
        self._tags_display = TagDisplayWidget()
        self._tags_section = _section_container(tr("metadata.section_tags"), self._tags_display)
        self._tags_section.setVisible(False)

        # ── Tables section (clickable neutral chips, scrollable after 2 rows) ──
        self._tables_flow_widget = QWidget()
        self._tables_flow = FlowLayout(self._tables_flow_widget, h_gap=4, v_gap=4)
        self._tables_flow_widget.setLayout(self._tables_flow)
        self._tables_scroll = _make_chip_scroll(self._tables_flow_widget)
        self._tables_section = _section_container(tr("metadata.section_tables"), self._tables_scroll)
        self._tables_section.setVisible(False)

        # ── Columns section (clickable neutral chips, scrollable after 2 rows) ─
        self._columns_flow_widget = QWidget()
        self._columns_flow = FlowLayout(self._columns_flow_widget, h_gap=4, v_gap=4)
        self._columns_flow_widget.setLayout(self._columns_flow)
        self._columns_scroll = _make_chip_scroll(self._columns_flow_widget)
        self._columns_section = _section_container(tr("metadata.section_columns"), self._columns_scroll)
        self._columns_section.setVisible(False)

        # ── Aliases section (clickable neutral chips, scrollable after 2 rows) ─
        self._aliases_flow_widget = QWidget()
        self._aliases_flow = FlowLayout(self._aliases_flow_widget, h_gap=4, v_gap=4)
        self._aliases_flow_widget.setLayout(self._aliases_flow)
        self._aliases_scroll = _make_chip_scroll(self._aliases_flow_widget)
        self._aliases_section = _section_container(tr("metadata.section_aliases"), self._aliases_scroll)
        self._aliases_section.setVisible(False)

        # ── File path (clickable, right-click to copy) ────────────────────
        self._path_btn = QPushButton()
        self._path_btn.setFlat(True)
        self._path_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._path_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; text-align: left; "
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 0px 0px 1px 0px; }} "
            f"QPushButton:hover {{ color: {ACCENT}; }}"
        )
        self._path_btn.clicked.connect(self.reveal_requested)
        self._path_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._path_btn.customContextMenuRequested.connect(self._show_path_menu)
        self._path_section = _section_container(tr("metadata.section_file"), self._path_btn)
        self._path_section.setVisible(False)

        # ── Modification date ──────────────────────────────────────────────
        self._mtime_label = QLabel()
        self._mtime_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px;"
        )
        self._mtime_section = _section_container(tr("metadata.section_mtime"), self._mtime_label)
        self._mtime_section.setVisible(False)

        # ── Read-only container ────────────────────────────────────────────
        ro_layout = QVBoxLayout()
        ro_layout.setContentsMargins(10, 10, 10, 8)
        ro_layout.setSpacing(6)
        ro_layout.addLayout(title_row)
        ro_layout.addWidget(self._desc_label)
        ro_layout.addWidget(self._tags_section)
        ro_layout.addWidget(self._tables_section)
        ro_layout.addWidget(self._columns_section)
        ro_layout.addWidget(self._aliases_section)
        _file_row = QHBoxLayout()
        _file_row.setContentsMargins(0, 0, 0, 0)
        _file_row.setSpacing(16)
        _file_row.addWidget(self._path_section, stretch=1)
        _file_row.addWidget(self._mtime_section)
        ro_layout.addLayout(_file_row)

        self._ro_widget = QWidget()
        self._ro_widget.setLayout(ro_layout)

        # ── Edit-mode widgets ──────────────────────────────────────────────
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText(tr("metadata.title_placeholder"))

        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText(tr("metadata.desc_placeholder"))
        self._desc_edit.setMaximumHeight(70)

        self._tags_input = TagInputWidget()

        self._edit_form = QFormLayout()
        self._edit_form.setContentsMargins(10, 10, 10, 8)
        self._edit_form.setSpacing(6)
        self._edit_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._edit_form.addRow(tr("metadata.label_title"), self._title_edit)
        self._edit_form.addRow(tr("metadata.label_desc"), self._desc_edit)
        self._edit_form.addRow(tr("metadata.label_tags"), self._tags_input)
        edit_layout = self._edit_form

        self._edit_widget = QWidget()
        self._edit_widget.setLayout(edit_layout)
        self._edit_widget.setVisible(False)

        # ── Main layout ────────────────────────────────────────────────────
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._ro_widget)
        main.addWidget(self._edit_widget)

        sp = self.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Maximum)
        self.setSizePolicy(sp)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _clear_flow(self, flow: FlowLayout) -> None:
        while flow.count():
            item = flow.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _make_object_chip(self, label: str, kind: str) -> QPushButton:
        """Create a chip that navigates to *label* on left-click and shows a
        context menu with search/copy options on right-click."""
        btn = QPushButton(label)
        btn.setStyleSheet(_chip_style())
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tr("metadata.chip_tooltip_navigate"))
        btn.clicked.connect(lambda checked=False, t=label: self.navigate_requested.emit(t))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, t=label, k=kind: self._show_chip_menu(btn, t, k)
        )
        return btn

    def _show_chip_menu(self, btn: QPushButton, token: str, kind: str) -> None:
        menu = QMenu(self)
        nav_act = menu.addAction(tr("metadata.chip_go_to_occurrence"))
        menu.addSeparator()
        search_act = menu.addAction(tr("metadata.chip_search_all"))
        copy_act = menu.addAction(tr("metadata.chip_copy_name"))
        chosen = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if chosen == nav_act:
            self.navigate_requested.emit(token)
        elif chosen == search_act:
            self.filter_requested.emit(kind, token)
        elif chosen == copy_act:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(token)

    def _rebuild_object_chips(self) -> None:
        self._clear_flow(self._tables_flow)
        self._clear_flow(self._columns_flow)
        self._clear_flow(self._aliases_flow)

        self._tables_section.setVisible(bool(self._tables))
        self._columns_section.setVisible(bool(self._columns))
        self._aliases_section.setVisible(bool(self._aliases))

        for table in sorted(self._tables):
            self._tables_flow.addWidget(self._make_object_chip(table, "table"))

        for col in sorted(self._columns):
            self._columns_flow.addWidget(self._make_object_chip(col, "col"))

        for alias in sorted(self._aliases):
            self._aliases_flow.addWidget(self._make_object_chip(alias, "alias"))

        self._tables_flow_widget.updateGeometry()
        self._columns_flow_widget.updateGeometry()
        self._aliases_flow_widget.updateGeometry()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_query(
        self,
        title: str,
        description: str,
        tags: list[str],
        tables: list[str],
        columns: list[str],
        aliases: list[str] | None = None,
    ) -> None:
        self._title_label.setText(title)
        self._desc_label.setText(description)
        self._tags_display.set_tags(tags, accent=True)
        self._tags_section.setVisible(bool(tags))
        self._tables = list(tables)
        self._columns = list(columns)
        self._aliases = list(aliases) if aliases else []
        self._rebuild_object_chips()

        self._title_edit.setText(title)
        self._desc_edit.setPlainText(description)
        self._tags_input.set_tags(tags)

    def set_path(self, path: Path | None) -> None:
        self._file_path = path
        if path is None:
            self._path_section.setVisible(False)
            return
        full = str(path)
        self._path_btn.setText(full)
        self._path_btn.setToolTip(full)
        try:
            mtime = path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y  %H:%M")
            self._mtime_label.setText(dt)
            self._mtime_section.setVisible(True)
        except OSError:
            self._mtime_section.setVisible(False)
        self._path_section.setVisible(True)

    def _show_path_menu(self) -> None:
        if self._file_path is None:
            return
        menu = QMenu(self)
        copy_act = menu.addAction(tr("metadata.copy_path"))
        copy_dir_act = menu.addAction(tr("metadata.copy_folder_path"))
        menu.addSeparator()
        reveal_act = menu.addAction(tr("metadata.reveal_in_explorer"))
        chosen = menu.exec(self._path_btn.mapToGlobal(self._path_btn.rect().bottomLeft()))
        if chosen == copy_act:
            QGuiApplication.clipboard().setText(str(self._file_path))
        elif chosen == copy_dir_act:
            QGuiApplication.clipboard().setText(str(self._file_path.parent))
        elif chosen == reveal_act:
            self.reveal_requested.emit()

    def set_favorite(self, is_fav: bool) -> None:
        if is_fav:
            self._star_btn.setText("★")
            self._star_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; font-size: 15px; "
                f"color: {STAR_ACTIVE}; padding: 0; }} "
                f"QPushButton:hover {{ color: {STAR_HOVER}; }}"
            )
        else:
            self._star_btn.setText("☆")
            self._star_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; font-size: 15px; "
                f"color: {TEXT_TERTIARY}; padding: 0; }} "
                f"QPushButton:hover {{ color: {STAR_HOVER}; }}"
            )

    def set_available_tags(self, tags: list[str]) -> None:
        """Forward the known-tag pool to the tag input for autocomplete."""
        self._tags_input.set_available_tags(tags)

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._ro_widget.setVisible(not enabled)
        self._edit_widget.setVisible(enabled)
        if enabled:
            self._title_edit.setFocus()

    def get_edited_values(self) -> tuple[str, str, list[str]]:
        title = self._title_edit.text().strip()
        description = self._desc_edit.toPlainText().strip()
        tags = self._tags_input.get_tags()
        return title, description, tags

    def retranslate_ui(self) -> None:
        self._star_btn.setToolTip(tr("metadata.toggle_favorite"))
        self._shortcut_hint.setToolTip(tr("metadata.command_palette"))
        self._title_edit.setPlaceholderText(tr("metadata.title_placeholder"))
        self._desc_edit.setPlaceholderText(tr("metadata.desc_placeholder"))
        # Section labels
        for section, key in [
            (self._tags_section, "metadata.section_tags"),
            (self._tables_section, "metadata.section_tables"),
            (self._columns_section, "metadata.section_columns"),
            (self._aliases_section, "metadata.section_aliases"),
            (self._path_section, "metadata.section_file"),
            (self._mtime_section, "metadata.section_mtime"),
        ]:
            lbl = section.layout().itemAt(0).widget() if section.layout() else None
            if isinstance(lbl, QLabel):
                lbl.setText(tr(key))
        # Form row labels
        lbl = self._edit_form.labelForField(self._title_edit)
        if lbl:
            lbl.setText(tr("metadata.label_title"))
        lbl = self._edit_form.labelForField(self._desc_edit)
        if lbl:
            lbl.setText(tr("metadata.label_desc"))
        lbl = self._edit_form.labelForField(self._tags_input)
        if lbl:
            lbl.setText(tr("metadata.label_tags"))

    def refresh_theme(self) -> None:
        self._title_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {_tk.TEXT_PRIMARY};"
        )
        self._desc_label.setStyleSheet(f"color: {_tk.TEXT_SECONDARY}; font-size: 12px;")
        self._shortcut_hint.setStyleSheet(_palette_btn_style())
        self._path_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; text-align: left; "
            f"color: {_tk.TEXT_SECONDARY}; font-size: 11px; padding: 0px 0px 1px 0px; }} "
            f"QPushButton:hover {{ color: {_tk.ACCENT}; }}"
        )
        is_fav = self._star_btn.text() == "★"
        self.set_favorite(is_fav)
        self._rebuild_object_chips()
        # Refresh section labels
        from PySide6.QtWidgets import QLabel
        style = _section_label_style()
        self._mtime_label.setStyleSheet(f"color: {_tk.TEXT_SECONDARY}; font-size: 11px;")
        for section in [
            self._tags_section,
            self._tables_section,
            self._columns_section,
            self._aliases_section,
            self._path_section,
            self._mtime_section,
        ]:
            lbl = section.layout().itemAt(0).widget() if section.layout() else None
            if isinstance(lbl, QLabel):
                lbl.setStyleSheet(style)

    def clear(self) -> None:
        self._title_label.setText("")
        self._desc_label.setText("")
        self._tags_display.set_tags([], accent=True)
        self._tags_section.setVisible(False)
        self._tables = []
        self._columns = []
        self._aliases = []
        self._rebuild_object_chips()
        self._title_edit.clear()
        self._desc_edit.clear()
        self._tags_input.set_tags([])
        self._mtime_label.setText("")
        self._mtime_section.setVisible(False)
        self.set_favorite(False)
        self.set_path(None)


# ── Module-level helper (avoids repeating section-building boilerplate) ────────

def _section_container(label_text: str, content: QWidget) -> QWidget:
    w = QWidget()
    vb = QVBoxLayout(w)
    vb.setContentsMargins(0, 0, 0, 0)
    vb.setSpacing(2)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(_section_label_style())
    vb.addWidget(lbl)
    vb.addWidget(content)
    return w
