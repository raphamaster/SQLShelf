from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core import config as cfg


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(cfg, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_CONFIG_FILE", config_file)
    return config_file


def test_layout_defaults() -> None:
    assert cfg.get_splitter_sizes() is None
    assert cfg.get_panel_visible() is True
    assert cfg.get_view_mode() == "details"
    assert cfg.get_group_mode() == "date"
    assert cfg.get_search_group_mode() == "folder"
    assert cfg.get_sort_key() == "mtime_desc"
    assert cfg.get_show_menu_bar() is False
    assert cfg.get_details_tab() == "sql"
    assert cfg.get_diagram_window_geometry() is None
    assert cfg.get_diagram_legend() is True


def test_layout_round_trip() -> None:
    cfg.set_splitter_sizes([240, 600, 500])
    cfg.set_panel_visible(False)
    cfg.set_view_mode("content")
    cfg.set_group_mode("folder")
    cfg.set_search_group_mode("none")
    cfg.set_sort_key("name_asc")
    cfg.set_show_menu_bar(True)
    cfg.set_details_tab("diagram")
    cfg.set_diagram_window_geometry("AdnQywADAAA=")
    cfg.set_diagram_legend(False)

    assert cfg.get_splitter_sizes() == [240, 600, 500]
    assert cfg.get_panel_visible() is False
    assert cfg.get_view_mode() == "content"
    assert cfg.get_group_mode() == "folder"
    assert cfg.get_search_group_mode() == "none"
    assert cfg.get_sort_key() == "name_asc"
    assert cfg.get_show_menu_bar() is True
    assert cfg.get_details_tab() == "diagram"
    assert cfg.get_diagram_window_geometry() == "AdnQywADAAA="
    assert cfg.get_diagram_legend() is False


@pytest.mark.parametrize("mode", ["details", "content", "grid"])
def test_every_view_mode_round_trips(mode: str) -> None:
    cfg.set_view_mode(mode)

    assert cfg.get_view_mode() == mode


@pytest.mark.parametrize(
    ("setter", "getter", "default"),
    [
        (cfg.set_view_mode, cfg.get_view_mode, "details"),
        (cfg.set_group_mode, cfg.get_group_mode, "date"),
        (cfg.set_search_group_mode, cfg.get_search_group_mode, "folder"),
        (cfg.set_sort_key, cfg.get_sort_key, "mtime_desc"),
        (cfg.set_details_tab, cfg.get_details_tab, "sql"),
    ],
)
def test_unknown_choices_are_ignored(setter, getter, default) -> None:
    setter("bogus")
    assert getter() == default


@pytest.mark.parametrize(
    "stored",
    ['{"splitter_sizes": [1, 2]}', '{"splitter_sizes": [1, -2, 3]}', "{bad json"],
)
def test_bad_splitter_sizes_fall_back_to_none(
    isolated_config: Path, stored: str
) -> None:
    isolated_config.write_text(stored, encoding="utf-8")
    assert cfg.get_splitter_sizes() is None


@pytest.mark.parametrize(
    "stored",
    ['{"diagram_window_geometry": 12}', '{"diagram_window_geometry": ""}', "{bad"],
)
def test_bad_diagram_window_geometry_falls_back_to_none(
    isolated_config: Path, stored: str
) -> None:
    isolated_config.write_text(stored, encoding="utf-8")
    assert cfg.get_diagram_window_geometry() is None
