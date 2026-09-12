from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.reply_formatter import sanitize_for_qq


class ReplyFormatterTests(unittest.TestCase):
    def test_strips_common_markdown_markers(self) -> None:
        source = (
            "# 标题\n"
            "> 请按这个看\n\n"
            "- **第一步** 打开 `设置`\n"
            "- [x] 已处理项\n"
            "- [ ] 待处理项\n"
            "参考：[说明文档](https://example.com/docs)\n\n"
            "```text\n"
            "code line\n"
            "```\n"
        )

        result = sanitize_for_qq(source)

        self.assertNotIn("# ", result)
        self.assertNotIn("**", result)
        self.assertNotIn("```", result)
        self.assertNotIn("`设置`", result)
        self.assertIn("标题", result)
        self.assertIn("请按这个看", result)
        self.assertIn("- 第一步 打开 设置", result)
        self.assertIn("[已处理] 已处理项", result)
        self.assertIn("[待处理] 待处理项", result)
        self.assertIn("说明文档: https://example.com/docs", result)
        self.assertIn("code line", result)

    def test_keeps_plain_text_stable(self) -> None:
        source = "第一行\n\n\n第二行"
        result = sanitize_for_qq(source)
        self.assertEqual(result, "第一行\n\n第二行")


if __name__ == "__main__":
    unittest.main()
