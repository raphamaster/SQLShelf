"""Small building blocks shared by the window's bars.

Colours come from the global stylesheet (``theme.app_stylesheet``) through
object names; only the icons are drawn here, so every widget has a
``refresh_theme()`` that re-requests them after a theme switch.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)

from . import icons

BAR_BUTTON_HEIGHT = 30


class KbdHint(QLabel):
    """A keyboard shortcut shown as a small outlined key, e.g. ``Ctrl F``."""

    def __init__(self, keys: str, parent: QWidget | None = None) -> None:
        super().__init__(keys, parent)
        self.setObjectName("Kbd")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


class BarSeparator(QFrame):
    """Thin vertical rule between groups of bar buttons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BarSeparator")
        self.setFixedSize(1, 20)


class IconButton(QToolButton):
    """Square ghost button showing one line icon."""

    def __init__(
        self,
        icon_name: str,
        tooltip: str = "",
        *,
        size: int = BAR_BUTTON_HEIGHT,
        checkable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        self._icon_name = icon_name
        self.setFixedSize(size, size)
        self.setIconSize(icons.icon_size(16))
        self.setCheckable(checkable)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.refresh_theme()

    def set_tooltip(self, text: str) -> None:
        self.setToolTip(text)
        self.setAccessibleName(text)

    def set_menu(self, menu: QMenu) -> None:
        self.setMenu(menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def refresh_theme(self) -> None:
        self.setIcon(icons.icon(self._icon_name, "text2"))


class BarButton(QPushButton):
    """Ghost (or primary) bar button: icon, label, then optional extras.

    The extras are a muted *value* (the current choice of a menu button), a
    keyboard hint and a chevron when the button opens a *menu*. They are
    child labels, so each part can take its own colour from the stylesheet.
    """

    def __init__(
        self,
        icon_name: str,
        text: str,
        *,
        value: str | None = None,
        kbd: str | None = None,
        menu: QMenu | None = None,
        primary: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BarButton")
        self.setProperty("primary", primary)
        self.setFixedHeight(BAR_BUTTON_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        self._icon_key = "accent_ink" if primary else "text2"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)
        # A QPushButton's size hint ignores its layout; without this the
        # labels would be squeezed instead of widening the button.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        self._icon = QLabel()
        self._icon.setFixedSize(16, 16)
        self._text = QLabel(text)
        self._text.setObjectName("BarButtonText")
        self._value = QLabel(value or "")
        self._value.setObjectName("BarButtonValue")
        self._value.setVisible(value is not None)
        self._kbd = KbdHint(kbd or "")
        self._kbd.setVisible(kbd is not None)
        self._chevron = QLabel()
        self._chevron.setFixedSize(14, 14)
        self._chevron.setVisible(menu is not None)
        for part in (self._icon, self._text, self._value, self._chevron):
            part.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(part)
        layout.insertWidget(3, self._kbd)

        if menu is not None:
            self.setMenu(menu)
        self.setAccessibleName(text)
        self.refresh_theme()

    def set_text(self, text: str) -> None:
        self._text.setText(text)
        self.setAccessibleName(text)

    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def refresh_theme(self) -> None:
        self._icon.setPixmap(icons.pixmap(self._icon_name, self._icon_key))
        self._chevron.setPixmap(icons.pixmap("chevron_down", "text3", 14))
