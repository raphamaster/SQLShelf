from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .encoding import read_text, write_text

FRONTMATTER_PATTERN = re.compile(r"^/\*\s*---\s*\n(.*?)\n---\s*\*/\s*\n?", re.DOTALL)


def read_sql_file(path: Path) -> tuple[dict, str, bool]:
    """Read a .sql file and separate YAML frontmatter from the SQL body.

    Returns (metadata, body, has_frontmatter).
    Tolerant: bad YAML → metadata={}, body=full content, has_frontmatter=False.
    Never raises — safe to call during indexing.
    """
    try:
        content, _enc, _bom = read_text(path)
    except Exception:
        return {}, "", False
    return split_frontmatter(content)


def split_frontmatter(content: str) -> tuple[dict, str, bool]:
    """Separate the YAML frontmatter block at the top of *content* from the SQL.

    Returns (metadata, body, has_frontmatter), with the same tolerance as
    read_sql_file: bad YAML leaves the whole text as the body. Never raises.
    """
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content.strip(), False

    raw_yaml = match.group(1)
    try:
        metadata = yaml.safe_load(raw_yaml) or {}
        if not isinstance(metadata, dict):
            return {}, content.strip(), False
    except yaml.YAMLError:
        return {}, content.strip(), False

    body = content[match.end() :].strip()
    return metadata, body, True


def apply_edits(
    metadata: dict[str, Any],
    *,
    title: str,
    description: str,
    tags: list[str],
    author: str,
) -> dict[str, Any]:
    """*metadata* with the edit form's fields; every other key keeps its place.

    An empty author removes the key rather than writing ``author: ''``.
    """
    meta = dict(metadata)
    meta["title"] = title
    meta["description"] = description
    meta["tags"] = list(tags)
    if author:
        meta["author"] = author
    else:
        meta.pop("author", None)
    return meta


def write_sql_file(path: Path, metadata: dict[str, Any], body: str) -> None:
    """Write (or overwrite) a .sql file with a YAML frontmatter block.

    - Preserves UTF-8 BOM if the original file had one; writes UTF-8 otherwise.
    - Always sets metadata['updated'] to today's date.
    - Preserves all keys in *metadata* that the UI did not edit.
    """
    original_encoding = "utf-8"
    had_bom = False
    if path.exists():
        try:
            _, original_encoding, had_bom = read_text(path)
        except Exception:
            pass

    meta = dict(metadata)
    meta["updated"] = date.today()

    yaml_text = yaml.safe_dump(
        meta, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).rstrip()

    content = f"/* ---\n{yaml_text}\n--- */\n\n{body}\n"
    write_text(path, content, original_encoding, had_bom)
