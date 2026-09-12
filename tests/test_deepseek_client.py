from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DeepSeekConfig
from src.deepseek_client import DeepSeekClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeAsyncClient:
    response = FakeResponse(
        200,
        {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                    }
                }
            ]
        },
    )
    last_request = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        FakeAsyncClient.last_request = {
            "url": url,
            "headers": headers,
            "json": json,
        }
        return FakeAsyncClient.response


class DeepSeekClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_success(self) -> None:
        client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
        FakeAsyncClient.response = FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "hello",
                        }
                    }
                ]
            },
        )
        with patch("src.deepseek_client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.chat(
                messages=[{"role": "user", "content": "hi"}],
                system_prompt="sys",
                user_id="123",
                temperature=0.3,
            )

        self.assertEqual(result, "hello")
        self.assertEqual(
            FakeAsyncClient.last_request["json"]["thinking"]["type"],
            "disabled",
        )
        self.assertEqual(FakeAsyncClient.last_request["json"]["user"], client.hash_user_id("123"))

    async def test_chat_json_parse_failure_returns_none(self) -> None:
        client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
        FakeAsyncClient.response = FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "not json",
                        }
                    }
                ]
            },
        )
        with patch("src.deepseek_client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.chat_json(messages=[{"role": "user", "content": "hi"}])
        self.assertIsNone(result)

    async def test_http_error_returns_empty(self) -> None:
        client = DeepSeekClient(DeepSeekConfig(api_key="test-key"))
        FakeAsyncClient.response = FakeResponse(500, text="boom")
        with patch("src.deepseek_client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.chat(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
