from __future__ import annotations

from pathlib import Path

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"


def detect_encoding(raw: bytes) -> tuple[str, bool]:
    """Return (encoding_name, had_bom) for raw bytes.

    Priority: UTF-16 BOM → UTF-8 BOM → try UTF-8 → fallback cp1252.
    """
    if raw.startswith(_UTF16_BE_BOM):
        return "utf-16-be", True
    if raw.startswith(_UTF16_LE_BOM):
        return "utf-16-le", True
    if raw.startswith(_UTF8_BOM):
        return "utf-8-sig", True
    try:
        raw.decode("utf-8")
        return "utf-8", False
    except UnicodeDecodeError:
        return "cp1252", False


_LABELS = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8 BOM",
    "utf-16-le": "UTF-16 LE",
    "utf-16-be": "UTF-16 BE",
    "cp1252": "Windows-1252",
}


def encoding_label(encoding: str) -> str:
    """Display name for an encoding returned by detect_encoding, e.g. "UTF-8 BOM"."""
    return _LABELS.get(encoding.lower(), encoding.upper())


def _decode(raw: bytes, errors: str = "strict") -> tuple[str, str, bool]:
    encoding, had_bom = detect_encoding(raw)
    if had_bom and encoding in ("utf-16-le", "utf-16-be"):
        text = raw[2:].decode(encoding, errors)
    elif had_bom and encoding == "utf-8-sig":
        text = raw[3:].decode("utf-8", errors)
    else:
        text = raw.decode(encoding, errors)
    return text, encoding, had_bom


def read_text(path: Path) -> tuple[str, str, bool]:
    """Read *path* and return (text, original_encoding, had_bom)."""
    return _decode(path.read_bytes())


def _drop_partial_char(raw: bytes) -> bytes:
    """*raw*, cut at a byte limit, without the character it may have split."""
    if raw.startswith((_UTF16_LE_BOM, _UTF16_BE_BOM)):
        return raw[: len(raw) // 2 * 2]
    for cut in range(4):  # a UTF-8 character is at most 4 bytes
        head = raw[: len(raw) - cut]
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return head
    return raw  # not UTF-8: cp1252 has no multi-byte characters


def read_head(path: Path, limit: int = 16 * 1024) -> str:
    """The text of the first *limit* bytes of *path*, decoded like read_text.

    A character cut by the limit is dropped, so a large UTF-8 file is not taken
    for Windows-1252. Bytes that cannot be decoded become U+FFFD.
    """
    with path.open("rb") as handle:
        raw = handle.read(limit)
        cut = bool(handle.read(1))
    if cut:
        raw = _drop_partial_char(raw)
    return _decode(raw, "replace")[0]


def write_text(
    path: Path, text: str, original_encoding: str = "utf-8", had_bom: bool = False
) -> None:
    """Write *text* to *path* as UTF-8, preserving BOM only if original had one."""
    if had_bom and original_encoding == "utf-8-sig":
        path.write_text(text, encoding="utf-8-sig")
    else:
        path.write_text(text, encoding="utf-8")
