from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.preview import preview_text, read_preview

FRONTMATTER = "/* ---\ntitle: Pedidos\ntags: [bi]\n--- */\n\n"


def test_the_frontmatter_is_not_previewed() -> None:
    assert preview_text(FRONTMATTER + "SELECT 1\nFROM t") == "SELECT 1\nFROM t"


def test_a_file_without_frontmatter_previews_from_its_first_line() -> None:
    assert preview_text("\n\nSELECT 1") == "SELECT 1"


@pytest.mark.parametrize(("lines", "expected"), [(3, 3), (12, 12), (50, 12)])
def test_the_preview_is_a_few_lines(lines: int, expected: int) -> None:
    content = "\n".join(f"SELECT {n}" for n in range(lines))
    assert len(preview_text(content).splitlines()) == expected


def test_tabs_become_spaces_and_the_line_end_is_trimmed() -> None:
    assert preview_text("SELECT\n\ta,   \n\tb") == "SELECT\n    a,\n    b"


def test_a_long_line_is_cut() -> None:
    assert preview_text("x" * 500, max_columns=20) == "x" * 20


def test_nothing_to_preview() -> None:
    assert preview_text("") == ""
    assert preview_text(FRONTMATTER) == ""


def test_read_preview_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "q.sql"
    path.write_text(FRONTMATTER + "SELECT relatório\nFROM t", encoding="utf-8")
    assert read_preview(path) == "SELECT relatório\nFROM t"


def test_an_unreadable_file_previews_as_empty(tmp_path: Path) -> None:
    assert read_preview(tmp_path / "missing.sql") == ""
    assert read_preview(tmp_path) == ""  # a directory
