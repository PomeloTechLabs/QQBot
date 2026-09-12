from __future__ import annotations

import json
import logging
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - test/runtime fallback
    class _MissingHTTPXModule:
        class TimeoutException(Exception):
            pass

        class AsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                raise ModuleNotFoundError("httpx is required to use OllamaClient")

    httpx = _MissingHTTPXModule()  # type: ignore[assignment]

from .config import OllamaConfig

logger = logging.getLogger("ollama_client")


class OllamaClient:
    """Small async client for Ollama's native ``/api/chat`` endpoint.

    It is used for lightweight JSON routing and fallback chat.  Agent execution
    itself belongs to Copilot CLI, which connects to Ollama separately.
    """

    def __init__(self, config: OllamaConfig) -> None:
        self._config = config

    @property
    def model(self) -> str:
        return self._config.model

    def _endpoint(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/api/chat"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        user_id: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        think: bool | None = None,
    ) -> str:
        del user_id  # Local Ollama has no user field; never leak QQ identifiers.
        data = await self._post_chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
        )
        return self._content_from_response(data)

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        user_id: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | None:
        del user_id
        data = await self._post_chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format="json",
            think=False,
        )
        content = self._content_from_response(data)
        if not content:
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Ollama JSON 响应不是合法 JSON: %s", content[:240])
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _post_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: str | None = None,
        think: bool | None = None,
    ) -> dict[str, Any] | None:
        final_messages: list[dict[str, Any]] = []
        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})
        final_messages.extend(messages)

        options: dict[str, Any] = {
            "temperature": self._config.reply_temperature if temperature is None else temperature,
            # Keep native fallback/routing calls aligned with the server-wide
            # context used by Copilot through Ollama's OpenAI endpoint.
            "num_ctx": self._config.context_length,
        }
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": final_messages,
            "stream": False,
            "options": options,
        }
        # JSON control turns stay concise and deterministic.
        payload["think"] = (
            False if response_format else (self._config.think if think is None else think)
        )
        if response_format:
            payload["format"] = response_format

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout) as client:
                response = await client.post(self._endpoint(), json=payload)
        except httpx.TimeoutException:
            logger.error("Ollama 请求超时（%ss）", self._config.timeout)
            return None
        except Exception:
            logger.exception("Ollama 请求异常；请检查服务是否已启动")
            return None

        if response.status_code >= 400:
            logger.error("Ollama API 错误 %s: %s", response.status_code, response.text[:1000])
            return None
        try:
            data = response.json()
        except Exception:
            logger.exception("Ollama 响应 JSON 解析失败")
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _content_from_response(data: dict[str, Any] | None) -> str:
        if not isinstance(data, dict):
            return ""
        message = data.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content", "")
        return content if isinstance(content, str) else ""
