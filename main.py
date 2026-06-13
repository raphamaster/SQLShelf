import sys

from PySide6.QtWidgets import QApplication

from sqlshelf.ui.main_window import MainWindow
from sqlshelf.ui.theme.tokens import app_stylesheet

try:
    import qt_material
    HAS_QT_MATERIAL = True
except ImportError:
    HAS_QT_MATERIAL = False


def main() -> None:
    app = QApplication(sys.argv)

    if HAS_QT_MATERIAL:
        qt_material.apply_stylesheet(app, theme="dark_teal.xml")

    app.setStyleSheet(app.styleSheet() + app_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
