from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from sqlshelf.ui.code_editor import CodeEditor


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


TEXT = "-- 📊 totals\nSELECT * FROM vendas.Pedidos p -- ok"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (TEXT.index("vendas"), TEXT.index(" p ")),
        (0, 4),
        (TEXT.index("totals"), len(TEXT)),
        (len(TEXT) - 2, len(TEXT) + 10),  # past the end: up to the end
    ],
)
def test_select_range_counts_as_python_does(start: int, end: int) -> None:
    _app()
    editor = CodeEditor()
    editor.setPlainText(TEXT)
    editor.select_range(start, end)
    # The cursor's selection ends lines with U+2029.
    selected = editor.textCursor().selectedText().replace(" ", "\n")
    assert selected == TEXT[start:end]
