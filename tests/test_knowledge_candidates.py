from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.knowledge_candidates import KnowledgeCandidateStore
from src.knowledge_store import KnowledgeStore


class KnowledgeCandidateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        root = PROJECT_ROOT / ".tmp_tests" / f"candidate-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.candidates = KnowledgeCandidateStore(root / "candidates.json")
        self.knowledge = KnowledgeStore(root / "knowledge.json", root / "knowledge.md")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_approve_moves_a_candidate_to_formal_knowledge(self) -> None:
        candidate = self.candidates.add(
            title="闪传无法发现设备",
            category="操作技能",
            content="先确认两台设备连接同一局域网，再重新打开扫描。",
            tags=["旧柚闪传", "局域网"],
            confidence=0.93,
            evidence="问题：找不到设备\n解答：检查是否在同一局域网",
        )
        self.assertIsNotNone(candidate)

        saved = self.candidates.approve(candidate.id, self.knowledge, "admin")

        self.assertIsNotNone(saved)
        self.assertEqual(self.knowledge.size, 1)
        self.assertEqual(self.candidates.get(candidate.id).status, "published")
        skill_file = self.root / "operational_skills.md"
        self.assertTrue(skill_file.exists())
        self.assertIn("闪传无法发现设备", skill_file.read_text(encoding="utf-8"))
