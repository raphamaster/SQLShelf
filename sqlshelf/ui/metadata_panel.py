from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .tag_widget import FlowLayout, TagDisplayWidget, TagInputWidget
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

_CHIP_STYLE = (
    f"QPushButton {{ background-color: {TAG_BG}; color: {TEXT_SECONDARY}; "
    f"border: 1px solid {BORDER_EMPH}; border-radius: {TAG_RADIUS}px; "
    f"padding: 1px 8px; font-size: 11px; max-height: 20px; }} "
    f"QPushButton:hover {{ background-color: {BORDER_EMPH}; color: {TEXT_PRIMARY}; }}"
)

_SECTION_LABEL_STYLE = (
    f"color: {TEXT_TERTIARY}; font-size: 9px; font-weight: bold; letter-spacing: 0.5px;"
)


class MetadataPanel(QWidget):
    """Right-panel top section — query title, description, tags, SQL objects.

    Read-only: displays information on a canvas card.
    Edit mode: title / description / tags are editable.

    Signals:
        filter_requested(kind, value) — user clicked a table/column chip.
        favorite_toggled              — star button pressed.
    """

    filter_requested = Signal(str, str)
    favorite_toggled = Signal()
    reveal_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit_mode = False
        self._tables: list[str] = []
        self._columns: list[str] = []

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
        self._star_btn.setToolTip("Toggle favorite")
        self._star_btn.clicked.connect(self.favorite_toggled)

        self._title_label = QLabel()
        self._title_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {TEXT_PRIMARY};"
        )
        self._title_label.setWordWrap(True)

        self._shortcut_hint = QLabel("Ctrl+P")
        self._shortcut_hint.setStyleSheet(
            f"color: {TEXT_TERTIARY}; font-size: 9px; padding-right: 4px;"
        )
        self._shortcut_hint.setToolTip("Command palette")

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
        self._tags_section = _section_container("TAGS", self._tags_display)
        self._tags_section.setVisible(False)

        # ── Tables section (clickable neutral chips) ───────────────────────
        self._tables_flow_widget = QWidget()
        self._tables_flow = FlowLayout(self._tables_flow_widget, h_gap=4, v_gap=4)
        self._tables_flow_widget.setLayout(self._tables_flow)
        self._tables_section = _section_container("TABLES", self._tables_flow_widget)
        self._tables_section.setVisible(False)

        # ── Columns section (clickable neutral chips) ──────────────────────
        self._columns_flow_widget = QWidget()
        self._columns_flow = FlowLayout(self._columns_flow_widget, h_gap=4, v_gap=4)
        self._columns_flow_widget.setLayout(self._columns_flow)
        self._columns_section = _section_container("COLUMNS", self._columns_flow_widget)
        self._columns_section.setVisible(False)

        # ── File path (clickable) ──────────────────────────────────────────
        self._path_btn = QPushButton()
        self._path_btn.setFlat(True)
        self._path_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._path_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; text-align: left; "
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 0px 0px 1px 0px; }} "
            f"QPushButton:hover {{ color: {ACCENT}; }}"
        )
        self._path_btn.clicked.connect(self.reveal_requested)
        self._path_section = _section_container("FILE", self._path_btn)
        self._path_section.setVisible(False)

        # ── Read-only container ────────────────────────────────────────────
        ro_layout = QVBoxLayout()
        ro_layout.setContentsMargins(10, 10, 10, 8)
        ro_layout.setSpacing(6)
        ro_layout.addLayout(title_row)
        ro_layout.addWidget(self._desc_label)
        ro_layout.addWidget(self._tags_section)
        ro_layout.addWidget(self._tables_section)
        ro_layout.addWidget(self._columns_section)
        ro_layout.addWidget(self._path_section)

        self._ro_widget = QWidget()
        self._ro_widget.setLayout(ro_layout)

        # ── Edit-mode widgets ──────────────────────────────────────────────
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Title")

        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText("Description")
        self._desc_edit.setMaximumHeight(70)

        self._tags_input = TagInputWidget()

        edit_layout = QFormLayout()
        edit_layout.setContentsMargins(10, 10, 10, 8)
        edit_layout.setSpacing(6)
        edit_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        edit_layout.addRow("Title:", self._title_edit)
        edit_layout.addRow("Description:", self._desc_edit)
        edit_layout.addRow("Tags:", self._tags_input)

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

    def _rebuild_object_chips(self) -> None:
        self._clear_flow(self._tables_flow)
        self._clear_flow(self._columns_flow)

        self._tables_section.setVisible(bool(self._tables))
        self._columns_section.setVisible(bool(self._columns))

        for table in sorted(self._tables):
            btn = QPushButton(table)
            btn.setStyleSheet(_CHIP_STYLE)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(
                lambda checked=False, t=table: self.filter_requested.emit("table", t)
            )
            self._tables_flow.addWidget(btn)

        for col in sorted(self._columns):
            btn = QPushButton(col)
            btn.setStyleSheet(_CHIP_STYLE)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(
                lambda checked=False, c=col: self.filter_requested.emit("col", c)
            )
            self._columns_flow.addWidget(btn)

        self._tables_flow_widget.updateGeometry()
        self._columns_flow_widget.updateGeometry()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_query(
        self,
        title: str,
        description: str,
        tags: list[str],
        tables: list[str],
        columns: list[str],
    ) -> None:
        self._title_label.setText(title)
        self._desc_label.setText(description)
        self._tags_display.set_tags(tags, accent=True)
        self._tags_section.setVisible(bool(tags))
        self._tables = list(tables)
        self._columns = list(columns)
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
        self._path_section.setVisible(True)

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

    def clear(self) -> None:
        self._title_label.setText("")
        self._desc_label.setText("")
        self._tags_display.set_tags([], accent=True)
        self._tags_section.setVisible(False)
        self._tables = []
        self._columns = []
        self._rebuild_object_chips()
        self._title_edit.clear()
        self._desc_edit.clear()
        self._tags_input.set_tags([])
        self.set_favorite(False)
        self.set_path(None)


# ── Module-level helper (avoids repeating section-building boilerplate) ────────

def _section_container(label_text: str, content: QWidget) -> QWidget:
    w = QWidget()
    vb = QVBoxLayout(w)
    vb.setContentsMargins(0, 0, 0, 0)
    vb.setSpacing(2)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(_SECTION_LABEL_STYLE)
    vb.addWidget(lbl)
    vb.addWidget(content)
    return w
