"""消息过滤模块：判断一条 OneBot v11 群/私聊消息是否需要机器人回复。"""
from __future__ import annotations

import logging

from .config import AppConfig

logger = logging.getLogger("message_filter")


class MessageFilter:
    """根据配置规则判断消息是否应触发 LLM 回复。"""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def should_reply(
        self,
        event: dict,
        segments: list[dict],
        text: str,
    ) -> tuple[bool, str]:
        """
        判断是否需要回复。

        Returns:
            (True, reason) 需要回复；(False, reason) 不需要回复。
        """
        message_type = event.get("message_type", "")
        group_id = str(event.get("group_id", ""))

        # ── 群聊黑白名单过滤 ─────────────────────────────────────
        if message_type == "group" and group_id:
            mode = self._config.groups.mode
            group_list = self._config.groups.group_list
            if mode == "blacklist" and group_id in group_list:
                return False, "group_blacklisted"
            if mode == "whitelist" and group_list and group_id not in group_list:
                return False, "group_not_whitelisted"

        # ── 私聊：始终回复（用户是主动找机器人的）─────────────────
        if message_type == "private":
            return True, "private_msg"

        bot_qq = self._config.bot.qq_id
        nickname = self._config.bot.nickname.lower()
        text_lower = text.lower()

        # ── 优先级 1：@机器人 ────────────────────────────────────
        if self.mentions_qq(segments, bot_qq):
            return True, "at_mention"

        # ── 优先级 1.5：含有图片 (如果在群聊中只发图，只有私聊默认回复) ──
        # 此处不直接判断 True，还是走 @ 逻辑，如果用户只发图但没 @ 就不回复。
        # 除非它是私聊。
        
        # ── 优先级 2：消息中包含机器人昵称 ───────────────────────
        if nickname and nickname in text_lower:
            return True, "nickname_mention"

        # ── 优先级 3：旧柚 App 相关咨询 ──────────────────────────
        fc = self._config.filter
        has_app_kw = any(kw.lower() in text_lower for kw in fc.app_keywords)
        if has_app_kw:
            if not fc.strict_mode:
                return True, "app_keyword"
            has_help_kw = any(kw.lower() in text_lower for kw in fc.help_keywords)
            if has_help_kw:
                return True, "app_help"

        return False, "no_trigger"

    def find_blocked_keywords(self, text: str) -> list[str]:
        text_lower = text.lower()
        matches: list[str] = []
        seen: set[str] = set()

        for keyword in self._config.filter.blocked_keywords:
            normalized = keyword.strip()
            if not normalized:
                continue

            keyword_lower = normalized.lower()
            if keyword_lower not in text_lower or keyword_lower in seen:
                continue

            matches.append(normalized)
            seen.add(keyword_lower)

        return matches

    @staticmethod
    def extract_text(segments: list[dict]) -> str:
        """从 OneBot v11 消息段列表中提取纯文本内容（跳过 at/image 等）。"""
        parts = []
        for seg in segments:
            if seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts)

    @staticmethod
    def mentions_qq(segments: list[dict], qq_id: str) -> bool:
        for seg in segments:
            if seg.get("type") != "at":
                continue
            at_qq = str(seg.get("data", {}).get("qq", ""))
            if at_qq == str(qq_id):
                return True
        return False

    @staticmethod
    def extract_images(segments: list[dict]) -> list[str]:
        """
        从 OneBot v11 消息段列表中提取图片来源。

        返回的列表中每个元素为以下两种格式之一：
          - HTTP URL 字符串：从 QQ CDN 下载
          - "base64://" 前缀的字符串：直接解码使用

        NapCat 会在 data.url 提供 CDN URL，也可能在 data.file 提供 base64://... 数据。
        """
        return [
            attachment["source"]
            for attachment in MessageFilter.extract_media(segments)
            if attachment["kind"] == "image" and attachment.get("source")
        ]

    @staticmethod
    def extract_media(segments: list[dict]) -> list[dict[str, str]]:
        """Collect image/video evidence without trusting local file paths from events."""
        results: list[dict[str, str]] = []
        for segment in segments:
            kind = str(segment.get("type", "")).lower()
            if kind not in {"image", "video"}:
                continue
            data = segment.get("data", {})
            if not isinstance(data, dict):
                continue
            source = ""
            file_value = data.get("file", "")
            url = data.get("url", "")
            if isinstance(file_value, str) and file_value.startswith("base64://"):
                source = file_value
            elif isinstance(url, str) and url:
                source = url
            # A OneBot `file` value can be a local path. Never read arbitrary
            # paths supplied in an event; keep a bounded unavailable record.
            results.append({"kind": kind, "source": source})
        return results

    @staticmethod
    def extract_plain(event: dict) -> str:
        """从事件的 raw_message 字段提取可读文本（用于日志）。"""
        return event.get("raw_message", "")
