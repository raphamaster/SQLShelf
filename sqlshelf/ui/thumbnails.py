"""Thumbnails of the queries: a miniature of the first lines of each file.

The text of a thumbnail is read off the GUI thread. The pixmap is painted on
the GUI thread, with the editor's highlighter and colours, and kept per file
version: a saved query has a new modification time, so it gets a new one.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QRunnable, QThreadPool, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPalette,
    QPixmap,
    QTextDocument,
)

from ..core.dialects import GENERIC
from ..core.preview import read_preview
from . import tokens as _tk
from .highlighter import SqlHighlighter
from .theme import mono_font, qcolor

# Logical px. The page is rendered once at this size and cropped to the card.
THUMB_WIDTH = 260
THUMB_HEIGHT = 120
_FONT_PX = 8
_PADDING = 9
_FADE = 30  # the last lines fade out, so the cut is not abrupt
_TEXT_CACHE = 2000  # previews are about a kilobyte
_PIXMAP_CACHE = 128

# Two threads read ahead of the GUI without crowding the global pool.
_READ_POOL = QThreadPool()
_READ_POOL.setMaxThreadCount(2)

_Key = tuple[str, int]  # (path, modification time)


def render_thumbnail(
    text: str, font: QFont, dpr: float = 1.0, dialect: str = GENERIC.key
) -> QPixmap:
    """The page of *text*: ``THUMB_WIDTH`` × ``THUMB_HEIGHT`` logical px, drawn
    like the editor, coloured as *dialect*, and faded out at the bottom."""
    pixmap = QPixmap(round(THUMB_WIDTH * dpr), round(THUMB_HEIGHT * dpr))
    pixmap.setDevicePixelRatio(dpr)
    page = qcolor(_tk.color("code"))
    pixmap.fill(page)

    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    document.setPlainText(text)
    # Highlighting is otherwise deferred to the event loop.
    SqlHighlighter(document, dialect).rehighlight()

    context = QAbstractTextDocumentLayout.PaintContext()
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Text, qcolor(_tk.color("syn_identifier")))
    context.palette = palette

    painter = QPainter(pixmap)
    painter.translate(_PADDING, _PADDING)
    document.documentLayout().draw(painter, context)
    painter.resetTransform()
    fade = QLinearGradient(0, THUMB_HEIGHT - _FADE, 0, THUMB_HEIGHT)
    clear = QColor(page)
    clear.setAlpha(0)
    fade.setColorAt(0, clear)
    fade.setColorAt(1, page)
    painter.fillRect(QRectF(0, THUMB_HEIGHT - _FADE, THUMB_WIDTH, _FADE), fade)
    painter.end()
    return pixmap


class _ReadSignals(QObject):
    done = Signal(str, int, str)  # (path, modification time, preview text)


class _ReadTask(QRunnable):
    def __init__(self, path: Path, mtime: int) -> None:
        super().__init__()
        self.signals = _ReadSignals()
        self._path = path
        self._mtime = mtime

    def run(self) -> None:
        self.signals.done.emit(str(self._path), self._mtime, read_preview(self._path))


class ThumbnailCache(QObject):
    """Hands out the thumbnail of a file, reading its text in the background.

    :meth:`thumbnail` returns at once: the pixmap if it is ready, otherwise
    None, and ``ready`` fires when the text arrives, so the view paints again.
    """

    ready = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._texts: OrderedDict[_Key, str] = OrderedDict()
        self._pixmaps: OrderedDict[tuple[_Key, float, str], QPixmap] = OrderedDict()
        self._pending: set[_Key] = set()
        self._font: QFont | None = None

    def thumbnail(
        self, path: Path, mtime: int, dpr: float = 1.0, dialect: str = GENERIC.key
    ) -> QPixmap | None:
        """The thumbnail of *path* as of *mtime*, coloured as *dialect*; None
        while it is being read."""
        key = (str(path), mtime)
        text = self._texts.get(key)
        if text is None:
            self._request(key, path)
            return None
        self._texts.move_to_end(key)
        pixmap = self._pixmaps.get((key, dpr, dialect))
        if pixmap is None:
            if self._font is None:
                self._font = mono_font(_FONT_PX)
            pixmap = render_thumbnail(text, self._font, dpr, dialect)
            self._pixmaps[(key, dpr, dialect)] = pixmap
            while len(self._pixmaps) > _PIXMAP_CACHE:
                self._pixmaps.popitem(last=False)
        else:
            self._pixmaps.move_to_end((key, dpr, dialect))
        return pixmap

    def refresh_theme(self) -> None:
        """Forget the pixmaps, which carry the old colours; keep the texts."""
        self._pixmaps.clear()

    def _request(self, key: _Key, path: Path) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        task = _ReadTask(path, key[1])
        task.signals.done.connect(self._on_read)
        _READ_POOL.start(task)

    def _on_read(self, path: str, mtime: int, text: str) -> None:
        key = (path, mtime)
        self._pending.discard(key)
        self._texts[key] = text
        while len(self._texts) > _TEXT_CACHE:
            self._texts.popitem(last=False)
        self.ready.emit()
