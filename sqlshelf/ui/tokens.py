"""Design tokens for the SQLShelf UI — values from docs/redesign-ui.md.

Pure Python, no Qt import, so the palette can be tested without a display.
Colours are CSS strings; rgba() alpha is 0–1 here, and ``theme.py`` converts it
to the 0–255 range QSS expects.
"""

from __future__ import annotations

import zlib

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

DARK: dict[str, str] = {
    # Surfaces
    "chrome": "#121417",
    "bg": "#181a1e",
    "panel": "#1c1f24",
    "field": "#23272d",
    "code": "#15171a",
    # Borders
    "border": "#2a2e34",
    "border_strong": "#3a3f46",
    # States
    "hover": "rgba(255,255,255,.05)",
    "sel": "rgba(10,222,153,.15)",
    "sel_soft": "rgba(255,255,255,.07)",
    # Text
    "text": "#e9ebee",
    "text2": "#b3b9c1",
    "text3": "#8b939c",
    # Accent
    "accent": "#0ADE99",
    "accent_ink": "#052a1d",
    "accent_text": "#2fe3a8",
    "mark": "rgba(10,222,153,.30)",
    "overlay": "rgba(5,6,8,.55)",
    "shadow": "rgba(0,0,0,.50)",
    # Editor
    "line_no": "#848c95",
    "cur_line": "rgba(255,255,255,.05)",
    "star": "#f5b524",
    # SQL syntax
    "syn_keyword": "#6cb6ff",
    "syn_function": "#e7c07b",
    "syn_string": "#f29e74",
    "syn_number": "#c9a6ff",
    "syn_comment": "#8b939c",
    "syn_identifier": "#d7dbe0",
    "syn_operator": "#a3abb4",
    # Query diagram; dg_dim and the dg_frame_* are opacities, not colours
    "dg_canvas": "#15171a",
    "dg_dot": "#262a30",
    "dg_node": "#1f2228",
    "dg_row_key": "#23272e",
    "dg_ink": "#0f1114",
    "dg_join": "#9aa2ab",
    "dg_flow": "#5aa7ff",
    "dg_dim": ".28",
    "dg_frame_fill": ".07",
    "dg_frame_border": ".38",
}

LIGHT: dict[str, str] = {
    # Surfaces
    "chrome": "#f3f4f6",
    "bg": "#ffffff",
    "panel": "#fbfbfc",
    "field": "#ffffff",
    "code": "#f7f8fa",
    # Borders
    "border": "#e3e6ea",
    "border_strong": "#cdd2d8",
    # States
    "hover": "rgba(0,0,0,.04)",
    "sel": "rgba(10,222,153,.18)",
    "sel_soft": "rgba(0,0,0,.06)",
    # Text
    "text": "#1a1d21",
    "text2": "#474e57",
    "text3": "#646b73",
    # Accent
    "accent": "#0ADE99",
    "accent_ink": "#052a1d",
    "accent_text": "#04794f",
    "mark": "rgba(10,222,153,.35)",
    "overlay": "rgba(20,24,28,.32)",
    "shadow": "rgba(20,24,28,.24)",
    # Editor
    "line_no": "#656d76",
    "cur_line": "rgba(0,0,0,.04)",
    "star": "#f5b524",
    # SQL syntax
    "syn_keyword": "#0b5cad",
    "syn_function": "#8a5a00",
    "syn_string": "#b1461b",
    "syn_number": "#6f42c1",
    "syn_comment": "#6a737d",
    "syn_identifier": "#24292f",
    "syn_operator": "#57606a",
    # Query diagram; dg_dim and the dg_frame_* are opacities, not colours
    "dg_canvas": "#f4f5f7",
    "dg_dot": "#dde1e6",
    "dg_node": "#ffffff",
    "dg_row_key": "#f6f7f9",
    "dg_ink": "#121417",
    "dg_join": "#6b737c",
    "dg_flow": "#2f74d6",
    "dg_dim": ".30",
    "dg_frame_fill": ".10",
    "dg_frame_border": ".55",
}

THEMES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

# ---------------------------------------------------------------------------
# Tag colours
# ---------------------------------------------------------------------------

TAG_PALETTE: tuple[str, ...] = (
    "#4c9aff",
    "#f5a524",
    "#0ade99",
    "#b38bff",
    "#ff7eb6",
    "#ff6b6b",
    "#2dd4bf",
    "#9aa3ad",
)


def tag_color(name: str) -> str:
    """Return the fixed colour for tag *name*.

    crc32 rather than hash(): str hashes are salted per process, and a tag must
    keep its colour across runs. Nothing is written to the .sql file.
    """
    return TAG_PALETTE[zlib.crc32(name.encode("utf-8")) % len(TAG_PALETTE)]


# ---------------------------------------------------------------------------
# Query diagram groups
# ---------------------------------------------------------------------------

DIAGRAM_SCHEMA_PALETTE: tuple[str, ...] = ("#4c9aff", "#ff7eb6", "#ff8b6b", "#8fa3bf")
_DIAGRAM_FIXED: dict[str, str] = {
    "temp": "#f5a524",
    "derived": "#b38bff",
    "result": "#2dd4bf",
}


def diagram_group_color(kind: str, key: str = "") -> str:
    """The colour of a diagram frame and of its nodes' headers.

    *kind* is the frame kind from ``graph_layout`` and *key* the schema for a
    schema frame. ``dbo`` and the tables without a schema are always blue;
    another schema keeps its colour across runs, as tags do. The accent green
    is never a group colour: it marks selection and focus.
    """
    if kind in _DIAGRAM_FIXED:
        return _DIAGRAM_FIXED[kind]
    name = key.casefold()
    if kind != "schema" or name in ("", "dbo"):
        return DIAGRAM_SCHEMA_PALETTE[0]
    return DIAGRAM_SCHEMA_PALETTE[
        zlib.crc32(name.encode("utf-8")) % len(DIAGRAM_SCHEMA_PALETTE)
    ]


# ---------------------------------------------------------------------------
# Typography and shape
# ---------------------------------------------------------------------------

# First installed family wins; on other platforms the system font is used.
UI_FONT_FAMILIES_WINDOWS: tuple[str, ...] = (
    "Segoe UI Variable Text",
    "Segoe UI Variable",
    "Segoe UI",
)
# Falls back to the system fixed-width font when neither is installed.
MONO_FONT_FAMILIES: tuple[str, ...] = ("Cascadia Mono", "Consolas")

FONT_PX_BASE = 13
FONT_PX_SMALL = 12  # secondary text and labels
FONT_PX_KBD = 11  # keyboard shortcut hints
FONT_PX_TITLE = 16  # details panel title
FONT_PX_INPUT = 15  # quick search input
FONT_PX_CODE = 13
CODE_LINE_HEIGHT_PX = 19

RADIUS_SM = 4  # table chips, kbd
RADIUS = 6  # buttons, fields, rows
RADIUS_PILL = 10
RADIUS_DIALOG = 12

# Query diagram
DG_FONT_PX_MONO = 11  # roles, aliases, badges, edge labels
DG_FONT_PX_PREDICATE = 12  # a filter on a column's row
DG_FONT_PX_MARK = 10  # the "on" mark before a filter
DG_RADIUS_FRAME = 10
DG_RADIUS_MARK = 3

# ---------------------------------------------------------------------------
# Active palette
# ---------------------------------------------------------------------------

ACTIVE_THEME: str = "dark"
_active: dict[str, str] = DARK


def color(key: str) -> str:
    """Return token *key* from the active palette."""
    return _active[key]


def opacity(key: str) -> float:
    """Token *key* of the active palette read as an opacity (``dg_dim``)."""
    return float(_active[key])


# ---------------------------------------------------------------------------
# Legacy names
#
# Widgets the redesign has not reached yet still read these. Each one now
# points at a design token, so the whole app takes the new palette; an alias
# goes away once the last widget reading it is rewritten.
# ---------------------------------------------------------------------------

_LEGACY: dict[str, str] = {
    "SURFACE": "bg",
    "CARD": "panel",
    "BORDER": "border",
    "BORDER_EMPH": "border_strong",
    "ACCENT": "accent_text",
    "ACCENT_FILL": "field",
    "ACCENT_BORDER": "border_strong",
    "ACCENT_FOCUS_BG": "field",
    "TEXT_PRIMARY": "text",
    "TEXT_SECONDARY": "text2",
    "TEXT_TERTIARY": "text3",
    "SELECTION_BG": "mark",
    "HOVER_BG_STRONG": "hover",
    "TAG_BG": "sel_soft",
    "TAG_TEXT": "text2",
    "STAR_ACTIVE": "star",
    "STAR_HOVER": "star",
    "CHIP_DELETE_BG": "hover",
    "CHIP_DELETE_FG": "text",
}


def _alias(legacy: str) -> str:
    return DARK[_LEGACY[legacy]]


SURFACE = _alias("SURFACE")
CARD = _alias("CARD")
BORDER = _alias("BORDER")
BORDER_EMPH = _alias("BORDER_EMPH")
ACCENT = _alias("ACCENT")
ACCENT_FILL = _alias("ACCENT_FILL")
ACCENT_BORDER = _alias("ACCENT_BORDER")
ACCENT_FOCUS_BG = _alias("ACCENT_FOCUS_BG")
TEXT_PRIMARY = _alias("TEXT_PRIMARY")
TEXT_SECONDARY = _alias("TEXT_SECONDARY")
TEXT_TERTIARY = _alias("TEXT_TERTIARY")
SELECTION_BG = _alias("SELECTION_BG")
HOVER_BG_STRONG = _alias("HOVER_BG_STRONG")
TAG_BG = _alias("TAG_BG")
TAG_TEXT = _alias("TAG_TEXT")
STAR_ACTIVE = _alias("STAR_ACTIVE")
STAR_HOVER = _alias("STAR_HOVER")
CHIP_DELETE_BG = _alias("CHIP_DELETE_BG")
CHIP_DELETE_FG = _alias("CHIP_DELETE_FG")

TAG_RADIUS = 9


def set_active_palette(name: str) -> None:
    """Make *name*'s palette the active one and repoint the legacy names.

    Called at startup before any UI module is imported, so that
    ``from .tokens import ACCENT`` in widget modules binds the right value,
    and again on every live theme switch.
    """
    global ACTIVE_THEME, _active
    ACTIVE_THEME = name if name in THEMES else "dark"
    _active = THEMES[ACTIVE_THEME]
    g = globals()
    for legacy, key in _LEGACY.items():
        g[legacy] = _active[key]
