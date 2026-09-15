from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.scanner import scan_file, scan_folder

FRONTMATTER = """/* ---
title: Faturamento mensal
description: Total por filial
tags:
  - vendas
  - mensal
--- */
SELECT SUM(valor) FROM dbo.Faturamento
"""


def write(folder: Path, name: str, content: str = "SELECT 1") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestScanFile:
    """scan_file exists so callers that know the path never rescan the folder."""

    def test_reads_frontmatter(self, tmp_path: Path) -> None:
        path = write(tmp_path, "faturamento.sql", FRONTMATTER)
        query = scan_file(path)
        assert query is not None
        assert query.title == "Faturamento mensal"
        assert query.description == "Total por filial"
        assert query.tags == ["vendas", "mensal"]
        assert query.has_frontmatter is True

    def test_title_falls_back_to_stem(self, tmp_path: Path) -> None:
        query = scan_file(write(tmp_path, "sem_meta.sql"))
        assert query is not None
        assert query.title == "sem_meta"
        assert query.has_frontmatter is False

    @pytest.mark.parametrize("name", ["notes.txt", "script.sqlx", "readme.md"])
    def test_ignores_non_sql(self, tmp_path: Path, name: str) -> None:
        assert scan_file(write(tmp_path, name)) is None

    def test_accepts_uppercase_extension(self, tmp_path: Path) -> None:
        assert scan_file(write(tmp_path, "UPPER.SQL")) is not None

    def test_ignores_the_index_folder(self, tmp_path: Path) -> None:
        assert scan_file(write(tmp_path, ".sqlshelf/cache.sql")) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert scan_file(tmp_path / "gone.sql") is None

    def test_directory_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "folder.sql").mkdir()
        assert scan_file(tmp_path / "folder.sql") is None

    def test_matches_scan_folder_for_the_same_file(self, tmp_path: Path) -> None:
        path = write(tmp_path, "faturamento.sql", FRONTMATTER)
        write(tmp_path, "outro.sql")
        from_folder = [q for q in scan_folder(tmp_path) if q.path == path][0]
        from_file = scan_file(path)
        assert from_file is not None
        assert from_file == from_folder


class TestScanFolder:
    def test_skips_the_index_folder(self, tmp_path: Path) -> None:
        write(tmp_path, "keep.sql")
        write(tmp_path, ".sqlshelf/drop.sql")
        assert [q.path.name for q in scan_folder(tmp_path)] == ["keep.sql"]

    def test_recurses_into_subfolders(self, tmp_path: Path) -> None:
        write(tmp_path, "a.sql")
        write(tmp_path, "sub/b.sql")
        assert {q.path.name for q in scan_folder(tmp_path)} == {"a.sql", "b.sql"}
