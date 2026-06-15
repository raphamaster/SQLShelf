from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.frontmatter import write_sql_file
from ..core.i18n import tr
from .tag_widget import TagInputWidget
from .theme import tokens as _tk


def _safe_filename(title: str) -> str:
    """Convert a title to a safe filename stem."""
    name = title.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "_", name)
    return name or "query"


def _get_subfolders(root: Path) -> list[str]:
    """Return relative subfolder paths, excluding hidden directories."""
    try:
        return [
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_dir()
            and not any(
                part.startswith(".") for part in p.relative_to(root).parts
            )
        ]
    except OSError:
        return []


_TREE_STYLE = f"""
    QTreeWidget {{
        background-color: {_tk.SURFACE};
        border: 1px solid {_tk.BORDER};
        color: {_tk.TEXT_PRIMARY};
        outline: none;
    }}
    QTreeWidget::item {{
        padding: 4px 4px;
        color: {_tk.TEXT_PRIMARY};
    }}
    QTreeWidget::item:hover {{
        background-color: {_tk.HOVER_BG_STRONG};
    }}
    QTreeWidget::item:selected,
    QTreeWidget::item:selected:active {{
        background-color: {_tk.BORDER_EMPH};
        color: {_tk.ACCENT};
    }}
    QTreeWidget::branch {{
        background-color: {_tk.SURFACE};
    }}
    QTreeWidget::branch:selected {{
        background-color: {_tk.BORDER_EMPH};
    }}
"""


def _build_project_tree(
    parent_widget: QWidget,
    all_projects: list[Path],
    default_project: Path,
) -> QTreeWidget:
    """Build a tree with known project folders as root nodes.

    Each project root is a top-level item (always visible).
    Its subfolders are children (collapsed by default).
    UserRole stores the absolute Path of each node.
    """
    tree = QTreeWidget(parent_widget)
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(True)
    tree.setStyleSheet(_TREE_STYLE)

    default_item: QTreeWidgetItem | None = None

    for project in all_projects:
        proj_item = QTreeWidgetItem(tree, [project.name])
        proj_item.setData(0, Qt.ItemDataRole.UserRole, project)

        if project.resolve() == default_project.resolve():
            default_item = proj_item

        subfolders = _get_subfolders(project)
        node_map: dict[str, QTreeWidgetItem] = {}

        for sf in sorted(subfolders):
            parts = Path(sf).parts
            for i, part in enumerate(parts):
                key = str(Path(*parts[: i + 1]))
                if key in node_map:
                    continue
                parent_item = node_map[str(Path(*parts[:i]))] if i > 0 else proj_item
                item = QTreeWidgetItem(parent_item, [part])
                item.setData(0, Qt.ItemDataRole.UserRole, project / key)
                node_map[key] = item

    tree.collapseAll()

    if default_item is not None:
        tree.setCurrentItem(default_item)
        tree.scrollToItem(default_item)

    return tree


class _FolderPickerDialog(QDialog):
    """Modal tree picker for choosing a destination folder."""

    def __init__(
        self,
        all_projects: list[Path],
        default_project: Path,
        current_path: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("dialog.new_query.label_folder"))
        self.setMinimumWidth(400)
        self.resize(440, 440)

        self._tree = _build_project_tree(self, all_projects, default_project)
        self._tree.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        if current_path is not None:
            self._restore_selection(current_path)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addWidget(buttons)

    def _restore_selection(self, path: Path) -> None:
        for item in self._tree.findItems(
            "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive
        ):
            if item.data(0, Qt.ItemDataRole.UserRole) == path:
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self._tree.setCurrentItem(item)
                self._tree.scrollToItem(item)
                return

    def selected_path(self) -> Path | None:
        """Return the absolute Path of the selected folder, or None."""
        item = self._tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)


class _FolderSelector(QWidget):
    """Read-only path label + browse button for forms."""

    def __init__(
        self,
        all_projects: list[Path],
        default_project: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._all_projects = all_projects
        self._default_project = default_project
        self._current_path: Path = default_project

        self._label = QLineEdit(default_project.name)
        self._label.setReadOnly(True)
        self._label.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_tk.SURFACE};
                border: 1px solid {_tk.BORDER};
                border-radius: {_tk.RADIUS}px;
                color: {_tk.TEXT_PRIMARY};
                padding: 4px 8px;
            }}
        """)

        self._btn = QPushButton("...")
        self._btn.setFixedWidth(32)
        self._btn.setToolTip(tr("dialog.new_query.label_folder"))
        self._btn.clicked.connect(self._open_picker)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._label)
        row.addWidget(self._btn)

    def _open_picker(self) -> None:
        dlg = _FolderPickerDialog(
            self._all_projects,
            self._default_project,
            self._current_path,
            self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            path = dlg.selected_path()
            if path is not None:
                self._current_path = path
                # Show "ProjectName" or "ProjectName/sub/folder"
                try:
                    # Find which project this path belongs to
                    for proj in self._all_projects:
                        if path.resolve() == proj.resolve():
                            self._label.setText(proj.name)
                            return
                    for proj in self._all_projects:
                        rel = path.relative_to(proj)
                        self._label.setText(f"{proj.name}/{rel}")
                        return
                except ValueError:
                    self._label.setText(str(path))

    def selected_path(self) -> Path:
        return self._current_path


class NewQueryDialog(QDialog):
    """Dialog for creating a new .sql file with frontmatter.

    Usage::
        dlg = NewQueryDialog(current_project, all_projects, parent, available_tags)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            path = dlg.created_path
    """

    def __init__(
        self,
        current_project: Path,
        all_projects: list[Path],
        parent: QWidget | None = None,
        available_tags: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("dialog.new_query.title"))
        self.resize(600, 450)
        self.created_path: Path | None = None

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText(tr("dialog.new_query.title_placeholder"))

        self._tags_input = TagInputWidget()
        if available_tags:
            self._tags_input.set_available_tags(available_tags)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText(tr("dialog.new_query.desc_placeholder"))

        self._folder_selector = _FolderSelector(
            all_projects,
            current_project,
        )

        self._body_edit = QPlainTextEdit()
        self._body_edit.setPlaceholderText(tr("dialog.new_query.sql_placeholder"))

        form = QFormLayout()
        form.addRow(tr("dialog.new_query.label_title"), self._title_edit)
        form.addRow(tr("dialog.new_query.label_tags"), self._tags_input)
        form.addRow(tr("dialog.new_query.label_desc"), self._desc_edit)
        form.addRow(tr("dialog.new_query.label_folder"), self._folder_selector)

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

        target_dir = self._folder_selector.selected_path()
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = _safe_filename(title) + ".sql"
        path = target_dir / filename
        counter = 1
        while path.exists():
            path = target_dir / f"{_safe_filename(title)}_{counter}.sql"
            counter += 1

        tags = self._tags_input.get_tags()

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
