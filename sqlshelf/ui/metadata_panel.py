from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class MetadataPanel(QWidget):
    """Right-panel top section: title, description, tags, SQL objects.

    In read-only mode shows labels; edit mode makes title/description/tags editable.
    """

    metadata_changed = Signal(str, str, list)  # title, description, tags

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edit_mode = False

        # --- read-only labels ---
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._title_label.setWordWrap(True)

        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: #aaaaaa;")

        self._tags_label = QLabel()
        self._tags_label.setStyleSheet("color: #66aacc;")
        self._tags_label.setWordWrap(True)

        self._objects_label = QLabel()
        self._objects_label.setWordWrap(True)
        self._objects_label.setStyleSheet("color: #88aa88; font-size: 11px;")

        # --- edit-mode widgets ---
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Title")
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText("Description")
        self._desc_edit.setMaximumHeight(80)
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("Tags (comma-separated)")

        # layout
        self._ro_layout = QVBoxLayout()
        self._ro_layout.setContentsMargins(8, 8, 8, 4)
        self._ro_layout.addWidget(self._title_label)
        self._ro_layout.addWidget(self._desc_label)
        self._ro_layout.addWidget(self._tags_label)
        self._ro_layout.addWidget(self._objects_label)

        self._edit_form = QFormLayout()
        self._edit_form.setContentsMargins(8, 8, 8, 4)
        self._edit_form.addRow("Title:", self._title_edit)
        self._edit_form.addRow("Description:", self._desc_edit)
        self._edit_form.addRow("Tags:", self._tags_edit)

        self._ro_widget = QWidget()
        self._ro_widget.setLayout(self._ro_layout)

        self._edit_widget = QWidget()
        self._edit_widget.setLayout(self._edit_form)
        self._edit_widget.setVisible(False)

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
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
        self._tags_label.setText("  ".join(f"#{t}" for t in tags))

        parts = []
        if tables:
            parts.append("Tables: " + ", ".join(sorted(tables)))
        if columns:
            parts.append("Columns: " + ", ".join(sorted(columns)))
        self._objects_label.setText("  |  ".join(parts) if parts else "")

        self._title_edit.setText(title)
        self._desc_edit.setPlainText(description)
        self._tags_edit.setText(", ".join(tags))

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
        raw_tags = self._tags_edit.text()
        tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]
        return title, description, tags

    def clear(self) -> None:
        self._title_label.setText("")
        self._desc_label.setText("")
        self._tags_label.setText("")
        self._objects_label.setText("")
        self._title_edit.clear()
        self._desc_edit.clear()
        self._tags_edit.clear()
