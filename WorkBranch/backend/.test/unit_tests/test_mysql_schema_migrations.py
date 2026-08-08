import unittest
import asyncio

from db.mysql import MySQLDatabase
from data.conversation_dao import ConversationDAO


class _FakeExecuteAffectedDB:
    def __init__(self):
        self.sql = None
        self.params = None
        self.affected = 1

    async def execute_affected(self, sql, params):
        self.sql = " ".join(sql.split())
        self.params = params
        return self.affected


class ConversationDAOTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_fail_expired_awaiting_marks_failed_with_timeout(self):
        db = _FakeExecuteAffectedDB()
        dao = ConversationDAO(db)

        affected = await dao.fail_expired_awaiting_conversations(7, 600)

        self.assertEqual(affected, 1)
        self.assertIn("state = 'failed'", db.sql)
        self.assertIn("state = 'awaiting_user_input'", db.sql)
        self.assertIn("INTERVAL %s SECOND", db.sql)
        self.assertEqual(db.params[2], 600)


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Cursor:
    def __init__(self, state_type):
        self.state_type = state_type
        self.statements = []
        self.last_sql = ""

    async def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        self.statements.append((self.last_sql, params))

    async def fetchone(self):
        if self.last_sql.startswith("SHOW COLUMNS"):
            params = self.statements[-1][1]
            column = params[0] if params else "state"
            if column == "state":
                return ("state", self.state_type)
            return (column, "varchar(128)")
        if self.last_sql.startswith("SHOW INDEX"):
            return ("index",)
        raise AssertionError(f"Unexpected fetchone after: {self.last_sql}")


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return _AsyncContext(self._cursor)

    async def commit(self):
        return None


class _Pool:
    def __init__(self, cursor):
        self._connection = _Connection(cursor)

    def acquire(self):
        return _AsyncContext(self._connection)


class MySQLSchemaMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def _statements_for(self, state_type):
        cursor = _Cursor(state_type)
        database = MySQLDatabase(settings_service=None)
        database._pool = _Pool(cursor)
        await database.init_tables()
        return [sql for sql, _ in cursor.statements]

    async def test_adds_awaiting_user_input_to_legacy_state_enum(self):
        statements = await self._statements_for(
            "enum('pending','running','completed','failed','cancelled')"
        )
        alters = [sql for sql in statements if "MODIFY COLUMN state" in sql]
        self.assertEqual(len(alters), 1)
        self.assertIn("'awaiting_user_input'", alters[0])

    async def test_skips_state_enum_alter_when_already_current(self):
        statements = await self._statements_for(
            "enum('pending','running','awaiting_user_input','completed','failed','cancelled')"
        )
        self.assertFalse(any("MODIFY COLUMN state" in sql for sql in statements))


if __name__ == "__main__":
    unittest.main()
