"""A small, standard MCP stdio server for public web research.

It is a tool server, not an Agent: Copilot chooses when to call it and Qwen
does the reasoning.  It deliberately accepts only public HTTP(S) destinations
so a public QQ bot cannot be used to probe localhost or private networks.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_MAX_REDIRECTS = 4
_MAX_BYTES = 500_000
_DEFAULT_MAX_CHARS = 12_000
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1\s*>", re.I | re.S)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.I | re.S)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _is_public_host(hostname: str) -> bool:
    """Resolve a host and refuse local, link-local and private ranges."""
    if not hostname or hostname.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    resolved = {item[4][0] for item in addresses}
    if not resolved:
        return False
    for address in resolved:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _validate_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持具有公开主机名的 HTTP(S) URL")
    if not _is_public_host(parsed.hostname):
        raise ValueError("拒绝访问本机、内网或无法解析的地址")
    return url


def _html_to_text(payload: str) -> str:
    title_match = _TITLE_RE.search(payload)
    title = html.unescape(_TAG_RE.sub(" ", title_match.group(1))).strip() if title_match else ""
    content = _SCRIPT_RE.sub(" ", payload)
    content = html.unescape(_TAG_RE.sub(" ", content))
    content = content.replace("\r", "\n").replace("\xa0", " ")
    content = _WHITESPACE_RE.sub(" ", content)
    content = _BLANK_LINES_RE.sub("\n\n", content).strip()
    return f"标题：{title}\n\n{content}".strip() if title else content


def _fetch_public_url(url: object, max_chars: object = _DEFAULT_MAX_CHARS) -> str:
    current_url = _validate_url(url)
    try:
        limit = max(500, min(int(max_chars), 30_000))
    except (TypeError, ValueError):
        limit = _DEFAULT_MAX_CHARS

    opener = build_opener(_NoRedirect())
    for _ in range(_MAX_REDIRECTS + 1):
        request = Request(
            current_url,
            headers={
                "User-Agent": "VintagePomeloBot/1.0 public-research",
                "Accept": "text/html,text/plain,application/xhtml+xml,application/json;q=0.8,*/*;q=0.1",
            },
        )
        try:
            with opener.open(request, timeout=15) as response:
                raw = response.read(_MAX_BYTES + 1)
                if len(raw) > _MAX_BYTES:
                    raise ValueError("网页内容超过抓取上限")
                charset = response.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                content_type = response.headers.get_content_type()
                if content_type in {"text/html", "application/xhtml+xml"}:
                    text = _html_to_text(text)
                return f"来源：{current_url}\n\n{text[:limit]}"
        except HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code in {301, 302, 303, 307, 308} and location:
                current_url = _validate_url(urljoin(current_url, location))
                continue
            raise ValueError(f"网页请求失败：HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"网页请求失败：{exc.reason}") from exc
    raise ValueError("重定向次数超过上限")


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(request_id: object, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: object, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return
    if method == "initialize":
        _result(
            request_id,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "public-web", "version": "1.0.0"},
            },
        )
        return
    if method == "tools/list":
        _result(
            request_id,
            {
                "tools": [
                    {
                        "name": "fetch_public_url",
                        "description": "获取公开 HTTP(S) 网页并提取文本。可抓取搜索结果页和官方资料页；拒绝 localhost、内网和私有 IP。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "要读取的公开 HTTP(S) URL"},
                                "max_chars": {"type": "integer", "description": "返回文本上限，500 到 30000"},
                            },
                            "required": ["url"],
                        },
                    }
                ]
            },
        )
        return
    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "fetch_public_url":
            _error(request_id, -32602, "未知工具")
            return
        arguments = params.get("arguments") or {}
        try:
            text = _fetch_public_url(arguments.get("url"), arguments.get("max_chars"))
            _result(request_id, {"content": [{"type": "text", "text": text}]})
        except ValueError as exc:
            _result(
                request_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
        return
    if request_id is not None:
        _error(request_id, -32601, f"不支持的方法：{method}")


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            _handle(message)


if __name__ == "__main__":
    main()
