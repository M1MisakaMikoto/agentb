import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pathlib import Path

from .registry import ToolDefinition, ToolRegistry


@dataclass
class DatabaseConfig:
    """数据库连接配置"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    charset: str = "utf8mb4"


class SQLToolsConfig:
    """SQL工具配置管理"""

    _instance = None
    _configs: Dict[str, DatabaseConfig] = {}
    _default_database: str = "default"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """从配置文件加载数据库配置"""
        config_path = Path(__file__).parent / "sql_tools_config.json"
        
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                
                self._default_database = config_data.get("default_database", "default")
                
                for db_name, db_config in config_data.get("databases", {}).items():
                    self._configs[db_name] = DatabaseConfig(
                        host=db_config.get("host", "localhost"),
                        port=db_config.get("port", 3306),
                        user=db_config.get("user", "root"),
                        password=db_config.get("password", ""),
                        charset=db_config.get("charset", "utf8mb4"),
                    )
            except Exception as e:
                print(f"[SQLToolsConfig] 加载配置文件失败: {e}")
        
        if not self._configs:
            self._configs["default"] = DatabaseConfig()

    def get_config(self, database: str = None) -> tuple[str, DatabaseConfig]:
        """获取数据库配置"""
        if database:
            if database in self._configs:
                return database, self._configs[database]
            raise KeyError(f"未找到数据库配置: {database}，可用配置: {', '.join(self.list_databases())}")
        return self._default_database, self._configs.get(self._default_database, DatabaseConfig())

    def list_databases(self) -> List[str]:
        """列出所有配置的数据库"""
        return list(self._configs.keys())


DANGEROUS_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE",
    "REPLACE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL", "LOAD_FILE",
    "INTO OUTFILE", "INTO DUMPFILE"
]

SELECT_PATTERN = re.compile(r"^\s*SELECT\s", re.IGNORECASE)


def validate_sql(query: str) -> tuple[bool, str]:
    """
    验证SQL语句安全性
    
    Returns:
        (is_valid, error_message)
    """
    if not query or not query.strip():
        return False, "SQL语句不能为空"
    
    query_upper = query.upper().strip()
    
    if not SELECT_PATTERN.match(query):
        return False, "仅支持SELECT查询语句"
    
    for keyword in DANGEROUS_KEYWORDS:
        if re.search(rf"\b{keyword}\b", query_upper):
            return False, f"SQL语句包含危险关键字: {keyword}"
    
    if ";" in query.rstrip(";"):
        return False, "SQL语句不能包含分号（多语句执行）"
    
    return True, ""


def _parse_limit(limit_value: Any) -> int:
    """解析并规范化 limit 参数"""
    try:
        limit = int(limit_value)
    except (TypeError, ValueError):
        return 100

    if limit <= 0:
        return 100
    if limit > 1000:
        return 1000
    return limit


def _run_async_in_thread(query: str, database: str | None, limit: int) -> dict:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, execute_sql_query_async(query, database, limit))
        return future.result()


async def execute_sql_query_async(
    query: str,
    database: str = None,
    limit: int = 100,
    timeout: int = 30
) -> dict:
    """
    异步执行SQL查询
    
    Args:
        query: SQL查询语句
        database: 数据库名称/连接名
        limit: 返回行数限制
        timeout: 查询超时时间（秒）
    
    Returns:
        {"result": str, "error": str or None}
    """
    is_valid, error_msg = validate_sql(query)
    if not is_valid:
        return {"result": None, "error": error_msg}
    
    config_manager = SQLToolsConfig()
    try:
        db_name, db_config = config_manager.get_config(database)
    except KeyError as e:
        return {"result": None, "error": str(e)}
    
    limit = _parse_limit(limit)
    
    try:
        import aiomysql
    except ImportError:
        return {"result": None, "error": "aiomysql库未安装，请运行: pip install aiomysql"}
    
    conn = None
    try:
        conn = await asyncio.wait_for(
            aiomysql.connect(
                host=db_config.host,
                port=db_config.port,
                user=db_config.user,
                password=db_config.password,
                db=db_name,
                charset=db_config.charset,
                connect_timeout=10,
            ),
            timeout=timeout
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await asyncio.wait_for(
                cursor.execute(query),
                timeout=timeout
            )
            
            rows = await asyncio.wait_for(
                cursor.fetchmany(limit),
                timeout=timeout
            )
            
            total_rows = len(rows)
            
            if not rows:
                return {
                    "result": f"查询执行成功，数据库 [{db_name}] 返回 0 行数据。",
                    "error": None
                }
            
            columns = list(rows[0].keys()) if rows else []
            
            result_lines = [
                f"SQL查询结果（数据库: {db_name}，返回 {total_rows} 行）：",
                "",
                "字段: " + " | ".join(columns),
                "-" * 80
            ]
            
            for i, row in enumerate(rows, 1):
                row_values = []
                for col in columns:
                    val = row.get(col)
                    if val is None:
                        val_str = "NULL"
                    elif isinstance(val, (dict, list)):
                        val_str = json.dumps(val, ensure_ascii=False)[:50]
                    else:
                        val_str = str(val)[:100]
                    row_values.append(val_str)
                
                result_lines.append(f"{i}. " + " | ".join(row_values))
            
            result_lines.append("")
            result_lines.append(f"--- 共 {total_rows} 行 ---")
            
            return {
                "result": "\n".join(result_lines),
                "error": None
            }
    
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except aiomysql.Error as e:
        return {"result": None, "error": f"数据库错误: {str(e)}"}
    except Exception as e:
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


def execute_sql_query(tool_args: dict) -> dict:
    """
    执行SQL查询工具（同步包装）
    
    Args:
        tool_args: {
            "query": SQL查询语句,
            "database": 数据库名称（可选）,
            "limit": 返回行数限制（可选，默认100）
        }
    
    Returns:
        {"result": str, "error": str or None}
    """
    query = tool_args.get("query")
    if not query:
        return {"result": None, "error": "缺少 query 参数"}
    
    database = tool_args.get("database")
    limit = _parse_limit(tool_args.get("limit", 100))

    print(f"[Tool] sql_query: database={database}, limit={limit}")
    print(f"[Tool] SQL: {query[:100]}...")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute_sql_query_async(query, database, limit))

    return _run_async_in_thread(query, database, limit)


SQL_TOOLS = {"sql_query"}


def register_sql_tools() -> None:
    """注册SQL工具到ToolRegistry"""
    ToolRegistry.register(
        ToolDefinition(
            name="sql_query",
            description="执行SQL SELECT查询，从业务数据库获取数据。支持多数据库配置。",
            params='sql_query:{"query":"(SQL SELECT语句)","database":"(数据库名称，可选)","limit":"(返回行数限制，默认100)"}',
            category="sql",
            executor=execute_sql_query,
        )
    )
