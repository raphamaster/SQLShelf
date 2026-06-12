from __future__ import annotations

from pathlib import Path

import pytest

from sqlshelf.core.index_db import IndexDB
from sqlshelf.core.models import Query
from sqlshelf.core.search import parse_query, search


def make_sql_file(folder: Path, name: str, content: str = "SELECT 1") -> Path:
    p = folder / name
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def db_with_data(tmp_path: Path) -> IndexDB:
    q1 = Query(
        path=make_sql_file(tmp_path, "invoices.sql", "SELECT amount FROM dbo.Invoices"),
        title="Invoice totals",
        description="Sums up invoice amounts",
        tags=["finance", "report"],
        body="SELECT amount FROM dbo.Invoices",
    )
    q2 = Query(
        path=make_sql_file(tmp_path, "orders.sql", "SELECT id, total FROM dbo.Orders"),
        title="Order list",
        description="All orders",
        tags=["report"],
        body="SELECT id, total FROM dbo.Orders",
    )
    q3 = Query(
        path=make_sql_file(tmp_path, "products.sql", "SELECT name, price FROM Products"),
        title="Product catalog",
        description="Full product listing",
        tags=["catalog"],
        body="SELECT name, price FROM Products",
    )
    db = IndexDB(tmp_path)
    db.index_all([q1, q2, q3])
    return db


class TestParseQuery:
    def test_empty_string(self) -> None:
        filters, free = parse_query("")
        assert free == ""
        assert filters == {"table": [], "col": [], "tag": []}

    def test_free_text_only(self) -> None:
        filters, free = parse_query("invoice amount")
        assert free == "invoice amount"
        assert filters["table"] == []

    def test_table_prefix(self) -> None:
        filters, free = parse_query("table:Invoices")
        assert filters["table"] == ["Invoices"]
        assert free == ""

    def test_col_prefix(self) -> None:
        filters, free = parse_query("col:amount")
        assert filters["col"] == ["amount"]
        assert free == ""

    def test_tag_prefix(self) -> None:
        filters, free = parse_query("tag:finance")
        assert filters["tag"] == ["finance"]

    def test_mixed(self) -> None:
        filters, free = parse_query("table:Orders tag:report totals")
        assert filters["table"] == ["Orders"]
        assert filters["tag"] == ["report"]
        assert "totals" in free

    def test_multiple_tables(self) -> None:
        filters, free = parse_query("table:Orders table:Customers")
        assert "Orders" in filters["table"]
        assert "Customers" in filters["table"]


class TestSearch:
    def test_empty_returns_all(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("")
        assert len(results) == 3

    def test_empty_sorted_alphabetically(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("")
        titles = [r.title for r in results]
        assert titles == sorted(titles)

    def test_fts_finds_word_in_title(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("invoice")
        assert any("Invoice" in r.title for r in results)

    def test_fts_finds_word_in_description(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("catalog")
        assert any("Product" in r.title for r in results)

    def test_tag_filter(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("tag:finance")
        assert len(results) == 1
        assert results[0].title == "Invoice totals"

    def test_tag_filter_multiple_results(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("tag:report")
        assert len(results) == 2

    def test_table_filter(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("table:Invoices")
        assert len(results) == 1
        assert "Invoice" in results[0].title

    def test_col_filter(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("col:total")
        assert len(results) == 1
        assert "Order" in results[0].title

    def test_combined_tag_and_fts(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("tag:report invoice")
        assert len(results) == 1
        assert "Invoice" in results[0].title

    def test_no_match_returns_empty(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("zzznomatch")
        assert results == []

    def test_invalid_fts_query_returns_empty_not_error(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("AND OR")
        assert isinstance(results, list)

    def test_results_have_tags(self, db_with_data: IndexDB) -> None:
        results = db_with_data.search("")
        invoice = next(r for r in results if "Invoice" in r.title)
        assert "finance" in invoice.tags
        assert "report" in invoice.tags

    def test_table_filter_case_insensitive(self, db_with_data: IndexDB) -> None:
        results_lower = db_with_data.search("table:invoices")
        results_upper = db_with_data.search("table:INVOICES")
        assert len(results_lower) == len(results_upper)
