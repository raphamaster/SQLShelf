"""Find search terms in list text the way the FTS index matches them.

The index uses the ``unicode61 remove_diacritics 2`` tokenizer and the search
prefix-matches every word, so a term matches where a word starts, ignoring
case and accents. The list marks the same places, without asking FTS for
``snippet()`` (dropped in 1.0.10 for speed).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from .search import parse_query


def _fold(text: str) -> tuple[str, list[int]]:
    """*text* without case or accents, and for each folded character the
    index in *text* of the character it came from."""
    out: list[str] = []
    origin: list[int] = []
    for i, char in enumerate(text):
        for part in unicodedata.normalize("NFD", char):
            if unicodedata.category(part) == "Mn":
                continue
            folded = part.casefold()
            out.append(folded)
            origin.extend([i] * len(folded))
    return "".join(out), origin


def fold(text: str) -> str:
    """*text* without case or accents, as the FTS tokenizer compares it."""
    return _fold(text)[0]


def find_span(text: str, term: str) -> tuple[int, int] | None:
    """Where *term* first appears in *text*, ignoring case and accents.

    Unlike ``match_spans`` it also matches inside a word, the way the tag
    suggestions filter: "mens" marks "relatorio-mensal" and "dimensao".
    """
    needle = fold(term)
    if not needle:
        return None
    folded, origin = _fold(text)
    start = folded.find(needle)
    if start == -1:
        return None
    end = origin[start + len(needle) - 1] + 1
    while end < len(text) and unicodedata.category(text[end]) == "Mn":
        end += 1
    return origin[start], end


def _words(term: str) -> list[str]:
    """The runs of letters and digits in *term*, folded: what FTS searches for."""
    words: list[str] = []
    current: list[str] = []
    for char in fold(term):
        if char.isalnum():
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def match_spans(text: str, terms: Iterable[str]) -> list[tuple[int, int]]:
    """Sorted ``(start, end)`` ranges of *text* where one of *terms* matches.

    A match ignores case and accents and starts at the beginning of a word,
    like the FTS prefix search: "ped" marks "Pedidos" but not "ItensPedido".
    Overlapping or touching ranges are merged.
    """
    folded, origin = _fold(text)
    found: list[tuple[int, int]] = []
    for term in terms:
        for word in _words(term):
            start = folded.find(word)
            while start != -1:
                if start == 0 or not folded[start - 1].isalnum():
                    end = origin[start + len(word) - 1] + 1
                    # Keep a decomposed accent with the letter it sits on.
                    while end < len(text) and unicodedata.category(text[end]) == "Mn":
                        end += 1
                    found.append((origin[start], end))
                start = folded.find(word, start + 1)

    merged: list[tuple[int, int]] = []
    for start, end in sorted(found):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def highlight_terms(search_text: str) -> list[str]:
    """Terms the list marks for *search_text*: the free text and ``table:`` values."""
    filters, free_text = parse_query(search_text)
    return [*free_text.split(), *filters["table"]]
