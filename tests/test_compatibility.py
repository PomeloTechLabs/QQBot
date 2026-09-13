from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.compatibility_collector import CompatibilityCollector
from src.compatibility_publisher import CompatibilityIssuePublisher
from src.compatibility_store import CompatibilityStore
from src.config import CompatibilityConfig


def _json_response(**overrides) -> str:
    data = {
        "is_game_compatibility": True,
        "jiuyou_related": True,
        "status": "works",
        "game": "赛博酒保",
        "game_version": "",
        "emulator": "",
        "device": "Mate 60",
        "system_version": "HarmonyOS 5",
        "driver": "",
        "settings": "",
        "summary": "《赛博酒保》 Winlator 下流畅运行",
        "confidence": 0.92,
        "reason": "明确运行体验",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def chat(self, **kwargs) -> str:
        self.prompts.append(kwargs.get("messages", [{}])[0].get("content", ""))
        if not self.responses:
            return "{}"
        return self.responses.pop(0)


class FakeMediaArchiver:
    def __init__(self) -> None:
        self.calls: list[tuple[int, list[dict[str, str]]]] = []

    async def archive(self, report_id: int, media: list[dict[str, str]]):
        self.calls.append((report_id, media))
        return [{"kind": "image", "status": "saved", "path": "data/compatibility_media/test.png"}]


class FakePublisher:
    def __init__(self, ok: bool = True) -> None:
        self.reports = []
        self.ok = ok

    async def submit(self, report):
        self.reports.append(report)
        if self.ok:
            return True, 7, "https://github.com/example/compat/issues/7", ""
        return False, None, "", "GitHub 创建 Issue 失败（HTTP 403）"


class SenderRecorder:
    def __init__(self) -> None:
        self.messages: list[tuple[dict, str]] = []

    async def __call__(self, event: dict, text: str) -> None:
        self.messages.append((event, text))


def _make_collector(responses: list[str], *, config_overrides: dict | None = None):
    temp_root = PROJECT_ROOT / ".tmp_tests"
    temp_root.mkdir(exist_ok=True)
    temp_dir = temp_root / f"compat-{uuid.uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    overrides = {"analysis_cooldown": 0, "clarify_timeout": 60}
    overrides.update(config_overrides or {})
    config = CompatibilityConfig(
        data_file=str(temp_dir / "compat.json"),
        md_file=str(temp_dir / "compat.md"),
        **overrides,
    )
    store = CompatibilityStore(config.data_file, config.md_file)
    llm = FakeLLM(responses)
    archiver = FakeMediaArchiver()
    publisher = FakePublisher()
    sender = SenderRecorder()
    collector = CompatibilityCollector(
        store=store,
        llm=llm,
        config=config,
        media_archiver=archiver,
        issue_publisher=publisher,
        sender=sender,
    )
    return collector, store, llm, archiver, publisher, sender, temp_dir


_EVENT = {"message_type": "group", "group_id": "1000", "user_id": "2000"}


class ShouldObserveTests(unittest.TestCase):
    def test_gate_accepts_compatibility_talk(self) -> None:
        self.assertTrue(CompatibilityCollector.should_observe("这游戏 Winlator 能跑，很流畅"))
        self.assertTrue(CompatibilityCollector.should_observe("旧柚里进不去，一直黑屏"))
        self.assertTrue(CompatibilityCollector.should_observe("完美运行 60帧"))

    def test_gate_rejects_off_topic(self) -> None:
        self.assertFalse(CompatibilityCollector.should_observe("晚上好"))
        self.assertFalse(CompatibilityCollector.should_observe("这个游戏怎么导入旧柚"))
        self.assertFalse(CompatibilityCollector.should_observe(""))


class CompatibilityCollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._cleanup: list[Path] = []

    def tearDown(self) -> None:
        for path in self._cleanup:
            shutil.rmtree(path, ignore_errors=True)

    def _make(self, responses: list[str], **config_overrides):
        collector, store, llm, archiver, publisher, sender, temp_dir = _make_collector(
            responses, config_overrides=config_overrides or None
        )
        self._cleanup.append(temp_dir)
        return collector, store, llm, archiver, publisher, sender

    async def test_complete_report_is_published_silently(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(status="works", emulator="Winlator", game_version="1.0.3")]
        )
        await collector.observe(
            _EVENT, "1000", "2000",
            "赛博酒保 1.0.3 在 Winlator 里完美运行，很流畅",
            group_context=[], media=[], reporter_name="测试群友",
        )

        self.assertEqual(store.size, 1)
        self.assertEqual(len(publisher.reports), 1)
        self.assertEqual(sender.messages, [])
        report = store.get_report(1)
        self.assertEqual(report.status, "works")
        self.assertEqual(report.game, "赛博酒保")
        self.assertEqual(report.game_version, "1.0.3")
        self.assertEqual(report.emulator, "Winlator")
        self.assertEqual(report.app_version, "最新版")
        self.assertEqual(report.reporter_name, "测试群友")
        self.assertEqual(report.github_issue_url, "https://github.com/example/compat/issues/7")

    async def test_missing_fields_get_defaults_and_inference(self) -> None:
        collector, store, *_ = self._make(
            [_json_response(game="暗黑破坏神2", status="issues", game_version="")]
        )
        await collector.observe(
            _EVENT, "1000", "2000",
            "暗黑破坏神2 的 pc版 在手机上进不去，一直黑屏",
            group_context=[], media=[], reporter_name="",
        )
        report = store.get_report(1)
        self.assertEqual(report.status, "issues")
        self.assertEqual(report.game_version, "不明")
        self.assertEqual(report.app_version, "最新版")
        self.assertIn("Winlator", report.emulator)
        self.assertIn("PC 游戏推断", report.emulator)

    async def test_missing_game_triggers_single_clarify_then_publish(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [
                _json_response(game="", status="works", summary="一个酒吧游戏流畅运行"),
                _json_response(game="赛博酒保", status="works"),
            ]
        )
        await collector.observe(
            _EVENT, "1000", "2000",
            "那个调酒的游戏我玩了一下午，很流畅",
            group_context=[], media=[], reporter_name="测试群友",
            directed=True,
        )

        self.assertEqual(store.size, 0)
        self.assertEqual(len(sender.messages), 1)
        self.assertIn("哪个游戏", sender.messages[0][1])
        self.assertTrue(collector.has_pending_clarification("1000", "2000"))

        confirmation = await collector.consume_clarification(
            "1000", "2000", "是赛博酒保", reporter_name="测试群友"
        )

        self.assertIn("已收录", confirmation)
        self.assertIn("issues/7", confirmation)
        self.assertEqual(store.size, 1)
        report = store.get_report(1)
        self.assertEqual(report.game, "赛博酒保")
        self.assertEqual(report.extra_text, "是赛博酒保")
        self.assertEqual(len(publisher.reports), 1)
        self.assertEqual(len(archiver.calls), 0)
        self.assertFalse(collector.has_pending_clarification("1000", "2000"))

    async def test_undirected_non_jiuyou_stays_silent(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(jiuyou_related=False, status="issues")]
        )
        await collector.observe(
            _EVENT, "1000", "2000",
            "赛博酒保这游戏在我手机上闪退",
            group_context=[], media=[], reporter_name="路人",
            directed=False,
        )
        self.assertEqual(store.size, 0)
        self.assertEqual(publisher.reports, [])
        self.assertEqual(sender.messages, [])

    async def test_undirected_complete_jiuyou_report_records_silently(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(status="issues", emulator="旧柚")]
        )
        await collector.observe(
            _EVENT, "1000", "2000",
            "旧柚里赛博酒保又闪退了",
            group_context=[], media=[], reporter_name="群友",
            directed=False,
        )
        self.assertEqual(store.size, 1)
        self.assertEqual(len(publisher.reports), 1)
        self.assertEqual(sender.messages, [])
        self.assertEqual(store.get_report(1).status, "issues")

    async def test_undirected_missing_game_never_asks(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(game="", status="works")]
        )
        await collector.observe(
            _EVENT, "1000", "2000", "旧柚里那个调酒游戏很流畅",
            group_context=[], media=[], reporter_name="",
            directed=False,
        )
        self.assertEqual(sender.messages, [])
        self.assertEqual(store.size, 0)
        self.assertFalse(collector.has_pending_clarification("1000", "2000"))

    async def test_undirected_unknown_status_records_without_asking(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(status="unknown")]
        )
        await collector.observe(
            _EVENT, "1000", "2000", "旧柚跑赛博酒保好像有点问题",
            group_context=[], media=[], reporter_name="",
            directed=False,
        )
        self.assertEqual(sender.messages, [])
        self.assertEqual(store.size, 1)
        self.assertEqual(store.get_report(1).status, "unknown")

    async def test_directed_report_asks_even_without_jiuyou(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [
                _json_response(jiuyou_related=False, game="", status="works"),
                _json_response(game="赛博酒保", status="works"),
            ]
        )
        await collector.observe(
            _EVENT, "1000", "2000", "@小柚 这个游戏很流畅",
            group_context=[], media=[], reporter_name="",
            directed=True,
        )
        self.assertEqual(len(sender.messages), 1)
        confirmation = await collector.consume_clarification("1000", "2000", "赛博酒保")
        self.assertIn("已收录", confirmation)
        self.assertEqual(store.size, 1)

    async def test_clarify_decline_skips_report(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(game="", status="works")]
        )
        await collector.observe(
            _EVENT, "1000", "2000", "这游戏跑得很流畅",
            group_context=[], media=[], reporter_name="",
            directed=True,
        )
        confirmation = await collector.consume_clarification("1000", "2000", "不用了")

        self.assertIn("不收录", confirmation)
        self.assertEqual(store.size, 0)
        self.assertEqual(publisher.reports, [])
        self.assertFalse(collector.has_pending_clarification("1000", "2000"))

    async def test_clarify_timeout_publishes_with_known_fields(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(game="", status="issues", summary="某游戏黑屏")],
            clarify_timeout=1,
        )
        await collector.observe(
            _EVENT, "1000", "2000", "手机上玩进不去黑屏",
            group_context=[], media=[], reporter_name="超时群友",
            directed=True,
        )
        self.assertEqual(store.size, 0)
        await asyncio.sleep(1.3)

        self.assertEqual(store.size, 1)
        report = store.get_report(1)
        self.assertEqual(report.status, "issues")
        self.assertEqual(report.reporter_name, "超时群友")
        self.assertFalse(collector.has_pending_clarification("1000", "2000"))

    async def test_media_is_archived_and_reported(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(status="issues")]
        )
        await collector.observe(
            _EVENT, "1000", "2000", "赛博酒报错退出了，截图如下",
            group_context=[],
            media=[{"kind": "image", "source": "https://cdn.example.com/shot.jpg"}],
            reporter_name="",
        )
        self.assertEqual(len(archiver.calls), 1)
        report = store.get_report(1)
        self.assertEqual(report.media[0]["status"], "saved")

    async def test_cooldown_skips_repeated_analysis(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(status="works")], analysis_cooldown=300
        )
        await collector.observe(
            _EVENT, "1000", "2000", "赛博酒保能玩，流畅",
            group_context=[], media=[], reporter_name="",
        )
        await collector.observe(
            _EVENT, "1000", "2000", "赛博酒保能玩，流畅",
            group_context=[], media=[], reporter_name="",
        )
        self.assertEqual(store.size, 1)
        self.assertEqual(len(publisher.reports), 1)

    async def test_non_compatibility_message_is_ignored(self) -> None:
        collector, store, llm, archiver, publisher, sender = self._make(
            [_json_response(is_game_compatibility=False)]
        )
        await collector.observe(
            _EVENT, "1000", "2000", "这游戏剧情真不错",
            group_context=[], media=[], reporter_name="",
        )
        self.assertEqual(store.size, 0)
        self.assertEqual(sender.messages, [])


class CompatibilityPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"compat-pub-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_report(self, **overrides):
        store = CompatibilityStore(
            self.temp_dir / "compat.json", self.temp_dir / "compat.md"
        )
        report = store.add_report(
            game="赛博酒保",
            status="works",
            summary="Winlator 下流畅运行",
            source_text="我的QQ 123456789 联系，游戏 1.0.3 完美运行",
            group_id="1000",
            user_id="2000",
            reporter_name="测试群昵称",
            game_version="不明",
            emulator="Winlator（报告者提及）",
            app_version="最新版",
            device="Mate 60",
        )
        report.media = [
            {"kind": "image", "status": "saved", "source_url": "https://cdn.example.com/shot.jpg"}
        ]
        return report

    def test_issue_body_keeps_nickname_and_image(self) -> None:
        config = CompatibilityConfig(repository="example/compat")
        report = self._make_report()
        body = CompatibilityIssuePublisher(config)._build_issue_body(report)
        self.assertIn("结论：运行正常", body)
        self.assertIn("游戏版本：不明", body)
        self.assertIn("群昵称：测试群昵称", body)
        self.assertIn("![用户图片 1]", body)
        self.assertIn("https://cdn.example.com/shot.jpg", body)
        self.assertIn("最新版", body)
        # 原文中的长 QQ 号应被脱敏
        self.assertNotIn("123456789", body)

    def test_issue_title_and_body_flag_issues_status(self) -> None:
        config = CompatibilityConfig(repository="example/compat")
        store = CompatibilityStore(
            self.temp_dir / "compat.json", self.temp_dir / "compat.md"
        )
        report = store.add_report(
            game="暗黑破坏神2",
            status="issues",
            summary="进不去一直黑屏",
            source_text="进不去，一直黑屏",
            group_id="1000",
            user_id="2000",
        )
        body = CompatibilityIssuePublisher(config)._build_issue_body(report)
        self.assertIn("结论：运行异常", body)
        self.assertIn("游戏：暗黑破坏神2", body)
        self.assertIn("模拟器/运行方式：不明", body)

    def test_token_priority_config_env_then_fallback(self) -> None:
        config = CompatibilityConfig(token="config-token", token_env="TEST_COMPAT_TOKEN")
        publisher = CompatibilityIssuePublisher(config, fallback_token="feedback-token")
        with patch.dict("os.environ", {"TEST_COMPAT_TOKEN": "env-token"}):
            self.assertEqual(publisher._resolve_token(), "config-token")
            config.token = ""
            self.assertEqual(publisher._resolve_token(), "env-token")
            os.environ.pop("TEST_COMPAT_TOKEN")
            self.assertEqual(publisher._resolve_token(), "feedback-token")

    def test_submit_refuses_without_token(self) -> None:
        config = CompatibilityConfig(repository="example/compat", token="")
        publisher = CompatibilityIssuePublisher(config, fallback_token="")
        ok, number, url, error = asyncio.run(publisher.submit(self._make_report()))
        self.assertFalse(ok)
        self.assertIn("令牌", error)


if __name__ == "__main__":
    unittest.main()
