"""What the quick search (Ctrl+P) lists for the typed text.

The palette filters the queries it already loaded instead of asking the index
on every key, so typing never waits on SQLite. ``table:``, ``tag:``, ``date:``
and ``dialect:`` narrow the list as in the search box; ``col:`` is ignored, because
the loaded rows carry no columns. Every other word must appear in the title,
the path or a tag, ignoring case and accents, even inside a word: "dos" finds
"Pedidos".
"""

from __future__ import annotations

import re

from .grouping import display_name
from .models import SearchResult
from .search import OPERATORS, _dialect_value, _parse_date_filter, parse_query
from .text_match import find_span, fold

# More rows than this only make the palette slower to fill; typing narrows it.
MAX_RESULTS = 50

# Tab moves the last word through these operators, then back to plain text.
TAB_OPERATORS = ("table", "tag")

_LAST_WORD_RE = re.compile(r"\S+$")


def _words(free_text: str) -> list[str]:
    return [word for word in (fold(w) for w in free_text.split()) if word]


def _passes(result: SearchResult, filters: dict[str, list[str]]) -> bool:
    tables = {fold(t) for t in result.tables}
    if any(fold(t) not in tables for t in filters["table"]):
        return False
    tags = {fold(t) for t in result.tags}
    if any(fold(t) not in tags for t in filters["tag"]):
        return False
    for date_text in filters["date"]:
        span = _parse_date_filter(date_text)
        if span is not None and not span[0] <= result.file_mtime <= span[1]:
            return False
    if any(_dialect_value(d) != result.dialect for d in filters["dialect"]):
        return False
    return True


def filter_results(
    results: list[SearchResult], text: str, limit: int = MAX_RESULTS
) -> list[SearchResult]:
    """The *results* that match *text*, at most *limit* of them.

    Results whose name holds every word come first; within each part the most
    recently modified come first.
    """
    filters, free_text = parse_query(text.strip())
    words = _words(free_text)
    ranked: list[tuple[int, SearchResult]] = []
    for result in results:
        if not _passes(result, filters):
            continue
        name = fold(display_name(result)[0])
        if all(word in name for word in words):
            ranked.append((0, result))
            continue
        fields = [name, fold(result.rel_path), *(fold(t) for t in result.tags)]
        if all(any(word in field for field in fields) for word in words):
            ranked.append((1, result))
    ranked.sort(key=lambda pair: (pair[0], -pair[1].file_mtime))
    return [result for _rank, result in ranked[:limit]]


def name_spans(name: str, text: str) -> list[tuple[int, int]]:
    """Sorted ``(start, end)`` ranges of *name* where a word of *text* first
    appears, ignoring case and accents; overlapping ranges are merged."""
    _filters, free_text = parse_query(text)
    found = sorted(
        span for span in (find_span(name, w) for w in free_text.split()) if span
    )
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def action_matches(label: str, text: str) -> bool:
    """Whether the action called *label* stays listed for *text*.

    Only plain words filter actions, each of them inside the label; any
    operator token hides every action.
    """
    filters, free_text = parse_query(text)
    if any(filters.values()):
        return False
    name = fold(label)
    return all(word in name for word in _words(free_text))


def cycle_operator(text: str) -> str:
    """*text* with the operator of its last word moved on: ``Pedidos`` →
    ``table:Pedidos`` → ``tag:Pedidos`` → ``Pedidos``.

    Text that ends in a space, or whose last word has another operator or no
    value yet, comes back unchanged.
    """
    match = _LAST_WORD_RE.search(text)
    if match is None:
        return text
    word = match.group()
    operator, colon, value = word.partition(":")
    if colon and operator in OPERATORS:
        if operator not in TAB_OPERATORS or not value:
            return text
        following = TAB_OPERATORS.index(operator) + 1
        word = (
            f"{TAB_OPERATORS[following]}:{value}"
            if following < len(TAB_OPERATORS)
            else value
        )
    else:
        word = f"{TAB_OPERATORS[0]}:{word}"
    return text[: match.start()] + word
