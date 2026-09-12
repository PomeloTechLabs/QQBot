from __future__ import annotations

import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.knowledge_router import KnowledgeRouter


class FakeClient:
    def __init__(self, result):
        self.result = result

    async def chat_json(self, **kwargs):
        return self.result


class KnowledgeRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_route_with_high_confidence(self) -> None:
        router = KnowledgeRouter(FakeClient({"route": "direct", "confidence": 0.92, "reason": "闲聊"}))
        result = await router.decide("你好呀", [], user_id="u1", temperature=0.0)
        self.assertEqual(result.route, "direct")
        self.assertFalse(result.used_fallback)

    async def test_low_confidence_falls_back_to_knowledge(self) -> None:
        router = KnowledgeRouter(FakeClient({"route": "direct", "confidence": 0.35, "reason": "不确定"}))
        result = await router.decide("这个游戏怎么装", [], user_id="u1", temperature=0.0)
        self.assertEqual(result.route, "knowledge")
        self.assertTrue(result.used_fallback)

    async def test_invalid_json_falls_back(self) -> None:
        router = KnowledgeRouter(FakeClient(None))
        result = await router.decide("版本支持吗", [], user_id="u1", temperature=0.0)
        self.assertEqual(result.route, "knowledge")
        self.assertTrue(result.used_fallback)


if __name__ == "__main__":
    unittest.main()
