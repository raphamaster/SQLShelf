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

    body = content[match.end():].strip()
    return metadata, body, True


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
