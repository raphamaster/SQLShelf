from __future__ import annotations

import json
from pathlib import Path

_LOCALE_DIR = Path(__file__).parent.parent.parent / "locales"
_current_lang: str = "en"
_translations: dict[str, str] = {}
_en_fallback: dict[str, str] = {}


def _load_locale(lang: str) -> dict[str, str]:
    path = _LOCALE_DIR / lang / "translation.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_language(lang: str) -> None:
    """Load translation files for *lang* and set it as active language."""
    global _current_lang, _translations, _en_fallback
    _current_lang = lang
    _en_fallback = _load_locale("en")
    _translations = _load_locale(lang) if lang != "en" else _en_fallback


def get_language() -> str:
    """Return the currently active language code."""
    return _current_lang


def tr(key: str, **kwargs: object) -> str:
    """Look up *key* in the active language, falling back to English, then the key itself."""
    text = _translations.get(key) or _en_fallback.get(key) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


def ntr(singular_key: str, plural_key: str, count: int, **kwargs: object) -> str:
    """Select singular or plural translation based on *count*."""
    key = singular_key if count == 1 else plural_key
    return tr(key, count=count, **kwargs)


def available_languages() -> list[tuple[str, str]]:
    """Return (code, display_name) pairs for every installed language pack."""
    if not _LOCALE_DIR.exists():
        return [("en", "English")]
    langs: list[tuple[str, str]] = []
    for d in sorted(_LOCALE_DIR.iterdir()):
        if not d.is_dir():
            continue
        t = _load_locale(d.name)
        name = t.get("_name", d.name)
        langs.append((d.name, name))
    return langs or [("en", "English")]
