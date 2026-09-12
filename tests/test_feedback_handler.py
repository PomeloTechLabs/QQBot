from __future__ import annotations

import asyncio
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.feedback_handler import FeedbackHandler
from src.config import GitHubIssuesConfig
from src.github_issue_publisher import GitHubIssuePublisher, GitHubIssueResult
from src.feedback_store import FeedbackStore


class FakeLLM:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    async def chat(self, **kwargs) -> str:
        return self.raw


class FakeMediaArchiver:
    def __init__(self) -> None:
        self.calls: list[tuple[int, list[dict[str, str]]]] = []

    async def archive(self, item_id: int, media: list[dict[str, str]]) -> list[dict[str, object]]:
        self.calls.append((item_id, media))
        return [{"kind": "image", "status": "saved", "path": "data/feedback_media/test.png"}]


class FakeIssuePublisher:
    def __init__(self) -> None:
        self.items = []

    async def submit_feedback(self, item):
        self.items.append(item)
        return GitHubIssueResult(True, number=42, url="https://github.com/example/repo/issues/42")


class FeedbackHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"feedback-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_store(self) -> FeedbackStore:
        return FeedbackStore(self.temp_dir / "feedback.json", self.temp_dir / "feedback.md")

    async def test_records_actionable_feedback(self) -> None:
        store = self._create_store()
        llm = FakeLLM(
            '{"is_product_related": true, "should_record": true, "type": "bug", "summary": "启动游戏闪退", "confidence": 0.91, "reason": "明确报错"}'
        )
        handler = FeedbackHandler(store=store, llm=llm)

        item = await handler.analyze_and_record(
            group_id="1000",
            user_id="2000",
            text="这个游戏一启动就闪退，完全进不去",
            group_context=[],
        )

        self.assertIsNotNone(item)
        self.assertEqual(item.type, "bug")
        self.assertEqual(item.content, "启动游戏闪退")
        self.assertEqual(store.size, 1)

    async def test_skips_normal_question(self) -> None:
        store = self._create_store()
        llm = FakeLLM(
            '{"is_product_related": false, "should_record": false, "type": "feedback", "summary": "", "confidence": 0.2, "reason": "普通咨询"}'
        )
        handler = FeedbackHandler(store=store, llm=llm)

        item = await handler.analyze_and_record(
            group_id="1000",
            user_id="2000",
            text="这个游戏怎么导入？",
            group_context=[],
        )

        self.assertIsNone(item)
        self.assertEqual(store.size, 0)

    async def test_bug_archives_media_and_submits_structured_issue(self) -> None:
        store = self._create_store()
        archive = FakeMediaArchiver()
        publisher = FakeIssuePublisher()
        llm = FakeLLM(
            '{"is_product_related":true,"should_record":true,"type":"bug","summary":"旧柚Pro 启动后黑屏","confidence":0.95,"reason":"明确功能异常","details":{"product":"旧柚Pro","environment":"Android 15","actual":"启动后黑屏"}}'
        )
        handler = FeedbackHandler(
            store=store,
            llm=llm,
            media_archiver=archive,
            issue_publisher=publisher,
        )

        item = await handler.analyze_and_record(
            group_id="1000",
            user_id="2000",
            text="旧柚Pro 启动后黑屏，Android 15，截图见附件",
            group_context=[],
            media=[{"kind": "image", "source": "https://cdn.example.com/error.png"}],
        )

        self.assertIsNotNone(item)
        self.assertEqual(item.details["product"], "旧柚Pro")
        self.assertEqual(item.media[0]["status"], "saved")
        self.assertEqual(item.github_issue_number, 42)
        self.assertEqual(len(archive.calls), 1)
        self.assertEqual(len(publisher.items), 1)

    async def test_feature_keeps_reporter_and_is_submitted_when_enabled(self) -> None:
        store = self._create_store()
        publisher = FakeIssuePublisher()
        llm = FakeLLM(
            '{"is_product_related":true,"should_record":true,"type":"feature","summary":"增加文档目录映射","confidence":0.93,"reason":"明确功能建议","details":{"product":"旧柚Pro","app_version":"1.2.4","device":"Mate 60","system_version":"HarmonyOS 5"}}'
        )
        handler = FeedbackHandler(store=store, llm=llm, issue_publisher=publisher)

        item = await handler.analyze_and_record(
            group_id="1000",
            user_id="2000",
            reporter_name="测试群昵称",
            text="建议把文档目录映射到 Z 盘，旧柚Pro 1.2.4，Mate 60",
            group_context=[],
        )

        self.assertIsNotNone(item)
        self.assertEqual(item.type, "feature")
        self.assertEqual(item.reporter_name, "测试群昵称")
        self.assertEqual(item.details["device"], "Mate 60")
        self.assertEqual(item.github_issue_number, 42)

    async def test_unrelated_bug_like_message_is_not_recorded_or_submitted(self) -> None:
        store = self._create_store()
        publisher = FakeIssuePublisher()
        llm = FakeLLM(
            '{"is_product_related":false,"should_record":true,"type":"bug","summary":"其他 App 闪退","confidence":0.95,"reason":"与旧柚无关"}'
        )
        handler = FeedbackHandler(store=store, llm=llm, issue_publisher=publisher)

        item = await handler.analyze_and_record(
            group_id="1000",
            user_id="2000",
            text="另一个 App 一启动就闪退",
            group_context=[],
        )

        self.assertIsNone(item)
        self.assertEqual(store.size, 0)
        self.assertEqual(publisher.items, [])

    def test_feedback_gate_skips_small_talk_and_keeps_bug_reports(self) -> None:
        self.assertFalse(FeedbackHandler.should_analyze("晚上好", [], is_private=False))
        self.assertTrue(FeedbackHandler.should_analyze("晚上好", [], is_private=True))
        self.assertTrue(FeedbackHandler.should_analyze("启动游戏就闪退", [], is_private=False))
        self.assertTrue(
            FeedbackHandler.should_analyze(
                "", [{"kind": "image", "source": "base64://AAAA"}], is_private=True
            )
        )

    def test_github_local_config_token_takes_precedence(self) -> None:
        publisher = GitHubIssuePublisher(
            GitHubIssuesConfig(token="local-test-token", token_env="TEST_GITHUB_TOKEN")
        )
        with patch.dict("os.environ", {"TEST_GITHUB_TOKEN": "environment-test-token"}):
            self.assertEqual(publisher._resolve_token(), "local-test-token")

    def test_issue_body_keeps_reporter_environment_and_image_link(self) -> None:
        store = self._create_store()
        item = store.add_item(
            type_="bug",
            content="旧柚Pro 启动黑屏",
            source_text="1.2.4 在 Mate 60 上启动黑屏，见截图",
            user_id="2000",
            group_id="1000",
            reporter_name="测试群昵称",
            product_related=True,
            details={
                "product": "旧柚Pro",
                "app_version": "1.2.4",
                "device": "Mate 60",
                "system_version": "HarmonyOS 5",
            },
        )
        item.media = [
            {
                "kind": "image",
                "status": "saved",
                "source_url": "https://cdn.example.com/error.png",
            }
        ]
        body = GitHubIssuePublisher(GitHubIssuesConfig())._build_issue_body(item)

        self.assertIn("群昵称：测试群昵称", body)
        self.assertIn("App 版本：1.2.4", body)
        self.assertIn("设备：Mate 60", body)
        self.assertIn("![用户图片 1]", body)
        self.assertIn("https://cdn.example.com/error.png", body)

    def test_publisher_refuses_unrelated_bug_even_if_called_directly(self) -> None:
        store = self._create_store()
        item = store.add_item(
            type_="bug",
            content="其他 App 闪退",
            source_text="其他 App 启动失败",
            user_id="2000",
            group_id="1000",
            product_related=False,
        )
        result = asyncio.run(
            GitHubIssuePublisher(GitHubIssuesConfig(token="test-token")).submit_bug(item)
        )

        self.assertFalse(result.submitted)
        self.assertIn("旧柚系列", result.error)

    def test_detects_summary_query(self) -> None:
        handler = FeedbackHandler(store=self._create_store(), llm=FakeLLM("{}"))
        query = handler.detect_query("帮我看看反馈列表")
        self.assertIsNotNone(query)
        self.assertEqual(query.mode, "summary")
        self.assertFalse(query.include_done)

    def test_detects_record_detail_query_with_id(self) -> None:
        handler = FeedbackHandler(store=self._create_store(), llm=FakeLLM("{}"))
        query = handler.detect_query("把反馈记录 #12 发我看下")
        self.assertIsNotNone(query)
        self.assertEqual(query.mode, "detail")
        self.assertEqual(query.item_id, 12)

    def test_formats_feedback_detail(self) -> None:
        store = self._create_store()
        store.add_item(
            type_="feature",
            content="增加日志导出入口",
            source_text="希望增加一个日志导出入口，方便定位问题",
            user_id="2000",
            group_id="1000",
        )
        handler = FeedbackHandler(store=store, llm=FakeLLM("{}"))

        detail = handler.format_feedback_detail(1)
        self.assertIn("增加日志导出入口", detail)
        self.assertIn("原文:", detail)

    def test_feedback_detail_respects_group_scope(self) -> None:
        store = self._create_store()
        store.add_item(
            type_="bug",
            content="A 群问题",
            source_text="A 群里出现了闪退",
            user_id="2000",
            group_id="1000",
        )
        handler = FeedbackHandler(store=store, llm=FakeLLM("{}"))

        detail = handler.format_feedback_detail(1, group_id="2000")
        self.assertEqual(detail, "未找到反馈记录 #1")


if __name__ == "__main__":
    unittest.main()
