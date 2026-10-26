from __future__ import annotations

import pytest

from sqlshelf.core.sql_segments import (
    Segment,
    segments,
    split_batches,
    split_statements,
)

# Cases 1 to 6 of docs/query-diagram.md.

SALES_BY_CUSTOMER = """\
SELECT c.ClienteID, c.Nome, r.Nome AS Regiao,
       COUNT(p.PedidoID) AS Pedidos, SUM(pg.Valor) AS Pago
FROM vendas.Pedidos p
INNER JOIN vendas.Clientes c ON c.ClienteID = p.ClienteID
LEFT JOIN vendas.Pagamentos pg ON pg.PedidoID = p.PedidoID
LEFT JOIN dbo.Regiao r ON r.RegiaoID = c.RegiaoID
WHERE p.DataPedido >= @inicio
  AND p.Status <> 'C'
GROUP BY c.ClienteID, c.Nome, r.Nome
"""

SYSTEM_HEALTH = """\
SET NOCOUNT ON;
BEGIN
    DECLARE @path NVARCHAR(260);

    SELECT TOP 1 CAST(xet.target_data AS XML) AS XMLDATA
    INTO #SystemHealthSessionData
    FROM sys.dm_xe_session_targets xet
    JOIN sys.dm_xe_sessions xe
      ON (xe.address = xet.event_session_address)
    WHERE xe.name = 'system_health'
      AND xet.target_name = 'ring_buffer';

    ;WITH CTE_HealthSession (EventXML) AS
    (
        SELECT C.query('.') AS EventXML
        FROM #SystemHealthSessionData a
        CROSS APPLY a.XMLDATA.nodes('/RingBufferTarget/event') AS T(C)
    )
    SELECT EventXML.value('(/event/@timestamp)[1]', 'datetime') AS EventTime
    FROM CTE_HealthSession;

    DROP TABLE #SystemHealthSessionData;
END
"""

LTV_ETL = """\
SELECT o.PedidoID, o.ClienteID, o.DataPedido, o.Total
INTO #pedidos_validos
FROM stg.Pedidos o
INNER JOIN stg.Pagamentos pg ON pg.PedidoID = o.PedidoID
WHERE pg.Status = 'OK';
GO

MERGE dim.Cliente AS d
USING stg.Clientes AS src ON d.ClienteID = src.ClienteID
WHEN MATCHED THEN
    UPDATE SET d.Email = src.Email, d.Cidade = src.Cidade
WHEN NOT MATCHED THEN
    INSERT (ClienteID, Email, Cidade) VALUES (src.ClienteID, src.Email, src.Cidade);
GO

WITH agg AS (
    SELECT ClienteID, MIN(DataPedido) AS PrimeiroPedido, SUM(Total) AS ValorVida
    FROM #pedidos_validos
    GROUP BY ClienteID
)
INSERT INTO mart.ClienteLtv (ClienteID, PrimeiroPedido, ValorVida, Cidade)
SELECT a.ClienteID, a.PrimeiroPedido, a.ValorVida, d.Cidade
FROM agg a
INNER JOIN dim.Cliente d ON d.ClienteID = a.ClienteID;
GO

UPDATE t
SET t.Faixa = f.Faixa
FROM mart.ClienteLtv t
JOIN ref.FaixasValor f ON t.ValorVida BETWEEN f.Minimo AND f.Maximo
GO
"""

LOAD_PROCEDURE = """\
CREATE PROCEDURE dbo.usp_CarregaFact
    @data DATE
AS
BEGIN
    SET NOCOUNT ON;

    SELECT s.Id, s.Valor
    INTO #t
    FROM stg.Fact s
    WHERE s.Data = @data;

    BEGIN TRY
        INSERT INTO dbo.Fact (Id, Valor, Faixa)
        SELECT t.Id, t.Valor,
               CASE WHEN t.Valor > 1000 THEN 'alto' ELSE 'baixo' END
        FROM #t t
        LEFT JOIN dbo.Fact f ON f.Id = t.Id
        WHERE f.Id IS NULL
    END TRY
    BEGIN CATCH
        THROW;
    END CATCH
END
"""

IF_ELSE = """\
IF EXISTS (SELECT 1 FROM dbo.Config WHERE Chave = 'modo')
BEGIN
    UPDATE dbo.Config SET Valor = 'novo' WHERE Chave = 'modo'
END
ELSE
    SELECT Chave, Valor FROM dbo.Config
"""

DBA_SCRIPT = """\
ALTER TABLE dbo.Pedidos WITH CHECK ADD CONSTRAINT FK_Pedidos_Clientes
    FOREIGN KEY (ClienteID) REFERENCES dbo.Clientes (ClienteID)
BACKUP DATABASE Vendas TO DISK = 'D:\\backup\\vendas.bak' WITH COMPRESSION, STATS = 10
UPDATE STATISTICS dbo.Pedidos WITH FULLSCAN
DECLARE c CURSOR FOR SELECT name FROM sys.databases WHERE database_id > 4
SELECT p.PedidoID, p.Total FROM dbo.Pedidos p WITH (NOLOCK)
WHERE p.Total > 0 OPTION (RECOMPILE)
"""


def _split(sql: str) -> list[Segment]:
    """The statements of *sql*, checking every offset against the text."""
    found, rejected = split_statements(sql)
    assert rejected == []
    for seg in found:
        assert sql[seg.start : seg.end] == seg.text
    return found


class TestReferenceScripts:
    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            pytest.param(
                SALES_BY_CUSTOMER,
                [
                    (
                        "SELECT",
                        "SELECT c.ClienteID",
                        "GROUP BY c.ClienteID, c.Nome, r.Nome",
                    )
                ],
                id="1-select",
            ),
            pytest.param(
                SYSTEM_HEALTH,
                [
                    ("SELECT", "SELECT TOP 1", "xet.target_name = 'ring_buffer'"),
                    ("SELECT", "WITH CTE_HealthSession", "FROM CTE_HealthSession"),
                ],
                id="2-system-health",
            ),
            pytest.param(
                LTV_ETL,
                [
                    ("SELECT", "SELECT o.PedidoID", "WHERE pg.Status = 'OK'"),
                    ("MERGE", "MERGE dim.Cliente", "src.Email, src.Cidade)"),
                    ("INSERT", "WITH agg AS (", "ON d.ClienteID = a.ClienteID"),
                    ("UPDATE", "UPDATE t\nSET", "AND f.Maximo"),
                ],
                id="3-etl-with-go",
            ),
            pytest.param(
                LOAD_PROCEDURE,
                [
                    ("SELECT", "SELECT s.Id", "WHERE s.Data = @data"),
                    ("INSERT", "INSERT INTO dbo.Fact", "WHERE f.Id IS NULL"),
                ],
                id="4-procedure",
            ),
            pytest.param(
                IF_ELSE,
                [
                    ("UPDATE", "UPDATE dbo.Config", "WHERE Chave = 'modo'"),
                    ("SELECT", "SELECT Chave, Valor", "FROM dbo.Config"),
                ],
                id="5-if-else",
            ),
            pytest.param(
                DBA_SCRIPT,
                [
                    ("SELECT", "SELECT name", "WHERE database_id > 4"),
                    ("SELECT", "SELECT p.PedidoID", "OPTION (RECOMPILE)"),
                ],
                id="6-dba-script",
            ),
        ],
    )
    def test_statements(self, sql: str, expected: list[tuple[str, str, str]]) -> None:
        found = _split(sql)
        assert [seg.kind for seg in found] == [kind for kind, _, _ in expected]
        for seg, (_, head, tail) in zip(found, expected, strict=True):
            assert seg.text.startswith(head)
            assert seg.text.endswith(tail)

    def test_case_does_not_cut_the_insert(self) -> None:
        insert = _split(LOAD_PROCEDURE)[1]
        assert "ELSE 'baixo' END\n        FROM #t t\n        LEFT JOIN dbo.Fact" in (
            insert.text
        )

    def test_crlf_script_keeps_its_offsets(self) -> None:
        sql = LTV_ETL.replace("\n", "\r\n")
        assert [seg.kind for seg in _split(sql)] == [
            "SELECT",
            "MERGE",
            "INSERT",
            "UPDATE",
        ]


def _statements(sql: str) -> list[tuple[str, str]]:
    return [(seg.kind, seg.text) for seg in _split(sql)]


class TestBoundaries:
    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            pytest.param(
                "SELECT 1;SELECT 2",
                [("SELECT", "SELECT 1"), ("SELECT", "SELECT 2")],
                id="semicolon",
            ),
            pytest.param(
                "SELECT a FROM t\nSELECT b FROM u",
                [("SELECT", "SELECT a FROM t"), ("SELECT", "SELECT b FROM u")],
                id="no-semicolon",
            ),
            pytest.param(
                "SELECT a FROM t UNION ALL SELECT a FROM u EXCEPT SELECT a FROM v",
                [
                    (
                        "SELECT",
                        "SELECT a FROM t UNION ALL SELECT a FROM u EXCEPT SELECT a FROM v",
                    )
                ],
                id="union",
            ),
            pytest.param(
                "INSERT INTO #r EXEC dbo.usp_Lista 1\nSELECT * FROM #r",
                [
                    ("INSERT", "INSERT INTO #r EXEC dbo.usp_Lista 1"),
                    ("SELECT", "SELECT * FROM #r"),
                ],
                id="insert-exec",
            ),
            pytest.param(
                "INSERT INTO t (a) VALUES (1), (2)\nSELECT a FROM t",
                [
                    ("INSERT", "INSERT INTO t (a) VALUES (1), (2)"),
                    ("SELECT", "SELECT a FROM t"),
                ],
                id="insert-values-then-select",
            ),
            pytest.param(
                "UPDATE t SET a = 1 WHERE b = 2\nSET @n = @@ROWCOUNT",
                [("UPDATE", "UPDATE t SET a = 1 WHERE b = 2")],
                id="update-then-set",
            ),
            pytest.param(
                "DELETE d FROM dbo.T d WHERE d.Data < @corte;",
                [("DELETE", "DELETE d FROM dbo.T d WHERE d.Data < @corte")],
                id="delete",
            ),
            pytest.param(
                "WITH a AS (SELECT 1 AS x), b (y) AS (SELECT x FROM a)\n"
                "SELECT y FROM b",
                [
                    (
                        "SELECT",
                        "WITH a AS (SELECT 1 AS x), b (y) AS (SELECT x FROM a)\n"
                        "SELECT y FROM b",
                    )
                ],
                id="cte-chain",
            ),
            pytest.param(
                "WITH XMLNAMESPACES ('urn:x' AS x) SELECT d.c FROM dbo.D d",
                [
                    (
                        "SELECT",
                        "WITH XMLNAMESPACES ('urn:x' AS x) SELECT d.c FROM dbo.D d",
                    )
                ],
                id="xmlnamespaces",
            ),
            pytest.param(
                "WITH a AS (SELECT 1 AS x)",
                [("WITH", "WITH a AS (SELECT 1 AS x)")],
                id="cte-without-statement",
            ),
            pytest.param(
                "SET NOCOUNT ON\nDELETE FROM dbo.Log WHERE Data < @corte",
                [("DELETE", "DELETE FROM dbo.Log WHERE Data < @corte")],
                id="set-option-on-then-dml",
            ),
            pytest.param(
                "DECLARE @n INT = (SELECT COUNT(*) FROM dbo.T)\n"
                "SET @n = (SELECT MAX(id) FROM dbo.T)\n"
                "WHILE (SELECT COUNT(*) FROM dbo.Q) > 0 BEGIN DELETE TOP (10) FROM dbo.Q END",
                [("DELETE", "DELETE TOP (10) FROM dbo.Q")],
                id="subqueries-in-control-flow",
            ),
            pytest.param(
                "SELECT a FROM t ORDER BY a OFFSET 10 ROWS FETCH NEXT 5 ROWS ONLY\n"
                "FETCH NEXT FROM c INTO @a",
                [
                    (
                        "SELECT",
                        "SELECT a FROM t ORDER BY a OFFSET 10 ROWS FETCH NEXT 5 ROWS ONLY",
                    )
                ],
                id="offset-fetch",
            ),
            pytest.param(
                "SELECT TOP 5 WITH TIES (a + b) AS s FROM t ORDER BY s",
                [("SELECT", "SELECT TOP 5 WITH TIES (a + b) AS s FROM t ORDER BY s")],
                id="with-ties",
            ),
        ],
    )
    def test_statements(self, sql: str, expected: list[tuple[str, str]]) -> None:
        assert _statements(sql) == expected

    @pytest.mark.parametrize(
        "sql",
        [
            "",
            "   \n\t",
            "-- SELECT * FROM t\n/* DELETE FROM t */",
            "CREATE TABLE #t (a INT, b VARCHAR(10))\nDROP TABLE #t",
            "PRINT 'SELECT * FROM t'",
            "EXEC('SELECT * FROM t')",
        ],
    )
    def test_nothing_to_find(self, sql: str) -> None:
        assert _split(sql) == []


class TestCase:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT CASE WHEN a = 1 THEN 'x' ELSE 'y' END AS c FROM t",
            "SELECT CASE b WHEN 1 THEN CASE WHEN c > 0 THEN 2 ELSE 3 END ELSE 4 END FROM t",
            "SELECT (CASE WHEN a = 1 THEN 1 END) AS c FROM t",
        ],
    )
    def test_case_stays_in_the_statement(self, sql: str) -> None:
        assert _statements(sql) == [("SELECT", sql)]

    def test_else_of_case_inside_if_block(self) -> None:
        sql = (
            "IF @x = 1 BEGIN SELECT CASE WHEN a = 1 THEN 1 ELSE 0 END FROM t END\n"
            "ELSE UPDATE t SET a = 0"
        )
        assert _statements(sql) == [
            ("SELECT", "SELECT CASE WHEN a = 1 THEN 1 ELSE 0 END FROM t"),
            ("UPDATE", "UPDATE t SET a = 0"),
        ]

    def test_unclosed_case_ends_at_the_semicolon(self) -> None:
        # Halfway through typing: the CASE has no END yet, and must not take the
        # END of the block below for its own.
        sql = "SELECT CASE WHEN a = 1 THEN 1 FROM t;\nIF @x = 1 BEGIN UPDATE t SET a = 1 END"
        assert _statements(sql) == [
            ("SELECT", "SELECT CASE WHEN a = 1 THEN 1 FROM t"),
            ("UPDATE", "UPDATE t SET a = 1"),
        ]


class TestWith:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT a FROM dbo.T WITH (NOLOCK) WHERE a > 0",
            "SELECT a FROM dbo.T t WITH (NOLOCK, INDEX(ix_a)) JOIN dbo.U u WITH (NOLOCK) ON u.a = t.a",
        ],
    )
    def test_table_hint_stays_in_the_statement(self, sql: str) -> None:
        assert _statements(sql) == [("SELECT", sql)]

    @pytest.mark.parametrize(
        "sql",
        [
            "ALTER TABLE dbo.T WITH CHECK CHECK CONSTRAINT FK_T_U",
            "ALTER TABLE dbo.T WITH NOCHECK ADD CONSTRAINT FK_T_U FOREIGN KEY (a) REFERENCES dbo.U (a)",
            "BACKUP DATABASE Vendas TO DISK = 'v.bak' WITH COMPRESSION",
            "CREATE PROCEDURE dbo.p WITH RECOMPILE AS BEGIN RETURN 0 END",
        ],
    )
    def test_option_is_not_a_statement(self, sql: str) -> None:
        assert _split(sql) == []

    def test_view_with_schemabinding(self) -> None:
        sql = "CREATE VIEW dbo.v WITH SCHEMABINDING AS SELECT a FROM dbo.T"
        assert _statements(sql) == [("SELECT", "SELECT a FROM dbo.T")]

    @pytest.mark.parametrize(
        "command", ["UPDATE STATISTICS", "update statistics", "UPDATE\n  STATISTICS"]
    )
    def test_update_statistics_is_not_dml(self, command: str) -> None:
        sql = f"SELECT 1\n{command} dbo.T WITH FULLSCAN\nSELECT 2"
        assert _statements(sql) == [("SELECT", "SELECT 1"), ("SELECT", "SELECT 2")]


class TestQuoted:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM t WHERE s = 'DELETE' AND n = 'END;' AND m = N'BEGIN'",
            'SELECT [End], [Select], "Update" FROM [Order Details]',
            "SELECT a FROM t WHERE b = ')' AND c = '('",
        ],
    )
    def test_quoted_words_are_not_keywords(self, sql: str) -> None:
        assert _statements(sql) == [("SELECT", sql)]

    def test_quoted_cte_name(self) -> None:
        sql = "WITH [last orders] AS (SELECT 1 AS x) SELECT x FROM [last orders]"
        assert _statements(sql) == [("SELECT", sql)]


class TestActions:
    """DML words that name an action, not a statement."""

    @pytest.mark.parametrize(
        "sql",
        [
            "GRANT SELECT, INSERT, UPDATE ON dbo.T TO app",
            "DENY DELETE ON dbo.T TO app",
            "REVOKE SELECT ON SCHEMA::vendas FROM app",
            "ALTER TABLE dbo.T ADD CONSTRAINT FK FOREIGN KEY (a) REFERENCES dbo.U (a)"
            " ON DELETE CASCADE ON UPDATE NO ACTION",
            "ALTER TABLE dbo.T ADD CONSTRAINT FK FOREIGN KEY (a) REFERENCES dbo.U (a)"
            " ON DELETE SET NULL",
            "BULK INSERT dbo.T FROM 'C:\\dados\\t.csv' WITH (FIELDTERMINATOR = ';')",
            "ALTER PARTITION FUNCTION pf_data() MERGE RANGE ('2026-01-01')",
        ],
    )
    def test_no_statement(self, sql: str) -> None:
        assert _split(sql) == []

    def test_trigger_events(self) -> None:
        sql = (
            "CREATE TRIGGER dbo.trg_T ON dbo.T AFTER INSERT, UPDATE, DELETE AS\n"
            "BEGIN\n"
            "    IF UPDATE(a) INSERT INTO dbo.Log (id) SELECT id FROM inserted\n"
            "END"
        )
        assert _statements(sql) == [
            ("INSERT", "INSERT INTO dbo.Log (id) SELECT id FROM inserted")
        ]

    @pytest.mark.parametrize(
        "header",
        ["FOR INSERT", "INSTEAD OF DELETE"],
    )
    def test_trigger_event_forms(self, header: str) -> None:
        sql = f"CREATE TRIGGER dbo.trg ON dbo.T {header} AS DELETE FROM dbo.Q"
        assert _statements(sql) == [("DELETE", "DELETE FROM dbo.Q")]

    def test_cursor_for_update(self) -> None:
        sql = "DECLARE c CURSOR FOR SELECT a FROM dbo.T FOR UPDATE OF a\n" "OPEN c"
        assert _statements(sql) == [("SELECT", "SELECT a FROM dbo.T FOR UPDATE OF a")]


class TestBatches:
    def test_split_at_go_lines(self) -> None:
        sql = "SELECT 1\nGO\nSELECT 2\n"
        assert split_batches(sql) == [(0, "SELECT 1\n"), (11, "\nSELECT 2\n")]

    @pytest.mark.parametrize(
        "separator",
        ["GO", "go", "  GO  ", "GO 5", "GO\t10", "GO -- next batch", "GO--x"],
    )
    def test_go_forms(self, separator: str) -> None:
        sql = f"SELECT 1\n{separator}\nSELECT 2"
        assert len(split_batches(sql)) == 2

    @pytest.mark.parametrize(
        "line",
        ["GOTO fim", "SELECT 1 AS go", "-- GO", "GO SELECT 2", "GO 5 x"],
    )
    def test_not_a_go_line(self, line: str) -> None:
        sql = f"SELECT 1\n{line}\nSELECT 2"
        assert len(split_batches(sql)) == 1

    def test_go_with_crlf(self) -> None:
        sql = "SELECT 1\r\nGO\r\nSELECT 2\r\nGO 3 -- again\r\nSELECT 3\r\n"
        assert [text.strip() for _, text in split_batches(sql)] == [
            "SELECT 1",
            "SELECT 2",
            "SELECT 3",
        ]
        assert [seg.text for seg in _split(sql)] == ["SELECT 1", "SELECT 2", "SELECT 3"]

    def test_blank_batches_are_dropped(self) -> None:
        assert split_batches("GO\n\nGO\nSELECT 1\nGO\n") == [(6, "\nSELECT 1\n")]

    def test_go_alone_in_a_statement_line_ends_it(self) -> None:
        sql = "SELECT a\nFROM t\nGO\nSELECT b FROM u"
        assert _statements(sql) == [
            ("SELECT", "SELECT a\nFROM t"),
            ("SELECT", "SELECT b FROM u"),
        ]

    def test_rejected_batch_is_skipped_alone(self) -> None:
        # Case 13: an unclosed string loses its own batch, not the script.
        sql = "SELECT 1\nGO\nSELECT 'open\nGO\nSELECT 3"
        found, rejected = split_statements(sql)
        assert [seg.text for seg in found] == ["SELECT 1", "SELECT 3"]
        assert rejected == [sql.index("\nSELECT 'open")]
        for seg in found:
            assert sql[seg.start : seg.end] == seg.text

    def test_unclosed_comment_is_rejected(self) -> None:
        found, rejected = split_statements("SELECT 1 /* never closed")
        assert found == []
        assert rejected == [0]

    def test_segments_shifts_offsets(self) -> None:
        found = segments("  SELECT 1", offset=100)
        assert found == [Segment("SELECT", 102, 110, "SELECT 1")]


class TestNeverLoses:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT ((((",
            "))) SELECT 1",
            "END END ELSE SELECT 1",
            "CASE CASE CASE",
            "WITH",
            "WITH x",
            "WITH x (a",
            "!@#$% {{{",
            "INSERT",
            "MERGE",
        ],
    )
    def test_garbage_does_not_raise(self, sql: str) -> None:
        found, _ = split_statements(sql)
        for seg in found:
            assert sql[seg.start : seg.end] == seg.text


# ---------------------------------------------------------------------------
# Dialects
# ---------------------------------------------------------------------------


def _in(sql: str, dialect: str) -> list[tuple[str, str]]:
    found, rejected = split_statements(sql, dialect)
    assert rejected == []
    for seg in found:
        assert sql[seg.start : seg.end] == seg.text
    return [(seg.kind, seg.text) for seg in found]


class TestBatchesByDialect:
    def test_oracle_slash_lines(self) -> None:
        sql = "SELECT 1 FROM dual;\n/\nSELECT 2 FROM dual;\n  /  \n"
        assert [b.strip() for _, b in split_batches(sql, "oracle")] == [
            "SELECT 1 FROM dual;",
            "SELECT 2 FROM dual;",
        ]

    def test_slash_is_no_batch_end_in_tsql(self) -> None:
        assert len(split_batches("SELECT 1\n/\nSELECT 2", "tsql")) == 1

    def test_go_is_no_batch_end_in_oracle(self) -> None:
        assert len(split_batches("SELECT 1 FROM dual\nGO\nSELECT 2", "oracle")) == 1

    @pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
    def test_mysql_delimiter(self, dialect: str) -> None:
        sql = (
            "SELECT 1;\n"
            "DELIMITER $$\n"
            "CREATE PROCEDURE p() BEGIN SELECT * FROM inv; END$$\n"
            "CREATE PROCEDURE q() BEGIN SELECT 2; END$$\n"
            "DELIMITER ;\n"
            "SELECT 3;"
        )
        batches = split_batches(sql, dialect)
        assert [b.strip() for _, b in batches] == [
            "SELECT 1;",
            "CREATE PROCEDURE p() BEGIN SELECT * FROM inv; END",
            "CREATE PROCEDURE q() BEGIN SELECT 2; END",
            "SELECT 3;",
        ]
        for offset, batch in batches:
            assert sql[offset : offset + len(batch)] == batch
        assert "DELIMITER" not in " ".join(text for _, text in _in(sql, dialect))

    @pytest.mark.parametrize("dialect", ["postgres", "bigquery"])
    def test_one_batch(self, dialect: str) -> None:
        sql = "SELECT 1;\nGO\nSELECT 2;\n/\n"
        assert split_batches(sql, dialect) == [(0, sql)]


class TestStatementsByDialect:
    def test_postgres_function_body(self) -> None:
        sql = (
            "CREATE FUNCTION f() RETURNS void AS $$\n"
            "BEGIN\n"
            "  DELETE FROM t WHERE a = 1;\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;\n"
            "SELECT 2;"
        )
        assert _in(sql, "postgres") == [
            ("DELETE", "DELETE FROM t WHERE a = 1"),
            ("SELECT", "SELECT 2"),
        ]

    def test_postgres_do_block_with_tag(self) -> None:
        sql = "DO $body$ BEGIN UPDATE t SET a = 1; END $body$;"
        assert _in(sql, "postgres") == [("UPDATE", "UPDATE t SET a = 1")]

    def test_postgres_dollar_string_stays_a_string(self) -> None:
        sql = "SELECT $$ UPDATE t SET a = 1; $$ AS code;"
        assert _in(sql, "postgres") == [
            ("SELECT", "SELECT $$ UPDATE t SET a = 1; $$ AS code")
        ]

    @pytest.mark.parametrize(
        "sql,dialect",
        [
            (
                "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 2",
                "postgres",
            ),
            ("INSERT INTO t (a) VALUES (1) ON CONFLICT DO NOTHING", "postgres"),
            ("INSERT INTO t (a) VALUES (1) ON DUPLICATE KEY UPDATE a = 2", "mysql"),
            ("INSERT INTO t SET a = 1, b = 2", "mysql"),
            ("SELECT a FROM t MINUS SELECT a FROM u", "oracle"),
            ("SELECT a FROM t UNION DISTINCT SELECT a FROM u", "bigquery"),
            ("SELECT IF(a > 1, 'x', 'y') AS f FROM t", "mysql"),
            ("SELECT IF(a > 1, 'x', 'y') AS f FROM t", "bigquery"),
        ],
    )
    def test_one_statement(self, sql: str, dialect: str) -> None:
        assert [text for _, text in _in(sql + ";\nSELECT 9;", dialect)] == [
            sql,
            "SELECT 9",
        ]

    def test_oracle_block(self) -> None:
        sql = (
            "DECLARE\n"
            "  v NUMBER;\n"
            "BEGIN\n"
            "  SELECT COUNT(*) INTO v FROM emp;\n"
            "  IF v > 0 THEN\n"
            "    UPDATE emp SET sal = sal * 1.1;\n"
            "  ELSIF v < 0 THEN\n"
            "    DELETE FROM emp;\n"
            "  END IF;\n"
            "EXCEPTION\n"
            "  WHEN OTHERS THEN NULL;\n"
            "END;\n"
            "/\n"
        )
        assert [kind for kind, _ in _in(sql, "oracle")] == [
            "SELECT",
            "UPDATE",
            "DELETE",
        ]

    def test_oracle_q_quote_keeps_offsets(self) -> None:
        sql = "SELECT q'[it's; END]' AS x FROM dual;\nSELECT 2 FROM dual;"
        assert _in(sql, "oracle") == [
            ("SELECT", "SELECT q'[it's; END]' AS x FROM dual"),
            ("SELECT", "SELECT 2 FROM dual"),
        ]

    def test_bigquery_script(self) -> None:
        sql = (
            "DECLARE x INT64 DEFAULT 1;\n"
            "BEGIN\n"
            "  SELECT 1;\n"
            "EXCEPTION WHEN ERROR THEN\n"
            "  SELECT 2;\n"
            "END;\n"
            "IF x > 0 THEN\n"
            "  SELECT 3;\n"
            "ELSEIF x < 0 THEN\n"
            "  SELECT 4;\n"
            "END IF;\n"
            "LOOP\n"
            "  DELETE FROM t WHERE TRUE;\n"
            "END LOOP;\n"
            "WHILE x > 0 DO\n"
            "  INSERT INTO u VALUES (1);\n"
            "END WHILE;"
        )
        assert [text for _, text in _in(sql, "bigquery")] == [
            "SELECT 1",
            "SELECT 2",
            "SELECT 3",
            "SELECT 4",
            "DELETE FROM t WHERE TRUE",
            "INSERT INTO u VALUES (1)",
        ]

    def test_if_statement_is_not_a_function_in_tsql(self) -> None:
        assert _in("IF (SELECT 1) = 1 SELECT 2", "tsql") == [("SELECT", "SELECT 2")]

    @pytest.mark.parametrize(
        "sql",
        [
            # sqlglot reads what follows these, up to the next ";", as one
            # string; the statement in there must still show.
            "IF @x = 1\nBEGIN\n  UPDATE t SET a = 1;\nEND\nSELECT b FROM u",
            "PRINT 'x'\nSELECT b FROM u",
        ],
    )
    def test_statement_after_a_command_word(self, sql: str) -> None:
        assert ("SELECT", "SELECT b FROM u") in _in(sql, "tsql")
