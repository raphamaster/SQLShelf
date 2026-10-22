import sys

# ── Apply theme palette and language BEFORE importing any UI module ──────────
# UI widget modules capture token constants at import time via
# ``from .tokens import ACCENT``, so the palette must be patched first.
# i18n.set_language() is also called early so tr() resolves correctly when
# widgets are constructed.
from sqlshelf.core import config as cfg
from sqlshelf.core import i18n
from sqlshelf.ui import tokens as _tokens

_theme_name = cfg.get_theme()
_tokens.set_active_palette(_theme_name)
i18n.set_language(cfg.get_language())

# ── Now it is safe to import UI modules ─────────────────────────────────────
from pathlib import Path  # noqa: E402

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_FAVICON = Path(__file__).parent / "images" / "FavIcon.png"

from sqlshelf.ui.main_window import MainWindow  # noqa: E402
from sqlshelf.ui.theme import apply_theme  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    if _FAVICON.exists():
        app.setWindowIcon(QIcon(str(_FAVICON)))

    apply_theme(app, _theme_name)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
