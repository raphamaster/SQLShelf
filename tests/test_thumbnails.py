from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from sqlshelf.ui import thumbnails as th
from sqlshelf.ui import tokens as _tk
from sqlshelf.ui.theme import mono_font, qcolor


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _pump(ms: int) -> None:
    # QEventLoop.exec releases the GIL while it waits; QSignalSpy.wait and
    # QTest.qWait do not, which starves the thread that reads the previews.
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        _pump(20)


@pytest.fixture
def cache() -> Iterator[th.ThumbnailCache]:
    _app()
    thumbnails = th.ThumbnailCache()
    yield thumbnails
    thumbnails.deleteLater()


def _ink(image: QImage, rows: range, page: QColor) -> int:
    """How many pixels of *rows* differ visibly from the page colour."""
    count = 0
    for y in rows:
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            gap = max(
                abs(pixel.red() - page.red()),
                abs(pixel.green() - page.green()),
                abs(pixel.blue() - page.blue()),
            )
            count += gap > 24
    return count


# ── The picture ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dpr", [1.0, 2.0])
def test_the_thumbnail_has_a_fixed_size(dpr: float) -> None:
    _app()
    pixmap = th.render_thumbnail("SELECT 1", mono_font(8), dpr)

    assert pixmap.devicePixelRatio() == dpr
    assert pixmap.width() == round(th.THUMB_WIDTH * dpr)
    assert pixmap.height() == round(th.THUMB_HEIGHT * dpr)


def test_the_thumbnail_shows_the_text_and_fades_out() -> None:
    _app()
    page = qcolor(_tk.color("code"))
    long_query = "\n".join(
        f"SELECT {n} FROM dbo.Orders WHERE Id > {n}" for n in range(30)
    )

    image = th.render_thumbnail(long_query, mono_font(8)).toImage()

    assert _ink(image, range(0, 30), page) > 0  # the first lines are there
    assert _ink(image, range(image.height() - 3, image.height()), page) == 0  # faded


def test_an_empty_file_is_a_blank_page() -> None:
    _app()
    page = qcolor(_tk.color("code"))

    image = th.render_thumbnail("", mono_font(8)).toImage()

    assert _ink(image, range(image.height()), page) == 0


def test_the_text_is_highlighted_like_the_editor() -> None:
    _app()
    plain = th.render_thumbnail("orders orders orders", mono_font(8)).toImage()
    keyword = th.render_thumbnail("SELECT SELECT SELECT", mono_font(8)).toImage()
    colours = {
        plain.pixelColor(x, y).rgb() for x in range(plain.width()) for y in range(30)
    }
    keyword_colours = {
        keyword.pixelColor(x, y).rgb()
        for x in range(keyword.width())
        for y in range(30)
    }

    assert colours != keyword_colours


# ── The cache ──────────────────────────────────────────────────────────────


def _write(folder: Path, name: str, text: str = "SELECT 1 AS One") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_text_is_read_in_the_background_then_the_thumbnail_is_ready(
    cache: th.ThumbnailCache, tmp_path: Path
) -> None:
    path = _write(tmp_path, "a.sql")
    ready: list[bool] = []
    cache.ready.connect(lambda: ready.append(True))

    assert cache.thumbnail(path, 100) is None  # asked for, not here yet
    _wait_until(lambda: bool(ready))

    pixmap = cache.thumbnail(path, 100)
    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (th.THUMB_WIDTH, th.THUMB_HEIGHT)
    assert cache.thumbnail(path, 100) is pixmap  # served from the cache


def test_each_text_is_read_once(
    cache: th.ThumbnailCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[Path] = []

    def read(path: Path) -> str:
        reads.append(path)
        return "SELECT 1"

    monkeypatch.setattr(th, "read_preview", read)
    path = tmp_path / "a.sql"
    ready: list[bool] = []
    cache.ready.connect(lambda: ready.append(True))

    for _ in range(5):  # a view paints again before the first read is back
        cache.thumbnail(path, 100)
    _wait_until(lambda: bool(ready))
    cache.thumbnail(path, 100)
    cache.thumbnail(path, 100, 2.0)  # another scale: the text is not read again
    _pump(50)

    assert reads == [path]
    assert len(ready) == 1


def test_a_saved_file_gets_a_new_thumbnail(
    cache: th.ThumbnailCache, tmp_path: Path
) -> None:
    path = _write(tmp_path, "a.sql", "SELECT 1")
    ready: list[bool] = []
    cache.ready.connect(lambda: ready.append(True))
    cache.thumbnail(path, 100)
    _wait_until(lambda: len(ready) == 1)
    first = cache.thumbnail(path, 100)

    path.write_text("SELECT 2", encoding="utf-8")
    assert cache.thumbnail(path, 200) is None  # a new modification time
    _wait_until(lambda: len(ready) == 2)

    second = cache.thumbnail(path, 200)
    assert second is not None and second is not first
    assert cache.thumbnail(path, 100) is first  # the old version is still cached


def test_each_scale_has_its_own_pixmap(
    cache: th.ThumbnailCache, tmp_path: Path
) -> None:
    path = _write(tmp_path, "a.sql")
    ready: list[bool] = []
    cache.ready.connect(lambda: ready.append(True))
    cache.thumbnail(path, 100)
    _wait_until(lambda: bool(ready))

    normal = cache.thumbnail(path, 100, 1.0)
    sharp = cache.thumbnail(path, 100, 2.0)

    assert normal is not None and sharp is not None
    assert sharp.width() == 2 * normal.width()


def test_a_missing_file_gets_a_blank_thumbnail_not_an_error(
    cache: th.ThumbnailCache, tmp_path: Path
) -> None:
    ready: list[bool] = []
    cache.ready.connect(lambda: ready.append(True))

    assert cache.thumbnail(tmp_path / "gone.sql", 100) is None
    _wait_until(lambda: bool(ready))

    assert cache.thumbnail(tmp_path / "gone.sql", 100) is not None


def test_a_theme_switch_drops_the_pixmaps_and_keeps_the_texts(
    cache: th.ThumbnailCache, tmp_path: Path
) -> None:
    path = _write(tmp_path, "a.sql")
    ready: list[bool] = []
    cache.ready.connect(lambda: ready.append(True))
    cache.thumbnail(path, 100)
    _wait_until(lambda: bool(ready))
    assert cache.thumbnail(path, 100) is not None

    cache.refresh_theme()

    assert cache._pixmaps == {}
    assert cache.thumbnail(path, 100) is not None  # redrawn without reading again
    assert len(ready) == 1


def test_the_oldest_texts_and_pixmaps_are_evicted(
    cache: th.ThumbnailCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(th, "_TEXT_CACHE", 2)
    monkeypatch.setattr(th, "_PIXMAP_CACHE", 2)
    a, b, c = (tmp_path / f"{name}.sql" for name in "abc")
    for path in (a, b, c):
        cache._on_read(str(path), 1, "SELECT 1")
    assert [key[0] for key in cache._texts] == [str(b), str(c)]

    cache.thumbnail(b, 1)
    cache.thumbnail(c, 1)
    cache.thumbnail(b, 1, 2.0)

    assert list(cache._pixmaps) == [
        ((str(c), 1), 1.0, "sql"),
        ((str(b), 1), 2.0, "sql"),
    ]


def test_each_dialect_has_its_own_thumbnail(
    cache: th.ThumbnailCache, tmp_path: Path
) -> None:
    path = tmp_path / "q.sql"
    cache._on_read(str(path), 1, "SELECT 1 # note")
    plain = cache.thumbnail(path, 1)
    mysql = cache.thumbnail(path, 1, dialect="mysql")
    assert plain is not None and mysql is not None
    assert plain is not mysql
    assert plain.toImage() != mysql.toImage()  # "# note" is a comment in MySQL
    assert cache.thumbnail(path, 1, dialect="mysql") is mysql
