from __future__ import annotations

# ── Surfaces (darkest → lightest) ─────────────────────────────────────────
BG_APP      = "#070B14"   # window / sidebar background
SURFACE     = "#0A0F1A"   # panels (list, detail)
CARD        = "#141C2B"   # selected item / hover / chips
EDITOR_BG   = "#060A12"   # SQL editor background

# ── Borders ───────────────────────────────────────────────────────────────
BORDER      = "#1C2433"   # subtle dividers
BORDER_EMPH = "#233045"   # hover / emphasis

# ── Accent (use ONLY for actions and active state) ─────────────────────────
ACCENT        = "#0ADE99"
ACCENT_FILL   = "rgba(10,222,153,0.10)"
ACCENT_BORDER = "rgba(10,222,153,0.40)"

# ── Text (two main levels + hint) ─────────────────────────────────────────
TEXT_PRIMARY   = "rgba(255,255,255,0.92)"
TEXT_SECONDARY = "rgba(255,255,255,0.55)"
TEXT_TERTIARY  = "rgba(255,255,255,0.38)"

# ── Neutral tags (information, not action) ─────────────────────────────────
TAG_BG      = "rgba(255,255,255,0.07)"
TAG_TEXT    = "rgba(255,255,255,0.55)"
TAG_RADIUS  = 9   # pill shape for chips

# ── Base spacing ──────────────────────────────────────────────────────────
PAD_GLOBAL = 12   # default panel breathing room
RADIUS     = 7    # card / button corner radius

# ── Semantic supplements ───────────────────────────────────────────────────
STAR_ACTIVE     = "#ffd700"   # favorite star — filled
STAR_HOVER      = "#ffec6e"   # favorite star — hover glow
LINK            = "#7aaa7a"   # table / column clickable links
LINK_PREFIX     = "#9090c0"   # "Tables:" / "Columns:" label
CHIP_DELETE_BG  = "#5c1a1a"   # remove-tag chip hover background
CHIP_DELETE_FG  = "#ffaaaa"   # remove-tag chip hover foreground

# ── CodeEditor gutter (QColor-safe hex, no rgba) ──────────────────────────
GUTTER_BG           = "#141C2B"   # gutter strip background (= CARD)
GUTTER_NUM_INACTIVE = "#4A5568"   # inactive line numbers
GUTTER_NUM_CURRENT  = "#A0AEC0"   # current-line number
EDITOR_LINE_HL      = "#0D1421"   # current-line highlight tint

# ── SQL syntax highlighting ────────────────────────────────────────────────
SYN_KEYWORD = "#569CD6"
SYN_STRING  = "#CE9178"
SYN_NUMBER  = "#B5CEA8"
SYN_COMMENT = "#6A9955"


def app_stylesheet() -> str:
    """Global QSS applied after qt-material, scoped by objectName / class."""
    return f"""
        QMainWindow {{
            background-color: {BG_APP};
        }}
        QSplitter {{
            background-color: {BG_APP};
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
        QToolBar#EditorToolBar {{
            background-color: {CARD};
            border: none;
            padding: 2px 8px;
            spacing: 2px;
        }}
        QToolBar#EditorToolBar QPushButton {{
            padding: 1px 10px;
            font-size: 12px;
            max-height: 24px;
        }}
    """
