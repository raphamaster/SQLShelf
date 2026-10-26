"""Qt side of the theme: Fusion style, QPalette, fonts and the global QSS.

Everything here is generated from the design tokens in ``tokens.py``;
``apply_theme`` is the single entry point, at startup and on a live switch.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from . import tokens as _tk

_RGBA_RE = re.compile(
    r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+)\s*\)"
)


def _rgba(value: str) -> tuple[int, int, int, int] | None:
    m = _RGBA_RE.fullmatch(value.strip())
    if m is None:
        return None
    alpha = round(float(m.group(4)) * 255)
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), alpha


def qss_color(value: str) -> str:
    """Return token *value* as QSS expects it: rgba() alpha in 0–255, not 0–1."""
    rgba = _rgba(value)
    if rgba is None:
        return value
    r, g, b, a = rgba
    return f"rgba({r}, {g}, {b}, {a})"


def qcolor(value: str) -> QColor:
    """Build a QColor from token *value*; QColor cannot parse CSS rgba()."""
    rgba = _rgba(value)
    if rgba is None:
        return QColor(value)
    r, g, b, a = rgba
    return QColor(r, g, b, a)


def qss(key: str) -> str:
    """Active token *key*, ready to interpolate into a widget's stylesheet."""
    return qss_color(_tk.color(key))


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def ui_font() -> QFont:
    """Application font: Segoe UI Variable or Segoe UI on Windows, else the system font."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if sys.platform == "win32":
        installed = set(QFontDatabase.families())
        for family in _tk.UI_FONT_FAMILIES_WINDOWS:
            if family in installed:
                font.setFamily(family)
                break
    font.setPixelSize(_tk.FONT_PX_BASE)
    return font


def mono_font(pixel_size: int = _tk.FONT_PX_CODE) -> QFont:
    """Code font: Cascadia Mono, then Consolas, then the system fixed-width font."""
    installed = set(QFontDatabase.families())
    for family in _tk.MONO_FONT_FAMILIES:
        if family in installed:
            font = QFont(family)
            break
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    return font


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

_ROLE = QPalette.ColorRole

_PALETTE_ROLES: dict[QPalette.ColorRole, str] = {
    _ROLE.Window: "chrome",
    _ROLE.WindowText: "text",
    _ROLE.Base: "bg",
    _ROLE.AlternateBase: "panel",
    _ROLE.Text: "text",
    _ROLE.PlaceholderText: "text3",
    _ROLE.Button: "field",
    _ROLE.ButtonText: "text",
    _ROLE.BrightText: "text",
    _ROLE.Light: "border_strong",
    _ROLE.Midlight: "border",
    _ROLE.Mid: "border_strong",
    _ROLE.Dark: "border",
    _ROLE.Shadow: "chrome",
    _ROLE.Highlight: "sel",
    _ROLE.HighlightedText: "text",
    _ROLE.Link: "accent_text",
    _ROLE.LinkVisited: "accent_text",
    _ROLE.ToolTipBase: "panel",
    _ROLE.ToolTipText: "text",
    _ROLE.Accent: "accent",
}


def build_palette(palette: Mapping[str, str]) -> QPalette:
    """QPalette for Fusion, so unstyled widgets already match the tokens."""
    pal = QPalette()
    for role, key in _PALETTE_ROLES.items():
        pal.setColor(role, qcolor(palette[key]))
    disabled = QPalette.ColorGroup.Disabled
    for role in (_ROLE.WindowText, _ROLE.Text, _ROLE.ButtonText):
        pal.setColor(disabled, role, qcolor(palette["text3"]))
    return pal


# ---------------------------------------------------------------------------
# Global QSS
# ---------------------------------------------------------------------------


def app_stylesheet(palette: Mapping[str, str]) -> str:
    """Global QSS for *palette*, scoped by widget class and objectName.

    Visual rules from docs/redesign-ui.md: no upper-case text, secondary buttons
    are outline (``border_strong``) or ghost, green only for selection, focus and
    the primary action (``primary="true"``), and focus is an ``accent`` border
    because QSS has no box-shadow.
    """
    c = {key: qss_color(value) for key, value in palette.items()}
    r, r_sm = _tk.RADIUS, _tk.RADIUS_SM
    return f"""
        QMainWindow, QSplitter, QWidget#SidebarWidget {{
            background-color: {c["chrome"]};
        }}
        QDialog {{
            background-color: {c["panel"]};
        }}
        QSplitter::handle {{
            background-color: {c["border"]};
            width: 1px;
            height: 1px;
        }}
        QToolTip {{
            background-color: {c["panel"]};
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r_sm}px;
            padding: 4px 6px;
        }}

        QPushButton {{
            background-color: transparent;
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
            padding: 5px 14px;
        }}
        QPushButton:hover {{
            background-color: {c["hover"]};
        }}
        QPushButton:pressed, QPushButton:checked {{
            background-color: {c["sel_soft"]};
        }}
        QPushButton:disabled {{
            color: {c["text3"]};
            border-color: {c["border"]};
        }}
        QPushButton[primary="true"] {{
            background-color: {c["accent"]};
            color: {c["accent_ink"]};
            border-color: {c["accent"]};
            font-weight: 600;
        }}
        QToolButton {{
            background-color: transparent;
            color: {c["text"]};
            border: none;
            border-radius: {r}px;
        }}
        QToolButton:hover {{
            background-color: {c["hover"]};
        }}
        QToolButton:pressed, QToolButton:checked {{
            background-color: {c["sel_soft"]};
        }}

        QLineEdit, QAbstractSpinBox {{
            background-color: {c["field"]};
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
            padding: 5px 8px;
            selection-background-color: {c["mark"]};
            selection-color: {c["text"]};
        }}
        QPlainTextEdit, QTextEdit {{
            background-color: {c["field"]};
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
            selection-background-color: {c["mark"]};
            selection-color: {c["text"]};
        }}
        QLineEdit:focus, QAbstractSpinBox:focus,
        QPlainTextEdit:focus, QTextEdit:focus {{
            border-color: {c["accent"]};
        }}
        QLineEdit:disabled, QAbstractSpinBox:disabled {{
            color: {c["text3"]};
        }}
        CodeEditor {{
            background-color: {c["code"]};
            border: none;
            border-radius: 0px;
        }}

        QComboBox {{
            background-color: {c["field"]};
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
            padding: 4px 8px;
            min-height: 22px;
        }}
        QComboBox:focus, QComboBox:on {{
            border-color: {c["accent"]};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 22px;
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background-color: {c["panel"]};
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            selection-background-color: {c["sel"]};
            selection-color: {c["text"]};
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            min-height: 24px;
            padding: 4px 8px;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background-color: {c["sel_soft"]};
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: {c["sel"]};
        }}

        QMenuBar {{
            background-color: {c["chrome"]};
            color: {c["text"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 4px 8px;
            border-radius: {r_sm}px;
        }}
        QMenuBar::item:selected {{
            background-color: {c["hover"]};
        }}
        QMenuBar::item:pressed {{
            background-color: {c["sel_soft"]};
        }}
        QMenu {{
            background-color: {c["panel"]};
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 24px 6px 28px;
            border-radius: {r_sm}px;
        }}
        QMenu::item:selected {{
            background-color: {c["sel_soft"]};
        }}
        QMenu::item:disabled {{
            color: {c["text3"]};
        }}
        QMenu::indicator {{
            left: 8px;
            width: 14px;
            height: 14px;
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {c["border"]};
            margin: 4px 8px;
        }}

        QListWidget {{
            background-color: {c["bg"]};
            border: 1px solid {c["border"]};
            outline: none;
        }}
        QListWidget::item {{
            color: {c["text"]};
            padding: 6px 8px;
            border-radius: {r}px;
        }}
        QListWidget::item:hover {{
            background-color: {c["hover"]};
        }}
        QListWidget::item:selected {{
            background-color: {c["sel"]};
            color: {c["text"]};
        }}
        QHeaderView::section {{
            background-color: {c["bg"]};
            color: {c["text3"]};
            border: none;
            border-bottom: 1px solid {c["border"]};
            padding: 4px 8px;
        }}
        QTableView, QTreeView {{
            gridline-color: {c["border"]};
        }}

        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0px;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 0px;
        }}
        QScrollBar::handle {{
            background-color: {c["border_strong"]};
            border-radius: {r_sm}px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            min-height: 24px;
        }}
        QScrollBar::handle:horizontal {{
            min-width: 24px;
        }}
        QScrollBar::handle:hover {{
            background-color: {c["text3"]};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0px;
            height: 0px;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
        }}

        QProgressBar {{
            background-color: {c["field"]};
            color: {c["text"]};
            border: 1px solid {c["border"]};
            border-radius: {r_sm}px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background-color: {c["accent"]};
            border-radius: {r_sm - 1}px;
        }}

        QWidget#MetadataPanel {{
            background-color: {c["panel"]};
        }}
        QLabel#PanelFileIcon {{
            background-color: {c["sel"]};
            border-radius: 8px;
        }}
        QLabel#PanelTitle {{
            color: {c["text"]};
            font-size: {_tk.FONT_PX_TITLE}px;
            font-weight: 600;
        }}
        QLabel#PanelFileName, QLabel#PanelFieldLabel {{
            color: {c["text3"]};
        }}
        QLabel#PanelDescription, QLabel#PanelFieldValue {{
            color: {c["text2"]};
        }}
        QLabel#PanelDescription[empty="true"] {{
            color: {c["text3"]};
        }}
        QFrame#PanelDivider {{
            background-color: {c["border"]};
            border: none;
        }}
        QPushButton#PanelButton {{
            background-color: transparent;
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
            padding: 0px 12px;
            font-size: {_tk.FONT_PX_BASE}px;
            font-weight: 500;
        }}
        QPushButton#PanelButton:hover {{
            background-color: {c["hover"]};
        }}
        QPushButton#PanelButton:pressed {{
            background-color: {c["sel_soft"]};
        }}
        QPushButton#PanelButton:disabled {{
            color: {c["text3"]};
            border-color: {c["border"]};
        }}
        QPushButton#PanelButton[primary="true"] {{
            background-color: {c["accent"]};
            color: {c["accent_ink"]};
            border-color: {c["accent"]};
            font-weight: 600;
        }}
        QPushButton#PanelButton[primary="true"]:disabled {{
            background-color: {c["sel"]};
            color: {c["text3"]};
            border-color: {c["sel"]};
        }}
        QPushButton#PanelButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QToolButton#PanelStar {{
            border-radius: {r}px;
        }}
        QPushButton#TagPill {{
            background-color: {c["sel_soft"]};
            color: {c["text"]};
            border: none;
            border-radius: 11px;
            padding: 0px 10px 0px 8px;
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QPushButton#TagPill:hover {{
            background-color: {c["border"]};
        }}
        QPushButton#TableChip {{
            background-color: transparent;
            color: {c["text"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r_sm}px;
            padding: 0px 8px 0px 6px;
        }}
        QPushButton#TableChip:hover {{
            background-color: {c["hover"]};
        }}
        QScrollArea#ChipArea, QWidget#ChipAreaContent {{
            background: transparent;
            border: none;
        }}
        QWidget#EditorStrip {{
            background-color: {c["code"]};
            border-top: 1px solid {c["border"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QLabel#EditorStripInfo {{
            color: {c["text3"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QLabel#EditorStripMode {{
            color: {c["accent_text"]};
            font-size: {_tk.FONT_PX_SMALL}px;
            font-weight: 600;
        }}
        QLabel#EditorStripStale {{
            color: {c["warning"]};
            font-size: {_tk.FONT_PX_SMALL}px;
            font-weight: 600;
        }}
        QToolButton#EditorTab {{
            background: transparent;
            color: {c["text2"]};
            border: none;
            border-bottom: 2px solid transparent;
            padding: 2px 6px 0px 4px;
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QToolButton#EditorTab:hover {{
            color: {c["text"]};
        }}
        QToolButton#EditorTab:checked {{
            color: {c["text"]};
            border-bottom-color: {c["accent"]};
            font-weight: 600;
        }}
        QToolButton#StripButton, QToolButton#StripZoom {{
            background: transparent;
            color: {c["text2"]};
            border: none;
            border-radius: {r_sm}px;
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QToolButton#StripButton {{
            padding: 0px 8px 0px 6px;
        }}
        QToolButton#StripZoom {{
            padding: 0px;
        }}
        QToolButton#StripButton:hover, QToolButton#StripZoom:hover {{
            background-color: {c["hover"]};
            color: {c["text"]};
        }}
        QToolButton#StripButton:pressed, QToolButton#StripZoom:pressed {{
            background-color: {c["sel_soft"]};
        }}
        QToolButton#StripButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QToolButton#StripButton QLabel {{
            background: transparent;
            color: {c["text2"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QToolButton#StripButton[bordered="true"] {{
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
            padding: 0px 10px 0px 8px;
        }}
        QToolButton#StripButton[bordered="true"] QLabel {{
            color: {c["text"]};
            font-size: {_tk.FONT_PX_BASE}px;
        }}
        QWidget#DiagramWindow {{
            background-color: {c["dg_canvas"]};
        }}
        QWidget#DiagramWindowBar {{
            background-color: {c["chrome"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QLabel#DiagramWindowTitle {{
            color: {c["text"]};
            font-size: {_tk.FONT_PX_BASE + 1}px;
            font-weight: 600;
        }}
        QLabel#DiagramWindowPath {{
            color: {c["text3"]};
        }}
        QGraphicsView#DiagramView {{
            background-color: {c["dg_canvas"]};
            border: none;
        }}
        QWidget#DiagramMessage {{
            background-color: {c["dg_canvas"]};
        }}
        QLabel#DiagramMessageTitle {{
            background: transparent;
            color: {c["text"]};
            font-size: {_tk.FONT_PX_BASE}px;
            font-weight: 600;
        }}
        QLabel#DiagramMessageTitle[state="analyzing"] {{
            color: {c["text3"]};
            font-weight: 400;
        }}
        QLabel#DiagramMessageDetail {{
            background: transparent;
            color: {c["text3"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QWidget#DiagramNotices {{
            background-color: {c["dg_canvas"]};
        }}
        QFrame#DiagramNotice {{
            background-color: {c["panel"]};
            border: 1px solid {c["border"]};
            border-radius: {r}px;
        }}
        QFrame#DiagramNotice QLabel {{
            background: transparent;
            border: none;
            color: {c["text"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QToolButton#DiagramNoticeButton {{
            background: transparent;
            color: {c["accent_text"]};
            border: none;
            border-radius: {r_sm}px;
            padding: 2px 6px;
            font-size: {_tk.FONT_PX_SMALL}px;
            font-weight: 600;
        }}
        QToolButton#DiagramNoticeButton:hover {{
            background-color: {c["hover"]};
        }}

        QWidget#EditBanner {{
            background-color: {c["sel"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QWidget#EditBanner QLabel {{
            background: transparent;
        }}
        QLabel#EditBannerTitle {{
            color: {c["text"]};
            font-weight: 600;
        }}
        QLabel#EditBannerSubtitle {{
            color: {c["text2"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QPushButton#PanelButton QLabel#PanelButtonText {{
            background: transparent;
            color: {c["text"]};
            font-weight: 600;
        }}
        QPushButton#PanelButton[primary="true"] QLabel#PanelButtonText {{
            color: {c["accent_ink"]};
        }}
        QPushButton#PanelButton[primary="true"] QLabel#Kbd {{
            color: {c["accent_ink"]};
            border-color: {c["accent_ink"]};
        }}
        QPushButton#PanelButton[primary="true"]:disabled QLabel {{
            color: {c["text3"]};
            border-color: {c["border_strong"]};
        }}
        QLineEdit#EditTitle {{
            font-weight: 600;
        }}
        QLineEdit#EditCreated {{
            background: transparent;
            color: {c["text2"]};
            border-color: {c["border"]};
        }}
        QFrame#TagField {{
            background-color: {c["field"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
        }}
        QFrame#TagField[focused="true"] {{
            border-color: {c["accent"]};
        }}
        QLineEdit#TagFieldInput {{
            background: transparent;
            border: none;
            padding: 0px 2px;
        }}
        QWidget#EditTagPill {{
            background-color: {c["sel_soft"]};
            border-radius: 11px;
        }}
        QLabel#EditTagPillText {{
            background: transparent;
            color: {c["text"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QToolButton#EditTagPillRemove {{
            background: transparent;
            border-radius: 8px;
        }}
        QToolButton#EditTagPillRemove:hover {{
            background-color: {c["hover"]};
        }}
        QFrame#TagPopup {{
            background-color: {c["panel"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
        }}
        QFrame#TagPopup QLabel {{
            background: transparent;
        }}
        QLabel#TagPopupHeader, QLabel#TagPopupFooter {{
            color: {c["text3"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QFrame#TagPopupDivider {{
            background-color: {c["border"]};
            border: none;
        }}

        QTreeWidget#SidebarTree {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QTreeWidget#SidebarTree::item, QTreeWidget#SidebarTree::branch {{
            background: transparent;
            border: none;
        }}
        QTreeView#QueryList {{
            background-color: {c["bg"]};
            border: none;
            outline: none;
            selection-background-color: transparent;
            show-decoration-selected: 0;
        }}
        QTreeView#QueryList::item, QTreeView#QueryList::branch,
        QTreeView#QueryList::item:selected, QTreeView#QueryList::item:hover {{
            background: transparent;
            border: none;
        }}
        QHeaderView#QueryListHeader {{
            background-color: {c["bg"]};
            border: none;
        }}
        QWidget#QueryListEmpty {{
            background-color: {c["bg"]};
        }}
        QPushButton#OpenFolderBtn {{
            padding: 6px 10px;
        }}
        QPushButton#SearchHelpBtn {{
            color: {c["text3"]};
            background: transparent;
            border: none;
            border-radius: {r_sm}px;
            padding: 2px 4px;
        }}
        QPushButton#SearchHelpBtn:hover {{
            color: {c["text"]};
            background-color: {c["hover"]};
        }}

        QWidget#AddressBar {{
            background-color: {c["chrome"]};
        }}
        QWidget#CommandBar {{
            background-color: {c["bg"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QFrame#Breadcrumb {{
            background-color: {c["field"]};
            border: 1px solid {c["border"]};
            border-radius: {r}px;
        }}
        QFrame#Breadcrumb QLabel {{
            background: transparent;
        }}
        QToolButton#Crumb {{
            background: transparent;
            color: {c["text2"]};
            padding: 2px 6px;
            border-radius: {r_sm}px;
        }}
        QToolButton#Crumb:hover {{
            background-color: {c["hover"]};
            color: {c["text"]};
        }}
        QToolButton#Crumb[current="true"] {{
            color: {c["text"]};
            font-weight: 600;
        }}
        QLabel#CrumbPrefix {{
            color: {c["text2"]};
            padding-left: 4px;
        }}
        QToolButton#IconButton {{
            background: transparent;
            border: none;
            border-radius: {r}px;
        }}
        QToolButton#IconButton:hover {{
            background-color: {c["hover"]};
        }}
        QToolButton#IconButton:pressed, QToolButton#IconButton:checked {{
            background-color: {c["sel_soft"]};
        }}
        QToolButton#IconButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QPushButton#BarButton {{
            background: transparent;
            border: none;
            border-radius: {r}px;
            padding: 0px;
        }}
        QPushButton#BarButton:hover {{
            background-color: {c["hover"]};
        }}
        QPushButton#BarButton:pressed {{
            background-color: {c["sel_soft"]};
        }}
        QPushButton#BarButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QPushButton#BarButton QLabel {{
            background: transparent;
            color: {c["text"]};
        }}
        QPushButton#BarButton QLabel#BarButtonValue {{
            color: {c["text3"]};
        }}
        QPushButton#BarButton:disabled QLabel {{
            color: {c["text3"]};
        }}
        QPushButton#BarButton[primary="true"] {{
            background-color: {c["accent"]};
        }}
        QPushButton#BarButton[primary="true"] QLabel {{
            color: {c["accent_ink"]};
            font-weight: 600;
        }}
        QLabel#Kbd {{
            background: transparent;
            color: {c["text3"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r_sm}px;
            padding: 0px 4px;
            font-size: {_tk.FONT_PX_KBD}px;
        }}
        QPushButton#BarButton QLabel#Kbd {{
            color: {c["text3"]};
            font-weight: 400;
        }}
        QFrame#BarSeparator {{
            background-color: {c["border"]};
            border: none;
        }}
        QFrame#SearchField {{
            background-color: {c["field"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {r}px;
        }}
        QFrame#SearchField[focused="true"] {{
            border-color: {c["accent"]};
        }}
        QFrame#SearchField QLabel {{
            background: transparent;
        }}
        QLineEdit#SearchInput {{
            background: transparent;
            border: none;
            padding: 0px 2px;
        }}
        QWidget#SearchChip {{
            background-color: {c["sel_soft"]};
            border-radius: {r_sm}px;
        }}
        QLabel#SearchChipOp {{
            color: {c["text2"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QLabel#SearchChipValue {{
            color: {c["text"]};
            font-size: {_tk.FONT_PX_SMALL}px;
            font-weight: 600;
        }}
        QToolButton#SearchChipRemove, QToolButton#SearchClear {{
            background: transparent;
            border: none;
            border-radius: {r_sm}px;
        }}
        QToolButton#SearchChipRemove:hover, QToolButton#SearchClear:hover {{
            background-color: {c["hover"]};
        }}
        QWidget#ScopeBar {{
            background-color: {c["bg"]};
            border-bottom: 1px solid {c["border"]};
        }}
        QWidget#ScopeBar QLabel {{
            background: transparent;
            color: {c["text2"]};
        }}
        QPushButton#ScopeOption {{
            background: transparent;
            color: {c["text2"]};
            border: none;
            border-radius: 13px;
            padding: 0px 12px;
        }}
        QPushButton#ScopeOption:hover {{
            background-color: {c["hover"]};
            color: {c["text"]};
        }}
        QPushButton#ScopeOption:checked {{
            background-color: {c["sel"]};
            color: {c["text"]};
            font-weight: 600;
        }}
        QPushButton#ScopeClear {{
            background: transparent;
            color: {c["accent_text"]};
            border: none;
            border-radius: {r_sm}px;
            padding: 0px 6px;
            font-weight: 600;
        }}
        QPushButton#ScopeClear:hover {{
            background-color: {c["hover"]};
        }}
        QFrame#QuickSearch {{
            background-color: {c["bg"]};
            border: 1px solid {c["border_strong"]};
            border-radius: {_tk.RADIUS_DIALOG}px;
        }}
        QFrame#QuickSearch QLabel {{
            background: transparent;
        }}
        QLineEdit#QuickSearchInput {{
            background: transparent;
            border: none;
            color: {c["text"]};
            font-size: {_tk.FONT_PX_INPUT}px;
            padding: 0px;
        }}
        QFrame#QuickSearchRule {{
            background-color: {c["border"]};
            border: none;
        }}
        QListWidget#QuickSearchList {{
            background: transparent;
            border: none;
        }}
        QWidget#QuickSearchFooter {{
            background-color: {c["panel"]};
            border-top: 1px solid {c["border"]};
            border-bottom-left-radius: {_tk.RADIUS_DIALOG - 1}px;
            border-bottom-right-radius: {_tk.RADIUS_DIALOG - 1}px;
        }}
        QLabel#QuickSearchHint {{
            color: {c["text3"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}

        QStatusBar#StatusBar {{
            background-color: {c["chrome"]};
            border-top: 1px solid {c["border"]};
            color: {c["text2"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QStatusBar#StatusBar::item {{
            border: none;
        }}
        QStatusBar#StatusBar QLabel {{
            background: transparent;
            color: {c["text2"]};
            font-size: {_tk.FONT_PX_SMALL}px;
        }}
        QStatusBar#StatusBar QLabel#StatusSeparator {{
            color: {c["text3"]};
        }}
        QStatusBar#StatusBar QLabel#DiagramWindowStale {{
            color: {c["warning"]};
            font-weight: 600;
        }}
        QStatusBar#StatusBar QLabel#StatusDot {{
            background-color: {c["accent"]};
            border-radius: 4px;
        }}
        QProgressBar#StatusProgress {{
            background-color: {c["border"]};
            border: none;
            border-radius: 2px;
        }}
        QProgressBar#StatusProgress::chunk {{
            background-color: {c["accent"]};
            border-radius: 2px;
        }}
    """


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply_theme(app: QApplication, name: str) -> None:
    """Apply theme *name* to the whole application.

    Safe to call again for a live switch; widgets that build their own
    stylesheets still need their ``refresh_theme()`` afterwards.
    """
    _tk.set_active_palette(name)
    palette = _tk.THEMES[_tk.ACTIVE_THEME]

    # Qt 6.8+: lets the native title bar follow the app theme, not the OS one.
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(
            Qt.ColorScheme.Light if _tk.ACTIVE_THEME == "light" else Qt.ColorScheme.Dark
        )

    # Once a stylesheet is set, style() is Qt's QStyleSheetStyle wrapper with an
    # empty name() and Fusion already underneath; setting it again would only
    # repolish every widget on each live switch.
    if app.style().name().lower() not in ("fusion", ""):
        app.setStyle("Fusion")
    app.setPalette(build_palette(palette))
    app.setFont(ui_font())
    app.setStyleSheet(app_stylesheet(palette))
