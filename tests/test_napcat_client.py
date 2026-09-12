from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import NapCatConfig
from src.napcat_client import NapCatClient


class FakeWebSocket:
    def __init__(self, first_event: dict) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queue.put_nowait(json.dumps(first_event))

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        echo_reply = {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": 1},
            "echo": payload["echo"],
        }
        await self._queue.put(json.dumps(echo_reply))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self._queue.get()


class FakeConnect:
    def __init__(self, ws: FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> FakeWebSocket:
        return self._ws

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class NapCatClientTests(unittest.IsolatedAsyncioTestCase):
    def test_uses_the_header_name_supported_by_installed_websockets(self) -> None:
        options = NapCatClient(NapCatConfig(access_token="test"))._connect_options()
        self.assertTrue(
            {"extra_headers", "additional_headers"}.intersection(options),
        )
        self.assertEqual(options.get("extra_headers", options.get("additional_headers")), {
            "Authorization": "Bearer test"
        })

    async def test_echo_response_can_be_processed_while_event_handler_sends(self) -> None:
        client = NapCatClient(NapCatConfig())
        ws = FakeWebSocket(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 10001,
                "message": [{"type": "text", "data": {"text": "hi"}}],
            }
        )
        done = asyncio.Event()

        async def on_event(_: dict) -> None:
            await client.send_private_msg(10001, [{"type": "text", "data": {"text": "pong"}}])
            done.set()

        with patch("src.napcat_client.websockets.connect", return_value=FakeConnect(ws)):
            task = asyncio.create_task(client.run(on_event))
            try:
                await asyncio.wait_for(done.wait(), timeout=1.0)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


if __name__ == "__main__":
    unittest.main()
