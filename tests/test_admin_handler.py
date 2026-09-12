from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.admin_handler import AdminHandler
from src.config import (
    AdminConfig,
    AppConfig,
    BotConfig,
    CacheConfig,
    DeepSeekConfig,
    FeedbackConfig,
    FilterConfig,
    GroupsConfig,
    KnowledgeBaseConfig,
    NapCatConfig,
    WelcomeConfig,
)
from src.context_cache import ContextCache
from src.feedback_handler import FeedbackHandler
from src.feedback_store import FeedbackStore
from src.knowledge_store import KnowledgeStore


class FakeLLM:
    async def chat_json(self, **kwargs):
        return {
            "title": "兼容性说明",
            "category": "兼容性",
            "content": "部分游戏需要使用兼容模式启动。",
            "tags": ["兼容性", "游戏"],
        }


class FakeNapCat:
    is_connected = True


class FakeCore:
    def __init__(self, temp_dir: str) -> None:
        self.config = AppConfig(
            bot=BotConfig(qq_id="109", nickname="小柚"),
            napcat=NapCatConfig(),
            deepseek=DeepSeekConfig(api_key="test-key"),
            filter=FilterConfig(),
            cache=CacheConfig(),
            admin=AdminConfig(admin_qq_list=["10001"], command_prefix="/bot"),
            groups=GroupsConfig(),
            welcome=WelcomeConfig(),
            feedback=FeedbackConfig(enabled=True),
            knowledge_base=KnowledgeBaseConfig(),
        )
        self.cache = ContextCache(self.config.cache)
        self.napcat = FakeNapCat()
        self.llm = FakeLLM()
        self.feedback_store = FeedbackStore(
            Path(temp_dir) / "feedback.json",
            Path(temp_dir) / "feedback.md",
        )
        self._feedback = FeedbackHandler(
            store=self.feedback_store,
            llm=self.llm,
        )
        self.knowledge_store = KnowledgeStore(
            Path(temp_dir) / "knowledge.json",
            Path(temp_dir) / "knowledge.md",
        )


class AdminHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"admin-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.core = FakeCore(str(self.temp_dir))
        self.handler = AdminHandler(self.core)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_kb_add_and_show(self) -> None:
        added = await self.handler.handle("/bot kb add 补充一个兼容性说明", {"user_id": "10001"})
        self.assertIn("已新增知识条目 #1", added)

        shown = await self.handler.handle("/bot kb show #1", {"user_id": "10001"})
        self.assertIn("兼容性说明", shown)

    async def test_bad_kb_update_argument(self) -> None:
        result = await self.handler.handle("/bot kb update abc 修订内容", {"user_id": "10001"})
        self.assertEqual(result, "update 只接受 #id")

    async def test_feedback_records_command_returns_details(self) -> None:
        self.core.feedback_store.add_item(
            type_="bug",
            content="启动游戏闪退",
            source_text="这个游戏一启动就闪退",
            user_id="3000",
            group_id="1000",
        )

        result = await self.handler.handle("/bot feedback records", {"user_id": "10001"})
        self.assertIn("全部反馈记录", result)
        self.assertIn("原文:", result)
        self.assertIn("启动游戏闪退", result)

    async def test_record_show_alias_returns_single_item(self) -> None:
        self.core.feedback_store.add_item(
            type_="todo",
            content="补充兼容模式说明",
            source_text="建议补充兼容模式说明",
            user_id="3000",
            group_id="1000",
        )

        result = await self.handler.handle("/bot record show #1", {"user_id": "10001"})
        self.assertIn("#1", result)
        self.assertIn("补充兼容模式说明", result)

    def test_is_admin(self) -> None:
        self.assertTrue(self.handler.is_admin("10001"))
        self.assertFalse(self.handler.is_admin("123456"))

    def test_group_owner_has_command_permission(self) -> None:
        event = {"user_id": "999999", "message_type": "group", "sender": {"role": "owner"}}
        self.assertTrue(self.handler.has_command_permission("999999", event))

    def test_group_admin_has_command_permission(self) -> None:
        event = {"user_id": "999998", "message_type": "group", "sender": {"role": "admin"}}
        self.assertTrue(self.handler.has_command_permission("999998", event))

    def test_extracts_group_mention_command_without_prefix(self) -> None:
        event = {"user_id": "10001", "message_type": "group"}
        segments = [
            {"type": "at", "data": {"qq": "109"}},
            {"type": "text", "data": {"text": " status"}},
        ]

        command = self.handler.extract_command_text("status", event, segments)
        self.assertEqual(command, "/bot status")

    def test_extracts_group_mention_command_with_leading_nickname(self) -> None:
        event = {"user_id": "10001", "message_type": "group"}
        segments = [
            {"type": "at", "data": {"qq": "109"}},
            {"type": "text", "data": {"text": "小柚 kb list"}},
        ]

        command = self.handler.extract_command_text("小柚 kb list", event, segments)
        self.assertEqual(command, "/bot kb list")

    def test_extracts_group_mention_feedback_alias(self) -> None:
        event = {"user_id": "10001", "message_type": "group"}
        segments = [
            {"type": "at", "data": {"qq": "109"}},
            {"type": "text", "data": {"text": "record show #3"}},
        ]

        command = self.handler.extract_command_text("record show #3", event, segments)
        self.assertEqual(command, "/bot record show #3")

    def test_non_admin_mention_does_not_become_command(self) -> None:
        event = {"user_id": "123456", "message_type": "group"}
        segments = [
            {"type": "at", "data": {"qq": "109"}},
            {"type": "text", "data": {"text": "status"}},
        ]

        command = self.handler.extract_command_text("status", event, segments)
        self.assertIsNone(command)

    def test_group_owner_mention_becomes_command(self) -> None:
        event = {"user_id": "654321", "message_type": "group", "sender": {"role": "owner"}}
        segments = [
            {"type": "at", "data": {"qq": "109"}},
            {"type": "text", "data": {"text": "status"}},
        ]

        command = self.handler.extract_command_text("status", event, segments)
        self.assertEqual(command, "/bot status")


if __name__ == "__main__":
    unittest.main()
