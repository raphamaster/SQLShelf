from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.encoding import detect_encoding, read_text, write_text


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
