import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from service.runtime.affinity import AffinityError, RuntimeState, validate_affinity_key
from service.session_service.redis_mq import _seq_from_stream_id, _stream_id_from_seq
from data.conversation_dao import ConversationDAO


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class AffinityValidationTests(unittest.TestCase):
    def test_accepts_routing_safe_values(self):
        for value in ("42", "session-42", "a.b:c_1"):
            with self.subTest(value=value):
                self.assertEqual(validate_affinity_key(value), value)

    def test_rejects_missing_or_unsafe_values(self):
        for value in (None, "", "contains space", "x" * 129, "../bad/key"):
            with self.subTest(value=value), self.assertRaises(AffinityError):
                validate_affinity_key(value)

    def test_redis_stream_sequence_round_trip(self):
        for stream_id in ("1-0", "1723456789012-0", "1723456789012-27"):
            self.assertEqual(
                _stream_id_from_seq(_seq_from_stream_id(stream_id)), stream_id
            )
            self.assertLess(_seq_from_stream_id(stream_id), 2**53)


class SchemaCompatibilityTests(unittest.TestCase):
    def test_running_session_generated_column_is_mysql_84_compatible(self):
        mysql_source = (BACKEND_ROOT / "db" / "mysql.py").read_text(encoding="utf-8")
        self.assertEqual(mysql_source.count(") VIRTUAL"), 2)
        self.assertNotIn(") STORED", mysql_source)


class RuntimeStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_tracks_active_sessions(self):
        with patch.dict(os.environ, {"AGENTB_REDIS_URL": ""}):
            runtime = RuntimeState()

        async with runtime.active_session("session-a"):
            self.assertEqual(runtime.active_task_count, 1)
            self.assertEqual(runtime.active_session_count, 1)
            async with runtime.active_session("session-a"):
                self.assertEqual(runtime.active_task_count, 2)
                self.assertEqual(runtime.active_session_count, 1)

        self.assertEqual(runtime.active_task_count, 0)
        self.assertEqual(runtime.active_session_count, 0)

    async def test_drain_waits_for_active_task(self):
        with patch.dict(
            os.environ,
            {"AGENTB_REDIS_URL": "", "AGENTB_DRAIN_TIMEOUT_SECONDS": "2"},
        ):
            runtime = RuntimeState()

        async def run_task():
            async with runtime.active_session("session-a"):
                await asyncio.sleep(0.05)

        task = asyncio.create_task(run_task())
        await asyncio.sleep(0)
        await runtime.begin_drain()
        self.assertTrue(await runtime.wait_for_drain())
        await task


class _FakeDatabase:
    def __init__(self, affected: int):
        self.affected = affected
        self.sql = ""
        self.params = ()

    async def execute_affected(self, sql, params):
        self.sql = sql
        self.params = params
        return self.affected


class ConversationStateTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transition_is_guarded_by_expected_state(self):
        database = _FakeDatabase(1)
        dao = ConversationDAO(database)

        changed = await dao.transition_conversation_state(
            "conversation-1",
            ["running"],
            "cancelled",
            owner_instance_id="agentb-1",
        )

        self.assertTrue(changed)
        self.assertIn("state IN (%s)", database.sql)
        self.assertEqual(
            database.params,
            ("cancelled", "agentb-1", "conversation-1", "running"),
        )

    async def test_transition_reports_lost_race(self):
        dao = ConversationDAO(_FakeDatabase(0))
        self.assertFalse(
            await dao.transition_conversation_state(
                "conversation-1", ["pending"], "running"
            )
        )


if __name__ == "__main__":
    unittest.main()
