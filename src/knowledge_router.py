from __future__ import annotations

from dataclasses import dataclass
from typing import Any



_ROUTER_SYSTEM_PROMPT = """你是一个消息路由器。请根据用户当前问题和最近对话，判断这条消息应该走 knowledge 还是 direct。

你必须输出 json，格式如下：
{
  "route": "knowledge" | "direct",
  "confidence": 0.0,
  "reason": "简短说明"
}

判定规则：
- knowledge：游戏/App 使用、版本、兼容性、报错、安装、配置、已知问题、规则说明
- direct：闲聊、情绪回应、泛问候、与本地知识库无关的普通对话
- 如果不确定，优先判为 knowledge
- confidence 取 0 到 1
"""

_DIRECT_THRESHOLD = 0.75


@dataclass
class RouteDecision:
    route: str
    confidence: float
    reason: str
    used_fallback: bool = False


class KnowledgeRouter:
    def __init__(self, client: Any, uncertain_to_kb: bool = True) -> None:
        self._client = client
        self._uncertain_to_kb = uncertain_to_kb

    async def decide(
        self,
        text: str,
        history: list[dict[str, str]],
        *,
        user_id: str,
        temperature: float,
    ) -> RouteDecision:
        prompt = self._build_prompt(text, history)
        result = await self._client.chat_json(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_ROUTER_SYSTEM_PROMPT,
            user_id=user_id,
            temperature=temperature,
            max_tokens=200,
        )

        if not isinstance(result, dict):
            return self._fallback("路由 JSON 为空")

        route = str(result.get("route", "")).strip().lower()
        confidence = self._normalize_confidence(result.get("confidence"))
        reason = str(result.get("reason", "")).strip() or "未提供原因"

        if route not in {"knowledge", "direct"}:
            return self._fallback("路由字段非法")
        if route == "direct" and confidence < _DIRECT_THRESHOLD:
            return self._fallback(f"direct 置信度偏低({confidence:.2f})")

        return RouteDecision(route=route, confidence=confidence, reason=reason)

    def _fallback(self, reason: str) -> RouteDecision:
        route = "knowledge" if self._uncertain_to_kb else "direct"
        return RouteDecision(route=route, confidence=0.0, reason=reason, used_fallback=True)

    @staticmethod
    def _normalize_confidence(value: object) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(max(confidence, 0.0), 1.0)

    @staticmethod
    def _build_prompt(text: str, history: list[dict[str, str]]) -> str:
        lines = []
        for turn in history[-6:]:
            role = turn.get("role", "user")
            content = turn.get("content", "").strip()
            if content:
                lines.append(f"{role}: {content[:200]}")

        history_text = "\n".join(lines) if lines else "(无历史对话)"
        return (
            "请做一次路由判断，并严格输出 json。\n"
            f"当前消息：{text}\n"
            f"最近对话：\n{history_text}\n"
            '再次提醒：只输出 json，route 只能是 "knowledge" 或 "direct"。'
        )
