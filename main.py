import sys

# ── Apply theme palette and language BEFORE importing any UI module ──────────
# UI widget modules capture token constants at import time via
# ``from .theme.tokens import ACCENT``, so the palette must be patched first.
# i18n.set_language() is also called early so tr() resolves correctly when
# widgets are constructed.
from sqlshelf.core import config as cfg
from sqlshelf.core import i18n
from sqlshelf.ui.theme import tokens as _tokens

_theme_name = cfg.get_theme()
_tokens.set_active_palette(_theme_name)
i18n.set_language(cfg.get_language())

# ── Now it is safe to import UI modules ─────────────────────────────────────
from PySide6.QtWidgets import QApplication  # noqa: E402

from sqlshelf.ui.main_window import MainWindow  # noqa: E402
from sqlshelf.ui.theme.tokens import QT_MATERIAL_THEMES, app_stylesheet  # noqa: E402

try:
    import qt_material
    HAS_QT_MATERIAL = True
except ImportError:
    HAS_QT_MATERIAL = False


def main() -> None:
    app = QApplication(sys.argv)

    if HAS_QT_MATERIAL:
        qt_theme = QT_MATERIAL_THEMES.get(_theme_name, "dark_teal.xml")
        qt_material.apply_stylesheet(app, theme=qt_theme)

    app.setStyleSheet(app.styleSheet() + app_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
