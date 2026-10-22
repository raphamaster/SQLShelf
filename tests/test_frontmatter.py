from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.frontmatter import (
    read_sql_file,
    split_frontmatter,
    write_sql_file,
)


def make_sql(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestReadSqlFile:
    def test_parses_valid_frontmatter(self, tmp_path: Path) -> None:
        p = make_sql(
            tmp_path / "q.sql",
            "/* ---\ntitle: My Query\ntags: [a, b]\n--- */\n\nSELECT 1",
        )
        meta, body, has_fm = read_sql_file(p)
        assert meta["title"] == "My Query"
        assert meta["tags"] == ["a", "b"]
        assert body == "SELECT 1"
        assert has_fm is True

    def test_no_frontmatter_returns_full_body(self, tmp_path: Path) -> None:
        p = make_sql(tmp_path / "q.sql", "SELECT 1")
        meta, body, has_fm = read_sql_file(p)
        assert meta == {}
        assert body == "SELECT 1"
        assert has_fm is False

    def test_bad_yaml_is_tolerant(self, tmp_path: Path) -> None:
        p = make_sql(tmp_path / "q.sql", "/* ---\n: bad: yaml: [\n--- */\n\nSELECT 1")
        meta, body, has_fm = read_sql_file(p)
        assert meta == {}
        assert has_fm is False

    def test_multiline_description(self, tmp_path: Path) -> None:
        p = make_sql(
            tmp_path / "q.sql",
            "/* ---\ntitle: T\ndescription: >\n  Line one\n  Line two\n--- */\n\nSELECT 1",
        )
        meta, body, has_fm = read_sql_file(p)
        assert "Line one" in meta["description"]
        assert has_fm is True

    def test_reads_cp1252_file(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        content = "/* ---\ntitle: relat\xf3rio\n--- */\n\nSELECT 1"
        p.write_bytes(content.encode("cp1252"))
        meta, body, has_fm = read_sql_file(p)
        assert "relat" in meta["title"]

    def test_empty_file(self, tmp_path: Path) -> None:
        p = make_sql(tmp_path / "q.sql", "")
        meta, body, has_fm = read_sql_file(p)
        assert has_fm is False


class TestWriteSqlFile:
    def test_creates_file_with_frontmatter(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        write_sql_file(p, {"title": "Test", "tags": ["a"]}, "SELECT 1")
        text = p.read_text(encoding="utf-8")
        assert "/* ---" in text
        assert "title: Test" in text
        assert "SELECT 1" in text

    def test_updated_field_set_automatically(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        write_sql_file(p, {"title": "T"}, "SELECT 1")
        text = p.read_text(encoding="utf-8")
        assert "updated:" in text

    def test_roundtrip_preserves_all_fields(self, tmp_path: Path) -> None:
        p = make_sql(
            tmp_path / "q.sql",
            "/* ---\ntitle: Original\ncreated: 2026-01-01\ncustom_field: keep_me\n--- */\n\nSELECT 1",
        )
        from sqlshelf.core.frontmatter import read_sql_file

        meta, body, _ = read_sql_file(p)
        meta["title"] = "Updated"
        write_sql_file(p, meta, body)
        meta2, body2, has_fm = read_sql_file(p)
        assert meta2["title"] == "Updated"
        assert "custom_field" in meta2
        assert body2 == "SELECT 1"
        assert has_fm is True

    def test_preserves_utf8_bom(self, tmp_path: Path) -> None:
        p = tmp_path / "q.sql"
        p.write_bytes(b"\xef\xbb\xbf/* ---\ntitle: T\n--- */\n\nSELECT 1\n")
        write_sql_file(p, {"title": "T2"}, "SELECT 1")
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_write_without_frontmatter_creates_block(self, tmp_path: Path) -> None:
        p = tmp_path / "new.sql"
        write_sql_file(p, {"title": "New"}, "SELECT 42")
        meta, body, has_fm = read_sql_file(p)
        assert meta["title"] == "New"
        assert body == "SELECT 42"
        assert has_fm is True


@pytest.mark.parametrize(
    ("content", "metadata", "body", "has_frontmatter"),
    [
        (
            "/* ---\ntitle: Sales\ntags: [bi]\n--- */\nSELECT 1\n",
            {"title": "Sales", "tags": ["bi"]},
            "SELECT 1",
            True,
        ),
        ("SELECT 1\n", {}, "SELECT 1", False),
        ("", {}, "", False),
        # Bad YAML and a block that is not a mapping leave the text as SQL.
        ("/* ---\ntitle: [\n--- */\nSELECT 1", {}, None, False),
        ("/* ---\n- a\n- b\n--- */\nSELECT 1", {}, None, False),
        # A frontmatter-like block below the first line is just a comment.
        ("SELECT 1\n/* ---\ntitle: X\n--- */", {}, None, False),
    ],
)
def test_split_frontmatter(
    content: str, metadata: dict, body: str | None, has_frontmatter: bool
) -> None:
    result = split_frontmatter(content)
    assert result[0] == metadata
    assert result[1] == (content.strip() if body is None else body)
    assert result[2] is has_frontmatter


def test_read_sql_file_matches_split_frontmatter(tmp_path: Path) -> None:
    content = "/* ---\ntitle: T\n--- */\n\nSELECT 1\n"
    p = make_sql(tmp_path / "q.sql", content)
    assert read_sql_file(p) == split_frontmatter(content)
