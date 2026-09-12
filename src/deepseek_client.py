from __future__ import annotations

import hashlib
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
                raise ModuleNotFoundError("httpx is required to use DeepSeekClient")

    httpx = _MissingHTTPXModule()  # type: ignore[assignment]

from .config import DeepSeekConfig

logger = logging.getLogger("deepseek_client")


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config

    @staticmethod
    def hash_user_id(user_id: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return f"qq-{digest[:16]}"

    def _endpoint(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        user_id: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        payload = self._build_payload(
            messages=messages,
            system_prompt=system_prompt,
            user_id=user_id,
            response_format=None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        data = await self._post_json(payload)
        if not data:
            return ""

        try:
            choice = (data.get("choices") or [])[0]
            content = (choice.get("message") or {}).get("content", "")
            return content if isinstance(content, str) else ""
        except Exception:
            logger.exception("DeepSeek 文本响应解析失败")
            return ""

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
        user_id: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | None:
        payload = self._build_payload(
            messages=messages,
            system_prompt=system_prompt,
            user_id=user_id,
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        data = await self._post_json(payload)
        if not data:
            return None

        try:
            choice = (data.get("choices") or [])[0]
            content = (choice.get("message") or {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                logger.warning("DeepSeek JSON 响应为空")
                return None
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("DeepSeek JSON 响应不是合法 JSON")
            return None
        except Exception:
            logger.exception("DeepSeek JSON 响应解析失败")
            return None

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None,
        user_id: str | None,
        response_format: dict[str, str] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        final_messages: list[dict[str, Any]] = []
        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})
        final_messages.extend(messages)

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": final_messages,
            "stream": False,
            "thinking": {"type": self._config.thinking_mode},
        }
        if response_format:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if user_id:
            payload["user"] = self.hash_user_id(user_id)
        return payload

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._config.api_key:
            logger.warning("DeepSeek API key 未配置")
            return None

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout) as client:
                response = await client.post(
                    self._endpoint(),
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.TimeoutException:
            logger.error("DeepSeek 请求超时")
            return None
        except Exception:
            logger.exception("DeepSeek 请求异常")
            return None

        if response.status_code >= 400:
            logger.error("DeepSeek API 错误 %s: %s", response.status_code, response.text)
            return None

        try:
            data = response.json()
            if not isinstance(data, dict):
                logger.error("DeepSeek 响应不是对象")
                return None
            return data
        except Exception:
            logger.exception("DeepSeek 响应 JSON 解析失败")
            return None
