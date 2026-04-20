#!/usr/bin/env python3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from service.agent_service.tools.sql_tools import execute_sql_query


def main():
    print("=" * 60)
    print("SQL Query Tool Simple Test")
    print("=" * 60)
    print()

    print("[Test 1] show_databases")
    result = execute_sql_query({"mode": "show_databases"})
    print("  Success:", result.get("error") is None)
    if result.get("error"):
        print("  Error:", result.get("error"))
    else:
        print("  Result:", str(result.get("result", ""))[:500])
    print()

    print("[Test 2] show_tables (BTManager)")
    result = execute_sql_query({"mode": "show_tables", "database": "BTManager"})
    print("  Success:", result.get("error") is None)
    if result.get("error"):
        print("  Error:", result.get("error"))
    else:
        print("  Result:", str(result.get("result", ""))[:500])
    print()

    print("[Test 3] describe TO_Org_User")
    result = execute_sql_query({"mode": "describe", "database": "BTManager", "table": "TO_Org_User"})
    print("  Success:", result.get("error") is None)
    if result.get("error"):
        print("  Error:", result.get("error"))
    else:
        print("  Result:", str(result.get("result", ""))[:500])
    print()

    print("[Test 4] query SELECT * FROM TO_Org_User LIMIT 3")
    result = execute_sql_query({
        "mode": "query",
        "database": "BTManager",
        "query": "SELECT * FROM TO_Org_User LIMIT 3"
    })
    print("  Success:", result.get("error") is None)
    if result.get("error"):
        print("  Error:", result.get("error"))
    else:
        print("  Result:", str(result.get("result", ""))[:1000])
    print()

    print("=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
