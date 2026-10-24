from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.encoding import (
    detect_encoding,
    encoding_label,
    read_head,
    read_text,
    write_text,
)


class TestDetectEncoding:
    def test_utf8_bom(self) -> None:
        raw = b"\xef\xbb\xbfSELECT 1"
        enc, had_bom = detect_encoding(raw)
        assert enc == "utf-8-sig"
        assert had_bom is True

    def test_utf16_le_bom(self) -> None:
        raw = b"\xff\xfe" + "SELECT".encode("utf-16-le")
        enc, had_bom = detect_encoding(raw)
        assert enc == "utf-16-le"
        assert had_bom is True

    def test_utf16_be_bom(self) -> None:
        raw = b"\xfe\xff" + "SELECT".encode("utf-16-be")
        enc, had_bom = detect_encoding(raw)
        assert enc == "utf-16-be"
        assert had_bom is True

    def test_plain_utf8(self) -> None:
        raw = "SELECT 1".encode("utf-8")
        enc, had_bom = detect_encoding(raw)
        assert enc == "utf-8"
        assert had_bom is False

    def test_utf8_with_multibyte(self) -> None:
        raw = "relatório".encode("utf-8")
        enc, had_bom = detect_encoding(raw)
        assert enc == "utf-8"
        assert had_bom is False

    def test_cp1252_fallback(self) -> None:
        raw = "relat\xf3rio".encode("cp1252")  # ó in cp1252
        enc, had_bom = detect_encoding(raw)
        assert enc == "cp1252"
        assert had_bom is False

    def test_empty_bytes(self) -> None:
        enc, had_bom = detect_encoding(b"")
        assert enc == "utf-8"
        assert had_bom is False


class TestReadText:
    def test_reads_utf8_file(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes("SELECT 1".encode("utf-8"))
        text, enc, had_bom = read_text(p)
        assert text == "SELECT 1"
        assert enc == "utf-8"
        assert had_bom is False

    def test_reads_utf8_bom_file(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes(b"\xef\xbb\xbf" + "SELECT 1".encode("utf-8"))
        text, enc, had_bom = read_text(p)
        assert text == "SELECT 1"
        assert enc == "utf-8-sig"
        assert had_bom is True

    def test_reads_cp1252_file(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes("relat\xf3rio".encode("cp1252"))
        text, enc, had_bom = read_text(p)
        assert text == "relatório"
        assert enc == "cp1252"
        assert had_bom is False

    def test_reads_utf16_le_file(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes(b"\xff\xfe" + "SELECT".encode("utf-16-le"))
        text, enc, had_bom = read_text(p)
        assert text == "SELECT"
        assert enc == "utf-16-le"
        assert had_bom is True


class TestReadHead:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (b"SELECT 1", "SELECT 1"),
            (b"\xef\xbb\xbfSELECT 1", "SELECT 1"),
            (b"\xff\xfe" + "SELECT".encode("utf-16-le"), "SELECT"),
            ("relat\xf3rio".encode("cp1252"), "relatório"),
            (b"", ""),
        ],
    )
    def test_a_short_file_is_read_whole(
        self, tmp_path: Path, raw: bytes, expected: str
    ) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes(raw)
        assert read_head(p) == expected

    @pytest.mark.parametrize("limit", [10, 11, 12, 13])
    def test_a_cut_utf8_character_is_dropped_not_misread(
        self, tmp_path: Path, limit: int
    ) -> None:
        # "é" and "ã" take two bytes, so one of these limits splits a character.
        text = "SELECT 'é', 'ã' FROM t"
        p = tmp_path / "q.sql"
        p.write_text(text, encoding="utf-8")
        head = read_head(p, limit)
        assert text.startswith(head)
        assert "�" not in head and "Ã" not in head

    def test_a_cut_utf16_character_is_dropped(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes(b"\xff\xfe" + "SELECT 1".encode("utf-16-le"))
        assert read_head(p, 9) == "SEL"  # 2 BOM bytes + 3 characters + 1 byte

    def test_a_large_cp1252_file_stays_cp1252(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes(("relat\xf3rio " * 50).encode("cp1252"))
        assert read_head(p, 30).startswith("relatório relatório")

    def test_only_the_limit_is_read(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_text("x" * 100_000, encoding="utf-8")
        assert len(read_head(p, 1024)) == 1024


class TestWriteText:
    def test_writes_utf8(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        write_text(p, "SELECT 1")
        assert p.read_bytes() == "SELECT 1".encode("utf-8")

    def test_preserves_utf8_bom(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        write_text(p, "SELECT 1", original_encoding="utf-8-sig", had_bom=True)
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_no_bom_for_cp1252_original(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        write_text(p, "SELECT 1", original_encoding="cp1252", had_bom=False)
        assert not p.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_roundtrip_utf8_bom(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        original = b"\xef\xbb\xbf" + "SELECT relatório".encode("utf-8")
        p.write_bytes(original)
        text, enc, had_bom = read_text(p)
        write_text(p, text, enc, had_bom)
        result = p.read_bytes()
        assert result.startswith(b"\xef\xbb\xbf")
        assert result[3:].decode("utf-8") == "SELECT relatório"


@pytest.mark.parametrize(
    ("encoding", "label"),
    [
        ("utf-8", "UTF-8"),
        ("utf-8-sig", "UTF-8 BOM"),
        ("utf-16-le", "UTF-16 LE"),
        ("utf-16-be", "UTF-16 BE"),
        ("cp1252", "Windows-1252"),
        ("latin-1", "LATIN-1"),
    ],
)
def test_encoding_label(encoding: str, label: str) -> None:
    assert encoding_label(encoding) == label


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (b"SELECT 1", "UTF-8"),
        (b"\xef\xbb\xbfSELECT 1", "UTF-8 BOM"),
        (b"\xff\xfe" + "SELECT".encode("utf-16-le"), "UTF-16 LE"),
        ("relat\xf3rio".encode("cp1252"), "Windows-1252"),
    ],
)
def test_every_detected_encoding_has_a_label(raw: bytes, label: str) -> None:
    encoding, _had_bom = detect_encoding(raw)
    assert encoding_label(encoding) == label
