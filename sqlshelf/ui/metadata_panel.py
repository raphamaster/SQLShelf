from __future__ import annotations

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

from .tag_widget import TagDisplayWidget, TagInputWidget


class MetadataPanel(QWidget):
    """Right-panel top section — query title, description, tags, SQL objects.

    Read-only: displays information on a canvas card.
    Edit mode: title / description / tags are editable.

    Signals:
        table_clicked(str)  — user clicked a table or column link (reverse search).
    """

    table_clicked = Signal(str)
    favorite_toggled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit_mode = False

        # ------------------------------------------------------------------
        # Canvas background
        # ------------------------------------------------------------------
        self.setObjectName("MetadataPanel")
        self.setStyleSheet("""
            QWidget#MetadataPanel {
                background-color: #1c1c2e;
                border-bottom: 1px solid #2e2e50;
            }
        """)
        self.setAutoFillBackground(True)

        # ------------------------------------------------------------------
        # Read-only widgets
        # ------------------------------------------------------------------
        self._star_btn = QPushButton("☆")
        self._star_btn.setFixedSize(22, 22)
        self._star_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; font-size: 15px; "
            "color: #555570; padding: 0; } "
            "QPushButton:hover { color: #ffd700; }"
        )
        self._star_btn.setToolTip("Toggle favorite")
        self._star_btn.clicked.connect(self.favorite_toggled)

        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #e8e8f0;")
        self._title_label.setWordWrap(True)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        title_row.addWidget(self._star_btn)
        title_row.addWidget(self._title_label, stretch=1)

        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #9090a8; font-size: 12px;")

        self._tags_display = TagDisplayWidget()

        # Tables and columns on separate lines
        self._tables: list[str] = []
        self._columns: list[str] = []
        self._hovered_link: str = ""

        self._tables_label = QLabel()
        self._tables_label.setWordWrap(True)
        self._tables_label.setStyleSheet("font-size: 11px;")
        self._tables_label.setTextFormat(Qt.TextFormat.RichText)
        self._tables_label.setOpenExternalLinks(False)
        self._tables_label.linkActivated.connect(self.table_clicked)
        self._tables_label.linkHovered.connect(self._on_link_hovered)

        self._columns_label = QLabel()
        self._columns_label.setWordWrap(True)
        self._columns_label.setStyleSheet("font-size: 11px;")
        self._columns_label.setTextFormat(Qt.TextFormat.RichText)
        self._columns_label.setOpenExternalLinks(False)
        self._columns_label.linkActivated.connect(self.table_clicked)
        self._columns_label.linkHovered.connect(self._on_link_hovered)

        ro_layout = QVBoxLayout()
        ro_layout.setContentsMargins(10, 10, 10, 8)
        ro_layout.setSpacing(4)
        ro_layout.addLayout(title_row)
        ro_layout.addWidget(self._desc_label)
        ro_layout.addWidget(self._tags_display)
        ro_layout.addWidget(self._tables_label)
        ro_layout.addWidget(self._columns_label)

        self._ro_widget = QWidget()
        self._ro_widget.setLayout(ro_layout)

        # ------------------------------------------------------------------
        # Edit-mode widgets
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Main layout
        # ------------------------------------------------------------------
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._ro_widget)
        main.addWidget(self._edit_widget)

        sp = self.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Maximum)
        self.setSizePolicy(sp)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        self._tags_display.set_tags(tags)
        self._tables = list(tables)
        self._columns = list(columns)
        self._hovered_link = ""
        self._rebuild_links()

        # Pre-fill edit widgets
        self._title_edit.setText(title)
        self._desc_edit.setPlainText(description)
        self._tags_input.set_tags(tags)

    def _on_link_hovered(self, href: str) -> None:
        self._hovered_link = href
        self._rebuild_links()

    def _rebuild_links(self) -> None:
        prefix_style = 'color:#9090c0;'
        if self._tables:
            self._tables_label.setText(
                f'<span style="{prefix_style}">Tables:</span> '
                + self._make_links(self._tables, "table:")
            )
        else:
            self._tables_label.setText("")

        if self._columns:
            self._columns_label.setText(
                f'<span style="{prefix_style}">Columns:</span> '
                + self._make_links(self._columns, "col:")
            )
        else:
            self._columns_label.setText("")

    def _make_links(self, items: list[str], prefix: str) -> str:
        return " ".join(
            f'<a href="{prefix}{t}" style="color:#7aaa7a; '
            f'text-decoration:{"underline" if self._hovered_link == prefix + t else "none"};">{t}</a>'
            for t in sorted(items)
        )

    def set_favorite(self, is_fav: bool) -> None:
        if is_fav:
            self._star_btn.setText("★")
            self._star_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; font-size: 15px; "
                "color: #ffd700; padding: 0; } "
                "QPushButton:hover { color: #ffec6e; }"
            )
        else:
            self._star_btn.setText("☆")
            self._star_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; font-size: 15px; "
                "color: #555570; padding: 0; } "
                "QPushButton:hover { color: #ffd700; }"
            )

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._ro_widget.setVisible(not enabled)
        self._edit_widget.setVisible(enabled)
        if enabled:
            self._title_edit.setFocus()

    def get_edited_values(self) -> tuple[str, str, list[str]]:
        """Return (title, description, tags) from the edit widgets."""
        title = self._title_edit.text().strip()
        description = self._desc_edit.toPlainText().strip()
        tags = self._tags_input.get_tags()
        return title, description, tags

    def clear(self) -> None:
        self._title_label.setText("")
        self._desc_label.setText("")
        self._tags_display.set_tags([])
        self._tables = []
        self._columns = []
        self._hovered_link = ""
        self._tables_label.setText("")
        self._columns_label.setText("")
        self._title_edit.clear()
        self._desc_edit.clear()
        self._tags_input.set_tags([])
        self.set_favorite(False)
