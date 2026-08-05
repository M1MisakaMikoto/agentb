import asyncio
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch


try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    httpx_stub = types.ModuleType("httpx")

    class _Timeout:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    httpx_stub.Timeout = _Timeout
    httpx_stub.Response = object
    httpx_stub.AsyncClient = object
    sys.modules["httpx"] = httpx_stub


TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT))

from test_cases.base import (  # noqa: E402
    APIClient,
    AffinityProtocolError,
    TestResult,
    collect_stream_output,
    wait_for_conversation_state,
)


CONFIG = {
    "api": {
        "base_url": "http://127.0.0.1:8152/api",
        "endpoints": {
            "session": {"create": "/session/sessions"},
            "conversation": {
                "create": "/session/sessions/{session_id}/conversations",
                "stream": "/session/conversations/{conversation_id}/stream",
            },
            "workspace": {
                "get": "/workspaces/{workspace_id}",
                "list_files": "/workspaces/{workspace_id}/files",
            },
        },
    }
}


class RecordingClient(APIClient):
    def __init__(self):
        super().__init__(CONFIG)
        self.calls = []

    async def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/session/sessions":
            return {
                "success": True,
                "data": {"id": 42, "workspace_id": "workspace-1"},
            }
        return {
            "success": True,
            "data": {"conversation_id": "conversation-1"},
        }


class AffinityClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_and_conversation_requests_carry_affinity(self):
        client = RecordingClient()

        await client.create_session("test")
        provisional = client.calls[0][2]["affinity_key"]
        uuid.UUID(provisional)

        await client.create_conversation(42, "hello")
        conversation_call = client.calls[1][2]
        self.assertEqual(conversation_call["affinity_key"], 42)
        self.assertEqual(conversation_call["session_id"], 42)
        uuid.UUID(conversation_call["json"]["idempotency_key"])
        self.assertEqual(client.session_for_conversation("conversation-1"), "42")

    async def test_explicit_idempotency_key_is_reused(self):
        client = RecordingClient()
        await client.create_conversation(
            42,
            "hello",
            idempotency_key="fixed-request-key",
        )
        self.assertEqual(
            client.calls[0][2]["json"]["idempotency_key"],
            "fixed-request-key",
        )

    async def test_rag_job_waits_until_completed(self):
        client = RecordingClient()
        statuses = iter(("queued", "running", "completed"))

        async def get_job(_job_id):
            return {"success": True, "status": next(statuses)}

        client.get_rag_job = get_job
        original_sleep = asyncio.sleep

        async def no_sleep(_seconds):
            return None

        asyncio.sleep = no_sleep
        try:
            result = await client.wait_for_rag_job(7, timeout=1)
        finally:
            asyncio.sleep = original_sleep
        self.assertEqual(result["status"], "completed")

    async def test_rag_job_accepts_worker_success_status(self):
        client = RecordingClient()

        async def get_job(_job_id):
            return {"success": True, "status": "success"}

        client.get_rag_job = get_job
        result = await client.wait_for_rag_job(7, timeout=1)
        self.assertEqual(result["status"], "success")

    def test_stream_path_contains_session_and_cursor(self):
        client = RecordingClient()
        client.bind_conversation("conversation-1", 42)
        self.assertEqual(
            client.stream_path("conversation-1", 123),
            "/session/conversations/conversation-1/stream?affinity_key=42&last_seq=123",
        )

    def test_instance_change_is_rejected(self):
        client = RecordingClient()
        client._observe_instance(42, "agentb-1")
        with self.assertRaises(AffinityProtocolError):
            client._observe_instance(42, "agentb-2")

    async def test_request_does_not_swallow_instance_change(self):
        class Response:
            status_code = 200
            headers = {"X-AgentB-Instance-ID": "agentb-2"}

            @staticmethod
            def json():
                return {"code": 200, "data": {"id": 42}}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def request(self, *_args, **_kwargs):
                return Response()

        client = APIClient(CONFIG)
        client._observe_instance(42, "agentb-1")
        with patch("test_cases.base.httpx.AsyncClient", return_value=Client()):
            with self.assertRaises(AffinityProtocolError):
                await client._request("GET", "/test", session_id=42)

    async def test_conversation_poll_rejects_failed_terminal_state(self):
        class FailedConversationAPI:
            async def get_conversation(self, _conversation_id):
                return {"success": True, "data": {"state": "failed"}}

        with self.assertRaisesRegex(RuntimeError, "terminal state 'failed'"):
            await wait_for_conversation_state(
                FailedConversationAPI(),
                "conversation-1",
                "completed",
                timeout=0.1,
                poll_interval=0.01,
            )

    async def test_conversation_poll_raises_on_timeout(self):
        class PendingConversationAPI:
            async def get_conversation(self, _conversation_id):
                return {"success": True, "data": {"state": "pending"}}

        with self.assertRaisesRegex(TimeoutError, "last state: 'pending'"):
            await wait_for_conversation_state(
                PendingConversationAPI(),
                "conversation-1",
                "completed",
                timeout=0.01,
                poll_interval=0.01,
            )

    async def test_workspace_uses_creator_then_session_affinity(self):
        client = RecordingClient()
        await client.create_session("test")

        provisional = client.affinity_for_workspace("workspace-1")
        uuid.UUID(provisional)
        await client.get_workspace("workspace-1")
        self.assertEqual(client.calls[-1][2]["affinity_key"], provisional)
        self.assertIsNone(client.calls[-1][2]["session_id"])

        await client.create_conversation(42, "hello")
        await client.list_workspace_files("workspace-1")
        self.assertEqual(client.calls[-1][2]["affinity_key"], "42")
        self.assertEqual(client.calls[-1][2]["session_id"], "42")

    async def test_stream_reconnects_from_last_cursor_until_done(self):
        class ReconnectingAPI:
            def __init__(self):
                self.calls = []

            async def stream_message(self, _conversation_id, last_seq=0, use_v2=False):
                self.calls.append(last_seq)
                if len(self.calls) == 1:
                    yield {"raw_line": "id: 7"}
                    return
                yield {"raw_line": "id: 8"}
                yield {"raw_line": 'data: {"type": "done", "seq": 8}'}

        api = ReconnectingAPI()
        result = TestResult("stream_test", {})
        with patch("test_cases.base.asyncio.sleep", new=AsyncMock()):
            await collect_stream_output(
                api,
                "conversation-1",
                result,
                verbose=False,
                timeout=1.0,
            )

        self.assertTrue(result.done)
        self.assertEqual(result.errors, [])
        self.assertEqual(api.calls, [0, 7])

    async def test_stream_rejects_affinity_conflict(self):
        class ConflictAPI:
            async def stream_message(self, *_args, **_kwargs):
                raise RuntimeError("SSE HTTP 409: owner conflict")
                yield

        result = TestResult("stream_test", {})
        await collect_stream_output(
            ConflictAPI(),
            "conversation-1",
            result,
            verbose=False,
            timeout=1.0,
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("stream fatal error", result.errors[0])

    async def test_completed_poll_waits_for_durable_done_event(self):
        class CompletionRaceAPI:
            async def stream_message(self, *_args, **_kwargs):
                yield {"raw_line": ": heartbeat"}
                yield {"raw_line": 'data: {"type": "done", "seq": 1}'}

            async def get_conversation(self, _conversation_id):
                return {"success": True, "data": {"state": "completed"}}

        result = TestResult("stream_test", {})
        await collect_stream_output(
            CompletionRaceAPI(),
            "conversation-1",
            result,
            verbose=False,
            timeout=1.0,
        )

        self.assertTrue(result.done)
        self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
