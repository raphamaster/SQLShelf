from __future__ import annotations

import pytest

from sqlshelf.core.sql_objects import extract_objects, objects_to_text


class TestExtractObjects:
    def test_simple_select(self) -> None:
        result = extract_objects("SELECT id, name FROM dbo.Customers")
        assert "Customers" in result["table"]
        assert "id" in result["column"]
        assert "name" in result["column"]

    def test_join_extracts_multiple_tables(self) -> None:
        sql = "SELECT o.id, c.name FROM Orders o JOIN Customers c ON o.customer_id = c.id"
        result = extract_objects(sql)
        assert "Orders" in result["table"]
        assert "Customers" in result["table"]

    def test_cte_excluded_from_tables(self) -> None:
        sql = """
        WITH recent AS (
            SELECT id FROM Orders WHERE created > '2026-01-01'
        )
        SELECT * FROM recent JOIN Customers c ON recent.id = c.order_id
        """
        result = extract_objects(sql)
        assert "recent" not in result["table"]
        assert "Customers" in result["table"]

    def test_cross_apply_tsql(self) -> None:
        sql = """
        SELECT d.DocumentoId, v.Versao
        FROM dbo.Documentos AS d
        CROSS APPLY (
            SELECT TOP (1) Versao FROM dbo.Versoes v
            WHERE v.DocumentoId = d.DocumentoId
            ORDER BY v.DataCriacao DESC
        ) AS v
        """
        result = extract_objects(sql)
        assert "Documentos" in result["table"]
        assert "Versoes" in result["table"]

    def test_parse_error_returns_empty(self) -> None:
        # A truly unparseable string (only punctuation) should yield empty sets
        result = extract_objects("!@#$% {{{")
        assert result["table"] == set()
        assert result["column"] == set()

    def test_empty_string_returns_empty(self) -> None:
        result = extract_objects("")
        assert result["table"] == set()

    def test_select_without_from(self) -> None:
        result = extract_objects("SELECT 1")
        assert result["table"] == set()

    def test_procedure_and_function_are_empty(self) -> None:
        result = extract_objects("SELECT id FROM Orders")
        assert result["procedure"] == set()
        assert result["function"] == set()

    @pytest.mark.parametrize(
        "sql,expected_table",
        [
            ("SELECT * FROM dbo.Orders", "Orders"),
            ("SELECT * FROM [dbo].[Orders]", "Orders"),
            ("INSERT INTO Products (name) VALUES ('x')", "Products"),
            ("UPDATE Stock SET qty=1 WHERE id=1", "Stock"),
            ("DELETE FROM Logs WHERE created < '2020-01-01'", "Logs"),
        ],
    )
    def test_various_dml(self, sql: str, expected_table: str) -> None:
        result = extract_objects(sql)
        assert expected_table in result["table"]


class TestObjectsToText:
    def test_empty_objects(self) -> None:
        objs = {"table": set(), "column": set(), "procedure": set(), "function": set()}
        assert objects_to_text(objs) == ""

    def test_names_joined_with_spaces(self) -> None:
        objs = {"table": {"Orders"}, "column": {"id"}, "procedure": set(), "function": set()}
        text = objects_to_text(objs)
        assert "Orders" in text
        assert "id" in text

    def test_sorted_within_each_type(self) -> None:
        objs = {"table": {"Zebra", "Alpha"}, "column": set(), "procedure": set(), "function": set()}
        text = objects_to_text(objs)
        assert text.index("Alpha") < text.index("Zebra")
