from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QTextFormat
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from .theme.tokens import (
    EDITOR_LINE_HL,
    GUTTER_BG,
    GUTTER_NUM_CURRENT,
    GUTTER_NUM_INACTIVE,
)


class _LineNumberArea(QWidget):
    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.line_number_area_paint_event(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with a line-number gutter and current-line highlight.

    Works in both read-only and edit mode — the cursor can always be moved
    and the active line is always highlighted.
    """

    _GUTTER_BG       = QColor(GUTTER_BG)
    _NUM_INACTIVE    = QColor(GUTTER_NUM_INACTIVE)
    _NUM_CURRENT     = QColor(GUTTER_NUM_CURRENT)
    _LINE_HIGHLIGHT  = QColor(EDITOR_LINE_HL)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gutter = _LineNumberArea(self)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_gutter_width(0)
        self._highlight_current_line()

    # ------------------------------------------------------------------
    # Gutter width / update
    # ------------------------------------------------------------------

    def line_number_area_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    # ------------------------------------------------------------------
    # Gutter paint
    # ------------------------------------------------------------------

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._GUTTER_BG)

        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        fm = self.fontMetrics()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(
                    self._NUM_CURRENT if block_num == current else self._NUM_INACTIVE
                )
                painter.drawText(
                    0, top,
                    self._gutter.width() - 4,
                    fm.height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block_num + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1

    # ------------------------------------------------------------------
    # Current-line highlight
    # ------------------------------------------------------------------

    def _highlight_current_line(self) -> None:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(self._LINE_HIGHLIGHT)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])
