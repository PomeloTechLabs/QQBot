from __future__ import annotations

import asyncio
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.bot_core import BotCore
from src.config import (
    AdminConfig,
    AgentConfig,
    AppConfig,
    BotConfig,
    CacheConfig,
    DeepSeekConfig,
    FeedbackConfig,
    FilterConfig,
    GroupsConfig,
    KnowledgeBaseConfig,
    MemoryConfig,
    NapCatConfig,
    WelcomeConfig,
)


class FakeNapCat:
    def __init__(self) -> None:
        self.group_messages: list[tuple[int | str, list[dict]]] = []
        self.private_messages: list[tuple[int | str, list[dict]]] = []

    async def send_group_msg(self, group_id: int | str, message: list[dict]) -> None:
        self.group_messages.append((group_id, message))

    async def send_private_msg(self, user_id: int | str, message: list[dict]) -> None:
        self.private_messages.append((user_id, message))


class GuardLLM:
    async def chat(self, **kwargs):
        raise AssertionError("LLM should not be called for blocked group messages")


class GuardRouter:
    async def decide(self, **kwargs):
        raise AssertionError("Router should not be called for blocked group messages")


class BatchAgent:
    available = True

    def __init__(self) -> None:
        self.batches: list[list[dict[str, str]]] = []

    async def triage_batch(
        self,
        messages: list[dict[str, str]],
        context: list[dict[str, str]],
    ) -> list[int]:
        self.batches.append(messages)
        return [0]

    async def answer(self, **kwargs) -> str:
        return "批量技术支持回复"


class KnowledgeRouter:
    async def decide(self, **kwargs):
        return SimpleNamespace(
            route="knowledge",
            confidence=1.0,
            used_fallback=False,
            reason="test",
        )


class CapturingFeedback:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @staticmethod
    def detect_query(text):
        return None

    @staticmethod
    def should_analyze(text, media, *, is_private):
        return "闪退" in text or (is_private and bool(media))

    async def analyze_and_record(
        self,
        group_id,
        user_id,
        text,
        group_context,
        media=None,
        reporter_name="",
    ):
        self.calls.append(
            {
                "group_id": group_id,
                "user_id": user_id,
                "text": text,
                "media": media or [],
                "reporter_name": reporter_name,
            }
        )


class BotCoreModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"bot-core-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.config = AppConfig(
            bot=BotConfig(qq_id="109", nickname="小柚"),
            napcat=NapCatConfig(),
            deepseek=DeepSeekConfig(api_key="test-key"),
            filter=FilterConfig(
                app_keywords=["旧柚", "旧柚闪传", "旧柚Pro"],
                help_keywords=["怎么", "如何", "无法", "不行"],
                blocked_keywords=["坏词", "违规词"],
                violation_reply_template="请文明发言，检测到：{matched_keywords}",
            ),
            cache=CacheConfig(),
            memory=MemoryConfig(directory=str(self.temp_dir / "conversation_memories")),
            admin=AdminConfig(),
            groups=GroupsConfig(),
            welcome=WelcomeConfig(),
            feedback=FeedbackConfig(enabled=False),
            knowledge_base=KnowledgeBaseConfig(
                json_file=str(self.temp_dir / "knowledge.json"),
                render_file=str(self.temp_dir / "knowledge.md"),
            ),
            agent=AgentConfig(
                candidate_file=str(self.temp_dir / "candidates.json"),
                skill_library_file=str(self.temp_dir / "operational_skills.md"),
            ),
        )
        self.core = BotCore(self.config)
        self.core.napcat = FakeNapCat()
        self.core.llm = GuardLLM()
        self.core.router = GuardRouter()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_group_message_with_blocked_keyword_gets_immediate_warning(self) -> None:
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 7788,
            "user_id": 5566,
            "message": [
                {"type": "text", "data": {"text": "这句话里带有坏词，需要立刻提醒"}},
            ],
        }

        await self.core._handle_event(event)

        self.assertEqual(len(self.core.napcat.group_messages), 1)
        group_id, payload = self.core.napcat.group_messages[0]
        self.assertEqual(group_id, 7788)
        self.assertEqual(payload[0], {"type": "at", "data": {"qq": "5566"}})
        self.assertEqual(
            payload[1],
            {"type": "text", "data": {"text": "\n请文明发言，检测到：坏词"}},
        )
        self.assertEqual(self.core.cache.get_history("7788", "5566"), [])

    async def test_unmentioned_group_messages_are_triaged_in_one_batch(self) -> None:
        self.config.agent.monitor_all_group_messages = True
        self.config.agent.group_batch_size = 10
        self.config.agent.group_batch_wait_seconds = 60
        agent = BatchAgent()
        self.core.support_agent = agent

        for index in range(10):
            await self.core._handle_event(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": 7788,
                    "user_id": 6000 + index,
                    "message": [{"type": "text", "data": {"text": f"普通群聊 {index}"}}],
                }
            )

        await asyncio.sleep(0.05)
        self.assertEqual(len(agent.batches), 1)
        self.assertEqual(len(agent.batches[0]), 10)
        self.assertEqual(len(self.core.napcat.group_messages), 1)
        await self.core._cancel_group_triage_tasks()

    async def test_untriggered_bug_is_recorded_even_when_group_batch_is_off(self) -> None:
        feedback = CapturingFeedback()
        self.core._feedback = feedback
        self.config.agent.monitor_all_group_messages = False

        await self.core._handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                    "group_id": 7788,
                    "user_id": 6012,
                    "sender": {"card": "群昵称", "nickname": "备用昵称"},
                "message": [
                    {"type": "text", "data": {"text": "启动游戏就闪退，麻烦记录一下"}},
                    {"type": "image", "data": {"url": "https://cdn.example.com/error.png"}},
                ],
            }
        )

        await asyncio.sleep(0.05)
        self.assertEqual(len(feedback.calls), 1)
        self.assertEqual(feedback.calls[0]["user_id"], "6012")
        self.assertEqual(feedback.calls[0]["reporter_name"], "群昵称")
        self.assertEqual(feedback.calls[0]["media"][0]["kind"], "image")
        self.assertEqual(self.core.napcat.group_messages, [])

    async def test_app_help_keyword_bypasses_batch_and_replies_immediately(self) -> None:
        agent = BatchAgent()
        self.core.support_agent = agent
        self.core.router = KnowledgeRouter()

        await self.core._handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 7788,
                "user_id": 6001,
                "message": [{"type": "text", "data": {"text": "旧柚闪传怎么接收文件？"}}],
            }
        )

        self.assertEqual(agent.batches, [])
        self.assertEqual(len(self.core.napcat.group_messages), 1)
        self.assertEqual(
            self.core.napcat.group_messages[0][1][1]["data"]["text"],
            "\n批量技术支持回复",
        )
        history = self.core.memory.get_history("7788", "6001")
        self.assertEqual([turn["role"] for turn in history], ["user", "assistant"])

    def test_reply_text_has_a_global_compact_length_guard(self) -> None:
        text = "内容。" * 500
        result = self.core._prepare_reply_text(text)
        self.assertLessEqual(len(result), 850)
        self.assertIn("内容较多", result)


if __name__ == "__main__":
    unittest.main()
