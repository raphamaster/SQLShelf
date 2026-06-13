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
from .theme import tokens as _tk


def _safe_filename(title: str) -> str:
    """Convert a title to a safe filename stem."""
    name = title.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "_", name)
    return name or "query"


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


def _build_folder_tree(
    parent_widget: QWidget,
    subfolders: list[str],
    root_label: str,
) -> QTreeWidget:
    """Build a QTreeWidget where root-level folders are always visible.

    Structure:
        (raiz do projeto)   <- top-level, selectable
        Backups             <- top-level, selectable, expandable
          Backup            <- child
          Restore           <- child
        CMS                 <- top-level, selectable, expandable
          CMSProperties     <- child
    """
    tree = QTreeWidget(parent_widget)
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(True)
    tree.setStyleSheet(_TREE_STYLE)

    # "(raiz do projeto)" is the first top-level item
    root_item = QTreeWidgetItem(tree, [root_label])
    root_item.setData(0, Qt.ItemDataRole.UserRole, "__root__")

    # node_map: relative-path key -> QTreeWidgetItem
    node_map: dict[str, QTreeWidgetItem] = {}

    for sf in sorted(subfolders):
        parts = Path(sf).parts
        for i, part in enumerate(parts):
            key = str(Path(*parts[: i + 1]))
            if key in node_map:
                continue
            if i == 0:
                # Top-level folder: direct child of the invisible root
                item = QTreeWidgetItem(tree, [part])
            else:
                parent_key = str(Path(*parts[:i]))
                item = QTreeWidgetItem(node_map[parent_key], [part])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            node_map[key] = item

    # Keep sub-folders collapsed; top-level items are always visible
    tree.collapseAll()
    tree.setCurrentItem(root_item)
    return tree


class _FolderPickerDialog(QDialog):
    """Modal tree picker for choosing a destination folder."""

    def __init__(
        self,
        subfolders: list[str],
        root_label: str,
        current_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("dialog.new_query.label_folder"))
        self.setMinimumWidth(380)
        self.resize(420, 420)

        self._tree = _build_folder_tree(self, subfolders, root_label)
        self._tree.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._restore_selection(current_key)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addWidget(buttons)

    def _restore_selection(self, key: str) -> None:
        """Re-select the previously chosen folder and expand its ancestors."""
        if key == "__root__":
            # First top-level item is the root label
            root = self._tree.topLevelItem(0)
            if root:
                self._tree.setCurrentItem(root)
            return
        for item in self._tree.findItems(
            "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive
        ):
            if item.data(0, Qt.ItemDataRole.UserRole) == key:
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self._tree.setCurrentItem(item)
                self._tree.scrollToItem(item)
                return

    def selected_folder(self) -> str:
        item = self._tree.currentItem()
        if item is None:
            return "__root__"
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data if data else "__root__"


class _FolderSelector(QWidget):
    """Read-only path label + browse button for forms."""

    def __init__(
        self,
        subfolders: list[str],
        root_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._subfolders = subfolders
        self._root_label = root_label
        self._current_key = "__root__"

        self._label = QLineEdit(root_label)
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
            self._subfolders,
            self._root_label,
            self._current_key,
            self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            key = dlg.selected_folder()
            self._current_key = key
            self._label.setText(self._root_label if key == "__root__" else key)

    def selected_folder(self) -> str:
        return self._current_key


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

        self._folder_selector = _FolderSelector(
            subfolders,
            tr("dialog.new_query.project_root"),
        )

        self._body_edit = QPlainTextEdit()
        self._body_edit.setPlaceholderText(tr("dialog.new_query.sql_placeholder"))

        form = QFormLayout()
        form.addRow(tr("dialog.new_query.label_title"), self._title_edit)
        form.addRow(tr("dialog.new_query.label_tags"), self._tags_edit)
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

        folder_key = self._folder_selector.selected_folder()
        if folder_key == "__root__":
            target_dir = self._project_root
        else:
            target_dir = self._project_root / folder_key
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
