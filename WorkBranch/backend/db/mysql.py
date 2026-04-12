import asyncio
from typing import List, Optional, Tuple
import aiomysql
from aiomysql import Pool, DictCursor


class MySQLDatabase:
    """MySQL 异步数据库封装类，提供连接池管理和基础操作方法。"""

    def __init__(self, settings_service):
        self._settings = settings_service
        self._pool: Optional[Pool] = None
        self._lock = asyncio.Lock()

    async def init_pool(self) -> None:
        """初始化连接池。"""
        if self._pool is not None:
            return

        async with self._lock:
            if self._pool is not None:
                return

            config = self._settings.get("mysql")
            database = config.get("database", "agentb")
            
            # 先创建数据库（如果不存在）
            conn = await aiomysql.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 3306),
                user=config.get("user", "root"),
                password=config.get("password", ""),
                charset="utf8mb4",
                autocommit=True,
            )
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            finally:
                conn.close()
            
            # 创建连接池
            self._pool = await aiomysql.create_pool(
                host=config.get("host", "localhost"),
                port=config.get("port", 3306),
                user=config.get("user", "root"),
                password=config.get("password", ""),
                db=database,
                minsize=config.get("min_pool_size", 5),
                maxsize=config.get("max_pool_size", 20),
                pool_recycle=config.get("pool_recycle", 3600),
                echo=config.get("echo", False),
                charset="utf8mb4",
                autocommit=True,
            )

    async def close_pool(self) -> None:
        """关闭连接池。"""
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def ensure_pool(self) -> None:
        """确保连接池已初始化。"""
        if self._pool is None:
            await self.init_pool()

    async def execute(self, sql: str, params: Optional[Tuple] = None) -> int:
        """执行 SQL 语句（INSERT, UPDATE, DELETE），返回最后插入的 ID 或受影响的行数。"""
        await self.ensure_pool()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                return cursor.lastrowid

    async def fetch_all(self, sql: str, params: Optional[Tuple] = None) -> List[dict]:
        """执行查询并返回所有结果（字典列表）。"""
        await self.ensure_pool()
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    async def fetch_one(self, sql: str, params: Optional[Tuple] = None) -> Optional[dict]:
        """执行查询并返回单个结果（字典）。"""
        await self.ensure_pool()
        async with self._pool.acquire() as conn:
            async with conn.cursor(DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def execute_many(self, sql: str, params_list: List[Tuple]) -> int:
        """批量执行 SQL 语句。"""
        await self.ensure_pool()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(sql, params_list)
                return cursor.rowcount

    async def init_tables(self) -> None:
        """初始化数据库表结构。"""
        await self.ensure_pool()
        
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        name VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                ''')

                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        user_id INTEGER NOT NULL,
                        title VARCHAR(255) DEFAULT '新会话',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                ''')

                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS conversations (
                        id VARCHAR(36) PRIMARY KEY,
                        session_id INTEGER NOT NULL,
                        workspace_id VARCHAR(36),
                        user_content TEXT NOT NULL,
                        assistant_content LONGTEXT,
                        thinking_content LONGTEXT,
                        state ENUM('pending', 'running', 'completed', 'failed', 'cancelled') DEFAULT 'pending',
                        error TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                ''')

                try:
                    await cursor.execute('''
                        CREATE INDEX idx_sessions_user_id ON sessions(user_id)
                    ''')
                except Exception:
                    pass
                    
                try:
                    await cursor.execute('''
                        CREATE INDEX idx_conversations_session_id ON conversations(session_id)
                    ''')
                except Exception:
                    pass
                    
                try:
                    await cursor.execute('''
                        CREATE INDEX idx_conversations_created_at ON conversations(created_at)
                    ''')
                except Exception:
                    pass

            await conn.commit()
