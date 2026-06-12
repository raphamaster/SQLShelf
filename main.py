import sys

from PySide6.QtWidgets import QApplication

from sqlshelf.ui.main_window import MainWindow

try:
    import qt_material
    HAS_QT_MATERIAL = True
except ImportError:
    HAS_QT_MATERIAL = False


def main() -> None:
    app = QApplication(sys.argv)

    if HAS_QT_MATERIAL:
        qt_material.apply_stylesheet(app, theme="dark_teal.xml")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
