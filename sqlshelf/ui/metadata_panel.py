"""Details panel: the selected query's header, actions and metadata.

Read mode (docs/redesign-ui.md, F5): a file icon, the title and file name
with a star; Copy SQL, Edit and Open in SSMS plus a ⋯ menu; then the
description and a label | value grid with tags, tables, location and the
modification date. Edit mode (F6): an "Editing" banner with Cancel and Save,
then title, description, tags, author and the read-only creation date.
Colours come from the global stylesheet through object names, so
``refresh_theme`` only re-requests the icons.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import shiboken6
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import (
    QEnterEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from . import icons
from . import tokens as _tk
from .tag_field import TagField
from .tag_widget import FlowLayout
from .theme import mono_font, qcolor
from .widgets import KbdHint

PANEL_BUTTON_HEIGHT = 32
CHIP_HEIGHT = 22
_CHIP_GAP = 6
_LABEL_COLUMN = 88
_TITLE_LINES = 2
_DESCRIPTION_LINES = 6
_EDIT_DESCRIPTION_LINES = 3


@dataclass(frozen=True)
class QueryEdits:
    """What the edit form holds, trimmed."""

    title: str
    description: str
    tags: list[str]
    author: str


def _opens_in_ssms() -> bool:
    """SSMS only exists on Windows; elsewhere the file opens in its default app."""
    return sys.platform == "win32"


def _wrapped_lines(paragraph: str, font: QFont, width: int) -> list[str]:
    layout = QTextLayout(paragraph, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    lines: list[str] = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        start = line.textStart()
        lines.append(paragraph[start : start + line.textLength()].rstrip())
    layout.endLayout()
    return lines or [""]


def elide_lines(text: str, font: QFont, width: int, max_lines: int) -> str:
    """*text* as it wraps at *width*, cut to *max_lines* lines with an ellipsis.

    Lines of the result are joined with "\\n" and each one fits *width*, so a
    word-wrapping QLabel shows them unchanged.
    """
    if width <= 0 or max_lines <= 0 or not text:
        return text
    lines = [
        line
        for paragraph in text.split("\n")
        for line in _wrapped_lines(paragraph, font, width)
    ]
    if len(lines) <= max_lines:
        return text
    rest = " ".join(line.strip() for line in lines[max_lines - 1 :] if line.strip())
    last = QFontMetrics(font).elidedText(rest, Qt.TextElideMode.ElideRight, width)
    return "\n".join([*lines[: max_lines - 1], last])


def modified_text(mtime: float | None, author: str) -> str:
    """ "dd/mm/yyyy HH:MM · author", leaving out whichever part is unknown."""
    parts: list[str] = []
    if mtime:
        try:
            parts.append(datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M"))
        except (OverflowError, OSError, ValueError):
            pass
    if author:
        parts.append(author)
    return " · ".join(parts)


def created_text(value: Any) -> str:
    """The frontmatter's ``created`` as "dd/mm/yyyy"; "—" when there is none.

    YAML reads a bare date as a ``date``; a quoted one stays a string, shown
    as a date when it starts with one and as written otherwise.
    """
    if isinstance(value, (date, datetime)):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip() if value is not None else ""
    if not text:
        return "—"
    try:
        return date.fromisoformat(text[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return text


def _repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class _ElidedLabel(QLabel):
    """Label cut to *max_lines* lines with an ellipsis; the tooltip has it all."""

    def __init__(self, object_name: str, max_lines: int) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self._full = ""
        self._max_lines = max_lines
        self.setWordWrap(max_lines > 1)
        # The panel decides the width; a long title must not widen it.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(1)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        self.ensurePolished()
        # QLabel lays text out a pixel or two narrower than its contents rect.
        width = self.contentsRect().width() - 2
        if self._max_lines == 1:
            shown = self.fontMetrics().elidedText(
                self._full.replace("\n", " "), Qt.TextElideMode.ElideRight, width
            )
        else:
            shown = elide_lines(self._full, self.font(), width, self._max_lines)
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class _PathButton(QAbstractButton):
    """A path in the accent colour, elided in the middle, underlined on hover."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PanelPath")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(mono_font(_tk.FONT_PX_SMALL))
        self.setFixedHeight(CHIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()), CHIP_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, CHIP_HEIGHT)

    def shown_text(self) -> str:
        return self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, self.width()
        )

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: object) -> None:
        super().leaveEvent(event)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        font = QFont(self.font())
        font.setUnderline(self.underMouse() or self.hasFocus())
        painter.setFont(font)
        painter.setPen(qcolor(_tk.color("accent_text")))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.shown_text(),
        )
        painter.end()


class _ChipArea(QScrollArea):
    """Chips that wrap onto new rows and scroll after *max_rows* rows."""

    def __init__(self, max_rows: int) -> None:
        super().__init__()
        self.setObjectName("ChipArea")
        self._content = QWidget()
        self._content.setObjectName("ChipAreaContent")
        self._flow = FlowLayout(self._content, h_gap=_CHIP_GAP, v_gap=_CHIP_GAP)
        # The style's default layout margins would make every row ~40 px tall.
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._max_height = max_rows * CHIP_HEIGHT + (max_rows - 1) * _CHIP_GAP
        self._retired: list[QWidget] = []
        self.setWidget(self._content)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self._content.setAutoFillBackground(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def chips(self) -> list[QPushButton]:
        found: list[QPushButton] = []
        for index in range(self._flow.count()):
            item = self._flow.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QPushButton):
                found.append(widget)
        return found

    def set_chips(self, chips: list[QPushButton]) -> None:
        # A chip may be replaced from its own click handler, so it is deleted
        # later. Detached and held here until then: a deferred delete still
        # parented to the area aborts the process if the panel goes first.
        self._retired = [w for w in self._retired if shiboken6.isValid(w)]
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                self._retired.append(widget)
        for chip in chips:
            self._flow.addWidget(chip)
        self._content.updateGeometry()
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return min(self._flow.heightForWidth(width), self._max_height)

    def sizeHint(self) -> QSize:
        width = max(self.width(), 200)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, CHIP_HEIGHT)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()


class _TagPill(QPushButton):
    """A tag with its colour dot; clicking it shows the queries with the tag."""

    def __init__(self, tag: str) -> None:
        super().__init__(tag)
        self.tag = tag
        self.setObjectName("TagPill")
        self.setFixedHeight(CHIP_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(8, 8))
        self.setIcon(icons.dot(_tk.tag_color(tag)))
        self.setToolTip(tr("details.tag_tooltip", tag=tag))


class _TableChip(QPushButton):
    """A table name in mono with a table icon."""

    def __init__(self, table: str) -> None:
        super().__init__(table)
        self.table = table
        self.setObjectName("TableChip")
        self.setFixedHeight(CHIP_HEIGHT)
        self.setFont(mono_font(_tk.FONT_PX_SMALL))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(14, 14))
        self.setIcon(icons.icon("table", "text3", 14))
        self.setToolTip(tr("metadata.chip_tooltip_navigate"))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)


def _panel_button(*, primary: bool = False) -> QPushButton:
    button = QPushButton()
    button.setObjectName("PanelButton")
    button.setProperty("primary", primary)
    button.setFixedHeight(PANEL_BUTTON_HEIGHT)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIconSize(QSize(16, 16))
    return button


def _field_label() -> QLabel:
    label = QLabel()
    label.setObjectName("PanelFieldLabel")
    label.setMinimumHeight(CHIP_HEIGHT)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return label


class _SaveButton(QPushButton):
    """The primary "Save" button with its ``Ctrl S`` hint inside.

    The label and hint are child labels, like the bar buttons', so each can
    take its own colour from the stylesheet.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PanelButton")
        self.setProperty("primary", True)
        self.setFixedHeight(PANEL_BUTTON_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(8)
        # A QPushButton's size hint ignores its layout; without this the
        # label would be squeezed instead of widening the button.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._text = QLabel()
        self._text.setObjectName("PanelButtonText")
        self._kbd = KbdHint("Ctrl S")
        for part in (self._text, self._kbd):
            part.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            # Centred at its own height, the hint stays a key inside the button.
            layout.addWidget(part, 0, Qt.AlignmentFlag.AlignVCenter)

    def label_text(self) -> str:
        return self._text.text()

    def set_label(self, text: str) -> None:
        self._text.setText(text)
        self.setAccessibleName(text)


def _edit_label(buddy: QWidget) -> QLabel:
    """A form field's caption, shown above the field it names."""
    label = QLabel()
    label.setObjectName("PanelFieldLabel")
    label.setBuddy(buddy)
    return label


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------


class MetadataPanel(QWidget):
    """The selected query: header, actions and metadata, or the edit form.

    Signals:
        navigate_requested(table)      — a table chip was clicked.
        filter_requested(kind, value)  — "Search in all queries" on a chip.
        tag_requested(tag)             — a tag pill was clicked.
        favorite_toggled               — the star was clicked.
        copy_requested / edit_requested / open_requested — the action row.
        select_all_requested           — ⋯ › Select all.
        reveal_requested               — the location or ⋯ › Reveal.
        path_copied(path)              — a path went to the clipboard.
        save_requested / cancel_requested — the edit form's buttons.
    """

    navigate_requested = Signal(str)
    filter_requested = Signal(str, str)
    tag_requested = Signal(str)
    favorite_toggled = Signal()
    copy_requested = Signal()
    edit_requested = Signal()
    open_requested = Signal()
    select_all_requested = Signal()
    reveal_requested = Signal()
    path_copied = Signal(str)
    save_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetadataPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._edit_mode = False
        self._is_favorite = False
        self._file_path: Path | None = None
        self._mtime: float | None = None
        self._author = ""
        self._description = ""
        self._tags: list[str] = []
        self._tables: list[str] = []

        self._ro_widget = QWidget()
        ro_layout = QVBoxLayout(self._ro_widget)
        ro_layout.setContentsMargins(0, 0, 0, 0)
        ro_layout.setSpacing(0)
        ro_layout.addWidget(self._build_header())
        divider = QFrame()
        divider.setObjectName("PanelDivider")
        divider.setFixedHeight(1)
        ro_layout.addWidget(divider)
        ro_layout.addWidget(self._build_body())

        self._edit_widget = self._build_edit_form()
        self._edit_widget.setVisible(False)

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._ro_widget)
        main.addWidget(self._edit_widget)

        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Maximum)
        self.setSizePolicy(policy)

        self.retranslate_ui()
        self.refresh_theme()
        self.clear()

    # ── Construction ───────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)

        self._file_icon = QLabel()
        self._file_icon.setObjectName("PanelFileIcon")
        self._file_icon.setFixedSize(40, 40)
        self._file_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title_label = _ElidedLabel("PanelTitle", _TITLE_LINES)
        self._file_name_label = _ElidedLabel("PanelFileName", 1)
        self._file_name_label.setFont(mono_font(_tk.FONT_PX_SMALL))

        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(4)
        names.addWidget(self._title_label)
        names.addWidget(self._file_name_label)

        self._star_btn = QToolButton()
        self._star_btn.setObjectName("PanelStar")
        self._star_btn.setFixedSize(28, 28)
        self._star_btn.setIconSize(QSize(18, 18))
        self._star_btn.setAutoRaise(True)
        self._star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._star_btn.clicked.connect(self.favorite_toggled)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        top.addWidget(self._file_icon, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(names, 1)
        top.addWidget(self._star_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self._copy_btn = _panel_button(primary=True)
        self._copy_btn.clicked.connect(self.copy_requested)
        self._edit_btn = _panel_button()
        self._edit_btn.clicked.connect(self.edit_requested)
        self._open_btn = _panel_button()
        self._open_btn.clicked.connect(self.open_requested)
        self._more_btn = _panel_button()
        self._more_btn.setFixedWidth(PANEL_BUTTON_HEIGHT)
        self._more_btn.clicked.connect(self._show_more_menu)

        self._more_menu = QMenu(self)
        self._select_all_act = self._more_menu.addAction("")
        # Shown as a hint only: Ctrl+A belongs to the editor.
        self._select_all_act.setShortcut("Ctrl+A")
        self._select_all_act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self._select_all_act.triggered.connect(self.select_all_requested)
        self._more_menu.addSeparator()
        self._copy_path_act = self._more_menu.addAction("")
        self._copy_path_act.triggered.connect(self._copy_file_path)
        self._copy_folder_act = self._more_menu.addAction("")
        self._copy_folder_act.triggered.connect(self._copy_folder_path)
        self._more_menu.addSeparator()
        self._reveal_act = self._more_menu.addAction("")
        self._reveal_act.triggered.connect(self.reveal_requested)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        for button in (self._copy_btn, self._edit_btn, self._open_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self._more_btn)
        layout.addLayout(actions)
        return header

    def _build_body(self) -> QWidget:
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        self._desc_label = _ElidedLabel("PanelDescription", _DESCRIPTION_LINES)
        layout.addWidget(self._desc_label)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(8)
        self._grid.setColumnMinimumWidth(0, _LABEL_COLUMN)
        self._grid.setColumnStretch(1, 1)

        self._tags_area = _ChipArea(max_rows=3)
        self._tables_area = _ChipArea(max_rows=3)

        self._path_btn = _PathButton()
        self._path_btn.clicked.connect(self.reveal_requested)
        self._path_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._path_btn.customContextMenuRequested.connect(self._show_path_menu)

        self._modified_label = QLabel()
        self._modified_label.setObjectName("PanelFieldValue")
        self._modified_label.setMinimumHeight(CHIP_HEIGHT)

        self._rows: dict[str, tuple[QLabel, QWidget]] = {}
        for row, (key, value) in enumerate(
            [
                ("tags", self._tags_area),
                ("tables", self._tables_area),
                ("location", self._path_btn),
                ("modified", self._modified_label),
            ]
        ):
            label = _field_label()
            self._grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            self._grid.addWidget(value, row, 1)
            self._rows[key] = (label, value)
        layout.addLayout(self._grid)
        return body

    def _build_edit_form(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_edit_banner())

        self._title_edit = QLineEdit()
        self._title_edit.setObjectName("EditTitle")
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setObjectName("EditDescription")
        # Tab moves on to the tags; a description is prose, not code.
        self._desc_edit.setTabChangesFocus(True)
        self._tag_field = TagField()
        self._author_edit = QLineEdit()
        self._author_edit.setObjectName("EditAuthor")
        self._created_edit = QLineEdit()
        self._created_edit.setObjectName("EditCreated")
        self._created_edit.setReadOnly(True)
        self._created_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._edit_labels: dict[str, QLabel] = {}

        def field(key: str, editor: QWidget) -> QVBoxLayout:
            label = _edit_label(editor)
            self._edit_labels[key] = label
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(6)
            column.addWidget(label)
            column.addWidget(editor)
            return column

        form = QVBoxLayout()
        form.setContentsMargins(20, 16, 20, 20)
        form.setSpacing(14)
        form.addLayout(field("title", self._title_edit))
        form.addLayout(field("description", self._desc_edit))
        form.addLayout(field("tags", self._tag_field))
        people = QHBoxLayout()
        people.setContentsMargins(0, 0, 0, 0)
        people.setSpacing(12)
        people.addLayout(field("author", self._author_edit), 1)
        people.addLayout(field("created", self._created_edit), 1)
        form.addLayout(people)
        layout.addLayout(form)
        return widget

    def _build_edit_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("EditBanner")
        banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(20, 12, 16, 12)
        layout.setSpacing(10)

        self._banner_dot = QLabel()
        self._banner_dot.setFixedSize(8, 8)
        self._banner_title = QLabel()
        self._banner_title.setObjectName("EditBannerTitle")
        self._banner_subtitle = _ElidedLabel("EditBannerSubtitle", 1)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        texts.addWidget(self._banner_title)
        texts.addWidget(self._banner_subtitle)

        self._cancel_btn = _panel_button()
        self._cancel_btn.clicked.connect(self.cancel_requested)
        self._save_btn = _SaveButton()
        self._save_btn.clicked.connect(self.save_requested)

        layout.addWidget(self._banner_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(texts, 1)
        layout.addWidget(self._cancel_btn)
        layout.addWidget(self._save_btn)
        return banner

    def _fit_description_height(self) -> None:
        """Three lines of text tall, whatever the font."""
        editor = self._desc_edit
        editor.ensurePolished()
        margin = editor.document().documentMargin()
        height = (
            editor.fontMetrics().lineSpacing() * _EDIT_DESCRIPTION_LINES
            + round(2 * margin)
            + 2 * editor.frameWidth()
            + 2
        )
        editor.setFixedHeight(height)

    # ── Menus and clipboard ────────────────────────────────────────────────

    def _show_more_menu(self) -> None:
        anchor = self._more_btn.mapToGlobal(QPoint(0, self._more_btn.height() + 4))
        # Right-align the menu with the button, which sits at the panel edge.
        anchor.setX(
            anchor.x() + self._more_btn.width() - self._more_menu.sizeHint().width()
        )
        self._more_menu.exec(anchor)

    def _copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.path_copied.emit(text)

    def _copy_file_path(self) -> None:
        if self._file_path is not None:
            self._copy_text(str(self._file_path))

    def _copy_folder_path(self) -> None:
        if self._file_path is not None:
            self._copy_text(str(self._file_path.parent))

    def _show_path_menu(self) -> None:
        if self._file_path is None:
            return
        menu = QMenu(self)
        copy_act = menu.addAction(tr("metadata.copy_path"))
        copy_dir_act = menu.addAction(tr("metadata.copy_folder_path"))
        menu.addSeparator()
        reveal_act = menu.addAction(tr("metadata.reveal_in_explorer"))
        chosen = menu.exec(
            self._path_btn.mapToGlobal(self._path_btn.rect().bottomLeft())
        )
        if chosen == copy_act:
            self._copy_file_path()
        elif chosen == copy_dir_act:
            self._copy_folder_path()
        elif chosen == reveal_act:
            self.reveal_requested.emit()

    def _show_chip_menu(self, chip: _TableChip) -> None:
        menu = QMenu(self)
        nav_act = menu.addAction(tr("metadata.chip_go_to_occurrence"))
        menu.addSeparator()
        search_act = menu.addAction(tr("metadata.chip_search_all"))
        copy_act = menu.addAction(tr("metadata.chip_copy_name"))
        chosen = menu.exec(chip.mapToGlobal(chip.rect().bottomLeft()))
        if chosen == nav_act:
            self.navigate_requested.emit(chip.table)
        elif chosen == search_act:
            self.filter_requested.emit("table", chip.table)
        elif chosen == copy_act:
            QGuiApplication.clipboard().setText(chip.table)

    # ── Rows ───────────────────────────────────────────────────────────────

    def _set_row_visible(self, key: str, visible: bool) -> None:
        label, value = self._rows[key]
        label.setVisible(visible)
        value.setVisible(visible)

    def _rebuild_tags(self) -> None:
        pills: list[QPushButton] = []
        for tag in self._tags:
            pill = _TagPill(tag)
            pill.clicked.connect(
                lambda _checked=False, t=tag: self.tag_requested.emit(t)
            )
            pills.append(pill)
        self._tags_area.set_chips(pills)
        self._set_row_visible("tags", bool(self._tags))

    def _rebuild_tables(self) -> None:
        chips: list[QPushButton] = []
        for table in sorted(self._tables, key=str.lower):
            chip = _TableChip(table)
            chip.clicked.connect(
                lambda _checked=False, t=table: self.navigate_requested.emit(t)
            )
            chip.customContextMenuRequested.connect(
                lambda _pos, c=chip: self._show_chip_menu(c)
            )
            chips.append(chip)
        self._tables_area.set_chips(chips)
        self._set_row_visible("tables", bool(self._tables))

    def _update_description(self) -> None:
        empty = not self._description.strip()
        self._desc_label.setProperty("empty", empty)
        _repolish(self._desc_label)
        self._desc_label.set_full_text(
            tr("details.no_description") if empty else self._description
        )

    def _update_modified(self) -> None:
        text = modified_text(self._mtime, self._author)
        self._modified_label.setText(text)
        self._set_row_visible("modified", bool(text))

    def _update_star(self) -> None:
        if self._is_favorite:
            self._star_btn.setIcon(icons.icon("star_filled", "star", 18))
            self._star_btn.setToolTip(tr("context_menu.remove_favorite"))
        else:
            self._star_btn.setIcon(icons.icon("star", "text3", 18))
            self._star_btn.setToolTip(tr("context_menu.add_favorite"))
        self._star_btn.setAccessibleName(self._star_btn.toolTip())

    # ── Public API ─────────────────────────────────────────────────────────

    def set_query(
        self,
        title: str,
        description: str,
        tags: list[str],
        tables: list[str],
    ) -> None:
        self._title_label.set_full_text(title)
        self._description = description
        self._update_description()
        if list(tags) != self._tags:
            self._tags = list(tags)
            self._rebuild_tags()
        if list(tables) != self._tables:
            self._tables = list(tables)
            self._rebuild_tables()

    def set_path(
        self, path: Path | None, mtime: float | None = None, author: str = ""
    ) -> None:
        """Show *path* (the folder as location) and when and by whom it changed.

        *mtime* comes from the index or a worker's stat: the panel never
        touches the disk itself.
        """
        self._file_path = path
        self._mtime = mtime
        self._author = author
        if path is None:
            self._file_name_label.set_full_text("")
            self._path_btn.setText("")
            self._path_btn.setToolTip("")
        else:
            self._file_name_label.set_full_text(path.name)
            self._path_btn.setText(str(path.parent))
            self._path_btn.setToolTip(tr("details.location_tooltip", path=str(path)))
            self._path_btn.updateGeometry()
        self._path_btn.update()
        self._set_row_visible("location", path is not None)
        for act in (self._copy_path_act, self._copy_folder_act, self._reveal_act):
            act.setEnabled(path is not None)
        self._update_modified()

    def set_favorite(self, is_fav: bool) -> None:
        self._is_favorite = is_fav
        self._update_star()

    def is_favorite(self) -> bool:
        return self._is_favorite

    def set_actions_enabled(self, enabled: bool) -> None:
        """Enable the actions once the query's SQL is in the editor."""
        for button in (self._copy_btn, self._edit_btn, self._open_btn):
            button.setEnabled(enabled)
        self._select_all_act.setEnabled(enabled)

    def set_known_tags(self, counts: list[tuple[str, int]]) -> None:
        """The library's ``(tag, queries using it)`` pairs, for the suggestions."""
        self._tag_field.set_known_tags(counts)

    def start_editing(
        self,
        title: str,
        description: str,
        tags: list[str],
        author: str = "",
        created: Any = None,
    ) -> None:
        """Fill the form with the query's metadata and show it."""
        self._title_edit.setText(title)
        self._desc_edit.setPlainText(description)
        self._tag_field.set_tags(tags)
        self._author_edit.setText(author)
        self._created_edit.setText(created_text(created))
        self.set_saving(False)
        self.set_edit_mode(True)

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self._ro_widget.setVisible(not enabled)
        self._edit_widget.setVisible(enabled)
        if enabled:
            self._fit_description_height()
            self._title_edit.setFocus()
            self._title_edit.selectAll()

    def is_editing(self) -> bool:
        return self._edit_mode

    def set_saving(self, saving: bool) -> None:
        """While the file is written the form's buttons wait."""
        self._save_btn.setEnabled(not saving)
        self._cancel_btn.setEnabled(not saving)

    def get_edited_values(self) -> QueryEdits:
        return QueryEdits(
            title=self._title_edit.text().strip(),
            description=self._desc_edit.toPlainText().strip(),
            tags=self._tag_field.tags(),
            author=self._author_edit.text().strip(),
        )

    def retranslate_ui(self) -> None:
        self._copy_btn.setText(tr("details.copy_sql"))
        self._edit_btn.setText(tr("details.edit"))
        self._edit_btn.setToolTip(tr("details.edit_tooltip"))
        open_key = "details.open_ssms" if _opens_in_ssms() else "details.open_external"
        self._open_btn.setText(tr(open_key))
        self._more_btn.setToolTip(tr("details.more"))
        self._more_btn.setAccessibleName(tr("details.more"))
        self._select_all_act.setText(tr("details.select_all"))
        self._copy_path_act.setText(tr("metadata.copy_path"))
        self._copy_folder_act.setText(tr("metadata.copy_folder_path"))
        self._reveal_act.setText(tr("metadata.reveal_in_explorer"))
        for key, (label, _value) in self._rows.items():
            label.setText(tr(f"details.label_{key}"))
        self._update_description()
        self._update_star()
        if self._file_path is not None:
            self._path_btn.setToolTip(
                tr("details.location_tooltip", path=str(self._file_path))
            )
        for pill in self._tags_area.chips():
            if isinstance(pill, _TagPill):
                pill.setToolTip(tr("details.tag_tooltip", tag=pill.tag))
        for chip in self._tables_area.chips():
            chip.setToolTip(tr("metadata.chip_tooltip_navigate"))

        self._banner_title.setText(tr("details.editing"))
        self._banner_subtitle.set_full_text(tr("details.editing_hint"))
        self._cancel_btn.setText(tr("common.cancel"))
        self._cancel_btn.setToolTip(tr("details.cancel_tooltip"))
        self._save_btn.set_label(tr("common.save"))
        self._save_btn.setToolTip(tr("details.save_tooltip"))
        for key, label in self._edit_labels.items():
            label.setText(tr(f"details.label_{key}"))
        self._desc_edit.setPlaceholderText(tr("details.description_placeholder"))
        self._author_edit.setPlaceholderText(tr("details.author_placeholder"))
        self._tag_field.retranslate_ui()

    def refresh_theme(self) -> None:
        self._file_icon.setPixmap(icons.pixmap("file", "accent_text", 20))
        self._copy_btn.setIcon(icons.icon("copy", "accent_ink"))
        self._edit_btn.setIcon(icons.icon("edit", "text2"))
        self._open_btn.setIcon(icons.icon("external", "text2"))
        self._more_btn.setIcon(icons.icon("more", "text2"))
        self._banner_dot.setPixmap(icons.dot(_tk.color("accent")))
        self._update_star()
        self._rebuild_tags()
        self._rebuild_tables()
        self._path_btn.update()
        self._tag_field.refresh_theme()

    def clear(self) -> None:
        self.set_query("", "", [], [])
        self.set_path(None)
        self.set_favorite(False)
        self.set_actions_enabled(False)
        self._title_edit.clear()
        self._desc_edit.clear()
        self._tag_field.set_tags([])
        self._author_edit.clear()
        self._created_edit.clear()
