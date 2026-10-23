"""The few lines of a query that its thumbnail shows."""

from __future__ import annotations

from pathlib import Path

from .encoding import read_head
from .frontmatter import split_frontmatter

PREVIEW_LINES = 12
PREVIEW_COLUMNS = 80
_TAB_WIDTH = 4


def preview_text(
    content: str, max_lines: int = PREVIEW_LINES, max_columns: int = PREVIEW_COLUMNS
) -> str:
    """The first *max_lines* lines of the SQL in *content*, without its
    frontmatter block, tabs expanded and each line cut at *max_columns*."""
    _metadata, body, _has_frontmatter = split_frontmatter(content)
    lines = [
        line.expandtabs(_TAB_WIDTH).rstrip()[:max_columns]
        for line in body.splitlines()[:max_lines]
    ]
    return "\n".join(lines)


def read_preview(path: Path) -> str:
    """The preview of the file at *path*; empty if it cannot be read.

    Reads only the start of the file, so the size of the query does not matter.
    Never raises, like the rest of the indexing path.
    """
    try:
        return preview_text(read_head(path))
    except (OSError, ValueError):
        return ""
