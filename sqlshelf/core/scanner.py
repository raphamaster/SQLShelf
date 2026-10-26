from __future__ import annotations

from pathlib import Path

from .dialects import normalize
from .frontmatter import read_sql_file
from .models import Query


def display_fields(path: Path, metadata: dict) -> tuple[str, str, list[str]]:
    """Title, description and tags of a query as the index stores them.

    The title falls back to the file stem; a missing or malformed field
    becomes empty rather than raising.
    """
    title = metadata.get("title") or path.stem
    description = metadata.get("description") or ""
    tags = metadata.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    return str(title), str(description).strip(), [str(t) for t in tags]


def scan_file(path: Path) -> Query | None:
    """Parse a single .sql file into a Query, or None if it is not indexable.

    Reading one file is orders of magnitude cheaper than rescanning a whole
    folder, so callers that already know which file changed must use this.
    """
    if path.suffix.lower() != ".sql" or ".sqlshelf" in path.parts:
        return None
    if not path.is_file():
        return None

    metadata, body, has_frontmatter = read_sql_file(path)
    title, description, tags = display_fields(path, metadata)

    created_raw = metadata.get("created")
    updated_raw = metadata.get("updated")

    return Query(
        path=path,
        title=title,
        description=description,
        tags=tags,
        body=body,
        has_frontmatter=has_frontmatter,
        created_at=str(created_raw) if created_raw is not None else None,
        updated_at=str(updated_raw) if updated_raw is not None else None,
        dialect=normalize(metadata.get("dialect")),
    )


def scan_folder(folder: Path) -> list[Query]:
    """Recursively scan *folder* for .sql files and return Query objects.

    Files without frontmatter are indexed normally: title = filename stem.
    The .sqlshelf/ subdirectory is always skipped.
    """
    queries: list[Query] = []

    for sql_path in sorted(folder.rglob("*.sql")):
        if ".sqlshelf" in sql_path.parts:
            continue
        query = scan_file(sql_path)
        if query is not None:
            queries.append(query)

    return queries
