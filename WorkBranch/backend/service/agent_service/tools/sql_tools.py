import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Literal, Callable, Awaitable
from dataclasses import dataclass

from .registry import ToolDefinition, ToolRegistry
from singleton import get_settings_service


QueryMode = Literal["query", "show_databases", "show_tables", "describe", "show_create", "facility_trend", "facility_report"]
MODE_SET = {"query", "show_databases", "show_tables", "describe", "show_create", "facility_trend", "facility_report"}
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_FACILITY_TABLE = "facility_detail"
GROUP_BY_SET = {"hour", "day", "device_type", "device"}


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

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._configs = {}
            cls._instance._default_database = "default"
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """从 SettingsService 加载数据库配置"""
        settings = get_settings_service()
        self._configs = {}

        try:
            self._default_database = settings.get("agent_tools:sql:default_database")
        except KeyError:
            self._default_database = "default"

        try:
            databases = settings.get("agent_tools:sql:databases")
            if isinstance(databases, dict):
                for db_name, db_config in databases.items():
                    self._configs[db_name] = DatabaseConfig(
                        host=db_config.get("host", "localhost"),
                        port=db_config.get("port", 3306),
                        user=db_config.get("user", "root"),
                        password=db_config.get("password", ""),
                        charset=db_config.get("charset", "utf8mb4"),
                    )
        except KeyError:
            pass

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
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "REPLACE",
    "GRANT",
    "REVOKE",
    "EXEC",
    "EXECUTE",
    "CALL",
    "LOAD_FILE",
    "INTO OUTFILE",
    "INTO DUMPFILE",
]

SELECT_PATTERN = re.compile(r"^\s*SELECT\s", re.IGNORECASE)
SHOW_DATABASES_PATTERN = re.compile(r"^\s*SHOW\s+DATABASES\s*$", re.IGNORECASE)
SHOW_TABLES_PATTERN = re.compile(r"^\s*SHOW\s+TABLES(\s+FROM\s+\S+)?\s*$", re.IGNORECASE)
DESCRIBE_PATTERN = re.compile(r"^\s*(DESCRIBE|DESC)\s+\S+\s*$", re.IGNORECASE)
SHOW_CREATE_TABLE_PATTERN = re.compile(r"^\s*SHOW\s+CREATE\s+TABLE\s+\S+\s*$", re.IGNORECASE)
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def _parse_positive_int(value: Any, default: int, max_value: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if num <= 0:
        return default
    if num > max_value:
        return max_value
    return num


def _run_async_in_thread(coro) -> dict:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def _safe_value(value: Any, max_length: int = 100) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:max_length]
    return str(value)[:max_length]


def _extract_first_value(row: Dict[str, Any], default: str = "未知") -> str:
    if not row:
        return default
    values = list(row.values())
    if not values:
        return default
    return str(values[0])


def _validate_identifier(name: str, label: str) -> tuple[bool, str]:
    if not name:
        return False, f"{label} 不能为空"
    if not SAFE_IDENTIFIER_PATTERN.match(name):
        return False, f"{label} 包含非法字符，仅允许字母、数字、下划线，且不能以数字开头"
    return True, ""


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_facility_common_filters(tool_args: dict) -> tuple[list[str], list[Any], str | None]:
    where = []
    params: list[Any] = []

    start_time = tool_args.get("start_time")
    end_time = tool_args.get("end_time")
    if start_time and end_time:
        where.append("add_time >= %s AND add_time < %s")
        params.extend([start_time, end_time])
    elif start_time:
        where.append("add_time >= %s")
        params.append(start_time)
    elif end_time:
        where.append("add_time < %s")
        params.append(end_time)

    device_type_name = tool_args.get("device_type_name")
    if device_type_name:
        where.append("device_type_name = %s")
        params.append(device_type_name)

    device_id = tool_args.get("device_id")
    if device_id:
        where.append("device_id = %s")
        params.append(device_id)

    content_keyword = tool_args.get("content_keyword")
    if content_keyword:
        where.append("content LIKE %s ESCAPE '\\\\'")
        params.append(f"%{_escape_like(str(content_keyword))}%")

    return where, params, None


async def _execute_with_connection(
    db_config: DatabaseConfig,
    timeout: int,
    operation: Callable[[Any], Awaitable[dict]],
    database: str | None = None,
) -> dict:
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
                connect_timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
            ),
            timeout=timeout,
        )
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            return await operation(cursor)
    except asyncio.TimeoutError:
        return {"result": None, "error": f"查询超时（超过 {timeout} 秒）"}
    except Exception as e:
        if e.__class__.__name__ in {"Error", "OperationalError", "ProgrammingError"}:
            return {"result": None, "error": f"数据库错误: {str(e)}"}
        return {"result": None, "error": f"查询执行失败: {str(e)}"}
    finally:
        if conn:
            conn.close()


async def _execute_query_async(
    query: str,
    database: str,
    db_config: DatabaseConfig,
    limit: int,
    timeout: int,
) -> dict:
    """执行SELECT查询"""

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute(query), timeout=timeout)
        rows = await asyncio.wait_for(cursor.fetchmany(limit), timeout=timeout)

        total_rows = len(rows)
        if not rows:
            return {"result": f"查询执行成功，数据库 [{database}] 返回 0 行数据。", "error": None}

        columns = list(rows[0].keys())
        result_lines = [
            f"SQL查询结果（数据库: {database}，返回 {total_rows} 行）：",
            "",
            "字段: " + " | ".join(columns),
            "-" * 80,
        ]

        for i, row in enumerate(rows, 1):
            row_values = [_safe_value(row.get(col)) for col in columns]
            result_lines.append(f"{i}. " + " | ".join(row_values))

        result_lines.append("")
        result_lines.append(f"--- 共 {total_rows} 行 ---")
        return {"result": "\n".join(result_lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation, database=database)


async def _execute_facility_report_async(
    table: str,
    db_config: DatabaseConfig,
    database: str,
    timeout: int,
    tool_args: dict,
) -> dict:
    where, params, err = _build_facility_common_filters(tool_args)
    if err:
        return {"result": None, "error": err}

    limit = _parse_limit(tool_args.get("limit", 200))
    select_cols = "add_time, device_type_name, device_id, content"
    sql = f"SELECT {select_cols} FROM `{table}`"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY add_time DESC"
    sql += " LIMIT %s"
    params.append(limit)

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute(sql, params), timeout=timeout)
        rows = await asyncio.wait_for(cursor.fetchall(), timeout=timeout)
        if not rows:
            return {"result": f"报告查询完成，数据库 [{database}] 无匹配数据。", "error": None}

        columns = list(rows[0].keys())
        lines = [f"facility_report 明细（库: {database}，表: {table}，行数: {len(rows)}）", "", "字段: " + " | ".join(columns), "-" * 80]
        for i, row in enumerate(rows, 1):
            lines.append(f"{i}. " + " | ".join(_safe_value(row.get(col), 120) for col in columns))
        lines.append("")
        lines.append(f"--- 共 {len(rows)} 行 ---")
        return {"result": "\n".join(lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation, database=database)


async def _execute_facility_trend_async(
    table: str,
    db_config: DatabaseConfig,
    database: str,
    timeout: int,
    tool_args: dict,
) -> dict:
    group_by = str(tool_args.get("group_by", "day")).lower()
    if group_by not in GROUP_BY_SET:
        return {"result": None, "error": f"group_by 无效: {group_by}，有效值: {', '.join(sorted(GROUP_BY_SET))}"}

    where, params, err = _build_facility_common_filters(tool_args)
    if err:
        return {"result": None, "error": err}

    bucket_expr = {
        "hour": "DATE_FORMAT(add_time, '%Y-%m-%d %H:00:00')",
        "day": "DATE_FORMAT(add_time, '%Y-%m-%d')",
        "device_type": "device_type_name",
        "device": "device_id",
    }[group_by]
    bucket_alias = {
        "hour": "hour_bucket",
        "day": "day_bucket",
        "device_type": "device_type_name",
        "device": "device_id",
    }[group_by]

    limit = _parse_positive_int(tool_args.get("limit", 200), default=200, max_value=1000)
    sql = f"SELECT {bucket_expr} AS `{bucket_alias}`, COUNT(*) AS `total_count` FROM `{table}`"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" GROUP BY `{bucket_alias}` ORDER BY `{bucket_alias}` ASC LIMIT %s"
    params.append(limit)

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute(sql, params), timeout=timeout)
        rows = await asyncio.wait_for(cursor.fetchall(), timeout=timeout)
        if not rows:
            return {"result": f"趋势查询完成，数据库 [{database}] 无匹配数据。", "error": None}

        lines = [f"facility_trend 趋势（库: {database}，表: {table}，维度: {group_by}）", "", f"{bucket_alias} | total_count", "-" * 60]
        for row in rows:
            lines.append(f"{_safe_value(row.get(bucket_alias), 64)} | {_safe_value(row.get('total_count'), 32)}")
        lines.append("")
        lines.append(f"--- 共 {len(rows)} 组 ---")
        return {"result": "\n".join(lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation, database=database)


async def _execute_show_databases_async(db_config: DatabaseConfig, timeout: int) -> dict:
    """执行 SHOW DATABASES"""

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute("SHOW DATABASES"), timeout=timeout)
        rows = await asyncio.wait_for(cursor.fetchall(), timeout=timeout)

        if not rows:
            return {"result": "未找到任何数据库。", "error": None}

        result_lines = ["数据库列表：", "-" * 40]
        for i, row in enumerate(rows, 1):
            result_lines.append(f"{i}. {_extract_first_value(row)}")

        result_lines.append("")
        result_lines.append(f"--- 共 {len(rows)} 个数据库 ---")
        return {"result": "\n".join(result_lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation)


async def _execute_show_tables_async(
    database: str,
    db_config: DatabaseConfig,
    timeout: int,
) -> dict:
    """执行 SHOW TABLES"""

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute("SHOW TABLES"), timeout=timeout)
        rows = await asyncio.wait_for(cursor.fetchall(), timeout=timeout)

        if not rows:
            return {"result": f"数据库 [{database}] 中未找到任何表。", "error": None}

        result_lines = [f"数据库 [{database}] 表列表：", "-" * 40]
        for i, row in enumerate(rows, 1):
            result_lines.append(f"{i}. {_extract_first_value(row)}")

        result_lines.append("")
        result_lines.append(f"--- 共 {len(rows)} 个表 ---")
        return {"result": "\n".join(result_lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation, database=database)


async def _execute_describe_async(
    table: str,
    database: str,
    db_config: DatabaseConfig,
    timeout: int,
) -> dict:
    """执行 DESCRIBE table"""

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute(f"DESCRIBE `{table}`"), timeout=timeout)
        rows = await asyncio.wait_for(cursor.fetchall(), timeout=timeout)

        if not rows:
            return {"result": None, "error": f"表 [{table}] 不存在或无权限访问"}

        result_lines = [f"表 [{table}] 结构：", ""]
        result_lines.append(f"{'字段':<25} {'类型':<20} {'允许空':<8} {'键':<8} {'默认值':<15} {'额外'}")
        result_lines.append("-" * 100)

        for row in rows:
            field = str(row.get("Field", ""))[:24]
            type_ = str(row.get("Type", ""))[:19]
            nullable = str(row.get("Null", ""))[:7]
            key = str(row.get("Key", ""))[:7]
            default = str(row.get("Default", "") or "")[:14]
            extra = str(row.get("Extra", ""))
            result_lines.append(f"{field:<25} {type_:<20} {nullable:<8} {key:<8} {default:<15} {extra}")

        result_lines.append("")
        result_lines.append(f"--- 共 {len(rows)} 个字段 ---")
        return {"result": "\n".join(result_lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation, database=database)


async def _execute_show_create_async(
    table: str,
    database: str,
    db_config: DatabaseConfig,
    timeout: int,
) -> dict:
    """执行 SHOW CREATE TABLE"""

    async def operation(cursor: Any) -> dict:
        await asyncio.wait_for(cursor.execute(f"SHOW CREATE TABLE `{table}`"), timeout=timeout)
        row = await asyncio.wait_for(cursor.fetchone(), timeout=timeout)

        if not row:
            return {"result": None, "error": f"表 [{table}] 不存在或无权限访问"}

        values = list(row.values())
        create_sql = str(values[1]) if len(values) >= 2 else ""
        result_lines = [f"表 [{table}] 建表语句：", "", create_sql]
        return {"result": "\n".join(result_lines), "error": None}

    return await _execute_with_connection(db_config, timeout, operation, database=database)


async def execute_sql_query_async(
    mode: QueryMode,
    query: str,
    database: str,
    table: str,
    limit: int,
    timeout: int,
    tool_args: dict | None = None,
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
    tool_args = tool_args or {}

    try:
        db_name, db_config = config_manager.get_config(database)
    except KeyError as e:
        return {"result": None, "error": str(e)}

    if mode == "query":
        is_valid, error_msg = validate_sql(query, mode)
        if not is_valid:
            return {"result": None, "error": error_msg}
        return await _execute_query_async(query, db_name, db_config, limit, timeout)

    if mode == "show_databases":
        return await _execute_show_databases_async(db_config, timeout)

    if mode == "show_tables":
        return await _execute_show_tables_async(db_name, db_config, timeout)

    if mode == "describe":
        if not table:
            return {"result": None, "error": "describe模式需要 table 参数"}
        is_valid, error_msg = _validate_identifier(table, "table")
        if not is_valid:
            return {"result": None, "error": error_msg}
        return await _execute_describe_async(table, db_name, db_config, timeout)

    if mode == "show_create":
        if not table:
            return {"result": None, "error": "show_create模式需要 table 参数"}
        is_valid, error_msg = _validate_identifier(table, "table")
        if not is_valid:
            return {"result": None, "error": error_msg}
        return await _execute_show_create_async(table, db_name, db_config, timeout)

    if mode in {"facility_trend", "facility_report"}:
        table_name = table  # 兼容现有 table 入参优先
        if not table_name:
            table_name = str(tool_args.get("table_name") or DEFAULT_FACILITY_TABLE)
        is_valid, error_msg = _validate_identifier(table_name, "table_name")
        if not is_valid:
            return {"result": None, "error": error_msg}
        if mode == "facility_trend":
            return await _execute_facility_trend_async(table_name, db_config, db_name, timeout, tool_args)
        return await _execute_facility_report_async(table_name, db_config, db_name, timeout, tool_args)

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
    timeout = DEFAULT_TIMEOUT_SECONDS

    if mode not in MODE_SET:
        return {"result": None, "error": f"无效的 mode 参数: {mode}，有效值: {', '.join(sorted(MODE_SET))}"}

    if mode == "query" and not query:
        return {"result": None, "error": "query模式需要 query 参数"}

    print(f"[Tool] sql_query: mode={mode}, database={database}, table={table}, limit={limit}")
    if query:
        print(f"[Tool] SQL: {query}")

    coro = execute_sql_query_async(mode, query, database, table, limit, timeout, tool_args)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    return _run_async_in_thread(coro)


SQL_TOOLS = {"sql_query"}


def register_sql_tools() -> None:
    """注册SQL工具到ToolRegistry"""
    ToolRegistry.register(
        ToolDefinition(
            name="sql_query",
            description="执行只读 SQL 查询或结构探查；支持 query(SELECT)、show_databases、show_tables、describe、show_create，以及面向facility_detail索引查询的 facility_trend/ facility_report",
            params='sql_query:{"mode":"(query|show_databases|show_tables|describe|show_create|facility_trend|facility_report，必填)","query":"(query 模式必填)","database":"(数据库名称，可选)","table":"(describe/show_create 可用)","table_name":"(facility_* 模式可选，默认 facility_detail)","start_time":"(facility_* 可选，例 2026-05-01 00:00:00)","end_time":"(facility_* 可选，建议与start_time一起传)","device_type_name":"(facility_* 可选，等值过滤)","device_id":"(facility_* 可选，等值过滤)","content_keyword":"(facility_* 可选，LIKE过滤)","group_by":"(facility_trend 可选: hour|day|device_type|device)","limit":"(query/facility_* 生效，最大1000)"}',
            category="sql",
            executor=execute_sql_query,
        )
    )
