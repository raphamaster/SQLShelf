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


def read_text(path: Path) -> tuple[str, str, bool]:
    """Read *path* and return (text, original_encoding, had_bom)."""
    raw = path.read_bytes()
    encoding, had_bom = detect_encoding(raw)
    if had_bom and encoding in ("utf-16-le", "utf-16-be"):
        text = raw[2:].decode(encoding)
    elif had_bom and encoding == "utf-8-sig":
        text = raw[3:].decode("utf-8")
    else:
        text = raw.decode(encoding)
    return text, encoding, had_bom


def write_text(
    path: Path, text: str, original_encoding: str = "utf-8", had_bom: bool = False
) -> None:
    """Write *text* to *path* as UTF-8, preserving BOM only if original had one."""
    if had_bom and original_encoding == "utf-8-sig":
        path.write_text(text, encoding="utf-8-sig")
    else:
        path.write_text(text, encoding="utf-8")
