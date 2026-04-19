import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List, Literal
from dataclasses import dataclass
from pathlib import Path

from .registry import ToolDefinition, ToolRegistry


QueryMode = Literal["query", "show_databases", "show_tables", "describe", "show_create"]


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
SHOW_DATABASES_PATTERN = re.compile(r"^\s*SHOW\s+DATABASES\s*$", re.IGNORECASE)
SHOW_TABLES_PATTERN = re.compile(r"^\s*SHOW\s+TABLES(\s+FROM\s+\S+)?\s*$", re.IGNORECASE)
DESCRIBE_PATTERN = re.compile(r"^\s*(DESCRIBE|DESC)\s+\S+\s*$", re.IGNORECASE)
SHOW_CREATE_TABLE_PATTERN = re.compile(r"^\s*SHOW\s+CREATE\s+TABLE\s+\S+\s*$", re.IGNORECASE)


def validate_sql(query: str, mode: QueryMode = "query") -> tuple[bool, str]:
    """
    验证SQL语句安全性
    
    Args:
        query: SQL语句
        mode: 查询模式
    
    Returns:
        (is_valid, error_message)
    """
    if not query or not query.strip():
        return False, "SQL语句不能为空"
    
    query_upper = query.upper().strip()
    
    if mode == "query":
        if not SELECT_PATTERN.match(query):
            return False, "query模式仅支持SELECT查询语句"
    elif mode == "show_databases":
        if not SHOW_DATABASES_PATTERN.match(query):
            return False, "show_databases模式仅支持 SHOW DATABASES 语句"
    elif mode == "show_tables":
        if not SHOW_TABLES_PATTERN.match(query):
            return False, "show_tables模式仅支持 SHOW TABLES [FROM db] 语句"
    elif mode == "describe":
        if not DESCRIBE_PATTERN.match(query):
            return False, "describe模式仅支持 DESCRIBE/DESC table 语句"
    elif mode == "show_create":
        if not SHOW_CREATE_TABLE_PATTERN.match(query):
            return False, "show_create模式仅支持 SHOW CREATE TABLE 语句"
    
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


def _run_async_in_thread(coro) -> dict:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


async def _execute_query_async(
    query: str,
    database: str,
    db_config: DatabaseConfig,
    limit: int,
    timeout: int
) -> dict:
    """执行SELECT查询"""
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
                db=database,
                charset=db_config.charset,
                connect_timeout=10,
            ),
            timeout=timeout
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await asyncio.wait_for(cursor.execute(query), timeout=timeout)
            rows = await asyncio.wait_for(cursor.fetchmany(limit), timeout=timeout)
            
            total_rows = len(rows)
            if not rows:
                return {"result": f"查询执行成功，数据库 [{database}] 返回 0 行数据。", "error": None}
            
            columns = list(rows[0].keys()) if rows else []
            result_lines = [
                f"SQL查询结果（数据库: {database}，返回 {total_rows} 行）：",
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
            
            return {"result": "\n".join(result_lines), "error": None}
    
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except aiomysql.Error as e:
        return {"result": None, "error": f"数据库错误: {str(e)}"}
    except Exception as e:
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


async def _execute_show_databases_async(
    db_config: DatabaseConfig,
    timeout: int
) -> dict:
    """执行 SHOW DATABASES"""
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
                charset=db_config.charset,
                connect_timeout=10,
            ),
            timeout=timeout
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SHOW DATABASES")
            rows = await cursor.fetchall()
            
            if not rows:
                return {"result": "未找到任何数据库。", "error": None}
            
            result_lines = ["数据库列表：", "-" * 40]
            for i, row in enumerate(rows, 1):
                db_name = list(row.values())[0] if row else "未知"
                result_lines.append(f"{i}. {db_name}")
            
            result_lines.append("")
            result_lines.append(f"--- 共 {len(rows)} 个数据库 ---")
            
            return {"result": "\n".join(result_lines), "error": None}
    
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except aiomysql.Error as e:
        return {"result": None, "error": f"数据库错误: {str(e)}"}
    except Exception as e:
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


async def _execute_show_tables_async(
    database: str,
    db_config: DatabaseConfig,
    timeout: int
) -> dict:
    """执行 SHOW TABLES"""
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
                db=database,
                charset=db_config.charset,
                connect_timeout=10,
            ),
            timeout=timeout
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SHOW TABLES")
            rows = await cursor.fetchall()
            
            if not rows:
                return {"result": f"数据库 [{database}] 中未找到任何表。", "error": None}
            
            result_lines = [f"数据库 [{database}] 表列表：", "-" * 40]
            for i, row in enumerate(rows, 1):
                table_name = list(row.values())[0] if row else "未知"
                result_lines.append(f"{i}. {table_name}")
            
            result_lines.append("")
            result_lines.append(f"--- 共 {len(rows)} 个表 ---")
            
            return {"result": "\n".join(result_lines), "error": None}
    
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except aiomysql.Error as e:
        return {"result": None, "error": f"数据库错误: {str(e)}"}
    except Exception as e:
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


async def _execute_describe_async(
    table: str,
    database: str,
    db_config: DatabaseConfig,
    timeout: int
) -> dict:
    """执行 DESCRIBE table"""
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
                db=database,
                charset=db_config.charset,
                connect_timeout=10,
            ),
            timeout=timeout
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(f"DESCRIBE `{table}`")
            rows = await cursor.fetchall()
            
            if not rows:
                return {"result": None, "error": f"表 [{table}] 不存在或无权限访问"}
            
            result_lines = [f"表 [{table}] 结构：", ""]
            result_lines.append(f"{'字段':<25} {'类型':<20} {'允许空':<8} {'键':<8} {'默认值':<15} {'额外'}")
            result_lines.append("-" * 100)
            
            for row in rows:
                field = str(row.get("Field", ""))[:24]
                type_ = str(row.get("Type", ""))[:19]
                null = str(row.get("Null", ""))[:7]
                key = str(row.get("Key", ""))[:7]
                default = str(row.get("Default", "") or "")[:14]
                extra = str(row.get("Extra", ""))
                result_lines.append(f"{field:<25} {type_:<20} {null:<8} {key:<8} {default:<15} {extra}")
            
            result_lines.append("")
            result_lines.append(f"--- 共 {len(rows)} 个字段 ---")
            
            return {"result": "\n".join(result_lines), "error": None}
    
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except aiomysql.Error as e:
        return {"result": None, "error": f"数据库错误: {str(e)}"}
    except Exception as e:
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


async def _execute_show_create_async(
    table: str,
    database: str,
    db_config: DatabaseConfig,
    timeout: int
) -> dict:
    """执行 SHOW CREATE TABLE"""
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
                db=database,
                charset=db_config.charset,
                connect_timeout=10,
            ),
            timeout=timeout
        )
        
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(f"SHOW CREATE TABLE `{table}`")
            row = await cursor.fetchone()
            
            if not row:
                return {"result": None, "error": f"表 [{table}] 不存在或无权限访问"}
            
            create_sql = list(row.values())[1] if len(row) >= 2 else ""
            
            result_lines = [f"表 [{table}] 建表语句：", ""]
            result_lines.append(create_sql)
            
            return {"result": "\n".join(result_lines), "error": None}
    
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except aiomysql.Error as e:
        return {"result": None, "error": f"数据库错误: {str(e)}"}
    except Exception as e:
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


async def execute_sql_query_async(
    mode: QueryMode,
    query: str,
    database: str,
    table: str,
    limit: int,
    timeout: int
) -> dict:
    """
    异步执行SQL查询
    
    Args:
        mode: 查询模式
        query: SQL查询语句（query模式使用）
        database: 数据库名称
        table: 表名（describe/show_create模式使用）
        limit: 返回行数限制
        timeout: 查询超时时间（秒）
    
    Returns:
        {"result": str, "error": str or None}
    """
    config_manager = SQLToolsConfig()
    
    try:
        db_name, db_config = config_manager.get_config(database)
    except KeyError as e:
        return {"result": None, "error": str(e)}
    
    if mode == "query":
        is_valid, error_msg = validate_sql(query, mode)
        if not is_valid:
            return {"result": None, "error": error_msg}
        return await _execute_query_async(query, db_name, db_config, limit, timeout)
    
    elif mode == "show_databases":
        return await _execute_show_databases_async(db_config, timeout)
    
    elif mode == "show_tables":
        return await _execute_show_tables_async(db_name, db_config, timeout)
    
    elif mode == "describe":
        if not table:
            return {"result": None, "error": "describe模式需要 table 参数"}
        return await _execute_describe_async(table, db_name, db_config, timeout)
    
    elif mode == "show_create":
        if not table:
            return {"result": None, "error": "show_create模式需要 table 参数"}
        return await _execute_show_create_async(table, db_name, db_config, timeout)
    
    else:
        return {"result": None, "error": f"未知的查询模式: {mode}"}


def execute_sql_query(tool_args: dict) -> dict:
    """
    执行SQL查询工具（统一入口）
    
    Args:
        tool_args: {
            "mode": "query|show_databases|show_tables|describe|show_create",
            "query": "SQL语句（query模式必填）",
            "database": "数据库名称（可选）",
            "table": "表名（describe/show_create模式必填）",
            "limit": "返回行数限制（query模式可选，默认100）"
        }
    
    Returns:
        {"result": str, "error": str or None}
    """
    mode: QueryMode = tool_args.get("mode", "query")
    query = tool_args.get("query", "")
    database = tool_args.get("database")
    table = tool_args.get("table")
    limit = _parse_limit(tool_args.get("limit", 100))
    timeout = 30
    
    valid_modes = {"query", "show_databases", "show_tables", "describe", "show_create"}
    if mode not in valid_modes:
        return {"result": None, "error": f"无效的 mode 参数: {mode}，有效值: {', '.join(valid_modes)}"}
    
    if mode == "query" and not query:
        return {"result": None, "error": "query模式需要 query 参数"}
    
    print(f"[Tool] sql_query: mode={mode}, database={database}, table={table}, limit={limit}")
    if query:
        print(f"[Tool] SQL: {query[:100]}...")
    
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute_sql_query_async(mode, query, database, table, limit, timeout))
    
    return _run_async_in_thread(execute_sql_query_async(mode, query, database, table, limit, timeout))


SQL_TOOLS = {"sql_query"}


def register_sql_tools() -> None:
    """注册SQL工具到ToolRegistry"""
    ToolRegistry.register(
        ToolDefinition(
            name="sql_query",
            description="执行只读 SQL 查询或结构探查；支持 query(SELECT)、show_databases(列出数据库)、show_tables(列出表)、describe(查看表结构)、show_create(查看建表语句)",
            params='sql_query:{"mode":"(query|show_databases|show_tables|describe|show_create，必填)","query":"(query 模式必填；其他模式忽略)","database":"(数据库名称，可选；show_databases 模式忽略，show_tables/describe/show_create 使用该库或默认库)","table":"(表名；describe/show_create 模式必填，其他模式忽略)","limit":"(仅 query 模式生效，默认100，最大1000)"}',
            category="sql",
            executor=execute_sql_query,
        )
    )
