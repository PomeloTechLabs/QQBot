from __future__ import annotations

import json
import shutil
import sys
import tomllib
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
from src.agent_activity import AgentActivityStream
from src.knowledge_store import KnowledgeStore
from src.knowledge_candidates import KnowledgeCandidateStore
from src.web_server import create_app


class WebServerTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"web-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.temp_dir / "knowledge.json"
        self.md_path = self.temp_dir / "knowledge.md"
        self.store = KnowledgeStore(self.json_path, self.md_path)
        self.candidates = KnowledgeCandidateStore(self.temp_dir / "candidates.json")
        self.agent_activity = AgentActivityStream()
        self.config = AppConfig(
            bot=BotConfig(qq_id="10000", nickname="小柚"),
            napcat=NapCatConfig(),
            deepseek=DeepSeekConfig(),
            filter=FilterConfig(),
            cache=CacheConfig(),
            admin=AdminConfig(),
            groups=GroupsConfig(),
            welcome=WelcomeConfig(),
            feedback=FeedbackConfig(),
            knowledge_base=KnowledgeBaseConfig(
                json_file=str(self.json_path),
                render_file=str(self.md_path),
            ),
            _path=self.temp_dir / "config.toml",
        )
        self.client = create_app(
            self.config,
            knowledge_store=self.store,
            knowledge_candidates=self.candidates,
            agent_activity=self.agent_activity,
        ).test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_config_endpoint_updates_violation_filter_settings(self) -> None:
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["filter"]["blocked_keywords"], [])
        self.assertIn("violation_reply_template", payload["filter"])

        update_response = self.client.post(
            "/api/config",
            json={
                "filter": {
                    "blocked_keywords": ["词A", "词B"],
                    "violation_reply_template": "请注意，已触发屏蔽词：{matched_keywords}",
                }
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(self.config.filter.blocked_keywords, ["词A", "词B"])
        self.assertEqual(
            self.config.filter.violation_reply_template,
            "请注意，已触发屏蔽词：{matched_keywords}",
        )
        with open(self.config._path, "rb") as file:
            saved_config = tomllib.load(file)
        self.assertEqual(saved_config["filter"]["blocked_keywords"], ["词A", "词B"])

    def test_config_endpoint_updates_group_batch_thresholds(self) -> None:
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["agent"]["group_batch_size"], 50)
        self.assertEqual(response.get_json()["agent"]["group_batch_wait_seconds"], 600)

        update_response = self.client.post(
            "/api/config",
            json={
                "agent": {
                    "group_batch_size": 75,
                    "group_batch_wait_seconds": 900,
                }
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(self.config.agent.group_batch_size, 75)
        self.assertEqual(self.config.agent.group_batch_wait_seconds, 900)
        with open(self.config._path, "rb") as file:
            saved_config = tomllib.load(file)
        self.assertEqual(saved_config["agent"]["group_batch_size"], 75)
        self.assertEqual(saved_config["agent"]["group_batch_wait_seconds"], 900)

    def test_knowledge_base_crud_endpoints(self) -> None:
        response = self.client.get("/api/knowledge-base")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["size"], 0)

        create_response = self.client.post(
            "/api/knowledge-base/items",
            json={
                "title": "RTP 缺失",
                "category": "RPGMaker",
                "content": "先确认是否缺 RTP，再检查目录层级。",
                "tags": ["RTP", "RPGMaker"],
                "updated_by": "web_console",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(self.store.size, 1)
        created = create_response.get_json()["item"]
        self.assertEqual(created["title"], "RTP 缺失")
        self.assertEqual(
            response.get_json().get("skill_library_file"),
            str(self.candidates.skill_library_path.resolve()),
        )

        update_response = self.client.put(
            "/api/knowledge-base/items/1",
            json={
                "title": "RTP 缺失排查",
                "category": "RPGMaker",
                "content": "先确认是否缺 RTP，再检查目录层级和文件名。",
                "tags": "RTP, RPGMaker, 报错",
                "updated_by": "editor",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertIn("文件名", self.store.render_text())
        self.assertEqual(self.store.get_item(1).updated_by, "editor")

        delete_response = self.client.delete("/api/knowledge-base/items/1")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(self.store.size, 0)

    def test_operational_skill_item_syncs_to_local_skill_file(self) -> None:
        response = self.client.post(
            "/api/knowledge-base/items",
            json={
                "title": "闪传收发排查流程",
                "category": "操作技能",
                "content": "先确认两端设备和传输报错。",
                "tags": ["旧柚闪传", "排查"],
                "updated_by": "web_console",
            },
        )
        self.assertEqual(response.status_code, 201)
        skill_file = self.candidates.skill_library_path
        self.assertTrue(skill_file.exists())
        self.assertIn("闪传收发排查流程", skill_file.read_text(encoding="utf-8"))

    def test_reload_endpoint_reads_disk_change(self) -> None:
        self.store.add_item(
            title="旧标题",
            category="测试",
            content="旧内容",
            tags=["A"],
            updated_by="tester",
        )

        with open(self.json_path, encoding="utf-8") as file:
            payload = json.load(file)
        payload["items"][0]["title"] = "磁盘标题"
        with open(self.json_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        reload_response = self.client.post("/api/knowledge-base/reload")
        self.assertEqual(reload_response.status_code, 200)
        self.assertEqual(self.store.get_item(1).title, "磁盘标题")

    def test_candidate_review_endpoints_publish_or_reject(self) -> None:
        published = self.candidates.add(
            title="导入后闪退排查",
            category="旧柚",
            content="先确认目录层级和应用版本，再收集报错文本。",
            tags=["闪退", "导入"],
            confidence=0.94,
            evidence="问题：导入后闪退\n\n机器人技术答复：先检查目录。",
        )
        rejected = self.candidates.add(
            title="待驳回示例",
            category="测试",
            content="不应发布。",
            tags=[],
            confidence=0.91,
            evidence="测试",
        )
        self.assertIsNotNone(published)
        self.assertIsNotNone(rejected)

        list_response = self.client.get("/api/knowledge-candidates?status=pending")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.get_json()["pending_size"], 2)

        approve_response = self.client.post(
            f"/api/knowledge-candidates/{published.id}/approve"
        )
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(self.store.size, 1)
        self.assertEqual(self.candidates.get(published.id).status, "published")

        reject_response = self.client.post(
            f"/api/knowledge-candidates/{rejected.id}/reject"
        )
        self.assertEqual(reject_response.status_code, 200)
        self.assertEqual(self.candidates.get(rejected.id).status, "rejected")

    def test_agent_activity_stream_exposes_only_current_live_work(self) -> None:
        self.agent_activity.start("技术支持答复", "正在处理测试问题。")
        response = self.client.get("/api/agent-activity/stream", buffered=False)
        event_chunks = [
            next(iter(response.response)).decode("utf-8"),
            next(iter(response.response)).decode("utf-8"),
        ]
        payload = "".join(event_chunks)
        self.assertIn("event: agent_activity", payload)
        self.assertIn("技术支持答复", payload)
        self.assertIn('"phase":"active"', payload)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        response.close()


if __name__ == "__main__":
    unittest.main()
