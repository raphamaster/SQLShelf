from __future__ import annotations

import re
from pathlib import Path

_TEMPLATES_DIR = Path.home() / ".sqlshelf" / "templates"

# Matches {{param_name}} placeholders
_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


def get_templates_dir() -> Path:
    """Return (and create if needed) the user-level templates folder."""
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    return _TEMPLATES_DIR


def list_templates() -> list[Path]:
    """Return all .sql template files sorted by name."""
    d = get_templates_dir()
    return sorted(d.glob("*.sql"), key=lambda p: p.stem.lower())


def extract_params(template_body: str) -> list[str]:
    """Return unique parameter names found in the template, preserving order."""
    seen: set[str] = set()
    params: list[str] = []
    for m in _PARAM_RE.finditer(template_body):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            params.append(name)
    return params


def apply_template(template_body: str, params: dict[str, str]) -> str:
    """Substitute {{param}} placeholders with the supplied values."""
    def _replace(m: re.Match) -> str:  # type: ignore[type-arg]
        return params.get(m.group(1), m.group(0))

    return _PARAM_RE.sub(_replace, template_body)
