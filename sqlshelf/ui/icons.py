"""Line icons drawn from small inline SVGs and tinted with theme tokens.

Each icon is a 24×24 stroke drawing; ``icon(name, color_key)`` renders it in
the active palette's colour, so callers re-request icons after a theme switch
(``refresh_theme``) rather than holding on to the old ones.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import tokens as _tk
from .theme import qcolor

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="{w}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

_FOLDER = (
    '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5'
    'a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>'
)
_STAR = (
    '<path d="M12 3.2l2.7 5.5 6 .9-4.35 4.25 1.03 6L12 17l-5.38 2.85 1.03-6'
    'L3.3 9.6l6-.9z"/>'
)

ICONS: dict[str, str] = {
    "back": '<path d="M19 12H5M11 18l-6-6 6-6"/>',
    "forward": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "library": (
        '<path d="M12 3 3 7.5l9 4.5 9-4.5z"/><path d="M3 12l9 4.5 9-4.5"/>'
        '<path d="M3 16.5 12 21l9-4.5"/>'
    ),
    "refresh": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": _FOLDER,
    "folder_plus": _FOLDER + '<path d="M12 10.5v6M9 13.5h6"/>',
    "sort": '<path d="M7 4v16M3.5 7.5 7 4l3.5 3.5"/><path d="M17 20V4M13.5 16.5 17 20l3.5-3.5"/>',
    "group": '<path d="M4 6h16M4 12h11M4 18h14"/>',
    "zap": '<path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "rows": (
        '<rect x="4" y="4.5" width="16" height="6.5" rx="1.5"/>'
        '<rect x="4" y="13" width="16" height="6.5" rx="1.5"/>'
    ),
    "grid": (
        '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/>'
        '<rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/>'
        '<rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/>'
        '<rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>'
    ),
    "panel": '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M14 4.5v15"/>',
    "more": '<path d="M5 12h.01M12 12h.01M19 12h.01"/>',
    "menu": '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    "file": (
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 3v5h5M9 13h6M9 17h6"/>'
    ),
    "star": _STAR,
    "star_filled": _STAR.replace("<path ", '<path fill="{c}" '),
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "tag": (
        '<path d="M12.5 3.5H4.5a1 1 0 0 0-1 1v8l8.8 8.8a1 1 0 0 0 1.4 0l6.6-6.6'
        'a1 1 0 0 0 0-1.4z"/><path d="M8 8h.01"/>'
    ),
    "copy": (
        '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/>'
        '<path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>'
    ),
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "external": (
        '<path d="M14 4h6v6M20 4l-9 9"/>'
        '<path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'
    ),
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m6 15 6-6 6 6"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "table": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 10h17M9.5 10v9.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
}

_DISABLED_OPACITY = 0.4

_cache: dict[tuple[str, str, int, float], QIcon] = {}


def _render(
    name: str, color: str, size: int, dpr: float, opacity: float = 1.0
) -> QPixmap:
    hex_color = qcolor(color).name()
    weight = "2.6" if name == "more" else "1.75"
    body = ICONS[name].replace("{c}", hex_color)
    svg = _SVG.format(c=hex_color, w=weight, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(opacity)
    renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _dpr() -> float:
    app = QGuiApplication.instance()
    return app.devicePixelRatio() if isinstance(app, QGuiApplication) else 1.0


def icon(name: str, color_key: str = "text2", size: int = 16) -> QIcon:
    """Icon *name* in the active palette's *color_key*, *size* px square.

    The disabled state is the same drawing faded out, rather than Qt's
    generated grey, which reads badly on the dark palette.
    """
    color = _tk.color(color_key)
    dpr = _dpr()
    key = (name, color, size, dpr)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    result = QIcon(_render(name, color, size, dpr))
    result.addPixmap(
        _render(name, color, size, dpr, _DISABLED_OPACITY), QIcon.Mode.Disabled
    )
    _cache[key] = result
    return result


def pixmap(name: str, color_key: str = "text2", size: int = 16) -> QPixmap:
    """Icon *name* as a pixmap, for a QLabel."""
    return icon(name, color_key, size).pixmap(QSize(size, size))


def icon_size(size: int = 16) -> QSize:
    return QSize(size, size)


def dot(color: str, size: int = 8) -> QPixmap:
    """A filled circle in *color* (a CSS colour, e.g. a tag's), *size* px wide."""
    dpr = _dpr()
    result = QPixmap(round(size * dpr), round(size * dpr))
    result.setDevicePixelRatio(dpr)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qcolor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return result
