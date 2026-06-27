#!/usr/bin/env python3
"""
Unit Tests Runner
运行所有单元测试（不需要启动后端服务）
"""

import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).parent
sys.path.insert(0, str(TEST_DIR / "unit_tests"))
sys.path.insert(0, str(TEST_DIR.parent))


def main():
    print("=" * 60)
    print("  SQL Tool Unit Tests")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 加载SQL工具单元测试
    from unit_tests.test_sql_tool_unit import TestSQLValidation, TestLimitParsing
    from unit_tests.test_sql_tool_unit import TestIdentifierValidation, TestRegionFilter
    from unit_tests.test_sql_tool_unit import TestLikeEscape, TestSafeValue, TestConfigSingleton

    suite.addTests(loader.loadTestsFromTestCase(TestSQLValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestLimitParsing))
    suite.addTests(loader.loadTestsFromTestCase(TestIdentifierValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestRegionFilter))
    suite.addTests(loader.loadTestsFromTestCase(TestLikeEscape))
    suite.addTests(loader.loadTestsFromTestCase(TestSafeValue))
    suite.addTests(loader.loadTestsFromTestCase(TestConfigSingleton))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print()
    print("=" * 60)
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
