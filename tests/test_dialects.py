from __future__ import annotations

import pytest

from sqlshelf.core.dialects import (
    DIALECTS,
    GENERIC,
    code_view,
    detect,
    get,
    key_for,
    normalize,
    parser_for,
    resolve,
    settle,
)


class TestDetect:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            # T-SQL
            ("SELECT TOP 10 * FROM dbo.Orders", "tsql"),
            ("SELECT * FROM [Sales].[Orders]", "tsql"),
            ("SELECT 1\nGO\nSELECT 2", "tsql"),
            ("DECLARE @x int = 1; SELECT @x", "tsql"),
            ("SELECT * INTO #tmp FROM Orders", "tsql"),
            ("SELECT * FROM Orders WITH (NOLOCK)", "tsql"),
            ("SELECT ISNULL(a, 1), GETDATE() FROM dbo.t", "tsql"),
            ("SELECT * FROM a CROSS APPLY b", "tsql"),
            # PostgreSQL
            ("SELECT id::text FROM orders", "postgres"),
            ("SELECT * FROM users WHERE name ILIKE 'a%'", "postgres"),
            ("CREATE TABLE t (id SERIAL, doc JSONB)", "postgres"),
            ("SELECT DISTINCT ON (a) a FROM t", "postgres"),
            ("INSERT INTO t VALUES (1) ON CONFLICT DO NOTHING", "postgres"),
            (
                "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql",
                "postgres",
            ),
            # MySQL
            ("SELECT `o`.`id` FROM `orders` o", "mysql"),
            ("SELECT * FROM t LIMIT 5, 10", "mysql"),
            ("CREATE TABLE t (id INT AUTO_INCREMENT) ENGINE=InnoDB", "mysql"),
            ("SELECT GROUP_CONCAT(name) FROM t", "mysql"),
            ("INSERT INTO t VALUES (1) ON DUPLICATE KEY UPDATE a = 2", "mysql"),
            ("DELIMITER $$\nCREATE PROCEDURE p() BEGIN SELECT 1; END$$", "mysql"),
            # MariaDB
            ("CREATE TABLE t (a INT) WITH SYSTEM VERSIONING", "mariadb"),
            ("CREATE TABLE `t` (a INT) ENGINE=Aria", "mariadb"),
            ("/*M!100100 SET @x = 1 */ SELECT `a` FROM t", "mariadb"),
            # Oracle
            ("SELECT SYSDATE FROM dual", "oracle"),
            ("SELECT * FROM t WHERE ROWNUM <= 10", "oracle"),
            ("SELECT NVL(a, 0) FROM t", "oracle"),
            ("CREATE TABLE t (name VARCHAR2(30))", "oracle"),
            ("SELECT a FROM t MINUS SELECT a FROM u", "oracle"),
            ("BEGIN\n  NULL;\nEND;\n/\n", "oracle"),
            ("SELECT q'[it's]' FROM t", "oracle"),
            # BigQuery
            ("SELECT * FROM `proj.dataset.orders`", "bigquery"),
            ("SELECT * FROM `my-proj`.sales.orders", "bigquery"),
            ("SELECT SAFE_CAST(a AS INT64) FROM t", "bigquery"),
            ("SELECT * EXCEPT (a) FROM t", "bigquery"),
            (
                "SELECT * FROM t QUALIFY ROW_NUMBER() OVER () = 1 AND x = STRUCT<a INT64>(1)",
                "bigquery",
            ),
            ("SELECT * FROM `ds.events_*` WHERE _TABLE_SUFFIX > '2020'", "bigquery"),
        ],
    )
    def test_reads_the_dialect(self, sql: str, expected: str) -> None:
        assert detect(sql).dialect.key == expected

    @pytest.mark.parametrize(
        "sql",
        [
            "",
            "SELECT id, name FROM customers WHERE id = 1",
            "SELECT * FROM t LIMIT 5",  # postgres, mysql and bigquery alike
            "UPDATE t SET a = 1",
        ],
    )
    def test_no_sign_is_plain_sql(self, sql: str) -> None:
        found = detect(sql)
        assert found.dialect is GENERIC
        assert found.evidence == ()

    def test_signs_that_cancel_out_are_plain_sql(self) -> None:
        # TOP (T-SQL, 3) against :: (PostgreSQL, 3): neither wins.
        assert detect("SELECT TOP 5 * FROM t WHERE a::int = 1").dialect is GENERIC

    def test_a_weak_sign_alone_is_not_enough(self) -> None:
        assert detect("SELECT a FROM t RETURNING a").dialect is GENERIC

    @pytest.mark.parametrize(
        "sql",
        [
            "-- SELECT TOP 5 * FROM t\nSELECT 1",
            "/* SELECT a::int FROM t\n   LIMIT 5, 10 */ SELECT 1",
            "SELECT 'GO' AS word",
            "SELECT 'FROM dual WHERE ROWNUM < 2' AS hint",
            "SELECT '`proj.ds.t`' AS name",
        ],
    )
    def test_comments_and_strings_say_nothing(self, sql: str) -> None:
        assert detect(sql).dialect is GENERIC

    def test_hash_temp_table_is_not_a_comment(self) -> None:
        assert detect("SELECT *\nFROM\n#temp").dialect.key == "tsql"

    def test_hash_comment_is_mysql(self) -> None:
        assert detect("# monthly report\nSELECT `a` FROM t").dialect.key == "mysql"

    def test_evidence_names_the_signs(self) -> None:
        found = detect("SELECT * FROM dual WHERE ROWNUM < 2")
        assert found.dialect.key == "oracle"
        assert found.evidence == ("DUAL", "ROWNUM")

    def test_mariadb_evidence_keeps_the_mysql_signs(self) -> None:
        found = detect("CREATE TABLE `t` (a INT) WITH SYSTEM VERSIONING")
        assert found.dialect.key == "mariadb"
        assert set(found.evidence) == {"`identifier`", "SYSTEM VERSIONING"}

    def test_the_stronger_dialect_wins(self) -> None:
        sql = "SELECT TOP 5 o.id FROM dbo.orders o WITH (NOLOCK) LIMIT 5"
        assert detect(sql).dialect.key == "tsql"

    def test_huge_body_does_not_raise(self) -> None:
        sql = "SELECT 1;\n" * 50_000 + "SELECT a::int FROM t"
        assert detect(sql).dialect is GENERIC  # the sign is past the scan limit


class TestCodeView:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 'a''b' -- note\nFROM t /* x\ny */",
            "SELECT q'[it's]' FROM dual",
            "SELECT N'São Paulo'",
            "SELECT 'never closed",
            "/* never closed",
        ],
    )
    def test_keeps_the_length_and_the_lines(self, sql: str) -> None:
        view = code_view(sql)
        assert len(view) == len(sql)
        assert view.count("\n") == sql.count("\n")

    def test_blanks_comments_and_empties_strings(self) -> None:
        view = code_view("SELECT 'GO' -- TOP\nFROM t /* NOLOCK */")
        assert "GO" not in view
        assert "TOP" not in view
        assert "NOLOCK" not in view
        assert "''" not in view and "'  '" in view
        assert "FROM t" in view


class TestNames:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("tsql", "tsql"),
            ("T-SQL", "tsql"),
            ("SQL Server", "tsql"),
            ("mssql", "tsql"),
            ("PostgreSQL", "postgres"),
            ("pg", "postgres"),
            ("PL/pgSQL", "postgres"),
            ("MySQL", "mysql"),
            ("MariaDB", "mariadb"),
            ("Oracle", "oracle"),
            ("PL/SQL", "oracle"),
            ("pl_sql", "oracle"),
            ("BigQuery", "bigquery"),
            ("Big Query", "bigquery"),
            ("bq", "bigquery"),
            ("SQL", "sql"),
            ("ansi", "sql"),
        ],
    )
    def test_normalize(self, value: str, expected: str) -> None:
        assert normalize(value) == expected

    @pytest.mark.parametrize("value", [None, "", "sqlite", "snowflake", 42, ["tsql"]])
    def test_normalize_unknown(self, value: object) -> None:
        assert normalize(value) is None

    def test_every_key_normalizes_to_itself(self) -> None:
        for key in DIALECTS:
            assert normalize(key) == key

    def test_get(self) -> None:
        assert get("oracle").label == "Oracle"
        assert get("mariadb").parser == "mysql"
        assert get("sql") is GENERIC
        assert get("nonsense") is GENERIC

    @pytest.mark.parametrize(
        "dialect,expected",
        [
            ("tsql", "tsql"),
            ("mariadb", "mysql"),
            ("sql", "tsql"),
            ("", "tsql"),
            ("snowflake", "snowflake"),  # a sqlglot name passes through
        ],
    )
    def test_parser_for(self, dialect: str, expected: str) -> None:
        assert parser_for(dialect) == expected

    def test_parser_for_none_reads_the_body(self) -> None:
        assert parser_for(None, "SELECT `a` FROM t") == "mysql"
        assert parser_for(None, "SELECT 1") == "tsql"

    def test_key_for(self) -> None:
        assert key_for("oracle", "SELECT TOP 1 * FROM t") == "oracle"
        assert key_for(None, "SELECT TOP 1 * FROM t") == "tsql"
        assert key_for(None, "SELECT 1") == "sql"


class TestResolve:
    def test_frontmatter_wins(self) -> None:
        resolved = resolve("PL/SQL", "SELECT TOP 1 * FROM dbo.t")
        assert resolved.key == "oracle"
        assert resolved.parser == "oracle"
        assert resolved.source == "frontmatter"
        assert resolved.evidence == ()

    def test_frontmatter_can_force_plain_sql(self) -> None:
        resolved = resolve("sql", "SELECT TOP 1 * FROM dbo.t")
        assert resolved.dialect is GENERIC
        assert resolved.source == "frontmatter"

    @pytest.mark.parametrize("declared", [None, "", "sqlite", 42])
    def test_unknown_declaration_falls_back_to_detection(
        self, declared: object
    ) -> None:
        resolved = resolve(declared, "SELECT id::text FROM t")
        assert resolved.key == "postgres"
        assert resolved.source == "detected"
        assert resolved.evidence == ("::",)

    def test_nothing_to_go_on_is_the_default(self) -> None:
        resolved = resolve(None, "SELECT 1")
        assert resolved.dialect is GENERIC
        assert resolved.source == "default"

    @pytest.mark.parametrize(
        ("declared", "body"),
        [
            ("PL/SQL", "SELECT TOP 1 * FROM dbo.t"),
            ("sql", "SELECT TOP 1 * FROM dbo.t"),
            ("sqlite", "SELECT id::text FROM t"),
            (None, "SELECT 1"),
        ],
    )
    def test_settle_matches_resolve(self, declared: object, body: str) -> None:
        assert settle(declared, detect(body)) == resolve(declared, body)
