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

from src.config import MemoryConfig
from src.conversation_memory import ConversationMemoryStore


class ConversationMemoryStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = PROJECT_ROOT / ".tmp_tests"
        root.mkdir(exist_ok=True)
        self.temp_dir = root / f"memory-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True)
        self.store = ConversationMemoryStore(
            MemoryConfig(directory=str(self.temp_dir), recent_turn_limit=10, retain_after_compaction=7),
            PROJECT_ROOT,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_compacts_old_turns_and_reloads_per_user_file(self) -> None:
        calls: list[tuple[str, list[dict[str, str]]]] = []

        async def summarize(previous: str, turns: list[dict[str, str]]) -> str:
            calls.append((previous, turns))
            return f"已处理 {len(turns)} 条旧对话；用户在排查旧柚闪传接收失败。"

        for index in range(11):
            await self.store.add_turn(
                "7788",
                "5566",
                role="user" if index % 2 == 0 else "assistant",
                content=f"第 {index} 条对话",
                summarize=summarize,
            )

        path = self.store.memory_path("7788", "5566")
        self.assertTrue(path.exists())
        self.assertEqual(len(self.store.get_history("7788", "5566")), 7)
        self.assertIn("旧柚闪传", self.store.summary_for_prompt("7788", "5566"))
        self.assertEqual(len(calls), 1)

        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["scope_id"], "7788")
        self.assertEqual(raw["user_id"], "5566")
        self.assertEqual(len(raw["recent_turns"]), 7)

        fresh_store = ConversationMemoryStore(self.store._config, PROJECT_ROOT)
        self.assertEqual(len(fresh_store.get_history("7788", "5566")), 7)
        self.assertNotEqual(
            self.store.memory_path("7788", "5566"),
            self.store.memory_path("7788", "5567"),
        )

    def test_rejects_a_memory_directory_outside_project_workspace(self) -> None:
        with self.assertRaises(ValueError):
            ConversationMemoryStore(MemoryConfig(directory=".."), PROJECT_ROOT)
