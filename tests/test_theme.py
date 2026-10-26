from __future__ import annotations

import json
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from sqlshelf.ui import tokens as _tk
from sqlshelf.ui.theme import (
    app_stylesheet,
    apply_theme,
    build_palette,
    qcolor,
    qss_color,
)

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_themes_have_the_same_keys() -> None:
    assert set(_tk.DARK) == set(_tk.LIGHT)
    assert set(_tk.THEMES) == {"dark", "light"}


def test_legacy_aliases_point_at_existing_tokens() -> None:
    assert set(_tk._LEGACY.values()) <= set(_tk.DARK)


@pytest.mark.parametrize("name", ["dark", "light"])
def test_set_active_palette_repoints_legacy_names(name: str) -> None:
    try:
        _tk.set_active_palette(name)
        palette = _tk.THEMES[name]
        assert _tk.color("accent") == palette["accent"]
        for legacy, key in _tk._LEGACY.items():
            assert getattr(_tk, legacy) == palette[key]
    finally:
        _tk.set_active_palette("dark")


def test_unknown_theme_falls_back_to_dark() -> None:
    try:
        _tk.set_active_palette("solarized")
        assert _tk.ACTIVE_THEME == "dark"
    finally:
        _tk.set_active_palette("dark")


# ---------------------------------------------------------------------------
# Tag colours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("finance", "#4c9aff"),
        ("sales", "#ff7eb6"),
        ("cliente", "#ff6b6b"),
        ("vendas", "#2dd4bf"),
    ],
)
def test_tag_color_is_stable(tag: str, expected: str) -> None:
    # Fixed values: a tag must keep its colour across runs and releases.
    assert _tk.tag_color(tag) == expected


@pytest.mark.parametrize("tag", ["", "a", "relatório", "x" * 200])
def test_tag_color_comes_from_the_palette(tag: str) -> None:
    assert _tk.tag_color(tag) in _tk.TAG_PALETTE
    assert _tk.tag_color(tag) == _tk.tag_color(tag)


# ---------------------------------------------------------------------------
# Diagram colours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "key", "expected"),
    [
        ("schema", "dbo", "#4c9aff"),
        ("schema", "DBO", "#4c9aff"),
        ("schema", "", "#4c9aff"),
        ("tables", "", "#4c9aff"),
        ("schema", "vendas", "#ff8b6b"),
        ("schema", "Vendas", "#ff8b6b"),
        ("schema", "dim", "#ff7eb6"),
        ("schema", "ref", "#8fa3bf"),
        ("temp", "", "#f5a524"),
        ("derived", "", "#b38bff"),
        ("result", "", "#2dd4bf"),
    ],
)
def test_diagram_group_color_is_stable(kind: str, key: str, expected: str) -> None:
    # Fixed values: a schema must keep its colour across runs and releases.
    assert _tk.diagram_group_color(kind, key) == expected


@pytest.mark.parametrize("key", ["sys", "stg", "mart", "x" * 200, "relatório"])
def test_diagram_group_color_is_never_the_accent(key: str) -> None:
    color = _tk.diagram_group_color("schema", key)
    assert color in _tk.DIAGRAM_SCHEMA_PALETTE
    for palette in _tk.THEMES.values():
        assert color.casefold() != palette["accent"].casefold()


@pytest.mark.parametrize("name", ["dark", "light"])
def test_diagram_opacities_are_fractions(name: str) -> None:
    try:
        _tk.set_active_palette(name)
        for key in ("dg_dim", "dg_frame_fill", "dg_frame_border"):
            assert 0.0 < _tk.opacity(key) < 1.0
    finally:
        _tk.set_active_palette("dark")


# ---------------------------------------------------------------------------
# Contrast (WCAG 2.x)
# ---------------------------------------------------------------------------


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fg", ["text", "text2", "text3"])
@pytest.mark.parametrize("bg", ["bg", "panel", "chrome"])
def test_text_contrast_is_at_least_aa(theme: str, fg: str, bg: str) -> None:
    palette = _tk.THEMES[theme]
    assert _contrast(palette[fg], palette[bg]) >= 4.5


# ---------------------------------------------------------------------------
# Qt conversion and the global stylesheet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rgba(255,255,255,.05)", "rgba(255, 255, 255, 13)"),
        ("rgba(10, 222, 153, 0.30)", "rgba(10, 222, 153, 76)"),
        ("rgba(0,0,0,1)", "rgba(0, 0, 0, 255)"),
        ("#0ADE99", "#0ADE99"),
    ],
)
def test_qss_color_converts_alpha_to_0_255(value: str, expected: str) -> None:
    assert qss_color(value) == expected


def test_qcolor_parses_css_rgba() -> None:
    color = qcolor("rgba(10,222,153,.15)")
    assert color.isValid()
    assert (color.red(), color.green(), color.blue(), color.alpha()) == (
        10,
        222,
        153,
        38,
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_stylesheet_uses_qss_alpha_and_no_upper_case(theme: str) -> None:
    qss = app_stylesheet(_tk.THEMES[theme])
    # Every rgba() alpha is an integer 0–255, never a CSS fraction.
    for alpha in re.findall(r"rgba\([^)]*,\s*([^,)]+)\)", qss):
        assert alpha.strip().isdigit()
    assert "uppercase" not in qss.lower()
    assert "text-transform" not in qss


_LOCALES = Path(__file__).resolve().parent.parent / "locales"


@pytest.mark.parametrize("lang", ["en", "pt-BR", "es"])
def test_no_ui_string_is_all_upper_case(lang: str) -> None:
    strings = json.loads(
        (_LOCALES / lang / "translation.json").read_text(encoding="utf-8")
    )
    # Short acronyms (SQL, FTS, UTF) are fine; whole words in capitals are not.
    shouting = {
        key: value
        for key, value in strings.items()
        if any(len(word) >= 4 and word.isupper() for word in value.split())
        and not any(c.islower() for c in value)
    }
    assert shouting == {}


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def test_apply_theme_switches_live() -> None:
    app = _app()
    # Clear any stylesheet left by other tests, or style() would be the
    # stylesheet wrapper and hide the non-Fusion base style.
    app.setStyleSheet("")
    app.setStyle("Windows")
    try:
        apply_theme(app, "light")
        assert app.palette().color(QPalette.ColorRole.Window) == qcolor(
            _tk.LIGHT["chrome"]
        )
        assert _tk.ACCENT == _tk.LIGHT["accent_text"]

        apply_theme(app, "dark")
        assert app.palette().color(QPalette.ColorRole.Window) == qcolor(
            _tk.DARK["chrome"]
        )
        assert app.styleSheet() == app_stylesheet(_tk.DARK)
    finally:
        _tk.set_active_palette("dark")
        app.setStyleSheet("")
    # With the stylesheet cleared, style() is the base style again.
    assert app.style().name().lower() == "fusion"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_build_palette_dims_disabled_text(theme: str) -> None:
    palette = build_palette(_tk.THEMES[theme])
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    assert disabled == qcolor(_tk.THEMES[theme]["text3"])
