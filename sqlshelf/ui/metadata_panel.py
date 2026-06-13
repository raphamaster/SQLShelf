from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
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
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #e8e8f0;")
        self._title_label.setWordWrap(True)

        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #9090a8; font-size: 12px;")

        self._tags_display = TagDisplayWidget()

        # Tables and columns on separate lines
        self._tables_label = QLabel()
        self._tables_label.setWordWrap(True)
        self._tables_label.setStyleSheet("font-size: 11px; color: #6a8a6a;")
        self._tables_label.setTextFormat(Qt.TextFormat.RichText)
        self._tables_label.setOpenExternalLinks(False)
        self._tables_label.linkActivated.connect(self.table_clicked)

        self._columns_label = QLabel()
        self._columns_label.setWordWrap(True)
        self._columns_label.setStyleSheet("font-size: 11px; color: #6a8a6a;")
        self._columns_label.setTextFormat(Qt.TextFormat.RichText)
        self._columns_label.setOpenExternalLinks(False)
        self._columns_label.linkActivated.connect(self.table_clicked)

        ro_layout = QVBoxLayout()
        ro_layout.setContentsMargins(10, 10, 10, 8)
        ro_layout.setSpacing(4)
        ro_layout.addWidget(self._title_label)
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

        def _links(items: list[str], prefix: str) -> str:
            return " ".join(
                f'<a href="{prefix}{t}" style="color:#7aaa7a; text-decoration:none;">{t}</a>'
                for t in sorted(items)
            )

        if tables:
            self._tables_label.setText(
                f'<span style="color:#555570;">Tables:</span> {_links(tables, "table:")}'
            )
        else:
            self._tables_label.setText("")

        if columns:
            self._columns_label.setText(
                f'<span style="color:#555570;">Columns:</span> {_links(columns, "col:")}'
            )
        else:
            self._columns_label.setText("")

        # Pre-fill edit widgets
        self._title_edit.setText(title)
        self._desc_edit.setPlainText(description)
        self._tags_input.set_tags(tags)

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
        self._tables_label.setText("")
        self._columns_label.setText("")
        self._title_edit.clear()
        self._desc_edit.clear()
        self._tags_input.set_tags([])
