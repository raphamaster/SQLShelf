from __future__ import annotations

# ---------------------------------------------------------------------------
# Palette definitions
# ---------------------------------------------------------------------------

DARK: dict[str, object] = {
    # Surfaces (darkest → lightest)
    "BG_APP":   "#070B14",
    "SURFACE":  "#0A0F1A",
    "CARD":     "#141C2B",
    "EDITOR_BG": "#060A12",
    # Borders
    "BORDER":      "#1C2433",
    "BORDER_EMPH": "#233045",
    # Accent
    "ACCENT":        "#0ADE99",
    "ACCENT_FILL":   "rgba(10,222,153,0.10)",
    "ACCENT_BORDER": "rgba(10,222,153,0.40)",
    "ACCENT_FOCUS_BG": "rgba(10,222,153,0.15)",
    # Text
    "TEXT_PRIMARY":   "rgba(255,255,255,0.92)",
    "TEXT_SECONDARY": "rgba(255,255,255,0.55)",
    "TEXT_TERTIARY":  "rgba(255,255,255,0.38)",
    # Selection
    "SELECTION_BG": "#264F78",
    "SELECTION_FG": "#ffffff",
    # Hover overlays
    "HOVER_BG_LIGHT":  "rgba(255,255,255,0.04)",
    "HOVER_BG_MEDIUM": "rgba(255,255,255,0.06)",
    "HOVER_BG_STRONG": "rgba(255,255,255,0.08)",
    # Tags
    "TAG_BG":     "rgba(255,255,255,0.07)",
    "TAG_TEXT":   "rgba(255,255,255,0.55)",
    "TAG_RADIUS": 9,
    # Spacing
    "PAD_GLOBAL": 12,
    "RADIUS":     7,
    # Semantic supplements
    "STAR_ACTIVE":    "#ffd700",
    "STAR_HOVER":     "#ffec6e",
    "LINK":           "#7aaa7a",
    "LINK_PREFIX":    "#9090c0",
    "CHIP_DELETE_BG": "#5c1a1a",
    "CHIP_DELETE_FG": "#ffaaaa",
    # QueryList item hover background (solid, for custom delegate)
    "LIST_ITEM_HOVER_BG": "#0E141F",
    # CodeEditor gutter
    "GUTTER_BG":           "#141C2B",
    "GUTTER_NUM_INACTIVE": "#4A5568",
    "GUTTER_NUM_CURRENT":  "#A0AEC0",
    "EDITOR_LINE_HL":       "#0D1421",
    "EDITOR_OCCURRENCE_BG": "#1A5C3A",
    "EDITOR_OCCURRENCE_FG": "#D0F5E8",
    # SQL syntax highlighting
    "SYN_KEYWORD": "#569CD6",
    "SYN_STRING":  "#CE9178",
    "SYN_NUMBER":  "#B5CEA8",
    "SYN_COMMENT": "#6A9955",
}

LIGHT: dict[str, object] = {
    # Surfaces
    "BG_APP":    "#F0F4F8",
    "SURFACE":   "#FFFFFF",
    "CARD":      "#E4EAF2",
    "EDITOR_BG": "#FAFBFD",
    # Borders
    "BORDER":      "#C8D0DC",
    "BORDER_EMPH": "#A8B4C8",
    # Accent (darker teal for contrast on light bg)
    "ACCENT":        "#00876C",
    "ACCENT_FILL":   "rgba(0,135,108,0.09)",
    "ACCENT_BORDER": "rgba(0,135,108,0.40)",
    "ACCENT_FOCUS_BG": "rgba(0,135,108,0.12)",
    # Text
    "TEXT_PRIMARY":   "rgba(12,16,28,0.87)",
    "TEXT_SECONDARY": "rgba(12,16,28,0.60)",
    "TEXT_TERTIARY":  "rgba(12,16,28,0.42)",
    # Selection
    "SELECTION_BG": "#B3D4FF",
    "SELECTION_FG": "#000000",
    # Hover overlays
    "HOVER_BG_LIGHT":  "rgba(0,0,0,0.04)",
    "HOVER_BG_MEDIUM": "rgba(0,0,0,0.06)",
    "HOVER_BG_STRONG": "rgba(0,0,0,0.09)",
    # Tags
    "TAG_BG":     "rgba(0,0,0,0.07)",
    "TAG_TEXT":   "rgba(12,16,28,0.60)",
    "TAG_RADIUS": 9,
    # Spacing
    "PAD_GLOBAL": 12,
    "RADIUS":     7,
    # Semantic supplements
    "STAR_ACTIVE":    "#C89000",
    "STAR_HOVER":     "#DDA000",
    "LINK":           "#1A6B40",
    "LINK_PREFIX":    "#5050A0",
    "CHIP_DELETE_BG": "#FFE0E0",
    "CHIP_DELETE_FG": "#C00000",
    # QueryList item hover background (solid, for custom delegate)
    "LIST_ITEM_HOVER_BG": "#D8E0EC",
    # CodeEditor gutter
    "GUTTER_BG":           "#E4EAF2",
    "GUTTER_NUM_INACTIVE": "#8090A0",
    "GUTTER_NUM_CURRENT":  "#384558",
    "EDITOR_LINE_HL":       "#EBF0F8",
    "EDITOR_OCCURRENCE_BG": "#A0D8C0",
    "EDITOR_OCCURRENCE_FG": "#002818",
    # SQL syntax highlighting (classic light IDE)
    "SYN_KEYWORD": "#0000B0",
    "SYN_STRING":  "#A31515",
    "SYN_NUMBER":  "#098658",
    "SYN_COMMENT": "#008000",
}

# Available themes
THEMES: dict[str, dict] = {"dark": DARK, "light": LIGHT}

# Maps theme name → qt-material XML filename
QT_MATERIAL_THEMES: dict[str, str] = {
    "dark":  "dark_teal.xml",
    "light": "light_teal.xml",
}

# ---------------------------------------------------------------------------
# Module-level constants (initialised to DARK; patched by set_active_palette)
# ---------------------------------------------------------------------------

ACTIVE_THEME: str = "dark"

# Surfaces
BG_APP      = DARK["BG_APP"]
SURFACE     = DARK["SURFACE"]
CARD        = DARK["CARD"]
EDITOR_BG   = DARK["EDITOR_BG"]

# Borders
BORDER      = DARK["BORDER"]
BORDER_EMPH = DARK["BORDER_EMPH"]

# Accent
ACCENT        = DARK["ACCENT"]
ACCENT_FILL   = DARK["ACCENT_FILL"]
ACCENT_BORDER = DARK["ACCENT_BORDER"]
ACCENT_FOCUS_BG = DARK["ACCENT_FOCUS_BG"]

# Text
TEXT_PRIMARY   = DARK["TEXT_PRIMARY"]
TEXT_SECONDARY = DARK["TEXT_SECONDARY"]
TEXT_TERTIARY  = DARK["TEXT_TERTIARY"]

# Selection
SELECTION_BG = DARK["SELECTION_BG"]
SELECTION_FG = DARK["SELECTION_FG"]

# Hover overlays
HOVER_BG_LIGHT  = DARK["HOVER_BG_LIGHT"]
HOVER_BG_MEDIUM = DARK["HOVER_BG_MEDIUM"]
HOVER_BG_STRONG = DARK["HOVER_BG_STRONG"]

# Tags
TAG_BG      = DARK["TAG_BG"]
TAG_TEXT    = DARK["TAG_TEXT"]
TAG_RADIUS  = DARK["TAG_RADIUS"]

# Spacing
PAD_GLOBAL = DARK["PAD_GLOBAL"]
RADIUS     = DARK["RADIUS"]

# Semantic supplements
STAR_ACTIVE     = DARK["STAR_ACTIVE"]
STAR_HOVER      = DARK["STAR_HOVER"]
LINK            = DARK["LINK"]
LINK_PREFIX     = DARK["LINK_PREFIX"]
CHIP_DELETE_BG  = DARK["CHIP_DELETE_BG"]
CHIP_DELETE_FG  = DARK["CHIP_DELETE_FG"]

# QueryList item hover
LIST_ITEM_HOVER_BG = DARK["LIST_ITEM_HOVER_BG"]

# CodeEditor gutter
GUTTER_BG           = DARK["GUTTER_BG"]
GUTTER_NUM_INACTIVE = DARK["GUTTER_NUM_INACTIVE"]
GUTTER_NUM_CURRENT  = DARK["GUTTER_NUM_CURRENT"]
EDITOR_LINE_HL       = DARK["EDITOR_LINE_HL"]
EDITOR_OCCURRENCE_BG = DARK["EDITOR_OCCURRENCE_BG"]
EDITOR_OCCURRENCE_FG = DARK["EDITOR_OCCURRENCE_FG"]

# SQL syntax highlighting
SYN_KEYWORD = DARK["SYN_KEYWORD"]
SYN_STRING  = DARK["SYN_STRING"]
SYN_NUMBER  = DARK["SYN_NUMBER"]
SYN_COMMENT = DARK["SYN_COMMENT"]


# ---------------------------------------------------------------------------
# Theme API
# ---------------------------------------------------------------------------

def get_palette(name: str) -> dict:
    """Return the palette dict for the given theme name."""
    return THEMES.get(name, DARK)


def set_active_palette(name: str) -> None:
    """Patch all module-level constants to *name*'s palette.

    Must be called ONCE at startup, before any UI module is imported,
    so that ``from .theme.tokens import ACCENT`` in widget modules
    picks up the correct value.
    """
    global ACTIVE_THEME
    ACTIVE_THEME = name
    p = THEMES.get(name, DARK)
    g = globals()
    for k, v in p.items():
        if k in g:
            g[k] = v


# ---------------------------------------------------------------------------
# Global QSS
# ---------------------------------------------------------------------------

def app_stylesheet() -> str:
    """Global QSS applied after qt-material, scoped by objectName / class.

    All color references are module-level constants so that set_active_palette()
    patches them correctly before this function is called.
    """
    return f"""
        QMainWindow {{
            background-color: {BG_APP};
        }}
        QSplitter {{
            background-color: {BG_APP};
        }}
        QWidget#SidebarWidget {{
            background-color: {BG_APP};
        }}
        QWidget#BrowseWidget {{
            background-color: transparent;
        }}
        QWidget#TitleRow {{
            background-color: transparent;
        }}
        QSplitter::handle {{
            background-color: {BORDER};
            width: 1px;
            height: 1px;
        }}
        QListWidget {{
            background-color: {SURFACE};
            border: 1px solid {BORDER};
            outline: none;
        }}
        QListWidget::item {{
            color: {TEXT_PRIMARY};
            padding: 6px 8px;
        }}
        QListWidget::item:hover {{
            background-color: {CARD};
        }}
        QListWidget::item:selected {{
            background-color: {CARD};
            color: {ACCENT};
            border-left: 2px solid {ACCENT};
        }}
        QWidget#EditorTopSection {{
            background-color: {CARD};
            border-bottom: 2px solid {BORDER_EMPH};
        }}
        QWidget#MetadataPanel {{
            background-color: {CARD};
            border-bottom: 1px solid {BORDER};
        }}
        QWidget#EditorToolBar {{
            background-color: {CARD};
        }}
        QWidget#EditorToolBar QPushButton {{
            padding: 2px 12px;
            font-size: 12px;
            max-height: 24px;
            border-radius: {RADIUS}px;
        }}
        QPushButton#NavButton {{
            background: transparent;
            border: none;
            border-left: 2px solid transparent;
            text-align: left;
            padding: 6px 8px 6px 12px;
            color: {TEXT_SECONDARY};
            font-size: 12px;
        }}
        QPushButton#NavButton:hover {{
            background-color: {CARD};
            color: {TEXT_PRIMARY};
        }}
        QPushButton#NavButton[active="true"] {{
            background-color: {CARD};
            color: {ACCENT};
            border-left: 2px solid {ACCENT};
        }}
        QToolButton#SectionHeader {{
            background: transparent;
            border: none;
            color: {TEXT_TERTIARY};
            font-size: 9px;
            font-weight: bold;
            text-align: left;
            padding: 4px 4px 2px 0px;
        }}
        QLabel#SectionLabel {{
            color: {TEXT_TERTIARY};
            font-size: 9px;
            font-weight: bold;
        }}
        QToolButton#SectionHeader:hover {{
            color: {TEXT_SECONDARY};
            background-color: {HOVER_BG_LIGHT};
            border-radius: 3px;
        }}
        QPushButton#OpenFolderBtn {{
            background-color: {ACCENT_FILL};
            border: 1px solid {ACCENT_BORDER};
            color: {ACCENT};
            border-radius: {RADIUS}px;
            padding: 6px 10px;
            font-size: 12px;
        }}
        QPushButton#OpenFolderBtn:hover {{
            background-color: {ACCENT_FOCUS_BG};
            border-color: {ACCENT};
        }}
        QPushButton#OpenFolderBtn:pressed {{
            background-color: {ACCENT_FOCUS_BG};
        }}
        QPushButton#SearchHelpBtn {{
            color: {TEXT_TERTIARY};
            background: transparent;
            border: none;
            border-radius: 3px;
            padding: 2px 4px;
        }}
        QPushButton#SearchHelpBtn:hover {{
            color: {TEXT_PRIMARY};
            background-color: {HOVER_BG_MEDIUM};
        }}
        QWidget#EditorToolBar QPushButton:hover {{
            background-color: {HOVER_BG_STRONG};
            border-radius: 4px;
            border: 1px solid {BORDER_EMPH};
        }}
        QPlainTextEdit {{
            selection-background-color: {SELECTION_BG};
            selection-color: {SELECTION_FG};
        }}
        QMenuBar {{
            background-color: {BG_APP};
            color: {TEXT_PRIMARY};
            border-bottom: 1px solid {BORDER};
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 4px 8px;
        }}
        QMenuBar::item:selected {{
            background-color: {BORDER_EMPH};
            color: {TEXT_PRIMARY};
            border-radius: 3px;
        }}
        QMenuBar::item:pressed {{
            background-color: {CARD};
            color: {TEXT_PRIMARY};
        }}
        QMenu {{
            background-color: {CARD};
            border: 1px solid {BORDER_EMPH};
            color: {TEXT_PRIMARY};
        }}
        QMenu::item {{
            padding: 5px 20px 5px 10px;
        }}
        QMenu::item:selected {{
            background-color: {BORDER_EMPH};
            color: {TEXT_PRIMARY};
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {BORDER};
            margin: 2px 0px;
        }}
        QMenu::indicator:checked {{
            color: {ACCENT};
        }}
        QComboBox {{
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: {RADIUS}px;
            padding: 4px 8px;
            min-height: 24px;
        }}
        QComboBox:hover {{
            border-color: {BORDER_EMPH};
        }}
        QComboBox:focus {{
            border-color: {ACCENT};
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
        }}
        QComboBox:on {{
            background-color: {SURFACE};
            color: {TEXT_PRIMARY};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 24px;
            border-left: 1px solid {BORDER};
            border-top-right-radius: {RADIUS}px;
            border-bottom-right-radius: {RADIUS}px;
        }}
        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {CARD};
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER_EMPH};
            selection-background-color: {BORDER_EMPH};
            selection-color: {TEXT_PRIMARY};
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 5px 10px;
            min-height: 24px;
            color: {TEXT_PRIMARY};
            background-color: transparent;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background-color: {HOVER_BG_STRONG};
            color: {TEXT_PRIMARY};
        }}
        QComboBox QAbstractItemView::item:selected {{
            background-color: {BORDER_EMPH};
            color: {TEXT_PRIMARY};
        }}
        QComboBox QAbstractItemView::item:hover:selected {{
            background-color: {BORDER_EMPH};
            color: {TEXT_PRIMARY};
        }}
    """
