from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from service.agent_service.tools.sql_tools import (
    _parse_show_tables_query,
    normalize_mysql_query,
    validate_sql,
)


class TestSqlToolsMysqlNormalization(unittest.TestCase):
    def test_rewrites_top_level_select_top_to_mysql_limit(self) -> None:
        query = "SELECT TOP 1 Id FROM TB_User ORDER BY Id"

        normalized = normalize_mysql_query(query)

        self.assertEqual("SELECT Id FROM TB_User ORDER BY Id LIMIT 1", normalized)
        self.assertEqual((True, ""), validate_sql(normalized, "query"))

    def test_rewrites_subquery_select_top_to_mysql_limit(self) -> None:
        query = (
            "SELECT RealName FROM TB_UserInfo "
            "WHERE UserId = (SELECT TOP 1 Id FROM TB_User ORDER BY Id)"
        )

        normalized = normalize_mysql_query(query)

        self.assertEqual(
            "SELECT RealName FROM TB_UserInfo "
            "WHERE UserId = (SELECT Id FROM TB_User ORDER BY Id LIMIT 1)",
            normalized,
        )
        self.assertEqual((True, ""), validate_sql(normalized, "query"))

    def test_does_not_rewrite_select_top_inside_string_literal(self) -> None:
        query = "SELECT 'SELECT TOP 1 Id FROM TB_User' AS sample_text"

        normalized = normalize_mysql_query(query)

        self.assertEqual(query, normalized)


class TestSqlToolsShowTables(unittest.TestCase):
    def test_accepts_show_tables_like_in_show_tables_mode(self) -> None:
        query = "SHOW TABLES LIKE '%User%'"

        self.assertEqual((True, ""), validate_sql(query, "show_tables"))
        self.assertEqual((None, "%User%"), _parse_show_tables_query(query))

    def test_parses_show_tables_from_database_with_like(self) -> None:
        query = "SHOW TABLES FROM BTManager LIKE '%Unit%'"

        self.assertEqual((True, ""), validate_sql(query, "show_tables"))
        self.assertEqual(("BTManager", "%Unit%"), _parse_show_tables_query(query))


if __name__ == "__main__":
    unittest.main()
