"""Painting helpers shared by item delegates.

Colours are read from the active tokens at paint time, so a theme switch only
needs a repaint.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QFont, QPainter, QPen

from . import icons
from . import tokens as _tk
from .theme import qcolor

KBD_HEIGHT = 18
_KBD_PAD = 5
GROUP_HEIGHT = 36  # a 32 px band under a 4 px gap
GROUP_BAND = 32
_GROUP_CHEVRON_X = 14
_GROUP_TEXT_X = 35


def sized_font(
    base: QFont, pixel_size: int, weight: QFont.Weight | None = None
) -> QFont:
    font = QFont(base)
    font.setPixelSize(pixel_size)
    if weight is not None:
        font.setWeight(weight)
    return font


def draw_marked_text(
    painter: QPainter,
    rect: QRect,
    text: str,
    color_key: str,
    spans: list[tuple[int, int]] | None = None,
) -> int:
    """Draw *text* on the painter's font, elided into *rect*, with the *spans*
    (``(start, end)`` ranges of *text*) on a ``mark`` background. Return the
    drawn width.

    Everything is measured through the painter: the font the cell resolves at
    paint time is the one that counts (1.0.10 cut the first digit of dates
    measured with another font).
    """
    metrics = painter.fontMetrics()
    shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, rect.width()))
    if spans and shown:
        visible = len(shown) if shown == text else len(shown) - 1  # minus "…"
        height = metrics.height()
        top = rect.top() + (rect.height() - height) / 2
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(qcolor(_tk.color("mark"))))
        for start, end in spans:
            end = min(end, visible)
            if start >= end:
                continue
            left = rect.left() + metrics.horizontalAdvance(shown[:start])
            right = rect.left() + metrics.horizontalAdvance(shown[:end])
            painter.drawRoundedRect(
                QRectF(left - 1, top, right - left + 2, height), 3, 3
            )
        painter.restore()
    painter.setPen(qcolor(_tk.color(color_key)))
    painter.drawText(
        rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown
    )
    return metrics.horizontalAdvance(shown)


def draw_kbd(painter: QPainter, right: int, center_y: int, keys: str) -> int:
    """Draw *keys* as a small outlined key ending at x = *right*, the way
    ``QLabel#Kbd`` looks. Return its left edge."""
    painter.save()
    font = QFont(painter.font())
    font.setPixelSize(_tk.FONT_PX_KBD)
    font.setWeight(QFont.Weight.Normal)
    painter.setFont(font)
    width = painter.fontMetrics().horizontalAdvance(keys) + 2 * _KBD_PAD
    top = center_y - KBD_HEIGHT / 2
    box = QRectF(right - width + 0.5, top + 0.5, width - 1, KBD_HEIGHT - 1)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(qcolor(_tk.color("border_strong")), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(box, _tk.RADIUS_SM, _tk.RADIUS_SM)
    painter.setPen(qcolor(_tk.color("text3")))
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, keys)
    painter.restore()
    return right - width


def draw_group_header(
    painter: QPainter,
    rect: QRect,
    base_font: QFont,
    label: str,
    count: int,
    expanded: bool,
) -> None:
    """Draw a list group header in *rect* (``GROUP_HEIGHT`` tall): a chevron,
    the *label*, the *count* and a rule to the right edge."""
    band = QRect(rect.left(), rect.bottom() - GROUP_BAND + 1, rect.width(), GROUP_BAND)
    centre_y = band.center().y()
    chevron = "chevron_down" if expanded else "chevron_right"
    icons.icon(chevron, "text3", 14).paint(
        painter, QRect(rect.left() + _GROUP_CHEVRON_X, centre_y - 7, 14, 14)
    )

    right = band.right() - 16
    x = rect.left() + _GROUP_TEXT_X
    painter.setFont(sized_font(base_font, _tk.FONT_PX_BASE, QFont.Weight.DemiBold))
    x += draw_marked_text(
        painter, QRect(x, band.top(), right - x - 48, GROUP_BAND), label, "text"
    )
    x += 8
    painter.setFont(sized_font(base_font, _tk.FONT_PX_SMALL))
    x += draw_marked_text(
        painter, QRect(x, band.top(), 40, GROUP_BAND), str(count), "text3"
    )
    x += 12
    if x < right:
        painter.setPen(QPen(qcolor(_tk.color("border")), 1))
        painter.drawLine(x, centre_y, right, centre_y)
