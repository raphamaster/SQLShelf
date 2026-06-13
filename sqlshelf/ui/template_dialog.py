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
from ..core.snippets import apply_template, extract_params, list_templates


def _safe_filename(title: str) -> str:
    name = title.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "_", name)
    return name or "query"


class TemplateDialog(QDialog):
    """Picker for parametrizable SQL templates.

    Templates live in ~/.sqlshelf/templates/*.sql.
    Placeholders use {{param_name}} syntax.

    After accept(), ``created_path`` holds the new .sql file path.
    """

    def __init__(
        self,
        project_root: Path,
        subfolders: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New from Template")
        self.resize(640, 520)
        self._project_root = project_root
        self.created_path: Path | None = None

        templates = list_templates()
        if not templates:
            # Nothing to show — caller should guard against this
            pass

        self._templates = templates
        self._params_widgets: dict[str, QLineEdit] = {}

        self._template_combo = QComboBox()
        for t in templates:
            self._template_combo.addItem(t.stem, userData=t)
        self._template_combo.currentIndexChanged.connect(self._on_template_changed)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("New query title")

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("Tags (comma-separated)")

        self._folder_combo = QComboBox()
        self._folder_combo.addItem("(project root)")
        for sf in sorted(subfolders):
            self._folder_combo.addItem(sf)

        self._params_form = QFormLayout()

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setPlaceholderText("Template preview…")
        self._preview.setMaximumHeight(140)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Template:"))
        layout.addWidget(self._template_combo)
        layout.addWidget(QLabel("Query title:"))
        layout.addWidget(self._title_edit)

        form = QFormLayout()
        form.addRow("Tags:", self._tags_edit)
        form.addRow("Folder:", self._folder_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("Template parameters:"))
        layout.addLayout(self._params_form)
        layout.addWidget(QLabel("Preview:"))
        layout.addWidget(self._preview)
        layout.addWidget(buttons)

        if templates:
            self._on_template_changed(0)

    def _current_template_body(self) -> str:
        idx = self._template_combo.currentIndex()
        if idx < 0 or idx >= len(self._templates):
            return ""
        path: Path = self._template_combo.itemData(idx)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _on_template_changed(self, _idx: int) -> None:
        body = self._current_template_body()
        params = extract_params(body)

        # Rebuild params form
        while self._params_form.rowCount():
            self._params_form.removeRow(0)
        self._params_widgets.clear()

        for param in params:
            edit = QLineEdit()
            edit.setPlaceholderText(param)
            edit.textChanged.connect(self._update_preview)
            self._params_widgets[param] = edit
            self._params_form.addRow(f"{param}:", edit)

        self._update_preview()

    def _update_preview(self) -> None:
        body = self._current_template_body()
        vals = {k: w.text() for k, w in self._params_widgets.items()}
        self._preview.setPlainText(apply_template(body, vals))

    def _on_accept(self) -> None:
        title = self._title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "Validation", "Title is required.")
            return

        body = self._current_template_body()
        if not body:
            QMessageBox.warning(self, "Validation", "No template selected or template is empty.")
            return

        params = {k: w.text() for k, w in self._params_widgets.items()}
        filled_body = apply_template(body, params)

        folder_text = self._folder_combo.currentText()
        if folder_text == "(project root)":
            target_dir = self._project_root
        else:
            target_dir = self._project_root / folder_text
            target_dir.mkdir(parents=True, exist_ok=True)

        filename = _safe_filename(title) + ".sql"
        path = target_dir / filename
        counter = 1
        while path.exists():
            path = target_dir / f"{_safe_filename(title)}_{counter}.sql"
            counter += 1

        raw_tags = self._tags_edit.text()
        tags = [t.strip().lower() for t in raw_tags.split(",") if t.strip()]
        metadata: dict = {"title": title, "tags": tags}

        write_sql_file(path, metadata, filled_body)
        self.created_path = path
        self.accept()
