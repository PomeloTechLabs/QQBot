from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import patch

from src import public_web_mcp


class PublicWebMcpTests(unittest.TestCase):
    def test_html_to_text_removes_scripts_and_keeps_title(self) -> None:
        result = public_web_mcp._html_to_text(
            "<html><title>页面标题</title><script>secret()</script><body>正文&nbsp;内容</body></html>"
        )
        self.assertIn("标题：页面标题", result)
        self.assertIn("正文 内容", result)
        self.assertNotIn("secret", result)

    def test_validate_url_rejects_localhost_without_resolution(self) -> None:
        with patch("src.public_web_mcp.socket.getaddrinfo") as resolver:
            with self.assertRaises(ValueError):
                public_web_mcp._validate_url("http://localhost:11434/api/tags")
        resolver.assert_not_called()

    def test_validate_url_rejects_private_resolved_address(self) -> None:
        with patch(
            "src.public_web_mcp.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.5", 0))],
        ):
            with self.assertRaises(ValueError):
                public_web_mcp._validate_url("https://public-looking.example/path")

    def test_tools_list_and_call_use_mcp_json_rpc(self) -> None:
        output = io.StringIO()
        with patch.object(sys, "stdout", output):
            public_web_mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            with patch.object(public_web_mcp, "_fetch_public_url", return_value="来源：https://example.com"):
                public_web_mcp._handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "fetch_public_url",
                            "arguments": {"url": "https://example.com"},
                        },
                    }
                )
        messages = [line for line in output.getvalue().splitlines() if line]
        self.assertIn("fetch_public_url", messages[0])
        self.assertIn("https://example.com", messages[1])


if __name__ == "__main__":
    unittest.main()
