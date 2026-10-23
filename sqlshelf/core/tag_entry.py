"""Typing tags in the edit form: what a typed tag becomes, and which of the
library's tags to suggest for it."""

from __future__ import annotations

from collections.abc import Iterable

from .text_match import fold


def normalize_tag(raw: str) -> str:
    """*raw* as a tag: no leading "#", no surrounding space, lower case."""
    return raw.strip().lstrip("#").strip().lower()


def split_tags(raw: str) -> list[str]:
    """The tags typed in *raw*, separated by commas or spaces, without repeats."""
    tags: list[str] = []
    for part in raw.replace(",", " ").split():
        tag = normalize_tag(part)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def suggest_tags(
    counts: Iterable[tuple[str, int]],
    typed: str,
    chosen: Iterable[str],
    limit: int = 6,
) -> list[tuple[str, int]]:
    """The known ``(tag, count)`` pairs to offer for *typed*.

    A tag matches when it contains *typed*, ignoring case and accents; with
    nothing typed every tag does. Tags already *chosen* are left out. Tags
    that start with the text come first, then the most used, then by name.
    """
    needle = fold(normalize_tag(typed))
    taken = {fold(tag) for tag in chosen}
    ranked: list[tuple[bool, int, str, str, int]] = []
    for name, count in counts:
        folded = fold(name)
        if folded in taken:
            continue
        position = folded.find(needle) if needle else 0
        if position < 0:
            continue
        ranked.append((position != 0, -count, folded, name, count))
    ranked.sort()
    return [(name, count) for *_key, name, count in ranked[: max(limit, 0)]]
