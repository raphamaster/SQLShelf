from __future__ import annotations

from pathlib import Path

from .frontmatter import read_sql_file
from .models import Query


def scan_folder(folder: Path) -> list[Query]:
    """Recursively scan *folder* for .sql files and return Query objects.

    Files without frontmatter are indexed normally: title = filename stem.
    The .sqlshelf/ subdirectory is always skipped.
    """
    queries: list[Query] = []

    for sql_path in sorted(folder.rglob("*.sql")):
        if ".sqlshelf" in sql_path.parts:
            continue

        metadata, body, has_frontmatter = read_sql_file(sql_path)

        title = metadata.get("title") or sql_path.stem
        description = (metadata.get("description") or "").strip()
        tags = metadata.get("tags") or []
        if not isinstance(tags, list):
            tags = []

        created_raw = metadata.get("created")
        updated_raw = metadata.get("updated")

        queries.append(
            Query(
                path=sql_path,
                title=str(title),
                description=str(description),
                tags=[str(t) for t in tags],
                body=body,
                has_frontmatter=has_frontmatter,
                created_at=str(created_raw) if created_raw is not None else None,
                updated_at=str(updated_raw) if updated_raw is not None else None,
            )
        )

    return queries
