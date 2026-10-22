"""How the query list arranges and labels results: date and folder groups.

Pure functions, so the bucket boundaries can be tested without a display.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath

from .i18n import tr
from .models import SearchResult

# In display order, newest first.
DATE_BUCKETS = ("today", "yesterday", "this_week", "this_month", "older")

FOLDER_SEPARATOR = " › "


def _local_date(mtime: float) -> date | None:
    if mtime <= 0:
        return None
    try:
        return datetime.fromtimestamp(mtime).date()
    except (OverflowError, OSError, ValueError):
        return None


def date_bucket(mtime: float, now: datetime, first_weekday: int) -> str:
    """Return the DATE_BUCKETS id for a file modified at *mtime* (epoch seconds).

    *first_weekday* is ISO numbered, 1 = Monday to 7 = Sunday, the same values
    as Qt.DayOfWeek, so the UI can pass ``QLocale().firstDayOfWeek()``. Buckets
    are checked newest first: on a Monday that starts the week, Sunday is still
    "yesterday" rather than "this week". A timestamp from the future counts as
    today.
    """
    day = _local_date(mtime)
    if day is None:
        return "older"
    today = now.date()
    if day >= today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    week_start = today - timedelta(days=(today.isoweekday() - first_weekday) % 7)
    if day >= week_start:
        return "this_week"
    if (day.year, day.month) == (today.year, today.month):
        return "this_month"
    return "older"


def group_by_date(
    results: list[SearchResult],
    now: datetime,
    first_weekday: int,
    *,
    oldest_first: bool = False,
) -> list[tuple[str, list[SearchResult]]]:
    """Split *results* into date buckets, keeping their order within each.

    Empty buckets are left out. *oldest_first* reverses the bucket order, for
    the list sorted by oldest modification first.
    """
    buckets: dict[str, list[SearchResult]] = {key: [] for key in DATE_BUCKETS}
    for result in results:
        buckets[date_bucket(result.file_mtime, now, first_weekday)].append(result)
    order = reversed(DATE_BUCKETS) if oldest_first else DATE_BUCKETS
    return [(key, buckets[key]) for key in order if buckets[key]]


def folder_label(root: Path | None, rel_path: str) -> str:
    """Label of the folder holding *rel_path*: ``Querys › relatorios``."""
    parts = list(PurePosixPath(rel_path.replace("\\", "/")).parent.parts)
    if root is not None:
        parts.insert(0, root.name)
    return FOLDER_SEPARATOR.join(parts)


def group_by_folder(
    results: list[SearchResult],
) -> list[tuple[str, list[SearchResult]]]:
    """Split *results* by the folder holding each file.

    Groups come in the order their first result appears, so they follow the
    list's sort; the key is the folder label.
    """
    groups: dict[str, list[SearchResult]] = {}
    for result in results:
        groups.setdefault(folder_label(result.folder, result.rel_path), []).append(
            result
        )
    return list(groups.items())


def display_name(result: SearchResult) -> tuple[str, bool]:
    """(name to show, whether the file has a title).

    The index stores the file stem as the title of a file whose frontmatter
    has none, so a title equal to the stem means there is no title, and the
    list shows the file name instead.
    """
    file_name = PurePosixPath(result.rel_path.replace("\\", "/")).name
    stem = PurePosixPath(file_name).stem
    if not result.title or result.title == stem:
        return file_name, False
    return result.title, True


def modified_label(mtime: float, now: datetime) -> str:
    """Return "Today 09:42", "Yesterday 19:31", otherwise dd/mm/yyyy."""
    day = _local_date(mtime)
    if day is None:
        return ""
    moment = datetime.fromtimestamp(mtime)
    time = moment.strftime("%H:%M")
    if day == now.date():
        return tr("list.today_at", time=time)
    if day == now.date() - timedelta(days=1):
        return tr("list.yesterday_at", time=time)
    return moment.strftime("%d/%m/%Y")
