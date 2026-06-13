from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.frontmatter import write_sql_file
from ..core.i18n import tr


def _safe_filename(title: str) -> str:
    """Convert a title to a safe filename stem."""
    name = title.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "_", name)
    return name or "query"


class NewQueryDialog(QDialog):
    """Dialog for creating a new .sql file with frontmatter.

    Usage::
        dlg = NewQueryDialog(project_root, subfolders, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            path = dlg.created_path
    """

    def __init__(
        self,
        project_root: Path,
        subfolders: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("dialog.new_query.title"))
        self.resize(600, 450)
        self._project_root = project_root
        self.created_path: Path | None = None

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText(tr("dialog.new_query.title_placeholder"))

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText(tr("dialog.new_query.tags_placeholder"))

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText(tr("dialog.new_query.desc_placeholder"))

        self._folder_combo = QComboBox()
        self._folder_combo.addItem(tr("dialog.new_query.project_root"), userData="__root__")
        for sf in sorted(subfolders):
            self._folder_combo.addItem(sf)

        self._body_edit = QPlainTextEdit()
        self._body_edit.setPlaceholderText(tr("dialog.new_query.sql_placeholder"))

        form = QFormLayout()
        form.addRow(tr("dialog.new_query.label_title"), self._title_edit)
        form.addRow(tr("dialog.new_query.label_tags"), self._tags_edit)
        form.addRow(tr("dialog.new_query.label_desc"), self._desc_edit)
        form.addRow(tr("dialog.new_query.label_folder"), self._folder_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr("dialog.new_query.label_body")))
        layout.addWidget(self._body_edit)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        title = self._title_edit.text().strip()
        if not title:
            QMessageBox.warning(
                self,
                tr("dialog.new_query.validation_title"),
                tr("dialog.new_query.title_required"),
            )
            return

        if self._folder_combo.currentData() == "__root__":
            target_dir = self._project_root
        else:
            target_dir = self._project_root / self._folder_combo.currentText()
            target_dir.mkdir(parents=True, exist_ok=True)

        filename = _safe_filename(title) + ".sql"
        path = target_dir / filename
        counter = 1
        while path.exists():
            path = target_dir / f"{_safe_filename(title)}_{counter}.sql"
            counter += 1

        raw_tags = self._tags_edit.text()
        tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]

        metadata = {
            "title": title,
            "tags": tags,
            "description": self._desc_edit.text().strip(),
        }
        if not metadata["description"]:
            del metadata["description"]

        body = self._body_edit.toPlainText().strip()
        write_sql_file(path, metadata, body)
        self.created_path = path
        self.accept()
