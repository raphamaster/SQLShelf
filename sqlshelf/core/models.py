from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Query:
    """A .sql file with its frontmatter metadata."""

    path: Path
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    body: str = ""
    has_frontmatter: bool = True
    created_at: str | None = None
    updated_at: str | None = None
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """One row returned by a search query."""

    query_id: int
    rel_path: str
    title: str
    description: str
    snippet: str
    rank: float
    tags: list[str] = field(default_factory=list)
    # Set by multi-folder search to know which project the result belongs to
    folder: Path | None = None
