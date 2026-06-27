#!/usr/bin/env python3
"""
SQL Tool 单元测试
纯逻辑测试，不需要数据库连接，直接测试sql_tools.py中的纯函数
共36个用例
"""

import sys
import os
from pathlib import Path

# 添加backend路径到sys.path
BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

import unittest
from service.agent_service.tools.sql_tools import (
    validate_sql,
    _validate_identifier,
    _parse_limit,
    _parse_positive_int,
    _build_region_filter,
    _escape_like,
    _safe_value,
    SQLToolsConfig,
    PermissionResult,
    DANGEROUS_KEYWORDS,
)


class TestSQLValidation(unittest.TestCase):
    """U01-U15: validate_sql 安全校验"""

    def test_U01_empty_sql(self):
        ok, msg = validate_sql("", "query")
        self.assertFalse(ok)
        self.assertIn("不能为空", msg)

    def test_U02_whitespace_only_sql(self):
        ok, msg = validate_sql("   ", "query")
        self.assertFalse(ok)

    def test_U03_insert_blocked(self):
        ok, msg = validate_sql("INSERT INTO test VALUES(1)", "query")
        self.assertFalse(ok)

    def test_U04_delete_blocked(self):
        ok, msg = validate_sql("DELETE FROM test WHERE id=1", "query")
        self.assertFalse(ok)

    def test_U05_semicolon_blocked(self):
        ok, msg = validate_sql("SELECT * FROM test; DROP TABLE test", "query")
        self.assertFalse(ok)
        self.assertIn("分号", msg)

    def test_U06_dangerous_keywords_all_blocked(self):
        for kw in DANGEROUS_KEYWORDS:
            ok, _ = validate_sql(f"SELECT * FROM test {kw} something", "query")
            self.assertFalse(ok, f"Dangerous keyword {kw} should be blocked")

    def test_U07_show_databases_valid(self):
        ok, msg = validate_sql("SHOW DATABASES", "show_databases")
        self.assertTrue(ok, msg)

    def test_U08_show_databases_rejects_select(self):
        ok, msg = validate_sql("SELECT 1", "show_databases")
        self.assertFalse(ok)

    def test_U09_show_tables_valid(self):
        ok, msg = validate_sql("SHOW TABLES", "show_tables")
        self.assertTrue(ok, msg)

    def test_U10_show_tables_with_from_valid(self):
        ok, msg = validate_sql("SHOW TABLES FROM testdb", "show_tables")
        self.assertTrue(ok, msg)

    def test_U11_describe_valid(self):
        ok, msg = validate_sql("DESCRIBE test_table", "describe")
        self.assertTrue(ok, msg)

    def test_U12_desc_valid(self):
        ok, msg = validate_sql("DESC test_table", "describe")
        self.assertTrue(ok, msg)

    def test_U13_show_create_valid(self):
        ok, msg = validate_sql("SHOW CREATE TABLE test_table", "show_create")
        self.assertTrue(ok, msg)

    def test_U14_case_insensitive_select(self):
        ok, msg = validate_sql("sElEcT * FrOm test_table", "query")
        self.assertTrue(ok, msg)

    def test_U15_update_blocked(self):
        ok, msg = validate_sql("UPDATE test SET a=1", "query")
        self.assertFalse(ok)


class TestLimitParsing(unittest.TestCase):
    """U16-U23: _parse_limit / _parse_positive_int 参数解析"""

    def test_U16_none_returns_default(self):
        self.assertEqual(_parse_limit(None), 100)

    def test_U17_non_numeric_returns_default(self):
        self.assertEqual(_parse_limit("abc"), 100)

    def test_U18_negative_returns_default(self):
        self.assertEqual(_parse_limit(-1), 100)

    def test_U19_zero_returns_default(self):
        self.assertEqual(_parse_limit(0), 100)

    def test_U20_normal_value_unchanged(self):
        self.assertEqual(_parse_limit(500), 500)

    def test_U21_exact_max_allowed(self):
        self.assertEqual(_parse_limit(1000), 1000)

    def test_U22_over_max_truncated(self):
        self.assertEqual(_parse_limit(1001), 1000)

    def test_U23_positive_int_parsing(self):
        self.assertEqual(_parse_positive_int(None, default=200, max_value=500), 200)
        self.assertEqual(_parse_positive_int("-5", default=200, max_value=500), 200)
        self.assertEqual(_parse_positive_int(100, default=200, max_value=500), 100)
        self.assertEqual(_parse_positive_int(600, default=200, max_value=500), 500)


class TestIdentifierValidation(unittest.TestCase):
    """U24-U27: _validate_identifier 标识符校验"""

    def test_U24_empty_invalid(self):
        ok, msg = _validate_identifier("", "表名")
        self.assertFalse(ok)
        self.assertIn("不能为空", msg)

    def test_U25_starts_with_number_invalid(self):
        ok, msg = _validate_identifier("123abc", "表名")
        self.assertFalse(ok)

    def test_U26_hyphen_invalid(self):
        ok, msg = _validate_identifier("table-name", "表名")
        self.assertFalse(ok)

    def test_U27_valid_identifier(self):
        ok, msg = _validate_identifier("valid_table_123", "表名")
        self.assertTrue(ok, msg)


class TestRegionFilter(unittest.TestCase):
    """U28-U32: _build_region_filter 区域过滤构建"""

    def test_U28_super_user_no_filter(self):
        perm = PermissionResult(permitted=True, error="", is_super=True, allowed_region_ids=[])
        clauses, params = _build_region_filter(perm)
        self.assertEqual(clauses, [])
        self.assertEqual(params, [])

    def test_U29_single_region_filter(self):
        perm = PermissionResult(permitted=True, error="", is_super=False, allowed_region_ids=["123"])
        clauses, params = _build_region_filter(perm)
        self.assertEqual(len(clauses), 1)
        self.assertIn("region_id", clauses[0])
        self.assertEqual(params, ["123"])

    def test_U30_multi_region_filter(self):
        perm = PermissionResult(permitted=True, error="", is_super=False, allowed_region_ids=["1", "2", "3"])
        clauses, params = _build_region_filter(perm)
        self.assertEqual(len(params), 3)
        self.assertIn("1", params)
        self.assertIn("2", params)
        self.assertIn("3", params)

    def test_U31_custom_region_column(self):
        perm = PermissionResult(permitted=True, error="", is_super=False, allowed_region_ids=["101"])
        clauses, params = _build_region_filter(perm, region_column="area_id")
        self.assertIn("area_id", clauses[0])

    def test_U32_empty_regions_no_filter(self):
        perm = PermissionResult(permitted=True, error="", is_super=False, allowed_region_ids=[])
        clauses, params = _build_region_filter(perm)
        self.assertEqual(clauses, [])
        self.assertEqual(params, [])


class TestLikeEscape(unittest.TestCase):
    """U33-U35: _escape_like LIKE转义"""

    def test_U33_backslash_escaped(self):
        result = _escape_like("test\\value")
        self.assertEqual(result, "test\\\\value")

    def test_U34_percent_escaped(self):
        result = _escape_like("100%")
        self.assertEqual(result, "100\\%")

    def test_U35_underscore_escaped(self):
        result = _escape_like("test_value")
        self.assertEqual(result, "test\\_value")


class TestSafeValue(unittest.TestCase):
    """_safe_value 值截断测试"""

    def test_short_string_unchanged(self):
        result = _safe_value("hello", max_length=100)
        self.assertEqual(result, "hello")

    def test_long_string_truncated(self):
        long_str = "a" * 200
        result = _safe_value(long_str, max_length=100)
        self.assertEqual(len(result), 100)

    def test_none_returns_null_string(self):
        result = _safe_value(None, max_length=100)
        self.assertEqual(result, "NULL")

    def test_dict_serialized_to_json(self):
        data = {"key": "value"}
        result = _safe_value(data, max_length=100)
        self.assertIn("key", result)
        self.assertIn("value", result)


class TestConfigSingleton(unittest.TestCase):
    """测试SQLToolsConfig单例模式"""

    def test_singleton_returns_same_instance(self):
        c1 = SQLToolsConfig()
        c2 = SQLToolsConfig()
        self.assertIs(c1, c2)

    def test_config_list_databases(self):
        cfg = SQLToolsConfig()
        dbs = cfg.list_databases()
        self.assertIsInstance(dbs, list)
        self.assertGreater(len(dbs), 0)


def run_unit_tests():
    """运行所有单元测试，返回(passed, failed, total)"""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed
    return passed, failed, total


if __name__ == "__main__":
    p, f, t = run_unit_tests()
    print(f"\n{'='*60}")
    print(f"Unit tests: {p}/{t} passed, {f} failed")
    print(f"{'='*60}")
    sys.exit(0 if f == 0 else 1)
