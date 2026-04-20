"""
SQL Tools Integration Tests - 真实数据库连接测试

测试目标:
1. 真实数据库连接测试
2. 所有模式的真实执行测试
3. 数据库创建、查询、清理

运行条件:
- 需要可用的 MySQL 服务器
- 需要正确的数据库连接配置（从 setting.json 读取）

Usage:
    pytest test_sql_tools_integration.py -v -s
    pytest test_sql_tools_integration.py -v -k "show_databases"
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent.parent
SETTINGS_PATH = PROJECT_ROOT / "setting.json"

sys.path.insert(0, str(BACKEND_DIR))

TOOLS_DIR = BACKEND_DIR / "service" / "agent_service" / "tools"

sys.modules["service"] = type(sys)("service")
sys.modules["service.agent_service"] = type(sys)("service.agent_service")
sys.modules["service.agent_service.tools"] = type(sys)("service.agent_service.tools")


def _import_module_from_file(module_name: str, file_path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


registry = _import_module_from_file("service.agent_service.tools.registry", TOOLS_DIR / "registry.py")
sys.modules["service.agent_service.tools.registry"] = registry

sql_tools = _import_module_from_file("service.agent_service.tools.sql_tools", TOOLS_DIR / "sql_tools.py")
sys.modules["service.agent_service.tools.sql_tools"] = sql_tools

DatabaseConfig = sql_tools.DatabaseConfig
SQLToolsConfig = sql_tools.SQLToolsConfig
execute_sql_query = sql_tools.execute_sql_query
execute_sql_query_async = sql_tools.execute_sql_query_async


def get_mysql_connection_settings() -> dict:
    """从 setting.json 获取 MySQL 连接设置"""
    if SETTINGS_PATH.exists():
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        mysql_settings = settings.get("mysql", {})
    else:
        mysql_settings = {}
    
    return {
        "host": os.environ.get("SQL_TEST_HOST", mysql_settings.get("host", "localhost")),
        "port": int(os.environ.get("SQL_TEST_PORT", mysql_settings.get("port", 3306))),
        "user": os.environ.get("SQL_TEST_USER", mysql_settings.get("user", "root")),
        "password": os.environ.get("SQL_TEST_PASSWORD", mysql_settings.get("password", "")),
        "charset": os.environ.get("SQL_TEST_CHARSET", "utf8mb4"),
    }


def check_mysql_available() -> bool:
    """检查 MySQL 是否可用"""
    try:
        import aiomysql
        return True
    except ImportError:
        return False


MYSQL_AVAILABLE = check_mysql_available()
MYSQL_SETTINGS = get_mysql_connection_settings()


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def test_database():
    """创建测试数据库并插入测试数据"""
    if not MYSQL_AVAILABLE:
        pytest.skip("aiomysql 未安装，跳过集成测试")
    
    import aiomysql
    
    db_name = f"agentb_sql_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}".lower()
    
    conn = await aiomysql.connect(
        host=MYSQL_SETTINGS["host"],
        port=MYSQL_SETTINGS["port"],
        user=MYSQL_SETTINGS["user"],
        password=MYSQL_SETTINGS["password"],
        charset=MYSQL_SETTINGS["charset"],
        autocommit=True,
    )
    
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print(f"\n[Integration] 创建测试数据库: {db_name}")
    except Exception as e:
        print(f"\n[Integration] 创建数据库失败: {e}")
        pytest.skip(f"无法创建测试数据库: {e}")
    finally:
        conn.close()
    
    seeded_conn = await aiomysql.connect(
        host=MYSQL_SETTINGS["host"],
        port=MYSQL_SETTINGS["port"],
        user=MYSQL_SETTINGS["user"],
        password=MYSQL_SETTINGS["password"],
        db=db_name,
        charset=MYSQL_SETTINGS["charset"],
        autocommit=True,
    )
    
    try:
        async with seeded_conn.cursor() as cursor:
            await cursor.execute("""
                CREATE TABLE users (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(64) NOT NULL,
                    email VARCHAR(128),
                    age INT
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            await cursor.execute("""
                CREATE TABLE orders (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    user_id INT NOT NULL,
                    product VARCHAR(64) NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    status VARCHAR(16) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            await cursor.executemany(
                "INSERT INTO users (name, email, age) VALUES (%s, %s, %s)",
                [
                    ("Alice", "alice@example.com", 28),
                    ("Bob", "bob@example.com", 35),
                    ("Charlie", "charlie@example.com", 22),
                ]
            )
            await cursor.executemany(
                "INSERT INTO orders (user_id, product, amount, status) VALUES (%s, %s, %s, %s)",
                [
                    (1, "Laptop", 1299.99, "paid"),
                    (1, "Mouse", 29.99, "paid"),
                    (2, "Keyboard", 89.99, "pending"),
                    (3, "Monitor", 399.99, "paid"),
                ]
            )
        print(f"[Integration] 插入测试数据完成")
    finally:
        seeded_conn.close()
    
    yield {
        "database": db_name,
        "connection": MYSQL_SETTINGS,
    }
    
    cleanup_conn = await aiomysql.connect(
        host=MYSQL_SETTINGS["host"],
        port=MYSQL_SETTINGS["port"],
        user=MYSQL_SETTINGS["user"],
        password=MYSQL_SETTINGS["password"],
        charset=MYSQL_SETTINGS["charset"],
        autocommit=True,
    )
    try:
        async with cleanup_conn.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        print(f"[Integration] 清理测试数据库: {db_name}")
    finally:
        cleanup_conn.close()


@pytest.fixture
def sql_config(test_database, tmp_path):
    """临时写入 SQL 工具配置"""
    config_file = TOOLS_DIR / "sql_tools_config.json"
    original_content = None
    
    if config_file.exists():
        original_content = config_file.read_text(encoding="utf-8")
    
    config_data = {
        "default_database": test_database["database"],
        "databases": {
            test_database["database"]: {
                "host": test_database["connection"]["host"],
                "port": test_database["connection"]["port"],
                "user": test_database["connection"]["user"],
                "password": test_database["connection"]["password"],
                "charset": test_database["connection"]["charset"],
            }
        }
    }
    
    config_file.write_text(json.dumps(config_data, ensure_ascii=False, indent=4), encoding="utf-8")
    
    SQLToolsConfig._instance = None
    SQLToolsConfig._configs = {}
    SQLToolsConfig._default_database = "default"
    
    yield test_database
    
    if original_content is not None:
        config_file.write_text(original_content, encoding="utf-8")
    
    SQLToolsConfig._instance = None
    SQLToolsConfig._configs = {}
    SQLToolsConfig._default_database = "default"


@pytest.mark.integration
@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="aiomysql 未安装")
class TestRealDatabaseConnection:
    """真实数据库连接测试"""

    @pytest.mark.asyncio
    async def test_show_databases_real(self, sql_config):
        """测试真实 show_databases"""
        result = await execute_sql_query_async(
            mode="show_databases",
            query="",
            database=None,
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] show_databases:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "数据库列表" in result["result"]
        assert sql_config["database"] in result["result"]

    @pytest.mark.asyncio
    async def test_show_tables_real(self, sql_config):
        """测试真实 show_tables"""
        result = await execute_sql_query_async(
            mode="show_tables",
            query="",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] show_tables:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "表列表" in result["result"]
        assert "users" in result["result"]
        assert "orders" in result["result"]

    @pytest.mark.asyncio
    async def test_describe_table_real(self, sql_config):
        """测试真实 describe"""
        result = await execute_sql_query_async(
            mode="describe",
            query="",
            database=sql_config["database"],
            table="users",
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] describe users:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "表 [users] 结构" in result["result"]
        assert "id" in result["result"]
        assert "name" in result["result"]
        assert "email" in result["result"]

    @pytest.mark.asyncio
    async def test_query_select_all_real(self, sql_config):
        """测试真实 SELECT 查询"""
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT * FROM users",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] SELECT * FROM users:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "SQL查询结果" in result["result"]
        assert "Alice" in result["result"]
        assert "Bob" in result["result"]
        assert "Charlie" in result["result"]

    @pytest.mark.asyncio
    async def test_query_with_where_real(self, sql_config):
        """测试真实带 WHERE 的查询"""
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT * FROM users WHERE age > 25",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] SELECT * FROM users WHERE age > 25:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "Alice" in result["result"]
        assert "Bob" in result["result"]
        assert "Charlie" not in result["result"]

    @pytest.mark.asyncio
    async def test_query_aggregation_real(self, sql_config):
        """测试真实聚合查询"""
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT status, COUNT(*) as count, SUM(amount) as total FROM orders GROUP BY status",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] Aggregation query:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "paid" in result["result"]
        assert "pending" in result["result"]

    @pytest.mark.asyncio
    async def test_query_join_real(self, sql_config):
        """测试真实 JOIN 查询"""
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT u.name, o.product, o.amount FROM users u JOIN orders o ON u.id = o.user_id",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] JOIN query:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "Laptop" in result["result"]
        assert "Mouse" in result["result"]

    @pytest.mark.asyncio
    async def test_query_limit_real(self, sql_config):
        """测试真实 LIMIT 限制"""
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT * FROM users",
            database=sql_config["database"],
            table=None,
            limit=2,
            timeout=30
        )
        
        print(f"\n[Result] SELECT with limit=2:")
        print(result["result"])
        
        assert result["error"] is None
        assert result["result"] is not None
        assert "2 行" in result["result"]

    @pytest.mark.asyncio
    async def test_query_empty_result_real(self, sql_config):
        """测试真实空结果"""
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT * FROM users WHERE id > 1000",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] Empty result:")
        print(result["result"])
        
        assert result["error"] is None
        assert "0 行" in result["result"]

    @pytest.mark.asyncio
    async def test_describe_nonexistent_table_real(self, sql_config):
        """测试真实不存在的表"""
        result = await execute_sql_query_async(
            mode="describe",
            query="",
            database=sql_config["database"],
            table="nonexistent_table_xyz",
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] Describe nonexistent table:")
        print(result)
        
        assert result["result"] is None
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_query_null_value_real(self, sql_config):
        """测试真实 NULL 值处理"""
        import aiomysql
        
        conn = await aiomysql.connect(
            host=MYSQL_SETTINGS["host"],
            port=MYSQL_SETTINGS["port"],
            user=MYSQL_SETTINGS["user"],
            password=MYSQL_SETTINGS["password"],
            db=sql_config["database"],
            charset=MYSQL_SETTINGS["charset"],
            autocommit=True,
        )
        
        try:
            async with conn.cursor() as cursor:
                await cursor.execute("INSERT INTO users (name, email, age) VALUES ('TestNull', NULL, NULL)")
        finally:
            conn.close()
        
        result = await execute_sql_query_async(
            mode="query",
            query="SELECT * FROM users WHERE name = 'TestNull'",
            database=sql_config["database"],
            table=None,
            limit=100,
            timeout=30
        )
        
        print(f"\n[Result] NULL value handling:")
        print(result["result"])
        
        assert result["error"] is None
        assert "NULL" in result["result"]


@pytest.mark.integration
@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="aiomysql 未安装")
class TestExecuteSQLQueryEntrypoint:
    """execute_sql_query 入口函数真实测试"""

    def test_show_databases_entrypoint(self, sql_config):
        """测试 show_databases 入口"""
        result = execute_sql_query({"mode": "show_databases"})
        
        print(f"\n[Result] show_databases via entrypoint:")
        print(result["result"])
        
        assert result["error"] is None
        assert "数据库列表" in result["result"]

    def test_query_entrypoint(self, sql_config):
        """测试 query 入口"""
        result = execute_sql_query({
            "mode": "query",
            "query": "SELECT COUNT(*) as total FROM users",
            "database": sql_config["database"],
        })
        
        print(f"\n[Result] query via entrypoint:")
        print(result["result"])
        
        assert result["error"] is None
        assert "total" in result["result"].lower() or "count" in result["result"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
