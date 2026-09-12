from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import AgentConfig, OllamaConfig
from src.copilot_agent import CopilotAgent


class CopilotAgentTests(unittest.TestCase):
    def test_reports_missing_cli_without_trying_to_fallback_to_a_homegrown_agent(self) -> None:
        with patch("src.copilot_agent.shutil.which", return_value=None):
            agent = CopilotAgent(AgentConfig(), OllamaConfig(), PROJECT_ROOT)
            self.assertFalse(agent.available)
            self.assertIn("未找到", agent.status)

    def test_formats_group_context_without_qq_identifier(self) -> None:
        context = [{"user_id": "123456789", "text": "旧柚闪传连不上"}]
        result = CopilotAgent._format_context(context, limit=8)
        self.assertIn("群成员", result)
        self.assertNotIn("123456789", result)

    def test_context_length_has_a_64k_default(self) -> None:
        self.assertEqual(OllamaConfig().context_length, 65536)

    def test_support_answers_default_to_high_reasoning(self) -> None:
        self.assertEqual(AgentConfig().reasoning_effort, "high")
        self.assertEqual(AgentConfig().triage_reasoning_effort, "minimal")

    def test_support_prompt_does_not_delegate_feedback_submission_to_copilot(self) -> None:
        captured: list[str] = []

        async def run_test() -> str:
            async def fake_run(prompt: str, **_kwargs) -> str:
                captured.append(prompt)
                return "收到"

            agent = CopilotAgent(AgentConfig(), OllamaConfig(), PROJECT_ROOT)
            with (
                patch("src.copilot_agent.shutil.which", return_value="copilot"),
                patch.object(agent, "_run", side_effect=fake_run),
            ):
                return await agent.answer(
                    text="建议映射文档目录",
                    history=[],
                    group_context=[],
                    knowledge_context="",
                )

        self.assertEqual(asyncio.run(run_test()), "收到")
        self.assertIn("不得声称“没有 GitHub 账号/权限”", captured[0])

    def test_web_mode_exposes_only_search_and_safe_page_reading_tools(self) -> None:
        captured: list[str] = []

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"ok", b""

        async def run_test() -> str:
            async def fake_create_subprocess_exec(*command, **_kwargs):
                captured.extend(command)
                return FakeProcess()

            agent = CopilotAgent(AgentConfig(), OllamaConfig(), PROJECT_ROOT)
            with (
                patch("src.copilot_agent.shutil.which", return_value="copilot"),
                patch(
                    "src.copilot_agent.asyncio.create_subprocess_exec",
                    side_effect=fake_create_subprocess_exec,
                ),
            ):
                return await agent._run("测试", allow_web=True)

        self.assertEqual(asyncio.run(run_test()), "ok")
        self.assertIn(
            "--available-tools=web-search-search_web,public-web-fetch_public_url",
            captured,
        )
        self.assertIn("--allow-tool=web-search(search_web)", captured)
        self.assertIn("--allow-tool=public-web(fetch_public_url)", captured)
        self.assertIn("--deny-tool=read,view,grep", captured)
        self.assertNotIn("--allow-tool=web-search(fetch_url)", captured)
