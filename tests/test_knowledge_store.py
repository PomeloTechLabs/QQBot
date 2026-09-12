from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.knowledge_store import KnowledgeStore


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = PROJECT_ROOT / ".tmp_tests"
        temp_root.mkdir(exist_ok=True)
        self.temp_dir = temp_root / f"store-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.temp_dir / "knowledge.json"
        self.md_path = self.temp_dir / "knowledge.md"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_add_update_delete(self) -> None:
        store = KnowledgeStore(self.json_path, self.md_path)

        self.assertEqual(store.size, 0)
        self.assertTrue(self.json_path.exists())
        self.assertTrue(self.md_path.exists())

        item = store.add_item(
            title="安装问题",
            category="安装",
            content="先确认安装包版本一致，再重试。",
            tags=["安装", "版本"],
            updated_by="10001",
        )
        self.assertEqual(item.id, 1)
        self.assertIn("安装问题", store.render_text())

        updated = store.update_item(
            1,
            title="安装问题",
            category="安装",
            content="先确认安装包版本一致，再重新安装。",
            tags=["安装", "版本"],
            updated_by="10001",
        )
        self.assertIsNotNone(updated)
        self.assertIn("重新安装", store.render_text())

        self.assertTrue(store.delete_item(1))
        self.assertEqual(store.size, 0)
        self.assertIn("当前知识库为空。", store.render_text())

    def test_reload_reads_external_file_change(self) -> None:
        store = KnowledgeStore(self.json_path, self.md_path)
        store.add_item(
            title="旧内容",
            category="测试",
            content="原始内容",
            tags=["A"],
            updated_by="tester",
        )

        with open(self.json_path, encoding="utf-8") as file:
            payload = json.load(file)

        payload["items"][0]["title"] = "新内容"
        payload["items"][0]["content"] = "已经从磁盘改过"

        with open(self.json_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

        store.reload()

        item = store.get_item(1)
        self.assertIsNotNone(item)
        self.assertEqual(item.title, "新内容")
        self.assertIn("已经从磁盘改过", store.render_text())

    def test_search_prefers_title_and_tags(self) -> None:
        store = KnowledgeStore(self.json_path, self.md_path)
        store.add_item(
            title="旧柚闪传连接失败",
            category="闪传",
            content="确认两台设备在同一局域网后重新连接。",
            tags=["旧柚闪传", "连接", "局域网"],
            updated_by="tester",
        )
        store.add_item(
            title="旧柚Pro 外观设置",
            category="功能",
            content="可以在设置中调整主题。",
            tags=["主题"],
            updated_by="tester",
        )

        results = store.search("旧柚闪传连接不了")

        self.assertEqual(results[0].title, "旧柚闪传连接失败")
        self.assertIn("局域网", store.format_search_results("闪传连接"))


if __name__ == "__main__":
    unittest.main()
