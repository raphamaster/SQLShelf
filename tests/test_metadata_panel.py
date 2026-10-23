from __future__ import annotations

import gc
import os
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from sqlshelf.core.i18n import set_language
from sqlshelf.ui import metadata_panel as mp
from sqlshelf.ui.metadata_panel import (
    MetadataPanel,
    QueryEdits,
    created_text,
    elide_lines,
    modified_text,
)

PATH = Path("C:/lib/Querys/reports/vendas_por_cliente.sql")


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


@pytest.fixture
def panel() -> Iterator[MetadataPanel]:
    _app()
    set_language("en")
    widget = MetadataPanel()
    widget.resize(480, 600)
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    set_language("en")


def _fill(panel: MetadataPanel, description: str = "Sales per client") -> None:
    panel.set_query(
        "Vendas por cliente", description, ["vendas", "bi"], ["Pedidos", "Clientes"]
    )
    panel.set_path(PATH, datetime(2026, 10, 22, 9, 5).timestamp(), "raphael")


def _visible(panel: MetadataPanel, key: str) -> bool:
    label, value = panel._rows[key]
    return not label.isHidden() and not value.isHidden()


# ── Pure helpers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mtime", "author", "expected"),
    [
        (datetime(2026, 10, 22, 9, 5).timestamp(), "ana", "22/10/2026 09:05 · ana"),
        (datetime(2026, 1, 2, 23, 59).timestamp(), "", "02/01/2026 23:59"),
        (None, "ana", "ana"),
        (None, "", ""),
        (0.0, "", ""),
    ],
)
def test_modified_text(mtime: float | None, author: str, expected: str) -> None:
    assert modified_text(mtime, author) == expected


def test_elide_lines_keeps_text_that_fits() -> None:
    _app()
    font = QFont()
    assert elide_lines("short", font, 400, 2) == "short"
    assert elide_lines("", font, 400, 2) == ""
    assert elide_lines("anything", font, 0, 2) == "anything"


def test_elide_lines_cuts_to_max_lines_with_an_ellipsis() -> None:
    _app()
    font = QFont()
    text = " ".join(["word"] * 200)
    width = 120
    shown = elide_lines(text, font, width, 2)
    lines = shown.split("\n")
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    metrics = QFontMetrics(font)
    assert all(metrics.horizontalAdvance(line) <= width for line in lines)


# ── Read mode ──────────────────────────────────────────────────────────────


def test_header_shows_title_file_name_and_folder(panel: MetadataPanel) -> None:
    _fill(panel)
    assert panel._title_label.full_text() == "Vendas por cliente"
    assert panel._file_name_label.full_text() == "vendas_por_cliente.sql"
    assert panel._path_btn.text() == str(PATH.parent)
    assert str(PATH) in panel._path_btn.toolTip()
    assert panel._modified_label.text() == "22/10/2026 09:05 · raphael"
    for key in ("tags", "tables", "location", "modified"):
        assert _visible(panel, key)


def test_long_title_is_elided_with_the_full_text_as_tooltip(
    panel: MetadataPanel,
) -> None:
    title = " ".join(["Relatório"] * 40)
    panel.set_query(title, "", [], [])
    shown = panel._title_label.text()
    assert shown != title
    assert shown.endswith("…")
    assert len(shown.split("\n")) <= 2
    assert panel._title_label.toolTip() == title


def test_missing_description_shows_a_muted_placeholder(panel: MetadataPanel) -> None:
    _fill(panel, description="   ")
    assert (
        panel._desc_label.full_text()
        == "No description. Use Edit to document this query."
    )
    assert panel._desc_label.property("empty") is True

    _fill(panel, description="Sales per client")
    assert panel._desc_label.full_text() == "Sales per client"
    assert panel._desc_label.property("empty") is False


def test_empty_rows_are_hidden(panel: MetadataPanel) -> None:
    panel.set_query("Bare", "", [], [])
    panel.set_path(PATH)
    assert not _visible(panel, "tags")
    assert not _visible(panel, "tables")
    assert _visible(panel, "location")
    assert not _visible(panel, "modified")


def test_tables_are_sorted_case_insensitively(panel: MetadataPanel) -> None:
    panel.set_query("q", "", [], ["pedidos", "Clientes", "ItensPedido"])
    names = [chip.text() for chip in panel._tables_area.chips()]
    assert names == ["Clientes", "ItensPedido", "pedidos"]


def test_tag_pill_click_requests_the_tag(panel: MetadataPanel) -> None:
    _fill(panel)
    spy = QSignalSpy(panel.tag_requested)
    pills = panel._tags_area.chips()
    assert [pill.text() for pill in pills] == ["vendas", "bi"]
    assert pills[0].toolTip() == "Show queries tagged vendas"
    pills[1].click()
    assert spy.count() == 1
    assert spy.at(0) == ["bi"]


def test_table_chip_click_navigates_to_the_table(panel: MetadataPanel) -> None:
    _fill(panel)
    spy = QSignalSpy(panel.navigate_requested)
    panel._tables_area.chips()[0].click()
    assert spy.count() == 1
    assert spy.at(0) == ["Clientes"]


def test_location_click_reveals_the_file(panel: MetadataPanel) -> None:
    _fill(panel)
    spy = QSignalSpy(panel.reveal_requested)
    panel._path_btn.click()
    assert spy.count() == 1


# ── Actions ────────────────────────────────────────────────────────────────


def test_actions_start_disabled_until_the_sql_is_loaded(panel: MetadataPanel) -> None:
    _fill(panel)
    buttons = (panel._copy_btn, panel._edit_btn, panel._open_btn)
    assert not any(button.isEnabled() for button in buttons)
    assert not panel._select_all_act.isEnabled()

    panel.set_actions_enabled(True)
    assert all(button.isEnabled() for button in buttons)
    assert panel._select_all_act.isEnabled()


@pytest.mark.parametrize(
    ("button", "signal"),
    [
        ("_copy_btn", "copy_requested"),
        ("_edit_btn", "edit_requested"),
        ("_open_btn", "open_requested"),
        ("_star_btn", "favorite_toggled"),
    ],
)
def test_action_buttons_emit_their_signal(
    panel: MetadataPanel, button: str, signal: str
) -> None:
    _fill(panel)
    panel.set_actions_enabled(True)
    spy = QSignalSpy(getattr(panel, signal))
    getattr(panel, button).click()
    assert spy.count() == 1


def test_copy_sql_is_the_primary_action(panel: MetadataPanel) -> None:
    assert panel._copy_btn.property("primary") is True
    assert panel._edit_btn.property("primary") is False
    assert panel._copy_btn.text() == "Copy SQL"


def test_more_menu_copies_paths_to_the_clipboard(panel: MetadataPanel) -> None:
    _fill(panel)
    spy = QSignalSpy(panel.path_copied)
    clipboard = QApplication.clipboard()

    panel._copy_path_act.trigger()
    assert clipboard.text() == str(PATH)
    panel._copy_folder_act.trigger()
    assert clipboard.text() == str(PATH.parent)
    assert spy.count() == 2
    assert spy.at(1) == [str(PATH.parent)]


def test_more_menu_reveal_and_select_all(panel: MetadataPanel) -> None:
    _fill(panel)
    panel.set_actions_enabled(True)
    reveal = QSignalSpy(panel.reveal_requested)
    select_all = QSignalSpy(panel.select_all_requested)
    panel._reveal_act.trigger()
    panel._select_all_act.trigger()
    assert reveal.count() == 1
    assert select_all.count() == 1


def test_path_actions_are_disabled_without_a_file(panel: MetadataPanel) -> None:
    for act in (panel._copy_path_act, panel._copy_folder_act, panel._reveal_act):
        assert not act.isEnabled()
    _fill(panel)
    for act in (panel._copy_path_act, panel._copy_folder_act, panel._reveal_act):
        assert act.isEnabled()


@pytest.mark.parametrize(
    ("on_windows", "label"), [(True, "Open in SSMS"), (False, "Open externally")]
)
def test_open_button_names_what_it_opens(
    panel: MetadataPanel,
    monkeypatch: pytest.MonkeyPatch,
    on_windows: bool,
    label: str,
) -> None:
    monkeypatch.setattr(mp, "_opens_in_ssms", lambda: on_windows)
    panel.retranslate_ui()
    assert panel._open_btn.text() == label


def test_star_tooltip_follows_the_favorite_state(panel: MetadataPanel) -> None:
    assert panel._star_btn.toolTip() == "Add to favorites"
    panel.set_favorite(True)
    assert panel.is_favorite()
    assert panel._star_btn.toolTip() == "Remove from favorites"


# ── Edit mode, language, clear ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 3, 9), "09/03/2026"),
        (datetime(2026, 3, 9, 14, 30), "09/03/2026"),
        ("2026-03-09", "09/03/2026"),
        ("2026-03-09T14:30:00", "09/03/2026"),
        ("last spring", "last spring"),
        ("", "—"),
        (None, "—"),
    ],
)
def test_created_text(value: object, expected: str) -> None:
    assert created_text(value) == expected


def _start_editing(panel: MetadataPanel) -> None:
    _fill(panel)
    panel.start_editing(
        "Vendas por cliente",
        "Sales per client",
        ["vendas", "bi"],
        author="raphael",
        created=date(2026, 3, 9),
    )


def test_start_editing_fills_the_form_and_hides_the_read_view(
    panel: MetadataPanel,
) -> None:
    _start_editing(panel)
    assert panel.is_editing()
    assert panel._ro_widget.isHidden()
    assert not panel._edit_widget.isHidden()
    assert panel._title_edit.text() == "Vendas por cliente"
    assert panel._desc_edit.toPlainText() == "Sales per client"
    assert panel._tag_field.tags() == ["vendas", "bi"]
    assert panel._author_edit.text() == "raphael"
    assert panel._created_edit.text() == "09/03/2026"
    assert panel._created_edit.isReadOnly()

    panel.set_edit_mode(False)
    assert not panel.is_editing()
    assert not panel._ro_widget.isHidden()
    assert panel._edit_widget.isHidden()


def test_edited_values_are_trimmed(panel: MetadataPanel) -> None:
    _start_editing(panel)
    panel._title_edit.setText("  New title ")
    panel._desc_edit.setPlainText("New description\n")
    panel._author_edit.setText("  ana ")
    panel._tag_field.input().setText("pending")
    assert panel.get_edited_values() == QueryEdits(
        title="New title",
        description="New description",
        tags=["vendas", "bi", "pending"],
        author="ana",
    )


def test_banner_buttons_emit_save_and_cancel(panel: MetadataPanel) -> None:
    _start_editing(panel)
    assert panel._banner_title.text() == "Editing"
    assert ".sql" in panel._banner_subtitle.full_text()
    assert panel._save_btn.label_text() == "Save"
    assert panel._save_btn.property("primary") is True
    assert panel._cancel_btn.toolTip() == "Discard changes (Esc)"

    save = QSignalSpy(panel.save_requested)
    cancel = QSignalSpy(panel.cancel_requested)
    panel._save_btn.click()
    panel._cancel_btn.click()
    assert save.count() == 1
    assert cancel.count() == 1


def test_saving_disables_the_banner_buttons(panel: MetadataPanel) -> None:
    _start_editing(panel)
    panel.set_saving(True)
    assert not panel._save_btn.isEnabled()
    assert not panel._cancel_btn.isEnabled()
    panel.set_saving(False)
    assert panel._save_btn.isEnabled()
    assert panel._cancel_btn.isEnabled()


def test_description_is_three_lines_tall(panel: MetadataPanel) -> None:
    _start_editing(panel)
    line = panel._desc_edit.fontMetrics().lineSpacing()
    height = panel._desc_edit.height()
    assert 3 * line < height < 4 * line + 16


def test_form_labels_sit_above_their_fields(panel: MetadataPanel) -> None:
    _start_editing(panel)
    labels = panel._edit_labels
    assert [labels[key].text() for key in labels] == [
        "Title",
        "Description",
        "Tags",
        "Author",
        "Created",
    ]
    assert labels["title"].buddy() is panel._title_edit
    assert labels["title"].y() < panel._title_edit.y()
    assert panel._author_edit.y() == panel._created_edit.y()


def test_retranslate_to_portuguese(panel: MetadataPanel) -> None:
    _fill(panel, description="")
    set_language("pt-BR")
    panel.retranslate_ui()
    assert panel._copy_btn.text() == "Copiar SQL"
    assert panel._edit_btn.text() == "Editar"
    assert panel._rows["tables"][0].text() == "Tabelas"
    assert panel._rows["location"][0].text() == "Local"
    assert panel._desc_label.full_text().startswith("Sem descrição")
    assert panel._tags_area.chips()[0].toolTip() == "Mostrar consultas com a tag vendas"
    assert panel._save_btn.label_text() == "Salvar"
    assert panel._banner_title.text() == "Editando"
    assert panel._edit_labels["created"].text() == "Criado"
    assert panel._tag_field.input().placeholderText() == "Adicionar tag…"


def test_clear_resets_everything(panel: MetadataPanel) -> None:
    _fill(panel)
    panel.set_favorite(True)
    panel.set_actions_enabled(True)
    panel.clear()
    assert panel._title_label.full_text() == ""
    assert panel._file_name_label.full_text() == ""
    assert panel._tags_area.chips() == []
    assert panel._tables_area.chips() == []
    assert not panel.is_favorite()
    assert not panel._copy_btn.isEnabled()
    for key in ("tags", "tables", "location", "modified"):
        assert not _visible(panel, key)


def test_a_chip_can_replace_the_chips_from_its_own_click(
    panel: MetadataPanel,
) -> None:
    _fill(panel)
    panel.tag_requested.connect(lambda _tag: panel.set_query("Other", "", ["z"], []))
    panel._tags_area.chips()[0].click()
    assert [pill.text() for pill in panel._tags_area.chips()] == ["z"]


def test_panel_can_go_while_removed_chips_await_deletion() -> None:
    _app()
    widget = MetadataPanel()
    widget.show()
    _fill(widget)
    widget.clear()
    widget.close()
    del widget  # the wrapper goes before the deferred deletes run
    gc.collect()
    loop = QEventLoop()
    QTimer.singleShot(20, loop.quit)
    loop.exec()  # used to abort the process
