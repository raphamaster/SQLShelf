from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QRegularExpression, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QPainter,
    QSyntaxHighlighter,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from .theme import tokens as _tk
from .theme.tokens import (
    EDITOR_LINE_HL,
    EDITOR_OCCURRENCE_BG,
    EDITOR_OCCURRENCE_FG,
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
    _OCCURRENCE_BG   = QColor(EDITOR_OCCURRENCE_BG)
    _OCCURRENCE_FG   = QColor(EDITOR_OCCURRENCE_FG)

    # Minimum token length to trigger occurrence highlighting.
    _MIN_OCCURRENCE_LEN = 2

    # Hard cap on how many occurrences to highlight. Without this, landing the
    # cursor on a common token (e.g. a keyword like SELECT right after loading
    # a large file) scans and formats every occurrence in the whole document —
    # thousands of ExtraSelections on a large script, freezing the UI thread.
    _MAX_OCCURRENCES = 500

    # Occurrence search+format is a full-document scan. cursorPositionChanged
    # fires on every keystroke/arrow-key/click, so recomputing it inline made
    # typing and navigating inside a large file feel laggy even after capping
    # the match count. Debounce it — only the cheap current-line highlight
    # (below) updates immediately; occurrences catch up once the cursor settles.
    _OCCURRENCE_DEBOUNCE_MS = 150

    # QSyntaxHighlighter processes every block synchronously when text is put
    # in the document.  Past this limit, keeping it attached makes merely
    # opening a query block the GUI for a noticeable amount of time.  Large
    # scripts remain fully viewable/editable; only decorative whole-document
    # syntax/occurrence highlighting is skipped.
    _MAX_HIGHLIGHT_LINES = 1000
    _MAX_HIGHLIGHT_CHARS = 200_000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gutter = _LineNumberArea(self)
        self._loading = False
        self._large_document = False
        self._syntax_highlighter: QSyntaxHighlighter | None = None
        self._occurrence_selections: list[QTextEdit.ExtraSelection] = []

        self._occurrence_timer = QTimer(self)
        self._occurrence_timer.setSingleShot(True)
        self._occurrence_timer.setInterval(self._OCCURRENCE_DEBOUNCE_MS)
        self._occurrence_timer.timeout.connect(self._recompute_occurrences)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._on_cursor_or_selection_changed)
        self.selectionChanged.connect(self._on_cursor_or_selection_changed)

        self._update_gutter_width(0)
        self._apply_selections()

    def set_syntax_highlighter(self, highlighter: QSyntaxHighlighter) -> None:
        """Register the editor highlighter so large loads can suspend it."""
        self._syntax_highlighter = highlighter

    def setPlainText(self, text: str) -> None:
        # The cursor resets to position 0 as a side effect of loading new
        # content, which would otherwise trigger occurrence highlighting for
        # whatever token happens to be first in the file (e.g. SELECT) — a
        # full-document scan+format the user never asked for. Suppress it for
        # this reset only; the current-line highlight still applies below.
        self._loading = True
        self._large_document = (
            len(text) > self._MAX_HIGHLIGHT_CHARS
            or text.count("\n") + 1 > self._MAX_HIGHLIGHT_LINES
        )
        self._occurrence_timer.stop()
        self._occurrence_selections = []
        highlighter = self._syntax_highlighter
        if highlighter is not None:
            # Detaching before insertion avoids an expensive highlightBlock()
            # call for every line while QPlainTextEdit builds the document.
            highlighter.setDocument(None)
        try:
            super().setPlainText(text)
        finally:
            if highlighter is not None and not self._large_document:
                highlighter.setDocument(self.document())
            self._loading = False

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

    def refresh_theme(self) -> None:
        CodeEditor._GUTTER_BG      = QColor(_tk.GUTTER_BG)
        CodeEditor._NUM_INACTIVE   = QColor(_tk.GUTTER_NUM_INACTIVE)
        CodeEditor._NUM_CURRENT    = QColor(_tk.GUTTER_NUM_CURRENT)
        CodeEditor._LINE_HIGHLIGHT = QColor(_tk.EDITOR_LINE_HL)
        CodeEditor._OCCURRENCE_BG  = QColor(_tk.EDITOR_OCCURRENCE_BG)
        CodeEditor._OCCURRENCE_FG  = QColor(_tk.EDITOR_OCCURRENCE_FG)
        if self._syntax_highlighter is not None and not self._large_document:
            self._syntax_highlighter.setDocument(self.document())
        self._recompute_occurrences()
        self._gutter.update()

    def _token_at_cursor(self) -> str:
        """Return the word under the cursor, or the selected text if it is a
        single-token selection (no whitespace).  Returns "" when nothing
        qualifies for occurrence highlighting."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText().strip()
            if len(text) >= self._MIN_OCCURRENCE_LEN and " " not in text and "\n" not in text:
                return text
            return ""
        # No explicit selection — use word under cursor.
        tmp = QTextCursor(cursor)
        tmp.select(QTextCursor.SelectionType.WordUnderCursor)
        word = tmp.selectedText()
        if len(word) >= self._MIN_OCCURRENCE_LEN:
            return word
        return ""

    def _on_cursor_or_selection_changed(self) -> None:
        # Cheap part runs immediately so the current-line highlight always
        # tracks the cursor with no lag. The expensive part (full-document
        # occurrence search) is debounced below.
        self._apply_selections()
        if self._loading:
            return
        self._occurrence_timer.start()

    def _apply_selections(self) -> None:
        """Push current-line highlight + the last computed occurrence highlights."""
        line_sel = QTextEdit.ExtraSelection()
        line_sel.format.setBackground(self._LINE_HIGHLIGHT)
        line_sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line_sel.cursor = self.textCursor()
        line_sel.cursor.clearSelection()
        self.setExtraSelections([line_sel, *self._occurrence_selections])

    def _recompute_occurrences(self) -> None:
        """Full-document scan for the token at the cursor. Debounced — see
        _OCCURRENCE_DEBOUNCE_MS — since this is too costly to run on every
        single cursor/selection change in a large file."""
        selections: list[QTextEdit.ExtraSelection] = []
        if self._large_document:
            self._occurrence_selections = selections
            self._apply_selections()
            return
        token = self._token_at_cursor()
        if token:
            pattern = QRegularExpression(
                rf"\b{re.escape(token)}\b",
                QRegularExpression.PatternOption.CaseInsensitiveOption,
            )
            doc = self.document()
            found = doc.find(pattern, 0)
            occurrences = 0
            while not found.isNull() and occurrences < self._MAX_OCCURRENCES:
                occ = QTextEdit.ExtraSelection()
                occ.format.setBackground(self._OCCURRENCE_BG)
                occ.format.setForeground(self._OCCURRENCE_FG)
                occ.cursor = found
                selections.append(occ)
                occurrences += 1
                found = doc.find(pattern, found)

        self._occurrence_selections = selections
        self._apply_selections()

    # ------------------------------------------------------------------
    # Token navigation
    # ------------------------------------------------------------------

    def navigate_to_token(self, token: str) -> bool:
        """Move the cursor to the first whole-word occurrence of *token*.

        Search is case-insensitive. The cursor lands with the word selected so
        that all other occurrences are highlighted automatically.
        Returns True if found.
        """
        pattern = QRegularExpression(
            rf"\b{re.escape(token)}\b",
            QRegularExpression.PatternOption.CaseInsensitiveOption,
        )
        cursor = self.document().find(pattern, 0)
        if cursor.isNull():
            return False
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self.setFocus()
        # Explicit one-off action (e.g. clicking a table/column chip) — give
        # immediate feedback instead of waiting out the debounce interval.
        self._occurrence_timer.stop()
        self._recompute_occurrences()
        return True
